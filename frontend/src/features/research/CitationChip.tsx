import type { RagCitation } from '@/types'
import { Link } from 'react-router-dom'

interface CitationChipProps {
  citation: RagCitation
  index: number
}

export function CitationChip({ citation, index }: CitationChipProps) {
  return (
    <Link
      to={`/reader/${citation.paper_id}`}
      className="block px-3 py-2 rounded-lg border transition-colors hover:opacity-80"
      style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-base)' }}
    >
      <div className="flex items-start gap-2">
        <span className="text-[10px] font-mono font-bold shrink-0 mt-0.5" style={{ color: 'var(--accent)' }}>
          [{index}]
        </span>
        <div className="min-w-0">
          <p className="text-[11px] font-medium truncate" style={{ color: 'var(--text-primary)' }}>
            {citation.paper_title}
          </p>
          <p className="text-[10px] truncate" style={{ color: 'var(--text-muted)' }}>
            {citation.section_name} — {citation.snippet.slice(0, 100)}...
          </p>
        </div>
        <span className="text-[10px] font-mono shrink-0" style={{ color: 'var(--text-muted)' }}>
          {(citation.score * 100).toFixed(0)}%
        </span>
      </div>
    </Link>
  )
}
