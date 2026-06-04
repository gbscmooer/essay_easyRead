import { useState, useMemo } from 'react'
import type { Paper } from '@/types'


interface SidebarProps {
  papers: Paper[]
  activeId: number | null
  onSelect: (id: number) => void
  onDelete: (id: number) => void
}

function statusDotColor(status: Paper['status']): string {
  switch (status) {
    case 'done': return 'bg-green-500'
    case 'extracting':
    case 'analyzing': return 'bg-yellow-500'
    case 'failed': return 'bg-red-500'
    default: return 'bg-gray-500'
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
    <div className="flex flex-col w-64 h-full bg-gray-900 border-r border-gray-800">
      <div className="p-3 border-b border-gray-800">
        <input
          type="text"
          placeholder="搜索论文..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full px-3 py-1.5 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500"
        />
      </div>

      <div className="flex-1 overflow-auto">
        {filtered.length === 0 ? (
          <p className="text-gray-500 text-sm text-center mt-8">暂无论文</p>
        ) : (
          filtered.map(paper => (
            <div
              key={paper.id}
              onClick={() => onSelect(paper.id)}
              className={`group flex items-start gap-2 px-3 py-2.5 cursor-pointer transition-colors border-l-2 ${
                activeId === paper.id
                  ? 'bg-gray-800 border-blue-500'
                  : 'border-transparent hover:bg-gray-800/50'
              }`}
            >
              <span className={`w-2 h-2 mt-1.5 rounded-full shrink-0 ${statusDotColor(paper.status)}`} />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-gray-200 truncate">
                  {paper.title || paper.filename}
                </p>
                <div className="flex items-center gap-2 mt-0.5">
                  {paper.venue && (
                    <span className="text-xs text-gray-500">{paper.venue}</span>
                  )}
                  {paper.year && (
                    <span className="text-xs text-gray-500">{paper.year}</span>
                  )}
                </div>
              </div>
              <button
                onClick={e => {
                  e.stopPropagation()
                  onDelete(paper.id)
                }}
                className="text-gray-600 hover:text-red-400 text-xs shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity"
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
