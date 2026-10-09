<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import type { Data, Layout } from 'plotly.js'
import { baselineLabel, figureTraces, series, metricNames, viewNames } from '../lib/analytics'
import type { Analysis } from '../lib/analytics'
import AnalysisResultTable from './AnalysisResultTable.vue'

const props = defineProps<{ analysis: Analysis; height?: number }>()
const host = ref<HTMLDivElement>()
const error = ref(false)
let library: typeof import('plotly.js') | undefined
let observer: ResizeObserver | undefined
let generation = 0
let disposed = false
async function draw() {
  const current = ++generation
  if (!host.value || disposed) return
  try {
    library ??= (await import('plotly.js-basic-dist-min')).default
    if (current !== generation || disposed || !host.value) return
    const figure = props.analysis.analysisResult?.figure
    if (figure) {
      const traces = figureTraces(props.analysis) as Data[]
      const layout = { autosize: true, height: props.height ?? 255, margin: { l: 65, r: 20, t: 20, b: 55 }, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', font: { family: 'Microsoft YaHei, sans-serif', size: 11, color: '#89928d' }, ...figure.layout } as Partial<Layout>
      await library.react(host.value, traces, layout, { responsive: true, displayModeBar: false, displaylogo: false })
      error.value = false
      return
    }
    const data = series(props.analysis)
    const money = props.analysis.metric !== 'count'
    const scale = money ? 1000000 : 1
    const traces: Data[] = [{
      x: data.labels, y: data.values.map(v => v === null ? null : v / scale), type: props.analysis.chart === 'bar' ? 'bar' : 'scatter',
      mode: 'lines+markers', name: '当前期间',
      line: { color: '#2f6851', width: 3, shape: 'spline', smoothing: 0.55 },
      marker: { color: '#2f6851', size: 6, line: { color: '#fff', width: 2 } },
      fill: props.analysis.chart === 'line' ? 'tozeroy' : undefined, fillcolor: 'rgba(75, 128, 96, 0.055)',
      hovertemplate: `%{x}<br>${metricNames[props.analysis.metric]} %{y:,.2f}${money ? ' 万元' : ' 笔'}<extra>当前期间</extra>`,
    }]
    if (data.baseline.length) traces.push({
      x: data.labels, y: data.baseline.map(v => v === null ? null : v / scale), type: props.analysis.chart === 'bar' ? 'bar' : 'scatter',
      mode: 'lines', name: baselineLabel(props.analysis), line: { color: '#b8c3bb', width: 2, dash: 'dot', shape: 'spline' },
      marker: { color: '#d4ddd5' }, hovertemplate: `%{x}<br>%{y:,.2f}${money ? ' 万元' : ' 笔'}<extra>${baselineLabel(props.analysis)}</extra>`,
    })
    const layout: Partial<Layout> = {
      autosize: true, height: props.height ?? 255, margin: { l: 48, r: 15, t: 15, b: props.analysis.view === 'products' ? 65 : 38 },
      font: { family: 'Inter, Microsoft YaHei, sans-serif', size: 11, color: '#89928d' },
      paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', showlegend: false,
      xaxis: { showgrid: false, zeroline: false, fixedrange: true, tickangle: props.analysis.view === 'products' ? -15 : 0, tickfont: { size: 11 }, automargin: true },
      yaxis: { gridcolor: '#edf0ed', griddash: 'dot', zeroline: false, fixedrange: true, rangemode: 'tozero', ticksuffix: money ? ' 万' : '', tickformat: ',.0f', nticks: 5 },
      hovermode: 'x unified', hoverlabel: { bgcolor: '#fff', bordercolor: '#e1e7e2', font: { color: '#34443b', size: 12 } },
      bargap: 0.5, barmode: 'group', dragmode: false,
    }
    await library.react(host.value, traces, layout, { responsive: true, displayModeBar: false, displaylogo: false, scrollZoom: false })
    error.value = false
  } catch { error.value = true }
}
onMounted(() => { void draw(); observer = new ResizeObserver(() => { if (host.value && library && !disposed) void library.Plots.resize(host.value) }); if (host.value) observer.observe(host.value) })
watch(() => [props.analysis, props.height], () => void draw(), { deep: true })
onBeforeUnmount(() => { disposed = true; generation++; observer?.disconnect(); if (host.value && library) library.purge(host.value) })
</script>

<template>
  <div class="chart-container" :style="{ minHeight: `${height ?? 255}px` }">
    <div v-if="error" class="chart-fallback">图表暂时无法加载，数据仍可查看或导出。
      <AnalysisResultTable v-if="analysis.analysisResult" :analysis="analysis" /><table v-else><tbody><tr v-for="(label, i) in series(analysis).labels" :key="label"><td>{{ label }}</td><td>{{ series(analysis).values[i] }}</td></tr></tbody></table>
    </div>
    <div v-show="!error" ref="host" role="img" :aria-label="`${metricNames[analysis.metric]}${viewNames[analysis.view]}图表`"></div>
  </div>
</template>
