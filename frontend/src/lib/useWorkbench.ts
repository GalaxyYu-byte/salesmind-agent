import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { analysisTable, answer, baselineLabel, canMetric, metricNames, money, periodLabel, periodOptions, ranking, scopeName, series, source, total, viewNames } from './analytics'
import type { Access, Analysis, Dataset, Scope } from './analytics'
import { fetchSalesOverview, mapSalesOverview } from './salesApi'
import { csv, download, readState, saveState } from './storage'
import type { Report } from './storage'
import { assertAccessGeneration, getAccessGeneration, invalidateAccess, onAccessInvalidated, useAuth } from './auth'
import { analysisSpec, readArtifact } from './conversationsApi'
import { useConversations } from './useConversations'

export function useWorkbench() {
  const userId = useAuth().user.value?.id
  const saved = readState(userId)
  const reports = ref<Report[]>(saved.reports)
  const page = ref('overview')
  const period = ref('6')
  const region = ref(0)
  const prompt = ref('')
  const sidebarOpen = ref(false)
  const toast = ref('')
  const salesData = shallowRef<Dataset | null>(null)
  const salesLoading = ref(false)
  const salesError = ref('')
  const queryTime = ref('')
  const cacheHit = ref(false)
  const access = shallowRef<Access | undefined>()
  const permissionEpoch = ref(0)
  let queryController: AbortController | undefined
  let querySequence = 0
  const scope = computed<Scope>(() => ({ months: [...(periodOptions.find(p => p.value === period.value) ?? periodOptions[0]!).months], region: region.value }))
  async function refreshSales(refresh = true) {
    const sequence = ++querySequence
    queryController?.abort()
    queryController = new AbortController()
    salesLoading.value = true; salesError.value = ''; salesData.value = null
    try {
      const result = await fetchSalesOverview(scope.value, queryController.signal, refresh)
      if (sequence !== querySequence) return
      assertAccessGeneration(result.accessEpoch)
      salesData.value = result.dataset; access.value = result.access; queryTime.value = result.queriedAt; cacheHit.value = result.cache?.hit ?? false
    } catch (error) {
      if (sequence !== querySequence) return
      salesError.value = error instanceof Error ? error.message : '销售服务暂时不可用'
    } finally { if (sequence === querySequence) salesLoading.value = false }
  }
  watch(scope, () => { void refreshSales(false) }, { immediate: true })
  let toastTimer: ReturnType<typeof setTimeout> | undefined
  let storageWarning = false
  const { sessions, currentId, currentSession, lastAnalysis, active, pending, stages, historyLoading, historyError, submitting, loadSessions, newSession, openSession, send, stop, resume, clarify, removeSession } = useConversations(prompt, notify, navigate)
  const unsubscribeAccess = onAccessInvalidated(() => {
    salesData.value = null; access.value = undefined; queryTime.value = ''; cacheHit.value = false; permissionEpoch.value++
    salesError.value = '账号权限已变化，请重新查询当前可见数据'
    reports.value.forEach(report => { report.analysis = undefined })
  })
  const allowedMetrics = computed(() => (Object.keys(metricNames) as Analysis['metric'][]).filter(metric => canMetric(metric, access.value)))
  const can = (metric: Analysis['metric']) => canMetric(metric, access.value)
  const canExtra = (name: string) => access.value?.allowed_metrics.includes(name) ?? false

  function notify(message: string) {
    toast.value = message
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => { toast.value = '' }, 3200)
  }
  watch(reports, () => {
    if (!saveState({ sessions: [], reports: reports.value }, userId) && !storageWarning) {
      storageWarning = true; notify('浏览器存储不可用，本地报告可能无法保存。')
    }
  }, { deep: true })
  function navigate(target: string) { page.value = target; sidebarOpen.value = false }
  function saveReport(analysis: Analysis) {
    if (!analysis.artifactId) { notify('这次分析没有服务端结果，请重新分析后保存'); return }
    const spec = analysisSpec(analysis)
    const exists = reports.value.find(r => r.artifactId === analysis.artifactId)
    if (exists) { notify('这份分析已经保存在报告中'); return }
    reports.value.unshift({ id: crypto.randomUUID(), title: `${scopeName(analysis.scope, analysis.dataset)}${metricNames[analysis.metric]}${viewNames[analysis.view]}分析`, created: new Date().toISOString(), artifactId: analysis.artifactId, spec })
    notify('报告已保存，可在“分析报告”中查看')
  }
  async function verifiedAnalysis(analysis: Analysis): Promise<Analysis> {
    const accessEpoch = getAccessGeneration()
    if (!analysis.artifactId) throw new Error('缺少服务端结果，请重新分析')
    const artifact = await readArtifact(analysis.artifactId)
    assertAccessGeneration(accessEpoch)
    if (!artifact.schema?.analysis) throw new Error('历史结果缺少有效分析条件，请重新分析')
    const spec = { ...artifact.schema.analysis, chart: analysis.chart }
    const overview = mapSalesOverview(artifact.payload, spec.scope)
    assertAccessGeneration(accessEpoch)
    if (!canMetric(spec.metric, overview.access)) throw new Error('当前账号无权查看这个指标')
    return { ...spec, question: analysis.question, artifactId: analysis.artifactId, dataset: overview.dataset, access: overview.access, queriedAt: overview.queriedAt, analysisResult: overview.analysisResult }
  }
  async function openReport(report: Report): Promise<Report | null> {
    const accessEpoch = getAccessGeneration()
    report.analysis = undefined
    try { const analysis = await verifiedAnalysis({ ...report.spec, artifactId: report.artifactId, question: report.title }); assertAccessGeneration(accessEpoch); report.spec = analysisSpec(analysis); report.analysis = analysis; return report }
    catch (error) { notify(error instanceof Error ? error.message : '报告暂时无法读取'); return null }
  }
  async function exportAnalysis(input: Analysis) {
    const accessEpoch = getAccessGeneration()
    try {
      if (input.artifactId) { const result = await verifiedAnalysis(input); assertAccessGeneration(accessEpoch); exportVerifiedAnalysis(result); return }
      const fresh = await fetchSalesOverview(input.scope, undefined, true)
      assertAccessGeneration(accessEpoch)
      if (!canMetric(input.metric, fresh.access)) throw new Error('当前账号无权查看这个指标')
      exportVerifiedAnalysis({ ...analysisSpec(input), question: input.question, dataset: fresh.dataset, access: fresh.access, queriedAt: fresh.queriedAt })
    } catch (error) { invalidateAccess(); notify(error instanceof Error ? error.message : '导出失败，请重新分析') }
  }
  function exportVerifiedAnalysis(analysis: Analysis) {
    if (analysis.analysisResult) {
      const result = analysis.analysisResult
      const rows: (string | number)[][] = [['统计范围', scopeName(analysis.scope, analysis.dataset ?? source), periodLabel(analysis.scope.months)], ['查询时间', analysis.queriedAt ?? ''], ['排序', result.sort_by, result.sort_order], ...analysisTable(analysis)]
      download(`SalesMind-${metricNames[analysis.metric]}-${analysis.scope.months[0]}.csv`, csv(rows)); notify('数据已导出'); return
    }
    const chart = series(analysis)
    const format = (v: number | null) => v === null ? '不可计算' : analysis.metric === 'count' ? v : (v / 100).toFixed(2)
    const rows: (string | number)[][] = [
      ['数据来源', analysis.dataset ? `MySQL 查询快照 v${analysis.dataset.version}` : `SalesMind 固定演示数据 v${source.version}`],
      ['统计范围', scopeName(analysis.scope, analysis.dataset), periodLabel(analysis.scope.months)],
      ['口径', '仅已完成订单；退款和取消订单已排除'],
      [analysis.view === 'trend' ? '月份' : '名称', metricNames[analysis.metric] + (analysis.metric === 'count' ? '（笔）' : '（元）'), ...(chart.baseline.length ? [baselineLabel(analysis)] : [])],
      ...chart.labels.map((label, i) => [analysis.view === 'trend' ? analysis.scope.months[i]! : label, format(chart.values[i]!), ...(chart.baseline.length ? [chart.baseline[i] === null ? '数据未覆盖' : format(chart.baseline[i]!)] : [])]),
    ]
    download(`SalesMind-${metricNames[analysis.metric]}-${analysis.scope.months[0]}.csv`, csv(rows)); notify('数据已导出')
  }
  async function exportReport(report: Report) {
    const accessEpoch = getAccessGeneration()
    const loaded = await openReport(report)
    if (!loaded?.analysis) return
    try { assertAccessGeneration(accessEpoch) } catch (error) { notify(error instanceof Error ? error.message : '权限已变化，请重新读取'); return }
    const analysis = loaded.analysis, result = answer(analysis), chart = series(analysis)
    const table = analysisTable(analysis)
    const tableLine = (row: (string | number)[]) => '| ' + row.map(value => String(value).replaceAll('|', '\\|').replaceAll(/\r?\n/g, ' ')).join(' | ') + ' |'
    const statistics = table.length ? [tableLine(table[0]!), tableLine(table[0]!.map(() => '---')), ...table.slice(1).map(tableLine)].join('\n') : chart.labels.map((name, i) => `- ${name}：${chart.values[i] === null ? '不可计算' : analysis.analysisResult?.chart_unit === '%' ? chart.values[i]?.toFixed(2) + '%' : analysis.metric === 'count' ? chart.values[i] : '¥ ' + money(chart.values[i]!)}`).join('\n')
    const content = `# ${report.title}\n\n${result.title}\n\n${result.headline}\n\n${result.detail}\n\n## 统计数据\n\n${statistics}\n\n数据来源：MySQL 查询快照 v${analysis.dataset!.version}。查询时间：${analysis.queriedAt ?? '未记录'}。仅已完成订单，排除退款和取消订单。\n\n${result.caveat}\n`
    download(`${report.title}.md`, content, 'text/markdown;charset=utf-8'); notify('报告已导出为 Markdown')
  }
  async function exportTable() {
    if (salesLoading.value) { notify('请等待真实查询成功后再导出'); return }
    try {
    const currentScope = { months: [...scope.value.months], region: scope.value.region }
    const fresh = await fetchSalesOverview(currentScope, undefined, true)
    assertAccessGeneration(fresh.accessEpoch)
    const dataset = fresh.dataset
    const permitted = (name: string) => fresh.access.allowed_metrics.includes(name)
    const columns = [['amount', 'sales_amount', '销售额（元）'], ['count', 'order_count', '订单数（笔）'], ['profit', 'gross_profit', '毛利（元）']] as const
    const visible = columns.filter(([, name]) => permitted(name))
    const margin = permitted('gross_margin') && permitted('gross_profit') && permitted('sales_amount')
    const rows: (string | number)[][] = [
      ['MySQL 实时查询', `v${dataset.version}`, periodLabel(currentScope.months), scopeName(currentScope, dataset)],
      ['查询时间', fresh.queriedAt],
      ['月份', '区域', ...visible.map(([, , label]) => label), ...(margin ? ['毛利率'] : [])],
    ]
    for (const month of currentScope.months) for (const item of dataset.regions.filter(r => !currentScope.region || r.id === currentScope.region)) {
      const result = total({ months: [month], region: item.id }, undefined, dataset)
      rows.push([month, item.name, ...visible.map(([key]) => result[key] === null ? '—' : key === 'count' ? result[key]! : (result[key]! / 100).toFixed(2)), ...(margin ? [result.amount && result.profit !== null ? (result.profit / result.amount * 100).toFixed(2) + '%' : '—'] : [])])
    }
    download('SalesMind-区域月度数据.csv', csv(rows)); notify('区域月度汇总已导出')
    } catch (error) { salesData.value = null; notify(error instanceof Error ? error.message : '导出失败，请重新查询') }
  }
  const overview = computed<Analysis>(() => ({ scope: scope.value, metric: allowedMetrics.value[0] ?? 'amount', view: 'trend', chart: 'line', question: '销售趋势分析', dataset: salesData.value ?? undefined, access: access.value, queriedAt: queryTime.value }))
  const totals = computed(() => total(scope.value, undefined, salesData.value ?? { ...source, cells: [] }))
  const regionRows = computed(() => salesData.value ? ranking(scope.value, 'regions', 'amount', salesData.value) : [])
  onBeforeUnmount(() => { unsubscribeAccess(); queryController?.abort(); clearTimeout(toastTimer) })
  return { sessions, reports, page, period, region, prompt, currentId, sidebarOpen, toast, scope, currentSession, pending, active, lastAnalysis, stages, overview, totals, regionRows, salesData, salesLoading, salesError, queryTime, cacheHit, access, allowedMetrics, permissionEpoch, can, canExtra, refreshSales, historyLoading, historyError, submitting, loadSessions, notify, navigate, newSession, openSession, send, stop, resume, clarify, saveReport, openReport, exportAnalysis, exportReport, exportTable, removeSession }
}
