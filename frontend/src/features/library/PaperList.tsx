/**
 * PaperList 组件
 *
 * 论文库列表，清新学术风格，支持筛选和搜索。
 */

import { useState, useMemo, memo } from 'react'
import type { Paper, SearchResult } from '@/types'
import { ConfirmDialog, Tooltip, SkeletonCard } from '@/ds-entry'


interface FilterParams {
  category?: string
  year?: number
  dateAdded?: string   // "YYYY-MM-DD"
}

/** ISO 日期（YYYY-MM-DD）→ 本地展示（YYYY/M/D） */
function formatDate(iso: string): string {
  const [y, m, d] = iso.split('-')
  return `${y}/${Number(m)}/${Number(d)}`
}

// 命中字段 → 中文标签（智能搜索结果用）
const FIELD_LABEL: Record<string, string> = {
  title: '标题',
  filename: '文件名',
  authors: '作者',
  venue: '刊物',
  year: '年份',
  one_sentence: '一句话',
  problem: '问题',
  limitations: '局限',
  method: '方法',
  experiments: '实验',
  fulltext: '正文',
}

interface PaperListProps {
  papers: Paper[]
  total: number
  onSelect: (id: number) => void
  onDelete: (id: number) => void
  onSearch: (query: string) => void
  onFilter: (filters: FilterParams) => void
  onPageChange: (page: number) => void
  page: number
  perPage: number
  // 智能搜索
  globalSearch: boolean
  onToggleGlobal: (v: boolean) => void
  includeExpanded: boolean
  onToggleExpanded: (v: boolean) => void
  searchResults: SearchResult[] | null   // null = 浏览模式；非 null = 搜索结果模式
  searchLoading: boolean
  searchQuery: string  // 当前搜索关键词，用于高亮
}

/** 高亮搜索关键词 */
function HighlightText({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = text.split(new RegExp(`(${escaped})`, 'gi'))
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === query.toLowerCase()
          ? <mark key={i} style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)', borderRadius: '4px', padding: '0.5px 3px', fontWeight: 600, boxShadow: '0 0 0 1px var(--accent-subtle)' }}>{part}</mark>
          : <span key={i}>{part}</span>
      )}
    </>
  )
}

type StatusIcon = 'check' | 'cross' | 'clock' | 'spin'

function statusLabel(status: Paper['status']): { text: string; bgColor: string; textColor: string; icon: StatusIcon } {
  switch (status) {
    case 'done': return { text: '已完成', bgColor: 'var(--success-subtle)', textColor: 'var(--success)', icon: 'check' }
    case 'extracting': return { text: '提取中', bgColor: 'var(--warning-subtle)', textColor: 'var(--warning)', icon: 'spin' }
    case 'analyzing': return { text: '分析中', bgColor: 'var(--warning-subtle)', textColor: 'var(--warning)', icon: 'spin' }
    case 'pending': return { text: '等待中', bgColor: 'var(--bg-surface)', textColor: 'var(--text-muted)', icon: 'clock' }
    case 'failed': return { text: '失败', bgColor: 'var(--error-subtle)', textColor: 'var(--error)', icon: 'cross' }
    default: return { text: status, bgColor: 'var(--bg-surface)', textColor: 'var(--text-muted)', icon: 'clock' }
  }
}

/** 状态徽标小图标 — 语义色由父级 currentColor 继承 */
function StatusBadgeIcon({ icon }: { icon: StatusIcon }) {
  const cls = 'w-3 h-3 shrink-0'
  if (icon === 'spin') {
    return (
      <svg className={`${cls} animate-spin`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} aria-hidden="true">
        <path strokeLinecap="round" d="M12 3a9 9 0 1 0 9 9" />
      </svg>
    )
  }
  if (icon === 'cross') {
    return (
      <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 6l12 12M18 6L6 18" />
      </svg>
    )
  }
  if (icon === 'clock') {
    return (
      <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 7v5l3 2M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18z" />
      </svg>
    )
  }
  return (
    <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
    </svg>
  )
}

export const PaperList = memo(function PaperList({
  papers,
  total,
  onSelect,
  onDelete,
  onSearch,
  onFilter,
  onPageChange,
  page,
  perPage,
  globalSearch,
  onToggleGlobal,
  includeExpanded,
  onToggleExpanded,
  searchResults,
  searchLoading,
  searchQuery,
}: PaperListProps) {
  const [searchInput, setSearchInput] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [yearFilter, setYearFilter] = useState('')
  const [dateFilter, setDateFilter] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null)

  // 全部方向：GROBID 提取的 arXiv 学科分类（如 cs.LG）
  const categories = useMemo(() => {
    const set = new Set(papers.map(p => p.category).filter(Boolean))
    return Array.from(set).sort() as string[]
  }, [papers])

  // 全部年份：仅保留合理年份（过滤脏年份如 20、2）
  const years = useMemo(() => {
    const set = new Set(papers.map(p => p.year).filter((y): y is number => !!y && y >= 1900))
    return Array.from(set).sort((a, b) => b - a)
  }, [papers])

  // 全部添加日期：按加入论文库的日期（取 created_at 的日期部分，UTC）
  const dates = useMemo(() => {
    const set = new Set(papers.map(p => p.created_at.slice(0, 10)).filter(Boolean))
    return Array.from(set).sort().reverse()
  }, [papers])

  const totalPages = Math.ceil(total / perPage)

  const handleSearch = () => {
    onSearch(searchInput)
  }

  const applyFilter = (next: { category?: string; year?: string; date?: string }) => {
    const category = next.category ?? categoryFilter
    const year = next.year ?? yearFilter
    const date = next.date ?? dateFilter
    onFilter({
      category: category || undefined,
      year: year ? Number(year) : undefined,
      dateAdded: date || undefined,
    })
  }

  const handleFilterCategory = (category: string) => {
    setCategoryFilter(category)
    applyFilter({ category })
  }

  const handleFilterYear = (year: string) => {
    setYearFilter(year)
    applyFilter({ year })
  }

  const handleFilterDate = (date: string) => {
    setDateFilter(date)
    applyFilter({ date })
  }

  return (
    <div className="flex flex-col h-full">
      {/* 搜索 + 筛选栏 */}
      <div className="sticky top-0 z-10 p-4" style={{ backgroundColor: 'var(--bg-base)', borderBottom: '1px solid var(--border)' }}>
        <div className="flex flex-wrap gap-3">
          {/* 智能搜索框 */}
          <div className="flex-1 min-w-[200px]">
            <div className="relative">
              <input
                type="text"
                placeholder="智能搜索（标题 / 作者 / 刊物 / 解读内容）..."
                value={searchInput}
                onChange={e => setSearchInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSearch()}
                aria-label="搜索论文"
                className="w-full px-4 py-2 pl-10 text-sm rounded-lg focus:outline-none focus-glow transition-shadow"
              />
              <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} style={{ color: 'var(--text-muted)' }}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
          </div>

          {/* 全局搜索开关：对论文正文全文做字面匹配 */}
          <Tooltip
            content="开启后，搜索词会额外在每篇论文的 PDF 正文全文中做字面匹配，而不仅限于标题、作者和解读内容。适用于在论文正文中查找特定术语或关键词的场景。"
            delay={1000}
            placement="bottom"
          >
            <label
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg cursor-pointer select-none transition-all duration-200"
              style={{
                color: globalSearch ? 'var(--accent)' : 'var(--text-muted)',
                border: `1px solid ${globalSearch ? 'var(--accent)' : 'var(--border)'}`,
                backgroundColor: globalSearch ? 'var(--accent-subtle)' : 'transparent',
                transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
              }}
            >
              <input
                type="checkbox"
                checked={globalSearch}
                onChange={e => onToggleGlobal(e.target.checked)}
                className="accent-blue-500"
                style={{ accentColor: 'var(--accent)' }}
              />
              全局搜索
            </label>
          </Tooltip>

          {/* 联网扩展论文开关：是否一并搜索深度研究下载的相关论文 */}
          <label
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg cursor-pointer select-none transition-all duration-200"
            style={{
              color: includeExpanded ? 'var(--accent)' : 'var(--text-muted)',
              border: `1px solid ${includeExpanded ? 'var(--accent)' : 'var(--border)'}`,
              backgroundColor: includeExpanded ? 'var(--accent-subtle)' : 'transparent',
              transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
            }}
            title="开启后，搜索范围一并包含深度研究通过联网下载的相关论文（L2+），默认只搜已加入论文库的论文"
          >
            <input
              type="checkbox"
              checked={includeExpanded}
              onChange={e => onToggleExpanded(e.target.checked)}
              className="accent-blue-500"
              style={{ accentColor: 'var(--accent)' }}
            />
            含联网扩展论文
          </label>

          {/* 方向筛选（arXiv 学科分类，GROBID 提取） */}
          <select
            value={categoryFilter}
            onChange={e => handleFilterCategory(e.target.value)}
            aria-label="按方向筛选"
            className="px-3 py-2 text-sm rounded-lg focus:outline-none focus-glow transition-shadow cursor-pointer"
          >
            <option value="">全部方向</option>
            {categories.map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>

          {/* 年份筛选 */}
          <select
            value={yearFilter}
            onChange={e => handleFilterYear(e.target.value)}
            aria-label="按年份筛选"
            className="px-3 py-2 text-sm rounded-lg focus:outline-none focus-glow transition-shadow cursor-pointer"
          >
            <option value="">全部年份</option>
            {years.map(y => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>

          {/* 添加日期筛选（加入论文库的日期） */}
          <select
            value={dateFilter}
            onChange={e => handleFilterDate(e.target.value)}
            className="px-3 py-2 text-sm rounded-lg focus:outline-none focus-glow transition-shadow cursor-pointer"
          >
            <option value="">全部添加日期</option>
            {dates.map(d => (
              <option key={d} value={d}>{formatDate(d)}</option>
            ))}
          </select>
        </div>
      </div>

      {/* 论文列表 / 智能搜索结果 */}
      <div className="flex-1 overflow-auto p-4 space-y-2">
        {searchResults !== null ? (
          searchLoading ? (
            <div className="space-y-2" aria-busy="true" aria-label="搜索中">
              {Array.from({ length: 4 }).map((_, i) => (
                <SkeletonCard key={i} style={{ animationDelay: `${i * 60}ms` }} />
              ))}
            </div>
          ) : searchResults.length === 0 ? (
            <div className="flex flex-col items-center text-center py-16 animate-reveal-fade">
              <svg className="w-10 h-10 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} style={{ color: 'var(--text-muted)', opacity: 0.6 }} aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <p className="text-lg mb-2" style={{ color: 'var(--text-secondary)' }}>没有匹配的论文</p>
              <p className="text-sm" style={{ color: 'var(--text-muted)' }}>换个关键词，或开启「全局搜索」匹配正文全文</p>
            </div>
          ) : (
            searchResults.map(r => (
              <div
                key={r.paper_id}
                onClick={() => onSelect(r.paper_id)}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(r.paper_id) } }}
                role="button"
                tabIndex={0}
                className="list-row card-hover press focus-glow"
              >
                <h3 className="text-sm font-medium mb-1 flex items-center gap-2" style={{ color: 'var(--text-primary)' }}>
                  <span className="truncate">
                    <HighlightText text={r.title} query={searchQuery} />
                  </span>
                  {r.source === 'expanded' && (
                    <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full" style={{ backgroundColor: 'var(--warning-subtle)', color: 'var(--warning)' }}>联网扩展</span>
                  )}
                  {r.source === 'uploaded' && (
                    <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full" style={{ backgroundColor: 'var(--success-subtle)', color: 'var(--success)' }}>本地论文</span>
                  )}
                </h3>
                <div className="flex items-center gap-3 text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
                  {r.authors && <span className="truncate max-w-[40%]"><HighlightText text={r.authors} query={searchQuery} /></span>}
                  {r.venue && <span><HighlightText text={r.venue} query={searchQuery} /></span>}
                  {r.year && <span>{r.year}</span>}
                </div>
                <div className="flex flex-wrap gap-1.5 mb-1.5">
                  {r.matched_fields.map((m, i) => (
                    <span key={i} className="text-[10px] px-1.5 py-0.5 rounded" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>
                      {FIELD_LABEL[m.field] ?? m.field}
                      {m.field === 'fulltext' && m.line && (
                        <span style={{ opacity: 0.7 }}> · 第{m.line}行</span>
                      )}
                    </span>
                  ))}
                </div>
                {r.matched_fields.slice(0, 3).map((m, i) => (
                  <p key={i} className="text-xs leading-relaxed line-clamp-2" style={{ color: 'var(--text-secondary)' }}>
                    <span style={{ color: 'var(--text-muted)' }}>
                      {FIELD_LABEL[m.field] ?? m.field}
                      {m.field === 'fulltext' && m.line && <> · 第{m.line}行</>}
                      ：
                    </span>
                    <HighlightText text={m.snippet} query={searchQuery} />
                  </p>
                ))}
              </div>
            ))
          )
        ) : papers.length === 0 ? (
          <div className="flex flex-col items-center text-center py-16 animate-reveal-fade">
            <svg className="w-10 h-10 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} style={{ color: 'var(--text-muted)', opacity: 0.6 }} aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
            <p className="text-lg mb-2" style={{ color: 'var(--text-secondary)' }}>暂无论文</p>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>上传 PDF 开始精读</p>
          </div>
        ) : (
          papers.map(paper => {
            const { text: statusText, bgColor: statusBg, textColor: statusColor, icon: statusIcon } = statusLabel(paper.status)
            return (
              <div
                key={paper.id}
                onClick={() => onSelect(paper.id)}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(paper.id) } }}
                role="button"
                tabIndex={0}
                className="flex items-center justify-between list-row card-hover press focus-glow group"
              >
                <div className="flex-1 min-w-0 mr-4">
                  <h3 className="text-sm font-medium truncate mb-1" style={{ color: 'var(--text-primary)' }}>
                    {paper.title || paper.filename}
                  </h3>
                  <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--text-muted)' }}>
                    {paper.venue && <span>{paper.venue}</span>}
                    {paper.year && <span>{paper.year}</span>}
                    {paper.category && (
                      <span className="px-1.5 py-0.5 rounded" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>
                        {paper.category}
                      </span>
                    )}
                    <span>{new Date(paper.created_at).toLocaleDateString('zh-CN')}</span>
                  </div>
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  {paper.read_level === 'full' && (
                    <span className="px-2 py-0.5 text-[10px] rounded-full" style={{ color: '#6366f1', border: '1px solid #6366f1' }}>
                      已精读
                    </span>
                  )}
                  {paper.read_level === 'digest' && (
                    <span className="px-2 py-0.5 text-[10px] rounded-full" style={{ color: '#10b981', border: '1px solid #10b981' }}>
                      已速读
                    </span>
                  )}
                  <span
                    className="inline-flex items-center gap-1 px-2.5 py-0.5 text-xs rounded-full"
                    style={{ backgroundColor: statusBg, color: statusColor }}
                  >
                    <StatusBadgeIcon icon={statusIcon} />
                    {statusText}
                  </span>
                  <button
                    onClick={e => {
                      e.stopPropagation()
                      setDeleteTarget(paper.id)
                    }}
                    aria-label="删除论文"
                    className="opacity-40 group-hover:opacity-100 hover:!opacity-100 focus-visible:opacity-100 press focus-glow rounded-md px-1.5 py-0.5 text-sm transition-all duration-200"
                    style={{ color: 'var(--text-muted)', transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)' }}
                    title="删除"
                    onMouseEnter={e => { e.currentTarget.style.color = 'var(--error)' }}
                    onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)' }}
                  >
                    删除
                  </button>
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* 分页控件 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 p-4" style={{ borderTop: '1px solid var(--border)' }}>
          <button
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
            aria-label="上一页"
            className="px-3 py-1.5 text-sm rounded-lg disabled:opacity-30 disabled:cursor-not-allowed press focus-glow transition-all duration-200"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)' }}
            onMouseEnter={e => { if (page > 1) e.currentTarget.style.borderColor = 'var(--border-light)' }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
          >
            上一页
          </button>
          <div className="flex flex-col items-center leading-tight">
            <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>
              <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{page}</span> / {totalPages}
            </span>
            <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              共 {total} 篇
            </span>
          </div>
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages}
            aria-label="下一页"
            className="px-3 py-1.5 text-sm rounded-lg disabled:opacity-30 disabled:cursor-not-allowed press focus-glow transition-all duration-200"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)' }}
            onMouseEnter={e => { if (page < totalPages) e.currentTarget.style.borderColor = 'var(--border-light)' }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
          >
            下一页
          </button>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除论文"
        message="确定删除这篇论文？删除后不可恢复。"
        variant="danger"
        confirmLabel="删除"
        cancelLabel="取消"
        onConfirm={() => {
          if (deleteTarget !== null) onDelete(deleteTarget)
          setDeleteTarget(null)
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
})
