import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchSalesOverview } from './salesApi'
import { series } from './analytics'

vi.mock('./auth', () => ({ authFetch: (url: string, init: RequestInit) => fetch(url, init) }))

const scope = { months: ['2026-08'], region: 1 }
const payload = {
  source: 'mysql', queried_at: '2026-10-01T09:00:00Z', months: ['2026-08'], region_id: 1,
  totals: { sales_amount: '120.00', gross_profit: '50.00', order_count: 1, quantity: 1 },
  dataset: {
    version: 2026092201, available_from: '2024-02-01', complete_through: '2026-09-21', order_count: 10261,
    regions: [{ id: 1, name: '华东' }], products: [{ id: 12, name: '商品', category: '其他' }],
    cells: [
      { month: '2026-08', region: 1, product: 12, amount: '12000', cost: '7000', count: 1, quantity: 1 },
      { month: '2025-08', region: 1, product: 12, amount: '10000', cost: '6000', count: 1, quantity: 1 },
    ],
  },
}

afterEach(() => vi.unstubAllGlobals())

describe('real sales API contract', () => {
  it('sends a bounded region query and renders server data rather than fixture values', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload)))
    vi.stubGlobal('fetch', fetchMock)
    const result = await fetchSalesOverview(scope)
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/v1/sales/overview?start_month=2026-08&end_month=2026-08&region_id=1')
    expect(result.dataset.cells[0]!.amount).toBe(12000)
    expect(series({ scope, metric: 'amount', view: 'trend', chart: 'line', question: '', dataset: result.dataset }).values).toEqual([12000])
  })
  it('preserves an AbortSignal and disables HTTP caching', async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload)))
    vi.stubGlobal('fetch', fetchMock)
    await fetchSalesOverview(scope, controller.signal)
    expect(fetchMock.mock.calls[0]![1]).toMatchObject({ signal: controller.signal, cache: 'no-store' })
  })
  it('returns a safe failure without silently loading a fixture', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '销售数据库暂时不可用' }), { status: 503 })))
    await expect(fetchSalesOverview(scope)).rejects.toThrow('销售数据库暂时不可用（503）')
  })
  it('rejects inconsistent server totals', async () => {
    const changed = structuredClone(payload)
    changed.totals.sales_amount = '999.99'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(changed))))
    await expect(fetchSalesOverview(scope)).rejects.toThrow('汇总与明细聚合不一致')
  })
  it('rejects an incorrect region response', async () => {
    const changed = structuredClone(payload)
    changed.region_id = 2
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(changed))))
    await expect(fetchSalesOverview(scope)).rejects.toThrow('范围与请求不一致')
  })
  it('rejects cent amounts beyond JavaScript safe integers', async () => {
    const changed = structuredClone(payload)
    changed.dataset.cells[0]!.amount = '9007199254740993'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(changed))))
    await expect(fetchSalesOverview(scope)).rejects.toThrow('超出可安全展示范围')
  })
  it('keeps negative gross profit exact', async () => {
    const changed = structuredClone(payload)
    changed.dataset.cells[0]!.cost = '14000'
    changed.totals.gross_profit = '-20.00'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(changed))))
    const result = await fetchSalesOverview(scope)
    expect(result.totals.gross_profit).toBe('-20.00')
  })
})
