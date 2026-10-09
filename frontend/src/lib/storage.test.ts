import { afterEach, expect, it, vi } from 'vitest'
import { readState, saveState } from './storage'
import type { Analysis } from './analytics'

afterEach(() => vi.unstubAllGlobals())
it('separates cached histories for different authenticated accounts and demo records', () => {
  const items = new Map<string, string>()
  vi.stubGlobal('localStorage', { getItem: (key: string) => items.get(key) ?? null, setItem: (key: string, value: string) => items.set(key, value) })
  const state = { sessions: [{ id: 's1', title: '用户一的分析', updated: '2026-10-01', messages: [] }], reports: [] }
  saveState(state, 'user-1')
  expect(readState('user-1').sessions).toHaveLength(1)
  expect(readState('user-2').sessions).toHaveLength(0)
  expect(readState().sessions).toHaveLength(0)
  saveState({ ...state, sessions: [{ ...state.sessions[0]!, title: '用户二的分析' }] }, 'user-2')
  expect(readState('user-1').sessions[0]!.title).toBe('用户一的分析')
})
it('persists only the artifact reference and safe analysis specification', () => {
  const items = new Map<string, string>()
  vi.stubGlobal('localStorage', { getItem: (key: string) => items.get(key) ?? null, setItem: (key: string, value: string) => items.set(key, value) })
  const spec = { scope: { months: ['2026-08'], region: 1 }, metric: 'amount' as const, view: 'trend' as const, chart: 'line' as const }
  const analysis = { ...spec, question: '', analysisResult: { headline: 'sensitive 123456' }, dataset: { cells: [{ amount: 123456 }] } } as Analysis
  saveState({ sessions: [], reports: [{ id: 'r1', title: '华东销售趋势', created: '2026-10-08', artifactId: 'artifact-1', spec, analysis }] }, 'user-1')
  const stored = items.get('salesmind.user.user-1.v1')!
  expect(stored).not.toContain('123456')
  expect(stored).not.toContain('dataset')
  expect(readState('user-1').reports[0]).toEqual({ id: 'r1', title: '华东销售趋势', created: '2026-10-08', artifactId: 'artifact-1', spec })
})
it('cleans up old complete report snapshots instead of importing them', () => {
  const items = new Map<string, string>([['salesmind.user.user-1.v1', JSON.stringify({ sessions: [], reports: [{ id: 'old', title: '旧报告', analysis: { scope: { months: ['2026-08'], region: 1 }, metric: 'amount', view: 'trend', chart: 'line', dataset: { cells: [{ amount: 987654 }] } } }] })]])
  vi.stubGlobal('localStorage', { getItem: (key: string) => items.get(key) ?? null, setItem: (key: string, value: string) => items.set(key, value) })
  expect(readState('user-1').reports).toEqual([])
  expect(items.get('salesmind.user.user-1.v1')).not.toContain('987654')
})

it('removes legacy assistant summaries and unexpected result fields from cached sessions', () => {
  const items = new Map<string, string>([['salesmind.user.user-1.v1', JSON.stringify({
    sessions: [{ id: 'old-session', title: '销售趋势', updated: '2026-10-08', messages: [{
      id: 'message-1', question: '八月销售额', status: 'completed', stage: 5,
      note: '毛利 987654 元', answer: '成本 654321 元',
      analysis: { scope: { months: ['2026-08'], region: 1 }, metric: 'amount', view: 'trend', chart: 'line', question: '八月销售额', dataset: { cells: [{ amount: 123456 }] } },
    }] }], reports: [],
  })]])
  vi.stubGlobal('localStorage', { getItem: (key: string) => items.get(key) ?? null, setItem: (key: string, value: string) => items.set(key, value) })
  const message = readState('user-1').sessions[0]!.messages[0]!
  expect(message.question).toBe('八月销售额')
  expect(message.note).toBeUndefined()
  expect(message.analysis?.dataset).toBeUndefined()
  const stored = items.get('salesmind.user.user-1.v1')!
  for (const value of ['987654', '654321', '123456']) expect(stored).not.toContain(value)
})
