import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ResearchGraph } from '@/types'

const snapshots = vi.hoisted(() => ({ nodes: [] as Array<Array<{ id: string; x?: number; y?: number }>> }))

vi.mock('react-force-graph-2d', async () => {
  const { createElement, forwardRef, useImperativeHandle } = await vi.importActual<typeof import('react')>('react')

  interface MockProps {
    width: number
    height: number
    graphData: { nodes: Array<{ id: string; x?: number; y?: number }> }
    onZoomEnd?: (transform: { k: number }) => void
  }

  const ForceGraph2D = forwardRef(function ForceGraph2D(props: MockProps, ref) {
    snapshots.nodes.push(props.graphData.nodes)
    useImperativeHandle(ref, () => ({
      d3Force: () => ({ distance: () => {} }),
      d3ReheatSimulation: () => {},
      zoom: () => 1.25,
      zoomToFit: () => {},
    }), [])

    // force-graph 的 width/height setter 会在包装组件 render 中同步触发此回调。
    props.onZoomEnd?.({ k: 1.25 })
    return createElement('div', {
      'data-testid': 'force-graph',
      'data-width': props.width,
      'data-height': props.height,
    })
  })

  return { default: ForceGraph2D }
})

import { GraphCanvas } from '@/features/research/GraphCanvas'

const graph: ResearchGraph = {
  workspace_id: 1,
  run_id: 1,
  nodes: [{ id: 'paper-1', kind: 'paper', label: '示例论文', meta: { paper_id: 1 } }],
  edges: [],
  stats: { node_count: 1, edge_count: 0, by_kind: {} },
  updated_at: '2026-08-13T00:00:00Z',
}

let animationFrames: Map<number, FrameRequestCallback>
let frameId: number

beforeEach(() => {
  snapshots.nodes = []
  animationFrames = new Map()
  frameId = 0
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
    const id = ++frameId
    animationFrames.set(id, callback)
    return id
  }))
  vi.stubGlobal('cancelAnimationFrame', vi.fn((id: number) => {
    animationFrames.delete(id)
  }))
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width: 640,
    height: 480,
    top: 0,
    right: 640,
    bottom: 480,
    left: 0,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('GraphCanvas', () => {
  it('边类型过滤保留节点对象与已有布局坐标', () => {
    const view = render(<GraphCanvas graph={graph} edgeFilter={new Set(['related'])} onSelectNode={() => {}} />)
    const before = snapshots.nodes[snapshots.nodes.length - 1][0]
    before.x = 150; before.y = 250
    view.rerender(<GraphCanvas graph={graph} edgeFilter={new Set(['cocitation'])} onSelectNode={() => {}} />)
    const after = snapshots.nodes[snapshots.nodes.length - 1][0]
    expect(after).toBe(before)
    expect([after.x, after.y]).toEqual([150, 250])
  })

  it('底层图谱在 render 中同步回报缩放时延迟更新父组件状态', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <GraphCanvas
        graph={graph}
        edgeFilter={new Set(['related'])}
        onSelectNode={() => {}}
      />,
    )

    expect(screen.getByTestId('force-graph')).toHaveAttribute('data-width', '640')
    expect(consoleError.mock.calls.flat().join(' ')).not.toContain('Cannot update a component')

    act(() => {
      const pending = Array.from(animationFrames.values())
      animationFrames.clear()
      pending.forEach(callback => callback(16))
    })
    expect(screen.getByTitle('适配画布')).toHaveTextContent('125%')
  })
})
