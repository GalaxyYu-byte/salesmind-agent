import type { Access, AnalysisResult, Dataset, Scope } from './analytics'
import { ApiError, authFetch, getAccessGeneration, observeAccess, readAccessJson } from './auth'

export interface SalesOverview {
  dataset: Dataset
  scope: Scope
  queriedAt: string
  analysisResult?: AnalysisResult
  access: Access
  accessEpoch: number
  cache?: { hit: boolean; ttl_seconds: number }
  totals: { sales_amount?: string | null; gross_profit?: string | null; order_count?: number | null; quantity?: number | null }
}

function integer(value: unknown) {
  if (typeof value !== 'number' && typeof value !== 'string') throw new Error('接口金额或数量格式无效')
  const parsed = Number(value)
  if (!Number.isSafeInteger(parsed) || parsed < 0) throw new Error('接口金额或数量超出可安全展示范围')
  return parsed
}

export async function fetchSalesOverview(scope: Scope, signal?: AbortSignal, refresh = false): Promise<SalesOverview> {
  const params = new URLSearchParams({ start_month: scope.months[0]!, end_month: scope.months.at(-1)! })
  if (scope.region) params.set('region_id', String(scope.region))
  if (refresh) params.set('refresh', 'true')
  const response = await authFetch(`/api/v1/sales/overview?${params}`, { signal, cache: 'no-store' })
  if (!response.ok) {
    let detail = '销售查询失败，请检查后端服务后重试'
    try { const body = await response.json(); if (typeof body.detail === 'string') detail = body.detail } catch { /* HTTP 代理错误不一定是 JSON。 */ }
    throw new ApiError(`${detail}（${response.status}）`, response.status)
  }
  const result = await readAccessJson(response)
  return mapSalesOverview(result, scope)
}

export function mapAnalysisResult(value: unknown): AnalysisResult | undefined {
  if (value === undefined) return undefined
  const result = value as AnalysisResult
  const numeric = (v: unknown) => v === null || typeof v === 'string' && /^-?\d+(?:\.\d+)?$/.test(v) && Number.isFinite(Number(v))
  if (!result || !Array.isArray(result.rows) || !Array.isArray(result.selected_ids) || !result.selected_ids.every(id => typeof id === 'string') || !['%', '元', '笔'].includes(result.chart_unit) || typeof result.headline !== 'string' || typeof result.detail !== 'string' || typeof result.caveat !== 'string' || !result.figure || !Array.isArray(result.figure.data) || !result.figure.layout) throw new Error('分析结果快照格式无效')
  if (!result.rows.every(r => typeof r.id === 'string' && typeof r.name === 'string' && typeof r.reason === 'string' && [r.current, r.baseline, r.yoy_change, r.yoy_percent].every(numeric)) || !result.figure.data.every(trace => Array.isArray(trace.x) && Array.isArray(trace.y) && trace.x.length === trace.y.length && trace.y.every(y => y === null || typeof y === 'number' && Number.isFinite(y)))) throw new Error('分析结果包含无效数值')
  if (result.comparison != null && !['none', 'yoy', 'mom'].includes(result.comparison) || result.baseline_label != null && typeof result.baseline_label !== 'string') throw new Error('分析比较方式格式无效')
  if (!result.rows.every(row => [row.comparison_change, row.comparison_percent, row.mom_change, row.mom_percent, row.expected, row.deviation_percent].every(v => v === undefined || numeric(v)) && (row.anomaly === undefined || typeof row.anomaly === 'boolean'))) throw new Error('分析结果包含无效数值')
  return result
}

// 实时查询与持久化结果快照共用同一组金额、范围和来源校验。
export function mapSalesOverview(result: any, scope: Scope): SalesOverview {
  const data = result.dataset, access = result.access as Access
  if (result.source !== 'mysql' || !data || !Array.isArray(data.cells) || !Array.isArray(data.regions) || !Array.isArray(data.products) || !Array.isArray(result.months) || !result.totals) throw new Error('销售接口返回格式无效')
  if (result.months.join(',') !== scope.months.join(',') || (result.region_id ?? 0) !== scope.region) throw new Error('销售接口返回的范围与请求不一致')
  if (!access || typeof access.user_id !== 'string' || !Number.isSafeInteger(access.permission_version) || typeof access.scope_hash !== 'string' || typeof access.scope_label !== 'string' || !Array.isArray(access.allowed_metrics) || !access.allowed_metrics.every(m => typeof m === 'string')) throw new Error('销售接口缺少有效权限范围，请重新查询')
  const cents = (value: string) => {
    if (!/^-?\d+\.\d{2}$/.test(value)) throw new Error('接口汇总金额格式无效')
    const parsed = Number(value.replace('.', ''))
    if (!Number.isSafeInteger(parsed)) throw new Error('汇总金额超出可安全展示范围')
    return parsed
  }
  const nullableInteger = (value: unknown, signed = false) => {
    if (value === undefined || value === null) return null
    if (!signed) return integer(value)
    if (typeof value !== 'number' && typeof value !== 'string' || !Number.isSafeInteger(Number(value))) throw new Error('接口金额超出可安全展示范围')
    return Number(value)
  }
  const dataset: Dataset = {
    version: integer(data.version), completeThrough: data.complete_through,
    availableFrom: data.available_from, orderCount: nullableInteger(data.order_count), access,
    regions: data.regions, products: data.products,
    cells: data.cells.map((c: Record<string, unknown>) => ({ month: String(c.month), region: integer(c.region), product: integer(c.product), amount: nullableInteger(c.amount), cost: nullableInteger(c.cost), count: nullableInteger(c.count), quantity: nullableInteger(c.quantity), grossProfit: nullableInteger(c.gross_profit, true) })),
  }
  const current = dataset.cells.filter(c => scope.months.includes(c.month))
  for (const [field, aggregate, permission] of [['amount', 'sales_amount', 'sales_amount'], ['grossProfit', 'gross_profit', 'gross_profit'], ['count', 'order_count', 'order_count'], ['quantity', 'quantity', 'quantity']] as const) {
    const expected = result.totals[aggregate]
    if (!access.allowed_metrics.includes(permission)) {
      if (expected != null || dataset.cells.some(c => c[field] !== null)) throw new Error('销售接口返回未授权指标')
      continue
    }
    if (expected == null || current.some(c => c[field] === null)) throw new Error('销售接口缺少已授权指标')
    const parsed = aggregate === 'sales_amount' || aggregate === 'gross_profit' ? cents(expected) : integer(expected)
    if (current.reduce((sum, c) => sum + c[field]!, 0) !== parsed) throw new Error('销售汇总与明细聚合不一致')
  }
  if (!access.allowed_metrics.includes('sales_cost') && dataset.cells.some(c => c.cost !== null)) throw new Error('销售接口返回未授权成本')
  observeAccess(access)
  if (result.cache !== undefined && (!result.cache || typeof result.cache.hit !== 'boolean' || !Number.isSafeInteger(result.cache.ttl_seconds) || result.cache.ttl_seconds < 0)) throw new Error('查询缓存状态格式无效')
  return { dataset, scope: { months: [...result.months], region: result.region_id ?? 0 }, queriedAt: result.queried_at, totals: result.totals, access, accessEpoch: getAccessGeneration(), analysisResult: mapAnalysisResult(result.analysis_result), cache: result.cache }
}
