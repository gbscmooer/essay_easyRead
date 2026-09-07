import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Trash2, X, Save } from 'lucide-react'
import type { Paper, SearchResult } from '@/types'
import { listPapers, deletePaper, smartSearch, getPaperFacets, refreshMetadata, getMetadataStatus,
  updatePaperMetadata, promoteExpandedPaper, type PaperFacets, type MetadataProgress, type MetadataEdit } from '@/api/papers'
import { listWorkspaces, addPaperToWorkspace, type WorkspaceItem } from '@/api/workspaces'
import { PaperList, type FilterParams, PUBLICATION_TYPES } from '@/features/library/PaperList'
import { ConfirmDialog } from '@/ds-entry'
import { toast } from '@/hooks/useToast'
import { loadLibrarySort, saveLibrarySort } from '@/utils/libraryPrefs'
import { forgetReadingPage } from '@/utils/readerPrefs'
import '@/styles/library.css'

function MetadataEditor({ paper, onClose, onSaved }: { paper: Paper; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<MetadataEdit>({ title: paper.title || paper.filename, authors: paper.authors || '',
    venue: paper.venue || '', year: paper.year, publication_type: paper.publication_type || 'unknown',
    subjects: paper.subjects || [], publisher: paper.publisher || '' })
  const [subjects, setSubjects] = useState(form.subjects.join(', '))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <dialog className="arc-editor" ref={el => { if (el && !el.open) el.showModal() }} onCancel={onClose} aria-label="编辑论文信息">
    <form onSubmit={async e => {
      e.preventDefault(); setBusy(true); setError('')
      try {
        await updatePaperMetadata(paper.id, { ...form, subjects: subjects.split(/[,，;；\n]/).map(s => s.trim()).filter(Boolean) })
        onSaved(); onClose()
      } catch (err) { setError(err instanceof Error ? err.message : '保存失败') }
      finally { setBusy(false) }
    }}>
      <header><h2>编辑论文信息</h2><button type="button" className="arc-icon" aria-label="关闭编辑" title="关闭" onClick={onClose}><X size={18} /></button></header>
      <label>标题<input required maxLength={1000} value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} /></label>
      <label>作者<input maxLength={4000} value={form.authors} onChange={e => setForm({ ...form, authors: e.target.value })} /></label>
      <div className="arc-editor-grid"><label>发表类型<select value={form.publication_type} onChange={e => setForm({ ...form, publication_type: e.target.value })}>
        {Object.entries(PUBLICATION_TYPES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label><label>发表年份<input type="number" min={1900} max={new Date().getFullYear() + 1} value={form.year ?? ''} onChange={e => setForm({ ...form, year: e.target.value ? Number(e.target.value) : null })} /></label></div>
      <label>会议 / 期刊<input maxLength={500} value={form.venue} onChange={e => setForm({ ...form, venue: e.target.value })} /></label>
      <label>出版社<input maxLength={500} value={form.publisher} onChange={e => setForm({ ...form, publisher: e.target.value })} /></label>
      <label>研究方向<input value={subjects} placeholder="计算机视觉, 机器学习" onChange={e => setSubjects(e.target.value)} /></label>
      {error && <p role="alert">{error}</p>}
      <footer><button type="submit" className="er-btn er-btn--primary" disabled={busy}><Save size={16} />{busy ? '保存中…' : '保存'}</button></footer>
    </form>
  </dialog>
}

export default function LibraryPage() {
  const navigate = useNavigate()
  const [papers, setPapers] = useState<Paper[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState<FilterParams>({})
  const [globalSearch, setGlobalSearch] = useState(false)
  const [includeExpanded, setIncludeExpanded] = useState(false)
  const [results, setResults] = useState<SearchResult[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [facets, setFacets] = useState<PaperFacets | null>(null)
  const [sort, setSort] = useState(() => loadLibrarySort('recent', ['recent', 'oldest', 'year_desc', 'year_asc', 'title']))
  const [refresh, setRefresh] = useState(0)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editing, setEditing] = useState<Paper | null>(null)
  const [progress, setProgress] = useState<MetadataProgress | null>(null)
  const [starting, setStarting] = useState(false)
  const perPage = 20
  const reload = useCallback(() => setRefresh(v => v + 1), [])
  useEffect(() => {
    if (!loading && !error) setPage(current => Math.min(current, Math.max(1, Math.ceil(total / perPage))))
  }, [loading, error, total])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    const query = search.trim()
    const load = async () => {
      try {
        if (query) {
          const response = await smartSearch(query, { ...filters, global: globalSearch, includeExpanded, page, perPage }, controller.signal)
          if (!controller.signal.aborted) { setResults(response.results); setTotal(response.total) }
        } else {
          const response = await listPapers({ ...filters, date_added: filters.dateAdded, source: includeExpanded ? 'all' : 'uploaded', page, per_page: perPage, sort }, controller.signal)
          if (!controller.signal.aborted) { setResults(null); setPapers(response.papers); setTotal(response.total) }
        }
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '加载论文失败')
      } finally { if (!controller.signal.aborted) setLoading(false) }
    }
    void load()
    return () => controller.abort()
  }, [filters, globalSearch, includeExpanded, page, search, sort, refresh])
  useEffect(() => {
    let active = true
    getPaperFacets(includeExpanded ? 'all' : 'uploaded').then(res => { if (active) setFacets(res) }).catch(() => { if (active) setFacets(null) })
    return () => { active = false }
  }, [includeExpanded, refresh])
  useEffect(() => { listWorkspaces().then(setWorkspaces).catch(() => setWorkspaces([])) }, [])
  useEffect(() => { saveLibrarySort(sort) }, [sort])
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const state = await getMetadataStatus()
        if (!active) return
        setProgress(state)
        if (state.running) timer = setTimeout(poll, 2500)
        else if (state.total) reload()
      } catch { if (active) setProgress(null) }
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [progress?.running, reload])
  const onDelete = useCallback(async (id: number) => {
    try { await deletePaper(id); forgetReadingPage(id); setSelected(ids => new Set([...ids].filter(v => v !== id))); reload() }
    catch (err) { toast({ type: 'error', message: err instanceof Error ? err.message : '删除失败' }) }
  }, [reload])
  const batch = async (action: (id: number) => Promise<unknown>, deleting = false) => {
    setBusy(true); setConfirmDelete(false)
    const ids = [...selected]
    const responses = await Promise.allSettled(ids.map(action))
    const failed = ids.filter((_, i) => responses[i].status === 'rejected')
    if (deleting) ids.forEach((id, i) => { if (responses[i].status === 'fulfilled') forgetReadingPage(id) })
    setSelected(new Set(failed)); setBusy(false); reload()
    toast({ type: failed.length ? 'error' : 'success', message: `完成 ${ids.length - failed.length} 篇${failed.length ? `，失败 ${failed.length} 篇` : ''}` })
  }
  const selection = useMemo(() => ({ ids: selected,
    toggle: (id: number) => setSelected(old => { const next = new Set(old); if (next.has(id)) next.delete(id); else next.add(id); return next }),
    selectPage: (ids: number[]) => setSelected(old => new Set([...old, ...ids])), clear: () => setSelected(new Set()),
    actions: <>
      <select className="er-select er-select--sm" aria-label="把所选论文加入工作区" value="" disabled={busy} onChange={e => { const id = Number(e.target.value); if (id) void batch(pid => addPaperToWorkspace(id, pid)) }}>
        <option value="">加入工作区…</option>{workspaces.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
      </select>
      <button type="button" className="arc-icon" title="删除所选" aria-label="删除所选" disabled={busy} onClick={() => setConfirmDelete(true)}><Trash2 size={17} /></button>
    </>,
  }), [selected, workspaces, busy])
  const onSearch = useCallback((value: string) => { setSearch(value); setPage(1) }, [])
  const onFilter = useCallback((value: FilterParams) => { setFilters(value); setPage(1) }, [])
  return <div className="er-page er-page--library">
    <header className="arc-header"><div><h1>论文库</h1><span>{total} 篇{search.trim() ? '匹配结果' : includeExpanded ? '论文（含扩展）' : '已入库论文'}</span></div>
      <button type="button" className="er-btn er-btn--secondary" disabled={starting || progress?.running} onClick={async () => {
        setStarting(true)
        try { setProgress(await refreshMetadata(includeExpanded)) }
        catch (err) { toast({ type: 'error', message: err instanceof Error ? err.message : '补全失败' }) }
        finally { setStarting(false) }
      }}><RefreshCw size={16} className={progress?.running ? 'animate-spin' : undefined} />{progress?.running ? '补全中…' : '补全元数据'}</button>
    </header>
    {!!progress?.total && <div className="arc-progress" role="status">{progress.running ? `正在补全 ${progress.completed}/${progress.total}` : `已处理 ${progress.completed} 篇${progress.unmatched ? `，${progress.unmatched} 篇未匹配到新信息` : ''}`}</div>}
    {error && <div className="arc-error" role="alert">{error}<button onClick={reload}>重试</button></div>}
    <PaperList papers={papers} total={total} onSelect={id => navigate(`/reader/${id}`)} onDelete={onDelete}
      onSearch={onSearch} onFilter={onFilter} onPageChange={setPage} page={page} perPage={perPage}
      globalSearch={globalSearch} onToggleGlobal={value => { setGlobalSearch(value); setPage(1) }}
      includeExpanded={includeExpanded} onToggleExpanded={value => { setIncludeExpanded(value); setPage(1); setSelected(new Set()) }}
      searchResults={results} searchLoading={loading} searchQuery={search} facets={facets} sort={sort}
      onSortChange={value => { setSort(value); setPage(1) }} selection={selection} onEdit={setEditing}
      onPromote={async id => { try { await promoteExpandedPaper(id); reload() } catch { toast({ type: 'error', message: '加入失败' }) } }} />
    {editing && <MetadataEditor key={editing.id} paper={editing} onClose={() => setEditing(null)} onSaved={reload} />}
    <ConfirmDialog open={confirmDelete} title="批量删除论文" message={`确定删除所选 ${selected.size} 篇论文？删除后不可恢复。`} variant="danger"
      confirmLabel="删除" cancelLabel="取消" onConfirm={() => void batch(deletePaper, true)} onCancel={() => setConfirmDelete(false)} />
  </div>
}
