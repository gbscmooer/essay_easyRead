import { Link } from 'react-router-dom'
import { LoadingSpinner } from '@/components/LoadingSpinner'
import type { WorkspaceInsightResponse, WorkspaceInsightPaper, WorkspaceClusterSummary } from '@/api/workspaces'

interface WorkspaceInsightsProps {
  insights: WorkspaceInsightResponse | null
  loading: boolean
}

function StatTile({
  label,
  value,
  accent,
}: {
  label: string
  value: number | string
  accent?: string
}) {
  return (
    <div className="px-3 py-2 border" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{label}</div>
      <div className="mt-1 text-[16px] font-semibold tabular-nums" style={{ color: accent || 'var(--text-primary)' }}>
        {value}
      </div>
    </div>
  )
}

function PaperRow({ paper }: { paper: WorkspaceInsightPaper }) {
  return (
    <Link
      to={`/reader/${paper.paper_id}`}
      className="group block px-3 py-2.5 transition-colors"
      style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[13px] font-medium truncate" style={{ color: 'var(--text-primary)' }}>
            {paper.title}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]" style={{ color: 'var(--text-muted)' }}>
            {paper.year && <span>{paper.year}</span>}
            {paper.venue && <span>{paper.venue}</span>}
            <span>{paper.read_level === 'full' ? '已精读' : paper.read_level === 'digest' ? '已速读' : '未解读'}</span>
            {paper.cluster_label && <span>{paper.cluster_label}</span>}
          </div>
          {paper.reason && (
            <div className="mt-1 text-[11px] leading-relaxed break-words" style={{ color: 'var(--text-secondary)' }}>
              {paper.reason}
            </div>
          )}
        </div>
        <div className="shrink-0 text-right">
          <div className="text-[11px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
            {(paper.importance * 100).toFixed(0)}%
          </div>
          {paper.bridge_score > 0 && (
            <div className="text-[11px] tabular-nums mt-0.5" style={{ color: 'var(--warning)' }}>
              {(paper.bridge_score * 100).toFixed(0)}%
            </div>
          )}
        </div>
      </div>
    </Link>
  )
}

function ClusterChip({ cluster }: { cluster: WorkspaceClusterSummary }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px]"
      style={{ backgroundColor: 'var(--bg-surface)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
    >
      <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{cluster.label}</span>
      <span className="tabular-nums">· {cluster.paper_count}</span>
    </span>
  )
}

export function WorkspaceInsights({ insights, loading }: WorkspaceInsightsProps) {
  if (loading) {
    return (
      <section className="ws-block">
        <div className="flex items-center gap-3">
          <LoadingSpinner size="sm" />
          <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>正在生成洞察</span>
        </div>
      </section>
    )
  }

  if (!insights) return null

  const clusterLabel = insights.graph_available
    ? `图谱 · ${insights.cluster_count} 个子方向`
    : '基础洞察'

  const bridgeList = insights.graph_available && insights.bridge_papers.length > 0
    ? insights.bridge_papers
    : insights.top_papers

  return (
    <section className="ws-block">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <p className="er-eyebrow">洞察</p>
          <div className="text-[15px] font-medium mt-0.5" style={{ color: 'var(--text-primary)' }}>{clusterLabel}</div>
        </div>
        <span
          className="text-[11px] px-2 py-0.5"
          style={{
            backgroundColor: insights.graph_available ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
            color: insights.graph_available ? 'var(--accent)' : 'var(--text-muted)',
          }}
        >
          {insights.graph_available ? '已构建图谱' : '未构建图谱'}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <StatTile label="已精读" value={insights.read_counts.full} accent="var(--accent)" />
        <StatTile label="已速读" value={insights.read_counts.digest} accent="var(--success)" />
        <StatTile label="未解读" value={insights.read_counts.none} accent="var(--warning)" />
        <StatTile label="子方向" value={insights.cluster_count} accent="var(--text-primary)" />
      </div>

      {typeof insights.modularity === 'number' && (
        <div className="mt-3 text-[11px]" style={{ color: 'var(--text-muted)' }}>
          模块度 {insights.modularity.toFixed(3)}
        </div>
      )}

      {insights.clusters.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {insights.clusters.slice(0, 6).map(cluster => (
            <ClusterChip key={cluster.cluster} cluster={cluster} />
          ))}
        </div>
      )}

      <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-2">
        <div className="min-w-0">
          <div className="text-[12px] font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>建议优先阅读</div>
          <div className="space-y-2">
            {insights.next_reads.length > 0 ? insights.next_reads.map(paper => (
              <PaperRow key={paper.paper_id} paper={paper} />
            )) : (
              <div className="px-3 py-2 text-[12px]" style={{ border: '1px dashed var(--border)', color: 'var(--text-muted)' }}>
                暂无
              </div>
            )}
          </div>
        </div>

        <div className="min-w-0">
          <div className="text-[12px] font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
            {insights.graph_available ? '桥梁论文' : '关键论文'}
          </div>
          <div className="space-y-2">
            {bridgeList.length > 0 ? bridgeList.map(paper => (
              <PaperRow key={paper.paper_id} paper={paper} />
            )) : (
              <div className="px-3 py-2 text-[12px]" style={{ border: '1px dashed var(--border)', color: 'var(--text-muted)' }}>
                暂无
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
