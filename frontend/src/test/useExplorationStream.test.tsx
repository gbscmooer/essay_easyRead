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

describe('useExplorationStream stage loop events', () => {
  it('shows bounded ReAct iteration progress without exposing rationale text', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const { result, unmount } = renderHook(() => useExplorationStream(12))
    expect(MockEventSource.current?.url).toBe('/api/exploration/runs/12/stream')

    act(() => MockEventSource.current?.emit('stage_iteration', {
      stage: 'synthesize', iteration: 3, phase: 'tool', tool: 'inspect_evidence',
      rationale_summary: 'should not be displayed',
    }))
    expect(result.current.message).toBe('证据综合与写作 · 第 3 轮 · 正在使用 inspect_evidence')
    expect(result.current.message).not.toContain('should not be displayed')

    act(() => MockEventSource.current?.emit('stage_iteration', {
      stage: 'synthesize', iteration: 4, phase: 'complete',
    }))
    expect(result.current.message).toBe('证据综合与写作 · 第 4 轮验收通过')
    unmount()
  })

  it('records per-stage timings from server ts for the timing panel', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const { result, unmount } = renderHook(() => useExplorationStream(13))

    act(() => MockEventSource.current?.emit('stage_start', { stage: 'recall', ts: 1000 }))
    expect(result.current.stageTimings.recall).toEqual({ startTs: 1_000_000 })

    act(() => MockEventSource.current?.emit('stage_done', { stage: 'recall', ts: 1092.5, summary: {} }))
    expect(result.current.stageTimings.recall).toEqual({ startTs: 1_000_000, endTs: 1_092_500 })

    // 暂停→续跑后同阶段再次 stage_start：覆盖为新计时段（清掉旧 endTs），
    // 否则暂停间隔会被计入该阶段走秒
    act(() => MockEventSource.current?.emit('stage_start', { stage: 'recall', ts: 5000 }))
    expect(result.current.stageTimings.recall).toEqual({ startTs: 5_000_000 })

    // 无 ts 的旧事件回退本地时钟，仍可记录（不抛错）
    act(() => MockEventSource.current?.emit('stage_start', { stage: 'enrich' }))
    expect(result.current.stageTimings.enrich.startTs).toBeLessThanOrEqual(Date.now())
    unmount()
  })

  it('lets EventSource reconnect after a transient network error', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const { result, unmount } = renderHook(() => useExplorationStream(14))
    const source = MockEventSource.current!

    act(() => source.emitNetworkError())
    expect(result.current.status).toBe('running')
    expect(result.current.message).toContain('自动重连')
    expect(result.current.error).toBeNull()
    expect(source.closeCount).toBe(0)

    act(() => source.emit('stage_start', { stage: 'report', ts: 1000 }))
    expect(result.current.message).toBe('正在整理报告并保存交付版本…')
    act(() => source.emit('stage_iteration', { stage: 'report', iteration: 1, phase: 'tool', tool: 'save_report' }))
    expect(result.current.message).toContain('报告保存')
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
