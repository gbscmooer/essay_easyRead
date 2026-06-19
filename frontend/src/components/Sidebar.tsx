/**
 * 论文侧边栏 — 搜索 + 论文列表
 *
 * 无边框搜索框 + 3px 强调色指示条 + 文字状态标记。
 */
import { useState, useMemo, memo } from 'react'
import type { Paper } from '@/types'
import { ConfirmDialog } from '@/ds-entry'

/* ============================================================
 * 模块级样式常量
 * ============================================================ */
const SIDEBAR_STYLE: React.CSSProperties = {
  backgroundColor: 'var(--bg-surface)',
  borderRight: '1px solid var(--border)',
}
const ACTIVE_PAPER_STYLE: React.CSSProperties = {
  borderLeft: '3px solid var(--accent)',
  backgroundColor: 'var(--accent-subtle)',
}
const INACTIVE_PAPER_STYLE: React.CSSProperties = {
  borderLeft: '3px solid transparent',
  backgroundColor: 'transparent',
}

interface SidebarProps {
  papers: Paper[]
  activeId: number | null
  onSelect: (id: number) => void
  onDelete: (id: number) => void
}

function statusLabel(status: Paper['status']): { text: string; color: string } {
  switch (status) {
    case 'done': return { text: '已完成', color: 'var(--success)' }
    case 'extracting': return { text: '提取中', color: 'var(--warning)' }
    case 'analyzing': return { text: '分析中', color: 'var(--warning)' }
    case 'failed': return { text: '失败', color: 'var(--error)' }
    default: return { text: '', color: 'var(--text-muted)' }
  }
}

export const Sidebar = memo(function Sidebar({ papers, activeId, onSelect, onDelete }: SidebarProps) {
  const [search, setSearch] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null)

  const filtered = useMemo(() => {
    if (!search.trim()) return papers
    const q = search.toLowerCase()
    return papers.filter(p =>
      (p.title || p.filename).toLowerCase().includes(q)
    )
  }, [papers, search])

  return (
    <div className="flex flex-col w-64 h-full" style={SIDEBAR_STYLE}>
      {/* 搜索框 — 无边框磨砂玻璃风格 */}
      <div className="px-3 pt-3 pb-2">
        <div
          className="flex items-center gap-2 px-3 py-2 rounded-lg transition-colors"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid transparent',
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round"
            style={{ color: 'var(--text-muted)', flexShrink: 0 }}
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            placeholder="搜索论文..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            data-shortcut-search
            className="flex-1 bg-transparent text-[13px] focus:outline-none"
            style={{
              color: 'var(--text-primary)',
              border: 'none',
            }}
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="shrink-0 p-0.5 rounded transition-colors"
              style={{ color: 'var(--text-muted)' }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* 论文列表 */}
      <div className="flex-1 overflow-auto">
        {filtered.length === 0 ? (
          <p className="text-[13px] text-center mt-8" style={{ color: 'var(--text-muted)' }}>
            {search.trim() ? '无匹配论文' : '暂无论文'}
          </p>
        ) : (
          filtered.map(paper => {
            const status = statusLabel(paper.status)
            const isActive = activeId === paper.id
            return (
              <div
                key={paper.id}
                role="button"
                tabIndex={0}
                onClick={() => onSelect(paper.id)}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(paper.id) } }}
                className="group flex items-start gap-2 px-3 py-2.5 cursor-pointer transition-all duration-200"
                style={{
                  ...(isActive ? ACTIVE_PAPER_STYLE : INACTIVE_PAPER_STYLE),
                  transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
                }}
              >
                <div className="flex-1 min-w-0 pl-1">
                  <p
                    className="text-[13px] truncate leading-snug"
                    style={{
                      color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                      fontWeight: isActive ? 500 : 400,
                    }}
                  >
                    {paper.title || paper.filename}
                  </p>
                  <div className="flex items-center gap-2 mt-1">
                    {status.text && (
                      <span
                        className="text-[11px]"
                        style={{ color: status.color }}
                      >
                        {status.text}
                      </span>
                    )}
                    {paper.venue && (
                      <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                        {paper.venue}
                      </span>
                    )}
                    {paper.year && (
                      <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                        {paper.year}
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={e => {
                    e.stopPropagation()
                    setDeleteTarget(paper.id)
                  }}
                  className="shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-1 rounded-md"
                  style={{ color: 'var(--text-muted)' }}
                  title="删除"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
              </div>
            )
          })
        )}
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="确认删除"
        message="确定删除这篇论文？删除后不可恢复。"
        variant="danger"
        onConfirm={() => { if (deleteTarget !== null) onDelete(deleteTarget); setDeleteTarget(null) }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
})
