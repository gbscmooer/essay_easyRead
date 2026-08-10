/**
 * PaperList — 论文账本
 *
 * 浏览与智能搜索共用同一套账本行（.er-ledger）：编号 / 标题 + 元信息 / 状态 /
 * 行内操作四栏对齐。之前每行各自 `flex justify-between`，标题右缘、标签起点、
 * 删除按钮的位置逐行不同，列表读起来是「一堆条目」而不是「一张表」。
 *
 * 所有呈现改由类名承载，组件里不再出现内联 style 与 onMouseEnter 手写 hover。
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

/** 高亮搜索关键词。命中段用 <mark>，配色由样式层的赭石高亮统一给。 */
function HighlightText({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = text.split(new RegExp(`(${escaped})`, 'gi'))
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === query.toLowerCase()
          ? <mark key={i}>{part}</mark>
          : <span key={i}>{part}</span>,
      )}
    </>
  )
}

type StatusIcon = 'check' | 'cross' | 'clock' | 'spin'

/** 状态 → 文案 + 语义色档位。色相只允许取自调色板里的四个语义色。 */
function statusLabel(status: Paper['status']): { text: string; tone: string; icon: StatusIcon } {
  switch (status) {
    case 'done': return { text: '已完成', tone: '', icon: 'check' }
    case 'extracting': return { text: '提取中', tone: 'er-tag--warn', icon: 'spin' }
    case 'analyzing': return { text: '分析中', tone: 'er-tag--warn', icon: 'spin' }
    case 'pending': return { text: '等待中', tone: 'er-tag--mute', icon: 'clock' }
    case 'failed': return { text: '失败', tone: 'er-tag--signal', icon: 'cross' }
    default: return { text: status, tone: 'er-tag--mute', icon: 'clock' }
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

/** 阅读程度：精读实心 / 速读描边。同一形制的两档，不用两个自带色相的胶囊。 */
function ReadMark({ level }: { level: Paper['read_level'] }) {
  if (level === 'full') return <span className="er-read-mark er-read-mark--full">精读</span>
  if (level === 'digest') return <span className="er-read-mark">速读</span>
  return null
}

function SearchIcon() {
  return (
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
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
    <div className="arc-list">
      <div className="er-filterbar">
        <div className="er-search">
          <SearchIcon />
          <input
            type="text"
            className="er-input"
            placeholder="智能搜索（标题 / 作者 / 刊物 / 解读内容）"
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            aria-label="搜索论文"
          />
        </div>

        <Tooltip
          content="开启后，搜索词会额外在每篇论文的 PDF 正文全文中做字面匹配，而不仅限于标题、作者和解读内容。适用于在论文正文中查找特定术语或关键词的场景。"
          delay={1000}
          placement="bottom"
        >
          <label className="er-toggle">
            <input
              type="checkbox"
              checked={globalSearch}
              onChange={e => onToggleGlobal(e.target.checked)}
            />
            <i aria-hidden="true" />
            全文
          </label>
        </Tooltip>

        <Tooltip
          content="开启后，搜索范围一并包含深度研究通过联网下载的相关论文（L2+）。默认只搜已加入论文库的论文。"
          delay={1000}
          placement="bottom"
        >
          <label className="er-toggle">
            <input
              type="checkbox"
              checked={includeExpanded}
              onChange={e => onToggleExpanded(e.target.checked)}
            />
            <i aria-hidden="true" />
            含扩展
          </label>
        </Tooltip>

        <select
          className="er-select"
          value={categoryFilter}
          onChange={e => handleFilterCategory(e.target.value)}
          aria-label="按方向筛选"
        >
          <option value="">全部方向</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>

        <select
          className="er-select"
          value={yearFilter}
          onChange={e => handleFilterYear(e.target.value)}
          aria-label="按年份筛选"
        >
          <option value="">全部年份</option>
          {years.map(y => <option key={y} value={y}>{y}</option>)}
        </select>

        <select
          className="er-select"
          value={dateFilter}
          onChange={e => handleFilterDate(e.target.value)}
          aria-label="按添加日期筛选"
        >
          <option value="">全部日期</option>
          {dates.map(d => <option key={d} value={d}>{formatDate(d)}</option>)}
        </select>
      </div>

      <div className="er-scroll arc-list__scroll">
        {searchResults !== null ? (
          searchLoading ? (
            <div className="arc-list__loading" aria-busy="true" aria-label="搜索中">
              {Array.from({ length: 4 }).map((_, i) => (
                <SkeletonCard key={i} style={{ animationDelay: `${i * 60}ms` }} />
              ))}
            </div>
          ) : searchResults.length === 0 ? (
            <div className="er-empty">
              <span className="er-empty__mark" aria-hidden="true">00</span>
              <div className="er-empty__body">
                <p className="er-empty__title">没有匹配的论文</p>
                <p className="er-empty__text">
                  换一个关键词，或者打开「全文」把匹配范围扩大到 PDF 正文。
                </p>
              </div>
            </div>
          ) : (
            <div className="er-ledger">
              {searchResults.map((r, index) => (
                <button
                  key={r.paper_id}
                  type="button"
                  className="er-ledger__row arc-result"
                  onClick={() => onSelect(r.paper_id)}
                >
                  <span className="er-ledger__index" aria-hidden="true">
                    {String((page - 1) * perPage + index + 1).padStart(2, '0')}
                  </span>
                  <span className="er-ledger__body">
                    <span className="er-ledger__title">
                      <span><HighlightText text={r.title} query={searchQuery} /></span>
                      {r.source === 'expanded' && <span className="er-tag er-tag--warn">联网扩展</span>}
                    </span>
                    <span className="er-ledger__meta">
                      {r.authors && <span className="arc-result__authors"><HighlightText text={r.authors} query={searchQuery} /></span>}
                      {r.venue && <span><HighlightText text={r.venue} query={searchQuery} /></span>}
                      {r.year && <span>{r.year}</span>}
                    </span>
                    <span className="arc-result__hits">
                      {r.matched_fields.slice(0, 3).map((m, i) => (
                        <span className="arc-result__hit" key={i}>
                          <b>
                            {FIELD_LABEL[m.field] ?? m.field}
                            {m.field === 'fulltext' && m.line ? ` L${m.line}` : ''}
                          </b>
                          <HighlightText text={m.snippet} query={searchQuery} />
                        </span>
                      ))}
                    </span>
                  </span>
                  <span className="er-ledger__aside">
                    <span className="er-tag er-tag--mute">{r.matched_fields.length} 处命中</span>
                  </span>
                </button>
              ))}
            </div>
          )
        ) : papers.length === 0 ? (
          <div className="er-empty">
            <span className="er-empty__mark" aria-hidden="true">00</span>
            <div className="er-empty__body">
              <p className="er-empty__title">论文库还是空的</p>
              <p className="er-empty__text">
                从首页放入一篇 PDF，解析完成后它会出现在这张账本上。
              </p>
            </div>
          </div>
        ) : (
          <div className="er-ledger">
            {papers.map((paper, index) => {
              const { text: statusText, tone, icon } = statusLabel(paper.status)
              return (
                <div
                  key={paper.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelect(paper.id)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(paper.id) }
                  }}
                  className="er-ledger__row"
                >
                  <span className="er-ledger__index" aria-hidden="true">
                    {String((page - 1) * perPage + index + 1).padStart(2, '0')}
                  </span>
                  <span className="er-ledger__body">
                    <span className="er-ledger__title">
                      <span>{paper.title || paper.filename}</span>
                    </span>
                    <span className="er-ledger__meta">
                      {paper.venue && <span>{paper.venue}</span>}
                      {paper.year && <span>{paper.year}</span>}
                      {paper.category && <span>{paper.category}</span>}
                      <span>{new Date(paper.created_at).toLocaleDateString('zh-CN')}</span>
                    </span>
                  </span>
                  <span className="er-ledger__aside">
                    <ReadMark level={paper.read_level} />
                    <span className={`er-tag ${tone}`}>
                      <StatusBadgeIcon icon={icon} />
                      {statusText}
                    </span>
                    <button
                      type="button"
                      onClick={e => { e.stopPropagation(); setDeleteTarget(paper.id) }}
                      aria-label={`删除论文 ${paper.title || paper.filename}`}
                      className="er-btn er-btn--ghost er-btn--destructive er-btn--sm er-ledger__action"
                    >
                      删除
                    </button>
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {totalPages > 1 && (
        <div className="er-pagination">
          <button
            type="button"
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
            className="er-btn er-btn--secondary er-btn--sm"
          >
            ← 上一页
          </button>
          <span className="er-pagination__state">
            <b>{String(page).padStart(2, '0')}</b>
            <span>/ {String(totalPages).padStart(2, '0')} 页</span>
            <span>· 共 {total} 篇</span>
          </span>
          <button
            type="button"
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages}
            className="er-btn er-btn--secondary er-btn--sm"
          >
            下一页 →
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
