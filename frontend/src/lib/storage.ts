import type { Analysis } from './analytics'

export type Status = 'running' | 'awaiting_input' | 'interrupted' | 'cancelled' | 'completed' | 'failed'
export interface Message { id: string; question: string; status: Status; stage: number; analysis?: Analysis; note?: string }
export interface Session { id: string; title: string; updated: string; messages: Message[] }
export interface Report { id: string; title: string; created: string; analysis: Analysis }
export interface SavedState { sessions: Session[]; reports: Report[] }
function storageKey(userId?: string) { return userId ? `salesmind.user.${userId}.v1` : 'salesmind.demo.v1' }
export function readState(userId?: string): SavedState {
  try {
    const data = JSON.parse(localStorage.getItem(storageKey(userId)) ?? 'null')
    if (!data || !Array.isArray(data.sessions) || !Array.isArray(data.reports)) return { sessions: [], reports: [] }
    const validAnalysis = (a: Analysis) => a && Array.isArray(a.scope?.months) && a.scope.months.length > 0 && a.scope.months.every(m => /^202\d-\d\d$/.test(m)) && Number.isInteger(a.scope.region) && ['amount', 'count', 'profit', 'average'].includes(a.metric) && ['trend', 'products', 'regions'].includes(a.view) && ['line', 'bar'].includes(a.chart)
    const sessions = data.sessions.filter((s: Session) => typeof s.id === 'string' && typeof s.title === 'string' && Array.isArray(s.messages)).map((s: Session) => ({ ...s, messages: s.messages.filter(m => typeof m.id === 'string' && typeof m.question === 'string' && ['running', 'awaiting_input', 'interrupted', 'cancelled', 'completed', 'failed'].includes(m.status) && (!m.analysis || validAnalysis(m.analysis))).map(m => m.status === 'running' ? { ...m, status: 'interrupted' as const, note: '页面关闭使分析中断，继续时会重新查询。' } : m) }))
    return { sessions, reports: data.reports.filter((r: Report) => typeof r.id === 'string' && typeof r.title === 'string' && validAnalysis(r.analysis)) }
  } catch { return { sessions: [], reports: [] } }
}
export function saveState(state: SavedState, userId?: string) {
  try { localStorage.setItem(storageKey(userId), JSON.stringify(state)); return true } catch { return false }
}
export function download(name: string, content: string, type = 'text/csv;charset=utf-8') {
  const url = URL.createObjectURL(new Blob(['\ufeff', content], { type }))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = name; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export function csv(rows: (string | number)[][]) {
  return rows.map(row => row.map(cell => `"${String(cell).replaceAll('"', '""')}"`).join(',')).join('\r\n')
}
