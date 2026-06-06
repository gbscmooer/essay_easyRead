import { useRef, useEffect, useMemo, useCallback, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import type { GraphNode, ResearchGraph, GraphEdgeKind } from '@/types'

interface GraphCanvasProps {
  graph: ResearchGraph | null
  edgeFilter: Set<GraphEdgeKind>
  onSelectNode: (node: GraphNode | null) => void
}

const ZOOM_MIN = 0.1
const ZOOM_MAX = 8
const ZOOM_STEP = 1.4

export function GraphCanvas({ graph, edgeFilter, onSelectNode }: GraphCanvasProps) {
  const fgRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [zoomLevel, setZoomLevel] = useState(1)

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

  const graphData = useMemo(() => {
    if (!graph) return { nodes: [], links: [] }
    const filteredEdges = graph.edges.filter(e => edgeFilter.has(e.kind as GraphEdgeKind))
    const connectedIds = new Set<string>()
    filteredEdges.forEach(e => {
      connectedIds.add(e.source)
      connectedIds.add(e.target)
    })
    const visibleNodes = filteredEdges.length > 0
      ? graph.nodes.filter(n => connectedIds.has(n.id))
      : graph.nodes

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

  const nodeColor = useCallback((node: any) => {
    if (node.kind === 'paper') {
      return node.meta?.source === 'expanded' ? '#f59e0b' : '#3b82f6'
    }
    if (node.kind === 'method') return '#10b981'
    if (node.kind === 'dataset') return '#8b5cf6'
    return '#6b7280'
  }, [])

  const linkColor = useCallback((link: any) => {
    if (link.kind === 'cites') return '#ef4444'
    if (link.kind === 'similar') return '#3b82f6'
    if (link.kind === 'shared-method') return '#10b981'
    if (link.kind === 'uses-dataset') return '#8b5cf6'
    return '#4b5563'
  }, [])

  const handleNodeClick = useCallback((node: any) => {
    if (node._raw) {
      onSelectNode(node._raw)
    }
  }, [onSelectNode])

  // 节点绘制：圆点 + 文字标签
  const drawNode = useCallback((node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const label = node.name || ''
    const r = 6
    ctx.beginPath()
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
    ctx.fillStyle = nodeColor(node)
    ctx.fill()
    ctx.strokeStyle = 'rgba(255,255,255,0.85)'
    ctx.lineWidth = 1.5 / globalScale
    ctx.stroke()

    // 文字（缩放过小时隐藏，避免过密）
    if (globalScale > 0.6) {
      const fontSize = 12 / globalScale
      ctx.font = `${fontSize}px ui-sans-serif, system-ui, -apple-system, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillStyle = 'rgba(40,40,40,0.92)'
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

  // 兜底：监听 ForceGraph2D 内部的 zoom 事件同步本地状态（用于按钮上 % 显示）
  useEffect(() => {
    const t = setInterval(() => {
      const z = fgRef.current?.zoom?.()
      if (typeof z === 'number' && Math.abs(z - zoomLevel) > 0.01) {
        setZoomLevel(z)
      }
    }, 300)
    return () => clearInterval(t)
  }, [zoomLevel])

  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center rounded-xl border" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-base)' }}>
        <div className="text-center">
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>暂无图谱</p>
          <p className="text-xs mt-1" style={{ color: 'var(--text-muted)', opacity: 0.6 }}>点击"开始研究"构建知识图谱</p>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={setContainer}
      className="flex-1 rounded-xl border overflow-hidden relative min-h-[300px]"
      style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-base)' }}
    >
      {size.w > 0 && size.h > 0 && (
        <ForceGraph2D
          ref={fgRef}
          graphData={graphData}
          width={size.w}
          height={size.h}
          nodeColor={nodeColor}
          nodeRelSize={6}
          nodeLabel={(node: any) => `<b>${node.name}</b><br/><small>${node.kind}</small>`}
          nodeCanvasObject={drawNode}
          nodeCanvasObjectMode={() => 'replace'}
          linkColor={linkColor}
          linkDirectionalArrowLength={4}
          linkDirectionalArrowRelPos={0.9}
          linkWidth={(link: any) => Math.max(1, link.weight * 3)}
          linkLabel={(link: any) => `${link.kind} (${link.weight.toFixed(2)})`}
          onNodeClick={handleNodeClick}
          onZoomEnd={(t: any) => setZoomLevel(t.k)}
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
        className="absolute top-3 right-3 flex items-center gap-1 rounded-lg border px-1.5 py-1 shadow-sm"
        style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)' }}
      >
        <button
          onClick={() => zoomBy(1 / ZOOM_STEP)}
          title="缩小"
          className="w-7 h-7 flex items-center justify-center rounded text-base transition-colors hover:bg-black/5"
          style={{ color: 'var(--text-secondary)' }}
        >
          −
        </button>
        <button
          onClick={resetZoom}
          title="适配画布"
          className="px-2 h-7 text-xs rounded transition-colors hover:bg-black/5 min-w-[3.5rem]"
          style={{ color: 'var(--text-secondary)' }}
        >
          {Math.round(zoomLevel * 100)}%
        </button>
        <button
          onClick={() => zoomBy(ZOOM_STEP)}
          title="放大"
          className="w-7 h-7 flex items-center justify-center rounded text-base transition-colors hover:bg-black/5"
          style={{ color: 'var(--text-secondary)' }}
        >
          +
        </button>
      </div>

      {/* 图例 */}
      <div className="absolute bottom-3 left-3 flex flex-wrap gap-2 text-[10px]" style={{ color: 'var(--text-muted)' }}>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-500 inline-block" />论文(上传)</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-500 inline-block" />论文(扩展)</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500 inline-block" />引用</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-500 inline-block" />相似</span>
      </div>
    </div>
  )
}
