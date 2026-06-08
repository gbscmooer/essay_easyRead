/**
 * PaperList 组件
 *
 * 论文库列表，清新学术风格，支持筛选和搜索。
 */

import { useState, useMemo } from 'react'
import type { Paper, SearchResult } from '@/types'
import { Tooltip } from '@/components/Tooltip'


interface FilterParams {
  venue?: string
  year?: number
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
          ? <mark key={i} style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)', borderRadius: '2px', padding: '0 1px' }}>{part}</mark>
          : <span key={i}>{part}</span>
      )}
    </>
  )
}

function statusLabel(status: Paper['status']): { text: string; bgColor: string; textColor: string } {
  switch (status) {
    case 'done': return { text: '已完成', bgColor: 'var(--success-subtle)', textColor: 'var(--success)' }
    case 'extracting': return { text: '提取中', bgColor: 'var(--warning-subtle)', textColor: 'var(--warning)' }
    case 'analyzing': return { text: '分析中', bgColor: 'var(--warning-subtle)', textColor: 'var(--warning)' }
    case 'pending': return { text: '等待中', bgColor: 'var(--bg-surface)', textColor: 'var(--text-muted)' }
    case 'failed': return { text: '失败', bgColor: 'var(--error-subtle)', textColor: 'var(--error)' }
    default: return { text: status, bgColor: 'var(--bg-surface)', textColor: 'var(--text-muted)' }
  }
}

export function PaperList({
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
  const [venueFilter, setVenueFilter] = useState('')
  const [yearFilter, setYearFilter] = useState('')

  const venues = useMemo(() => {
    const set = new Set(papers.map(p => p.venue).filter(Boolean))
    return Array.from(set).sort() as string[]
  }, [papers])

  const years = useMemo(() => {
    const set = new Set(papers.map(p => p.year).filter(Boolean))
    return Array.from(set).sort((a, b) => (b ?? 0) - (a ?? 0)) as number[]
  }, [papers])

  const totalPages = Math.ceil(total / perPage)

  const handleSearch = () => {
    onSearch(searchInput)
  }

  const handleFilterVenue = (venue: string) => {
    setVenueFilter(venue)
    onFilter({ venue: venue || undefined, year: yearFilter ? Number(yearFilter) : undefined })
  }

  const handleFilterYear = (year: string) => {
    setYearFilter(year)
    onFilter({ venue: venueFilter || undefined, year: year ? Number(year) : undefined })
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
                className="w-full px-4 py-2 pl-10 text-sm rounded-lg focus:outline-none"
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
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg cursor-pointer select-none"
              style={{ color: globalSearch ? 'var(--accent)' : 'var(--text-muted)', border: '1px solid var(--border)' }}
            >
              <input
                type="checkbox"
                checked={globalSearch}
                onChange={e => onToggleGlobal(e.target.checked)}
                className="accent-blue-500"
              />
              全局搜索
            </label>
          </Tooltip>

          {/* 联网扩展论文开关：是否一并搜索深度研究下载的相关论文 */}
          <label
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg cursor-pointer select-none"
            style={{ color: includeExpanded ? 'var(--accent)' : 'var(--text-muted)', border: '1px solid var(--border)' }}
            title="开启后，搜索范围一并包含深度研究通过联网下载的相关论文（L2+），默认只搜已加入论文库的论文"
          >
            <input
              type="checkbox"
              checked={includeExpanded}
              onChange={e => onToggleExpanded(e.target.checked)}
              className="accent-blue-500"
            />
            含联网扩展论文
          </label>

          {/* 会议筛选 */}
          <select
            value={venueFilter}
            onChange={e => handleFilterVenue(e.target.value)}
            className="px-3 py-2 text-sm rounded-lg focus:outline-none"
          >
            <option value="">全部会议</option>
            {venues.map(v => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>

          {/* 年份筛选 */}
          <select
            value={yearFilter}
            onChange={e => handleFilterYear(e.target.value)}
            className="px-3 py-2 text-sm rounded-lg focus:outline-none"
          >
            <option value="">全部年份</option>
            {years.map(y => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>
      </div>

      {/* 论文列表 / 智能搜索结果 */}
      <div className="flex-1 overflow-auto p-4 space-y-2">
        {searchResults !== null ? (
          searchLoading ? (
            <div className="text-center py-16">
              <p className="text-sm" style={{ color: 'var(--text-muted)' }}>搜索中…</p>
            </div>
          ) : searchResults.length === 0 ? (
            <div className="text-center py-16">
              <p className="text-lg mb-2" style={{ color: 'var(--text-secondary)' }}>没有匹配的论文</p>
              <p className="text-sm" style={{ color: 'var(--text-muted)' }}>换个关键词，或开启「全局搜索」匹配正文全文</p>
            </div>
          ) : (
            searchResults.map(r => (
              <div
                key={r.paper_id}
                onClick={() => onSelect(r.paper_id)}
                className="p-4 rounded-xl cursor-pointer transition-all"
                style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}
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
          <div className="text-center py-16">
            <p className="text-lg mb-2" style={{ color: 'var(--text-secondary)' }}>暂无论文</p>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>上传 PDF 开始精读</p>
          </div>
        ) : (
          papers.map(paper => {
            const { text: statusText, bgColor: statusBg, textColor: statusColor } = statusLabel(paper.status)
            return (
              <div
                key={paper.id}
                onClick={() => onSelect(paper.id)}
                className="flex items-center justify-between p-4 rounded-xl cursor-pointer transition-all group"
                style={{
                  backgroundColor: 'var(--bg-surface)',
                  border: '1px solid var(--border)',
                }}
                onMouseEnter={e => {
                  const el = e.currentTarget
                  el.style.borderColor = 'var(--border-light)'
                  el.style.boxShadow = '0 1px 4px rgba(0,0,0,0.05)'
                }}
                onMouseLeave={e => {
                  const el = e.currentTarget
                  el.style.borderColor = 'var(--border)'
                  el.style.boxShadow = 'none'
                }}
              >
                <div className="flex-1 min-w-0 mr-4">
                  <h3 className="text-sm font-medium truncate mb-1" style={{ color: 'var(--text-primary)' }}>
                    {paper.title || paper.filename}
                  </h3>
                  <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--text-muted)' }}>
                    {paper.venue && <span>{paper.venue}</span>}
                    {paper.year && <span>{paper.year}</span>}
                    <span>{new Date(paper.created_at).toLocaleDateString('zh-CN')}</span>
                  </div>
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  <span className="px-2.5 py-0.5 text-xs rounded-full" style={{ backgroundColor: statusBg, color: statusColor }}>
                    {statusText}
                  </span>
                  <button
                    onClick={e => {
                      e.stopPropagation()
                      onDelete(paper.id)
                    }}
                    className="opacity-0 group-hover:opacity-100 transition-all text-sm"
                    style={{ color: 'var(--text-muted)' }}
                    title="删除"
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
            className="px-3 py-1.5 text-sm rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}
          >
            上一页
          </button>
          <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
            {page} / {totalPages}
          </span>
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages}
            className="px-3 py-1.5 text-sm rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
