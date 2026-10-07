import type { Dataset, Scope } from './analytics'
import { authFetch } from './auth'

export interface SalesOverview {
  dataset: Dataset
  scope: Scope
  queriedAt: string
  totals: { sales_amount: string; gross_profit: string; order_count: number; quantity: number }
}

function integer(value: unknown) {
  if (typeof value !== 'number' && typeof value !== 'string') throw new Error('接口金额或数量格式无效')
  const parsed = Number(value)
  if (!Number.isSafeInteger(parsed) || parsed < 0) throw new Error('接口金额或数量超出可安全展示范围')
  return parsed
}

export async function fetchSalesOverview(scope: Scope, signal?: AbortSignal): Promise<SalesOverview> {
  const params = new URLSearchParams({ start_month: scope.months[0]!, end_month: scope.months.at(-1)! })
  if (scope.region) params.set('region_id', String(scope.region))
  const response = await authFetch(`/api/v1/sales/overview?${params}`, { signal, cache: 'no-store' })
  if (!response.ok) {
    let detail = '销售查询失败，请检查后端服务后重试'
    try { const body = await response.json(); if (typeof body.detail === 'string') detail = body.detail } catch { /* HTTP 代理错误不一定是 JSON。 */ }
    throw new Error(`${detail}（${response.status}）`)
  }
  const result = await response.json()
  const data = result.dataset
  if (result.source !== 'mysql' || !data || !Array.isArray(data.cells) || !Array.isArray(data.regions) || !Array.isArray(data.products) || !Array.isArray(result.months) || !result.totals) throw new Error('销售接口返回格式无效')
  if (result.months.join(',') !== scope.months.join(',') || (result.region_id ?? 0) !== scope.region) throw new Error('销售接口返回的范围与请求不一致')
  const cents = (value: string) => {
    if (!/^-?\d+\.\d{2}$/.test(value)) throw new Error('接口汇总金额格式无效')
    const parsed = Number(value.replace('.', ''))
    if (!Number.isSafeInteger(parsed)) throw new Error('汇总金额超出可安全展示范围')
    return parsed
  }
  const dataset: Dataset = {
    version: integer(data.version), completeThrough: data.complete_through,
    availableFrom: data.available_from, orderCount: integer(data.order_count),
    regions: data.regions, products: data.products,
    cells: data.cells.map((c: Record<string, unknown>) => ({ month: String(c.month), region: integer(c.region), product: integer(c.product), amount: integer(c.amount), cost: integer(c.cost), count: integer(c.count), quantity: integer(c.quantity) })),
  }
  const current = dataset.cells.filter(c => scope.months.includes(c.month))
  if (current.reduce((sum, c) => sum + c.amount, 0) !== cents(result.totals.sales_amount) || current.reduce((sum, c) => sum + c.amount - c.cost, 0) !== cents(result.totals.gross_profit) || current.reduce((sum, c) => sum + c.count, 0) !== result.totals.order_count) throw new Error('销售汇总与明细聚合不一致')
  return { dataset, scope: { months: [...result.months], region: result.region_id ?? 0 }, queriedAt: result.queried_at, totals: result.totals }
}
