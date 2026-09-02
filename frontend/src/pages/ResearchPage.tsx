import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import type { ResearchMode, ResearchRunCreate, ResearchGraph, GraphNode, GraphEdgeKind, GraphSynthesis } from '@/types'
import { ConfirmDialog, LoadingSpinner, Tooltip } from '@/ds-entry'
import { createRun, getGraph, getLatestRun, clearResearch } from '@/api/research'
import { get } from '@/api/client'
import type { AppConfig } from '@/features/settings/config'
import { useResearchStream } from '@/hooks/useResearchStream'
import { ControlPanel } from '@/features/research/ControlPanel'
import { ProgressPanel } from '@/features/research/ProgressPanel'
import { GraphCanvas, type SelectedEdge } from '@/features/research/GraphCanvas'
import { DomainOverview } from '@/features/research/DomainOverview'
import { NodeDetail } from '@/features/research/NodeDetail'
import { EdgeDetail } from '@/features/research/EdgeDetail'
import { BriefingPanel } from '@/features/research/BriefingPanel'
import { ResearchSettingsDrawer } from '@/features/research/ResearchSettingsDrawer'
import { EdgeFilter } from '@/features/research/EdgeFilter'
import { RagQueryBox } from '@/features/research/RagQueryBox'
import { toast } from '@/hooks/useToast'

const CHANNEL_LABEL: Record<string, string> = {
  topic: '主题', purpose: '研究目的', method: '方法', term: '术语',
  dataset: '数据集', baseline: '对比基线', bibcoupling: '文献耦合', cocitation: '共引',
}

/** 图谱质量摘要：地图密度工作点 + 本次是否按工作区自动调过权。 */
function graphQualityHint(graph: ResearchGraph): string {
  const st = graph.stats
  const lines: string[] = []
  if (st?.backbone) {
    lines.push(
      `多尺度骨架：从 ${st.backbone.candidate_pairs} 对候选里留下 ${Math.round(st.backbone.mean_degree * (st.node_count ?? graph.nodes.length) / 2)} 条关系边`
      + `（目标密度 ${st.backbone.map_density} 边/篇，实际 ${st.backbone.mean_degree.toFixed(1)}）`
      + (st.backbone.bridge_pairs ? `，其中 ${st.backbone.bridge_pairs} 条为保证地图连通补的桥接边` : ''),
    )
  }
  if (st?.modularity != null) lines.push(`子方向划分质量 modularity=${st.modularity.toFixed(3)}（越高说明子方向越分明）`)
  const cal = st?.relation_channels?.calibration
  if (cal?.applied) {
    const top = Object.entries(cal.weights).sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `${CHANNEL_LABEL[k] ?? k} ${(v * 100).toFixed(0)}%`).join(' · ')
    lines.push(`已按本工作区的 ${cal.n_positives} 对引用证据自动调权：${top}`)
  } else if (cal) {
    lines.push(`未自动调权：${cal.reason}`)
  }
  return lines.join('\n') || '图谱统计'
}

export default function ResearchPage() {
  const { id } = useParams<{ id: string }>()
  const wsId = Number(id)
  const [runId, setRunId] = useState<number | null>(null)
  const [runMode, setRunMode] = useState<ResearchMode | null>(null)
  const [autoDigest, setAutoDigest] = useState(true)
  const [ragPaperIds, setRagPaperIds] = useState<number[]>([])
  const [ragTopN, setRagTopN] = useState(5)
  const [graph, setGraph] = useState<ResearchGraph | null>(null)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<SelectedEdge | null>(null)
  const [edgeFilter, setEdgeFilter] = useState<Set<GraphEdgeKind>>(new Set(['cites', 'related', 'bibcoupling', 'cocitation']))
  const [linkDistance, setLinkDistance] = useState(50)
  const [showSettings, setShowSettings] = useState(false)
  const [showBriefing, setShowBriefing] = useState(false)
  // 领域总览（热力图/战略坐标/卡片）为默认首屏：节点-连线图在骨架密度下信息过载，
  // 入门者先读聚合视图，需要追溯具体关系时再切图谱
  const [view, setView] = useState<'overview' | 'graph'>('overview')
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
      if (r && r.status === 'running') {
        setRunId(r.id)
        setRunMode(r.mode)
      }
    }).catch(() => { toast({ type: 'error', message: '加载研究记录失败' }) })
    get<AppConfig>('/analysis/config').then(cfg => {
      setAutoDigest(cfg.research?.auto_digest ?? true)
      setRagTopN(cfg.reranker?.top_n ?? 5)
    }).catch(() => { /* 默认 true */ })
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

  // synthesis_ready 到达（或 done 回放）：把角色/因果语义合并进图谱节点与连边，
  // 图上出现角色徽章与因果配色，无需重建图谱。
  useEffect(() => {
    const s: GraphSynthesis | null | undefined = stream.synthesis ?? graph?.synthesis ?? null
    if (!s) return
    setGraph(prev => {
      if (!prev) return prev
      const en = s.enriched_nodes ?? {}
      const ee = s.enriched_edges ?? {}
      return {
        ...prev,
        nodes: prev.nodes.map(n => (en[n.id] ? { ...n, semantic: en[n.id] } : n)),
        edges: prev.edges.map(e => {
          const key = `${e.source}>${e.target}`
          return ee[key] ? { ...e, semantic: ee[key] } : e
        }),
        synthesis: s,
      }
    })
    // graph 故意不进依赖：只随 synthesis 事件合并一次，避免循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.synthesis])

  const onStart = async (body: ResearchRunCreate) => {
    try {
      const run = await createRun(wsId, body)
      setRunMode(body.mode)
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
      setRunMode(null)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '清空失败'
      toast({ type: 'error', message })
    }
  }

  return (
    <div className="er-page er-page--research flex flex-col h-full">
      <ControlPanel
        wsId={wsId}
        onStart={onStart}
        onClear={onClear}
        disabled={stream.status === 'running'}
        linkDistance={linkDistance}
        onLinkDistanceChange={setLinkDistance}
        onSelectedPaperIdsChange={setRagPaperIds}
      />

      {(stream.status === 'running' || stream.status === 'failed') && (
        <ProgressPanel
          stage={stream.stage}
          progressMessage={stream.progressMessage}
          embed={stream.embed}
          papersAdded={stream.papersAdded}
          recallStats={stream.recallStats}
          error={stream.error}
          mode={runMode}
          autoDigest={autoDigest}
        />
      )}

      {/* 图谱 + RAG 区域 */}
      <div className="rs-planes flex-1">
        <div className="rs-graph">
          <div className="rs-graph__bar">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="rs-viewtoggle" role="tablist" aria-label="视图切换">
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === 'overview'}
                  className={view === 'overview' ? 'is-active' : ''}
                  onClick={() => setView('overview')}
                >
                  📊 总览
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === 'graph'}
                  className={view === 'graph' ? 'is-active' : ''}
                  onClick={() => setView('graph')}
                >
                  🕸️ 图谱
                </button>
              </div>
              {view === 'graph' && <EdgeFilter selected={edgeFilter} onChange={setEdgeFilter} />}
            </div>
            <div className="flex items-center gap-2.5">
              {graph && (
                <Tooltip content={graphQualityHint(graph)}>
                  <span className="text-[11px] cursor-help" style={{ color: 'var(--text-muted)' }}>
                    {graph.stats?.node_count ?? graph.nodes.length} 节点 / {graph.stats?.edge_count ?? graph.edges.length} 边
                    {graph.stats?.backbone?.mean_degree != null && ` · 平均度 ${graph.stats.backbone.mean_degree.toFixed(1)}`}
                    {graph.stats?.clusters != null && ` · ${graph.stats.clusters} 子方向`}
                  </span>
                </Tooltip>
              )}
              {(graph?.synthesis || stream.synthesis) && (
                <button
                  type="button"
                  onClick={() => setShowBriefing(true)}
                  className="er-btn er-btn--secondary er-btn--sm"
                  title="3 分钟读懂领域全局：演进时间轴 / 技术流派 / 热点与公开挑战"
                >
                  📜 领域全景
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowSettings(true)}
                className="er-btn er-btn--secondary er-btn--sm"
              >
                研究设置
              </button>
            </div>
          </div>
          {graphLoading ? (
            <div className="flex-1 flex items-center justify-center">
              <LoadingSpinner size="lg" />
            </div>
          ) : view === 'overview' ? (
            <DomainOverview
              graph={graph}
              synthesis={stream.synthesis ?? graph?.synthesis ?? null}
              onSelectNode={(n) => { setSelectedNode(n); if (n) setSelectedEdge(null) }}
              onViewInGraph={(nodeId) => {
                setView('graph')
                const n = graph?.nodes.find(x => x.id === nodeId)
                if (n) { setSelectedNode(n); setSelectedEdge(null) }
              }}
            />
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
        <RagQueryBox
          wsId={wsId}
          paperIds={ragPaperIds.length > 0 ? ragPaperIds : undefined}
          topN={ragTopN}
        />
      </div>

      <NodeDetail node={selectedNode} onClose={() => setSelectedNode(null)} />
      <EdgeDetail edge={selectedEdge} onClose={() => setSelectedEdge(null)} />
      <ResearchSettingsDrawer open={showSettings} onClose={() => setShowSettings(false)} />
      <BriefingPanel
        open={showBriefing}
        onClose={() => setShowBriefing(false)}
        graph={graph}
        synthesis={stream.synthesis}
        onSelectNode={(nodeId) => {
          const n = graph?.nodes.find(x => x.id === nodeId) ?? null
          if (n) {
            setShowBriefing(false)
            setSelectedNode(n)
          }
        }}
      />

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
