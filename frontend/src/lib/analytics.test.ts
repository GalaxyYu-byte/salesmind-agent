import { describe, expect, it } from 'vitest'
import { answer, covered, growth, money, parseQuestion, periodOptions, previousPeriod, previousYear, ranking, series, source, total } from './analytics'
import type { Analysis, Scope } from './analytics'
import { csv } from './storage'

const scope: Scope = { months: [...periodOptions[0]!.months], region: 0 }
const base: Analysis = { scope, metric: 'amount', chart: 'line', view: 'trend', question: '销售趋势' }

describe('fixed demo data and deterministic calculations', () => {
  it('matches the original fixture manifest across all completed orders', () => {
    const result = total({ months: [...new Set(source.cells.map(c => c.month))], region: 0 })
    expect(result.amount).toBe(2560625636)
    expect(result.cost).toBe(1664398031)
    expect(result.profit).toBe(896227605)
    expect(result.count).toBe(9176)
    expect(result.quantity).toBe(27437)
  })
  it('preserves cent precision and excludes cancelled/refunded fixture orders', () => {
    expect(total({ months: ['2026-08'], region: 1 }, 12).amount).toBe(12000)
    expect(total({ months: ['2026-08'], region: 1 }, 12).count).toBe(1)
    expect(total({ months: ['2026-08'], region: 1 }, 13).amount).toBe(6858)
    expect(total({ months: ['2026-08'], region: 1 }, 14).profit).toBe(-2000)
  })
  it('regional partitions sum to the same aggregate', () => {
    expect(ranking(scope, 'regions').reduce((sum, row) => sum + row.amount, 0)).toBe(total(scope).amount)
    expect(ranking(scope, 'products').reduce((sum, row) => sum + row.count, 0)).toBe(total(scope).count)
  })
  it('keeps an empty region empty', () => {
    expect(total({ ...scope, region: 5 }).amount).toBe(0)
    expect(total({ ...scope, region: 5 }).average).toBe(0)
    expect(answer({ ...base, scope: { ...scope, region: 5 } }).headline).toContain('没有已完成订单')
  })
  it('does not divide by a zero baseline', () => {
    expect(growth(50000, 0)).toBeNull()
    expect(growth(12000, 10000)).toBe(20)
  })
  it('does not treat missing year-over-year coverage as zero', () => {
    const analysis: Analysis = { ...base, scope: { months: ['2024-02'], region: 0 } }
    expect(covered(['2023-02'])).toBe(false)
    expect(series(analysis).baseline).toEqual([null])
    expect(answer(analysis).headline).toContain('数据未完整覆盖')
  })
  it('handles periods across year boundaries', () => {
    expect(previousYear(['2026-01', '2026-02'])).toEqual(['2025-01', '2025-02'])
    expect(previousPeriod(['2026-01', '2026-02'])).toEqual(['2025-11', '2025-12'])
  })
  it('formats negative profit and precise money', () => {
    expect(money(-2000)).toBe('-20.00')
    expect(money(6858)).toBe('68.58')
  })
})

describe('bounded demo question parsing', () => {
  it('inherits time, region and metric for chart-only followups without mutating context', () => {
    const previous: Analysis = { ...base, scope: { ...scope, region: 1 }, metric: 'profit' }
    const result = parseQuestion('换成柱状图', scope, previous)
    expect(result.kind).toBe('ready')
    if (result.kind === 'ready') {
      expect(result.analysis.scope).toEqual(previous.scope)
      expect(result.analysis.metric).toBe('profit')
      expect(result.analysis.chart).toBe('bar')
      expect(result.analysis.scope).not.toBe(previous.scope)
    }
  })
  it('changes a region on a followup', () => {
    const result = parseQuestion('华南呢？', scope, base)
    expect(result.kind).toBe('ready')
    if (result.kind === 'ready') expect(result.analysis.scope.region).toBe(2)
  })
  it('resets an explicitly requested sales amount after a profit analysis', () => {
    const result = parseQuestion('看看销售额趋势', scope, { ...base, metric: 'profit' })
    if (result.kind === 'ready') expect(result.analysis.metric).toBe('amount')
    else throw new Error(result.message)
  })
  it('asks for dates before a first ambiguous request', () => {
    expect(parseQuestion('分析华东销售额', scope).kind).toBe('clarify')
  })
  it('parses arbitrary supported recent month counts', () => {
    const result = parseQuestion('最近2个完整月的订单数趋势', scope)
    if (result.kind === 'ready') {
      expect(result.analysis.scope.months).toEqual(['2026-07', '2026-08'])
      expect(result.analysis.metric).toBe('count')
    } else throw new Error(result.message)
  })
  it('parses Chinese six-month prompts from the UI', () => {
    const result = parseQuestion('分析最近六个完整月的销售趋势和同比', scope)
    if (result.kind === 'ready') expect(result.analysis.scope.months).toEqual(scope.months)
    else throw new Error(result.message)
  })
  it('parses explicit multi-month and single-month periods with spaces', () => {
    const result = parseQuestion('分析2026 年 3 月至2026 年 8 月的销售趋势', scope)
    if (result.kind === 'ready') expect(result.analysis.scope.months).toEqual(scope.months)
    else throw new Error(result.message)
    const single = parseQuestion('2026 年 8 月商品销售排名', scope)
    if (single.kind === 'ready') expect(single.analysis.scope.months).toEqual(['2026-08'])
    else throw new Error(single.message)
  })
  it('rejects incomplete, missing and invalid periods rather than fabricating zeroes', () => {
    for (const text of ['本月销售额', '2026年9月销售额', '2024年1月销售额', '2026年13月销售额', '最近30个月销售额', '2026年8月至2026年3月销售额']) {
      expect(parseQuestion(text, scope).kind, text).toBe('unsupported')
    }
  })
  it('does not invent causes or unsupported single-product filtering', () => {
    for (const text of ['销售额下降的原因', '预测销售额', '笔记本电脑销售额', '分析毛利率']) expect(parseQuestion(text, scope, base).kind).toBe('unsupported')
  })
  it('returns a helpful response to an unrelated question', () => {
    expect(parseQuestion('帮我写诗', scope).kind).toBe('unsupported')
  })
  it('escapes CSV commas, quotes and newlines', () => {
    expect(csv([['华东,华南', 'a"b', '第一行\n第二行']])).toBe('"华东,华南","a""b","第一行\n第二行"')
  })
})
