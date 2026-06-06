import { useRef, useEffect, useMemo, useCallback } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import type { GraphNode, ResearchGraph, GraphEdgeKind } from '@/types'

interface GraphCanvasProps {
  graph: ResearchGraph | null
  edgeFilter: Set<GraphEdgeKind>
  onSelectNode: (node: GraphNode | null) => void
}

export function GraphCanvas({ graph, edgeFilter, onSelectNode }: GraphCanvasProps) {
  const fgRef = useRef<any>(null)

  const graphData = useMemo(() => {
    if (!graph) return { nodes: [], links: [] }
    const filteredEdges = graph.edges.filter(e => edgeFilter.has(e.kind as GraphEdgeKind))
    // 只保留有边的节点
    const connectedIds = new Set<string>()
    filteredEdges.forEach(e => {
      connectedIds.add(e.source)
      connectedIds.add(e.target)
    })
    // 如果没有任何边过滤，显示全部节点
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
    if (fgRef.current && graphData.nodes.length > 0) {
      fgRef.current.zoomToFit(400, 40)
    }
  }, [graphData.nodes.length])

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
    <div className="flex-1 rounded-xl border overflow-hidden relative" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-base)' }}>
      <ForceGraph2D
        ref={fgRef}
        graphData={graphData}
        width={undefined}
        height={undefined}
        nodeColor={nodeColor}
        nodeRelSize={6}
        nodeLabel={(node: any) => `<b>${node.name}</b><br/><small>${node.kind}</small>`}
        linkColor={linkColor}
        linkDirectionalArrowLength={4}
        linkDirectionalArrowRelPos={0.9}
        linkWidth={(link: any) => Math.max(1, link.weight * 3)}
        linkLabel={(link: any) => `${link.kind} (${link.weight.toFixed(2)})`}
        onNodeClick={handleNodeClick}
        cooldownTicks={100}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
      />
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
