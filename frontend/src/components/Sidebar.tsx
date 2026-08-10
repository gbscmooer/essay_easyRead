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
const SEED_RADIUS = 'var(--seed-radius, 12px)'

const SIDEBAR_STYLE: React.CSSProperties = {
  backgroundColor: 'var(--bg-surface)',
  borderRight: '1px solid var(--border)',
}

/* ============================================================
 * 状态徽标 — 文字 + 语义色 + 小图标
 * ============================================================ */
type StatusIconKind = 'done' | 'pending' | 'failed' | null

function statusLabel(status: Paper['status']): { text: string; color: string; icon: StatusIconKind } {
  switch (status) {
    case 'done': return { text: '已完成', color: 'var(--success)', icon: 'done' }
    case 'extracting': return { text: '提取中', color: 'var(--warning)', icon: 'pending' }
    case 'analyzing': return { text: '分析中', color: 'var(--warning)', icon: 'pending' }
    case 'failed': return { text: '失败', color: 'var(--error)', icon: 'failed' }
    default: return { text: '', color: 'var(--text-muted)', icon: null }
  }
}

function StatusIcon({ kind }: { kind: StatusIconKind }) {
  if (!kind) return null
  const common = {
    width: 11,
    height: 11,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2.5,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
    style: { flexShrink: 0 },
  }
  if (kind === 'done') {
    return <svg {...common}><polyline points="20 6 9 17 4 12" /></svg>
  }
  if (kind === 'failed') {
    return <svg {...common}><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
  }
  // pending — 时钟
  return (
    <svg {...common} strokeWidth={2}>
      <circle cx="12" cy="12" r="9" />
      <polyline points="12 7 12 12 15 14" />
    </svg>
  )
}

interface SidebarProps {
  papers: Paper[]
  activeId: number | null
  onSelect: (id: number) => void
  onDelete: (id: number) => void
}

/* ============================================================
 * 论文列表项 — hover 背景 + active 强调条 + 键盘焦点环
 * ============================================================ */
interface PaperItemProps {
  paper: Paper
  isActive: boolean
  onSelect: (id: number) => void
  onRequestDelete: (id: number) => void
}

function PaperItem({ paper, isActive, onSelect, onRequestDelete }: PaperItemProps) {
  const [hover, setHover] = useState(false)
  const status = statusLabel(paper.status)

  const bg = isActive
    ? 'var(--accent-subtle)'
    : hover
      ? 'var(--bg-elevated)'
      : 'transparent'

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(paper.id)}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(paper.id) } }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-current={isActive ? 'true' : undefined}
      className="focus-glow group flex items-start gap-2 px-3 py-2.5 mx-2 my-0.5 cursor-pointer transition-all duration-200"
      style={{
        borderLeft: `3px solid ${isActive ? 'var(--accent)' : 'transparent'}`,
        backgroundColor: bg,
        borderRadius: SEED_RADIUS,
        transitionTimingFunction: 'var(--ease-press)',
      }}
    >
      <div className="flex-1 min-w-0 pl-1">
        <p
          className="text-[13px] truncate leading-snug transition-colors"
          style={{
            color: isActive || hover ? 'var(--text-primary)' : 'var(--text-secondary)',
            fontWeight: isActive ? 500 : 400,
          }}
        >
          {paper.title || paper.filename}
        </p>
        <div className="flex items-center gap-2 mt-1">
          {status.text && (
            <span
              className="inline-flex items-center gap-1 text-[11px]"
              style={{ color: status.color }}
            >
              <StatusIcon kind={status.icon} />
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
          onRequestDelete(paper.id)
        }}
        aria-label="删除论文"
        className="focus-glow shrink-0 mt-0.5 p-1 transition-all press opacity-0 group-hover:opacity-60 hover:!opacity-100 focus-visible:opacity-100"
        style={{ color: 'var(--text-muted)' }}
        title="删除"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  )
}

export const Sidebar = memo(function Sidebar({ papers, activeId, onSelect, onDelete }: SidebarProps) {
  const [search, setSearch] = useState('')
  const [searchFocused, setSearchFocused] = useState(false)
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
          className="flex items-center gap-2 px-3 py-2 transition-all duration-200"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            border: `1px solid ${searchFocused ? 'var(--accent)' : 'transparent'}`,
            borderRadius: SEED_RADIUS,
            boxShadow: searchFocused ? '0 0 0 3px var(--accent-subtle)' : 'none',
            transitionTimingFunction: 'var(--ease-press)',
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round"
            aria-hidden="true"
            style={{ color: searchFocused ? 'var(--accent)' : 'var(--text-muted)', flexShrink: 0, transition: 'color 0.2s' }}
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            placeholder="搜索论文..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            onFocus={() => setSearchFocused(true)}
            onBlur={() => setSearchFocused(false)}
            aria-label="搜索论文"
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
              aria-label="清除搜索"
              title="清除搜索"
              className="focus-glow shrink-0 p-0.5 transition-colors press hover:!opacity-100"
              style={{ color: 'var(--text-muted)' }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* 论文列表 */}
      <div className="flex-1 overflow-auto py-1">
        {filtered.length === 0 ? (
          <p className="text-[13px] text-center mt-8 animate-reveal-fade" style={{ color: 'var(--text-muted)' }}>
            {search.trim() ? '无匹配论文' : '暂无论文'}
          </p>
        ) : (
          filtered.map(paper => (
            <PaperItem
              key={paper.id}
              paper={paper}
              isActive={activeId === paper.id}
              onSelect={onSelect}
              onRequestDelete={setDeleteTarget}
            />
          ))
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
