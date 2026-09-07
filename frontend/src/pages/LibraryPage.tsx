/**
 * LibraryPage — 论文库
 *
 * Display 排版标题 + 精致重索引按钮 + 磨砂展开区块。
 */

import { useEffect, useState, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Paper, SearchResult } from '@/types'
import {
  listPapers,
  deletePaper,
  promoteExpandedPaper,
  smartSearch,
  reindexLibrary,
  getPaperFacets,
  type PaperFacets,
} from '@/api/papers'
import { listWorkspaces, addPaperToWorkspace, type WorkspaceItem } from '@/api/workspaces'
import { toast } from '@/hooks/useToast'
import { PaperList } from '@/features/library/PaperList'
import { ConfirmDialog, Tooltip } from '@/ds-entry'
import { stopRowActivation } from '@/utils/keyboard'
import { loadLibrarySort, saveLibrarySort } from '@/utils/libraryPrefs'
import { forgetReadingPage } from '@/utils/readerPrefs'
import '@/styles/library.css'

const DEFAULT_SORT = 'recent'
/** facets 拿不到时也要有一组可选排序，否则下拉是空的。 */
const FALLBACK_SORTS = ['recent', 'oldest', 'year_desc', 'year_asc', 'title']

const STATUS_LABEL: Record<string, string> = {
  done:       '已解析',
  indexed:    '已索引 · 待解析',
  pending:    '待解析',
  failed:     '解析失败',
  extracting: '提取中',
  analyzing:  '分析中',
}

function ExpandedPaperRow({
  paper,
  index,
  onPromote,
  onSelect,
}: {
  paper: Paper
  index: number
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
      className="er-ledger__row"
      role="button"
      tabIndex={0}
      onClick={() => onSelect(paper.id)}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(paper.id) } }}
    >
      <span className="er-ledger__index" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
      <span className="er-ledger__body">
        <span className="er-ledger__title"><span>{paper.title || paper.filename}</span></span>
        <span className="er-ledger__meta">
          {paper.venue && <span>{paper.venue}</span>}
          {paper.year && <span>{paper.year}</span>}
          {paper.category && <span>{paper.category}</span>}
          {paper.index_status === 'indexed' && !paper.year && !paper.category && <span>元数据已索引</span>}
        </span>
      </span>
      <span className="er-ledger__aside">
        <span className={`er-tag ${paper.status === 'failed' ? 'er-tag--signal' : 'er-tag--mute'}`}>
          {statusText}
        </span>
        <button
          type="button"
          onClick={handlePromote}
          onKeyDown={stopRowActivation}
          disabled={promoting}
          aria-busy={promoting}
          className="er-btn er-btn--secondary er-btn--sm"
        >
          {promoting ? '移动中…' : '加入论文库'}
        </button>
      </span>
    </div>
  )
}

export default function LibraryPage() {
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

  const [facets, setFacets] = useState<PaperFacets | null>(null)
  const [sort, setSort] = useState(() => loadLibrarySort(DEFAULT_SORT, FALLBACK_SORTS))
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [batchBusy, setBatchBusy] = useState(false)
  const [batchDeleteConfirm, setBatchDeleteConfirm] = useState(false)

  const [reindexing, setReindexing] = useState(false)
  const [reindexNote, setReindexNote] = useState<string | null>(null)

  const [globalSearch, setGlobalSearch] = useState(false)
  const [includeExpanded, setIncludeExpanded] = useState(false)
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null)
  const [searchTotal, setSearchTotal] = useState(0)
  const [searchLoading, setSearchLoading] = useState(false)

  const fetchPapers = useCallback(() => {
    listPapers({
      category: category || undefined,
      year,
      date_added: dateAdded || undefined,
      page,
      per_page: perPage,
      source: 'uploaded',
      sort,
    })
      .then(res => {
        setPapers(res.papers)
        setTotal(res.total)
      })
      .catch(() => { toast({ type: 'error', message: '加载论文列表失败' }) })
  }, [category, year, dateAdded, page, sort])

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

  /* 筛选值取自全库，所以只在库内容可能变化时重取（增删、入库、重新索引） */
  const fetchFacets = useCallback(() => {
    getPaperFacets(includeExpanded ? 'all' : 'uploaded')
      .then(setFacets)
      .catch(() => setFacets(null))
  }, [includeExpanded])

  const fetchExpandedPapers = useCallback(() => {
    listPapers({ source: 'expanded', per_page: 100 })
      .then(res => {
        setExpandedPapers(res.papers)
        setExpandedTotal(res.total)
      })
      .catch(() => { toast({ type: 'error', message: '加载扩展论文失败' }) })
  }, [])

  useEffect(() => { fetchPapers() }, [fetchPapers])
  useEffect(() => { fetchExpandedPapers() }, [fetchExpandedPapers])
  useEffect(() => { fetchFacets() }, [fetchFacets])
  useEffect(() => {
    listWorkspaces().then(setWorkspaces).catch(() => setWorkspaces([]))
  }, [])
  useEffect(() => { saveLibrarySort(sort) }, [sort])

  const handleSelect = useCallback((id: number) => {
    navigate(`/reader/${id}`)
  }, [navigate])

  const handleDelete = useCallback(async (id: number) => {
    try {
      await deletePaper(id)
      forgetReadingPage(id)
      // 删除成功此前是静默的，只有列表悄悄少一行；成功也要说一声
      toast({ type: 'success', message: '已删除该论文' })
      setSelectedIds(prev => {
        if (!prev.has(id)) return prev
        const next = new Set(prev)
        next.delete(id)
        return next
      })
      fetchPapers()
      fetchFacets()
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '删除论文失败' })
    }
  }, [fetchPapers, fetchFacets])

  const handleSearch = useCallback((query: string) => {
    setSearch(query)
    setPage(1)
  }, [])

  const handleSortChange = useCallback((next: string) => {
    setSort(next)
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
    fetchFacets()
  }, [fetchPapers, fetchFacets])

  const handleToggleGlobal = useCallback((v: boolean) => {
    setGlobalSearch(v)
    setPage(1)
  }, [])

  const handleToggleExpanded = useCallback((v: boolean) => {
    setIncludeExpanded(v)
    setPage(1)
  }, [])

  const toggleSelect = useCallback((id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const selectPage = useCallback((ids: number[]) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      ids.forEach(id => next.add(id))
      return next
    })
  }, [])

  const clearSelection = useCallback(() => setSelectedIds(new Set()), [])

  /** 批量删除。逐篇独立成败，最后只报一次结果，不逐条弹 toast。 */
  const handleBatchDelete = useCallback(async () => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    setBatchDeleteConfirm(false)
    setBatchBusy(true)
    try {
      const results = await Promise.allSettled(ids.map(id => deletePaper(id)))
      const failed = results.filter(r => r.status === 'rejected').length
      results.forEach((result, i) => { if (result.status === 'fulfilled') forgetReadingPage(ids[i]) })
      if (failed === 0) toast({ type: 'success', message: `已删除 ${ids.length} 篇论文` })
      else toast({ type: 'error', message: `${ids.length - failed} 篇已删除，${failed} 篇失败` })
      clearSelection()
      fetchPapers()
      fetchFacets()
    } finally {
      setBatchBusy(false)
    }
  }, [selectedIds, clearSelection, fetchPapers, fetchFacets])

  /** 批量加入工作区。已在该工作区里的论文由后端幂等处理，这里只汇总成败。 */
  const handleBatchAddToWorkspace = useCallback(async (workspaceId: number) => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0 || !workspaceId) return
    const workspace = workspaces.find(w => w.id === workspaceId)
    setBatchBusy(true)
    try {
      const results = await Promise.allSettled(ids.map(id => addPaperToWorkspace(workspaceId, id)))
      const failed = results.filter(r => r.status === 'rejected').length
      const name = workspace?.name ?? '工作区'
      if (failed === 0) toast({ type: 'success', message: `已把 ${ids.length} 篇论文加入「${name}」` })
      else toast({ type: 'error', message: `${ids.length - failed} 篇已加入「${name}」，${failed} 篇失败` })
      clearSelection()
    } finally {
      setBatchBusy(false)
    }
  }, [selectedIds, workspaces, clearSelection])

  const selection = useMemo(() => ({
    ids: selectedIds,
    toggle: toggleSelect,
    selectPage,
    clear: clearSelection,
    actions: (
      <>
        {workspaces.length > 0 && (
          <select
            className="er-select er-select--sm"
            value=""
            disabled={batchBusy}
            aria-label="把所选论文加入工作区"
            onChange={e => {
              const id = Number(e.target.value)
              e.target.value = ''
              if (id) handleBatchAddToWorkspace(id)
            }}
          >
            <option value="">加入工作区…</option>
            {workspaces.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        )}
        <button
          type="button"
          className="er-btn er-btn--ghost er-btn--destructive er-btn--sm"
          disabled={batchBusy}
          aria-busy={batchBusy}
          onClick={() => setBatchDeleteConfirm(true)}
        >
          删除所选
        </button>
      </>
    ),
  }), [selectedIds, toggleSelect, selectPage, clearSelection, workspaces, batchBusy, handleBatchAddToWorkspace])

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
      setReindexNote(`正在重新索引 ${queued} 篇论文${includeExpanded ? '（含联网扩展论文）' : ''}…`)
      const pollSource = includeExpanded ? 'all' : 'uploaded'
      const started = Date.now()
      const poll = async () => {
        const res = await listPapers({ source: pollSource, per_page: 200 }).catch(() => null)
        const indexing = res ? res.papers.filter(p => p.index_status === 'indexing').length : 0
        fetchPapers()
        fetchFacets()
        if (includeExpanded) fetchExpandedPapers()
        const elapsed = Date.now() - started
        if (indexing === 0 && elapsed > 4000) {
          setReindexing(false)
          setReindexNote('重新索引完成')
          setTimeout(() => setReindexNote(null), 4000)
          return
        }
        if (elapsed > 180000) {
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
  }, [reindexing, fetchPapers, fetchExpandedPapers, fetchFacets, includeExpanded])

  const activeCount = searchResults !== null ? searchTotal : total
  const currentPageReadCount = searchResults === null
    ? papers.filter(p => p.read_level === 'full').length
    : 0

  return (
    <div className="er-page er-page--library">
      <header className="er-page-head">
        <p className="er-page-head__eyebrow">论文档案库 · 检索与管理</p>
        <h1 className="er-page-head__title">论文库</h1>
        <div className="er-page-head__actions">
          {reindexNote && <span className="arc-note" role="status">{reindexNote}</span>}
          <Tooltip
            content="重新索引会对每篇论文清空旧索引、用 GROBID 重新提取元数据并覆盖写回。默认处理论文库里的所有论文；勾选「含扩展」后会一并重做深度研究发现的论文。"
            delay={600}
            placement="bottom"
          >
            {/* hover 交给 CSS —— 原先用 onMouseEnter/onMouseLeave 手写内联
                背景与边框，禁用态时还得靠 if 分支避开，逻辑与样式混在一起。 */}
            <button
              type="button"
              onClick={handleReindex}
              disabled={reindexing}
              aria-busy={reindexing}
              className="er-btn er-btn--secondary er-btn--sm"
            >
              <svg
                className={reindexing ? 'animate-spin' : undefined}
                fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {reindexing ? '索引中…' : '重新索引'}
            </button>
          </Tooltip>
        </div>
      </header>

      <div className="er-page-head__readouts">
        <span className="er-readout"><b>{activeCount}</b>{searchResults !== null ? '篇命中' : '篇在库'}</span>
        {expandedTotal > 0 && (
          <span className="er-readout"><b>{expandedTotal}</b>篇联网扩展</span>
        )}
        {searchResults === null && (
          <span className="er-readout">
            <b>{currentPageReadCount}</b>篇已精读（当前页）
          </span>
        )}
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
        facets={facets}
        sort={sort}
        onSortChange={handleSortChange}
        selection={selection}
      />

      <ConfirmDialog
        open={batchDeleteConfirm}
        title="批量删除论文"
        message={`确定删除所选的 ${selectedIds.size} 篇论文？删除后不可恢复。`}
        variant="danger"
        confirmLabel="删除"
        cancelLabel="取消"
        onConfirm={handleBatchDelete}
        onCancel={() => setBatchDeleteConfirm(false)}
      />

      {/* 深度研究发现区块 —— 折叠面板，展开后在自己的滚动区里列出扩展论文 */}
      {expandedTotal > 0 && (
        <section className="arc-expanded" aria-labelledby="arc-expanded-title">
          <button
            type="button"
            className="arc-expanded__handle"
            aria-expanded={showExpanded}
            aria-controls="arc-expanded-body"
            onClick={() => setShowExpanded(v => !v)}
          >
            <span className="arc-expanded__caret" aria-hidden="true" />
            <span id="arc-expanded-title" className="arc-expanded__label">深度研究发现的论文</span>
            <span className="er-tag er-tag--mute">{expandedTotal}</span>
            <span className="arc-expanded__hint">
              {showExpanded ? '收起' : '展开'}
            </span>
          </button>

          {showExpanded && (
            <div className="arc-expanded__body" id="arc-expanded-body">
              <p className="arc-expanded__note">
                深度研究联网下载的论文。点击「加入论文库」可将其移入主论文库。
              </p>
              <div className="er-scroll arc-expanded__scroll">
                <div className="er-ledger">
                  {expandedPapers.map((p, i) => (
                    <ExpandedPaperRow
                      key={p.id}
                      paper={p}
                      index={i}
                      onPromote={handlePromote}
                      onSelect={handleSelect}
                    />
                  ))}
                </div>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
