import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Paper, SearchResult } from '@/types'
import { listPapers, deletePaper, promoteExpandedPaper, smartSearch, reindexLibrary } from '@/api/papers'
import { PaperList } from '@/features/library/PaperList'
import { Tooltip } from '@/components/Tooltip'

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
          backgroundColor: paper.status === 'failed' ? 'var(--error-subtle)' : 'var(--bg-surface)',
          color: paper.status === 'failed' ? 'var(--error)' : 'var(--text-muted)',
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
  const [category, setCategory] = useState('')
  const [year, setYear] = useState<number | undefined>()
  const [dateAdded, setDateAdded] = useState('')
  const perPage = 20

  const [expandedPapers, setExpandedPapers] = useState<Paper[]>([])
  const [expandedTotal, setExpandedTotal] = useState(0)
  const [showExpanded, setShowExpanded] = useState(false)

  // 重新索引：用 GROBID 重提元数据缺失/索引失败的论文，轮询观察进度
  const [reindexing, setReindexing] = useState(false)
  const [reindexNote, setReindexNote] = useState<string | null>(null)

  // 智能搜索：searchResults 为 null = 浏览模式；非 null = 搜索结果模式
  const [globalSearch, setGlobalSearch] = useState(false)
  const [includeExpanded, setIncludeExpanded] = useState(false)
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null)
  const [searchTotal, setSearchTotal] = useState(0)
  const [searchLoading, setSearchLoading] = useState(false)

  // 浏览：仅在无搜索词时拉取分页论文（同时为筛选下拉提供 venue/year 选项）
  const fetchPapers = useCallback(() => {
    listPapers({
      category: category || undefined,
      year,
      date_added: dateAdded || undefined,
      page,
      per_page: perPage,
      source: 'uploaded',
    })
      .then(res => {
        setPapers(res.papers)
        setTotal(res.total)
      })
      .catch(() => {})
  }, [category, year, dateAdded, page])

  // 智能搜索：搜索词非空时调 smart-search（元数据 + 解读内容 + 可选全局正文）
  useEffect(() => {
    const q = search.trim()
    if (!q) {
      setSearchResults(null)
      return
    }
    setSearchLoading(true)
    smartSearch(q, { category: category || undefined, year, dateAdded: dateAdded || undefined, global: globalSearch, includeExpanded, page, perPage })
      .then(res => {
        setSearchResults(res.results)
        setSearchTotal(res.total)
      })
      .catch(() => { setSearchResults([]); setSearchTotal(0) })
      .finally(() => setSearchLoading(false))
  }, [search, category, year, dateAdded, globalSearch, includeExpanded, page])

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
    } catch {}
  }, [fetchPapers])

  const handleSearch = useCallback((query: string) => {
    setSearch(query)
    setPage(1)
  }, [])

  const handleFilter = useCallback((filters: { category?: string; year?: number; dateAdded?: string }) => {
    setCategory(filters.category || '')
    setYear(filters.year)
    setDateAdded(filters.dateAdded || '')
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

  const handleToggleGlobal = useCallback((v: boolean) => {
    setGlobalSearch(v)
    setPage(1)
  }, [])

  const handleToggleExpanded = useCallback((v: boolean) => {
    setIncludeExpanded(v)
    setPage(1)
  }, [])

  const handleReindex = useCallback(async () => {
    if (reindexing) return
    setReindexNote(null)
    setReindexing(true)
    try {
      const { queued } = await reindexLibrary(includeExpanded)
      if (queued === 0) {
        setReindexing(false)
        setReindexNote('没有可重新索引的论文')
        setTimeout(() => setReindexNote(null), 4000)
        return
      }
      setReindexNote(`正在重新索引 ${queued} 篇论文${includeExpanded ? '（含联网扩展）' : ''}…`)
      // 轮询：当不再有论文处于 indexing（且已过启动窗口）即视为完成
      const pollSource = includeExpanded ? 'all' : 'uploaded'
      const started = Date.now()
      const poll = async () => {
        const res = await listPapers({ source: pollSource, per_page: 200 }).catch(() => null)
        const indexing = res ? res.papers.filter(p => p.index_status === 'indexing').length : 0
        fetchPapers()
        const elapsed = Date.now() - started
        if (indexing === 0 && elapsed > 4000) {
          setReindexing(false)
          setReindexNote('重新索引完成')
          setTimeout(() => setReindexNote(null), 4000)
          return
        }
        if (elapsed > 180000) {  // 兜底超时
          setReindexing(false)
          setReindexNote(null)
          return
        }
        setTimeout(poll, 3000)
      }
      setTimeout(poll, 3000)
    } catch {
      setReindexing(false)
      setReindexNote('重新索引失败，请重试')
      setTimeout(() => setReindexNote(null), 4000)
    }
  }, [reindexing, fetchPapers, includeExpanded])

  return (
    <div className="max-w-5xl mx-auto h-[calc(100vh-3.5rem)] flex flex-col">
      <div className="px-6 pt-6 pb-2 flex items-center justify-between gap-3">
        <h1 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>论文库</h1>
        <div className="flex items-center gap-3">
          {reindexNote && (
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{reindexNote}</span>
          )}
          <Tooltip
            content="重新索引会对每篇论文清空旧索引、用 GROBID 重新提取元数据并覆盖写回（不跳过已索引的论文）。默认处理论文库里的所有论文；勾选「含联网扩展论文」后会一并重做深度研究发现的论文。需后台 GROBID 服务在运行。"
            delay={600}
            placement="bottom"
          >
            <button
              onClick={handleReindex}
              disabled={reindexing}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
              style={{ color: 'var(--accent)', border: '1px solid var(--border)' }}
            >
              <svg
                className={`w-4 h-4 ${reindexing ? 'animate-spin' : ''}`}
                fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {reindexing ? '索引中…' : '重新索引'}
            </button>
          </Tooltip>
        </div>
      </div>

      <PaperList
        papers={papers}
        total={searchResults !== null ? searchTotal : total}
        onSelect={handleSelect}
        onDelete={handleDelete}
        onSearch={handleSearch}
        onFilter={handleFilter}
        onPageChange={handlePageChange}
        page={page}
        perPage={perPage}
        globalSearch={globalSearch}
        onToggleGlobal={handleToggleGlobal}
        includeExpanded={includeExpanded}
        onToggleExpanded={handleToggleExpanded}
        searchResults={searchResults}
        searchLoading={searchLoading}
        searchQuery={search}
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
