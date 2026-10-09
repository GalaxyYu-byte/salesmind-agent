import type { Analysis } from './analytics'
import { ApiError, assertAccessGeneration, authFetch, readAccessJson, responseAccessGeneration } from './auth'

export type AnalysisSpec = Pick<Analysis, 'scope' | 'metric' | 'view' | 'chart' | 'comparison' | 'sort_by' | 'sort_order' | 'top_n' | 'goal' | 'product_ids' | 'anomaly_window' | 'anomaly_threshold_percent'>
export interface ServerSession { id: string; title: string; updated_at: string; active_run_id: string | null }
export interface ServerRun {
  id: string; session_id: string; status: 'queued' | 'running' | 'cancelling' | 'awaiting_input' | 'interrupted' | 'completed' | 'partial' | 'failed' | 'cancelled'
  stage: string | null; attempt: number; last_event_seq: number; updated_at: string
  request: { question: string; analysis: AnalysisSpec | null; mode: string; explanation: string | null }
  error_message: string | null; artifact_id?: string | null
  clarification_fields?: string[]
  clarification_replies?: string[]; context_run_id?: string | null; inherited_fields?: string[]; plan?: { id: string; label: string }[]
}
export interface RunEvent { schema_version: number; run_id: string; session_id: string; seq: number; type: string; payload: Record<string, unknown> }
export interface RunInput { question: string; mode?: 'auto' | 'analysis' | 'clarify' | 'unsupported'; analysis?: AnalysisSpec; explanation?: string }

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await authFetch(`/api/v1${url}`, init)
  if (!response.ok) {
    let message = '会话服务暂时不可用'
    try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail } catch { /* 代理错误可能没有 JSON。 */ }
    throw new ApiError(message, response.status)
  }
  return response.status === 204 ? undefined as T : readAccessJson<T>(response)
}
const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export function analysisSpec(analysis: Analysis): AnalysisSpec {
  return { scope: { months: [...analysis.scope.months], region: analysis.scope.region }, metric: analysis.metric, view: analysis.view, chart: analysis.chart, ...Object.fromEntries(['comparison', 'sort_by', 'sort_order', 'top_n', 'goal', 'anomaly_window', 'anomaly_threshold_percent'].filter(k => analysis[k as keyof Analysis] !== undefined).map(k => [k, analysis[k as keyof Analysis]])), ...(analysis.product_ids === undefined ? {} : { product_ids: [...analysis.product_ids] }) }
}
export const createSession = () => request<ServerSession>('/sessions', json({}))
export const listSessions = (offset = 0) => request<{ items: ServerSession[] }>(`/sessions?limit=100&offset=${offset}`)
export const readHistory = (sid: string, offset = 0) => request<{ session: ServerSession; runs: ServerRun[] }>(`/sessions/${sid}/messages?limit=100&offset=${offset}`)
export const deleteSession = (sid: string) => request<void>(`/sessions/${sid}`, { method: 'DELETE' })
export const createRun = (sid: string, body: RunInput, key: string) => request<ServerRun>(`/sessions/${sid}/runs`, { ...json(body), headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key } })
export const getRun = (rid: string) => request<ServerRun>(`/runs/${rid}`)
export const resumeRun = (rid: string, analysis?: AnalysisSpec, reply?: string) => request<ServerRun>(`/runs/${rid}/resume`, json({ ...(analysis ? { analysis } : {}), ...(reply ? { reply } : {}) }))
export const stopRun = (rid: string, pause = false) => request<ServerRun>(`/runs/${rid}/${pause ? 'pause' : 'cancel'}`, json({}))
export const readArtifact = (aid: string) => request<{ id: string; kind: string; payload: unknown; schema?: { analysis?: AnalysisSpec } }>(`/artifacts/${aid}`)

export async function readRunEvents(rid: string, after: number, receive: (event: RunEvent) => void, signal: AbortSignal): Promise<number> {
  const response = await authFetch(`/api/v1/runs/${rid}/events`, { signal, headers: { Accept: 'text/event-stream', 'Last-Event-ID': String(after) } })
  if (!response.ok) throw new ApiError('事件订阅失败', response.status)
  if (!response.headers.get('content-type')?.startsWith('text/event-stream') || !response.body) throw new Error('事件响应格式无效')
  const reader = response.body.getReader()
  const accessEpoch = responseAccessGeneration(response)
  const decoder = new TextDecoder()
  let buffer = '', cursor = after
  try {
    while (!signal.aborted) {
      const chunk = await reader.read()
      assertAccessGeneration(accessEpoch)
      buffer = (buffer + decoder.decode(chunk.value, { stream: !chunk.done })).replaceAll('\r\n', '\n')
      let boundary: number
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2)
        const lines = frame.split('\n')
        const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (!data) continue
        const event = JSON.parse(data) as RunEvent
        const id = lines.find(line => line.startsWith('id:'))?.slice(3).trim()
        const kind = lines.find(line => line.startsWith('event:'))?.slice(6).trim()
        if (event.schema_version !== 1 || event.run_id !== rid || !Number.isSafeInteger(event.seq) || String(event.seq) !== id || event.type !== kind || !event.payload || typeof event.payload !== 'object') throw new Error('事件协议校验失败')
        if (event.seq <= cursor) continue
        if (event.seq !== cursor + 1) throw new Error('事件序号不连续，请重连恢复')
        assertAccessGeneration(accessEpoch)
        receive(event)
        cursor = event.seq
      }
      if (buffer.length > 1048576) throw new Error('事件帧超过大小限制')
      if (chunk.done) {
        if (buffer.trim() && !buffer.trimStart().startsWith(':')) throw new Error('事件连接在帧结束前中断')
        return cursor
      }
    }
    return cursor
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}
