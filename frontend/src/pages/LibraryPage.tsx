/**
 * LibraryPage 组件
 *
 * 论文库页面，管理所有已解析的论文。
 *
 * 路由：/library
 *
 * 功能：
 *   - PaperList 组件展示全部论文
 *   - 搜索和筛选
 *   - 批量操作（可选）
 *   - 导出功能
 *
 * 样式：
 *   - max-w-6xl, mx-auto
 *   - 顶部标题 + 搜索栏
 *   - 论文列表
 */

import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Paper } from '@/types'
import { listPapers, deletePaper } from '@/api/papers'
import { PaperList } from '@/features/library/PaperList'


export function LibraryPage() {
  const navigate = useNavigate()
  const [papers, setPapers] = useState<Paper[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [venue, setVenue] = useState('')
  const [year, setYear] = useState<number | undefined>()
  const perPage = 20

  const fetchPapers = useCallback(() => {
    listPapers({
      search: search || undefined,
      venue: venue || undefined,
      year,
      page,
      per_page: perPage,
    })
      .then(res => {
        setPapers(res.papers)
        setTotal(res.total)
      })
      .catch(() => {})
  }, [search, venue, year, page])

  useEffect(() => {
    fetchPapers()
  }, [fetchPapers])

  const handleSelect = useCallback((id: number) => {
    navigate(`/reader/${id}`)
  }, [navigate])

  const handleDelete = useCallback(async (id: number) => {
    try {
      await deletePaper(id)
      fetchPapers()
    } catch {
      // 删除失败
    }
  }, [fetchPapers])

  const handleSearch = useCallback((query: string) => {
    setSearch(query)
    setPage(1)
  }, [])

  const handleFilter = useCallback((filters: { venue?: string; year?: number }) => {
    setVenue(filters.venue || '')
    setYear(filters.year)
    setPage(1)
  }, [])

  const handlePageChange = useCallback((newPage: number) => {
    setPage(newPage)
  }, [])

  return (
    <div className="max-w-6xl mx-auto h-[calc(100vh-3.5rem)] flex flex-col">
      <div className="px-6 pt-6 pb-2">
        <h1 className="text-2xl font-bold text-gray-100">论文库</h1>
      </div>
      <PaperList
        papers={papers}
        total={total}
        onSelect={handleSelect}
        onDelete={handleDelete}
        onSearch={handleSearch}
        onFilter={handleFilter}
        onPageChange={handlePageChange}
        page={page}
        perPage={perPage}
      />
    </div>
  )
}
