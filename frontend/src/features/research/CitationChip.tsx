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
