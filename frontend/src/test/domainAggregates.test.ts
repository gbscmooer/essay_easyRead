/**
 * 领域总览聚合（domainAggregates.ts）单测。
 *
 * 守住的行为契约：
 * - 分桶按簇、按规模降序，未归类（-1）垫底；
 * - 名称优先级 synthesis 范式 > TF-ICF 簇标签 > 编号兜底；
 * - 年份裁剪到最近 15 年、无年份论文单独计数；
 * - related 边双向存储必须去重后再算密度/中心性（否则双计）；
 * - 近三年窗口锚定数据里的 maxYear（不随测试运行日期漂移）；
 * - 代表作先按 synthesis 顺序、再按 importance 补齐。
 */

import { describe, expect, it } from 'vitest'
import { aggregateDomain } from '@/features/research/domainAggregates'
import type { GraphEdge, GraphNode, GraphSynthesis, ResearchGraph } from '@/types'

function node(id: string, meta: Partial<GraphNode['meta']> = {}): GraphNode {
  return { id, kind: 'paper', label: `论文 ${id}`, meta: { ...meta } }
}

function edge(a: string, b: string, weight = 1, kind: GraphEdge['kind'] = 'related'): GraphEdge {
  return { source: a, target: b, kind, weight }
}

function graph(nodes: GraphNode[], edges: GraphEdge[] = []): ResearchGraph {
  return {
    workspace_id: 1,
    run_id: 1,
    nodes,
    edges,
    updated_at: '2026-01-01T00:00:00Z',
    stats: { node_count: nodes.length, edge_count: edges.length, by_kind: {} },
  }
}

describe('aggregateDomain', () => {
  it('空图谱返回 null', () => {
    expect(aggregateDomain(null)).toBeNull()
    expect(aggregateDomain(graph([]))).toBeNull()
  })

  it('按簇分桶、按规模降序，未归类垫底', () => {
    const agg = aggregateDomain(graph([
      node('a', { cluster: 0 }),
      node('b', { cluster: 0 }),
      node('c', { cluster: 1 }),
      node('d', { cluster: 1 }),
      node('e', { cluster: 1 }),
      node('iso', { cluster: -1 }),
    ]))
    expect(agg).not.toBeNull()
    expect(agg!.buckets.map(b => b.clusterId)).toEqual([1, 0, -1])
    expect(agg!.buckets.map(b => b.paperCount)).toEqual([3, 2, 1])
    expect(agg!.buckets[2].name).toBe('未归类')
    expect(agg!.buckets[0].name).toBe('子方向 2') // cluster 1 + 1，无标签时编号兜底
  })

  it('名称优先级：synthesis 范式 > TF-ICF 标签 > 编号', () => {
    const g = graph([
      node('a', { cluster: 0, cluster_label: 'diffusion · sampler' }),
      node('c', { cluster: 2 }),
    ])
    const plain = aggregateDomain(g)
    expect(plain!.buckets.find(b => b.clusterId === 0)).toMatchObject({
      name: 'diffusion · sampler',
      labelSource: 'tficf',
    })

    const synthesis: GraphSynthesis = {
      paradigms: [{
        cluster_id: 0,
        name: '扩散生成流派',
        core_philosophy: '',
        pros: [],
        cons: [],
        representative_node_ids: [],
      }],
      enriched_nodes: {},
      enriched_edges: {},
    }
    const withSyn = aggregateDomain(g, synthesis)
    expect(withSyn!.buckets.find(b => b.clusterId === 0)).toMatchObject({
      name: '扩散生成流派',
      labelSource: 'synthesis',
    })
  })

  it('年份裁剪到最近 15 年，无年份论文单独计数', () => {
    const agg = aggregateDomain(graph([
      node('old', { cluster: 0, year: 2005 }),
      node('new', { cluster: 0, year: 2024 }),
      node('mid', { cluster: 0, year: 2015 }),
      node('unknown', { cluster: 0 }),
    ]))
    // maxYear=2024 → 起始 2010，2005 被裁掉
    expect(agg!.years).toHaveLength(15)
    expect(agg!.years[0]).toBe(2010)
    expect(agg!.years[14]).toBe(2024)
    expect(agg!.buckets[0].byYear[2005]).toBeUndefined()
    expect(agg!.buckets[0].byYear[2015]).toBe(1)
    expect(agg!.unknownYearCount).toBe(1)
  })

  it('related 边双向存储去重后再算密度与中心性', () => {
    const agg = aggregateDomain(graph(
      [
        node('a', { cluster: 0 }),
        node('b', { cluster: 0 }),
        node('c', { cluster: 0 }),
        node('d', { cluster: 1 }),
        node('e', { cluster: 1 }),
      ],
      [
        edge('a', 'b', 0.8), edge('b', 'a', 0.8),   // 同一对的双向存储
        edge('a', 'd', 0.4), edge('d', 'a', 0.4),
        edge('c', 'd', 0.6),                        // 单向（异常数据）也按无向对计一次
      ],
    ))
    const c0 = agg!.buckets.find(b => b.clusterId === 0)!
    const c1 = agg!.buckets.find(b => b.clusterId === 1)!
    // 簇0：intra = 0.8（去重），3 篇 → 3 对 → 密度 0.8/3
    expect(c0.density).toBeCloseTo(0.8 / 3, 6)
    // 簇0：外部 = 0.4 + 0.6 = 1.0，篇均 1.0/3
    expect(c0.centrality).toBeCloseTo(1.0 / 3, 6)
    // 簇1：无簇内边 → 密度 0；外部 = 1.0，篇均 0.5
    expect(c1.density).toBe(0)
    expect(c1.centrality).toBeCloseTo(0.5, 6)
  })

  it('展示桥不进入子方向密度与中心性', () => {
    const agg = aggregateDomain(graph([
      node('a', { cluster: 0 }), node('b', { cluster: 0 }), node('c', { cluster: 1 }),
    ], [
      { ...edge('a', 'b'), bridge: true },
      { ...edge('a', 'c'), selection_method: 'display_bridge' },
    ]))
    expect(agg!.buckets.every(b => b.density === 0 && b.centrality === 0)).toBe(true)
  })

  it('近三年窗口锚定数据 maxYear', () => {
    const agg = aggregateDomain(graph([
      node('a', { cluster: 0, year: 2024 }),
      node('b', { cluster: 0, year: 2024 }),
      node('c', { cluster: 0, year: 2010 }),
    ]))
    // 窗口 = maxYear-2 .. maxYear = 2022..2024 → 2/3
    expect(agg!.buckets[0].recentShare).toBeCloseTo(2 / 3, 6)
  })

  it('代表作先按 synthesis 顺序、再按 importance 补齐，上限 5', () => {
    const nodes = Array.from({ length: 7 }, (_, i) =>
      node(`p${i}`, { cluster: 0, importance: (7 - i) / 10 }))
    const synthesis: GraphSynthesis = {
      paradigms: [{
        cluster_id: 0,
        name: '流派',
        core_philosophy: '',
        pros: [],
        cons: [],
        representative_node_ids: ['p5', 'p0'],
      }],
      enriched_nodes: {},
      enriched_edges: {},
    }
    const agg = aggregateDomain(graph(nodes), synthesis)
    const ids = agg!.buckets[0].representatives.map(n => n.id)
    expect(ids).toEqual(['p5', 'p0', 'p1', 'p2', 'p3'])
  })

  it('maxCellCount 与中位数象限分割只算有效子方向', () => {
    const agg = aggregateDomain(graph([
      node('a', { cluster: 0, year: 2024 }),
      node('b', { cluster: 0, year: 2024 }),
      node('c', { cluster: 1, year: 2024 }),
      node('iso', { cluster: -1, year: 2024 }),
    ]))
    expect(agg!.maxCellCount).toBe(2)
    // 两个有效簇都没有边 → 密度/中心性全 0，中位数 0，不影响渲染（span=0 时居中）
    expect(agg!.medianDensity).toBe(0)
    expect(agg!.medianCentrality).toBe(0)
  })
})
