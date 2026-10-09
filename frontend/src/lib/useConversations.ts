import { computed, onBeforeUnmount, ref } from 'vue'
import type { Ref } from 'vue'
import { periodOptions } from './analytics'
import type { Message, Session } from './storage'
import { ApiError, assertAccessGeneration, getAccessGeneration, onAccessInvalidated } from './auth'
import { createRun, createSession, deleteSession, getRun, listSessions, readArtifact, readHistory, readRunEvents, resumeRun, stopRun } from './conversationsApi'
import type { ServerRun, ServerSession } from './conversationsApi'
import { mapSalesOverview } from './salesApi'

const stages = ['理解问题与校验', '查询数据', '保存结果']
const stageIndex = (stage: string | null, plan?: { id: string; label: string }[]) => plan?.length ? Math.max(0, plan.findIndex(p => p.id === stage)) : ({ understand: 0, query: 1, result: 2 } as Record<string, number>)[stage ?? ''] ?? 0
const errorText = (error: unknown) => error instanceof Error ? error.message : '会话服务暂时不可用'
const serverSession = (row: ServerSession): Session => ({ id: row.id, title: row.title, updated: row.updated_at, messages: [], loaded: false })

export function useConversations(prompt: Ref<string>, notify: (message: string) => void, navigate: (page: string) => void) {
  const sessions = ref<Session[]>([])
  const currentId = ref<string | null>(null)
  const currentSession = computed(() => sessions.value.find(s => s.id === currentId.value))
  const lastAnalysis = computed(() => currentSession.value?.messages.filter(m => m.status === 'completed').at(-1)?.analysis)
  const active = computed(() => sessions.value.flatMap(s => s.messages).find(m => m.status === 'running'))
  const pending = computed(() => currentSession.value?.messages.find(m => ['running', 'awaiting_input', 'interrupted'].includes(m.status)))
  const subscriptions = new Map<string, AbortController>()
  const submitting = ref(false)
  const historyLoading = ref(false)
  const historyError = ref('')
  const controlling = new Set<string>()
  let disposed = false
  function clearVisibleResults() {
    subscriptions.forEach(controller => controller.abort())
    sessions.value.forEach(session => { session.loaded = false; session.messages.forEach(message => { message.analysis = undefined; if (message.status === 'completed' || message.status === 'running') { message.status = 'failed'; message.note = '账号权限已变化，请重新分析。' } }) })
  }
  const unsubscribeAccess = onAccessInvalidated(clearVisibleResults)

  async function runMessage(run: ServerRun): Promise<Message> {
    const accessEpoch = getAccessGeneration()
    const spec = run.request.analysis
    const message: Message = { id: run.id, question: run.request.question, status: ['queued', 'running', 'cancelling'].includes(run.status) ? 'running' : run.status === 'partial' ? 'failed' : run.status as Message['status'], stage: stageIndex(run.stage, run.plan), eventSeq: 0,
      note: run.error_message ?? run.request.explanation ?? undefined, analysis: spec ? { ...spec, question: run.request.question } : undefined, clarificationFields: run.clarification_fields, clarificationReplies: run.clarification_replies, plan: run.plan, contextRunId: run.context_run_id, inheritedFields: run.inherited_fields }
    if (run.status === 'completed' && message.analysis) {
      try {
        const details = run.artifact_id ? run : await getRun(run.id)
        if (!details.artifact_id) throw new Error('任务缺少结果快照，请重新分析')
        const artifact = await readArtifact(details.artifact_id)
        assertAccessGeneration(accessEpoch)
        if (!artifact.schema?.analysis) throw new Error('历史结果缺少有效分析条件，请重新分析')
        const artifactAnalysis = artifact.schema.analysis
        const overview = mapSalesOverview(artifact.payload, artifactAnalysis.scope)
        assertAccessGeneration(accessEpoch)
        message.analysis = { ...artifactAnalysis, question: message.question, artifactId: details.artifact_id, access: overview.access, dataset: overview.dataset, queriedAt: overview.queriedAt, analysisResult: overview.analysisResult }
        message.stage = message.plan?.length || stages.length
      } catch (error) { message.status = 'failed'; message.note = errorText(error); message.analysis = undefined }
    }
    return message
  }

  function waitReconnect(signal: AbortSignal, delay: number) {
    return new Promise<void>(resolve => {
      const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve() }
      const timer = setTimeout(finish, delay)
      signal.addEventListener('abort', finish, { once: true })
      if (signal.aborted) finish()
    })
  }

  async function follow(message: Message) {
    const previous = subscriptions.get(message.id)
    if ((previous && !previous.signal.aborted) || disposed) return
    const controller = new AbortController()
    subscriptions.set(message.id, controller)
    let failures = 0
    try {
      while (!controller.signal.aborted && !disposed) {
        try {
          await readRunEvents(message.id, message.eventSeq ?? 0, event => {
            message.eventSeq = event.seq
            if (event.type === 'plan.created') message.plan = event.payload.steps as { id: string; label: string }[]
            if (event.type === 'intent.resolved') message.analysis = { ...event.payload.analysis as NonNullable<ServerRun['request']['analysis']>, question: message.question }
            if (event.type === 'intent.resolved') message.inheritedFields = event.payload.inherited_fields as string[]
            if (event.type === 'stage.started') message.note = undefined
            if (event.type === 'stage.started') message.stage = stageIndex(String(event.payload.stage), message.plan)
            if (event.type === 'stage.completed') message.stage = stageIndex(String(event.payload.stage), message.plan) + 1
            if (typeof event.payload.message === 'string') message.note = event.payload.message
          }, controller.signal)
          if (controller.signal.aborted || disposed) return
          const run = await getRun(message.id)
          const updated = await runMessage(run)
          if (controller.signal.aborted || disposed) return
          Object.assign(message, updated, { eventSeq: message.eventSeq })
          if (!['queued', 'running', 'cancelling'].includes(run.status)) return
          if (++failures >= 5) throw new Error('事件连接反复关闭')
        } catch (error) {
          if (controller.signal.aborted || disposed) return
          if (error instanceof ApiError && error.status === 410) {
            const run = await getRun(message.id)
            const updated = await runMessage(run)
            if (controller.signal.aborted || disposed) return
            Object.assign(message, updated, { eventSeq: run.last_event_seq })
            if (!['queued', 'running', 'cancelling'].includes(run.status)) return
          } else if (error instanceof ApiError && [401, 403, 404, 409].includes(error.status)) { message.analysis = undefined; message.status = 'failed'; throw error }
          if (++failures >= 5) throw error
          message.note = '连接断开，任务仍在服务端执行；正在重新连接…'
        }
        await waitReconnect(controller.signal, Math.min(500 * 2 ** failures, 5000))
      }
    } catch (error) { if (!disposed && !controller.signal.aborted) { message.note = `${errorText(error)}。重新打开会话可同步任务状态。`; notify(message.note) } }
    finally { if (subscriptions.get(message.id) === controller) subscriptions.delete(message.id) }
  }

  async function loadSessions() {
    historyLoading.value = true; historyError.value = ''
    try {
      const all: ServerSession[] = []
      for (let offset = 0; ; offset += 100) { const { items } = await listSessions(offset); all.push(...items); if (items.length < 100) break }
      if (!disposed) sessions.value = all.map(serverSession)
    } catch (error) { if (!disposed) historyError.value = errorText(error) }
    finally { if (!disposed) historyLoading.value = false }
  }

  async function loadHistory(session: Session) {
    const accessEpoch = getAccessGeneration()
    historyLoading.value = true; historyError.value = ''
    session.messages.forEach(message => { subscriptions.get(message.id)?.abort(); message.analysis = undefined })
    try {
      const runs: ServerRun[] = []
      for (let offset = 0; ; offset += 100) {
        const history = await readHistory(session.id, offset)
        session.title = history.session.title; session.updated = history.session.updated_at
        runs.unshift(...history.runs)
        if (history.runs.length < 100) break
      }
      session.messages.forEach(message => subscriptions.get(message.id)?.abort())
      const messages = await Promise.all(runs.map(runMessage))
      if (disposed) return
      assertAccessGeneration(accessEpoch)
      session.messages = messages; session.loaded = true
      for (const message of session.messages) if (message.status === 'running') void follow(message)
    } catch (error) { historyError.value = errorText(error); throw error }
    finally { historyLoading.value = false }
  }

  void loadSessions()
  async function newSession() {
    try {
      const session = { ...serverSession(await createSession()), loaded: true }
      if (disposed) return
      sessions.value.unshift(session); currentId.value = session.id; prompt.value = ''; navigate('analysis')
      return sessions.value[0]!
    } catch (error) { notify(errorText(error)); return undefined }
  }
  async function openSession(id: string) {
    if (submitting.value || historyLoading.value) return
    currentId.value = id; prompt.value = ''; navigate('analysis')
    const session = sessions.value.find(s => s.id === id)
    if (session) { try { await loadHistory(session) } catch (error) { notify(errorText(error)) } }
  }

  async function send(question = prompt.value, fresh = false) {
    const accessEpoch = getAccessGeneration()
    const input = question.trim()
    if (!input || submitting.value || historyLoading.value) return
    if (active.value) { notify('请先完成或停止正在执行的分析'); return }
    if (pending.value && !fresh) {
      if (pending.value.status === 'awaiting_input') {
        await continueMessage(pending.value, input); prompt.value = ''; return
      }
      notify('请先继续或取消暂停的分析'); return
    }
    submitting.value = true
    try {
      const session = fresh || !currentSession.value ? await newSession() : currentSession.value
      if (!session) return
      if (!session.loaded) await loadHistory(session)
      const body = { question: input, mode: 'auto' as const }
      const key = crypto.randomUUID()
      let run: ServerRun
      // 同一次提交的网络重试复用 key，避免产生重复任务。
      try { run = await createRun(session.id, body, key) }
      catch (error) { if (error instanceof ApiError) throw error; run = await createRun(session.id, body, key) }
      if (disposed) return
      const result = await runMessage(run)
      assertAccessGeneration(accessEpoch)
      session.messages.push(result)
      session.title = session.messages.length === 1 ? input.slice(0, 200) : session.title
      session.updated = run.updated_at
      sessions.value.sort((a, b) => b.updated.localeCompare(a.updated))
      prompt.value = ''; navigate('analysis')
      const message = session.messages.at(-1)!
      if (message.status === 'running') void follow(message)
    } catch (error) { notify(errorText(error)) }
    finally { submitting.value = false }
  }

  async function stop(message: Message, pause = false) {
    const accessEpoch = getAccessGeneration()
    if (controlling.has(message.id)) return
    controlling.add(message.id)
    try {
      const run = await stopRun(message.id, pause)
      subscriptions.get(message.id)?.abort()
      const result = await runMessage(run)
      assertAccessGeneration(accessEpoch)
      Object.assign(message, result)
    } catch (error) { notify(errorText(error)) }
    finally { controlling.delete(message.id) }
  }
  async function continueMessage(message: Message, reply?: string) {
    const accessEpoch = getAccessGeneration()
    if (active.value) { notify('另一个分析正在执行，请稍后继续'); return }
    if (controlling.has(message.id)) return
    controlling.add(message.id)
    try {
      const run = await resumeRun(message.id, undefined, reply)
      const result = await runMessage(run)
      assertAccessGeneration(accessEpoch)
      Object.assign(message, result)
      void follow(message)
    } catch (error) { notify(errorText(error)) }
    finally { controlling.delete(message.id) }
  }
  const resume = (message: Message) => continueMessage(message)
  async function clarify(message: Message, value: string) {
    const chosen = periodOptions.find(p => p.value === value)!
    await continueMessage(message, `时间范围改为 ${chosen.months[0]} 至 ${chosen.months.at(-1)}`)
  }
  async function removeSession(id: string) {
    try {
      await deleteSession(id)
      sessions.value.find(s => s.id === id)?.messages.forEach(message => subscriptions.get(message.id)?.abort())
      sessions.value = sessions.value.filter(s => s.id !== id)
      if (currentId.value === id) currentId.value = null
      notify('会话已从服务端删除')
    } catch (error) { notify(errorText(error)) }
  }
  onBeforeUnmount(() => { disposed = true; unsubscribeAccess(); subscriptions.forEach(controller => controller.abort()) })
  return { sessions, currentId, currentSession, lastAnalysis, active, pending, stages, historyLoading, historyError, submitting, loadSessions, newSession, openSession, send, stop, resume, clarify, removeSession, clearVisibleResults }
}
