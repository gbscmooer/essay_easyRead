import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useExplorationStream } from '@/hooks/useExplorationStream'

class MockEventSource {
  static current: MockEventSource | null = null
  listeners = new Map<string, (event: MessageEvent) => void>()

  constructor(public url: string) {
    MockEventSource.current = this
  }

  addEventListener(name: string, handler: EventListenerOrEventListenerObject) {
    this.listeners.set(name, handler as (event: MessageEvent) => void)
  }

  close() {}

  emit(name: string, data: object) {
    this.listeners.get(name)?.({ data: JSON.stringify(data) } as MessageEvent)
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
    expect(result.current.message).toBe('证据综合 · 第 3 轮 · 正在使用 inspect_evidence')
    expect(result.current.message).not.toContain('should not be displayed')

    act(() => MockEventSource.current?.emit('stage_iteration', {
      stage: 'synthesize', iteration: 4, phase: 'complete',
    }))
    expect(result.current.message).toBe('证据综合 · 第 4 轮验收通过')
    unmount()
  })
})
