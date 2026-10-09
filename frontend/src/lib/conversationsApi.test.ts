import { afterEach, describe, expect, it, vi } from 'vitest'
import { analysisSpec, createRun, readRunEvents, resumeRun } from './conversationsApi'

vi.mock('./auth', async () => {
  const real = await vi.importActual<typeof import('./auth')>('./auth')
  return { ...real, authFetch: (url: string, init: RequestInit) => fetch(url, init) }
})
afterEach(() => vi.unstubAllGlobals())

function frame(seq: number, type = 'stage.started', rid = 'run-1') {
  return `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ schema_version: 1, run_id: rid, session_id: 'session-1', seq, type, payload: { stage: 'query', message: '查询华东数据' } })}\n\n`
}
function response(chunks: Uint8Array[]) {
  return new Response(new ReadableStream({ start(controller) { chunks.forEach(chunk => controller.enqueue(chunk)); controller.close() } }), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
}

describe('authenticated task and SSE contract', () => {
  it('submits the original question for server parsing and sends raw clarification replies', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}')))
    vi.stubGlobal('fetch', fetchMock)
    await createRun('session-1', { question: '2026年8月华东卖了多少钱？', mode: 'auto' }, 'intent-key')
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual({ question: '2026年8月华东卖了多少钱？', mode: 'auto' })
    await resumeRun('run-1', undefined, '改为2026年7月')
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body)).toEqual({ reply: '改为2026年7月' })
  })
  it('only submits whitelisted analysis fields and carries an idempotency key', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetchMock)
    const analysis = { scope: { months: ['2026-08'], region: 1 }, metric: 'amount' as const, view: 'trend' as const, chart: 'line' as const, question: '问题', dataset: { secret: 'client-data' } }
    const spec = analysisSpec(analysis as never)
    await createRun('session-1', { question: '问题', mode: 'analysis', analysis: spec }, 'same-key')
    const init = fetchMock.mock.calls[0]![1]
    expect(init.headers['Idempotency-Key']).toBe('same-key')
    expect(JSON.parse(init.body).analysis).toEqual({ scope: analysis.scope, metric: 'amount', view: 'trend', chart: 'line' })
    expect(init.body).not.toContain('client-data')
  })
  it('decodes fragmented UTF-8, CRLF, heartbeat comments and duplicate replay events', async () => {
    const encoded = new TextEncoder().encode((': heartbeat\n\n' + frame(1) + frame(1) + frame(2, 'run.completed')).replaceAll('\n', '\r\n'))
    const chunks = Array.from(encoded, byte => Uint8Array.of(byte))
    const fetchMock = vi.fn().mockResolvedValue(response(chunks))
    vi.stubGlobal('fetch', fetchMock)
    const events: number[] = []
    const cursor = await readRunEvents('run-1', 0, event => { events.push(event.seq); expect(event.payload.message).toBe('查询华东数据') }, new AbortController().signal)
    expect(events).toEqual([1, 2]); expect(cursor).toBe(2)
    expect(fetchMock.mock.calls[0]![1].headers['Last-Event-ID']).toBe('0')
  })
  it('keeps comparison products and anomaly settings when saving a report or resuming a task', () => {
    const analysis = { scope: { months: ['2026-08'], region: 1 }, metric: 'amount' as const, view: 'products' as const, chart: 'bar' as const, comparison: 'mom' as const, sort_by: 'mom_percent' as const, sort_order: 'desc' as const, goal: 'anomalies' as const, product_ids: [1, 2], anomaly_window: 4, anomaly_threshold_percent: 25, question: '问题' }
    const spec = analysisSpec(analysis)
    expect(spec).toEqual({ scope: analysis.scope, metric: analysis.metric, view: analysis.view, chart: analysis.chart, comparison: 'mom', sort_by: 'mom_percent', sort_order: 'desc', goal: 'anomalies', product_ids: [1, 2], anomaly_window: 4, anomaly_threshold_percent: 25 })
    expect(spec.product_ids).not.toBe(analysis.product_ids)
    expect(spec.scope.months).not.toBe(analysis.scope.months)
  })
  it('continues from the last sequence without duplicating previously delivered events', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response([new TextEncoder().encode(frame(4))])))
    const receive = vi.fn()
    expect(await readRunEvents('run-1', 3, receive, new AbortController().signal)).toBe(4)
    expect(receive).toHaveBeenCalledOnce()
  })
  it.each([frame(2), frame(1, 'stage.started', 'someone-else')])('rejects gaps or another run in a stream', async data => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response([new TextEncoder().encode(data)])))
    await expect(readRunEvents('run-1', 0, vi.fn(), new AbortController().signal)).rejects.toThrow()
  })
  it('reports expired replay cursors so the caller can rebuild from the saved result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 410 })))
    await expect(readRunEvents('run-1', 3, vi.fn(), new AbortController().signal)).rejects.toMatchObject({ status: 410 })
  })
  it('rejects a broken final frame and a non-SSE proxy response', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response([new TextEncoder().encode(frame(1).slice(0, -2))])).mockResolvedValueOnce(new Response('proxy error'))
    vi.stubGlobal('fetch', fetchMock)
    await expect(readRunEvents('run-1', 0, vi.fn(), new AbortController().signal)).rejects.toThrow('帧结束前中断')
    await expect(readRunEvents('run-1', 0, vi.fn(), new AbortController().signal)).rejects.toThrow('响应格式无效')
  })
})
