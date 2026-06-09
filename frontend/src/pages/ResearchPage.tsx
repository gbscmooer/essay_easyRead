import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import type { ResearchRunCreate, ResearchGraph, GraphNode, GraphEdgeKind } from '@/types'
import { createRun, getGraph, getLatestRun, clearResearch } from '@/api/research'
import { useResearchStream } from '@/hooks/useResearchStream'
import { ControlPanel } from '@/features/research/ControlPanel'
import { ProgressPanel } from '@/features/research/ProgressPanel'
import { GraphCanvas } from '@/features/research/GraphCanvas'
import { NodeDetail } from '@/features/research/NodeDetail'
import { EdgeFilter } from '@/features/research/EdgeFilter'
import { RagQueryBox } from '@/features/research/RagQueryBox'
import { toast } from '@/hooks/useToast'

export function ResearchPage() {
  const { id } = useParams<{ id: string }>()
  const wsId = Number(id)
  const [runId, setRunId] = useState<number | null>(null)
  const [graph, setGraph] = useState<ResearchGraph | null>(null)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [edgeFilter, setEdgeFilter] = useState<Set<GraphEdgeKind>>(new Set(['cites', 'similar', 'shared-method']))
  const stream = useResearchStream(runId)

  // 1) 初始加载历史：图谱 + 最近 run
  useEffect(() => {
    getGraph(wsId).then(setGraph).catch(() => { toast({ type: 'error', message: '加载图谱失败' }) })
    getLatestRun(wsId).then(r => {
      if (r && r.status === 'running') setRunId(r.id)
    }).catch(() => { toast({ type: 'error', message: '加载研究记录失败' }) })
  }, [wsId])

  // 2) stream.graph 覆盖历史
  useEffect(() => {
    if (stream.graph) {
      setGraph({
        workspace_id: wsId,
        run_id: runId!,
        nodes: stream.graph.nodes,
        edges: stream.graph.edges,
        stats: stream.graph.stats as ResearchGraph['stats'],
        updated_at: new Date().toISOString(),
      })
    }
  }, [stream.graph, wsId, runId])

  const onStart = async (body: ResearchRunCreate) => {
    try {
      const run = await createRun(wsId, body)
      setRunId(run.id)
    } catch (e: any) {
      toast({ type: 'error', message: e.message || '启动失败' })
    }
  }

  const onClear = async () => {
    if (!confirm('确定清空该工作区的图谱和向量索引？这不会删除论文文件。')) return
    try {
      await clearResearch(wsId)
      setGraph(null)
      setRunId(null)
    } catch (e: any) {
      toast({ type: 'error', message: e.message || '清空失败' })
    }
  }

  return (
    <div className="flex flex-col h-full">
      <ControlPanel
        wsId={wsId}
        onStart={onStart}
        onClear={onClear}
        disabled={stream.status === 'running'}
      />

      {(stream.status === 'running' || stream.status === 'failed') && (
        <ProgressPanel
          stage={stream.stage}
          progressMessage={stream.progressMessage}
          embed={stream.embed}
          papersAdded={stream.papersAdded}
          error={stream.error}
        />
      )}

      {/* 边类型过滤 + 图谱 + RAG */}
      <div className="flex-1 flex gap-4 p-4 overflow-hidden">
        <div className="flex-1 flex flex-col gap-2 min-w-0">
          <div className="flex items-center justify-between">
            <EdgeFilter selected={edgeFilter} onChange={setEdgeFilter} />
            {graph && (
              <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                {graph.stats?.node_count ?? graph.nodes.length} 节点 / {graph.stats?.edge_count ?? graph.edges.length} 边
              </span>
            )}
          </div>
          <GraphCanvas
            graph={graph}
            edgeFilter={edgeFilter}
            onSelectNode={setSelectedNode}
          />
        </div>
        <RagQueryBox wsId={wsId} />
      </div>

      {/* 节点详情抽屉 */}
      <NodeDetail node={selectedNode} onClose={() => setSelectedNode(null)} />
    </div>
  )
}
