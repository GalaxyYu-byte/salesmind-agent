import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { answer, metricNames, money, parseQuestion, periodLabel, periodOptions, ranking, scopeName, series, source, total } from './analytics'
import type { Analysis, Dataset, Scope } from './analytics'
import { fetchSalesOverview } from './salesApi'
import { csv, download, readState, saveState } from './storage'
import type { Message, Report, Session } from './storage'
import { useAuth } from './auth'

export function useWorkbench() {
  const userId = useAuth().user.value?.id
  const saved = readState(userId)
  const sessions = ref<Session[]>(saved.sessions)
  const reports = ref<Report[]>(saved.reports)
  const page = ref('overview')
  const period = ref('6')
  const region = ref(0)
  const prompt = ref('')
  const currentId = ref<string | null>(null)
  const sidebarOpen = ref(false)
  const toast = ref('')
  const salesData = shallowRef<Dataset | null>(null)
  const salesLoading = ref(false)
  const salesError = ref('')
  const queryTime = ref('')
  let queryController: AbortController | undefined
  let querySequence = 0
  const scope = computed<Scope>(() => ({ months: [...(periodOptions.find(p => p.value === period.value) ?? periodOptions[0]!).months], region: region.value }))
  async function refreshSales() {
    const sequence = ++querySequence
    queryController?.abort()
    queryController = new AbortController()
    salesLoading.value = true; salesError.value = ''; salesData.value = null
    try {
      const result = await fetchSalesOverview(scope.value, queryController.signal)
      if (sequence !== querySequence) return
      salesData.value = result.dataset; queryTime.value = result.queriedAt
    } catch (error) {
      if (sequence !== querySequence) return
      salesError.value = error instanceof Error ? error.message : '销售服务暂时不可用'
    } finally { if (sequence === querySequence) salesLoading.value = false }
  }
  watch(scope, () => { void refreshSales() }, { immediate: true })
  const currentSession = computed(() => sessions.value.find(s => s.id === currentId.value))
  const lastAnalysis = computed(() => currentSession.value?.messages.filter(m => m.status === 'completed').at(-1)?.analysis)
  const active = computed(() => sessions.value.flatMap(s => s.messages).find(m => m.status === 'running'))
  const pending = computed(() => currentSession.value?.messages.find(m => ['running', 'awaiting_input', 'interrupted'].includes(m.status)))
  let runController: AbortController | undefined
  let runningId: string | undefined
  let toastTimer: ReturnType<typeof setTimeout> | undefined
  let storageWarning = false
  const stages = ['理解问题', '查询数据', '计算指标', '生成图表', '整理结论']

  function notify(message: string) {
    toast.value = message
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => { toast.value = '' }, 3200)
  }
  watch([sessions, reports], () => {
    if (!saveState({ sessions: sessions.value, reports: reports.value }, userId) && !storageWarning) {
      storageWarning = true; notify('浏览器存储不可用，刷新后将无法恢复本次记录。')
    }
  }, { deep: true })
  function navigate(target: string) { page.value = target; sidebarOpen.value = false }
  function newSession() {
    const session: Session = { id: crypto.randomUUID(), title: '新的分析', updated: new Date().toISOString(), messages: [] }
    sessions.value.unshift(session); currentId.value = session.id; prompt.value = ''; navigate('analysis')
    return session
  }
  function openSession(id: string) { currentId.value = id; prompt.value = ''; navigate('analysis') }
  async function advance(message: Message) {
    if (message.status !== 'running' || !message.analysis) return
    runningId = message.id
    const controller = new AbortController()
    runController = controller
    message.stage = 1
    try {
      const result = await fetchSalesOverview(message.analysis.scope, controller.signal)
      if (controller.signal.aborted || message.status !== 'running' || runningId !== message.id) return
      message.analysis = { ...message.analysis, dataset: result.dataset, queriedAt: result.queriedAt }
      message.stage = 4; message.status = 'completed'; message.note = undefined
      notify('MySQL 查询完成，结果已保存到会话')
    } catch (error) {
      if (controller.signal.aborted || message.status !== 'running' || runningId !== message.id) return
      message.status = 'failed'; message.note = error instanceof Error ? error.message : '真实销售查询失败，请重试'
    } finally {
      if (runningId === message.id) { runningId = undefined; runController = undefined }
    }
  }
  function send(question = prompt.value, fresh = false) {
    const input = question.trim()
    if (!input) return
    if (active.value) { notify('请先完成或停止正在执行的分析'); return }
    if (pending.value && !fresh) {
      if (pending.value.status === 'awaiting_input') {
        const parsed = parseQuestion(`${pending.value.question}，${input}`, scope.value)
        if (parsed.kind === 'ready') { pending.value.analysis = parsed.analysis; pending.value.status = 'running'; pending.value.note = `补充范围：${input}`; advance(pending.value); prompt.value = ''; return }
        notify(parsed.message); return
      }
      notify('请先继续或取消暂停的分析'); return
    }
    if (fresh || !currentSession.value) newSession()
    const session = currentSession.value!
    const parsed = parseQuestion(input, scope.value, lastAnalysis.value)
    const message: Message = {
      id: crypto.randomUUID(), question: input, stage: 0,
      status: parsed.kind === 'ready' ? 'running' : parsed.kind === 'clarify' ? 'awaiting_input' : 'failed',
      ...(parsed.kind === 'ready' ? { analysis: parsed.analysis } : { note: parsed.message }),
    }
    session.messages.push(message)
    if (session.messages.length === 1) session.title = input.slice(0, 28)
    session.updated = new Date().toISOString()
    sessions.value.sort((a, b) => b.updated.localeCompare(a.updated))
    prompt.value = ''; navigate('analysis')
    if (message.status === 'running') advance(session.messages.at(-1)!)
  }
  function stop(message: Message, pause = false) {
    if (runningId === message.id) { runController?.abort(); runningId = undefined; runController = undefined }
    message.status = pause ? 'interrupted' : 'cancelled'
    message.note = pause ? '已暂停读取结果，继续时会按同一范围重新查询。' : '已停止读取本次查询结果，你可以提交新的问题。'
  }
  function resume(message: Message) {
    if (active.value) { notify('另一个分析正在执行，请稍后继续'); return }
    message.status = 'running'; message.note = undefined; advance(message)
  }
  function clarify(message: Message, value: string) {
    if (active.value) { notify('另一个分析正在执行，请稍后继续'); return }
    const chosen = periodOptions.find(p => p.value === value)!
    const base: Analysis = { scope: { months: [...chosen.months], region: scope.value.region }, metric: 'amount', view: 'trend', chart: 'line', question: message.question }
    const parsed = parseQuestion(message.question, base.scope, base)
    if (parsed.kind !== 'ready') { notify(parsed.message); return }
    message.analysis = parsed.analysis; message.status = 'running'; message.note = `统计范围：${chosen.label}`; advance(message)
  }
  function saveReport(analysis: Analysis) {
    const snapshot = JSON.parse(JSON.stringify(analysis)) as Analysis
    const exists = reports.value.find(r => JSON.stringify(r.analysis.scope) === JSON.stringify(snapshot.scope) && r.analysis.metric === snapshot.metric && r.analysis.view === snapshot.view)
    if (exists) { notify('这份分析已经保存在报告中'); return }
    reports.value.unshift({ id: crypto.randomUUID(), title: `${scopeName(analysis.scope, analysis.dataset ?? source)}${metricNames[analysis.metric]}${analysis.view === 'trend' ? '趋势' : '排名'}分析`, created: new Date().toISOString(), analysis: snapshot })
    notify('报告已保存，可在“分析报告”中查看')
  }
  function exportAnalysis(analysis: Analysis) {
    const chart = series(analysis)
    const format = (v: number) => analysis.metric === 'count' ? v : (v / 100).toFixed(2)
    const rows: (string | number)[][] = [
      ['数据来源', analysis.dataset ? `MySQL 查询快照 v${analysis.dataset.version}` : `SalesMind 固定演示数据 v${source.version}`],
      ['统计范围', scopeName(analysis.scope), periodLabel(analysis.scope.months)],
      ['口径', '仅已完成订单；退款和取消订单已排除'],
      [analysis.view === 'trend' ? '月份' : '名称', metricNames[analysis.metric] + (analysis.metric === 'count' ? '（笔）' : '（元）'), ...(chart.baseline.length ? ['去年同期'] : [])],
      ...chart.labels.map((label, i) => [analysis.view === 'trend' ? analysis.scope.months[i]! : label, format(chart.values[i]!), ...(chart.baseline.length ? [chart.baseline[i] === null ? '数据未覆盖' : format(chart.baseline[i]!)] : [])]),
    ]
    download(`SalesMind-${metricNames[analysis.metric]}-${analysis.scope.months[0]}.csv`, csv(rows)); notify('数据已导出')
  }
  function exportReport(report: Report) {
    const result = answer(report.analysis)
    const content = `# ${report.title}\n\n${result.title}\n\n${result.headline}\n\n${result.detail}\n\n## 统计数据\n\n${series(report.analysis).labels.map((name, i) => `- ${name}：${report.analysis.metric === 'count' ? series(report.analysis).values[i] : '¥ ' + money(series(report.analysis).values[i]!)}`).join('\n')}\n\n数据来源：${report.analysis.dataset ? "MySQL 查询快照 v" + report.analysis.dataset.version : "固定演示数据 v" + source.version}。仅已完成订单，排除退款和取消订单。\n\n${result.caveat}\n`
    download(`${report.title}.md`, content, 'text/markdown;charset=utf-8'); notify('报告已导出为 Markdown')
  }
  function exportTable() {
    const dataset = salesData.value
    if (!dataset || salesLoading.value || salesError.value) { notify('请等待真实查询成功后再导出'); return }
    const rows: (string | number)[][] = [
      ['MySQL 实时查询', `v${dataset.version}`, periodLabel(scope.value.months), scopeName(scope.value, dataset)],
      ['查询时间', queryTime.value],
      ['月份', '区域', '销售额（元）', '订单数（笔）', '毛利（元）', '毛利率'],
    ]
    for (const month of scope.value.months) for (const item of dataset.regions.filter(r => !scope.value.region || r.id === scope.value.region)) {
      const result = total({ months: [month], region: item.id }, undefined, dataset)
      rows.push([month, item.name, (result.amount / 100).toFixed(2), result.count, (result.profit / 100).toFixed(2), result.amount ? (result.profit / result.amount * 100).toFixed(2) + '%' : '—'])
    }
    download('SalesMind-区域月度数据.csv', csv(rows)); notify('区域月度汇总已导出')
  }
  function removeSession(id: string) {
    const session = sessions.value.find(s => s.id === id)
    if (session?.messages.some(m => m.status === 'running')) { notify('请先停止分析，再删除会话'); return }
    sessions.value = sessions.value.filter(s => s.id !== id)
    if (currentId.value === id) currentId.value = null
    notify('会话已删除')
  }
  const overview = computed<Analysis>(() => ({ scope: scope.value, metric: 'amount', view: 'trend', chart: 'line', question: '销售趋势分析', dataset: salesData.value ?? undefined, queriedAt: queryTime.value }))
  const totals = computed(() => total(scope.value, undefined, salesData.value ?? { ...source, cells: [] }))
  const regionRows = computed(() => salesData.value ? ranking(scope.value, 'regions', 'amount', salesData.value) : [])
  onBeforeUnmount(() => { queryController?.abort(); runController?.abort(); clearTimeout(toastTimer) })
  return { sessions, reports, page, period, region, prompt, currentId, sidebarOpen, toast, scope, currentSession, pending, active, lastAnalysis, stages, overview, totals, regionRows, salesData, salesLoading, salesError, queryTime, refreshSales, notify, navigate, newSession, openSession, send, stop, resume, clarify, saveReport, exportAnalysis, exportReport, exportTable, removeSession }
}
