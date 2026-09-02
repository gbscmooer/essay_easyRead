/**
 * 领域总览聚合 —— 把力导向图谱压缩成人可读的宏观统计。
 *
 * 设计动机：图谱骨架按「目标平均度 8」留边，100 篇论文就是 400+ 条关系边，
 * 节点-连线布局在这个密度下的可读性会崩（Ghoniem et al. 2005 的对比研究结论）。
 * 领域入门需要的是先把信息聚合到「子方向 × 年份」这种 O(流派数) 的粒度：
 * 热力图回答"什么时候热"、战略坐标回答"谁是核心/谁在上升"、卡片回答"每派是什么"。
 *
 * 所有量都从已持久化的图谱字段（meta.cluster / meta.year / meta.citation_count /
 * meta.importance、related 边权）前端计算，不依赖 LLM synthesis；
 * synthesis 的范式命名可用则用，没有就回退 TF-ICF 簇标签，再回退编号。
 */

import type { GraphNode, GraphSynthesis, ResearchGraph } from '@/types'

/** 热力图最多展示的年份数：更早的裁掉，长尾不该稀释近年的格子 */
export const MAX_YEAR_SPAN = 15
/** 「近三年」窗口：maxYear-2 .. maxYear（含端点共 3 年） */
const RECENT_WINDOW = 2
/** 每个子方向最多展示的代表论文数 */
const MAX_REPRESENTATIVES = 5

export type LabelSource = 'synthesis' | 'tficf' | 'fallback'

export interface ParadigmBucket {
  clusterId: number            // meta.cluster 原值；-1 = 未归类（孤立点）
  name: string
  labelSource: LabelSource
  nodes: GraphNode[]
  paperCount: number
  citationMass: number         // 簇内被引数之和（缺失按 0）
  recentShare: number          // 近三年论文占比 [0,1]
  byYear: Record<number, number>
  /** 簇内平均边权（密度）：intraWeight / 簇内论文对数，无对时 0 */
  density: number
  /** 篇均外部关联强度（中心性）：与外簇的边权和 / 簇内论文数 */
  centrality: number
  representatives: GraphNode[]
}

export interface DomainAggregates {
  /** 按 paperCount 降序；未归类（-1）固定垫底 */
  buckets: ParadigmBucket[]
  /** 热力图列：从裁剪后的最早年到 maxYear 的连续序列 */
  years: number[]
  /** 无有效年份的论文数（热力图不计入，提示里说明） */
  unknownYearCount: number
  maxCellCount: number
  /** 象限分割线：仅用有效子方向（clusterId ≥ 0）取中位数 */
  medianDensity: number
  medianCentrality: number
  maxCentrality: number
}

function median(values: number[]): number {
  if (values.length === 0) return 0
  const s = [...values].sort((a, b) => a - b)
  const mid = Math.floor(s.length / 2)
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2
}

export function aggregateDomain(
  graph: ResearchGraph | null | undefined,
  synthesis?: GraphSynthesis | null,
): DomainAggregates | null {
  if (!graph || graph.nodes.length === 0) return null
  const papers = graph.nodes.filter(n => n.kind === 'paper')
  if (papers.length === 0) return null

  const nodeCluster = new Map<string, number>()
  const byCluster = new Map<number, GraphNode[]>()
  for (const n of papers) {
    const c = typeof n.meta.cluster === 'number' ? n.meta.cluster : -1
    nodeCluster.set(n.id, c)
    const arr = byCluster.get(c)
    if (arr) arr.push(n)
    else byCluster.set(c, [n])
  }

  // related 边双向存储：按无向对去重后再聚合，密度/中心性才不会双计
  const intraWeight = new Map<number, number>()
  const interWeight = new Map<number, number>()
  const seenPairs = new Set<string>()
  for (const e of graph.edges) {
    if (e.kind !== 'related' && e.kind !== 'similar') continue
    const a = nodeCluster.get(e.source)
    const b = nodeCluster.get(e.target)
    if (a === undefined || b === undefined) continue
    const key = e.source < e.target ? `${e.source}|${e.target}` : `${e.target}|${e.source}`
    if (seenPairs.has(key)) continue
    seenPairs.add(key)
    const w = e.weight || 0
    if (a === b) {
      intraWeight.set(a, (intraWeight.get(a) ?? 0) + w)
    } else {
      interWeight.set(a, (interWeight.get(a) ?? 0) + w)
      interWeight.set(b, (interWeight.get(b) ?? 0) + w)
    }
  }

  const nowYear = new Date().getFullYear()
  const knownYears = papers
    .map(n => n.meta.year)
    .filter((y): y is number => typeof y === 'number' && y > 1900 && y <= nowYear + 1)
  const maxYear = knownYears.length ? Math.max(...knownYears) : nowYear
  const spanStart = knownYears.length
    ? Math.max(Math.min(...knownYears), maxYear - (MAX_YEAR_SPAN - 1))
    : maxYear
  const years: number[] = []
  for (let y = spanStart; y <= maxYear; y++) years.push(y)

  const buckets: ParadigmBucket[] = []
  for (const [clusterId, nodes] of byCluster) {
    const n = nodes.length
    const byYear: Record<number, number> = {}
    let citationMass = 0
    let recentCount = 0
    for (const node of nodes) {
      const y = node.meta.year
      if (typeof y === 'number' && y >= spanStart && y <= maxYear) {
        byYear[y] = (byYear[y] ?? 0) + 1
      }
      if (typeof y === 'number' && y >= maxYear - RECENT_WINDOW) recentCount++
      citationMass += node.meta.citation_count ?? 0
    }
    const pairs = n > 1 ? (n * (n - 1)) / 2 : 0
    const paradigm = synthesis?.paradigms?.find(p => p.cluster_id === clusterId)
    // TF-ICF 簇标签同一簇内共享，取第一个非空即可
    const tficf = nodes.map(x => x.meta.cluster_label).find(l => l && l.trim())
    const name = clusterId < 0
      ? '未归类'
      : paradigm?.name || tficf || `子方向 ${clusterId + 1}`
    const labelSource: LabelSource = clusterId < 0
      ? 'fallback'
      : paradigm?.name ? 'synthesis' : tficf ? 'tficf' : 'fallback'

    // 代表作：优先 synthesis 给出的顺序，缺失/不全的按重要性补齐
    const ranked = [...nodes].sort(
      (a, b) =>
        (b.meta.importance ?? 0) - (a.meta.importance ?? 0) ||
        (b.meta.citation_count ?? 0) - (a.meta.citation_count ?? 0),
    )
    let representatives: GraphNode[]
    if (paradigm?.representative_node_ids?.length) {
      const byId = new Map(nodes.map(x => [x.id, x]))
      const picked: GraphNode[] = []
      for (const rid of paradigm.representative_node_ids) {
        const node = byId.get(rid)
        if (node && !picked.includes(node)) picked.push(node)
      }
      for (const node of ranked) {
        if (picked.length >= MAX_REPRESENTATIVES) break
        if (!picked.includes(node)) picked.push(node)
      }
      representatives = picked.slice(0, MAX_REPRESENTATIVES)
    } else {
      representatives = ranked.slice(0, MAX_REPRESENTATIVES)
    }

    buckets.push({
      clusterId,
      name,
      labelSource,
      nodes,
      paperCount: n,
      citationMass,
      recentShare: n > 0 ? recentCount / n : 0,
      byYear,
      density: pairs > 0 ? (intraWeight.get(clusterId) ?? 0) / pairs : 0,
      centrality: n > 0 ? (interWeight.get(clusterId) ?? 0) / n : 0,
      representatives,
    })
  }

  buckets.sort((x, y) => {
    if ((x.clusterId < 0) !== (y.clusterId < 0)) return x.clusterId < 0 ? 1 : -1
    return y.paperCount - x.paperCount || x.clusterId - y.clusterId
  })

  const valid = buckets.filter(b => b.clusterId >= 0)
  let maxCellCount = 0
  for (const b of buckets) {
    for (const y of years) maxCellCount = Math.max(maxCellCount, b.byYear[y] ?? 0)
  }

  return {
    buckets,
    years,
    unknownYearCount: papers.length - knownYears.length,
    maxCellCount,
    medianDensity: median(valid.map(b => b.density)),
    medianCentrality: median(valid.map(b => b.centrality)),
    maxCentrality: Math.max(0, ...valid.map(b => b.centrality)),
  }
}
