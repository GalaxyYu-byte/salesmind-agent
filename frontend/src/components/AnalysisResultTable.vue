<script setup lang="ts">
import { baselineLabel, comparisonKind, comparisonName, comparisonPercent } from '../lib/analytics'
import type { Analysis } from '../lib/analytics'

defineProps<{ analysis: Analysis }>()
const value = (input: string | null | undefined) => input == null ? '—' : Number(input).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const rate = (input: string | null | undefined) => input == null ? '不可比' : Number(input).toFixed(2) + '%'
</script>

<template>
  <div v-if="analysis.analysisResult" class="analysis-table-wrap">
    <table class="analysis-table">
      <thead><tr>
        <th>{{ analysis.view === 'trend' ? '月份' : '名称' }}</th><th>本期{{ analysis.metric === 'count' ? '（笔）' : '（元）' }}</th>
        <template v-if="comparisonKind(analysis) !== 'none' && analysis.goal !== 'anomalies'"><th>{{ baselineLabel(analysis) }}</th><th>{{ comparisonName(analysis) }}增长率</th></template>
        <template v-if="analysis.goal === 'anomalies'"><th>期望值</th><th>偏离率</th><th>检测结果</th></template>
        <th>说明</th>
      </tr></thead>
      <tbody><tr v-for="row in analysis.analysisResult.rows" :key="row.id">
        <td>{{ row.name }}<span v-if="analysis.goal === 'largest_decline' && analysis.analysisResult.selected_ids.includes(row.id)"> · 下降最多</span></td><td>{{ value(row.current) }}</td>
        <template v-if="comparisonKind(analysis) !== 'none' && analysis.goal !== 'anomalies'"><td>{{ value(row.baseline) }}</td><td>{{ rate(comparisonPercent(row, analysis)) }}</td></template>
        <template v-if="analysis.goal === 'anomalies'"><td>{{ value(row.expected) }}</td><td>{{ rate(row.deviation_percent) }}</td><td :class="{ 'anomaly-label': row.anomaly }">{{ row.anomaly ? '异常' : row.deviation_percent == null ? '不可检测' : '正常' }}</td></template>
        <td>{{ row.reason || (analysis.goal === 'anomalies' ? '按历史窗口与阈值检测' : comparisonKind(analysis) === 'none' ? '本期统计' : '可比') }}</td>
      </tr></tbody>
    </table>
  </div>
</template>

<style scoped>
.analysis-table-wrap { overflow-x: auto; margin: 16px 0; }
.analysis-table { width: 100%; font-size: 12px; border-collapse: collapse; }
.analysis-table th, .analysis-table td { padding: 10px; text-align: left; border-bottom: 1px solid #e1e7d8; white-space: nowrap; }
.anomaly-label { color: #b45938; font-weight: 600; }
</style>
