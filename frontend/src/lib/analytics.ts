import fixture from '../data/fixture.json'

export const source = fixture
export const emptyDataset: Dataset = { version: 0, completeThrough: '', availableFrom: '', orderCount: null, regions: [], products: [], cells: [], access: { user_id: '', permission_version: 0, scope_hash: '', allowed_metrics: [], scope_label: '按账号授权' } }
export interface Access { user_id: string; permission_version: number; scope_hash: string; allowed_metrics: string[]; scope_label: string }
export interface DataCell { month: string; region: number; product: number; amount: number | null; cost: number | null; count: number | null; quantity: number | null; grossProfit?: number | null }
export interface Dataset { version: number; completeThrough: string; availableFrom: string; orderCount: number | null; regions: { id: number; name: string }[]; products: { id: number; name: string; category: string }[]; cells: DataCell[]; access?: Access }
export type Metric = 'amount' | 'count' | 'profit' | 'average'
export type ChartKind = 'line' | 'bar'
export type ViewKind = 'summary' | 'trend' | 'products' | 'regions'
export const viewNames: Record<ViewKind, string> = { summary: '汇总', trend: '趋势', products: '商品排名', regions: '区域对比' }
export interface Scope { months: string[]; region: number }
export interface Totals { amount: number | null; cost: number | null; count: number | null; quantity: number | null; profit: number | null; average: number | null }
export interface NamedTotal extends Totals { id: number; name: string; category?: string; growth: number | null; share: number | null }
export type Comparison = 'none' | 'yoy' | 'mom'
export interface AnalysisRow {
  id: string; name: string; current: string | null; baseline: string | null; yoy_change: string | null; yoy_percent: string | null; reason: string
  comparison_change?: string | null; comparison_percent?: string | null; mom_change?: string | null; mom_percent?: string | null
  anomaly?: boolean; expected?: string | null; deviation_percent?: string | null
}
export interface AnalysisResult {
  rows: AnalysisRow[]
  selected_ids: string[]; sort_by: string; sort_order: string
  comparison?: Comparison | null; baseline_label?: string | null
  headline: string; detail: string; caveat: string; chart_unit: string
  figure: { data: Record<string, unknown>[]; layout: Record<string, unknown> }
}
export interface Analysis {
  scope: Scope; metric: Metric; view: ViewKind; chart: ChartKind; question: string
  dataset?: Dataset
  queriedAt?: string
  comparison?: Comparison; sort_by?: 'value' | 'yoy_percent' | 'yoy_change' | 'mom_percent' | 'mom_change'; sort_order?: 'asc' | 'desc'; top_n?: number | null; goal?: 'overview' | 'largest_decline' | 'anomalies'
  product_ids?: number[]; anomaly_window?: number; anomaly_threshold_percent?: number
  analysisResult?: AnalysisResult
  artifactId?: string
  access?: Access
}
export function canMetric(metric: Metric, access?: Access) {
  if (!access) return false
  const required: Record<Metric, string[]> = { amount: ['sales_amount'], count: ['order_count'], profit: ['gross_profit'], average: ['avg_order_value', 'sales_amount', 'order_count'] }
  return required[metric].every(name => access.allowed_metrics.includes(name))
}
export function canDerived(name: string, dataset: Dataset) { return !dataset.access || dataset.access.allowed_metrics.includes(name) }
export const metricNames: Record<Metric, string> = { amount: '销售额', count: '订单数', profit: '毛利', average: '平均客单价' }
export const periodOptions = [
  { value: '6', label: '最近六个完整月', short: '近 6 个月', months: range(3, 8) },
  { value: '3', label: '最近三个完整月', short: '近 3 个月', months: range(6, 8) },
  { value: 'year', label: '2026 年至今（完整月）', short: '本年至今', months: range(1, 8) },
  { value: 'aug', label: '2026 年 8 月', short: '2026 年 8 月', months: ['2026-08'] },
]
export function range(start: number, end: number, year = 2026) {
  return Array.from({ length: end - start + 1 }, (_, i) => `${year}-${String(start + i).padStart(2, '0')}`)
}
export function previousYear(months: string[]) { return months.map(m => `${Number(m.slice(0, 4)) - 1}${m.slice(4)}`) }
export function covered(months: string[], dataset: Dataset = source) {
  return months.every(m => {
    const [year, month] = m.split('-').map(Number)
    const lastDay = new Date(Date.UTC(year!, month!, 0)).toISOString().slice(0, 10)
    return `${m}-01` >= dataset.availableFrom && lastDay <= dataset.completeThrough
  })
}
export function previousPeriod(months: string[]) {
  const start = months[0]!.split('-').map(Number)
  return Array.from({ length: months.length }, (_, i) => {
    const date = new Date(Date.UTC(start[0]!, start[1]! - 1 - months.length + i, 1))
    return date.toISOString().slice(0, 7)
  })
}
export function total(scope: Scope, product?: number, dataset: Dataset = source): Totals {
  const rows = dataset.cells.filter(c => scope.months.includes(c.month) && (!scope.region || c.region === scope.region) && (product === undefined || c.product === product))
  const sum = (field: 'amount' | 'cost' | 'count' | 'quantity', permission: string) => !canDerived(permission, dataset) || rows.some(row => row[field] === null) ? null : rows.reduce((n, row) => n + (row[field] ?? 0), 0)
  const amount = sum('amount', 'sales_amount'), cost = sum('cost', 'sales_cost'), count = sum('count', 'order_count'), quantity = sum('quantity', 'quantity')
  const profit = !canDerived('gross_profit', dataset) || rows.some(row => row.grossProfit === null || row.grossProfit === undefined && (row.amount === null || row.cost === null)) ? null : rows.reduce((n, row) => n + (row.grossProfit ?? (row.amount! - row.cost!)), 0)
  const average = canDerived('avg_order_value', dataset) && amount !== null && count !== null ? count ? amount / count : 0 : null
  return { amount, cost, count, quantity, profit, average }
}
export function growth(current: number | null, baseline: number | null): number | null { return current === null || baseline === null || baseline === 0 ? null : (current - baseline) / baseline * 100 }
export function scopeName(scope: Scope, dataset: Dataset = source) { return dataset.regions.find(r => r.id === scope.region)?.name ?? dataset.access?.scope_label ?? '全部区域' }
export function periodLabel(months: string[]) {
  if (!months.length) return '未选择期间'
  return months.length === 1 ? months[0]!.replace('-', ' 年 ') + ' 月' : `${months[0]!.replace('-', '.')} — ${months.at(-1)!.replace('-', '.')}`
}
export function ranking(scope: Scope, by: 'products' | 'regions', metric: Metric = 'amount', dataset: Dataset = source): NamedTotal[] {
  const full = total(scope, undefined, dataset)
  const baseScope = { ...scope, months: previousYear(scope.months) }
  const dimensions = by === 'products' ? dataset.products : dataset.regions.filter(r => !scope.region || r.id === scope.region)
  return dimensions.map(item => {
    const current = by === 'products' ? total(scope, item.id, dataset) : total({ ...scope, region: item.id }, undefined, dataset)
    const baseline = by === 'products' ? total(baseScope, item.id, dataset) : total({ ...baseScope, region: item.id }, undefined, dataset)
    return { ...current, ...item, growth: canDerived('growth_rate', dataset) && covered(baseScope.months, dataset) ? growth(current[metric], baseline[metric]) : null, share: !canDerived('product_share', dataset) || current.amount === null || full.amount === null ? null : full.amount ? current.amount / full.amount * 100 : 0 }
  }).sort((a, b) => (b[metric] ?? -Infinity) - (a[metric] ?? -Infinity))
}
export function percent(value: number | null, signed = true) { return value === null ? '无可比基期' : `${signed && value > 0 ? '+' : ''}${value.toFixed(1)}%` }
export function money(cents: number | null, compact = false) {
  if (cents === null) return '—'
  const amount = cents / 100
  return compact && Math.abs(amount) >= 10000 ? `${(amount / 10000).toFixed(2)} 万` : amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
export function metricValue(value: number | null, metric: Metric, compact = false) {
  if (value === null) return '—'
  return metric === 'count' ? value.toLocaleString('zh-CN') : `¥ ${money(value, compact)}`
}
export function comparisonKind(analysis: Analysis): Comparison { return analysis.analysisResult?.comparison ?? analysis.comparison ?? 'yoy' }
export function comparisonName(analysis: Analysis) { return comparisonKind(analysis) === 'mom' ? '环比' : comparisonKind(analysis) === 'yoy' ? '同比' : '比较' }
export function baselineLabel(analysis: Analysis) { return analysis.analysisResult?.baseline_label ?? (comparisonKind(analysis) === 'mom' ? analysis.view === 'trend' || analysis.scope.months.length === 1 ? '上月' : '上一期间' : '去年同期') }
export function comparisonPercent(row: AnalysisRow, analysis: Analysis) { return row.comparison_percent !== undefined ? row.comparison_percent : comparisonKind(analysis) === 'mom' ? row.mom_percent ?? null : comparisonKind(analysis) === 'yoy' ? row.yoy_percent : null }
export function comparisonChange(row: AnalysisRow, analysis: Analysis) { return row.comparison_change !== undefined ? row.comparison_change : comparisonKind(analysis) === 'mom' ? row.mom_change ?? null : comparisonKind(analysis) === 'yoy' ? row.yoy_change : null }
export function analysisRows(analysis: Analysis) { return analysis.analysisResult?.rows.filter(r => !analysis.top_n || analysis.goal === 'largest_decline' || analysis.goal === 'anomalies' || analysis.analysisResult!.selected_ids.includes(r.id)) ?? [] }
export function analysisTable(analysis: Analysis): (string | number)[][] {
  const result = analysis.analysisResult
  if (!result) return []
  const compare = comparisonKind(analysis) !== 'none' && analysis.goal !== 'anomalies'
  const anomalies = analysis.goal === 'anomalies'
  const unit = analysis.metric === 'count' ? '笔' : '元'
  return [
    ['名称', `本期（${unit}）`, ...(compare ? [baselineLabel(analysis), `${comparisonName(analysis)}增减（${unit}）`, `${comparisonName(analysis)}增长率（%）`] : []), ...(anomalies ? [`期望值（${unit}）`, '偏离率（%）', '检测结果'] : []), '说明'],
    ...result.rows.map(row => [row.name, row.current ?? '不可计算', ...(compare ? [row.baseline ?? '未覆盖', comparisonChange(row, analysis) ?? '不可计算', comparisonPercent(row, analysis) ?? '不可比'] : []), ...(anomalies ? [row.expected ?? '基期不足', row.deviation_percent ?? '不可比', row.anomaly ? '异常' : row.deviation_percent == null ? '不可检测' : '正常'] : []), row.reason]),
  ]
}
// 异常检测包含期望值曲线和独立异常标记，保留服务端图形语义。
export function figureTraces(analysis: Analysis) {
  return analysis.analysisResult?.figure.data.map(trace => analysis.goal === 'anomalies' ? { ...trace } : { ...trace, type: analysis.chart === 'bar' ? 'bar' : 'scatter', ...(analysis.chart === 'line' ? { mode: 'lines+markers' } : {}) }) ?? []
}
export function series(analysis: Analysis) {
  if (analysis.analysisResult) {
    const result = analysis.analysisResult
    const rows = analysisRows(analysis)
    const percentage = result.chart_unit === '%'
    const scale = percentage || analysis.metric === 'count' ? 1 : 100
    return { labels: rows.map(r => r.name), values: rows.map(r => { const value = percentage ? comparisonPercent(r, analysis) : r.current; return value === null ? null : Number(value) * scale }), baseline: comparisonKind(analysis) !== 'none' && !percentage ? rows.map(r => r.baseline === null ? null : Number(r.baseline) * scale) : [] }
  }
  const original = analysis.dataset ?? (analysis.artifactId || analysis.access ? emptyDataset : source)
  const dataset = analysis.product_ids?.length ? { ...original, products: original.products.filter(p => analysis.product_ids!.includes(p.id)), cells: original.cells.filter(c => analysis.product_ids!.includes(c.product)) } : original
  const compare = comparisonKind(analysis)
  const baseMonths = (months: string[]) => compare === 'mom' ? previousPeriod(months) : previousYear(months)
  if (analysis.view === 'summary') {
    const baselineMonths = baseMonths(analysis.scope.months)
    return { labels: [periodLabel(analysis.scope.months)], values: [total(analysis.scope, undefined, dataset)[analysis.metric]], baseline: compare === 'none' ? [] : [canDerived('growth_rate', dataset) && covered(baselineMonths, dataset) ? total({ ...analysis.scope, months: baselineMonths }, undefined, dataset)[analysis.metric] : null] }
  }
  if (analysis.view === 'products' || analysis.view === 'regions') {
    const rows = ranking(analysis.scope, analysis.view, analysis.metric, dataset).slice(0, 7)
    return { labels: rows.map(r => r.name), values: rows.map(r => r[analysis.metric]), baseline: compare === 'mom' && canDerived('growth_rate', dataset) ? rows.map(r => covered(baseMonths(analysis.scope.months), dataset) ? total({ ...analysis.scope, months: baseMonths(analysis.scope.months), ...(analysis.view === 'regions' ? { region: r.id } : {}) }, analysis.view === 'products' ? r.id : undefined, dataset)[analysis.metric] : null) : [] as number[] }
  }
  return {
    labels: analysis.scope.months.map(m => `${Number(m.slice(5))} 月`),
    values: analysis.scope.months.map(m => total({ ...analysis.scope, months: [m] }, undefined, dataset)[analysis.metric]),
    baseline: compare !== 'none' && canDerived('growth_rate', dataset) ? analysis.scope.months.map(month => { const [m] = baseMonths([month]); return covered([m!], dataset) ? total({ ...analysis.scope, months: [m!] }, undefined, dataset)[analysis.metric] : null }) : [],
  }
}
export function answer(analysis: Analysis) {
  const original = analysis.dataset ?? (analysis.artifactId || analysis.access ? emptyDataset : source)
  const dataset = analysis.product_ids?.length ? { ...original, products: original.products.filter(p => analysis.product_ids!.includes(p.id)), cells: original.cells.filter(c => analysis.product_ids!.includes(c.product)) } : original
  const current = total(analysis.scope, undefined, dataset)
  const baselineMonths = comparisonKind(analysis) === 'mom' ? previousPeriod(analysis.scope.months) : previousYear(analysis.scope.months)
  const baseline = total({ ...analysis.scope, months: baselineMonths }, undefined, dataset)
  const title = `${scopeName(analysis.scope, dataset)} · ${periodLabel(analysis.scope.months)}`
  if (analysis.analysisResult) return { title, headline: analysis.analysisResult.headline, detail: analysis.analysisResult.detail, caveat: analysis.analysisResult.caveat }
  const change = canDerived('growth_rate', dataset) ? growth(current[analysis.metric], baseline[analysis.metric]) : null
  const comparison = comparisonKind(analysis) === 'none' ? '' : !covered(baselineMonths, dataset) ? `${baselineLabel(analysis)}数据未完整覆盖，无法计算${comparisonName(analysis)}增长率。` : change === null ? `${baselineLabel(analysis)}为零或指标未授权，无法计算${comparisonName(analysis)}增长率。` : `较${baselineLabel(analysis)}${change >= 0 ? '增长' : '下降'} ${Math.abs(change).toFixed(1)}%。`
  let headline = `${metricNames[analysis.metric]}为 ${metricValue(current[analysis.metric], analysis.metric, true)}，${comparison}`
  if (analysis.view === 'products' || analysis.view === 'regions') {
    const first = ranking(analysis.scope, analysis.view, analysis.metric, dataset)[0]
    if (first && first[analysis.metric] !== null) headline = `${first.name}的${metricNames[analysis.metric]}排名第一，为 ${metricValue(first[analysis.metric], analysis.metric, true)}${first.share === null ? '。' : `；销售额占当前范围的 ${first.share.toFixed(1)}%。`}`
  }
  if (current.count === 0 && current.amount === 0) headline = '当前范围在已覆盖期间内没有已完成订单，销售额和订单数均为零。'
  return {
    title, headline,
    detail: [current.count === null ? '' : `统计 ${current.count.toLocaleString()} 笔已完成订单`, current.profit === null ? '' : `总毛利 ¥ ${money(current.profit, true)}`, current.average === null ? '' : `平均客单价 ¥ ${money(current.average)}`].filter(Boolean).join('，') + '。退款与取消订单已排除。',
    caveat: analysis.dataset ? '基于 MySQL 实时查询快照计算；仅统计已完成订单，业务原因需要额外证据验证。' : '基于固定演示数据计算；数字可核对，业务原因需要额外证据验证。',
  }
}

export type ParseResult = { kind: 'ready'; analysis: Analysis } | { kind: 'clarify'; message: string } | { kind: 'unsupported'; message: string }
export function parseQuestion(question: string, fallback: Scope, previous?: Analysis): ParseResult {
  const text = question.trim().replaceAll(/\s+/g, '')
  if (!/(销售|订单|毛利|客单价|商品|产品|区域|趋势|排名|同比|环比|华东|华南|华北|西南|华中|柱状|折线|六个?月|三个月)/.test(text)) {
    return { kind: 'unsupported', message: '演示模式支持销售趋势、商品排名、区域对比、订单数、毛利与客单价。试试“分析最近六个月的销售趋势”。' }
  }
  if (/原因|为什么|预测|库存|客户|销售员|目标|环比|毛利率|净利润|明细/.test(text) || source.products.some(p => text.includes(p.name))) {
    return { kind: 'unsupported', message: '这项分析需要额外数据或完整的 Agent 服务，当前演示尚不支持。可先查看销售趋势、同比或商品与区域排名。' }
  }
  const inherited = previous?.scope ?? fallback
  const scope: Scope = { months: [...inherited.months], region: inherited.region }
  let explicit = false
  const dates = [...text.matchAll(/(202\d)[年\-/](\d{1,2})月?/g)]
  const dated = dates[0]
  if (dates.length > 2 || dates.some(d => Number(d[2]) < 1 || Number(d[2]) > 12)) return { kind: 'unsupported', message: '请选择有效的月份或连续月份范围，例如“2026年3月至2026年8月的销售趋势”。' }
  if (dated) {
    scope.months = [`${dated[1]}-${dated[2]!.padStart(2, '0')}`]
    if (dates[1]) {
      const end = `${dates[1][1]}-${dates[1][2]!.padStart(2, '0')}`
      if (end < scope.months[0]!) return { kind: 'unsupported', message: '结束月份不能早于开始月份，请调整日期范围。' }
      const start = Number(dated[1]) * 12 + Number(dated[2]) - 1
      const count = Number(dates[1][1]) * 12 + Number(dates[1][2]) - 1 - start + 1
      scope.months = Array.from({ length: count }, (_, i) => `${Math.floor((start + i) / 12)}-${String((start + i) % 12 + 1).padStart(2, '0')}`)
    }
    explicit = true
  }
  else if (/最近|近|本年|今年|至今|上个月|上月/.test(text)) {
    scope.months = /本年|今年|至今/.test(text) ? range(1, 8) : /上个月|上月/.test(text) ? ['2026-08'] : range(3, 8)
    const recent = text.match(/(?:最近|近)([一二三四六五七八九十两\d]+)个?(?:完整)?月/)
    if (recent) {
      const counts: Record<string, number> = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10, 十一: 11, 十二: 12 }
      const count = counts[recent[1]!] ?? Number(recent[1])
      if (!Number.isInteger(count) || count < 1 || count > 24) return { kind: 'unsupported', message: '演示支持最近 1 至 24 个完整月，请选择有效范围。' }
      scope.months = Array.from({ length: count }, (_, i) => {
        const d = new Date(Date.UTC(2026, 8 - count + i, 1)); return d.toISOString().slice(0, 7)
      })
    }
    else if (/最近|近/.test(text)) return { kind: 'clarify', message: '请明确完整月份范围，例如“最近六个月”，也可选择下方期间。' }
    explicit = true
  }
  if (/本月|这个月|9月|九月|今天|昨天|季度|周|日|天|2027|2028|2029/.test(text) || (dated && (Number(dated[2]) < 1 || Number(dated[2]) > 12))) {
    return { kind: 'unsupported', message: '演示数据完整月截至 2026 年 8 月，当前支持按月统计。2026 年 9 月数据不完整，请选择完整月份再分析。' }
  }
  if (scope.months.some(m => m < '2024-02' || m > '2026-08')) return { kind: 'unsupported', message: '请求的月份超出演示数据覆盖范围（2024 年 2 月至 2026 年 8 月完整月），缺失数据不能当作零销售。' }
  const region = source.regions.find(r => text.includes(r.name))
  if (region) scope.region = region.id
  if (/全国|全部区域|全企业|所有区域/.test(text)) scope.region = 0
  if (!explicit && !previous) return { kind: 'clarify', message: '你想分析哪个时间范围？请选择完整月份，确定统计口径。' }
  const metric: Metric = /毛利/.test(text) ? 'profit' : /订单数|订单量/.test(text) ? 'count' : /客单价/.test(text) ? 'average' : /销售额|销售总额/.test(text) ? 'amount' : previous?.metric ?? 'amount'
  const view: ViewKind = /商品|产品/.test(text) ? 'products' : /区域.*(对比|排名)|各区域/.test(text) ? 'regions' : /趋势|销售额|销售趋势/.test(text) ? 'trend' : previous?.view ?? 'trend'
  const chart: ChartKind = /柱状/.test(text) ? 'bar' : /折线/.test(text) ? 'line' : view === 'trend' ? previous?.chart ?? 'line' : 'bar'
  return { kind: 'ready', analysis: { question: text, scope, metric, view, chart } }
}
