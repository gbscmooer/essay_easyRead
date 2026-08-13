import { useRef, useEffect, useMemo, useCallback, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import type { GraphNode, ResearchGraph, GraphEdgeKind, RelationDetail, CitationContext } from '@/types'

export interface SelectedEdge {
  sourceLabel: string
  targetLabel: string
  kind: GraphEdgeKind
  weight: number
  detail?: RelationDetail
  contexts?: CitationContext[]
}

interface GraphCanvasProps {
  graph: ResearchGraph | null
  edgeFilter: Set<GraphEdgeKind>
  onSelectNode: (node: GraphNode | null) => void
  onSelectEdge?: (edge: SelectedEdge | null) => void
  linkDistance?: number
}

const ZOOM_MIN = 0.1
const ZOOM_MAX = 8
const ZOOM_STEP = 1.4
import { EDGE_INK, NODE_INK, clusterInk } from '@/theme/graphPalette'

const DEFAULT_LINK_DISTANCE = 50

// 研究子方向（Louvain 社区）配色与边色统一取自 theme/graphPalette 的油墨分类色。
// 之前这里是一串 Tailwind 默认色，饱和度比界面主色高一大截，落在米黄纸面上
// 像贴上去的塑料片；而且同一语义在 EdgeFilter / EdgeDetail 各写一遍。

export function GraphCanvas({ graph, edgeFilter, onSelectNode, onSelectEdge, linkDistance = DEFAULT_LINK_DISTANCE }: GraphCanvasProps) {
  const fgRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const zoomFrameRef = useRef<number | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [zoomLevel, setZoomLevel] = useState(1)
  const labelColorRef = useRef<string>('rgba(230,230,230,0.92)')
  const strokeColorRef = useRef<string>('rgba(255,255,255,0.9)')
  const expandedStrokeColorRef = useRef<string>('rgba(245,158,11,0.95)')

  // Cache CSS variable values for canvas drawing (avoid reading per-frame)
  useEffect(() => {
    const style = getComputedStyle(document.documentElement)
    const c = style.getPropertyValue('--text-primary').trim()
    if (c) labelColorRef.current = c
    const border = style.getPropertyValue('--border-light').trim()
    if (border) strokeColorRef.current = border
    const warning = style.getPropertyValue('--warning').trim()
    if (warning) expandedStrokeColorRef.current = warning
  }, [])

  // 监听容器尺寸 → 显式传给 ForceGraph2D（否则它默认用 window 尺寸，flex 布局下会渲染失败）
  // ref callback：元素挂载时立即测一次 + ResizeObserver 跟踪后续变化
  const setContainer = useCallback((el: HTMLDivElement | null) => {
    containerRef.current = el
    if (!el) return
    const rect = el.getBoundingClientRect()
    if (rect.width > 0 && rect.height > 0) {
      setSize({ w: Math.floor(rect.width), h: Math.floor(rect.height) })
    }
  }, [])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        const cr = entry.contentRect
        const w = Math.max(0, Math.floor(cr.width))
        const h = Math.max(0, Math.floor(cr.height))
        setSize(prev => (prev.w === w && prev.h === h ? prev : { w, h }))
      }
    })
    ro.observe(el)
    // 兜底：下一帧再测一次，应对父容器异步铺开
    const raf = requestAnimationFrame(() => {
      const r = el.getBoundingClientRect()
      if (r.width > 0 && r.height > 0) {
        setSize(prev =>
          prev.w === Math.floor(r.width) && prev.h === Math.floor(r.height)
            ? prev
            : { w: Math.floor(r.width), h: Math.floor(r.height) }
        )
      }
    })
    return () => {
      ro.disconnect()
      cancelAnimationFrame(raf)
    }
  }, [])

  const handleZoomEnd = useCallback((transform: { k: number }) => {
    if (!Number.isFinite(transform.k)) return
    if (zoomFrameRef.current !== null) cancelAnimationFrame(zoomFrameRef.current)
    zoomFrameRef.current = requestAnimationFrame(() => {
      zoomFrameRef.current = null
      setZoomLevel(current => current === transform.k ? current : transform.k)
    })
  }, [])

  useEffect(() => () => {
    if (zoomFrameRef.current !== null) cancelAnimationFrame(zoomFrameRef.current)
  }, [])

  const graphData = useMemo(() => {
    if (!graph) return { nodes: [], links: [] }
    // 旧图谱（v2 及以前）里关系边叫 similar，与 related 同属「关联」筛选项
    const filteredEdges = graph.edges.filter(e => {
      const kind = (e.kind === 'similar' ? 'related' : e.kind) as GraphEdgeKind
      return edgeFilter.has(kind)
    })
    // 始终显示所有节点（含孤立节点），不因无边而过滤
    const visibleNodes = graph.nodes

    return {
      nodes: visibleNodes.map(n => ({
        id: n.id,
        name: n.label,
        kind: n.kind,
        meta: n.meta,
        _raw: n,
      })),
      links: filteredEdges.map(e => ({
        source: e.source,
        target: e.target,
        kind: e.kind,
        weight: e.weight,
        detail: e.detail,
        contexts: e.contexts,
      })),
    }
  }, [graph, edgeFilter])

  useEffect(() => {
    if (fgRef.current && graphData.nodes.length > 0 && size.w > 0) {
      // 等一帧让布局先跑一下再 fit
      const t = setTimeout(() => {
        try {
          fgRef.current?.zoomToFit(400, 60)
          setZoomLevel(fgRef.current?.zoom?.() ?? 1)
        } catch { /* noop */ }
      }, 50)
      return () => clearTimeout(t)
    }
  }, [graphData.nodes.length, size.w])

  // Update link distance independently (no zoom reset)
  useEffect(() => {
    if (fgRef.current && graphData.nodes.length > 0) {
      try {
        fgRef.current.d3Force('link')?.distance(linkDistance)
        // Reheat simulation so the layout adjusts to the new distance
        fgRef.current.d3ReheatSimulation?.()
      } catch { /* noop */ }
    }
  }, [linkDistance, graphData.nodes.length])

  const nodeColor = useCallback((node: any) => {
    if (node.kind === 'paper') {
      // 优先按研究子方向（Louvain 社区）着色；无聚类时回退到来源色
      const c = node.meta?.cluster
      if (typeof c === 'number' && c >= 0) return clusterInk(c)
      return node.meta?.source === 'expanded' ? NODE_INK.paperExpanded : NODE_INK.paperLocal
    }
    if (node.kind === 'method') return NODE_INK.method
    if (node.kind === 'dataset') return NODE_INK.dataset
    return '#6b7079'
  }, [])

  const linkColor = useCallback((link: any) => {
    if (link.kind === 'cites') return EDGE_INK.cites
    if (link.kind === 'related' || link.kind === 'similar') return EDGE_INK.related
    if (link.kind === 'bibcoupling') return EDGE_INK.bibcoupling
    if (link.kind === 'cocitation') return EDGE_INK.cocitation
    if (link.kind === 'shared-method') return EDGE_INK['shared-method']
    if (link.kind === 'uses-dataset') return EDGE_INK['uses-dataset']
    return '#6b7079'
  }, [])

  const handleNodeClick = useCallback((node: any) => {
    if (node._raw) {
      onSelectNode(node._raw)
    }
  }, [onSelectNode])

  // 点击连线 → 弹出相似度分解（source/target 经 force-graph 处理后是节点对象）
  const handleLinkClick = useCallback((link: any) => {
    if (!onSelectEdge) return
    const srcLabel = typeof link.source === 'object' ? link.source.name : String(link.source)
    const tgtLabel = typeof link.target === 'object' ? link.target.name : String(link.target)
    onSelectEdge({
      sourceLabel: srcLabel || '',
      targetLabel: tgtLabel || '',
      kind: link.kind,
      weight: link.weight,
      detail: link.detail,
      contexts: link.contexts,
    })
  }, [onSelectEdge])

  // 节点绘制：半径随 importance（PageRank）放大，边框按来源区分上传/扩展
  const drawNode = useCallback((node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const label = node.name || ''
    const importance = typeof node.meta?.importance === 'number' ? node.meta.importance : 0
    const r = 4 + importance * 8  // 关键论文（importance≈1）半径 12，普通节点 4
    ctx.beginPath()
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
    ctx.fillStyle = nodeColor(node)
    ctx.fill()
    // 边框：上传=白，扩展=琥珀；importance 高则描边更粗，进一步突出关键论文
    ctx.strokeStyle = node.meta?.source === 'expanded' ? expandedStrokeColorRef.current : strokeColorRef.current
    ctx.lineWidth = (importance > 0.6 ? 4.0 : 2.5) / globalScale
    ctx.stroke()

    // 文字（缩放过小时隐藏，避免过密）
    if (globalScale > 0.6) {
      const fontSize = 12 / globalScale
      ctx.font = `${fontSize}px ui-sans-serif, system-ui, -apple-system, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillStyle = labelColorRef.current
      const maxLen = 24
      const text = label.length > maxLen ? label.slice(0, maxLen) + '…' : label
      ctx.fillText(text, node.x, node.y + r + 2)
    }
  }, [nodeColor])

  const zoomBy = useCallback((factor: number) => {
    if (!fgRef.current) return
    const cur = fgRef.current.zoom?.() ?? 1
    const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, cur * factor))
    fgRef.current.zoom(next, 200)
    setZoomLevel(next)
  }, [])

  const resetZoom = useCallback(() => {
    if (!fgRef.current) return
    fgRef.current.zoomToFit(400, 60)
    setTimeout(() => setZoomLevel(fgRef.current?.zoom?.() ?? 1), 450)
  }, [])

  // Zoom level synced via onZoomEnd callback — no polling needed

  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}>
        <div className="text-center">
          <p className="text-[14px]" style={{ color: 'var(--text-secondary)' }}>暂无图谱</p>
          <p className="text-[12px] mt-1.5" style={{ color: 'var(--text-muted)' }}>点击「开始研究」构建知识图谱</p>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={setContainer}
      className="flex-1 overflow-hidden relative min-h-[300px]"
      style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-base)' }}
    >
      {size.w > 0 && size.h > 0 && (
        <ForceGraph2D
          ref={fgRef}
          graphData={graphData}
          width={size.w}
          height={size.h}
          nodeColor={nodeColor}
          nodeRelSize={6}
          nodeVal={(node: any) => Math.pow((4 + (node.meta?.importance ?? 0) * 8) / 6, 2)}
          nodeLabel={(node: any) => `<b>${node.name}</b><br/><small>${node.meta?.cluster_label ? '子方向: ' + node.meta.cluster_label : node.kind}</small>`}
          nodeCanvasObject={drawNode}
          nodeCanvasObjectMode={() => 'replace'}
          linkColor={linkColor}
          linkDirectionalArrowLength={4}
          linkDirectionalArrowRelPos={0.9}
          linkWidth={(link: any) => {
            if (link.kind === 'cites') {
              const n = link.contexts?.length ?? 0
              if (n >= 4) return 3.5
              if (n >= 2) return 2.5
              if (n >= 1) return 1.8
              return 1
            }
            return Math.max(1, link.weight * 3)
          }}
          linkLabel={(link: any) => {
            if (link.kind === 'cites' && link.contexts?.length) {
              return `引用 · ${link.contexts.length} 处上下文 · 点击查看`
            }
            if (link.detail) {
              return `综合相似度 ${link.weight.toFixed(2)} · 点击查看分解`
            }
            return `${link.kind} (${link.weight.toFixed(2)})`
          }}
          onNodeClick={handleNodeClick}
          onLinkClick={handleLinkClick}
          onZoomEnd={handleZoomEnd}
          cooldownTicks={100}
          d3AlphaDecay={0.02}
          d3VelocityDecay={0.3}
          minZoom={ZOOM_MIN}
          maxZoom={ZOOM_MAX}
          enableZoomInteraction={true}
          enablePanInteraction={true}
        />
      )}

      {/* 缩放控制（右上角） */}
      <div
        className="absolute top-3 right-3 flex items-center gap-0.5 px-1.5 py-1"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--bg-elevated) 88%, transparent)',
          backdropFilter: 'blur(16px) saturate(180%)',
          WebkitBackdropFilter: 'blur(16px) saturate(180%)',
          border: '1px solid var(--border)',
          boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
        }}
      >
        {/* 回到总观 */}
        <button
          onClick={resetZoom}
          title="回到总观（适配全部节点）"
          className="w-7 h-7 flex items-center justify-center transition-colors hover:bg-black/5"
          style={{ color: 'var(--text-secondary)' }}
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
          </svg>
        </button>
        <span className="w-px h-4 mx-0.5" style={{ backgroundColor: 'var(--border)' }} />
        <button
          onClick={() => zoomBy(1 / ZOOM_STEP)}
          title="缩小"
          className="w-7 h-7 flex items-center justify-center text-base transition-colors hover:bg-black/5"
          style={{ color: 'var(--text-secondary)' }}
        >
          −
        </button>
        <button
          onClick={resetZoom}
          title="适配画布"
          className="px-2 h-7 text-xs transition-colors hover:bg-black/5 min-w-[3.5rem]"
          style={{ color: 'var(--text-secondary)' }}
        >
          {Math.round(zoomLevel * 100)}%
        </button>
        <button
          onClick={() => zoomBy(ZOOM_STEP)}
          title="放大"
          className="w-7 h-7 flex items-center justify-center text-base transition-colors hover:bg-black/5"
          style={{ color: 'var(--text-secondary)' }}
        >
          +
        </button>
      </div>

      {/* 图例 */}
      <div className="rs-legend">
        <span>
          <i className="rs-legend__dot rs-legend__dot--sm" />
          <i className="rs-legend__dot" />
          大小 = 影响力
        </span>
        <span>
          <i className="rs-legend__ramp" />
          颜色 = 研究子方向
        </span>
        <span><i className="rs-legend__ring" />扩展论文</span>
        <span><i className="rs-legend__line" style={{ background: EDGE_INK.cites }} />引用</span>
        <span><i className="rs-legend__line" style={{ background: EDGE_INK.related }} />关联</span>
      </div>
    </div>
  )
}
