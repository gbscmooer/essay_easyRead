/**
 * PaperList — 论文账本
 *
 * 浏览与智能搜索共用同一套账本行（.er-ledger）：编号 / 标题 + 元信息 / 状态 /
 * 行内操作四栏对齐。之前每行各自 `flex justify-between`，标题右缘、标签起点、
 * 删除按钮的位置逐行不同，列表读起来是「一堆条目」而不是「一张表」。
 *
 * 所有呈现改由类名承载，组件里不再出现内联 style 与 onMouseEnter 手写 hover。
 */

import { useState, useMemo, useEffect, memo, type ReactNode } from 'react'
import type { Paper, SearchResult } from '@/types'
import type { PaperFacets } from '@/api/papers'
import { ConfirmDialog, Tooltip, LoadingSpinner } from '@/ds-entry'
import { stopRowActivation } from '@/utils/keyboard'
import { parseServerDate } from '@/utils/datetime'
import { SlidersHorizontal, Pencil, Trash2, FolderPlus, ExternalLink } from 'lucide-react'
import { paperLink } from '@/features/research/paperLinks'

export const PUBLICATION_TYPES: Record<string, string> = { journal: '期刊论文', conference: '会议论文', preprint: '预印本', book: '书籍 / 章节', other: '其他', unknown: '类型未识别' }
const PROVIDERS: Record<string, string> = { arxiv: 'arXiv', scopus: 'Scopus / Elsevier', openalex: 'OpenAlex', crossref: 'Crossref', semantic_scholar: 'Semantic Scholar', import: '外部导入' }

/** 搜索输入停下多久后自动发起检索（毫秒）。 */
const SEARCH_DEBOUNCE_MS = 350

/**
 * 多选与批量操作的接口。
 *
 * 勾选状态归 LibraryPage（批量操作要发请求、要刷新列表），列表只负责画出来
 * 并把点击转回去；`actions` 由页面注入，避免把工作区 API 塞进列表组件。
 */
export interface PaperSelection {
  ids: Set<number>
  toggle: (id: number) => void
  selectPage: (ids: number[]) => void
  clear: () => void
  actions: ReactNode
}

export interface FilterParams {
  category?: string
  subject?: string
  venue?: string
  publication_type?: string
  provider?: string
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
  subjects: '研究方向',
  providers: '元数据来源',
  doi: 'DOI',
  publisher: '出版社',
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
  // 全库筛选值与排序
  facets: PaperFacets | null
  sort: string
  onSortChange: (sort: string) => void
  selection: PaperSelection
  onEdit?: (paper: Paper) => void
  onPromote?: (id: number) => void
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
    case 'indexed': return { text: '已索引 · 待解析', tone: 'er-tag--warn', icon: 'clock' }
    default: return { text: '待解析', tone: 'er-tag--mute', icon: 'clock' }
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
  facets,
  sort,
  onSortChange,
  selection,
  onEdit,
  onPromote,
}: PaperListProps) {
  const [searchInput, setSearchInput] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [yearFilter, setYearFilter] = useState('')
  const [dateFilter, setDateFilter] = useState('')
  const [extraFilters, setExtraFilters] = useState<{ venue: string; publication_type: string; provider: string }>({ venue: '', publication_type: '', provider: '' })
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null)

  /* 筛选下拉的选项来自全库 facets。后端不可达时才回落到当前页派生 ——
     那正是老行为：翻一页选项就换一批，跨页的方向和年份根本选不到。 */
  const categories = useMemo(() => {
    if (facets) return facets.subjects ?? facets.categories
    return Array.from(new Set(papers.flatMap(p => p.subjects ?? (p.category ? [p.category] : [])))).sort()
  }, [facets, papers])

  const years = useMemo(() => {
    if (facets) return facets.years
    const set = new Set(papers.map(p => p.year).filter((y): y is number => !!y && y >= 1900))
    return Array.from(set).sort((a, b) => b - a)
  }, [facets, papers])

  const dates = useMemo(() => {
    if (facets) return facets.dates
    return Array.from(new Set(papers.map(p => p.created_at.slice(0, 10)).filter(Boolean))).sort().reverse()
  }, [facets, papers])

  const totalPages = Math.ceil(total / perPage)

  /* 输入即搜（停 350ms 后发起）。此前只有敲回车才会搜，输入框里打完字
     什么也不发生，看起来像坏了。回车仍然可用，只是不再是唯一的出口。 */
  useEffect(() => {
    if (searchInput.trim() === searchQuery.trim()) return
    const timer = setTimeout(() => onSearch(searchInput), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [searchInput, searchQuery, onSearch])

  const handleSearch = () => {
    onSearch(searchInput)
  }

  const clearSearch = () => {
    setSearchInput('')
    onSearch('')
  }

  const applyFilter = (next: { category?: string; year?: string; date?: string; venue?: string; publication_type?: string; provider?: string }) => {
    const category = next.category ?? categoryFilter
    const year = next.year ?? yearFilter
    const date = next.date ?? dateFilter
    onFilter({
      ...(facets?.subjects ? { subject: category || undefined } : { category: category || undefined }),
      year: year ? Number(year) : undefined,
      dateAdded: date || undefined,
      venue: (next.venue ?? extraFilters.venue) || undefined,
      publication_type: (next.publication_type ?? extraFilters.publication_type) || undefined,
      provider: (next.provider ?? extraFilters.provider) || undefined,
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
            onKeyDown={e => {
              if (e.key === 'Enter') handleSearch()
              if (e.key === 'Escape' && searchInput) { e.preventDefault(); clearSearch() }
            }}
            aria-label="搜索论文"
            data-shortcut-search
          />
          {searchLoading && <LoadingSpinner size="sm" />}
          {searchInput && (
            <button
              type="button"
              className="er-search__clear"
              onClick={clearSearch}
              aria-label="清除搜索"
              title="清除搜索（Esc）"
            >
              ×
            </button>
          )}
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
          content="包含深度研究发现的论文，浏览与搜索使用相同范围。"
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
          value={sort}
          onChange={e => onSortChange(e.target.value)}
          aria-label="排序方式"
          disabled={searchResults !== null}
          title={searchResults !== null ? '搜索结果按命中数排序' : '选择排序方式'}
        >
          {(facets?.sorts ?? [{ value: 'recent', label: '最近添加' }]).map(option => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>
      <details className="arc-filters">
        <summary><SlidersHorizontal size={16} />筛选{[categoryFilter, yearFilter, dateFilter, ...Object.values(extraFilters)].filter(Boolean).length > 0 && <span> · {[categoryFilter, yearFilter, dateFilter, ...Object.values(extraFilters)].filter(Boolean).length} 项</span>}</summary>
        <div className="arc-filter-grid">
        <label>研究方向
        <select
          className="er-select"
          value={categoryFilter}
          onChange={e => handleFilterCategory(e.target.value)}
          aria-label="按方向筛选"
        >
          <option value="">全部方向</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
          {facets?.subjects && <option value="__missing__">方向未识别</option>}
        </select>
        </label>
        <label>发表类型<select className="er-select" aria-label="按发表类型筛选" value={extraFilters.publication_type} onChange={e => { const value = e.target.value; setExtraFilters(f => ({ ...f, publication_type: value })); applyFilter({ publication_type: value }) }}>
          <option value="">全部类型</option>{Object.entries(PUBLICATION_TYPES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>会议 / 期刊<select className="er-select" aria-label="按会议期刊筛选" value={extraFilters.venue} onChange={e => { const value = e.target.value; setExtraFilters(f => ({ ...f, venue: value })); applyFilter({ venue: value }) }}>
          <option value="">全部会议 / 期刊</option>{(facets?.venues ?? []).map(v => <option key={v} value={v}>{v}</option>)}<option value="__missing__">会议 / 期刊未识别</option>
        </select></label>
        <label>元数据来源<select className="er-select" aria-label="按元数据来源筛选" value={extraFilters.provider} onChange={e => { const value = e.target.value; setExtraFilters(f => ({ ...f, provider: value })); applyFilter({ provider: value }) }}>
          <option value="">全部来源</option>{(facets?.providers ?? []).map(v => <option key={v} value={v}>{PROVIDERS[v] ?? v}</option>)}<option value="__missing__">来源未识别</option>
        </select></label>
        <label>发表年份
        <select
          className="er-select"
          value={yearFilter}
          onChange={e => handleFilterYear(e.target.value)}
          aria-label="按年份筛选"
        >
          <option value="">全部年份</option>
          {years.map(y => <option key={y} value={y}>{y}</option>)}
        </select>
        </label><label>添加日期
        <select
          className="er-select"
          value={dateFilter}
          onChange={e => handleFilterDate(e.target.value)}
          aria-label="按添加日期筛选"
        >
          <option value="">全部日期</option>
          {dates.map(d => <option key={d} value={d}>{formatDate(d)}</option>)}
        </select>
        </label>
        <button type="button" className="er-btn er-btn--ghost" onClick={() => { setCategoryFilter(''); setYearFilter(''); setDateFilter(''); setExtraFilters({ venue: '', publication_type: '', provider: '' }); onFilter({}) }}>清除筛选</button>
        </div>
      </details>

      {searchResults === null && selection.ids.size > 0 && (
        <div className="arc-selection" role="status" aria-live="polite">
          <span className="arc-selection__count"><b>{selection.ids.size}</b> 篇已选</span>
          <button
            type="button"
            className="er-btn er-btn--ghost er-btn--sm"
            onClick={() => selection.selectPage(papers.map(p => p.id))}
          >
            全选本页
          </button>
          <button
            type="button"
            className="er-btn er-btn--ghost er-btn--sm"
            onClick={selection.clear}
          >
            取消选择
          </button>
          <span className="arc-selection__spacer" />
          {selection.actions}
        </div>
      )}

      <div className="er-scroll arc-list__scroll">
        {searchLoading ? <div role="status" className="arc-loading">正在加载论文…</div> : searchResults !== null ? (
          searchResults.length === 0 ? (
            <div className="er-empty">
              <span className="er-empty__mark" aria-hidden="true">00</span>
              <div className="er-empty__body">
                <p className="er-empty__title">没有匹配的论文</p>
              </div>
            </div>
          ) : (
            <div className="er-ledger">
              {searchResults.map((r, index) => (
                <div
                  key={r.paper_id}
                  role="button"
                  tabIndex={0}
                  className="er-ledger__row arc-result"
                  onClick={() => onSelect(r.paper_id)}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(r.paper_id) } }}
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
                      {r.authors && <span className="arc-authors" title={r.authors}><HighlightText text={r.authors} query={searchQuery} /></span>}
                    </span>
                    <span className="arc-publication">
                      <span className={`arc-kind arc-kind--${r.publication_type || 'unknown'}`}>{PUBLICATION_TYPES[r.publication_type || 'unknown']}</span>
                      <span>{r.venue || (r.publication_type === 'preprint' ? '尚无正式发表载体' : '会议 / 期刊未识别')}</span>
                      <span>{r.year || '年份未识别'}</span>
                    </span>
                    <span className="arc-paper-details">
                      {(r.subjects ?? []).map(s => <span key={s}>{s}</span>)}
                      <span>{r.providers?.map(v => PROVIDERS[v] || v).join(' · ')}</span>
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
                    {paperLink(r) && <a className="arc-icon" href={paperLink(r)!} target="_blank" rel="noopener noreferrer" title="打开论文来源" aria-label={`打开论文来源 ${r.title}`} onClick={e => e.stopPropagation()} onKeyDown={stopRowActivation}><ExternalLink size={16} /></a>}
                  </span>
                </div>
              ))}
            </div>
          )
        ) : papers.length === 0 ? (
          <div className="er-empty">
            <span className="er-empty__mark" aria-hidden="true">00</span>
            <div className="er-empty__body">
              <p className="er-empty__title">暂无符合条件的论文</p>
              <p className="er-empty__text">
                当前范围内没有记录。
              </p>
            </div>
          </div>
        ) : (
          <div className="er-ledger er-ledger--selectable">
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
                  data-selected={selection.ids.has(paper.id) || undefined}
                >
                  {/* 勾选不算「打开这篇论文」，所以在这里把冒泡断掉 */}
                  <label
                    className="er-ledger__check"
                    onClick={e => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={selection.ids.has(paper.id)}
                      onChange={() => selection.toggle(paper.id)}
                      onKeyDown={stopRowActivation}
                      aria-label={`选择 ${paper.title || paper.filename}`}
                    />
                  </label>
                  <span className="er-ledger__index" aria-hidden="true">
                    {String((page - 1) * perPage + index + 1).padStart(2, '0')}
                  </span>
                  <span className="er-ledger__body">
                    <span className="er-ledger__title">
                      <span>{paper.title || paper.filename}</span>
                    </span>
                    <span className="er-ledger__meta">
                      <span className="arc-authors" title={paper.authors || undefined}>{paper.authors || '作者未识别'}</span>
                    </span>
                    <span className="arc-publication"><span className={`arc-kind arc-kind--${paper.publication_type || 'unknown'}`}>{PUBLICATION_TYPES[paper.publication_type || 'unknown']}</span>
                      <span>{paper.venue || (paper.publication_type === 'preprint' ? '尚无正式发表载体' : '会议 / 期刊未识别')}</span><span>{paper.year || '年份未识别'}</span>
                    </span>
                    <span className="arc-paper-details">
                      {(paper.subjects ?? []).map(s => <span key={s}>{s}</span>)}
                      <span>{paper.providers?.length ? paper.providers.map(v => PROVIDERS[v] || v).join(' · ') : '来源未识别'}</span>
                      {paper.source === 'expanded' && <span>扩展发现</span>}
                      <span>添加于 {parseServerDate(paper.created_at).toLocaleDateString('zh-CN')}</span>
                    </span>
                  </span>
                  <span className="er-ledger__aside">
                    <ReadMark level={paper.read_level} />
                    <span className={`er-tag ${tone}`}>
                      <StatusBadgeIcon icon={icon} />
                      {statusText}
                    </span>
                    <span className="arc-row-actions">
                    {paperLink({ ...paper, title: paper.title || paper.filename }) && <a className="arc-icon" href={paperLink({ ...paper, title: paper.title || paper.filename })!} target="_blank" rel="noopener noreferrer" title="打开论文来源" aria-label={`打开论文来源 ${paper.title}`} onClick={e => e.stopPropagation()} onKeyDown={stopRowActivation}><ExternalLink size={16} /></a>}
                    {onEdit && <button type="button" className="arc-icon" title="编辑论文信息" aria-label={`编辑论文信息 ${paper.title}`} onClick={e => { e.stopPropagation(); onEdit(paper) }} onKeyDown={stopRowActivation}><Pencil size={16} /></button>}
                    {paper.source === 'expanded' && onPromote && <button type="button" className="arc-icon" title="加入论文库" aria-label={`加入论文库 ${paper.title}`} onClick={e => { e.stopPropagation(); onPromote(paper.id) }} onKeyDown={stopRowActivation}><FolderPlus size={16} /></button>}
                    <button
                      type="button"
                      onClick={e => { e.stopPropagation(); setDeleteTarget(paper.id) }}
                      onKeyDown={stopRowActivation}
                      aria-label={`删除论文 ${paper.title || paper.filename}`}
                      title="删除论文"
                      className="er-btn er-btn--ghost er-btn--destructive er-btn--sm er-ledger__action"
                    >
                      <Trash2 size={16} />
                    </button>
                    </span>
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
