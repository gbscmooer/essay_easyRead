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
              className="text-[11px] px-1.5 py-0.5 font-mono tabular-nums"
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
    <span className="er-tag er-tag--warn shrink-0">{label}</span>
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
      to={`/reader/${citation.paper_id}${citation.page_number ? `?page=${citation.page_number}` : ''}`}
      /* 用 .list-row 的左色条 hover 代替上浮 + 投影：硬边平面里没有「浮起来」
         这回事，而那段 JS 设的 boxShadow 本来就被全局阴影禁令吃掉了，从来没
         生效过。 */
      className="list-row focus-glow block px-3.5 py-2.5"
      style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
    >
      <div className="flex items-start gap-2">
        <span className="text-[11px] font-mono font-semibold shrink-0 mt-px tabular-nums" style={{ color: 'var(--accent)' }}>
          [P{index}]
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 min-w-0">
            <p className="text-[12px] font-medium truncate flex-1" style={{ color: 'var(--text-primary)' }}>
              {citation.paper_title}
            </p>
            <DownloadBadge citation={citation} />
          </div>
          <p className="text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>
            {citation.page_number ? `第 ${citation.page_number} 页 · ` : ''}{citation.section_name} — {citation.snippet.slice(0, 100)}…
          </p>
          {showDebug && dbg && <CitationDebugPanel debug={dbg} />}
        </div>
        <span className="text-[11px] font-mono shrink-0 tabular-nums" style={{ color: 'var(--text-muted)' }}>
          排序分 {citation.score.toFixed(4)}
        </span>
      </div>
    </Link>
  )
}
