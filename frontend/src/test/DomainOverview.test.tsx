/**
 * 领域总览组件（DomainOverview.tsx）渲染冒烟：
 * 热力图/卡片/统计条能从图谱数据里长出来，代表作可点击回传节点。
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GraphNode, GraphSynthesis, ResearchGraph } from '@/types'
import { DomainOverview } from '@/features/research/DomainOverview'

function node(id: string, label: string, meta: Partial<GraphNode['meta']>): GraphNode {
  return { id, kind: 'paper', label, meta }
}

const graph: ResearchGraph = {
  workspace_id: 1,
  run_id: 1,
  updated_at: '2026-01-01T00:00:00Z',
  stats: { node_count: 5, edge_count: 2, by_kind: {}, modularity: 0.31 },
  nodes: [
    node('paper:1', 'Attention is all you need', { cluster: 0, cluster_label: 'transformer · attention', year: 2017, importance: 0.9, citation_count: 120000 }),
    node('paper:2', 'BERT', { cluster: 0, cluster_label: 'transformer · attention', year: 2018, importance: 0.7, citation_count: 80000 }),
    node('paper:3', 'GCN', { cluster: 1, cluster_label: 'graph · message-passing', year: 2016, importance: 0.8, citation_count: 30000 }),
    node('paper:4', 'GAT', { cluster: 1, cluster_label: 'graph · message-passing', year: 2017, importance: 0.5, citation_count: 9000 }),
    node('paper:5', '孤立论文', { cluster: -1 }),
  ],
  edges: [
    { source: 'paper:1', target: 'paper:2', kind: 'related', weight: 0.7 },
    { source: 'paper:2', target: 'paper:1', kind: 'related', weight: 0.7 },
  ],
}

const synthesis: GraphSynthesis = {
  paradigms: [
    {
      cluster_id: 0,
      name: 'Transformer 架构流',
      core_philosophy: '用自注意力取代循环与卷积',
      pros: ['并行训练'],
      cons: ['序列长度平方开销'],
      representative_node_ids: ['paper:1', 'paper:2'],
    },
    {
      cluster_id: 1,
      name: '图神经网络',
      core_philosophy: '在图结构上做消息传递',
      pros: ['利用结构先验'],
      cons: ['过平滑'],
      representative_node_ids: ['paper:3'],
    },
  ],
  enriched_nodes: {},
  enriched_edges: {},
}

describe('DomainOverview', () => {
  it('空图谱给出引导文案', () => {
    render(<DomainOverview graph={null} onSelectNode={() => {}} />)
    expect(screen.getByText(/尚未生成图谱/)).toBeInTheDocument()
  })

  it('渲染统计条、热力图与卡片，synthesis 范式名生效', () => {
    render(<DomainOverview graph={graph} synthesis={synthesis} onSelectNode={() => {}} />)
    expect(screen.getByText(/2 个子方向/)).toBeInTheDocument()
    // 范式名同时出现在热力图行头与卡片名
    expect(screen.getAllByText('Transformer 架构流').length).toBeGreaterThan(0)
    expect(screen.getAllByText('图神经网络').length).toBeGreaterThan(0)
    // 未归类只出现在热力图行头，不进卡片区（onViewInGraph 未传时无定位按钮，
    // 定位按钮数量在下一个用例里单独验证）
    expect(screen.getByText('未归类')).toBeInTheDocument()
  })

  it('点击代表作回传节点，卡片定位回传节点 id', () => {
    const onSelectNode = vi.fn()
    const onViewInGraph = vi.fn()
    render(
      <DomainOverview
        graph={graph}
        synthesis={synthesis}
        onSelectNode={onSelectNode}
        onViewInGraph={onViewInGraph}
      />,
    )
    fireEvent.click(screen.getByTitle('Attention is all you need'))
    expect(onSelectNode).toHaveBeenCalledTimes(1)
    expect(onSelectNode.mock.calls[0][0].id).toBe('paper:1')

    const locateButtons = screen.getAllByText('在图谱中查看')
    expect(locateButtons).toHaveLength(2) // 只有两个有效子方向
    fireEvent.click(locateButtons[0])
    expect(onViewInGraph).toHaveBeenCalledWith('paper:1')
  })
})
