import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchSalesOverview, mapAnalysisResult, mapSalesOverview } from './salesApi'
import { canMetric, series, total } from './analytics'

vi.mock('./auth', async () => ({ ...await vi.importActual<typeof import('./auth')>('./auth'), authFetch: (url: string, init: RequestInit) => fetch(url, init) }))

const scope = { months: ['2026-08'], region: 1 }
const payload = {
  source: 'mysql', queried_at: '2026-10-01T09:00:00Z', months: ['2026-08'], region_id: 1,
  access: { user_id: 'user-1', permission_version: 1, scope_hash: 'scope-1', scope_label: '授权区域', allowed_metrics: ['sales_amount', 'sales_cost', 'gross_profit', 'order_count', 'quantity', 'avg_order_value', 'product_share', 'growth_rate'] },
  totals: { sales_amount: '120.00', gross_profit: '50.00', order_count: 1, quantity: 1 },
  dataset: {
    version: 2026092201, available_from: '2024-02-01', complete_through: '2026-09-21', order_count: 10261,
    regions: [{ id: 1, name: '华东' }], products: [{ id: 12, name: '商品', category: '其他' }],
    cells: [
      { month: '2026-08', region: 1, product: 12, amount: '12000', cost: '7000', gross_profit: '5000', count: 1, quantity: 1 },
      { month: '2025-08', region: 1, product: 12, amount: '10000', cost: '6000', gross_profit: '4000', count: 1, quantity: 1 },
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
  it('bypasses the server query cache only for an explicit refresh and keeps the snapshot timestamp', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...payload, cache: { hit: false, ttl_seconds: 300 } })))
    vi.stubGlobal('fetch', fetchMock)
    const result = await fetchSalesOverview(scope, undefined, true)
    expect(fetchMock.mock.calls[0]![0]).toContain('&refresh=true')
    expect(result.cache).toEqual({ hit: false, ttl_seconds: 300 })
    expect(result.queriedAt).toBe(payload.queried_at)
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
    changed.dataset.cells[0]!.gross_profit = '-2000'
    changed.totals.gross_profit = '-20.00'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(changed))))
    const result = await fetchSalesOverview(scope)
    expect(result.totals.gross_profit).toBe('-20.00')
  })
  it('rejects legacy snapshots without an access scope', () => {
    const { access: _access, ...legacy } = payload
    expect(() => mapSalesOverview(legacy, scope)).toThrow('缺少有效权限范围')
  })
  it('keeps restricted metrics null instead of manufacturing zero or profit', () => {
    const restricted = { ...payload, access: { ...payload.access, allowed_metrics: ['order_count'] }, totals: { order_count: 1 }, dataset: { ...payload.dataset, cells: payload.dataset.cells.map(c => ({ month: c.month, region: c.region, product: c.product, count: c.count })) } }
    const data = mapSalesOverview(restricted, scope).dataset
    expect(total(scope, undefined, data)).toEqual({ amount: null, cost: null, count: 1, quantity: null, profit: null, average: null })
    expect(series({ scope, metric: 'amount', view: 'trend', chart: 'line', question: '', dataset: data }).values).toEqual([null])
    expect(canMetric('average', data.access)).toBe(false)
  })
  it('uses separately authorized gross profit while keeping cost hidden', () => {
    const safe = { ...payload, access: { ...payload.access, allowed_metrics: payload.access.allowed_metrics.filter(m => m !== 'sales_cost') }, dataset: { ...payload.dataset, cells: payload.dataset.cells.map(({ cost: _cost, ...c }) => c) } }
    const data = mapSalesOverview(safe, scope).dataset
    expect(total(scope, undefined, data).profit).toBe(5000)
    expect(total(scope, undefined, data).cost).toBeNull()
  })
  it('rejects a forbidden metric even when it appears only in baseline data', () => {
    const restricted = { ...payload, access: { ...payload.access, allowed_metrics: ['order_count'] }, totals: { order_count: 1 }, dataset: { ...payload.dataset, cells: [{ month: '2026-08', region: 1, product: 12, count: 1 }, { month: '2025-08', region: 1, product: 12, count: 1, amount: '10000' }] } }
    expect(() => mapSalesOverview(restricted, scope)).toThrow('未授权指标')
  })
})


it('rejects nonfinite analysis rows and malformed plot values', () => {
  const result = { rows: [{ id: '1', name: '华东', current: '100', baseline: '200', yoy_change: '-100', yoy_percent: '-50', reason: '' }], selected_ids: ['1'], headline: '下降50%', detail: '', caveat: '', chart_unit: '%', figure: { data: [{ x: ['华东'], y: [-50] }], layout: {} } }
  expect(mapAnalysisResult(result)).toMatchObject({ chart_unit: '%' })
  expect(() => mapAnalysisResult({ ...result, figure: { data: [{ x: ['华东'], y: [NaN] }], layout: {} } })).toThrow('无效数值')
  expect(() => mapAnalysisResult({ ...result, rows: [{ ...result.rows[0], yoy_percent: 'Infinity' }] })).toThrow('无效数值')
})

it('validates optional comparison and anomaly fields while accepting legacy snapshots', () => {
  const row = { id: '1', name: '8月', current: '100', baseline: '200', yoy_change: null, yoy_percent: null, reason: '' }
  const result = { rows: [row], selected_ids: ['1'], headline: '环比下降', detail: '', caveat: '', chart_unit: '%', figure: { data: [{ x: ['8月'], y: [-50] }], layout: {} } }
  expect(mapAnalysisResult(result)?.rows).toEqual([row])
  expect(mapAnalysisResult({ ...result, comparison: 'none', baseline_label: null })?.comparison).toBe('none')
  expect(mapAnalysisResult({ ...result, comparison: 'mom', baseline_label: '上月', rows: [{ ...row, comparison_change: '-100', comparison_percent: '-50', mom_change: '-100', mom_percent: '-50', expected: '120', deviation_percent: '-16.67', anomaly: false }] })?.comparison).toBe('mom')
  for (const field of ['comparison_change', 'comparison_percent', 'mom_change', 'mom_percent', 'expected', 'deviation_percent']) {
    expect(() => mapAnalysisResult({ ...result, rows: [{ ...row, [field]: 'Infinity' }] })).toThrow('无效数值')
  }
  expect(() => mapAnalysisResult({ ...result, rows: [{ ...row, anomaly: 'yes' }] })).toThrow('无效数值')
  expect(() => mapAnalysisResult({ ...result, comparison: 'wow' })).toThrow('比较方式')
  expect(() => mapAnalysisResult({ ...result, baseline_label: 10 })).toThrow('比较方式')
})
