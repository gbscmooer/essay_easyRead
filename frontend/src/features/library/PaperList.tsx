/**
 * PaperList 组件
 *
 * 论文库列表，支持筛选和搜索。
 *
 * Props:
 *   papers: Paper[] - 论文列表
 *   total: number - 总数
 *   onSelect: (id: number) => void - 选中论文
 *   onDelete: (id: number) => void - 删除论文
 *   onSearch: (query: string) => void - 搜索回调
 *   onFilter: (filters: FilterParams) => void - 筛选回调
 *   onPageChange: (page: number) => void - 翻页
 *
 * 筛选参数：
 *   - venue: 会议下拉选择
 *   - year: 年份下拉选择
 *
 * 功能：
 *   - 顶部搜索框 + 筛选下拉
 *   - 论文卡片列表（标题、会议、年份、状态、时间）
 *   - 分页控件
 *   - 空状态提示
 *
 * 样式：
 *   - 搜索栏：sticky top-0, bg-gray-900
 *   - 卡片：bg-gray-800, hover:bg-gray-700, 圆角
 *   - 分页：底部居中
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

function statusLabel(status: Paper['status']): { text: string; color: string } {
  switch (status) {
    case 'done': return { text: '已完成', color: 'text-green-400 bg-green-400/10' }
    case 'extracting': return { text: '提取中', color: 'text-yellow-400 bg-yellow-400/10' }
    case 'analyzing': return { text: '分析中', color: 'text-yellow-400 bg-yellow-400/10' }
    case 'pending': return { text: '等待中', color: 'text-gray-400 bg-gray-400/10' }
    case 'failed': return { text: '失败', color: 'text-red-400 bg-red-400/10' }
    default: return { text: status, color: 'text-gray-400 bg-gray-400/10' }
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

  // 从 papers 提取可用的 venue 和 year 列表
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
      <div className="sticky top-0 z-10 bg-gray-900 p-4 border-b border-gray-800">
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
                className="w-full px-4 py-2 pl-10 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500"
              />
              <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
          </div>

          {/* 会议筛选 */}
          <select
            value={venueFilter}
            onChange={e => handleFilterVenue(e.target.value)}
            className="px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-300 focus:outline-none focus:border-blue-500"
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
            className="px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-300 focus:outline-none focus:border-blue-500"
          >
            <option value="">全部年份</option>
            {years.map(y => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>
      </div>

      {/* 论文列表 */}
      <div className="flex-1 overflow-auto p-4 space-y-3">
        {papers.length === 0 ? (
          <div className="text-center text-gray-500 py-12">
            <p className="text-lg mb-2">暂无论文</p>
            <p className="text-sm">上传 PDF 开始精读</p>
          </div>
        ) : (
          papers.map(paper => {
            const { text: statusText, color: statusColor } = statusLabel(paper.status)
            return (
              <div
                key={paper.id}
                onClick={() => onSelect(paper.id)}
                className="flex items-center justify-between p-4 bg-gray-800 rounded-lg hover:bg-gray-700 cursor-pointer transition-colors group"
              >
                <div className="flex-1 min-w-0 mr-4">
                  <h3 className="text-sm font-medium text-gray-200 truncate mb-1">
                    {paper.title || paper.filename}
                  </h3>
                  <div className="flex items-center gap-3 text-xs text-gray-500">
                    {paper.venue && <span>{paper.venue}</span>}
                    {paper.year && <span>{paper.year}</span>}
                    <span>{new Date(paper.created_at).toLocaleDateString('zh-CN')}</span>
                  </div>
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  <span className={`px-2 py-0.5 text-xs rounded-full ${statusColor}`}>
                    {statusText}
                  </span>
                  <button
                    onClick={e => {
                      e.stopPropagation()
                      onDelete(paper.id)
                    }}
                    className="text-gray-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all text-sm"
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
        <div className="flex items-center justify-center gap-2 p-4 border-t border-gray-800">
          <button
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
            className="px-3 py-1 text-sm text-gray-400 bg-gray-800 rounded hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            上一页
          </button>
          <span className="text-sm text-gray-500">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages}
            className="px-3 py-1 text-sm text-gray-400 bg-gray-800 rounded hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
