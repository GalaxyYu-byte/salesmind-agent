import { afterEach, expect, it, vi } from 'vitest'
import { readState, saveState } from './storage'

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
