import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useExplorationStream } from '@/hooks/useExplorationStream'

class MockEventSource {
  static current: MockEventSource | null = null
  listeners = new Map<string, (event: Event) => void>()
  readyState = 0
  closeCount = 0

  constructor(public url: string) {
    MockEventSource.current = this
  }

  addEventListener(name: string, handler: EventListenerOrEventListenerObject) {
    this.listeners.set(name, handler as (event: Event) => void)
  }

  close() { this.readyState = 2; this.closeCount += 1 }

  emit(name: string, data: object) {
    this.listeners.get(name)?.({ data: JSON.stringify(data) } as MessageEvent)
  }

  emitNetworkError() {
    this.listeners.get('error')?.(new Event('error'))
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  MockEventSource.current = null
})

describe('useExplorationStream single-loop events', () => {
  it('collects queries, crawl steps and newly selected papers', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const { result, rerender } = renderHook(({ id }) => useExplorationStream(id), { initialProps: { id: 12 } })
    expect(MockEventSource.current?.url).toBe('/api/exploration/runs/12/stream')
    act(() => MockEventSource.current?.emit('queries_ready', { queries: ['dense retrieval', 'retrieval survey'] }))
    expect(result.current.crawl.queries).toEqual(['dense retrieval', 'retrieval survey'])
    act(() => MockEventSource.current?.emit('crawl_step', {
      action: 'search', query: 'dense retrieval', hits: 20, new: 18, selected: 5, llm_calls: 2,
      candidates: 18, total_selected: 5, top: [{ key: 'k1', title: 'DPR', label: 2 }],
    }))
    expect(result.current.crawl.candidates).toBe(18)
    expect(result.current.crawl.selected).toBe(5)
    expect(result.current.crawl.recent[0].title).toBe('DPR')
    expect(result.current.message).toBe('检索「dense retrieval」：新增 18 篇，选中 5 篇')
    act(() => MockEventSource.current?.emit('crawl_step', {
      action: 'expand', paper: 'k1', title: 'DPR', chosen: ['Related Work'], new: 9, selected: 2, llm_calls: 2,
      candidates: 27, total_selected: 7,
    }))
    expect(result.current.message).toBe('展开「DPR」（Related Work）：新增 9 篇，选中 2 篇')
    expect(result.current.crawl.steps).toHaveLength(2)
    rerender({ id: 13 })
    expect(result.current.crawl.steps).toHaveLength(0)
  })

  it('keeps the answer from answer_ready and the snapshot from metrics_ready', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const { result, unmount } = renderHook(() => useExplorationStream(16))
    act(() => MockEventSource.current?.emit('metrics_ready', { queries: ['q'], papers: [] }))
    expect(result.current.result?.queries).toEqual(['q'])
    act(() => MockEventSource.current?.emit('answer_ready', { status: 'ok', text: 'A [1].', references: [] }))
    expect(result.current.answer?.text).toBe('A [1].')
    act(() => MockEventSource.current?.emit('done', {}))
    expect(result.current.status).toBe('done')
    unmount()
  })

  it('records per-stage timings from server ts for the timing panel', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const { result, unmount } = renderHook(() => useExplorationStream(13))
    act(() => MockEventSource.current?.emit('stage_start', { stage: 'search', ts: 1000 }))
    expect(result.current.stageTimings.search).toEqual({ startTs: 1_000_000 })
    act(() => MockEventSource.current?.emit('stage_done', { stage: 'search', ts: 1092.5 }))
    expect(result.current.stageTimings.search).toEqual({ startTs: 1_000_000, endTs: 1_092_500 })
    // 暂停→续跑后同阶段再次 stage_start：覆盖为新计时段
    act(() => MockEventSource.current?.emit('stage_start', { stage: 'search', ts: 5000 }))
    expect(result.current.stageTimings.search).toEqual({ startTs: 5_000_000 })
    unmount()
  })

  it('lets EventSource reconnect after a transient network error', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const { result, unmount } = renderHook(() => useExplorationStream(14))
    const source = MockEventSource.current!
    act(() => source.emitNetworkError())
    expect(result.current.status).toBe('running')
    expect(result.current.message).toContain('自动重连')
    expect(source.closeCount).toBe(0)
    act(() => source.emit('stage_start', { stage: 'answer', ts: 1000 }))
    expect(result.current.message).toBe('正在起草答案并为每句查找原文证据…')
    unmount()
  })

  it('stops on a server-sent error event', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const { result, unmount } = renderHook(() => useExplorationStream(15))
    const source = MockEventSource.current!
    act(() => source.emit('error', { message: '研究失败' }))
    expect(result.current.status).toBe('failed')
    expect(result.current.error).toBe('研究失败')
    expect(source.closeCount).toBe(1)
    unmount()
  })
})
