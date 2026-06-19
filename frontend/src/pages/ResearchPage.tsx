import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import type { ResearchRunCreate, ResearchGraph, GraphNode, GraphEdgeKind } from '@/types'
import { ConfirmDialog, LoadingSpinner } from '@/ds-entry'
import { createRun, getGraph, getLatestRun, clearResearch } from '@/api/research'
import { useResearchStream } from '@/hooks/useResearchStream'
import { ControlPanel } from '@/features/research/ControlPanel'
import { ProgressPanel } from '@/features/research/ProgressPanel'
import { GraphCanvas, type SelectedEdge } from '@/features/research/GraphCanvas'
import { NodeDetail } from '@/features/research/NodeDetail'
import { EdgeDetail } from '@/features/research/EdgeDetail'
import { ResearchSettingsDrawer } from '@/features/research/ResearchSettingsDrawer'
import { EdgeFilter } from '@/features/research/EdgeFilter'
import { RagQueryBox } from '@/features/research/RagQueryBox'
import { toast } from '@/hooks/useToast'

export default function ResearchPage() {
  const { id } = useParams<{ id: string }>()
  const wsId = Number(id)
  const [runId, setRunId] = useState<number | null>(null)
  const [graph, setGraph] = useState<ResearchGraph | null>(null)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<SelectedEdge | null>(null)
  const [edgeFilter, setEdgeFilter] = useState<Set<GraphEdgeKind>>(new Set(['cites', 'similar', 'shared-method']))
  const [linkDistance, setLinkDistance] = useState(50)
  const [showSettings, setShowSettings] = useState(false)
  const [graphLoading, setGraphLoading] = useState(true)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const stream = useResearchStream(runId)

  useEffect(() => {
    if (stream.status !== 'running') return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [stream.status])

  useEffect(() => {
    getGraph(wsId).then(setGraph).catch(() => { toast({ type: 'error', message: '加载图谱失败' }) }).finally(() => setGraphLoading(false))
    getLatestRun(wsId).then(r => {
      if (r && r.status === 'running') setRunId(r.id)
    }).catch(() => { toast({ type: 'error', message: '加载研究记录失败' }) })
  }, [wsId])

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
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '启动失败'
      toast({ type: 'error', message })
    }
  }

  const onClear = () => { setShowClearConfirm(true) }

  const confirmClear = async () => {
    setShowClearConfirm(false)
    try {
      await clearResearch(wsId)
      setGraph(null)
      setRunId(null)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '清空失败'
      toast({ type: 'error', message })
    }
  }

  return (
    <div className="flex flex-col h-full">
      <ControlPanel
        wsId={wsId}
        onStart={onStart}
        onClear={onClear}
        disabled={stream.status === 'running'}
        linkDistance={linkDistance}
        onLinkDistanceChange={setLinkDistance}
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

      {/* 图谱 + RAG 区域 */}
      <div className="flex-1 flex flex-col md:flex-row gap-4 p-5 overflow-hidden">
        <div className="flex-1 flex flex-col gap-2.5 min-w-0">
          <div className="flex items-center justify-between">
            <EdgeFilter selected={edgeFilter} onChange={setEdgeFilter} />
            <div className="flex items-center gap-2.5">
              {graph && (
                <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                  {graph.stats?.node_count ?? graph.nodes.length} 节点 / {graph.stats?.edge_count ?? graph.edges.length} 边
                </span>
              )}
              <button
                onClick={() => setShowSettings(true)}
                className="text-[12px] px-3 py-1 rounded-full transition-all duration-200"
                style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
              >
                研究设置
              </button>
            </div>
          </div>
          {graphLoading ? (
            <div className="flex-1 flex items-center justify-center">
              <LoadingSpinner size="lg" />
            </div>
          ) : (
            <GraphCanvas
              graph={graph}
              edgeFilter={edgeFilter}
              onSelectNode={(n) => { setSelectedNode(n); if (n) setSelectedEdge(null) }}
              onSelectEdge={(e) => { setSelectedEdge(e); if (e) setSelectedNode(null) }}
              linkDistance={linkDistance}
            />
          )}
        </div>
        <RagQueryBox wsId={wsId} />
      </div>

      <NodeDetail node={selectedNode} onClose={() => setSelectedNode(null)} />
      <EdgeDetail edge={selectedEdge} onClose={() => setSelectedEdge(null)} />
      <ResearchSettingsDrawer open={showSettings} onClose={() => setShowSettings(false)} />

      <ConfirmDialog
        open={showClearConfirm}
        title="确认清空"
        message="确定清空该工作区的图谱和向量索引？这不会删除论文文件。"
        variant="warning"
        confirmLabel="清空"
        onConfirm={confirmClear}
        onCancel={() => setShowClearConfirm(false)}
      />
    </div>
  )
}
