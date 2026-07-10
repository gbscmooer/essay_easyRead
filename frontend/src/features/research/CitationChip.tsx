import type { RagCitation, RagCitationDebug } from '@/types'
import { Link } from 'react-router-dom'

const SOURCE_LABELS: Record<string, string> = {
  dense: '向量',
  bm25: 'BM25',
  graph_ppr: '图谱 PPR',
}

function formatDebugScores(debug: RagCitationDebug): string {
  const parts: string[] = []
  if (debug.fused_score != null) parts.push(`融合 ${debug.fused_score.toFixed(4)}`)
  if (debug.rerank_score != null) parts.push(`重排 ${debug.rerank_score.toFixed(4)}`)
  if (debug.graph_score != null) parts.push(`图谱 ${debug.graph_score.toFixed(4)}`)
  return parts.join(' · ')
}

function CitationDebugPanel({ debug }: { debug: RagCitationDebug }) {
  const hits = Object.entries(debug.source_hits ?? {})
  return (
    <div className="mt-2 pt-2 space-y-1" style={{ borderTop: '1px solid var(--border)' }}>
      {hits.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {hits.map(([ch, score]) => (
            <span
              key={ch}
              className="text-[10px] px-1.5 py-0.5 rounded font-mono tabular-nums"
              style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-muted)' }}
            >
              {SOURCE_LABELS[ch] ?? ch} {score.toFixed(3)}
            </span>
          ))}
        </div>
      )}
      <p className="text-[10px] font-mono tabular-nums" style={{ color: 'var(--text-muted)' }}>
        {formatDebugScores(debug)}
      </p>
      {debug.channel_ranks && Object.keys(debug.channel_ranks).length > 0 && (
        <p className="text-[10px] font-mono" style={{ color: 'var(--text-muted)' }}>
          排名 {Object.entries(debug.channel_ranks).map(([ch, r]) => `${SOURCE_LABELS[ch] ?? ch}#${r}`).join(' · ')}
        </p>
      )}
      {debug.chunk_id && (
        <p className="text-[10px] font-mono truncate" style={{ color: 'var(--text-disabled)' }}>
          chunk {debug.chunk_id}
        </p>
      )}
    </div>
  )
}

function DownloadBadge({ citation }: { citation: RagCitation }) {
  const metaOnly = citation.metadata_only
    ?? (citation.download_status != null && citation.download_status !== 'available')
  if (!metaOnly) return null
  const label = citation.download_status === 'failed'
    ? '下载失败'
    : citation.download_status === 'pending'
      ? '下载中'
      : '仅元数据'
  return (
    <span
      className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full"
      style={{
        color: 'var(--warning, #eab308)',
        border: '1px solid color-mix(in srgb, var(--warning, #eab308) 40%, transparent)',
      }}
    >
      {label}
    </span>
  )
}

export interface CitationChipProps {
  citation: RagCitation
  index: number
  showDebug?: boolean
  debug?: RagCitationDebug | null
}

export function CitationChip({ citation, index, showDebug = false, debug }: CitationChipProps) {
  const dbg = debug ?? citation.debug

  return (
    <Link
      to={`/reader/${citation.paper_id}`}
      className="focus-glow block px-3.5 py-2.5 rounded-xl transition-all duration-200 hover:-translate-y-px"
      style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
      onMouseEnter={e => { const t = e.currentTarget as HTMLElement; t.style.borderColor = 'var(--accent)'; t.style.boxShadow = '0 4px 14px -6px rgba(0,0,0,0.25)' }}
      onMouseLeave={e => { const t = e.currentTarget as HTMLElement; t.style.borderColor = 'var(--border)'; t.style.boxShadow = 'none' }}
    >
      <div className="flex items-start gap-2">
        <span className="text-[11px] font-mono font-semibold shrink-0 mt-px tabular-nums" style={{ color: 'var(--accent)' }}>
          [{index}]
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 min-w-0">
            <p className="text-[12px] font-medium truncate flex-1" style={{ color: 'var(--text-primary)' }}>
              {citation.paper_title}
            </p>
            <DownloadBadge citation={citation} />
          </div>
          <p className="text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>
            {citation.section_name} — {citation.snippet.slice(0, 100)}…
          </p>
          {showDebug && dbg && <CitationDebugPanel debug={dbg} />}
        </div>
        <span className="text-[11px] font-mono shrink-0 tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {(citation.score * 100).toFixed(0)}%
        </span>
      </div>
    </Link>
  )
}
