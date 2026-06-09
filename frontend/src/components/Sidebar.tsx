import { useState, useMemo } from 'react'
import type { Paper } from '@/types'


// Module-level stable style objects
const SIDEBAR_STYLE: React.CSSProperties = {
  backgroundColor: 'var(--bg-surface)',
  borderRight: '1px solid var(--border)',
}
const SEARCH_CONTAINER_STYLE: React.CSSProperties = { borderBottom: '1px solid var(--border)' }
const SEARCH_INPUT_STYLE: React.CSSProperties = {
  backgroundColor: 'var(--bg-surface)',
  border: '1px solid var(--border)',
  color: 'var(--text-primary)',
}
const ACTIVE_PAPER_STYLE: React.CSSProperties = {
  borderLeft: '2px solid var(--accent)',
  backgroundColor: 'var(--accent-subtle)',
}
const INACTIVE_PAPER_STYLE: React.CSSProperties = {
  borderLeft: '2px solid transparent',
  backgroundColor: 'transparent',
}

interface SidebarProps {
  papers: Paper[]
  activeId: number | null
  onSelect: (id: number) => void
  onDelete: (id: number) => void
}

function statusDotColor(status: Paper['status']): string {
  switch (status) {
    case 'done': return '#22c55e'
    case 'extracting':
    case 'analyzing': return '#eab308'
    case 'failed': return '#ef4444'
    default: return 'var(--text-muted)'
  }
}

export function Sidebar({ papers, activeId, onSelect, onDelete }: SidebarProps) {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    if (!search.trim()) return papers
    const q = search.toLowerCase()
    return papers.filter(p =>
      (p.title || p.filename).toLowerCase().includes(q)
    )
  }, [papers, search])

  return (
    <div className="flex flex-col w-64 h-full" style={SIDEBAR_STYLE}>
      <div className="p-3" style={SEARCH_CONTAINER_STYLE}>
        <input
          type="text"
          placeholder="搜索论文..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full px-3 py-1.5 text-sm rounded-md focus:outline-none"
          style={SEARCH_INPUT_STYLE}
        />
      </div>

      <div className="flex-1 overflow-auto">
        {filtered.length === 0 ? (
          <p className="text-sm text-center mt-8" style={{ color: 'var(--text-muted)' }}>暂无论文</p>
        ) : (
          filtered.map(paper => (
            <div
              key={paper.id}
              onClick={() => onSelect(paper.id)}
              className="group flex items-start gap-2 px-3 py-2.5 cursor-pointer transition-colors"
              style={activeId === paper.id ? ACTIVE_PAPER_STYLE : INACTIVE_PAPER_STYLE}
            >
              <span className="w-2 h-2 mt-1.5 rounded-full shrink-0" style={{ backgroundColor: statusDotColor(paper.status) }} />
              <div className="flex-1 min-w-0">
                <p className="text-sm truncate" style={{ color: 'var(--text-primary)' }}>
                  {paper.title || paper.filename}
                </p>
                <div className="flex items-center gap-2 mt-0.5">
                  {paper.venue && (
                    <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{paper.venue}</span>
                  )}
                  {paper.year && (
                    <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{paper.year}</span>
                  )}
                </div>
              </div>
              <button
                onClick={e => {
                  e.stopPropagation()
                  onDelete(paper.id)
                }}
                className="shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity text-xs"
                style={{ color: 'var(--text-muted)' }}
                title="删除"
              >
                &times;
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
