/**
 * PaperList 组件
 *
 * 论文库列表，清新学术风格，支持筛选和搜索。
 */

import { useState, useMemo } from 'react'
import type { Paper } from '@/types'


interface FilterParams {
  venue?: string
  year?: number
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
}

function statusLabel(status: Paper['status']): { text: string; bgColor: string; textColor: string } {
  switch (status) {
    case 'done': return { text: '已完成', bgColor: 'rgba(34,197,94,0.1)', textColor: '#16a34a' }
    case 'extracting': return { text: '提取中', bgColor: 'rgba(234,179,8,0.1)', textColor: '#ca8a04' }
    case 'analyzing': return { text: '分析中', bgColor: 'rgba(234,179,8,0.1)', textColor: '#ca8a04' }
    case 'pending': return { text: '等待中', bgColor: 'var(--bg-surface)', textColor: 'var(--text-muted)' }
    case 'failed': return { text: '失败', bgColor: 'rgba(239,68,68,0.1)', textColor: '#dc2626' }
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
          {/* 搜索框 */}
          <div className="flex-1 min-w-[200px]">
            <div className="relative">
              <input
                type="text"
                placeholder="搜索论文标题..."
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

      {/* 论文列表 */}
      <div className="flex-1 overflow-auto p-4 space-y-2">
        {papers.length === 0 ? (
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
                className="flex items-center justify-between p-4 rounded-xl cursor-pointer card-hover group"
                style={{
                  backgroundColor: 'var(--bg-surface)',
                  border: '1px solid var(--border)',
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
