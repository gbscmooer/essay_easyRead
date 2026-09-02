/**
 * 领域总览 —— 深度研究的新默认首屏。
 *
 * 背景：力导向图谱在"目标平均度 8"的骨架密度下仍是一张毛球，领域入门者
 * 读不出重点。这里把同一份图谱数据聚合到 O(流派数) 的粒度，用三个互补视图
 * 回答入门三问：
 *   1. 热力图（子方向 × 年份）：领域热度何时烧到哪里、现在聚焦什么
 *   2. 战略坐标（Callon 四象限）：谁是核心引擎、谁在上升、谁已边缘化
 *   3. 子方向卡片：每派的理念 / 优劣势 / 代表作（synthesis 可用时）
 *
 * 聚合逻辑在 domainAggregates.ts（纯函数、可单测）；本组件只做渲染与交互。
 * 颜色与图谱同源（clusterInk(簇号)），同一子方向在总览和图谱里颜色一致。
 */

import { Fragment, useMemo } from 'react'
import type { GraphNode, GraphSynthesis, ResearchGraph } from '@/types'
import { ROLE_META, clusterInk, inkWash } from '@/theme/graphPalette'
import { aggregateDomain, type DomainAggregates, type ParadigmBucket } from './domainAggregates'

interface DomainOverviewProps {
  graph: ResearchGraph | null
  synthesis?: GraphSynthesis | null
  onSelectNode: (node: GraphNode | null) => void
  /** 跳到图谱视图并定位某节点（"在图谱中查看"按钮） */
  onViewInGraph?: (nodeId: string) => void
}

function short(s: string, n = 12): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

function fmtCitations(n: number | null | undefined): string {
  if (!n || n <= 0) return ''
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

/** 被引展示：只有召回源带回过被引数才显示，避免误导 */
function repLine(node: GraphNode): string {
  const parts: string[] = []
  if (typeof node.meta.year === 'number') parts.push(String(node.meta.year))
  const c = fmtCitations(node.meta.citation_count)
  if (c) parts.push(`被引 ${c}`)
  return parts.join(' · ')
}

// ── 热力图 ────────────────────────────────────────────────────────────
// CSS grid 而非 SVG：单元格随容器自适应，悬停/数字对比度都走主题变量。

function Heatmap({ agg, onFocusNode }: {
  agg: DomainAggregates
  onFocusNode: (node: GraphNode) => void
}) {
  return (
    <div className="rs-heat">
      <div
        className="rs-heat__grid"
        style={{ gridTemplateColumns: `minmax(150px, 240px) repeat(${agg.years.length}, minmax(26px, 1fr))` }}
      >
        <div className="rs-heat__corner">子方向 / 年份</div>
        {agg.years.map(y => (
          <div key={y} className="rs-heat__colhead" title={`${y} 年`}>{y}</div>
        ))}
        {agg.buckets.map(b => {
          const ink = clusterInk(b.clusterId)
          const rep = b.representatives[0] ?? b.nodes[0]
          return (
            <Fragment key={b.clusterId}>
              <button
                type="button"
                className="rs-heat__rowhead"
                title={rep ? `点击查看代表作：${rep.label}` : b.name}
                onClick={() => rep && onFocusNode(rep)}
              >
                <span className="rs-heat__dot" style={{ background: ink }} />
                <span className="rs-heat__name">{short(b.name, 16)}</span>
                <span className="rs-heat__count">{b.paperCount}</span>
              </button>
              {agg.years.map(y => {
                const c = b.byYear[y] ?? 0
                const t = agg.maxCellCount > 0 ? c / agg.maxCellCount : 0
                return (
                  <div
                    key={y}
                    className="rs-heat__cell"
                    title={`${y} · ${b.name} · ${c} 篇`}
                    style={c > 0 ? { background: inkWash(ink, 12 + Math.round(88 * t)) } : undefined}
                  >
                    {c > 0 && (
                      <span className={t > 0.55 ? 'rs-heat__num rs-heat__num--inv' : 'rs-heat__num'}>{c}</span>
                    )}
                  </div>
                )
              })}
            </Fragment>
          )
        })}
      </div>
      <div className="rs-heat__legend">
        <span className="rs-heat__ramp">
          {[12, 34, 56, 78, 100].map(p => (
            <i key={p} style={{ background: inkWash('#3969a8', p) }} />
          ))}
        </span>
        <span>越深 = 该子方向当年论文越多</span>
        {agg.unknownYearCount > 0 && <span>另有 {agg.unknownYearCount} 篇年份未知，未计入</span>}
      </div>
    </div>
  )
}

// ── 战略坐标（Callon 四象限）──────────────────────────────────────────
// 横轴 = 篇均跨流派关联强度（外部中心性），纵轴 = 簇内平均关联度（内部密度）。
// 分割线取有效子方向的中位数：右上核心引擎 / 左上内聚深耕 / 右下跨域桥梁 / 左下边缘。

function StrategicDiagram({ agg, onFocusNode }: {
  agg: DomainAggregates
  onFocusNode: (node: GraphNode) => void
}) {
  const W = 640
  const H = 380
  const PAD = { l: 60, r: 26, t: 36, b: 48 }
  const plotW = W - PAD.l - PAD.r
  const plotH = H - PAD.t - PAD.b

  const valid = agg.buckets.filter(b => b.clusterId >= 0)
  const maxDensity = Math.max(0, ...valid.map(b => b.density))
  const maxN = Math.max(1, ...valid.map(b => b.paperCount))

  const xOf = (c: number) =>
    PAD.l + plotW * (agg.maxCentrality > 0 ? Math.min(1, c / agg.maxCentrality) : 0.5)
  const yOf = (d: number) =>
    PAD.t + plotH * (maxDensity > 0 ? 1 - Math.min(1, d / maxDensity) : 0.5)
  const splitX = xOf(agg.medianCentrality)
  const splitY = yOf(agg.medianDensity)

  return (
    <svg className="rs-quad" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="子方向战略坐标四象限图">
      <rect className="rs-quad__frame" x={PAD.l} y={PAD.t} width={plotW} height={plotH} />
      <line className="rs-quad__split" x1={splitX} y1={PAD.t} x2={splitX} y2={PAD.t + plotH} />
      <line className="rs-quad__split" x1={PAD.l} y1={splitY} x2={PAD.l + plotW} y2={splitY} />
      <text className="rs-quad__corner" x={PAD.l + plotW - 6} y={PAD.t + 16} textAnchor="end">核心引擎</text>
      <text className="rs-quad__corner" x={PAD.l + 6} y={PAD.t + 16}>内聚深耕</text>
      <text className="rs-quad__corner" x={PAD.l + plotW - 6} y={PAD.t + plotH - 8} textAnchor="end">跨域桥梁</text>
      <text className="rs-quad__corner" x={PAD.l + 6} y={PAD.t + plotH - 8}>边缘方向</text>
      <text className="rs-quad__axislabel" x={PAD.l + plotW / 2} y={H - 18} textAnchor="middle">跨流派中心性 →</text>
      <text className="rs-quad__axislabel" x={16} y={PAD.t + plotH / 2} textAnchor="middle" transform={`rotate(-90 16 ${PAD.t + plotH / 2})`}>簇内密度 →</text>
      {valid.map(b => {
        const ink = clusterInk(b.clusterId)
        const cx = xOf(b.centrality)
        const cy = yOf(b.density)
        const r = 9 + 17 * Math.sqrt(b.paperCount / maxN)
        const rep = b.representatives[0] ?? b.nodes[0]
        const above = cy - r - 6 > PAD.t
        return (
          <g
            key={b.clusterId}
            className="rs-quad__bubble"
            onClick={() => rep && onFocusNode(rep)}
          >
            <title>{[
              b.name,
              `${b.paperCount} 篇`,
              `簇内密度 ${b.density.toFixed(3)}`,
              `篇均外部关联 ${b.centrality.toFixed(3)}`,
              `近三年占比 ${(b.recentShare * 100).toFixed(0)}%`,
            ].join('\n')}</title>
            <circle cx={cx} cy={cy} r={r} fill={inkWash(ink, 42)} stroke={ink} strokeWidth={1.5} />
            <text
              className="rs-quad__blabel"
              x={cx}
              y={above ? cy - r - 5 : cy + r + 13}
              textAnchor="middle"
            >
              {short(b.name, 9)}{b.recentShare >= 0.45 ? '🔥' : ''}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// ── 子方向卡片 ────────────────────────────────────────────────────────

function ParadigmCard({ bucket, synthesis, onSelectNode, onViewInGraph }: {
  bucket: ParadigmBucket
  synthesis?: GraphSynthesis | null
  onSelectNode: (node: GraphNode) => void
  onViewInGraph?: (nodeId: string) => void
}) {
  const ink = clusterInk(bucket.clusterId)
  const paradigm = synthesis?.paradigms?.find(p => p.cluster_id === bucket.clusterId)
  const anchor = bucket.representatives[0] ?? bucket.nodes[0]

  return (
    <article className="rs-card">
      <header className="rs-card__head">
        <span className="rs-card__dot" style={{ background: ink }} />
        <h4 className="rs-card__name" title={bucket.name}>{bucket.name}</h4>
        <span className="rs-card__size">{bucket.paperCount} 篇</span>
        {bucket.recentShare >= 0.45 && (
          <span className="rs-card__rising" title={`近三年论文占比 ${(bucket.recentShare * 100).toFixed(0)}%`}>
            🔥 上升中
          </span>
        )}
      </header>
      {paradigm?.core_philosophy && (
        <p className="rs-card__philosophy">{paradigm.core_philosophy}</p>
      )}
      {!paradigm && bucket.labelSource === 'tficf' && (
        <p className="rs-card__philosophy rs-card__philosophy--muted">关键词标签（未运行领域综合）</p>
      )}
      {(paradigm?.pros?.length || paradigm?.cons?.length) && (
        <div className="rs-card__procon">
          {paradigm!.pros!.length > 0 && (
            <ul>
              {paradigm!.pros!.slice(0, 3).map((p, i) => (
                <li key={i} className="rs-card__pro">✓ {p}</li>
              ))}
            </ul>
          )}
          {paradigm!.cons!.length > 0 && (
            <ul>
              {paradigm!.cons!.slice(0, 3).map((c, i) => (
                <li key={i} className="rs-card__con">✗ {c}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      <ul className="rs-card__reps">
        {bucket.representatives.map(node => {
          const role = node.semantic?.role
          const rm = role && role !== 'regular' ? ROLE_META[role] : null
          const line = repLine(node)
          return (
            <li key={node.id}>
              <button type="button" onClick={() => onSelectNode(node)} title={node.label}>
                {rm && <span style={{ color: rm.ink }}>{rm.icon}</span>}
                <span className="rs-card__replabel">{short(node.label, 34)}</span>
                {line && <span className="rs-card__repline">{line}</span>}
              </button>
            </li>
          )
        })}
      </ul>
      {onViewInGraph && anchor && (
        <button
          type="button"
          className="er-btn er-btn--secondary er-btn--sm rs-card__locate"
          onClick={() => onViewInGraph(anchor.id)}
        >
          在图谱中查看
        </button>
      )}
    </article>
  )
}

// ── 总览主组件 ────────────────────────────────────────────────────────

export function DomainOverview({ graph, synthesis, onSelectNode, onViewInGraph }: DomainOverviewProps) {
  const agg = useMemo(
    () => aggregateDomain(graph, synthesis ?? graph?.synthesis ?? null),
    [graph, synthesis],
  )

  if (!agg) {
    return (
      <div className="rs-overview__empty">
        <p>尚未生成图谱 —— 先在上方启动深度研究，跑完「图谱构建」后这里会给出领域总览。</p>
      </div>
    )
  }

  const validCount = agg.buckets.filter(b => b.clusterId >= 0).length
  const focusNode = (node: GraphNode) => { onSelectNode(node) }

  if (validCount === 0) {
    return (
      <div className="rs-overview__empty">
        <p>图谱里还没有可识别的子方向（关系边不足，所有论文都是孤立点）。</p>
        <p>可以尝试：启动一次深度研究补充召回，或切到图谱视图查看原始结构。</p>
        {onViewInGraph && (
          <button type="button" className="er-btn er-btn--secondary er-btn--sm" onClick={() => onViewInGraph(graph!.nodes[0]?.id ?? '')}>
            切换到图谱视图
          </button>
        )}
      </div>
    )
  }

  const yearSpan = agg.years.length
    ? `${agg.years[0]}–${String(agg.years[agg.years.length - 1]).slice(2)}`
    : '无年份信息'
  const modularity = graph?.stats?.modularity

  return (
    <div className="rs-overview">
      <p className="rs-overview__stats">
        {graph ? graph.stats?.node_count ?? graph.nodes.length : 0} 篇论文 · {validCount} 个子方向
        · {yearSpan}
        {modularity != null && ` · 划分质量 modularity ${modularity.toFixed(3)}`}
        <span className="rs-overview__hint">点击代表作 / 气泡 / 行头可查看论文详情</span>
      </p>

      <section className="rs-overview__section">
        <h3 className="rs-overview__title">子方向 × 年份 <span>领域热度何时烧到哪里、现在聚焦什么</span></h3>
        <Heatmap agg={agg} onFocusNode={focusNode} />
      </section>

      <section className="rs-overview__section">
        <h3 className="rs-overview__title">战略坐标 <span>横轴篇均跨流派关联，纵轴簇内密度；气泡大小 = 论文规模</span></h3>
        <StrategicDiagram agg={agg} onFocusNode={focusNode} />
      </section>

      <section className="rs-overview__section">
        <h3 className="rs-overview__title">子方向卡片 <span>{synthesis?.paradigms?.length ? '理念与优劣势来自领域认知综合' : '运行领域综合（需配置解读模型）后补充理念与优劣势'}</span></h3>
        <div className="rs-cards">
          {agg.buckets
            .filter(b => b.clusterId >= 0)
            .map(b => (
              <ParadigmCard
                key={b.clusterId}
                bucket={b}
                synthesis={synthesis}
                onSelectNode={onSelectNode}
                onViewInGraph={onViewInGraph}
              />
            ))}
        </div>
      </section>
    </div>
  )
}
