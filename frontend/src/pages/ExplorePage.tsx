/**
 * 领域探索页（/explore）—— GOAL.md 核心命题的产品入口
 *
 * 学者提一个自然语言问句（「用 AI 识别肿瘤有没有人做，做得怎样」），系统自动：
 * 拆解检索式 → 多源检索（Scopus 首选 / OpenAlex 回退）→ 计量与聚类（纯算法）→
 * LLM 跨文献综合。全程免读单篇论文，结论挂到本轮证据材料。
 *
 * 数据流：POST /api/exploration/runs → SSE（stage_* / recall_progress / metrics_ready /
 * synthesis_ready / done）。历史 run 通过 SSE 回放（后端 done 状态会重发产物事件）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import '@/styles/exploration-detail.css'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { createPortal } from 'react-dom'
import { ArrowLeft, ChevronDown, Download, FileText, History, MessageSquare } from 'lucide-react'
import { PaperTitle, paperLink, EvidenceMarkdown } from '@/features/research/paperLinks'
import { ChartView } from '@/features/research/ChartView'
import { ExplorationConversation } from '@/features/research/ExplorationConversation'
import { SurveyReportView } from '@/features/research/SurveyReportView'
import { RunTokenUsageCard } from '@/features/research/RunTokenUsageCard'
import { ResearchQuestions } from '@/features/research/ResearchQuestions'
import { ResearchRevision } from '@/features/research/ResearchRevision'
import { LegacyRevision } from '@/features/research/LegacyRevision'
import { PauseAdjust } from '@/features/research/PauseAdjust'
import { listWorkspaces, getWorkspace, type WorkspaceItem, type WorkspaceDetail } from '@/api/workspaces'
import { toast } from '@/hooks/useToast'
import { DomainOverview } from '@/features/research/DomainOverview'
import { useExplorationStream } from '@/hooks/useExplorationStream'
import { formatDuration, mergeRunTiming, resolveTimelineEndMs, TIMING_STAGE_ORDER } from '@/features/research/runTiming'
import { parseServerDate } from '@/utils/datetime'
import { createExploration, deleteExploration, listExplorations, getExploration, getExplorationMessages, preflightExploration, regenerateReport, pauseExploration, resumeExploration, cancelExploration, retryExploration } from '@/api/exploration'
import type {
  ExplorationDepth, ExplorationKeywordTrend, ExplorationPaper, ExplorationRunSummary,
  GraphNode, GraphSynthesis, NoveltyStatus, ResearchGraph,
  ExplorationRun, ExplorationResult, RunIntegrity, ExplorationMessage, CitationMethod, PreflightResponse,
} from '@/types'
import { clusterInk, inkWash } from '@/theme/graphPalette'
import { ConfirmDialog } from '@/ds-entry'

const EXAMPLES = [
  '用 AI 识别肿瘤有没有人做？做得怎样？',
  '多模态大模型在医学影像诊断上进展如何？',
  '状态空间模型（Mamba 一类）会取代 Transformer 吗？',
]

const DEPTHS: { value: ExplorationDepth; label: string; hint: string; loopHint: string }[] = [
  { value: 'lite', label: '轻量', hint: '2 式 · 上限 50 篇', loopHint: '优先核心材料，提供简要回答与证据缺口' },
  { value: 'quick', label: '快览', hint: '3 式 · 上限 200 篇', loopHint: '围绕问题检索、阅读并补充关键依据' },
  { value: 'standard', label: '标准', hint: '4 式 · 上限 500 篇', loopHint: '为补充检索与原文分析提供更多研究空间' },
  { value: 'deep', label: '深挖', hint: '5 式 · 上限 1000 篇', loopHint: '提供更充分的研究空间，仍以实际材料为依据' },
]

const STAGE_META: { key: string; label: string; hint: string }[] = [
  { key: 'preflight', label: '⓪ 数据源预检', hint: '探测检索权限与来源可用性' },
  { key: 'protocol', label: '① 问题协议', hint: '冻结研究问题、检索范围与预算' },
  { key: 'recall', label: '② 多源检索', hint: '召回材料并保留来源审计' },
  { key: 'screen', label: '③ 材料筛选', hint: '筛选相关候选并计算样本指标' },
  { key: 'select', label: '④ 代表选材', hint: '选择互补的研究材料' },
  { key: 'extract', label: '⑤ 证据提取', hint: '阅读原文并保存结构化证据' },
  { key: 'synthesize', label: '⑥ 跨文献综合', hint: '基于已提取材料形成报告草稿' },
  { key: 'report', label: '⑦ 报告交付', hint: '冻结报告并保存版本' },
]

const VERDICT_META: Record<NoveltyStatus, { label: string; ink: string; note: string }> = {
  done_extensively: { label: '已被充分实现', ink: '#d94a38', note: '多个成熟实现，赛道拥挤' },
  done_partial: { label: '部分实现', ink: '#d3a52f', note: '有相关工作，未完全覆盖问题条件' },
  emerging: { label: '新兴方向', ink: '#2f7d78', note: '近 1~2 年出现，尚无公认范式' },
  gap: { label: '检索未见实现', ink: '#3969a8', note: '材料中无直接实现——注意区分检索失败' },
  unclear: { label: '证据不足', ink: '#6b7079', note: '现有材料不足以判定' },
}

const INTENT_LABEL: Record<string, string> = {
  novelty: '查新（有没有人做）',
  maturity: '成熟度（做到什么程度）',
  landscape: '全景（领域现状）',
}

const SOURCE_LABELS: Record<string, string> = {
  scopus: 'Scopus',
  openalex: 'OpenAlex',
  arxiv: 'arXiv',
  semantic_scholar: 'Semantic Scholar',
  local: '本地',
}

const HEALTH_STATUS_LABELS: Record<string, string> = {
  auth_error: '无权限',
  rate_limited: '限速中',
  network_error: '网络不通',
  error: '异常',
  unconfigured: '未配置',
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
  const [useQuestionLoop, setUseQuestionLoop] = useState(false)
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
  // 运行中/失败的 run 其 result 可能只写了检索审计（persist_sources 中途落盘），
  // 缺 papers/metrics 等字段；不完整的产物一律按"尚无结果"处理，避免渲染期崩溃。
  const rawResult = stream.result ?? savedRun?.result ?? null
  const planningFailed = savedRun?.workflow_version === 'academic_survey_v2' && rawResult?.survey_report?.method?.stop_reason === 'planning_failed'
  const selectionFailed = rawResult?.survey_report?.method?.stop_reason === 'material_selection_failed'
  const selectionEmpty = rawResult?.survey_report?.method?.stop_reason === 'material_selection_empty'
  const result = rawResult && Array.isArray(rawResult.papers)
    && rawResult.metrics && rawResult.decompose && rawResult.graph
    ? rawResult as ExplorationResult
    : null
  const synthesis = stream.synthesis ?? result?.synthesis ?? null
  const running = stream.status === 'running' || submitting
  const paused = stream.status === 'paused'
  const busy = running || paused
  const checkpoint = savedRun?.checkpoint_summary ?? null

  // 数据源预检：进入列表页即探测（带权限的真实最小请求），开始研究前就能看到 401/403
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
  const deadPrimary = preflight?.sources.find(s => s.role === 'primary' && s.status !== 'ok' && s.status !== 'unconfigured') ?? null
  const okFallback = preflight?.sources.find(s => s.role === 'fallback' && s.status === 'ok') ?? null

  useEffect(() => {
    setSavedRun(null); setLoadError(''); setMessages([]); setQuestion(''); setTab('report')
    if (!detail) return
    if (runId === null) { setLoadError('探索编号无效'); return }
    const controller = new AbortController()
    getExploration(runId, controller.signal).then(run => {
      if (!controller.signal.aborted) setSavedRun(run)
    }).catch(e => { if (!controller.signal.aborted) setLoadError(e instanceof Error ? e.message : '探索加载失败') })
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
    rawResult?.papers?.forEach(p => map.set(p.key, p))
    messages.forEach(m => m.papers?.forEach(p => map.set(p.key, { ...map.get(p.key), ...p })))
    return [...map.values()]
  }, [rawResult, messages])

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
  // 路由离开列表视图时收起抽屉
  useEffect(() => { setHistoryOpen(false) }, [detail, runId])
  // 抽屉打开期间：ESC 关闭 + 锁定页面滚动
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
    if (q.length < 2) { toast({ type: 'error', message: '请先输入想探索的问题' }); return }
    setSubmitting(true)
    try {
      if (workspaceId && !seedIds.length) throw new Error('请至少选择一篇母论文')
      const run = await createExploration(q, depth, { workspace_id: workspaceId, seed_paper_ids: workspaceId ? seedIds : null, search_online: searchOnline,
        workflow_version: useQuestionLoop ? 'academic_survey_v2' : 'legacy_v1' })
      navigate(`/explorer/${run.id}`)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '探索任务创建失败'
      toast({ type: 'error', message })
    } finally {
      setSubmitting(false)
    }
  }, [question, depth, navigate, workspaceId, seedIds, searchOnline, useQuestionLoop])

  const generateReport = async () => {
    if (!runId) return
    setSubmitting(true)
    try {
      setSavedRun(await regenerateReport(runId))
      setRevision(v => v + 1)
      setTab('report')
    } catch (e) { toast({ type: 'error', message: e instanceof Error ? e.message : '报告生成失败' }) }
    finally { setSubmitting(false) }
  }

  // 暂停：请求后在最近的安全点（阶段/轮次/篇目边界）停止，断点保留
  const [pauseRequested, setPauseRequested] = useState(false)
  useEffect(() => { if (stream.status !== 'running') setPauseRequested(false) }, [stream.status])
  const pauseRun = useCallback(async () => {
    if (!runId) return
    setPauseRequested(true)
    try {
      const res = await pauseExploration(runId)
      toast({ type: 'info', message: res.detail || '已请求暂停，将在安全点停止并保留断点' })
    } catch (e) {
      setPauseRequested(false)
      toast({ type: 'error', message: e instanceof Error ? e.message : '暂停请求失败' })
    }
  }, [runId])

  // 续传：跳过已完成阶段与已提取篇目，从断点继续；重连 SSE 观察进度
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

  const retryFailedTasks = useCallback(async () => {
    if (!runId) return
    try {
      await retryExploration(runId)
      setRevision(value => value + 1)
      void refreshHistory()
    } catch (error) {
      toast({ type: 'error', message: error instanceof Error ? error.message : '重试失败任务未完成' })
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
    if (!node || !result) { setSelected(null); return }
    const idx = result.graph.nodes.findIndex(n => n.id === node.id)
    setSelected(idx >= 0 ? result.papers[idx] ?? null : null)
  }, [result])

  const overviewGraph: ResearchGraph | null = useMemo(() => {
    if (!result || !result.graph.nodes.length) return null
    return {
      workspace_id: 0,
      run_id: runId ?? 0,
      nodes: result.graph.nodes,
      edges: result.graph.edges,
      stats: result.graph.stats,
      updated_at: new Date().toISOString(),
    }
  }, [result, runId])

  const overviewSynthesis: GraphSynthesis | null = useMemo(() => (
    synthesis && synthesis.paradigms.length
      ? { paradigms: synthesis.paradigms, enriched_nodes: {}, enriched_edges: {} }
      : null
  ), [synthesis])

  const papersByKey = useMemo(() => {
    const m = new Map<string, ExplorationPaper>()
    result?.papers.forEach(p => m.set(p.key, p))
    return m
  }, [result])

  const verdict = synthesis?.verdict
  const verdictMeta = verdict ? VERDICT_META[verdict.status] : null
  const total = result?.metrics.total ?? rawResult?.papers?.length ?? 0
  const citationMethod = result?.metrics.citation_method
  const exactCitations = isExactCitationMethod(citationMethod)
  const selectedMethod = selected?.citation_metrics ?? citationMethod
  const selectedExact = isExactCitationMethod(selectedMethod)
  const trendTitle = selectedExact
    ? `最近两个完整年份与此前两年比较：精确 Poisson 率检验，BH 校正，α = ${selectedMethod.alpha ?? '未记录'}。α 是显著性阈值，不是准确率。`
    : '旧版启发式指标，未按精确 Poisson 检验和 BH 校正重新计算。'

  const isQuestionLoop = savedRun?.workflow_version === 'academic_survey_v2'

  const currentStageMeta = useMemo(() => {
    if (submitting) return STAGE_META[0]
    if (!stream.stage) return null
    const matched = STAGE_META.find(s => stream.stage?.startsWith(s.key))
    return matched ?? null
  }, [stream.stage, submitting])

  const displayProgress = useMemo(() => {
    if (stream.status === 'done') return 100
    if (submitting) return 6
    if (typeof stream.progress === 'number' && stream.progress > 0) return stream.progress
    if (stream.status === 'running') return 10
    return 0
  }, [stream.status, stream.progress, submitting])

  const exportContext = async () => {
    if (!runId) return
    try {
      const [run, turns] = await Promise.all([getExploration(runId), getExplorationMessages(runId)])
      const archive = new Map((run.result?.papers ?? []).map(p => [p.key, p]))
      turns.forEach(m => m.papers?.forEach(p => archive.set(p.key, { ...archive.get(p.key), ...p })))
      const blob = new Blob([JSON.stringify({ ...run, messages: turns, papers: [...archive.values()] }, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = url; a.download = `exploration-${runId}.json`; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '导出失败' })
    }
  }

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
          {detail ? `文献研究 · 探索 #${runId}` : '领域探索 · 问句驱动的免读领域认知'}
        </p>
        <h1 className={`er-page-head__title${detail ? ' ex-detail-title' : ''}`}>
          {detail ? savedRun?.question ?? '研究详情' : '文献研究'}
        </h1>
        {detail && savedRun && (
          <div className="er-page-head__actions">
            <button
              type="button"
              className="er-btn er-btn--ghost er-btn--sm"
              title="导出全部上下文 (JSON)"
              aria-label="导出全部上下文"
              onClick={exportContext}
            >
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
          <span className="er-readout"><b>{allPapers.length}</b> 篇资料</span>
          <span className="er-readout">
            {(() => {
              // 流程状态与报告生成状态分开展示：流程 done 不代表正文已经生成。
              const flow = selectionEmpty ? '未形成阅读组合' : selectionFailed ? '选材未完成' : planningFailed ? '规划失败' : ['done', 'completed', 'partial'].includes(savedRun.status) ? '流程已完成'
                : savedRun.status === 'cancelled' ? '流程已取消'
                : savedRun.status === 'failed' ? '流程失败'
                : savedRun.status === 'paused' ? '流程已暂停' : '流程运行中'
              const report = savedRun.result?.survey_report
              const reportLabel = selectionEmpty ? ' · 尚无研究答案，具体缺口已保留' : selectionFailed ? ' · 召回材料已保留，尚无完整答案' : planningFailed ? ' · 尚未执行文献检索' : !report ? ''
                : report.status === 'no_results' ? ' · 检索未命中，无报告正文'
                : report.status === 'partial' ? ' · 报告部分生成'
                : report.status === 'unavailable' ? ' · 报告未生成'
                : report.quality_status === 'needs_review' ? (savedRun.workflow_version === 'academic_survey_v2' ? ' · 报告已生成（仍有证据缺口）' : ' · 报告已生成（结构待处理）')
                : ' · 报告已生成'
              return flow + reportLabel
            })()}
          </span>
          <span className="er-readout">
            {savedRun.options?.workspace_name || '无母论文'}
            {savedRun.options?.seed_paper_ids?.length ? ` · ${savedRun.options.seed_paper_ids.length} 篇母论文` : ''}
          </span>
          {savedRun.options?.workspace_id && (
            <Link to={`/workspace/${savedRun.options.workspace_id}/graph`} className="er-link">
              工作区图谱 ↗
            </Link>
          )}
        </div>
      )}
      {detail && savedRun && <RunTimingPanel run={savedRun} stream={stream} />}
      {detail && !running && result?.integrity && <IntegrityNotice integrity={result.integrity} />}
      {detail && savedRun && allPapers.length === 0 && !running && !paused && stream.status !== 'failed' && (
        <div className="ex-empty-warning" role="alert">
          <div className="ex-empty-warning__header">
            <span className="ex-empty-warning__badge">{planningFailed ? '规划失败' : '未检索到资料'}</span>
            <strong>{planningFailed ? '研究计划未通过校验，尚未执行文献检索' : '本次文献检索未在学术数据库中命中相关文献（0 篇资料）'}</strong>
          </div>
          <p className="ex-empty-warning__desc">
            {planningFailed ? '本次没有形成研究结论，不能据此判断是否存在相关文献。已保留此次运行及失败状态，可修正研究问题后重新运行。' : <>
            {result?.decompose?.fallback && result.decompose.fallback_reason && result.decompose.fallback_reason !== 'UNCONFIGURED_KEY'
              ? `原因提示：LLM 检索拆解未完成（${result.decompose.fallback_reason}），原句回退检索未命中结果。`
              : result?.decompose?.fallback
              ? '原因提示：未配置有效的 LLM Key，使用原句直接检索未在学术数据库中命中结果。'
              : '原因提示：所提问句关键词在当前数据源中未检索到匹配论文。'}
            {' '}系统仍已生成检索审计报告，列出实际搜索过的检索式、来源和建议补搜方向；
            零命中不等于该领域不存在相关工作。也可精简提问、使用标准学术英文术语，或在设置页检查数据源与模型配置。
            </>}
          </p>
          <div className="ex-empty-warning__actions">
            <Link to="/explore" className="er-btn er-btn--primary er-btn--sm">
              重新发起研究
            </Link>
            <button
              type="button"
              className="er-btn er-btn--secondary er-btn--sm"
              onClick={() => setTab(savedRun?.workflow_version === 'academic_survey_v2' ? 'report' : 'analysis')}
            >
              {savedRun?.workflow_version === 'academic_survey_v2' ? '查看检索记录报告' : '查看检索透明度与数据源体检'}
            </button>
          </div>
        </div>
      )}
      {loadError && <div role="alert" className="ex-empty ex-empty--error">{loadError}</div>}

      {/* 提问表单 */}
      {!detail && <section className="ex-ask" aria-label="探索提问">
        <textarea
          className="ex-ask__input"
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit() }
          }}
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
                <span
                  key={s.name}
                  className={`ex-preflight__chip is-${s.status}`}
                  title={`${sourceLabel(s.name)} · ${s.role === 'primary' ? '首选源' : s.role === 'supplement' ? '补充源' : '回退源'} · ${s.detail ?? ''}`}
                >
                  <i className="ex-preflight__dot" aria-hidden="true" />
                  {sourceLabel(s.name)}
                  {s.status === 'ok' && s.latency_ms != null && <em>{s.latency_ms}ms</em>}
                  {s.status !== 'ok' && <em>{HEALTH_STATUS_LABELS[s.status] ?? s.status}</em>}
                </span>
              ))}
              {!preflight && <span className="ex-preflight__hint">暂未探测</span>}
              <button
                type="button"
                className="ex-preflight__refresh"
                onClick={() => void runPreflight()}
                disabled={preflightLoading || running}
                aria-label="重新探测数据源"
                title="重新用真实最小检索探测各数据源权限"
              >↻</button>
            </>
          )}
          {deadPrimary && okFallback && (
            <span className="ex-preflight__note">首选源不可用，开始研究将自动回退 {sourceLabel(okFallback.name)}</span>
          )}
        </div>
        {workspace && <details className="research-seeds"><summary>母论文 · 已选 {seedIds.length} / {workspace.papers.length} 篇</summary>
          <div className="research-seed-actions"><button type="button" onClick={() => setSeedIds(workspace.papers.map(p => p.id))}>全选</button><button type="button" onClick={() => setSeedIds([])}>清空选择</button></div>
          <div className="research-seed-list">{workspace.papers.map(p => <label key={p.id}><input type="checkbox" checked={seedIds.includes(p.id)} onChange={e => setSeedIds(prev => e.target.checked ? [...prev, p.id] : prev.filter(id => id !== p.id))} />{p.title || p.filename}</label>)}</div>
        </details>}
        <div className="ex-ask__row">
          <div className="ex-ask__depth" role="radiogroup" aria-label="探索深度">
            {DEPTHS.map(d => (
              <button
                key={d.value}
                type="button"
                role="radio"
                aria-checked={depth === d.value}
                className={depth === d.value ? 'is-active' : ''}
                onClick={() => setDepth(d.value)}
                disabled={running}
                title={useQuestionLoop ? d.loopHint : d.hint}
              >
                {d.label}
              </button>
            ))}
          </div>
          <label><input type="checkbox" checked={useQuestionLoop} onChange={e => setUseQuestionLoop(e.target.checked)} /> 使用问题驱动研究（实验）</label>
          {useQuestionLoop && <p className="survey-note">围绕问题补搜、补读并展示依据与缺口；尚未完成独立质量对照评测。</p>}
          <button type="button" className="ex-ask__run" onClick={() => void submit()} disabled={running || sourceLoading || !!workspaceId && (!workspace || !seedIds.length)}>
            {running ? '研究中…' : '开始研究'}
          </button>
        </div>
        <div className="ex-ask__examples">
          {EXAMPLES.map(ex => (
            <button key={ex} type="button" onClick={() => setQuestion(ex)} disabled={running}>{ex}</button>
          ))}
        </div>
      </section>}

      {/* 探索进行中/已暂停：进度条与实时动态分析反馈 */}
      {(stream.status === 'running' || paused || submitting) && (
        <section className="ex-progress" aria-label="探索进度">
          <div className="ex-progress__header">
            <div className="ex-progress__title-group">
              <span className="ex-progress__spinner" aria-hidden="true" />
              <div>
                <div className="ex-progress__title">
                  {submitting
                    ? '正在启动领域探索…'
                    : paused
                    ? '研究已在安全点暂停'
                    : (savedRun?.workflow_version === 'academic_survey_v2' ? '正在补充问题所需依据' : (currentStageMeta?.label ? `正在进行：${currentStageMeta.label}` : '领域探索分析中…'))}
                </div>
                <div className="ex-progress__subtitle">
                  {paused
                    ? (checkpoint?.completed_stages?.length
                        ? `断点已保留（完成至 ${checkpoint.completed_stages.length} / 8 个阶段${checkpoint.report_extracted ? `，已缓存 ${checkpoint.report_extracted} 篇证据提取` : ''}），可随时继续`
                        : '断点已保留，可随时继续')
                    : (stream.message || (submitting ? '正在准备分析环境并连接学术检索总线…' : (currentStageMeta?.hint ?? '系统正在自动检索学术文献并计算文献计量指标')))}
                </div>
              </div>
            </div>
            <div className="ex-progress__meta">
              <span className="ex-progress__percent tabular-nums font-mono font-semibold">
                {savedRun?.workflow_version === 'academic_survey_v2' ? '按问题推进' : `${displayProgress}%`}
              </span>
              {!submitting && stream.status === 'running' && (
                <button
                  type="button"
                  className="er-btn er-btn--ghost er-btn--sm"
                  onClick={() => void pauseRun()}
                  disabled={pauseRequested}
                  title="在最近的安全点（阶段/轮次/篇目边界）停止研究，断点保留"
                >
                  {pauseRequested ? '暂停中…' : '暂停'}
                </button>
              )}
              {savedRun?.workflow_version === 'academic_survey_v2' && (running || paused) && <button type="button"
                className="er-btn er-btn--ghost er-btn--sm" onClick={() => {
                  if (runId) void cancelExploration(runId).then(() => setRevision(v => v + 1)).catch(e => toast({ type: 'error', message: e instanceof Error ? e.message : '取消失败' }))
                }}>取消研究</button>}
              {paused && (
                <button
                  type="button"
                  className="er-btn er-btn--primary er-btn--sm"
                  onClick={() => void resumeRun()}
                >
                  继续研究
                </button>
              )}
            </div>
          </div>

          {/* 进度条轨道与动态填充 */}
          <div
            className="ex-progress__bar-track"
            role="progressbar"
            aria-valuenow={displayProgress}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="探索总体进度"
          >
            <div
              className="ex-progress__bar-fill"
              style={{ width: `${Math.min(100, Math.max(displayProgress, 4))}%` }}
            />
          </div>

          {/* 阶段步骤指示器 */}
          {isQuestionLoop ? <p className="ex-progress__loop-note">按问题缺口安排检索、阅读与核查；补充依据后重新检查答案。</p> : <ol className="ex-progress__stages">
            {STAGE_META.map((s, idx) => {
              const done = (stream.stage ?? '').startsWith(`${s.key} 完成`) || isAfterStage(stream.stage, s.key, result ?? undefined, synthesis)
              const active = stream.stage === s.key || (idx === 0 && (submitting || !stream.stage))
              return (
                <li key={s.key} className={active ? 'is-active' : done ? 'is-done' : ''} title={s.hint}>
                  <span className="ex-progress__dot" aria-hidden="true">
                    {done ? '✓' : ''}
                  </span>
                  <span className="ex-progress__stage-label">{s.label}</span>
                </li>
              )
            })}
          </ol>}
        </section>
      )}

      {/* 历史记录：抽屉式二级菜单入口 */}
      {!detail && history.length > 0 && (
        <section className="ex-history" aria-label="历史探索">
          <button
            type="button"
            className="ex-history__toggle"
            aria-haspopup="dialog"
            aria-expanded={historyOpen}
            onClick={() => setHistoryOpen(v => !v)}
          >
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
                      {h.status === 'done' && h.report_status === 'partial' ? '◌ 报告部分生成 · ' : ''}
                      {h.status === 'done' && h.report_status === 'no_results' ? '◌ 检索未命中 · ' : ''}
                      {h.status === 'done' && h.report_status === 'completed' ? '✓ 报告已生成 · ' : ''}
                      {parseServerDate(h.created_at).toLocaleString()}
                    </span>
                  </Link>
                  <button
                    type="button"
                    className="ex-history__del"
                    aria-label="删除该记录"
                    onClick={() => setDeleteTarget(h)}
                    disabled={h.status === 'running' || h.status === 'pending'}
                  >×</button>
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
        message={deleteTarget
          ? `确定永久删除「${deleteTarget.question}」及其报告、证据和对话？此操作无法撤销。`
          : ''}
        confirmLabel="永久删除"
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (!deleteTarget) return
          const targetId = deleteTarget.id
          setDeleteTarget(null)
          void removeRun(targetId)
        }}
      />

      {detail && paused && runId && savedRun?.workflow_version !== 'academic_survey_v2' && <PauseAdjust runId={runId} papers={allPapers} />}

      {(stream.status === 'failed' && stream.error) ? (
        <section className="ex-empty ex-empty--error" role="alert">
          <div>探索失败：{stream.error}</div>
          {checkpoint?.completed_stages?.length ? (
            <p>已保留断点（完成 {checkpoint.completed_stages.length} / 8 个阶段
              {checkpoint.report_extracted ? `，已缓存 ${checkpoint.report_extracted} 篇证据提取` : ''}），
              继续时自动跳过已完成部分。</p>
          ) : null}
          <div className="ex-empty-warning__actions">
            <button type="button" className="er-btn er-btn--primary er-btn--sm" onClick={() => void resumeRun()}>
              从断点继续研究
            </button>
            <Link to="/explore" className="er-btn er-btn--secondary er-btn--sm">重新发起研究</Link>
          </div>
        </section>
      ) : null}

      {savedRun?.workflow_version === 'academic_survey_v2' && ['partial', 'failed'].includes(savedRun.status) && (
        <section className="ex-empty ex-empty--warning">
          <p>继续研究保留失败任务的记录；如需重新执行失败步骤，请显式重试。重试复用原输入和剩余预算。</p>
          <button type="button" className="er-btn er-btn--secondary er-btn--sm" onClick={() => void retryFailedTasks()}>重试失败任务</button>
        </section>
      )}

      {detail && savedRun && <>
        {savedRun.workflow_version === 'academic_survey_v2' && <ResearchQuestions runId={runId!} refreshKey={`${stream.status}-${revision}`} />}
        {savedRun.workflow_version === 'academic_survey_v2' && <ResearchRevision run={savedRun} onCreated={child => navigate(`/explorer/${child.id}`)} />}
        {savedRun.workflow_version !== 'academic_survey_v2' && !running && !paused && (
          <LegacyRevision run={savedRun} papers={allPapers} disabled={busy} onCreated={child => navigate(`/explorer/${child.id}`)} />)}
        <div className="ex-detail-tabs" role="tablist" aria-label="探索视图">
          {([{ key: 'report', label: '研究报告' }, { key: 'analysis', label: '分析结果' }, { key: 'papers', label: `全部资料 (${allPapers.length})` }, { key: 'chat', label: '连续追问' }] as const)
            .filter(t => t.key !== 'analysis' || savedRun.workflow_version !== 'academic_survey_v2').map(t =>
            <button key={t.key} id={`tab-${t.key}`} type="button" role="tab" aria-selected={tab === t.key} aria-controls={`panel-${t.key}`} onClick={() => setTab(t.key)}>{t.label}</button>)}
        </div>
        <div id="panel-report" role="tabpanel" aria-labelledby="tab-report" hidden={tab !== 'report'}>
          <RunTokenUsageCard
            runId={runId!}
            refreshKey={`${stream.status}-${revision}-${savedRun.updated_at ?? ''}`}
          />
          <SurveyReportView report={rawResult?.survey_report} runId={runId!} busy={busy} onGenerate={generateReport} error={rawResult?.report_error} />
        </div>
        <div id="panel-chat" role="tabpanel" aria-labelledby="tab-chat" hidden={tab !== 'chat'}>
          <ExplorationConversation key={runId} runId={runId!} papers={allPapers} disabled={busy}
            onMessages={setMessages} question={question} setQuestion={setQuestion} selected={selected} onClearSelection={() => setSelected(null)} />
        </div>
        <div id="panel-papers" role="tabpanel" aria-labelledby="tab-papers" hidden={tab !== 'papers'}>
          <input className="ex-paper-search" type="search" aria-label="搜索资料" placeholder="搜索标题、摘要或作者" value={paperSearch} onChange={e => setPaperSearch(e.target.value)} />
          <ul className="ex-paper-archive">{allPapers.filter(p => `${p.title} ${p.abstract ?? ''} ${p.authors ?? ''}`.toLowerCase().includes(paperSearch.toLowerCase())).map(p => <li key={p.key}>
            <PaperTitle paper={p} /><span>{p.year ?? '年份未知'} · {p.authors ?? ''}</span>
            <button type="button" className="ex-icon" title="针对论文提问" aria-label={`针对论文提问：${p.title}`} onClick={() => { setSelected(p); setTab('chat') }}><MessageSquare size={17} /></button>
            <details><summary>摘要与来源</summary><p>{p.abstract || '暂无摘要'}</p><p>{p.doi || p.arxiv_id || p.key}</p></details>
          </li>)}</ul>
          {!allPapers.length && <p>暂无资料</p>}
        </div>
      </>}

      {/* 结果 */}
      {result && (
        <div className="ex-result" id="panel-analysis" role={detail ? 'tabpanel' : undefined} aria-labelledby={detail ? 'tab-analysis' : undefined} hidden={detail && tab !== 'analysis'}>
          {/* 结论卡 */}
          {verdict && verdictMeta && (
            <section className="ex-verdict" aria-label="查新结论">
              <div className="ex-verdict__head">
                <span className="ex-verdict__badge" style={{ borderColor: verdictMeta.ink, color: verdictMeta.ink, background: inkWash(verdictMeta.ink, 10) }}>
                  {verdictMeta.label}
                </span>
                <span className="ex-verdict__note">{verdictMeta.note}</span>
              </div>
              <div className="ex-verdict__expl"><EvidenceMarkdown text={verdict.explanation} papers={allPapers} /></div>
              {synthesis?.maturity && (
                <div className="ex-verdict__maturity"><strong>实现程度：</strong><EvidenceMarkdown text={synthesis.maturity} papers={allPapers} /></div>
              )}
              {(verdict.evidence_keys?.length ?? 0) > 0 && (
                <div className="ex-verdict__evidence">
                  <span>支撑证据：</span>
                  {verdict.evidence_keys.map(k => {
                    const p = papersByKey.get(k)
                    if (!p) return null
                    return (
                      <span key={k}><PaperTitle paper={p} onSelect={() => setSelected(p)} /><button type="button" className="ex-icon" aria-label={`查看摘要：${p.title}`} title="查看摘要" onClick={() => setSelected(p)}><FileText size={15} /></button></span>
                    )
                  })}
                </div>
              )}
            </section>
          )}

          {/* 检索透明度：客观性的可见性 */}
          <section className="ex-transparency" aria-label="检索透明度">
            <div className="ex-transparency__row">
              <span className="ex-transparency__label">检索式（{INTENT_LABEL[result.decompose.intent] ?? result.decompose.intent}）</span>
              <div className="ex-transparency__queries">
                {(result.decompose.en_queries ?? []).map(q => <code key={q}>{q}</code>)}
              </div>
            </div>
            <div className="ex-transparency__stats">
              <span>数据源 <strong>{result.provider === 'stub' ? '测试' : result.provider}</strong></span>
              <span>去重后 <strong>{total}</strong> 篇</span>
              {result.metrics.year_span?.length === 2 && (
                <span>年份 <strong>{result.metrics.year_span[0]}–{result.metrics.year_span[1]}</strong></span>
              )}
              <span>近年发表占比 <strong>{Math.round(result.metrics.recent_share * 100)}%</strong></span>
              <span>引用计量 <strong>{exactCitations ? '精确 Poisson + BH（v1）' : citationMethod ? '未识别的方法版本' : '旧版指标（未重新计算）'}</strong></span>
              <span>子方向 <strong>{result.metrics.clusters}</strong> 个</span>
              {result.metrics.enriched && result.metrics.enriched.with_history > 0 && (
                <span title="获得逐年引用记录的论文数；不等于满足完整年份检验条件的论文数">
                  逐年引用富化 <strong>{result.metrics.enriched.with_history}/{total}</strong>
                </span>
              )}
            </div>
            {exactCitations && <p className="ex-heat__note">
              最近两个完整年份{citationMethod.recent_years?.length === 2 ? `（${citationMethod.recent_years.join('、')}）` : ''}与此前两年比较，
              精确 Poisson 率检验经 BH 多重检验校正；α = {citationMethod.alpha ?? '未记录'} 是显著性阈值，不是准确率。
              缺少完整记录时不判定趋势。检验依赖 Poisson 计数与多重检验假设，未检出变化不代表没有变化。
            </p>}
            {result.decompose.fallback && (
              <p className="ex-transparency__warn">
                {result.decompose.fallback_reason && result.decompose.fallback_reason !== 'UNCONFIGURED_KEY'
                  ? `LLM 检索拆解未成功（${result.decompose.fallback_reason}）：已回退使用原句直检索（统计视图照常可用）。`
                  : '未配置 LLM Key：跳过了检索式拆解与综合报告，使用原句直检索（统计视图照常可用）。'}
              </p>
            )}
            {(result.sources?.preflight ?? []).some(s => s.status !== 'ok' && s.status !== 'unconfigured') && (
              <p className="ex-transparency__warn">
                数据源预检：{(result.sources?.preflight ?? [])
                  .filter(s => s.status !== 'ok' && s.status !== 'unconfigured')
                  .map(s => `${sourceLabel(s.name)}（${s.detail || HEALTH_STATUS_LABELS[s.status] || s.status}）`)
                  .join('；')}——本次研究已自动回退可用数据源。
              </p>
            )}
            {total === 0 && (
              <p className="ex-transparency__warn">
                检索未命中：换一个更具体的表述，或在设置页检查检索源配置。系统不会用模型记忆填补空白。
              </p>
            )}
          </section>

          {/* 证据详情 */}
          {selected && (
            <section className="ex-evidence-card" aria-label="证据详情">
              <div className="ex-evidence-card__head">
                <h3><PaperTitle paper={selected} /></h3>
                <button type="button" aria-label="关闭" onClick={() => setSelected(null)}>×</button>
              </div>
              <div className="ex-evidence-card__meta">
                {selected.year ? <span>{selected.year} 年</span> : null}
                <span>被引 {fmtCitations(selected.citation_count)}</span>
                {selected.citation_velocity != null && <span title="累计被引除以日历年龄，仅为描述值，不用于显著性判断">累计年均 {selected.citation_velocity}/年</span>}
                {selected.recent_citations != null && (
                  <span title={selectedExact ? `最近两个完整年份：${selectedMethod.recent_years?.join('、') ?? '年份未记录'}` : '旧运行保存的窗口值，未按完整年份重算'}>
                    {selectedExact ? '最近两个完整年份被引' : '旧版近2年被引'} {fmtCitations(selected.recent_citations)}
                  </span>
                )}
                {selected.citation_velocity_2y != null && <span>
                  {selectedExact ? '最近两个完整年份年均' : '旧版近2年年均'} {selected.citation_velocity_2y}/年
                </span>}
                {selected.citation_percentile != null && selected.citation_percentile >= 90 && (
                  <span title="仅比较本次样本中同年发表论文，不代表领域总体排名或 ESI 高被引">
                    {selectedExact ? '样本内同年' : '旧版样本内同年'} P{selected.citation_percentile}
                  </span>
                )}
                {selected.highly_cited && <span className="ex-surge">{selectedExact ? '样本内同年高被引' : '旧版样本内高被引'}</span>}
                {selected.trajectory === 'rising' && <span title={trendTitle}>{selectedExact ? '引用率显著上升' : '旧版引用爬升'}</span>}
                {selected.trajectory === 'declining' && <span title={trendTitle}>{selectedExact ? '引用率显著下降' : '旧版引用回落'}</span>}
                {selected.trajectory === 'steady' && <span title={trendTitle}>{selectedExact ? '未检出显著变化' : '旧版平稳标记'}</span>}
                {selected.surge && !selectedExact && <span className="ex-surge" title={trendTitle}>旧版突增候选</span>}
                {selected.venue?.name && (
                  <span title={selected.venue.sjr != null ? 'SJR = SCImago 期刊等级分（Serial Title API 实时值）' : undefined}>
                    {selected.venue.name}{selected.venue.sjr != null ? ` · SJR ${selected.venue.sjr}` : ''}
                  </span>
                )}
                {selected.cluster >= 0 && (
                  <span>
                    <i className="ex-cluster-dot" style={{ background: clusterInk(selected.cluster) }} />
                    {selected.cluster_label || `子方向 ${selected.cluster + 1}`}
                  </span>
                )}
                {selected.authors && <span className="ex-evidence-card__authors">{selected.authors}</span>}
                {paperLink(selected) && (
                  <a href={paperLink(selected) ?? '#'} target="_blank" rel="noreferrer">打开原文 ↗</a>
                )}
              </div>
              {selected.keywords && selected.keywords.length > 0 && (
                <div className="ex-evidence-card__kws">
                  {selected.keywords.map(k => <code key={k}>{k}</code>)}
                </div>
              )}
              {selected.abstract && <p className="ex-evidence-card__abstract">{selected.abstract}</p>}
              {detail && <button type="button" className="er-btn er-btn--secondary" onClick={() => setTab('chat')}><MessageSquare size={16} />针对这篇论文提问</button>}
            </section>
          )}

          {/* 领域总览（复用：热力图 + 战略坐标 + 范式卡片） */}
          {overviewGraph && (
            <section className="ex-section" aria-label="领域总览">
              <h2 className="ex-section__title">领域总览 · 子方向 × 年份</h2>
              <DomainOverview
                graph={overviewGraph}
                synthesis={overviewSynthesis}
                onSelectNode={handleSelectNode}
                paperForNode={node => result.papers[result.graph.nodes.findIndex(n => n.id === node.id)]}
              />
            </section>
          )}

          {/* 主阵地期刊：哪些期刊在承载这个方向 */}
          {result.metrics.venue_ranking && result.metrics.venue_ranking.length > 0 && (
            <section className="ex-section" aria-label="主阵地期刊">
              <h2 className="ex-section__title">主阵地期刊 · 该方向论文集中的发表地</h2>
              <ul className="ex-venue">
                {result.metrics.venue_ranking.map(v => (
                  <li key={v.name}>
                    <span className="ex-venue__name" title={v.name}>{v.name}</span>
                    <span className="ex-venue__count">{v.count} 篇</span>
                    {v.sjr != null && (
                      <span className="ex-venue__metric" title="SCImago 期刊等级分（Scopus Serial Title 实时值）">SJR {v.sjr}</span>
                    )}
                    {v.snip != null && (
                      <span className="ex-venue__metric" title="来源标准化影响因子（学科口径归一）">SNIP {v.snip}</span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* 关键词 × 年份热力图（GOAL 承诺的热力图，v1.4 兑现） */}
          {result.metrics.keyword_trend && (result.metrics.keyword_trend.rows?.length ?? 0) > 0 && (
            <section className="ex-section" aria-label="关键词趋势热力图">
              <ChartView title="关键词 × 年份热力图">
              <KeywordHeatmap trend={result.metrics.keyword_trend} />
              </ChartView>
              <p className="ex-heat__note">颜色越深 = 当年含该关键词的新发表论文越多（语料内计数，≥3 篇才入图）</p>
            </section>
          )}

          {/* 证据列表：高被引经典 + 近期上升 */}
          {total > 0 && (
            <section className="ex-section" aria-label="证据列表">
              <h2 className="ex-section__title">证据列表 · 点击查看摘要与原文链接</h2>
              <div className="ex-evidence-cols">
                <EvidenceList
                  title="样本内高被引"
                  hint="按本次检索样本累计被引排序，不代表领域排名"
                  keys={result.metrics.top_cited}
                  citationMethod={citationMethod}
                  papersByKey={papersByKey}
                  onSelect={setSelected}
                />
                <EvidenceList
                  title={exactCitations ? '引用率显著上升' : '旧版引用速度排序'}
                  hint={exactCitations ? '精确 Poisson 检验 + BH 校正；按最近两个完整年份年均被引排序' : '历史累计被引年均排序，未验证显著上升'}
                  keys={result.metrics.rising}
                  citationMethod={citationMethod}
                  papersByKey={papersByKey}
                  onSelect={setSelected}
                />
              </div>
            </section>
          )}

          {/* 热点 / 空白 / 后续 */}
          {synthesis && (
            <section className="ex-section" aria-label="热点与空白">
              <h2 className="ex-section__title">热点 · 空白 · 后续动作</h2>
              <div className="ex-evidence-cols">
                <ChipList title="📈 近期热点" items={synthesis.hotspots || []} ink="#d94a38" papers={allPapers} />
                <ChipList title="🧭 尚未解决的空白" items={synthesis.gaps || []} ink="#2f7d78" papers={allPapers} />
              </div>
              {(synthesis.follow_ups?.length ?? 0) > 0 && (
                <div className="ex-followups">
                  <span>建议下一步：</span>
                  {synthesis.follow_ups?.map((f, i) => (
                    <button key={i} type="button" onClick={() => { setQuestion(f); if (detail) setTab('chat') }}>{f}</button>
                  ))}
                </div>
              )}
            </section>
          )}

          {/* Markdown 报告 */}
          {synthesis?.markdown_report && (
            <section className="ex-section" aria-label="探索报告">
              <h2 className="ex-section__title">探索报告</h2>
              <div className="ex-report">
                <EvidenceMarkdown text={synthesis.markdown_report} papers={allPapers} />
              </div>
            </section>
          )}
        </div>
      )}

      {/* 尚无结果时的引导 */}
      {!result && !rawResult?.survey_report && stream.status !== 'running' && !submitting && stream.status !== 'failed' && (
        <section className="ex-empty">
          {runId ? '该探索没有可展示的产物。' : useQuestionLoop
            ? '输入一个问题开始。系统会整理研究问题、检索和阅读相关文献，给出带来源的回答，并提示尚缺的依据。'
            : '输入一个问题开始。系统会给出：查新结论 → 检索透明度 → 子方向×年份热力图 → 样本内被引与引用率变化 → 跨文献报告。'}
        </section>
      )}
    </div>
  )
}

// ── 子组件 ────────────────────────────────────────────────────────────────

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
    const runEnded = ['done', 'completed', 'partial', 'cancelled', 'failed', 'paused'].includes(run.status)
    if (!streamEnded && !runEnded) return null
    // updated_at 会在报告重生成/选择 revision 时继续变化，不能代表研究结束。
    // 完成态优先取最后一个闭合阶段的结束时刻；旧 run 无时间线才回退。
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
        <span className="ex-timing__label">{run.workflow_version === 'academic_survey_v2' ? '有效工作时长（已记录）' : '总耗时'}</span>
        <strong className="ex-timing__total tabular-nums">{formatDuration(run.workflow_version === 'academic_survey_v2' ? (stream.activeSeconds ?? run.active_seconds ?? 0) : timing.wallSeconds)}</strong>
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

function isAfterStage(stage: string | null, key: string, result?: { metrics?: unknown }, synthesis?: unknown): boolean {
  if (!stage) return false
  const order = STAGE_META.map(s => s.key)
  const cur = order.findIndex(k => stage.startsWith(k))
  if (cur < 0) return false
  const idx = order.indexOf(key)
  if (idx < cur) return true
  // 阶段完成但 stage 文案还没翻页的兜底：产物已到即视为该阶段之后
  if (key === 'metrics' && result) return true
  if (key === 'synthesize' && synthesis) return true
  return false
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

function ChipList({ title, items, ink, papers }: { title: string; items: string[]; ink: string; papers: ExplorationPaper[] }) {
  return (
    <div className="ex-chips">
      <div className="ex-chips__head" style={{ color: ink }}>{title}</div>
      {items.length === 0 && <p className="ex-chips__empty">—</p>}
      <ul>
        {items.map((t, i) => <ChipItem key={i} text={t} ink={ink} papers={papers} />)}
      </ul>
    </div>
  )
}

/** 卡片摘要：渲染级截断 + 点击展开；不修改存储的完整正文 */
function ChipItem({ text, ink, papers }: { text: string; ink: string; papers: ExplorationPaper[] }) {
  const [open, setOpen] = useState(false)
  const long = text.length > 90
  return (
    <li
      style={{ borderColor: inkWash(ink, 45), background: inkWash(ink, 8) }}
    >
      <div className={long && !open ? 'ex-chip-clamped' : undefined}>
        <EvidenceMarkdown text={text} papers={papers} />
      </div>
      {long && <button type="button" className="ex-chip-toggle" aria-expanded={open} onClick={() => setOpen(v => !v)}>{open ? '收起' : '展开全文'}</button>}
    </li>
  )
}


/** 检索与分析完整性：失败、资料不足与分析不完整的统一提示（任务书要求）。 */
function IntegrityNotice({ integrity }: { integrity: RunIntegrity }) {
  const ok = integrity.level === 'complete'
  return (
    <section className={`ex-integrity ex-integrity--${integrity.level}`} aria-label="检索与分析完整性" role={ok ? undefined : 'alert'}>
      <div className="ex-empty-warning__header">
        <span className="ex-integrity__badge">{integrity.label}</span>
        <strong>检索与分析完整性</strong>
      </div>
      {integrity.items.length > 0
        ? <ul className="ex-integrity__list">{integrity.items.map((item, i) => <li key={i}>{item.message}</li>)}</ul>
        : <p className="ex-empty-warning__desc">各阶段均已完成，未发现数据源失败或提取缺失。</p>}
    </section>
  )
}
