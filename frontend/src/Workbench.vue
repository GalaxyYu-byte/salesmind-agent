<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowDown, ArrowDownToLine, ArrowRight, ArrowUp, ArrowUpRight, BarChart3, BookOpen, CalendarDays, Check, CheckCheck, ChevronDown, ChevronRight, CircleHelp, Clock3, Coins, Database, FileBarChart2, FileText, Grid2X2, Layers3, Leaf, Lightbulb, LineChart, LoaderCircle, Menu, MessageSquare, MoreHorizontal, Pause, Play, Plus, Search, Send, Settings2, ShieldCheck, ShoppingBag, Sparkles, Square, Trash2, TrendingUp, Wallet, X } from 'lucide-vue-next'
import SalesChart from './components/SalesChart.vue'
import AnalysisResultTable from './components/AnalysisResultTable.vue'
import { answer, comparisonKind, comparisonName, growth, metricNames, money, percent, periodLabel, periodOptions, previousYear, ranking, scopeName, series, source, total, viewNames } from './lib/analytics'
import type { Analysis, Metric } from './lib/analytics'
import { useWorkbench } from './lib/useWorkbench'
import type { Message, Report } from './lib/storage'
import { useAuth } from './lib/auth'

const { sessions, reports, page, period, region, prompt, sidebarOpen, toast, scope, currentSession, pending, active, stages, overview, totals, regionRows, salesData, salesLoading, salesError, queryTime, cacheHit, access, allowedMetrics, permissionEpoch, can, canExtra, refreshSales, historyLoading, historyError, submitting, loadSessions, notify, navigate, newSession, openSession, send, stop, resume, clarify, saveReport, openReport, exportAnalysis, exportReport, exportTable, removeSession } = useWorkbench()
const { user, logout } = useAuth()
const logoutBusy = ref(false)
const logoutError = ref('')
const avatarText = computed(() => user.value?.display_name.slice(0, 1) ?? '')
async function signOut() {
  logoutBusy.value = true; logoutError.value = ''
  try { await logout() } catch (error) { logoutError.value = error instanceof Error ? error.message : '退出失败，请重试' }
  finally { logoutBusy.value = false }
}
const livePage = computed(() => page.value === 'overview' || page.value === 'data')
const displaySource = computed(() => salesData.value ?? { ...source, regions: [], products: [], cells: [], orderCount: null })
const nav = [
  { id: 'overview', label: '工作台', icon: Grid2X2 },
  { id: 'analysis', label: '智能分析', icon: Sparkles },
  { id: 'reports', label: '分析报告', icon: FileBarChart2 },
  { id: 'data', label: '数据概览', icon: Database },
]
const selectedMetric = ref<Metric>('amount')
const chartType = ref<'line' | 'bar'>('line')
const rankingTab = ref<'regions' | 'products'>('regions')
const modal = ref<'help' | 'settings' | 'search' | null>(null)
const searchText = ref('')
const searchInput = ref<HTMLInputElement>()
const dialogElement = ref<HTMLElement>()
let returnFocus: HTMLElement | null = null
const tableSearch = ref('')
const tablePage = ref(1)
const tableSort = ref<'month' | 'amount'>('month')
const selectedReport = ref<Report | null>(null)
const deleteTarget = ref<{ kind: 'session' | 'report'; id: string; title: string } | null>(null)
const messagesEnd = ref<HTMLDivElement>()
const date = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())
const dashboardChart = computed<Analysis>(() => ({ ...overview.value, metric: selectedMetric.value, chart: chartType.value }))
const baseline = computed(() => total({ ...scope.value, months: previousYear(scope.value.months) }, undefined, salesData.value ?? { ...source, cells: [] }))
const kpis = computed(() => [
  { metric: 'amount' as const, label: '销售总额', icon: Wallet, value: totals.value.amount, base: baseline.value.amount, unit: 'CNY' },
  { metric: 'count' as const, label: '成交订单', icon: ShoppingBag, value: totals.value.count, base: baseline.value.count, unit: '笔' },
  { metric: 'profit' as const, label: '销售毛利', icon: Coins, value: totals.value.profit, base: baseline.value.profit, unit: 'CNY' },
  { metric: 'average' as const, label: '平均客单价', icon: TrendingUp, value: totals.value.average, base: baseline.value.average, unit: 'CNY' },
].filter(card => can(card.metric)))
const leaderboard = computed(() => salesData.value ? ranking(scope.value, rankingTab.value, 'amount', salesData.value).slice(0, 5) : [])
const products = computed(() => salesData.value ? ranking(scope.value, 'products', 'amount', salesData.value).filter(p => p.amount !== null && p.amount > 0).slice(0, 4) : [])
const bestRegion = computed(() => [...regionRows.value].filter(r => r.growth !== null && (r.count ?? 0) > 0).sort((a, b) => (b.growth ?? 0) - (a.growth ?? 0))[0])
const bestMonth = computed(() => {
  const values = series({ ...overview.value, metric: 'amount' })
  const index = values.values.indexOf(Math.max(...values.values.filter((v): v is number => v !== null)))
  return { name: values.labels[index], amount: values.values[index] ?? 0 }
})
const overallGrowth = computed(() => canExtra('growth_rate') ? growth(totals.value.amount, baseline.value.amount) : null)
const questions = [
  { label: '看看销售趋势', text: '分析最近六个有完整数据的月份的销售额趋势和同比', icon: LineChart },
  { label: '哪些商品最畅销？', text: '最近六个有完整数据的月份的商品销售额排名', icon: ShoppingBag },
  { label: '对比各区域表现', text: '最近六个有完整数据的月份各区域销售额对比', icon: Layers3 },
  { label: '看看环比变化', text: '分析最近六个有完整数据的月份的销售额趋势和环比', icon: TrendingUp },
  { label: '对比指定商品', text: '最近六个有完整数据的月份笔记本电脑和显示器的销售额对比', icon: ShoppingBag },
  { label: '检查销售异常', text: '检测最近六个有完整数据的月份的销售额异常', icon: CircleHelp },
]
function analysisConditions(analysis: Analysis) {
  const conditions: string[] = []
  if (analysis.goal === 'anomalies') conditions.push(`异常检测（前 ${analysis.anomaly_window ?? 3} 月均值，偏离阈值 ${analysis.anomaly_threshold_percent ?? 30}%）`)
  else if (comparisonKind(analysis) !== 'none') conditions.push(`${comparisonName(analysis)}比较`)
  if (analysis.product_ids?.length) conditions.push(analysis.product_ids.map(id => (analysis.dataset ?? source).products.find(p => p.id === id)?.name ?? `商品 ${id}`).join('、'))
  const sortNames: Record<string, string> = { yoy_percent: '同比百分比', yoy_change: '同比增减', mom_percent: '环比百分比', mom_change: '环比增减' }
  if (analysis.sort_by && sortNames[analysis.sort_by]) conditions.push(`${sortNames[analysis.sort_by]}${analysis.sort_order === 'asc' ? '升序' : '降序'}`)
  return conditions.join(' · ')
}
const statusLabels: Record<Message['status'], string> = { running: '分析中', awaiting_input: '等待补充', interrupted: '已暂停', cancelled: '已取消', completed: '已完成', failed: '未完成' }
const filteredSearch = computed(() => sessions.value.filter(s => s.title.toLowerCase().includes(searchText.value.toLowerCase())))
const dataRows = computed(() => !salesData.value ? [] : scope.value.months.flatMap(month => salesData.value!.regions.filter(r => !scope.value.region || r.id === scope.value.region).map(r => ({ month, name: r.name, id: `${month}-${r.id}`, ...total({ months: [month], region: r.id }, undefined, salesData.value!) }))).filter(r => `${r.month} ${r.name}`.includes(tableSearch.value.trim())).sort((a, b) => tableSort.value === 'amount' ? (b.amount ?? 0) - (a.amount ?? 0) : b.month.localeCompare(a.month) || (b.amount ?? 0) - (a.amount ?? 0)))
const visibleDataRows = computed(() => dataRows.value.slice((tablePage.value - 1) * 10, tablePage.value * 10))
const pageCount = computed(() => Math.max(1, Math.ceil(dataRows.value.length / 10)))
watch([scope, tableSearch, tableSort], () => { tablePage.value = 1 })
watch(modal, async value => {
  if (value) {
    returnFocus = document.activeElement as HTMLElement
    await nextTick()
    if (value === 'search') searchInput.value?.focus()
    else dialogElement.value?.focus()
  } else returnFocus?.focus()
})
watch(() => currentSession.value?.messages.map(m => `${m.id}:${m.status}`).join(','), async () => { await nextTick(); messagesEnd.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) })
function sparkline(metric: Metric) {
  const values = series({ ...overview.value, metric }).values.filter((v): v is number => v !== null)
  const low = Math.min(...values), spread = Math.max(...values) - low || 1
  return values.map((v, i) => `${i * 16},${26 - (v - low) / spread * 22}`).join(' ')
}
watch(allowedMetrics, metrics => { if (!metrics.includes(selectedMetric.value)) selectedMetric.value = metrics[0] ?? 'amount' }, { immediate: true })
watch(permissionEpoch, () => { selectedReport.value = null })
async function viewReport(report: Report) { selectedReport.value = null; selectedReport.value = await openReport(report) }
function openModal(value: 'help' | 'settings' | 'search') { modal.value = value; sidebarOpen.value = false }
function confirmDelete() {
  const target = deleteTarget.value
  if (!target) return
  if (target.kind === 'session') removeSession(target.id)
  else { reports.value = reports.value.filter(r => r.id !== target.id); if (selectedReport.value?.id === target.id) selectedReport.value = null; notify('报告已删除') }
  deleteTarget.value = null
}
function friendlyDate(value: string) { if (!value) return '待查询'; return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) }
function keydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openModal('search') }
  if (event.key === 'Escape') { modal.value = null; deleteTarget.value = null; selectedReport.value = null; sidebarOpen.value = false }
  if (event.key === 'Tab' && (modal.value || deleteTarget.value || selectedReport.value)) {
    const container = document.querySelector<HTMLElement>('[role="dialog"]')
    const elements = Array.from(container?.querySelectorAll<HTMLElement>('button:not([disabled]), input, select, textarea, [tabindex="0"]') ?? [])
    const first = elements[0], last = elements.at(-1)
    if (event.shiftKey && (document.activeElement === first || document.activeElement === container)) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
  }
}
onMounted(() => window.addEventListener('keydown', keydown))
onBeforeUnmount(() => window.removeEventListener('keydown', keydown))
</script>

<template>
  <div class="app-shell">
    <button v-if="sidebarOpen" class="sidebar-scrim" aria-label="关闭导航" @click="sidebarOpen = false"></button>
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <a class="brand" href="#" @click.prevent="navigate('overview')"><span class="brand-symbol"><Sparkles :size="22" :stroke-width="1.6" /></span><span>SalesMind<span class="brand-dot">.</span></span></a>
      <button class="workspace-selector" @click="openModal('settings')"><span class="workspace-icon"><Layers3 :size="15" /></span><span>销售分析空间</span><ChevronDown :size="14" /></button>
      <div class="nav-label">工作空间 <span>WORKSPACE</span></div>
      <nav aria-label="主导航">
        <button v-for="item in nav" :key="item.id" :class="['nav-item', { active: page === item.id }]" :aria-current="page === item.id ? 'page' : undefined" @click="navigate(item.id)">
          <component :is="item.icon" :size="19" :stroke-width="1.7" /><span>{{ item.label }}</span><span v-if="item.id === 'analysis'" class="ai-tag">AI</span><span v-if="item.id === 'reports' && reports.length" class="nav-count">{{ reports.length }}</span>
        </button>
      </nav>
      <div class="sidebar-divider"></div>
      <div class="recent-heading"><span>最近的分析</span><button class="icon-button" aria-label="搜索历史分析" @click="openModal('search')"><Search :size="14" /></button></div>
      <button class="new-analysis" @click="newSession"><Plus :size="16" />开启新的分析</button>
      <div class="recent-sessions">
        <div v-for="session in sessions.slice(0, 5)" :key="session.id" class="session-row" :class="{ selected: page === 'analysis' && currentSession?.id === session.id }">
          <button class="session-link" @click="openSession(session.id)"><MessageSquare :size="14" /><span>{{ session.title }}</span><span v-if="session.messages.some(m => m.status === 'running')" class="live-dot"></span></button>
          <button class="delete-session" :aria-label="`删除会话 ${session.title}`" @click="deleteTarget = { kind: 'session', id: session.id, title: session.title }"><X :size="13" /></button>
        </div>
        <div v-if="historyError" class="history-empty" role="alert"><span>{{ historyError }}</span><button class="text-button" @click="loadSessions">重试加载</button></div>
        <div v-if="!sessions.length && !historyError" class="history-empty"><span class="history-line"></span>你的下一次洞察，从这里开始。<span>分析记录将自动保存在服务端</span></div>
      </div>
      <div class="sidebar-bottom">
        <div class="sidebar-note"><Leaf :size="20" :stroke-width="1.5" /><h4>让数据，开口说话。</h4><p>少一点报表，多一点洞察。<br />和你的销售数据聊一聊。</p><button @click="openModal('help')">了解 SalesMind <ArrowUpRight :size="14" /></button></div>
        <button class="sidebar-settings" @click="openModal('settings')"><Settings2 :size="17" />空间设置</button>
        <button class="profile" @click="openModal('settings')"><span class="avatar">{{ avatarText }}</span><span><strong>{{ user?.display_name }}</strong><small>{{ access?.scope_label ?? '按账号授权' }} · {{ user?.username }}</small></span><MoreHorizontal :size="18" /></button>
      </div>
    </aside>

    <div class="main-shell">
      <header class="topbar">
        <div class="breadcrumb"><button class="icon-button mobile-menu" aria-label="打开导航" @click="sidebarOpen = true"><Menu :size="21" /></button><span>工作空间</span><ChevronRight :size="13" /><strong>{{ nav.find(n => n.id === page)?.label }}</strong></div>
        <div class="topbar-actions"><button class="demo-badge" @click="openModal('settings')"><span></span>{{ livePage ? (salesLoading ? "数据库查询中" : salesError ? "连接失败" : "MySQL 实时查询") : "规则解析 · MySQL" }}</button><span class="topbar-separator"></span><button class="icon-button" aria-label="搜索分析记录" title="搜索 · Ctrl K" @click="openModal('search')"><Search :size="18" /></button><button class="icon-button" aria-label="使用帮助" @click="openModal('help')"><CircleHelp :size="18" /></button><button class="avatar avatar-small" aria-label="查看当前账号信息" @click="openModal('settings')">{{ avatarText }}</button></div>
      </header>

      <main :class="['main-content', { 'chat-main': page === 'analysis' }]">
        <template v-if="page === 'overview'">
          <div class="page-heading"><div><div class="eyebrow">A LITTLE CLARITY. A LOT OF POSSIBILITY.</div><h1>看见数据，发现增长<span class="heading-dot">。</span></h1><p>每一个好决策，都从一个好问题开始。</p></div><div class="today-label"><CalendarDays :size="16" /><span>{{ date }}</span></div></div>

          <section class="assistant-card" aria-labelledby="assistant-title">
            <div class="hero-decoration" aria-hidden="true"><svg viewBox="0 0 250 160"><path d="M20 125 80 92 122 104 181 39 226 57" fill="none" stroke="#b5c9b2" stroke-width="1.5"/><path d="M20 143 80 114 122 133 181 84 226 95" fill="none" stroke="#d0dbcb" stroke-width="1" stroke-dasharray="4 5"/><g fill="#f3f6ef" stroke="#a8bfa5"><circle cx="80" cy="92" r="5"/><circle cx="122" cy="104" r="5"/><circle cx="181" cy="39" r="7"/></g><rect x="152" y="8" width="71" height="23" rx="6" fill="#e4ecdf"/><text x="164" y="23" fill="#6c8565" font-size="10" font-family="sans-serif">INSIGHTS ↗</text></svg></div>
            <div class="assistant-heading"><span class="assistant-icon"><Sparkles :size="21" :stroke-width="1.6" /></span><div><h2 id="assistant-title">今天，想了解哪些销售数据？</h2><p>把问题交给 SalesMind，让数字变成清晰的答案。</p></div><span class="small-tag">你的分析搭档</span></div>
            <form class="hero-input" @submit.prevent="send(prompt, true)"><Sparkles :size="18" /><input v-model="prompt" aria-label="输入销售分析问题" placeholder="例如：分析最近六个有完整数据的月份的销售趋势，和去年同期相比怎么样？" maxlength="500" /><button class="send-button" type="submit" :disabled="!prompt.trim() || !!active || submitting || historyLoading" aria-label="开始分析"><ArrowUp :size="20" /></button></form>
            <div class="suggestions"><span>试着问问</span><button v-for="question in questions" :key="question.label" @click="send(question.text, true)"><component :is="question.icon" :size="13" />{{ question.label }}<ArrowUpRight :size="12" /></button></div>
          </section>

          <div class="section-toolbar"><div class="section-title"><h2>经营概览</h2><span class="quiet-label">{{ periodLabel(scope.months) }} · 完整月份</span></div><div class="filter-group"><label class="select-wrap"><Layers3 :size="14" /><select v-model="region" aria-label="选择区域"><option :value="0">{{ access?.scope_label ?? "全部可见数据" }}</option><option v-for="item in displaySource.regions" :key="item.id" :value="item.id">{{ item.name }}</option></select><ChevronDown :size="12" /></label><label class="select-wrap"><CalendarDays :size="14" /><select v-model="period" aria-label="选择时间范围"><option v-for="item in periodOptions" :key="item.value" :value="item.value">{{ item.short }}</option></select><ChevronDown :size="12" /></label><button class="icon-button bordered" aria-label="导出经营数据" @click="exportTable"><ArrowDownToLine :size="16" /></button></div></div>
          <div class="query-status"><span><Database :size="13" />真实数据库查询 · {{ salesLoading ? "正在读取销售订单…" : salesError ? "查询未完成" : (cacheHit ? '复用查询快照 · ' : '查询于 ') + friendlyDate(queryTime) }}</span><button class="text-button" :disabled="salesLoading" @click="refreshSales()">刷新数据 <Clock3 :size="12" /></button></div>
          <div v-if="salesLoading || salesError" class="query-state panel" role="status"><LoaderCircle v-if="salesLoading" :size="23" class="spin" /><CircleHelp v-else :size="23" /><h3>{{ salesLoading ? "正在查询 MySQL 销售数据" : "销售数据暂时无法读取" }}</h3><p>{{ salesLoading ? "正在获取已完成订单的月度汇总和同比基期。" : salesError }}</p><button v-if="salesError" class="outline-button" @click="refreshSales()">重试查询</button></div>
          <template v-else-if="salesData">
          <section class="kpi-grid" aria-label="经营指标">
            <button v-for="card in kpis" :key="card.metric" class="kpi-card" :class="{ chosen: selectedMetric === card.metric }" @click="selectedMetric = card.metric">
              <div class="kpi-top"><span>{{ card.label }}</span><component :is="card.icon" :size="16" :stroke-width="1.6" /></div>
              <div class="kpi-value"><span v-if="card.metric !== 'count'" class="currency">¥</span>{{ card.metric === 'count' ? card.value === null ? '—' : card.value.toLocaleString() : money(card.value, card.metric !== 'average') }}<small v-if="card.metric === 'count'">笔</small></div>
              <div class="kpi-bottom"><div v-if="canExtra('growth_rate')"><span :class="['growth', { negative: (growth(card.value, card.base) ?? 0) < 0 }]"><ArrowUpRight v-if="(growth(card.value, card.base) ?? 0) >= 0" :size="12" /><ArrowDown v-else :size="11" />{{ percent(growth(card.value, card.base)) }}</span><span class="kpi-comparison">同比</span></div><svg class="sparkline" viewBox="0 0 82 30" aria-hidden="true"><polyline :points="sparkline(card.metric)" fill="none" :stroke="(growth(card.value, card.base) ?? 0) < 0 ? '#c3977f' : '#71977d'" stroke-width="1.8" stroke-linejoin="round" /></svg></div>
            </button>
          </section>

          <div class="charts-row">
            <section v-if="allowedMetrics.length" class="panel trend-panel"><div class="panel-header"><div><h3>{{ metricNames[selectedMetric] }}趋势 <span v-if="canExtra('growth_rate')" class="subtle-tag">同比对比</span></h3><p>把每个月的表现，放在一起看。</p></div><div class="chart-toggle" aria-label="图表类型"><button :class="{ active: chartType === 'line' }" aria-label="切换折线图" @click="chartType = 'line'"><LineChart :size="15" /></button><button :class="{ active: chartType === 'bar' }" aria-label="切换柱状图" @click="chartType = 'bar'"><BarChart3 :size="15" /></button></div></div><div class="chart-legend"><span><i class="current"></i>当前期间</span><span v-if="canExtra('growth_rate')"><i class="previous"></i>去年同期</span><span class="chart-unit">{{ selectedMetric === 'count' ? '单位：笔' : '单位：万元' }}</span></div><SalesChart :analysis="dashboardChart" :height="235" /><div class="chart-footer"><span><ShieldCheck :size="13" />仅统计已完成订单</span><button @click="send(`分析${periodOptions.find(p => p.value === period)?.label ?? '最近六个有完整数据的月份'}${scopeName(scope, salesData ?? undefined)}的${metricNames[selectedMetric]}趋势和同比`, true)">深入分析 <ArrowUpRight :size="13" /></button></div></section>

            <section v-if="can('amount')" class="panel insight-panel"><div class="panel-header"><div class="insight-heading"><span class="insight-icon"><Sparkles :size="15" /></span><h3>数据里的小发现</h3></div><span class="small-dot"></span></div><p class="insight-intro">从当前数据出发，找到值得关注的信号。</p><div v-if="canExtra('growth_rate')" class="insight-item"><span class="insight-number">01</span><div><h4>{{ overallGrowth === null ? '基期为空，谨慎比较' : overallGrowth >= 0 ? '销售规模有所增长' : '销售规模有所回落' }}</h4><p v-if="overallGrowth !== null">销售额较去年同期{{ overallGrowth >= 0 ? '增长' : '下降' }} <strong>{{ Math.abs(overallGrowth).toFixed(1) }}%</strong>。<span v-if="can('count') && totals.count !== null">当前共完成 {{ totals.count.toLocaleString() }} 笔订单。</span></p><p v-else>当前范围缺少可比基期，无法计算同比增长率。</p></div></div><div class="insight-item"><span class="insight-number">02</span><div><h4>{{ bestMonth.name }}表现更突出</h4><p>期间内销售额最高的月份为 {{ bestMonth.name }}，贡献 <strong>¥ {{ money(bestMonth.amount, true) }}</strong>。</p></div></div><div v-if="canExtra('growth_rate')" class="insight-item"><span class="insight-number">03</span><div><h4>{{ bestRegion ? `${bestRegion.name}区域值得关注` : '当前范围暂无可比区域' }}</h4><p v-if="bestRegion">{{ bestRegion.name }}销售额同比 <strong>{{ percent(bestRegion.growth) }}</strong>。变化原因需要进一步核实。</p><p v-else>当前范围没有可比区域，可调整查询范围。</p></div></div><button class="insight-action" @click="send('最近六个有完整数据的月份各区域销售额对比', true)">和数据再聊一聊 <ArrowRight :size="15" /></button><div class="insight-disclaimer">基于 MySQL 查询结果的确定性统计</div></section>
          </div>

          <div v-if="can('amount')" class="bottom-row"><section class="panel ranking-panel"><div class="panel-header"><div class="ranking-tabs"><button :class="{ active: rankingTab === 'regions' }" @click="rankingTab = 'regions'">区域表现</button><button :class="{ active: rankingTab === 'products' }" @click="rankingTab = 'products'">商品排名</button></div><button class="text-button" @click="navigate('data')">查看数据 <ArrowUpRight :size="13" /></button></div><div class="table-scroll"><table class="ranking-table"><thead><tr><th>{{ rankingTab === 'regions' ? '销售区域' : '商品名称' }}</th><th>销售额</th><th v-if="canExtra('product_share')">占比</th><th v-if="canExtra('growth_rate')">同比变化</th></tr></thead><tbody><tr v-for="(row, index) in leaderboard" :key="row.id"><td><span :class="['rank-number', { first: index === 0 }]">{{ String(index + 1).padStart(2, '0') }}</span><strong>{{ row.name }}</strong></td><td class="number-cell">¥ {{ money(row.amount, true) }}</td><td v-if="canExtra('product_share') && row.share !== null"><div class="share-bar"><span><i :style="{ width: `${row.share}%` }"></i></span><small>{{ row.share.toFixed(1) }}%</small></div></td><td v-if="canExtra('growth_rate')" :class="['growth', { negative: (row.growth ?? 0) < 0 }]">{{ percent(row.growth) }}</td></tr></tbody></table></div></section><section class="panel products-panel"><div class="panel-header"><div><h3>畅销商品</h3><p>被选择最多的，值得多看一眼。</p></div><ShoppingBag :size="17" class="muted" /></div><div v-for="(product, index) in products" :key="product.id" class="product-row"><span :class="`product-image product-color-${index}`"><component :is="[Layers3, Grid2X2, ShoppingBag, BookOpen][index]" :size="21" :stroke-width="1.4" /></span><div><strong>{{ product.name }}</strong><small>{{ product.category }}<span v-if="can('count')"> · {{ product.count }} 笔订单</span></small></div><span class="product-amount">¥ {{ money(product.amount, true) }}</span></div><div v-if="!products.length" class="small-empty">当前区域暂无已完成订单。</div></section></div>
          </template>
          <footer v-if="salesData && !salesLoading && !salesError" class="page-footer"><span><span class="live-dot"></span>MySQL 数据截至 {{ displaySource.completeThrough }} · 版本 {{ displaySource.version }}</span><span>每个答案，都有数据可循。 <Leaf :size="12" /></span></footer>
        </template>

        <template v-else-if="page === 'analysis'">
          <div v-if="historyError" class="query-status" role="alert"><span>{{ historyError }}</span><button class="text-button" @click="currentSession ? openSession(currentSession.id) : loadSessions()">重新加载会话</button></div>
          <div v-if="historyLoading" class="query-status" role="status"><span><LoaderCircle :size="14" class="spin" />正在读取服务端会话…</span></div>
          <div class="analysis-titlebar"><div><div class="eyebrow">YOUR DATA. YOUR QUESTIONS.</div><h1>{{ currentSession?.messages.length ? currentSession.title : '好问题，是洞察的开始。' }}</h1><p>自然语言分析 · 可追溯结果 · 条件澄清</p></div><button class="outline-button" :disabled="submitting || historyLoading" @click="newSession"><Plus :size="15" />新分析</button></div>
          <div v-if="!currentSession?.messages.length" class="chat-welcome"><span class="large-brand"><Sparkles :size="35" :stroke-width="1.4" /></span><h2>和你的销售数据，聊一聊。</h2><p>不必写 SQL，也不用翻找报表。<br />从一个问题开始，让数据回答你。</p><div class="welcome-questions"><button v-for="question in questions" :key="question.label" @click="send(question.text)"><component :is="question.icon" :size="22" :stroke-width="1.4" /><strong>{{ question.label }}</strong><span>{{ question.text }}</span><ArrowUpRight :size="16" /></button></div><div class="demo-note"><Lightbulb :size="14" />DeepSeek 理解问题，后端校验条件并查询 MySQL。</div></div>
          <div v-else class="conversation">
            <article v-for="message in currentSession.messages" :key="message.id" class="analysis-message">
              <div class="user-message"><span class="user-mark">你</span><p>{{ message.question }}</p></div>
              <p v-for="(reply, index) in message.clarificationReplies" :key="index" class="clarification-reply">补充：{{ reply }}</p><div class="assistant-response"><span class="response-avatar"><Sparkles :size="18" /></span><div class="response-body"><div class="response-header"><strong>SalesMind</strong><span>销售分析助手</span><span :class="['run-status', message.status]"><LoaderCircle v-if="message.status === 'running'" :size="12" class="spin" /><CheckCheck v-else-if="message.status === 'completed'" :size="13" />{{ statusLabels[message.status] }}</span></div>
                <template v-if="message.status === 'running' || message.status === 'interrupted'"><div class="stage-timeline"><div v-for="(stage, i) in (message.plan?.map(p => p.label) ?? stages)" :key="stage" :class="['stage', { done: i < message.stage, current: i === message.stage }]"><span><Check v-if="i < message.stage" :size="12" /><LoaderCircle v-else-if="i === message.stage && message.status === 'running'" :size="12" class="spin" /><span v-else>{{ i + 1 }}</span></span><small>{{ stage }}</small></div></div><p class="stage-description">{{ message.note ?? `${(message.plan?.map(p => p.label) ?? stages)[message.stage] ?? '保存结果'}中，请稍候…` }}</p><div class="run-controls"><template v-if="message.status === 'running'"><button class="outline-button" @click="stop(message, true)"><Pause :size="13" />暂停</button><button class="text-button" @click="stop(message)"><Square :size="12" />停止分析</button></template><template v-else><button class="primary-button" @click="resume(message)"><Play :size="13" />继续分析</button><button class="text-button" @click="stop(message)">取消任务</button></template></div></template>
                <template v-else-if="message.status === 'awaiting_input'"><div class="clarification"><CalendarDays :size="20" /><div><h4>补充一下分析条件</h4><p>{{ message.note }}</p><div v-if="message.clarificationFields?.includes('months')" class="clarify-options"><button v-for="item in periodOptions" :key="item.value" @click="clarify(message, item.value)">{{ item.label }}<ArrowRight :size="13" /></button></div><button class="text-button" @click="stop(message)">取消本次分析</button></div></div></template>
                <template v-else-if="message.status === 'completed' && message.analysis"><div class="completed-stages"><span v-for="step in (message.plan?.map(p => p.label) ?? stages)" :key="step"><Check :size="13" />{{ step }}</span></div><p v-if="message.inheritedFields?.length" class="answer-detail">已沿用上一轮条件：{{ message.inheritedFields.map(f => ({ metric: '指标', months: '时间', region: '区域', view: '分析方式', chart: '图形', comparison: '比较方式', product_ids: '商品', anomaly_window: '检测窗口', anomaly_threshold_percent: '异常阈值', goal: '分析目标', sort_by: '排序', sort_order: '排序方向', top_n: '排名数量' } as Record<string, string>)[f]).join('、') }}</p><div class="intent-summary">已确认：{{ metricNames[message.analysis.metric] }} · {{ periodLabel(message.analysis.scope.months) }} · {{ scopeName(message.analysis.scope, message.analysis.dataset ?? source) }} · {{ viewNames[message.analysis.view] }}<span v-if="analysisConditions(message.analysis)"> · {{ analysisConditions(message.analysis) }}</span></div><h3 class="answer-headline">{{ answer(message.analysis).headline }}</h3><p class="answer-detail">{{ answer(message.analysis).detail }}</p><div class="answer-scope"><span><CalendarDays :size="12" />{{ periodLabel(message.analysis.scope.months) }}</span><span><Layers3 :size="12" />{{ scopeName(message.analysis.scope, message.analysis.dataset ?? source) }}</span><span>仅已完成订单</span></div><div class="answer-chart"><div class="panel-header"><h4>{{ metricNames[message.analysis.metric] }}{{ viewNames[message.analysis.view] }}</h4><div v-if="message.analysis.goal !== 'anomalies'" class="chart-toggle"><button :class="{ active: message.analysis.chart === 'line' }" aria-label="结果切换折线图" @click="message.analysis.chart = 'line'"><LineChart :size="14" /></button><button :class="{ active: message.analysis.chart === 'bar' }" aria-label="结果切换柱状图" @click="message.analysis.chart = 'bar'"><BarChart3 :size="14" /></button></div></div><SalesChart :analysis="message.analysis" :height="250" /></div><AnalysisResultTable :analysis="message.analysis" /><p class="answer-caveat"><ShieldCheck :size="13" />{{ answer(message.analysis).caveat }}</p><div v-if="!pending && message.id === currentSession?.messages.at(-1)?.id" class="followup-options"><button class="outline-button" @click="send('华南呢')">华南呢</button><button class="outline-button" @click="send('同比呢')">同比呢</button><button class="outline-button" @click="send('环比呢')">环比呢</button><button class="outline-button" @click="send('检测销售额异常')">检查异常</button><button class="outline-button" @click="send('对比笔记本电脑和显示器')">商品对比</button><button v-if="message.analysis.goal !== 'anomalies'" class="outline-button" @click="send('换成柱状图')">换成柱状图</button></div><div class="answer-actions"><button @click="saveReport(message.analysis)"><Plus :size="14" />保存报告</button><button @click="exportAnalysis(message.analysis)"><ArrowDownToLine :size="14" />导出数据</button><span>{{ message.analysis.dataset ? "MySQL 查询快照 v" + message.analysis.dataset.version : "历史演示数据 v" + source.version }}</span></div></template>
                <div v-else class="message-notice"><CircleHelp :size="18" /><p>{{ message.note }}</p></div>
              </div></div>
            </article><div ref="messagesEnd"></div>
          </div>
          <div class="chat-composer"><form class="composer-input" @submit.prevent="send()"><textarea v-model="prompt" aria-label="输入分析问题或补充条件" :placeholder="pending?.status === 'awaiting_input' ? '补充需要的指标、月份或区域…' : '描述指标、月份、区域和分析方式…'" rows="2" maxlength="500" :disabled="!!active || submitting || historyLoading || pending?.status === 'interrupted'" @keydown.enter.exact.prevent="send()"></textarea><div class="composer-toolbar"><span><Sparkles :size="13" />SalesMind <span class="composer-divider">/</span>MySQL 查询<span class="character-count">{{ prompt.length }}/500</span></span><button class="send-button" type="submit" :disabled="!prompt.trim() || !!active || submitting || historyLoading || pending?.status === 'interrupted'" aria-label="发送问题"><Send :size="17" /></button></div></form><p class="composer-footnote">真实 MySQL 查询 · DeepSeek 提取分析条件 · Shift + Enter 换行</p></div>
        </template>

        <template v-else-if="page === 'reports'">
          <div class="page-heading"><div><div class="eyebrow">GOOD INSIGHTS DESERVE A PLACE.</div><h1>把洞察，留在这里。</h1><p>已保存的分析报告，随时回看、继续探索。</p></div><button class="primary-button" @click="newSession"><Plus :size="15" />创建分析</button></div><div class="section-toolbar"><div class="section-title"><h2>我的报告</h2><span class="subtle-tag">{{ reports.length }} 份</span></div><span class="quiet-label">报告索引保存在当前浏览器</span></div><div v-if="!reports.length" class="empty-state panel"><span class="empty-illustration"><FileBarChart2 :size="36" :stroke-width="1.2" /></span><h3>值得记录的发现，都有一个位置。</h3><p>完成一次智能分析后，点击“保存报告”，<br />你的图表和结论就会出现在这里。</p><button class="primary-button" @click="send('最近六个有完整数据的月份的销售趋势和同比', true)">开始第一份分析 <ArrowRight :size="15" /></button></div><div v-else class="report-grid"><article v-for="report in reports" :key="report.id" class="report-card panel"><button class="report-preview" :aria-label="`查看报告 ${report.title}`" @click="viewReport(report)"><span class="report-type"><FileText :size="13" />销售分析报告</span><h3>{{ report.title }}</h3><p>{{ periodLabel(report.spec.scope.months) }} · {{ metricNames[report.spec.metric] }}{{ viewNames[report.spec.view] }}</p><div class="report-summary">打开时读取当前账号可访问的服务端分析结果。</div></button><div class="report-card-footer"><span><Clock3 :size="12" />{{ friendlyDate(report.created) }}</span><button class="icon-button" aria-label="导出报告" @click="exportReport(report)"><ArrowDownToLine :size="15" /></button><button class="icon-button" aria-label="删除报告" @click="deleteTarget = { kind: 'report', id: report.id, title: report.title }"><Trash2 :size="14" /></button></div></article></div>
        </template>

        <template v-else-if="page === 'data'">
          <div class="query-status"><span><Database :size="13" />MySQL 实时查询 · {{ salesLoading ? "加载中" : salesError ? "连接失败" : friendlyDate(queryTime) }}</span><button class="text-button" :disabled="salesLoading" @click="refreshSales()">刷新数据</button></div>
          <div v-if="salesLoading || salesError" class="query-state panel" role="status"><LoaderCircle v-if="salesLoading" :size="23" class="spin" /><CircleHelp v-else :size="23" /><h3>{{ salesLoading ? "正在查询销售数据" : "真实查询失败" }}</h3><p>{{ salesError || "正在读取数据库中的已完成订单。" }}</p><button v-if="salesError" class="outline-button" @click="refreshSales()">重试查询</button><button v-if="region" class="text-button" @click="region = 0">返回全部可见数据</button></div>
          <template v-else-if="salesData">
          <div class="page-heading"><div><div class="eyebrow">THE NUMBERS BEHIND THE ANSWERS.</div><h1>每个答案，都有数据可循。</h1><p>查看销售数据的覆盖范围、统计口径和区域月度汇总。</p></div><button class="outline-button" @click="exportTable"><ArrowDownToLine :size="15" />导出数据</button></div><div class="source-card"><span class="source-icon"><Database :size="26" :stroke-width="1.4" /></span><div><h3>SalesMind 业务数据库 <span class="subtle-tag">MySQL 实时查询</span></h3><p><span v-if="can('count') && displaySource.orderCount !== null">{{ displaySource.orderCount.toLocaleString() }} 笔可见订单 · </span>{{ displaySource.regions.length }} 个区域 · {{ displaySource.products.length }} 种商品</p></div><div class="source-watermark"><small>数据覆盖</small><strong>{{ displaySource.availableFrom }} — {{ displaySource.completeThrough }}</strong><span>完整月截至 2026 年 8 月</span></div></div><div class="data-scope-note"><ShieldCheck :size="16" />统一口径：仅统计 COMPLETED（已完成）订单，排除退款和取消；金额按整数分汇总。9 月部分数据不计入完整月分析。</div><div class="section-toolbar"><div class="section-title"><h2>区域月度汇总</h2><span class="quiet-label">{{ dataRows.length }} 条记录</span></div><div class="filter-group"><label class="select-wrap"><select v-model="region" aria-label="数据区域筛选"><option :value="0">{{ access?.scope_label ?? "全部可见数据" }}</option><option v-for="item in displaySource.regions" :key="item.id" :value="item.id">{{ item.name }}</option></select><ChevronDown :size="12" /></label><label class="select-wrap"><CalendarDays :size="14" /><select v-model="period" aria-label="数据时间筛选"><option v-for="item in periodOptions" :key="item.value" :value="item.value">{{ item.short }}</option></select><ChevronDown :size="12" /></label></div></div><section class="panel data-table-panel"><div class="data-table-tools"><label class="table-search"><Search :size="15" /><input v-model="tableSearch" aria-label="搜索月份或区域" placeholder="搜索月份或区域…" /></label><label class="sort-control">排序<select v-model="tableSort" aria-label="数据排序"><option value="month">月份从近到远</option><option v-if="can('amount')" value="amount">销售额从高到低</option></select><ChevronDown :size="12" /></label></div><div class="table-scroll"><table class="data-table"><thead><tr><th>月份</th><th>区域</th><th v-if="can('amount')">销售额（元）</th><th v-if="can('count')">订单数</th><th v-if="can('profit')">毛利（元）</th><th v-if="canExtra('gross_margin') && can('amount') && can('profit')">毛利率</th></tr></thead><tbody><tr v-for="row in visibleDataRows" :key="row.id"><td>{{ row.month }}</td><td><span class="region-label">{{ row.name }}</span></td><td v-if="can('amount')" class="number-cell">{{ money(row.amount) }}</td><td v-if="can('count')">{{ row.count ?? '—' }}</td><td v-if="can('profit')" class="number-cell">{{ money(row.profit) }}</td><td v-if="canExtra('gross_margin') && can('amount') && can('profit')">{{ row.amount && row.profit !== null ? (row.profit / row.amount * 100).toFixed(2) + '%' : '—' }}</td></tr><tr v-if="!dataRows.length"><td colspan="6" class="small-empty">没有匹配的数据，请调整搜索条件。</td></tr></tbody></table></div><div class="pagination"><span>{{ dataRows.length ? (tablePage - 1) * 10 + 1 : 0 }}–{{ Math.min(tablePage * 10, dataRows.length) }} / {{ dataRows.length }} 条</span><div><button class="icon-button bordered" aria-label="上一页" :disabled="tablePage === 1" @click="tablePage--"><ChevronRight :size="15" class="rotate" /></button><span>{{ tablePage }} / {{ pageCount }}</span><button class="icon-button bordered" aria-label="下一页" :disabled="tablePage >= pageCount" @click="tablePage++"><ChevronRight :size="15" /></button></div></div></section>
          </template>
        </template>
      </main>
    </div>

    <Transition name="toast"><div v-if="toast" class="toast-message" role="status"><span><Check :size="14" /></span>{{ toast }}</div></Transition>
    <div v-if="modal" class="modal-overlay" @click.self="modal = null"><section ref="dialogElement" class="modal" :class="{ 'search-modal': modal === 'search' }" role="dialog" aria-modal="true" :aria-label="modal === 'search' ? '搜索分析' : modal === 'help' ? '使用帮助' : '空间信息'" tabindex="-1"><button class="modal-close icon-button" aria-label="关闭弹窗" @click="modal = null"><X :size="18" /></button><template v-if="modal === 'search'"><h2>找回一个好问题。</h2><label class="search-modal-input"><Search :size="18" /><input ref="searchInput" v-model="searchText" aria-label="搜索会话" placeholder="搜索你的分析记录…" /></label><div class="search-results"><button v-for="session in filteredSearch" :key="session.id" @click="openSession(session.id); modal = null"><MessageSquare :size="16" /><span><strong>{{ session.title }}</strong><small>{{ session.messages.length }} 轮分析 · {{ friendlyDate(session.updated) }}</small></span><ArrowUpRight :size="15" /></button><p v-if="!filteredSearch.length" class="small-empty">{{ sessions.length ? '没有匹配的分析，换个关键词试试。' : '还没有分析记录，从一个新问题开始吧。' }}</p></div><div class="modal-footer">Ctrl / ⌘ + K 快速打开 · Esc 关闭</div></template><template v-else-if="modal === 'help'"><span class="modal-brand"><Sparkles :size="25" /></span><div class="eyebrow">MEET YOUR ANALYSIS PARTNER.</div><h2>问得简单，看得明白。</h2><p>SalesMind 帮你通过自然语言，探索销售数据。</p><div class="help-steps"><div><span>01</span><div><strong>问一个具体的问题</strong><p>例如“分析最近六个有完整数据的月份华东的销售额和同比”。</p></div></div><div><span>02</span><div><strong>看懂数字和图表</strong><p>查看分析进度、确定性结论和 Plotly 交互图表。</p></div></div><div><span>03</span><div><strong>补充条件，或者留下洞察</strong><p>补充缺少的分析条件，或保存报告、导出数据。</p></div></div></div><div class="help-notice">DeepSeek 提取指标、时间、区域和分析方式；后端校验后查询 MySQL，通过 SSE 展示进度。</div><button class="primary-button" @click="modal = null; newSession()">试着问一个问题 <ArrowRight :size="15" /></button></template><template v-else><span class="modal-brand"><Layers3 :size="25" /></span><h2>销售分析空间</h2><p>销售数据和指标按当前账号授权展示。</p><dl class="settings-list"><div><dt>当前账号</dt><dd>{{ user?.display_name }} · {{ user?.username }}</dd></div><div><dt>运行模式</dt><dd><span class="demo-badge"><span></span>DeepSeek 意图解析 + MySQL 查询</span></dd></div><div><dt>数据来源</dt><dd>MySQL sales_data 业务库</dd></div><div><dt>数据版本</dt><dd>{{ source.version }}</dd></div><div><dt>完整月范围</dt><dd>2024.02 — 2026.08</dd></div><div><dt>历史与报告</dt><dd>会话与结果保存在服务端；浏览器仅保存报告索引</dd></div></dl><div class="help-notice">当前可见范围：{{ access?.scope_label ?? '按账号授权' }}。指标由后端按授权校验。退出仅注销当前设备，其他设备不受影响。基础意图解析已接入；请提供完整问题，缺少条件时会提示补充。</div><button class="primary-button logout-button" :disabled="logoutBusy" @click="signOut">{{ logoutBusy ? "正在退出…" : "退出当前设备" }}</button><p v-if="logoutError" class="logout-error" role="alert">{{ logoutError }}</p></template></section></div>
    <div v-if="selectedReport?.analysis" class="modal-overlay" @click.self="selectedReport = null"><section class="modal report-detail-modal" role="dialog" aria-modal="true" aria-label="报告详情"><button class="modal-close icon-button" aria-label="关闭报告" @click="selectedReport = null"><X :size="18" /></button><span class="report-type"><FileText :size="13" />已保存的分析报告</span><h2>{{ selectedReport.title }}</h2><p>{{ answer(selectedReport.analysis).title }}</p><h3 class="answer-headline">{{ answer(selectedReport.analysis).headline }}</h3><p class="answer-detail">{{ answer(selectedReport.analysis).detail }}</p><SalesChart :analysis="selectedReport.analysis" :height="280" /><AnalysisResultTable :analysis="selectedReport.analysis" /><p class="answer-caveat">{{ answer(selectedReport.analysis).caveat }}</p><div class="report-detail-actions"><button class="primary-button" @click="exportReport(selectedReport)"><ArrowDownToLine :size="15" />导出报告</button><button class="outline-button" @click="exportAnalysis(selectedReport.analysis)">导出数据</button></div></section></div>
    <div v-if="deleteTarget" class="modal-overlay" @click.self="deleteTarget = null"><section class="modal delete-modal" role="dialog" aria-modal="true" aria-label="确认删除"><span class="delete-icon"><Trash2 :size="23" /></span><h2>删除这{{ deleteTarget.kind === 'session' ? '段分析' : '份报告' }}？</h2><p>“{{ deleteTarget.title }}”将被删除。</p><div class="delete-actions"><button class="outline-button" @click="deleteTarget = null">保留</button><button class="danger-button" @click="confirmDelete">确认删除</button></div></section></div>
  </div>
</template>

<style scoped>
.intent-summary { padding: 12px 14px; margin: 15px 0; border: 1px solid #e1e7d8; border-radius: 8px; font-size: 12px; line-height: 1.8; color: #65765c; background: #f7f9f3; }
.stage-timeline { flex-wrap: wrap; gap: 12px; }
.completed-stages { flex-wrap: wrap; gap: 12px; }
.completed-stages > span { display: inline-flex; align-items: center; gap: 4px; }
.analysis-table-wrap { overflow-x: auto; margin: 16px 0; }
.analysis-table { width: 100%; font-size: 12px; border-collapse: collapse; }
.analysis-table th, .analysis-table td { padding: 10px; text-align: left; border-bottom: 1px solid #e1e7d8; white-space: nowrap; }
.followup-options { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; }
.clarification-reply { margin: 12px 0 12px 52px; font-size: 12px; color: #65765c; }
</style>
