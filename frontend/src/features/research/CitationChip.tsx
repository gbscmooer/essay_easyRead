import type { RagCitation } from '@/types'
import { Link } from 'react-router-dom'

export interface CitationChipProps {
  citation: RagCitation
  index: number
}

export function CitationChip({ citation, index }: CitationChipProps) {
  return (
    <Link
      to={`/reader/${citation.paper_id}`}
      className="block px-3.5 py-2.5 rounded-xl transition-all duration-200"
      style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--accent)' }}
      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)' }}
    >
      <div className="flex items-start gap-2">
        <span className="text-[11px] font-mono font-semibold shrink-0 mt-px tabular-nums" style={{ color: 'var(--accent)' }}>
          [{index}]
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[12px] font-medium truncate" style={{ color: 'var(--text-primary)' }}>
            {citation.paper_title}
          </p>
          <p className="text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>
            {citation.section_name} — {citation.snippet.slice(0, 100)}…
          </p>
        </div>
        <span className="text-[11px] font-mono shrink-0 tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {(citation.score * 100).toFixed(0)}%
        </span>
      </div>
    </Link>
  )
}
