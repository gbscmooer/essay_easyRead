import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Paper } from '@/types'
import { listPapers, deletePaper, promoteExpandedPaper } from '@/api/papers'
import { toast } from '@/hooks/useToast'
import { PaperList } from '@/features/library/PaperList'

const STATUS_LABEL: Record<string, string> = {
  done:       '已解析',
  indexed:    '已索引',
  pending:    '待解析',
  failed:     '解析失败',
  extracting: '提取中',
  analyzing:  '分析中',
}

function ExpandedPaperRow({
  paper,
  onPromote,
  onSelect,
}: {
  paper: Paper
  onPromote: (id: number) => void
  onSelect: (id: number) => void
}) {
  const [promoting, setPromoting] = useState(false)

  const handlePromote = async (e: React.MouseEvent) => {
    e.stopPropagation()
    setPromoting(true)
    try {
      await promoteExpandedPaper(paper.id)
      onPromote(paper.id)
    } catch {
      setPromoting(false)
    }
  }

  const statusText = STATUS_LABEL[paper.status] ?? paper.status

  return (
    <div
      className="flex items-center gap-3 px-4 py-2.5 rounded-lg cursor-pointer transition-colors group"
      style={{ backgroundColor: 'var(--bg-base)' }}
      onClick={() => onSelect(paper.id)}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm truncate" style={{ color: 'var(--text-primary)' }}>
          {paper.title || paper.filename}
        </p>
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
          {[paper.venue, paper.year].filter(Boolean).join(' · ')}
        </p>
      </div>
      <span
        className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full"
        style={{
          backgroundColor: paper.status === 'failed' ? 'rgba(239,68,68,0.12)' : 'var(--bg-surface)',
          color: paper.status === 'failed' ? '#ef4444' : 'var(--text-muted)',
        }}
      >
        {statusText}
      </span>
      <button
        onClick={handlePromote}
        disabled={promoting}
        className="shrink-0 text-xs px-2.5 py-1 rounded-md border transition-colors disabled:opacity-50"
        style={{
          borderColor: 'var(--accent)',
          color: 'var(--accent)',
          backgroundColor: 'transparent',
        }}
      >
        {promoting ? '移动中…' : '加入论文库'}
      </button>
    </div>
  )
}

export function LibraryPage() {
  const navigate = useNavigate()
  const [papers, setPapers] = useState<Paper[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [venue, setVenue] = useState('')
  const [year, setYear] = useState<number | undefined>()
  const perPage = 20

  const [expandedPapers, setExpandedPapers] = useState<Paper[]>([])
  const [expandedTotal, setExpandedTotal] = useState(0)
  const [showExpanded, setShowExpanded] = useState(false)

  const fetchPapers = useCallback(() => {
    listPapers({
      search: search || undefined,
      venue: venue || undefined,
      year,
      page,
      per_page: perPage,
      source: 'uploaded',
    })
      .then(res => {
        setPapers(res.papers)
        setTotal(res.total)
      })
      .catch(() => {})
  }, [search, venue, year, page])

  const fetchExpandedPapers = useCallback(() => {
    listPapers({ source: 'expanded', per_page: 100 })
      .then(res => {
        setExpandedPapers(res.papers)
        setExpandedTotal(res.total)
      })
      .catch(() => {})
  }, [])

  useEffect(() => { fetchPapers() }, [fetchPapers])
  useEffect(() => { fetchExpandedPapers() }, [fetchExpandedPapers])

  const handleSelect = useCallback((id: number) => {
    navigate(`/reader/${id}`)
  }, [navigate])

  const handleDelete = useCallback(async (id: number) => {
    try {
      await deletePaper(id)
      fetchPapers()
    } catch (e: any) {
      toast({ type: 'error', message: e?.message || '删除论文失败' })
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

  const handlePromote = useCallback((id: number) => {
    setExpandedPapers(prev => prev.filter(p => p.id !== id))
    setExpandedTotal(prev => prev - 1)
    fetchPapers()
  }, [fetchPapers])

  return (
    <div className="max-w-5xl mx-auto h-[calc(100vh-3.5rem)] flex flex-col">
      <div className="px-6 pt-6 pb-2">
        <h1 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>论文库</h1>
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

      {/* 深度研究发现区块 */}
      {expandedTotal > 0 && (
        <div className="px-6 pb-6 shrink-0">
          <button
            className="w-full flex items-center justify-between px-4 py-3 rounded-xl border transition-colors"
            style={{
              borderColor: 'var(--border)',
              backgroundColor: showExpanded ? 'var(--bg-surface)' : 'transparent',
            }}
            onClick={() => setShowExpanded(v => !v)}
          >
            <div className="flex items-center gap-2">
              <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"
                style={{ color: 'var(--accent)' }}>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <span className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
                深度研究发现的论文
              </span>
              <span
                className="text-xs px-1.5 py-0.5 rounded-full"
                style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}
              >
                {expandedTotal}
              </span>
            </div>
            <svg
              className={`w-4 h-4 transition-transform ${showExpanded ? 'rotate-180' : ''}`}
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
              style={{ color: 'var(--text-muted)' }}
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {showExpanded && (
            <div
              className="mt-2 rounded-xl border overflow-hidden"
              style={{ borderColor: 'var(--border)' }}
            >
              <div className="px-4 py-2 border-b" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)' }}>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  通过深度研究联网扩展下载的论文。点击「加入论文库」可将其移入主论文库并在首页显示。
                </p>
              </div>
              <div className="divide-y" style={{ borderColor: 'var(--border)' }}>
                {expandedPapers.map(p => (
                  <ExpandedPaperRow
                    key={p.id}
                    paper={p}
                    onPromote={handlePromote}
                    onSelect={handleSelect}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
