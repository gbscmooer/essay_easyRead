/**
 * 文献研究页（/explore、/explorer/:id）—— 单循环研究的入口与结果
 *
 * 流程（backend services/deep_research/loop）：写检索式 → 多源检索 → Selector 逐篇判定（2/1/0）→
 * 展开高分论文（读章节名、追所选章节的引用）→ 补检 → 计量/聚类/图谱 → 指针式作答（每句带原文摘录）。
 *
 * 数据流：POST /api/exploration/runs → SSE（stage_* / queries_ready / crawl_step / metrics_ready / answer_ready / done）。
 * 结束的研究通过 SSE 回放产物事件。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import '@/styles/exploration-detail.css'
import '@/styles/survey-report.css'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { createPortal } from 'react-dom'
import { ArrowLeft, ChevronDown, Download, FileText, History, MessageSquare } from 'lucide-react'
import { PaperTitle, paperLink, EvidenceMarkdown } from '@/features/research/paperLinks'
import { ChartView } from '@/features/research/ChartView'
import { ExplorationConversation } from '@/features/research/ExplorationConversation'
import { RunTokenUsageCard } from '@/features/research/RunTokenUsageCard'
import { RevisionPanel } from '@/features/research/RevisionPanel'
import { PauseAdjust } from '@/features/research/PauseAdjust'
import { listWorkspaces, getWorkspace, type WorkspaceItem, type WorkspaceDetail } from '@/api/workspaces'
import { toast } from '@/hooks/useToast'
import { DomainOverview } from '@/features/research/DomainOverview'
import { useExplorationStream, type LiveCrawl } from '@/hooks/useExplorationStream'
import { formatDuration, mergeRunTiming, resolveTimelineEndMs, TIMING_STAGE_ORDER } from '@/features/research/runTiming'
import { parseServerDate } from '@/utils/datetime'
import {
  cancelExploration, createExploration, deleteExploration, getExploration, getExplorationMessages, listExplorations,
  pauseExploration, preflightExploration, reportUrl, resumeExploration,
} from '@/api/exploration'
import type {
  CitationMethod, CrawlReport, DirectAnswer, ExplorationDepth, ExplorationKeywordTrend, ExplorationMessage,
  ExplorationPaper, ExplorationResult, ExplorationRun, ExplorationRunSummary, GraphNode, PreflightResponse, ResearchGraph,
} from '@/types'
import { clusterInk, inkWash } from '@/theme/graphPalette'
import { ConfirmDialog } from '@/ds-entry'

const EXAMPLES = [
  '用 AI 识别肿瘤有没有人做？做得怎样？',
  '多模态大模型在医学影像诊断上进展如何？',
  '状态空间模型（Mamba 一类）会取代 Transformer 吗？',
]

// 与 backend loop/config.py 的 PRESETS 对应
const DEPTHS: { value: ExplorationDepth; label: string; hint: string }[] = [
  { value: 'lite', label: '轻量', hint: '4 条检索式 · 展开 1 层 × 5 篇 · 约 10 分钟' },
  { value: 'quick', label: '快览', hint: '5 条检索式 · 展开 1 层 × 8 篇 + 补检 · 约 15 分钟' },
  { value: 'standard', label: '标准', hint: '6 条检索式 · 展开 2 层 × 10 篇 + 补检 · 约 25 分钟' },
  { value: 'deep', label: '深挖', hint: '8 条检索式 · 展开 2 层 × 20 篇 + 补检 · 约 30 分钟' },
]

const STAGE_META: { key: string; label: string; hint: string }[] = [
  { key: 'search', label: '① 检索与判定', hint: '写检索式、多源检索，每篇论文由 Selector 判定' },
  { key: 'expand', label: '② 引用展开', hint: '展开高分论文：读章节名、追所选章节的引用' },
  { key: 'landscape', label: '③ 计量与图谱', hint: '选中论文的年份、引用、聚类与图谱' },
  { key: 'answer', label: '④ 作答', hint: '起草答案，逐句查找原文证据' },
]

const ORIGIN_LABELS: Record<string, string> = {
  search: '检索', refine: '补检', expand: '引用展开', seed: '母论文', reuse: '复用',
}
const LABEL_TEXT: Record<number, string> = { 2: '直接相关', 1: '背景相关', 0: '无关' }

const SOURCE_LABELS: Record<string, string> = {
  scopus: 'Scopus', openalex: 'OpenAlex', arxiv: 'arXiv', semantic_scholar: 'Semantic Scholar', local: '本地',
}

const HEALTH_STATUS_LABELS: Record<string, string> = {
  auth_error: '无权限', rate_limited: '限速中', network_error: '网络不通', error: '异常', unconfigured: '未配置',
}

const STOP_REASONS: Record<string, string> = {
  layers_done: '展开层数用完', no_frontier: '没有可展开的高分论文', time_cap: '到达时长上限',
  no_expansion: '未展开（离线或关闭展开）',
}

function sourceLabel(name: string): string {
  return SOURCE_LABELS[name] ?? name
}

function fmtCitations(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 10000) return `${(n / 1000).toFixed(0)}k`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

function isExactCitationMethod(method: CitationMethod | string | undefined): method is CitationMethod {
  return typeof method === 'object' && method !== null
    && method.algorithm === 'conditional_poisson_rate_test'
    && method.version === 1 && method.adjustment === 'benjamini_hochberg'
}

export default function ExplorePage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const initialWorkspace = Number(searchParams.get('workspace_id')) || null
  const { id } = useParams()
  const runId = id && /^\d+$/.test(id) && Number.isSafeInteger(Number(id)) && Number(id) > 0 ? Number(id) : null
  const detail = id !== undefined
  const [question, setQuestion] = useState('')
  const [depth, setDepth] = useState<ExplorationDepth>('standard')
  const [savedRun, setSavedRun] = useState<ExplorationRun | null>(null)
  const [loadError, setLoadError] = useState('')
  const [tab, setTab] = useState<'report' | 'analysis' | 'papers' | 'chat'>('report')
  const [revision, setRevision] = useState(0)
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [workspaceId, setWorkspaceId] = useState<number | null>(initialWorkspace)
  const [workspace, setWorkspace] = useState<WorkspaceDetail | null>(null)
  const [seedIds, setSeedIds] = useState<number[]>([])
  const [searchOnline, setSearchOnline] = useState(true)
  const [sourceError, setSourceError] = useState('')
  const [sourceLoading, setSourceLoading] = useState(false)
  useEffect(() => { setWorkspaceId(initialWorkspace) }, [initialWorkspace])
  const [messages, setMessages] = useState<ExplorationMessage[]>([])
  const [paperSearch, setPaperSearch] = useState('')
  const [history, setHistory] = useState<ExplorationRunSummary[]>([])
  const [deleteTarget, setDeleteTarget] = useState<ExplorationRunSummary | null>(null)
  const [selected, setSelected] = useState<ExplorationPaper | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [preflight, setPreflight] = useState<PreflightResponse | null>(null)
  const [preflightLoading, setPreflightLoading] = useState(false)

  const stream = useExplorationStream(runId, revision)
  const result: ExplorationResult | null = stream.result ?? savedRun?.result ?? null
  const answer: DirectAnswer | null = stream.answer ?? result?.answer ?? null
  const crawl: CrawlReport | null = result?.crawl ?? null
  const hasLandscape = Boolean(result && Array.isArray(result.papers) && result.metrics && result.graph)
  const running = stream.status === 'running' || submitting
  const paused = stream.status === 'paused'
  const busy = running || paused
  const checkpoint = savedRun?.checkpoint_summary ?? null

  const runPreflight = useCallback(async (signal?: AbortSignal) => {
    setPreflightLoading(true)
    try { setPreflight(await preflightExploration(signal)) } catch { /* 预检失败不阻塞提问 */ }
    finally { setPreflightLoading(false) }
  }, [])
  useEffect(() => {
    if (detail) return
    const controller = new AbortController()
    void runPreflight(controller.signal)
    return () => controller.abort()
  }, [detail, runPreflight])

  useEffect(() => {
    setSavedRun(null); setLoadError(''); setMessages([]); setQuestion(''); setTab('report')
    if (!detail) return
    if (runId === null) { setLoadError('研究编号无效'); return }
    const controller = new AbortController()
    getExploration(runId, controller.signal).then(run => {
      if (!controller.signal.aborted) setSavedRun(run)
    }).catch(e => { if (!controller.signal.aborted) setLoadError(e instanceof Error ? e.message : '研究加载失败') })
    return () => controller.abort()
  }, [detail, runId])

  useEffect(() => {
    if (!detail) listWorkspaces().then(setWorkspaces).catch(() => setSourceError('工作区列表加载失败'))
  }, [detail])
  useEffect(() => {
    let active = true
    setWorkspace(null); setSeedIds([]); setSourceError('')
    if (!workspaceId) { setSearchOnline(true); setSourceLoading(false); return }
    setSourceLoading(true)
    getWorkspace(workspaceId).then(ws => {
      if (active) { setWorkspace(ws); setSeedIds(ws.papers.filter(p => (p.level ?? 1) === 1).map(p => p.id)) }
    }).catch(() => { if (active) setSourceError('工作区资料加载失败') })
      .finally(() => { if (active) setSourceLoading(false) })
    return () => { active = false }
  }, [workspaceId])

  const allPapers = useMemo(() => {
    const map = new Map<string, ExplorationPaper>()
    result?.papers?.forEach(p => map.set(p.key, p))
    messages.forEach(m => m.papers?.forEach(p => map.set(p.key, { ...map.get(p.key), ...p })))
    return [...map.values()]
  }, [result, messages])

  const refreshHistory = useCallback(async () => {
    try { setHistory(await listExplorations(20)) } catch { /* 历史加载失败不打断主流程 */ }
  }, [])

  useEffect(() => { void refreshHistory() }, [refreshHistory])
  useEffect(() => { if (stream.status === 'done') void refreshHistory() }, [stream.status, refreshHistory])
  useEffect(() => {
    if (runId && ['done', 'paused', 'failed'].includes(stream.status)) {
      void getExploration(runId).then(setSavedRun).catch(() => {})
    }
  }, [runId, stream.status])
  useEffect(() => { setSelected(null) }, [runId])
  useEffect(() => { setHistoryOpen(false) }, [detail, runId])
  useEffect(() => {
    if (!historyOpen || detail || history.length === 0) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setHistoryOpen(false) }
    document.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [historyOpen, detail, history.length])

  const submit = useCallback(async () => {
    const q = question.trim()
    if (q.length < 2) { toast({ type: 'error', message: '请先输入研究问题' }); return }
    setSubmitting(true)
    try {
      if (workspaceId && !seedIds.length) throw new Error('请至少选择一篇母论文')
      const run = await createExploration(q, depth, { workspace_id: workspaceId, seed_paper_ids: workspaceId ? seedIds : null, search_online: searchOnline })
      navigate(`/explorer/${run.id}`)
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '研究创建失败' })
    } finally {
      setSubmitting(false)
    }
  }, [question, depth, navigate, workspaceId, seedIds, searchOnline])

  const [pauseRequested, setPauseRequested] = useState(false)
  useEffect(() => { if (stream.status !== 'running') setPauseRequested(false) }, [stream.status])
  const pauseRun = useCallback(async () => {
    if (!runId) return
    setPauseRequested(true)
    try {
      const res = await pauseExploration(runId)
      toast({ type: 'info', message: res.detail || '已请求暂停' })
    } catch (e) {
      setPauseRequested(false)
      toast({ type: 'error', message: e instanceof Error ? e.message : '暂停请求失败' })
    }
  }, [runId])

  const resumeRun = useCallback(async () => {
    if (!runId) return
    try {
      await resumeExploration(runId)
      setRevision(v => v + 1)
      void refreshHistory()
    } catch (e) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '继续研究失败' })
    }
  }, [runId, refreshHistory])

  const cancelRun = useCallback(async () => {
    if (!runId) return
    try {
      await cancelExploration(runId)
      setRevision(v => v + 1)
      void refreshHistory()
    } catch (e) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '取消失败' })
    }
  }, [runId, refreshHistory])

  const removeRun = useCallback(async (id: number) => {
    try {
      await deleteExploration(id)
      if (runId === id) navigate('/explore')
      void refreshHistory()
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '删除失败' })
    }
  }, [runId, refreshHistory, navigate])

  // 证据定位：graph.nodes[i] ↔ papers[i]（后端按同序构建）
  const handleSelectNode = useCallback((node: GraphNode | null) => {
    if (!node || !result?.graph) { setSelected(null); return }
    const idx = result.graph.nodes.findIndex(n => n.id === node.id)
    setSelected(idx >= 0 ? result.papers[idx] ?? null : null)
  }, [result])

  const overviewGraph: ResearchGraph | null = useMemo(() => {
    if (!result?.graph || !result.graph.nodes.length) return null
    return { workspace_id: 0, run_id: runId ?? 0, nodes: result.graph.nodes, edges: result.graph.edges,
      stats: result.graph.stats, updated_at: new Date().toISOString() }
  }, [result, runId])

  const papersByKey = useMemo(() => {
    const m = new Map<string, ExplorationPaper>()
    result?.papers?.forEach(p => m.set(p.key, p))
    return m
  }, [result])

  const total = result?.papers?.length ?? 0
  const citationMethod = result?.metrics?.citation_method
  const exactCitations = isExactCitationMethod(citationMethod)
  const selectedMethod = selected?.citation_metrics ?? citationMethod
  const selectedExact = isExactCitationMethod(selectedMethod)
  const trendTitle = selectedExact
    ? `最近两个完整年份与此前两年比较：精确 Poisson 率检验，BH 校正，α = ${selectedMethod.alpha ?? '未记录'}。α 是显著性阈值，不是准确率。`
    : '未记录检验方法。'

  const currentStageMeta = useMemo(() => {
    if (submitting) return STAGE_META[0]
    if (!stream.stage) return null
    return STAGE_META.find(s => stream.stage?.startsWith(s.key)) ?? null
  }, [stream.stage, submitting])

  const displayProgress = useMemo(() => {
    if (stream.status === 'done') return 100
    if (submitting) return 4
    if (typeof stream.progress === 'number' && stream.progress > 0) return stream.progress
    return stream.status === 'running' ? 6 : 0
  }, [stream.status, stream.progress, submitting])

  const exportContext = async () => {
    if (!runId) return
    try {
      const [run, turns] = await Promise.all([getExploration(runId), getExplorationMessages(runId)])
      const archive = new Map((run.result?.papers ?? []).map(p => [p.key, p]))
      turns.forEach(m => m.papers?.forEach(p => archive.set(p.key, { ...archive.get(p.key), ...p })))
      const blob = new Blob([JSON.stringify({ ...run, messages: turns, papers: [...archive.values()] }, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = url; a.download = `research-${runId}.json`; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '导出失败' })
    }
  }

  const flowLabel = !savedRun ? '' : savedRun.status === 'done' ? '已完成'
    : savedRun.status === 'cancelled' ? '已取消' : savedRun.status === 'failed' ? '失败'
    : savedRun.status === 'paused' ? '已暂停' : '运行中'

  return (
    <div className="er-page er-page--explore">
      {detail && (
        <Link to="/explore" className="ex-back-link">
          <ArrowLeft size={14} aria-hidden="true" />
          返回文献研究
        </Link>
      )}
      <header className="er-page-head">
        <p className="er-page-head__eyebrow">
          {detail ? `文献研究 · #${runId}` : '文献研究 · 检索、逐篇判定、追引用、带原文证据的回答'}
        </p>
        <h1 className={`er-page-head__title${detail ? ' ex-detail-title' : ''}`}>
          {detail ? savedRun?.question ?? '研究详情' : '文献研究'}
        </h1>
        {detail && savedRun && (
          <div className="er-page-head__actions">
            <a className="er-btn er-btn--ghost er-btn--sm" href={reportUrl(runId!, 'markdown')}>导出 Markdown</a>
            <a className="er-btn er-btn--ghost er-btn--sm" href={reportUrl(runId!, 'html')}>导出 HTML</a>
            <button type="button" className="er-btn er-btn--ghost er-btn--sm" title="导出全部上下文 (JSON)" aria-label="导出全部上下文" onClick={exportContext}>
              <Download size={14} aria-hidden="true" />
              导出数据
            </button>
          </div>
        )}
      </header>
      {detail && savedRun && (
        <div className="er-page-head__readouts ex-detail-readouts">
          <span className="er-readout">{parseServerDate(savedRun.created_at).toLocaleString()}</span>
          <span className="er-readout">研究 <b>#{runId}</b></span>
          <span className="er-readout">选中 <b>{total}</b> 篇{crawl ? ` / 候选 ${crawl.candidates} 篇` : ''}</span>
          <span className="er-readout">{flowLabel}{answer ? (answer.status === 'failed' ? ' · 未能作答' : ' · 已作答') : ''}</span>
          <span className="er-readout">
            {savedRun.options?.workspace_name || '无母论文'}
            {savedRun.options?.seed_paper_ids?.length ? ` · ${savedRun.options.seed_paper_ids.length} 篇母论文` : ''}
          </span>
          {savedRun.options?.workspace_id && (
            <Link to={`/workspace/${savedRun.options.workspace_id}/graph`} className="er-link">工作区图谱 ↗</Link>
          )}
        </div>
      )}
      {detail && savedRun && <RunTimingPanel run={savedRun} stream={stream} />}
      {detail && savedRun && total === 0 && !running && !paused && stream.status !== 'failed' && (
        <div className="ex-empty-warning" role="alert">
          <div className="ex-empty-warning__header">
            <span className="ex-empty-warning__badge">未选中论文</span>
            <strong>本次检索没有论文被判为相关</strong>
          </div>
          <p className="ex-empty-warning__desc">
            {crawl?.candidates ? `共检索到 ${crawl.candidates} 篇候选，Selector 均判为无关。` : '数据源没有返回候选论文。'}
            {' '}可以换用标准学术英文术语重新提问，或在设置页检查数据源与模型配置。零命中不等于该领域不存在相关工作。
          </p>
          <div className="ex-empty-warning__actions">
            <Link to="/explore" className="er-btn er-btn--primary er-btn--sm">重新发起研究</Link>
          </div>
        </div>
      )}
      {loadError && <div role="alert" className="ex-empty ex-empty--error">{loadError}</div>}

      {!detail && <section className="ex-ask" aria-label="研究提问">
        <textarea
          className="ex-ask__input"
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit() } }}
          placeholder="描述研究问题、关注的技术、评估方式或数据集"
          aria-label="研究问题"
          maxLength={4000}
          rows={2}
          disabled={running}
        />
        <div className="research-source-controls">
          <label>母论文集合<select aria-label="母论文集合" value={workspaceId ?? ''} disabled={running} onChange={e => setWorkspaceId(Number(e.target.value) || null)}>
            <option value="">无母论文</option>{workspaces.map(ws => <option value={ws.id} key={ws.id}>{ws.name} ({ws.paper_count} 篇)</option>)}
          </select></label>
          <label><input type="checkbox" checked={searchOnline} disabled={!workspaceId || running} onChange={e => setSearchOnline(e.target.checked)} />联网补充文献</label>
          {workspaceId && <Link className="research-workspace-link" to={`/workspace/${workspaceId}/graph`}>已有工作区图谱</Link>}
        </div>
        {sourceLoading && <p role="status">正在加载母论文…</p>}
        {sourceError && <p role="alert" className="survey-error">{sourceError}</p>}
        <div className="ex-preflight" aria-label="数据源预检">
          <span className="ex-preflight__label">数据源体检</span>
          {preflightLoading && !preflight ? (
            <span className="ex-preflight__hint">探测各源访问权限中…</span>
          ) : (
            <>
              {(preflight?.sources ?? []).map(s => (
                <span key={s.name} className={`ex-preflight__chip is-${s.status}`}
                  title={`${sourceLabel(s.name)} · ${s.role === 'primary' ? '首选源' : s.role === 'supplement' ? '补充源' : '回退源'} · ${s.detail ?? ''}`}>
                  <i className="ex-preflight__dot" aria-hidden="true" />
                  {sourceLabel(s.name)}
                  {s.status === 'ok' && s.latency_ms != null && <em>{s.latency_ms}ms</em>}
                  {s.status !== 'ok' && <em>{HEALTH_STATUS_LABELS[s.status] ?? s.status}</em>}
                </span>
              ))}
              {!preflight && <span className="ex-preflight__hint">暂未探测</span>}
              <button type="button" className="ex-preflight__refresh" onClick={() => void runPreflight()}
                disabled={preflightLoading || running} aria-label="重新探测数据源" title="重新用真实最小检索探测各数据源权限">↻</button>
            </>
          )}
        </div>
        {workspace && <details className="research-seeds"><summary>母论文 · 已选 {seedIds.length} / {workspace.papers.length} 篇（会作为展开起点）</summary>
          <div className="research-seed-actions"><button type="button" onClick={() => setSeedIds(workspace.papers.map(p => p.id))}>全选</button><button type="button" onClick={() => setSeedIds([])}>清空选择</button></div>
          <div className="research-seed-list">{workspace.papers.map(p => <label key={p.id}><input type="checkbox" checked={seedIds.includes(p.id)} onChange={e => setSeedIds(prev => e.target.checked ? [...prev, p.id] : prev.filter(id => id !== p.id))} />{p.title || p.filename}</label>)}</div>
        </details>}
        <div className="ex-ask__row">
          <div className="ex-ask__depth" role="radiogroup" aria-label="研究深度">
            {DEPTHS.map(d => (
              <button key={d.value} type="button" role="radio" aria-checked={depth === d.value}
                className={depth === d.value ? 'is-active' : ''} onClick={() => setDepth(d.value)} disabled={running} title={d.hint}>
                {d.label}
              </button>
            ))}
          </div>
          <button type="button" className="ex-ask__run" onClick={() => void submit()} disabled={running || sourceLoading || !!workspaceId && (!workspace || !seedIds.length)}>
            {running ? '研究中…' : '开始研究'}
          </button>
        </div>
        <div className="ex-ask__examples">
          {EXAMPLES.map(ex => <button key={ex} type="button" onClick={() => setQuestion(ex)} disabled={running}>{ex}</button>)}
        </div>
      </section>}

      {(stream.status === 'running' || paused || submitting) && (
        <section className="ex-progress" aria-label="研究进度">
          <div className="ex-progress__header">
            <div className="ex-progress__title-group">
              <span className="ex-progress__spinner" aria-hidden="true" />
              <div>
                <div className="ex-progress__title">
                  {submitting ? '正在启动研究…' : paused ? '研究已暂停'
                    : currentStageMeta?.label ? `正在进行：${currentStageMeta.label}` : '研究进行中…'}
                </div>
                <div className="ex-progress__subtitle">
                  {paused
                    ? (checkpoint ? `断点已保留（候选 ${checkpoint.candidates} 篇，已展开 ${checkpoint.expanded} 篇，剩余检索式 ${checkpoint.queries_todo} 条），可随时继续` : '断点已保留，可随时继续')
                    : (stream.message || currentStageMeta?.hint || '')}
                </div>
              </div>
            </div>
            <div className="ex-progress__meta">
              <span className="ex-progress__percent tabular-nums font-mono font-semibold">{displayProgress}%</span>
              {!submitting && stream.status === 'running' && (
                <button type="button" className="er-btn er-btn--ghost er-btn--sm" onClick={() => void pauseRun()} disabled={pauseRequested}
                  title="当前动作结束后停止，断点保留">{pauseRequested ? '暂停中…' : '暂停'}</button>
              )}
              {(running || paused) && !submitting && (
                <button type="button" className="er-btn er-btn--ghost er-btn--sm" onClick={() => void cancelRun()}>取消研究</button>
              )}
              {paused && <button type="button" className="er-btn er-btn--primary er-btn--sm" onClick={() => void resumeRun()}>继续研究</button>}
            </div>
          </div>
          <div className="ex-progress__bar-track" role="progressbar" aria-valuenow={displayProgress} aria-valuemin={0} aria-valuemax={100} aria-label="研究总体进度">
            <div className="ex-progress__bar-fill" style={{ width: `${Math.min(100, Math.max(displayProgress, 4))}%` }} />
          </div>
          <ol className="ex-progress__stages">
            {STAGE_META.map((s, idx) => {
              const order = STAGE_META.map(x => x.key)
              const cur = order.findIndex(k => (stream.stage ?? '').startsWith(k))
              const done = (stream.stage ?? '').startsWith(`${s.key} 完成`) || (cur >= 0 && idx < cur)
              const active = stream.stage === s.key || (idx === 0 && (submitting || !stream.stage))
              return (
                <li key={s.key} className={active ? 'is-active' : done ? 'is-done' : ''} title={s.hint}>
                  <span className="ex-progress__dot" aria-hidden="true">{done ? '✓' : ''}</span>
                  <span className="ex-progress__stage-label">{s.label}</span>
                </li>
              )
            })}
          </ol>
          <LiveCrawlPanel crawl={stream.crawl} />
        </section>
      )}

      {!detail && history.length > 0 && (
        <section className="ex-history" aria-label="历史研究">
          <button type="button" className="ex-history__toggle" aria-haspopup="dialog" aria-expanded={historyOpen} onClick={() => setHistoryOpen(v => !v)}>
            <History size={15} aria-hidden="true" />
            最近研究
            <span className="ex-history__count">{history.length}</span>
            <ChevronDown size={14} aria-hidden="true" className={historyOpen ? 'is-open' : undefined} />
          </button>
        </section>
      )}

      {!detail && createPortal(
        <div className="ex-drawer" hidden={!historyOpen || history.length === 0}>
          <div className="ex-drawer__backdrop" onClick={() => setHistoryOpen(false)} />
          <aside className="ex-drawer__panel" role="dialog" aria-modal="true" aria-label="最近研究">
            <header className="ex-drawer__head">
              <span>最近研究</span>
              <button type="button" className="ex-drawer__close" aria-label="关闭最近研究" onClick={() => setHistoryOpen(false)}>×</button>
            </header>
            <ul className="ex-drawer__list">
              {history.map(h => (
                <li key={h.id}>
                  <Link to={`/explorer/${h.id}`} onClick={() => setHistoryOpen(false)}>
                    <span className="ex-drawer__q">{h.question}</span>
                    <span className="ex-drawer__meta">
                      {h.status === 'failed' ? '⚠ 失败 · ' : ''}
                      {h.status === 'paused' ? '⏸ 已暂停 · ' : ''}
                      {h.status === 'done' ? `✓ 选中 ${h.selected ?? 0} 篇${h.answer_status && h.answer_status !== 'failed' ? ' · 已作答' : ''} · ` : ''}
                      {parseServerDate(h.created_at).toLocaleString()}
                    </span>
                  </Link>
                  <button type="button" className="ex-history__del" aria-label="删除该记录" onClick={() => setDeleteTarget(h)}
                    disabled={h.status === 'running' || h.status === 'pending'}>×</button>
                </li>
              ))}
            </ul>
          </aside>
        </div>,
        document.body,
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除研究记录"
        message={deleteTarget ? `确定永久删除「${deleteTarget.question}」及其结果和对话？此操作无法撤销。` : ''}
        confirmLabel="永久删除"
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (!deleteTarget) return
          const targetId = deleteTarget.id
          setDeleteTarget(null)
          void removeRun(targetId)
        }}
      />

      {detail && paused && runId && <PauseAdjust runId={runId} papers={allPapers} />}

      {(stream.status === 'failed' && stream.error) ? (
        <section className="ex-empty ex-empty--error" role="alert">
          <div>研究失败：{stream.error}</div>
          {checkpoint ? <p>已保留断点（候选 {checkpoint.candidates} 篇，已展开 {checkpoint.expanded} 篇），继续时从同一动作接着做。</p> : null}
          <div className="ex-empty-warning__actions">
            <button type="button" className="er-btn er-btn--primary er-btn--sm" onClick={() => void resumeRun()}>从断点继续研究</button>
            <Link to="/explore" className="er-btn er-btn--secondary er-btn--sm">重新发起研究</Link>
          </div>
        </section>
      ) : null}

      {detail && savedRun && <>
        {!running && !paused && <RevisionPanel run={savedRun} papers={allPapers} disabled={busy} onCreated={child => navigate(`/explorer/${child.id}`)} />}
        <div className="ex-detail-tabs" role="tablist" aria-label="研究视图">
          {([{ key: 'report', label: '回答与过程' }, { key: 'analysis', label: '领域概览' }, { key: 'papers', label: `选中论文 (${allPapers.length})` }, { key: 'chat', label: '连续追问' }] as const).map(t =>
            <button key={t.key} id={`tab-${t.key}`} type="button" role="tab" aria-selected={tab === t.key} aria-controls={`panel-${t.key}`} onClick={() => setTab(t.key)}>{t.label}</button>)}
        </div>
        <div id="panel-report" role="tabpanel" aria-labelledby="tab-report" hidden={tab !== 'report'}>
          {answer && <DirectAnswerCard answer={answer} papers={allPapers} />}
          {crawl && <CrawlPanel crawl={crawl} queries={result?.queries ?? []} papersByKey={papersByKey} warnings={result?.warnings ?? []} onSelect={p => { setSelected(p); setTab('papers') }} />}
          <RunTokenUsageCard runId={runId!} refreshKey={`${stream.status}-${revision}-${savedRun.updated_at ?? ''}`} />
        </div>
        <div id="panel-chat" role="tabpanel" aria-labelledby="tab-chat" hidden={tab !== 'chat'}>
          <ExplorationConversation key={runId} runId={runId!} papers={allPapers} disabled={busy}
            onMessages={setMessages} question={question} setQuestion={setQuestion} selected={selected} onClearSelection={() => setSelected(null)} />
        </div>
        <div id="panel-papers" role="tabpanel" aria-labelledby="tab-papers" hidden={tab !== 'papers'}>
          {selected && <EvidenceCard paper={selected} exact={selectedExact} trendTitle={trendTitle} onClose={() => setSelected(null)} onAsk={() => setTab('chat')} />}
          <input className="ex-paper-search" type="search" aria-label="搜索论文" placeholder="搜索标题、摘要或作者" value={paperSearch} onChange={e => setPaperSearch(e.target.value)} />
          <ul className="ex-paper-archive">{allPapers.filter(p => `${p.title} ${p.abstract ?? ''} ${p.authors ?? ''}`.toLowerCase().includes(paperSearch.toLowerCase())).map(p => <li key={p.key}>
            <PaperTitle paper={p} onSelect={() => setSelected(p)} />
            <span>{p.year ?? '年份未知'} · {p.authors ?? ''}</span>
            <span className="ex-paper-archive__tags">
              {p.label != null && <em title={`Selector 判定；排序分 ${p.score ?? '—'}`}>{LABEL_TEXT[p.label] ?? p.label}</em>}
              {p.origin && <em title={p.via || undefined}>{ORIGIN_LABELS[p.origin] ?? p.origin}{p.depth ? ` · 第 ${p.depth} 层` : ''}</em>}
            </span>
            <button type="button" className="ex-icon" title="针对论文提问" aria-label={`针对论文提问：${p.title}`} onClick={() => { setSelected(p); setTab('chat') }}><MessageSquare size={17} /></button>
            <details><summary>摘要与来源</summary><p>{p.abstract || '暂无摘要'}</p><p>{p.doi || p.arxiv_id || p.key}{p.via && p.origin !== 'search' ? ` · 来自：${p.via}` : ''}</p></details>
          </li>)}</ul>
          {!allPapers.length && <p>暂无论文</p>}
        </div>
      </>}

      {hasLandscape && result?.metrics && (
        <div className="ex-result" id="panel-analysis" role={detail ? 'tabpanel' : undefined} aria-labelledby={detail ? 'tab-analysis' : undefined} hidden={detail && tab !== 'analysis'}>
          <section className="ex-transparency" aria-label="选中论文统计">
            <div className="ex-transparency__stats">
              <span>选中 <strong>{total}</strong> 篇</span>
              {result.metrics.year_span?.length === 2 && <span>年份 <strong>{result.metrics.year_span[0]}–{result.metrics.year_span[1]}</strong></span>}
              <span>近年发表占比 <strong>{Math.round(result.metrics.recent_share * 100)}%</strong></span>
              <span>引用计量 <strong>{exactCitations ? '精确 Poisson + BH（v1）' : '未记录方法'}</strong></span>
              <span>子方向 <strong>{result.metrics.clusters}</strong> 个</span>
              {result.metrics.enriched && result.metrics.enriched.with_history > 0 && (
                <span title="获得逐年引用记录的论文数">逐年引用富化 <strong>{result.metrics.enriched.with_history}/{total}</strong></span>
              )}
            </div>
            {exactCitations && <p className="ex-heat__note">
              最近两个完整年份{citationMethod.recent_years?.length === 2 ? `（${citationMethod.recent_years.join('、')}）` : ''}与此前两年比较，
              精确 Poisson 率检验经 BH 多重检验校正；α = {citationMethod.alpha ?? '未记录'} 是显著性阈值，不是准确率。
            </p>}
          </section>

          {selected && !detail && <EvidenceCard paper={selected} exact={selectedExact} trendTitle={trendTitle} onClose={() => setSelected(null)} />}

          {overviewGraph && (
            <section className="ex-section" aria-label="领域总览">
              <h2 className="ex-section__title">领域总览 · 子方向 × 年份</h2>
              <DomainOverview graph={overviewGraph} synthesis={null} onSelectNode={node => { handleSelectNode(node); if (detail && node) setTab('papers') }}
                paperForNode={node => result.papers[result.graph!.nodes.findIndex(n => n.id === node.id)]} />
            </section>
          )}

          {result.metrics.venue_ranking && result.metrics.venue_ranking.length > 0 && (
            <section className="ex-section" aria-label="主阵地期刊">
              <h2 className="ex-section__title">主阵地期刊 · 选中论文集中的发表地</h2>
              <ul className="ex-venue">
                {result.metrics.venue_ranking.map(v => (
                  <li key={v.name}>
                    <span className="ex-venue__name" title={v.name}>{v.name}</span>
                    <span className="ex-venue__count">{v.count} 篇</span>
                    {v.sjr != null && <span className="ex-venue__metric" title="SCImago 期刊等级分">SJR {v.sjr}</span>}
                    {v.snip != null && <span className="ex-venue__metric" title="来源标准化影响因子">SNIP {v.snip}</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {result.metrics.keyword_trend && (result.metrics.keyword_trend.rows?.length ?? 0) > 0 && (
            <section className="ex-section" aria-label="关键词趋势热力图">
              <ChartView title="关键词 × 年份热力图"><KeywordHeatmap trend={result.metrics.keyword_trend} /></ChartView>
              <p className="ex-heat__note">颜色越深 = 当年含该关键词的新发表论文越多（选中论文内计数，≥3 篇才入图）</p>
            </section>
          )}

          {total > 0 && (
            <section className="ex-section" aria-label="证据列表">
              <h2 className="ex-section__title">证据列表 · 点击查看摘要与原文链接</h2>
              <div className="ex-evidence-cols">
                <EvidenceList title="样本内高被引" hint="按选中论文累计被引排序，不代表领域排名" keys={result.metrics.top_cited}
                  citationMethod={citationMethod} papersByKey={papersByKey} onSelect={p => { setSelected(p); if (detail) setTab('papers') }} />
                <EvidenceList title={exactCitations ? '引用率显著上升' : '引用速度排序'} hint="精确 Poisson 检验 + BH 校正；按最近两个完整年份年均被引排序"
                  keys={result.metrics.rising} citationMethod={citationMethod} papersByKey={papersByKey} onSelect={p => { setSelected(p); if (detail) setTab('papers') }} />
              </div>
            </section>
          )}
        </div>
      )}

      {!result && stream.status !== 'running' && !submitting && stream.status !== 'failed' && (
        <section className="ex-empty">
          {runId ? '该研究没有可展示的结果。'
            : '输入一个问题开始。系统会写检索式、多源检索并逐篇判定相关性，从高分论文的相关章节追引用，最后给出每句带原文摘录的回答，并附选中论文的计量与图谱。'}
        </section>
      )}
    </div>
  )
}

// ── 子组件 ────────────────────────────────────────────────────────────────

/** 运行中：检索式、候选/选中计数、最近的动作与新选中的论文 */
function LiveCrawlPanel({ crawl }: { crawl: LiveCrawl }) {
  if (!crawl.queries.length && !crawl.steps.length) return null
  return (
    <div className="ex-crawl ex-crawl--live" aria-label="实时检索过程">
      <p className="ex-crawl__counts">候选 <b>{crawl.candidates}</b> 篇 · 选中 <b>{crawl.selected}</b> 篇 · 动作 <b>{crawl.steps.length}</b> 次</p>
      {crawl.queries.length > 0 && <div className="ex-transparency__queries">{crawl.queries.map(q => <code key={q}>{q}</code>)}</div>}
      {crawl.recent.length > 0 && (
        <ul className="ex-crawl__recent">{crawl.recent.slice(0, 6).map(p => <li key={p.key}>{p.title}{p.label === 2 ? ' · 直接相关' : ''}</li>)}</ul>
      )}
    </div>
  )
}

/** 完成后：检索过程（检索式、每个动作的新增/选中、Selector 与 Crawler 的模型调用） */
function CrawlPanel({ crawl, queries, papersByKey, warnings, onSelect }: {
  crawl: CrawlReport
  queries: string[]
  papersByKey: Map<string, ExplorationPaper>
  warnings: string[]
  onSelect: (p: ExplorationPaper) => void
}) {
  const labels = crawl.selector?.labels ?? {}
  return (
    <section className="ex-section ex-crawl" aria-label="检索过程">
      <h2 className="ex-section__title">检索过程</h2>
      <div className="ex-transparency__stats">
        <span>候选 <strong>{crawl.candidates}</strong> 篇</span>
        <span>选中 <strong>{crawl.selected}</strong> 篇（直接相关 {labels['2'] ?? 0} · 背景相关 {labels['1'] ?? 0}）</span>
        <span>展开 <strong>{crawl.expanded}</strong> 篇（章节 {crawl.crawler?.expand_html ?? 0} · 参考文献列表 {crawl.crawler?.expand_reflist ?? 0}）</span>
        <span>停止：<strong>{STOP_REASONS[crawl.stop_reason] ?? crawl.stop_reason ?? '—'}</strong></span>
        <span title="Selector 判定调用 + Crawler（检索式/章节/补检）调用">模型调用 <strong>{(crawl.selector?.llm_calls ?? 0) + (crawl.crawler?.llm_calls ?? 0)}</strong> 次</span>
        {crawl.selector?.floor_rejected ? <span title="交叉编码器分过低，未花模型调用">粗排剔除 <strong>{crawl.selector.floor_rejected}</strong> 篇</span> : null}
      </div>
      {queries.length > 0 && <div className="ex-transparency__row">
        <span className="ex-transparency__label">检索式</span>
        <div className="ex-transparency__queries">{queries.map(q => <code key={q}>{q}</code>)}</div>
      </div>}
      {Object.keys(crawl.sources ?? {}).length > 0 && <p className="ex-heat__note">
        数据源：{Object.entries(crawl.sources).map(([name, s]) => `${sourceLabel(name)} ${s.hits} 篇${s.errors ? `（失败 ${s.errors} 次）` : ''}`).join(' · ')}
      </p>}
      {warnings.length > 0 && <ul className="ex-transparency__warn">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      <details>
        <summary>逐步动作（{crawl.trace.length}）</summary>
        <ol className="ex-crawl__trace">
          {crawl.trace.map((s, i) => {
            const paper = s.paper ? papersByKey.get(s.paper) : undefined
            return (
              <li key={i}>
                {s.action === 'expand'
                  ? <>展开 {paper ? <PaperTitle paper={paper} onSelect={() => onSelect(paper)} /> : (s.title ?? s.paper)}
                      {s.chosen?.length ? `（${s.chosen.join('、')}）` : s.mode === 'reference_list' ? '（参考文献列表）' : s.mode === 'none' ? '（无法展开）' : ''}</>
                  : <>{s.action === 'refine' ? '补检' : '检索'} <code>{s.query}</code></>}
                {' '}→ 新增 {s.new} · 选中 {s.selected}
              </li>
            )
          })}
        </ol>
      </details>
    </section>
  )
}

function EvidenceCard({ paper, exact, trendTitle, onClose, onAsk }: {
  paper: ExplorationPaper
  exact: boolean
  trendTitle: string
  onClose: () => void
  onAsk?: () => void
}) {
  return (
    <section className="ex-evidence-card" aria-label="证据详情">
      <div className="ex-evidence-card__head">
        <h3><PaperTitle paper={paper} /></h3>
        <button type="button" aria-label="关闭" onClick={onClose}>×</button>
      </div>
      <div className="ex-evidence-card__meta">
        {paper.label != null && <span>{LABEL_TEXT[paper.label] ?? paper.label}{paper.score != null ? ` · 排序分 ${paper.score}` : ''}</span>}
        {paper.origin && <span>{ORIGIN_LABELS[paper.origin] ?? paper.origin}{paper.via && paper.origin !== 'search' ? `：${paper.via}` : ''}</span>}
        {paper.year ? <span>{paper.year} 年</span> : null}
        <span>被引 {fmtCitations(paper.citation_count)}</span>
        {paper.citation_velocity_2y != null && <span>{exact ? '最近两个完整年份年均' : '近2年年均'} {paper.citation_velocity_2y}/年</span>}
        {paper.highly_cited && <span className="ex-surge">样本内同年高被引</span>}
        {paper.trajectory === 'rising' && <span title={trendTitle}>引用率显著上升</span>}
        {paper.trajectory === 'declining' && <span title={trendTitle}>引用率显著下降</span>}
        {paper.venue?.name && <span>{paper.venue.name}{paper.venue.sjr != null ? ` · SJR ${paper.venue.sjr}` : ''}</span>}
        {paper.cluster >= 0 && (
          <span><i className="ex-cluster-dot" style={{ background: clusterInk(paper.cluster) }} />{paper.cluster_label || `子方向 ${paper.cluster + 1}`}</span>
        )}
        {paper.authors && <span className="ex-evidence-card__authors">{paper.authors}</span>}
        {paperLink(paper) && <a href={paperLink(paper) ?? '#'} target="_blank" rel="noreferrer">打开原文 ↗</a>}
      </div>
      {paper.keywords && paper.keywords.length > 0 && <div className="ex-evidence-card__kws">{paper.keywords.map(k => <code key={k}>{k}</code>)}</div>}
      {paper.abstract && <p className="ex-evidence-card__abstract">{paper.abstract}</p>}
      {onAsk && <button type="button" className="er-btn er-btn--secondary" onClick={onAsk}><MessageSquare size={16} />针对这篇论文提问</button>}
    </section>
  )
}

/** 阶段配色（与 STAGE_META 顺序对应） */
const TIMING_COLORS = ['#3969a8', '#2f7d78', '#d3a52f', '#d94a38', '#7c5cbf', '#0e7490', '#b45309', '#6b7079']

/**
 * 研究计时面板：总耗时（墙钟，运行中走秒）+ 各阶段耗时分配堆叠条。
 * 数据 = 落库 stage_timeline（完成/暂停/失败阶段）⊕ 实时事件时间戳
 * （运行中阶段走秒；刷新后靠历史回放携带的原始 ts 复原）。
 */
function RunTimingPanel({ run, stream }: {
  run: ExplorationRun
  stream: ReturnType<typeof useExplorationStream>
}) {
  const active = stream.status === 'running'
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [active])

  const endedAtMs = useMemo(() => {
    if (active) return null
    const streamEnded = stream.status === 'done' || stream.status === 'failed' || stream.status === 'paused'
    const runEnded = ['done', 'cancelled', 'failed', 'paused'].includes(run.status)
    if (!streamEnded && !runEnded) return null
    // 完成态优先取最后一个闭合阶段的结束时刻；没有时间线才回退 updated_at。
    return resolveTimelineEndMs(run.result?.sources?.stage_timeline, stream.stageTimings)
      ?? parseServerDate(run.updated_at).getTime()
  }, [active, stream.status, stream.stageTimings, run.status, run.updated_at, run.result])

  const timing = useMemo(() => mergeRunTiming({
    createdAtMs: parseServerDate(run.created_at).getTime(),
    endedAtMs,
    nowMs,
    persisted: run.result?.sources?.stage_timeline,
    live: stream.stageTimings,
  }), [run.created_at, run.result, endedAtMs, nowMs, stream.stageTimings])

  const total = timing.stages.length
  const colorFor = (stage: string) =>
    TIMING_COLORS[Math.max(0, TIMING_STAGE_ORDER.indexOf(stage)) % TIMING_COLORS.length]

  return (
    <section className="ex-timing" aria-label="研究耗时">
      <div className="ex-timing__head">
        <span className="ex-timing__label">总耗时</span>
        <strong className="ex-timing__total tabular-nums">{formatDuration(timing.wallSeconds)}</strong>
        {active && <span className="ex-timing__live">计时中</span>}
        {total > 0 && (
          <span className="ex-timing__sum">各阶段合计 {formatDuration(timing.stageSeconds)}</span>
        )}
      </div>
      {total > 0 && (
        <>
          <div className="ex-timing__bar" role="img" aria-label="各阶段耗时占比">
            {timing.stages.map(s => (
              <i
                key={s.stage}
                title={`${s.label} ${formatDuration(s.seconds)}`}
                style={{ flexGrow: Math.max(s.seconds, 0.01), background: colorFor(s.stage) }}
              />
            ))}
          </div>
          <ul className="ex-timing__stages">
            {timing.stages.map(s => (
              <li key={s.stage} className={`is-${s.status}`} title={s.status === 'failed' ? '该阶段失败' : undefined}>
                <i className="ex-timing__dot" style={{ background: colorFor(s.stage) }} aria-hidden="true" />
                <span className="ex-timing__stage-label">{s.label}</span>
                <span className="ex-timing__stage-time tabular-nums">
                  {formatDuration(s.seconds)}
                  {s.status === 'running' && ' · 进行中'}
                  {s.status === 'paused' && ' · 已暂停'}
                  {s.status === 'failed' && ' · 失败'}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

function EvidenceList({
  title, hint, keys, papersByKey, onSelect, citationMethod,
}: {
  title: string
  hint: string
  keys: string[]
  papersByKey: Map<string, ExplorationPaper>
  onSelect: (p: ExplorationPaper) => void
  citationMethod?: CitationMethod | string
}) {
  const papers = keys.map(k => papersByKey.get(k)).filter((p): p is ExplorationPaper => Boolean(p))
  return (
    <div className="ex-evlist">
      <div className="ex-evlist__head" title={hint}>{title}</div>
      <ol>
        {papers.map((p, i) => (
          <li key={p.key}>
            <div className="ex-evlist__row">
              <span className="ex-evlist__idx">{i + 1}</span>
              <PaperTitle paper={p} className="ex-evlist__title" onSelect={() => onSelect(p)} />
              <span className="ex-evlist__nums">
                {p.year ?? '—'} · 被引{fmtCitations(p.citation_count)}
                {p.citation_velocity != null && ` · 累计年均 ${p.citation_velocity}/年`}
                {p.recent_citations != null && ` · ${isExactCitationMethod(p.citation_metrics ?? citationMethod) ? '完整两年被引' : '旧版2y'} ${fmtCitations(p.recent_citations)}`}
                {p.highly_cited && <span title="仅限本次样本内同年排名"> · 样本高被引</span>}
                {p.surge && <span title={isExactCitationMethod(p.citation_metrics ?? citationMethod) ? '精确 Poisson 检验 + BH 校正' : '旧版启发式标记，未重新检验'}>
                  {isExactCitationMethod(p.citation_metrics ?? citationMethod) ? ' · 显著上升' : ' · 旧版候选'}
                </span>}
              </span>
              <button type="button" className="ex-icon" aria-label={`查看摘要：${p.title}`} title="查看摘要" onClick={() => onSelect(p)}><FileText size={16} /></button>
            </div>
          </li>
        ))}
        {papers.length === 0 && <li className="ex-evlist__empty">暂无</li>}
      </ol>
    </div>
  )
}

function KeywordHeatmap({ trend }: { trend: ExplorationKeywordTrend }) {
  const { years, rows } = trend
  if (!rows.length) return null
  const max = Math.max(...rows.flatMap(r => r.counts), 1)
  // 年份列数动态：经 --heat-cols 注入 grid 模板（repeat 的次数参数）
  const gridStyle = { '--heat-cols': years.length } as React.CSSProperties
  return (
    <div className="ex-heat" role="table" aria-label="关键词与年份交叉的热力图">
      <div className="ex-heat__row ex-heat__row--head" role="row" style={gridStyle}>
        <span className="ex-heat__kw" role="columnheader" aria-label="关键词" />
        {years.map(y => (
          <span key={y} className="ex-heat__year" role="columnheader">{String(y).slice(2)}’</span>
        ))}
        <span className="ex-heat__total" role="columnheader" title="语料内总篇数">Σ</span>
      </div>
      {rows.map(r => (
        <div key={r.keyword} className="ex-heat__row" role="row" style={gridStyle}>
          <span className="ex-heat__kw" role="rowheader" title={r.keyword}>{r.keyword}</span>
          {r.counts.map((c, i) => (
            <span
              key={i}
              className="ex-heat__cell"
              role="cell"
              style={c ? { background: inkWash('#3969a8', 16 + Math.round(68 * Math.sqrt(c / max))) } : undefined}
            >
              {c > 0 ? c : ''}
            </span>
          ))}
          <span className="ex-heat__total" role="cell">{r.total}</span>
        </div>
      ))}
    </div>
  )
}

/** 直接回答：每句以 [n] 引用材料，参考文献附原文摘录（便于逐句核查）。 */
function DirectAnswerCard({ answer, papers }: { answer: DirectAnswer; papers: ExplorationPaper[] }) {
  const citations = Object.fromEntries(answer.references.map(r => [String(r.n), r.key]))
  // 指针式作答会引用本次召回之外的论文（镜像检索、点名代表作）：参考文献本身也参与链接解析
  const linkable = [...papers, ...answer.references.map(r => ({ key: r.key, title: r.title }))]
  const statusLabel = answer.status === 'needs_review' ? '引用待复核'
    : answer.status === 'insufficient' ? '资料不足' : answer.status === 'failed' ? '生成失败' : null
  return (
    <section className="ex-answer" aria-label="直接回答">
      <div className="ex-empty-warning__header">
        <strong>回答</strong>
        {statusLabel && <span className="ex-integrity__badge">{statusLabel}</span>}
      </div>
      {answer.text
        ? <div className="ex-answer__body"><EvidenceMarkdown text={answer.text} papers={linkable} citations={citations} /></div>
        : <p className="ex-empty-warning__desc">未能生成回答{answer.error ? `（${answer.error}）` : ''}；选中的论文与检索过程仍可在下方查看。</p>}
      {answer.references.length > 0 && (
        <ol className="ex-answer__refs" aria-label="直接回答的参考文献">
          {answer.references.map(r => (
            <li key={r.n} value={r.n}>
              <span className="ex-answer__ref-title">{r.title}{r.year ? `（${r.year}）` : ''}</span>
              {(r.excerpts?.length ? r.excerpts : [r.excerpt]).map((e, i) => (
                <q key={i} className="ex-answer__excerpt">{e}</q>
              ))}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
