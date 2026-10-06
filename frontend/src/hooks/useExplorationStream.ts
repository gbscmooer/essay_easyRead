import { useState, useEffect, useRef } from 'react'
import type { ExplorationResult, QuestionLoopResult, ExplorationSynthesis } from '@/types'

/**
 * 领域探索 SSE 进度流（/api/exploration/runs/{id}/stream）。
 *
 * 事件协议（后端 explorer.py，v8 八阶段）：
 *   stage_start / stage_iteration / stage_done
 *                              preflight | protocol | recall | screen | select | extract | synthesize | report
 *   recall_progress            {query, hit_count, total_so_far}
 *   enrich_progress            {done, total}（OpenAlex 逐篇补逐年引用/摘要/关键词）
 *   metrics_ready              统计产物（decompose + papers + metrics + graph）——纯算法，无 Key 也有
 *   synthesis_ready            LLM 跨文献综合
 *   done / error
 *  旧阶段名（decompose/enrich/metrics/persist）保留在消息表里，历史 run 回放不退化。
 */
const STAGE_DEFAULT_MESSAGES: Record<string, string> = {
  preflight: '正在用带 Key 的最小真实检索探测各数据源权限（Scopus / OpenAlex / arXiv）…',
  protocol: '正在制定研究协议：拆解检索式 + 六类覆盖目标（演进/方法/数据集/指标/部署/趋势）…',
  recall: '正在多源检索学术文献（arXiv / Scopus / OpenAlex）…',
  screen: '正在裁决候选论文相关性并清洗计量语料（隔离未决论文）…',
  select: '正在分层选材（奠基/最新/综述/数据集/方法）并获取全文…',
  extract: '正在逐篇提取方法、数据集、指标与实验结果的原文证据…',
  synthesize: '正在基于证据行综合领域认知并撰写报告草稿…',
  report: '正在整理报告并保存交付版本…',
  decompose: '正在使用 AI 将自然语言问题拆解为学术检索式与意图…（旧阶段）',
  enrich: '正在获取文献被引轨迹、摘要、关键词与期刊指标…（旧阶段）',
  metrics: '正在执行纯算法计量分析与子方向聚类…（旧阶段）',
  persist: '正在持久化分析产物并构建知识图谱…（旧阶段）',
}

export interface StageTimingEntry {
  /** 阶段开始时刻（ms，来自服务端 ts 或本地时钟） */
  startTs: number
  /** 阶段结束时刻；运行中缺省 */
  endTs?: number
}

export function useExplorationStream(runId: number | null, revision = 0) {
  const [streamRunId, setStreamRunId] = useState<number | null>(null)
  const [status, setStatus] = useState<'idle' | 'running' | 'done' | 'failed' | 'paused'>('idle')
  const [stage, setStage] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [progress, setProgress] = useState<number>(0)
  const [result, setResult] = useState<ExplorationResult | QuestionLoopResult | null>(null)
  const [synthesis, setSynthesis] = useState<ExplorationSynthesis | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stageTimings, setStageTimings] = useState<Record<string, StageTimingEntry>>({})
  const [activeSeconds, setActiveSeconds] = useState<number | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const isUnmounted = useRef(false)

  useEffect(() => {
    isUnmounted.current = false
    return () => { isUnmounted.current = true }
  }, [])

  useEffect(() => {
    setStage(null)
    setMessage('')
    setProgress(0)
    setResult(null)
    setSynthesis(null)
    setError(null)
    setStageTimings({})
    setActiveSeconds(null)

    if (runId === null) {
      setStreamRunId(null)
      setStatus('idle')
      return
    }

    setStreamRunId(runId)
    const es = new EventSource(`/api/exploration/runs/${runId}/stream`)
    esRef.current = es
    let closed = false
    const shouldIgnore = () => isUnmounted.current || closed

    setStatus('running')
    setProgress(8)
    setMessage('正在连接分析事件总线…')

    es.addEventListener('run_status', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as { active_seconds?: unknown }
        if (typeof data.active_seconds === 'number' && Number.isFinite(data.active_seconds) && data.active_seconds >= 0) {
          setActiveSeconds(data.active_seconds)
        }
      } catch { /* invalid event is ignored */ }
    })

    es.addEventListener('task_state_changed', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as { state?: string; reason?: string; question_id?: string }
        setStage('research')
        setMessage(data.state === 'running' ? (data.reason || '正在补充问题所需依据…') : '已更新研究材料，正在评估缺口…')
        setProgress(previous => Math.min(95, previous + 1))
      } catch { /* invalid event is ignored */ }
    })
    es.addEventListener('cancelled', () => {
      if (!shouldIgnore()) { setStatus('done'); setMessage('研究已取消，已获材料保留') }
      es.close()
    })

    // 服务端 epoch 秒 → 本地 ms；旧后端事件无 ts 时退回本地时钟
    const eventTs = (data: { ts?: unknown }) => (
      typeof data.ts === 'number' && data.ts > 0 ? data.ts * 1000 : Date.now()
    )

    es.addEventListener('stage_start', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as { stage?: unknown; ts?: unknown }
        if (typeof data.stage === 'string') {
          const st = data.stage
          setStage(st)
          // 重入覆盖：暂停→续跑后同一阶段会再次 stage_start（新计时段）。
          // 若沿用第一次的 startTs，暂停间隔会被计入该阶段的走秒；
          // 重置 startTs 并清掉旧 endTs，旧段由落库时间线补回（求和）。
          setStageTimings(prev => ({ ...prev, [st]: { startTs: eventTs(data) } }))
          if (STAGE_DEFAULT_MESSAGES[st]) {
            setMessage(STAGE_DEFAULT_MESSAGES[st])
          }
          if (st === 'preflight') setProgress(8)
          else if (st === 'protocol') setProgress(15)
          else if (st === 'recall') setProgress(32)
          else if (st === 'screen') setProgress(55)
          else if (st === 'select') setProgress(66)
          else if (st === 'extract') setProgress(78)
          else if (st === 'synthesize') setProgress(88)
          else if (st === 'report') setProgress(94)
        }
      } catch { /* noop */ }
    })
    es.addEventListener('stage_iteration', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as {
          stage?: string; iteration?: number; phase?: string; tool?: string; action?: string
          done?: number; total?: number
        }
        const labels: Record<string, string> = {
          preflight: '数据源预检', protocol: '研究协议', recall: '文献召回', screen: '相关性裁决与计量',
          select: '分层选材与全文', extract: '证据提取', synthesize: '证据综合与写作',
          report: '报告保存', report_extract: '逐篇证据提取',
          decompose: '检索式拆解（旧）', enrich: '资料富化（旧）', metrics: '计量分析（旧）',
          persist: '结果保存（旧）',
        }
        const label = labels[data.stage ?? ''] ?? data.stage ?? '当前阶段'
        const iteration = Number.isFinite(data.iteration) ? `第 ${data.iteration} 轮` : '本轮'
        const action = data.tool || data.action
        if (data.phase === 'complete') setMessage(`${label} · ${iteration}验收通过`)
        else if (data.phase === 'filter' && Number.isFinite(data.done) && Number.isFinite(data.total)) {
          // 筛选是召回阶段最耗时的一段：逐批回报，避免长时间无消息被误判为卡死
          setMessage(`摘要相关性筛选：${data.done}/${data.total} 篇`)
        }
        else if (action) setMessage(`${label} · ${iteration} · 正在使用 ${action}`)
        else setMessage(`${label} · ${iteration}分析中`)
      } catch { /* noop */ }
    })
    es.addEventListener('stage_done', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data)
        const st = String(data.stage || '')
        setStage(`${st} 完成`)
        setStageTimings(prev => ({
          ...prev,
          [st]: { ...(prev[st] ?? { startTs: eventTs(data) }), endTs: eventTs(data) },
        }))
        if (st === 'preflight') {
          const srcs: { name: string; status: string }[] = Array.isArray(data.summary?.sources) ? data.summary.sources : []
          const dead: string[] = Array.isArray(data.summary?.dead) ? data.summary.dead : []
          const line = srcs.map(s => `${s.name} ${s.status === 'ok' ? '✓' : '✗'}`).join(' · ')
          setMessage(srcs.length
            ? (dead.length
                ? `数据源预检：${line}（不可用：${dead.join('、')}，将自动回退可用源）`
                : `数据源预检通过：${line}`)
            : '数据源预检完成')
          setProgress(18)
        } else if (st === 'decompose') {
          const count = data.summary?.queries?.length ?? 0
          setMessage(`检索式拆解完成，生成 ${count} 组学术检索式`)
          setProgress(25)
        } else if (st === 'recall') {
          const total = data.summary?.total ?? data.summary?.total_deduped ?? data.summary?.total_recalled ?? 0
          setMessage(`文献检索完成，去重合并得到 ${total} 篇相关论文`)
          setProgress(50)
        } else if (st === 'enrich') {
          setMessage('文献指标与期刊数据富化完成')
          setProgress(72)
        } else if (st === 'metrics') {
          setMessage('计量与聚类算法分析完成')
          setProgress(82)
        } else if (st === 'synthesize') {
          setMessage('跨文献证据综合研判完成')
          setProgress(94)
        } else if (st === 'persist') {
          setMessage('分析产物落盘完成')
          setProgress(99)
        }
      } catch { /* noop */ }
    })
    es.addEventListener('recall_progress', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as { query: string; hit_count: number; total_so_far: number }
        setMessage(`检索「${data.query}」命中 ${data.hit_count} 篇（累计 ${data.total_so_far} 篇）`)
        setProgress(prev => Math.max(prev, Math.min(48, 30 + Math.round((data.total_so_far || 0) * 0.4))))
      } catch { /* noop */ }
    })
    es.addEventListener('enrich_progress', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as { done: number; total: number }
        setMessage(`指标富化：逐年引用 / 摘要 / 期刊指标（${data.done}/${data.total} 篇）`)
        if (data.total > 0) {
          const ratio = Math.min(1, Math.max(0, data.done / data.total))
          setProgress(Math.round(52 + ratio * 18))
        }
      } catch { /* noop */ }
    })
    es.addEventListener('metrics_ready', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        setResult(JSON.parse(e.data) as ExplorationResult | QuestionLoopResult)
        setProgress(prev => Math.max(prev, 84))
      } catch { /* noop */ }
    })
    es.addEventListener('synthesis_ready', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        setSynthesis(JSON.parse(e.data) as ExplorationSynthesis)
        setProgress(prev => Math.max(prev, 94))
      } catch { /* noop */ }
    })
    es.addEventListener('report_progress', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data)
        setMessage(`报告证据提取：${data.done}/${data.total} 篇`)
        setProgress(90 + Math.round(5 * data.done / Math.max(1, data.total)))
      } catch { /* noop */ }
    })
    es.addEventListener('report_ready', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const report = JSON.parse(e.data)
        setResult(prev => prev ? { ...prev, survey_report: report } : prev)
      } catch { /* noop */ }
    })
    es.addEventListener('done', () => {
      if (!shouldIgnore()) {
        setStatus('done')
        setProgress(100)
      }
      es.close()
    })
    // 协作式暂停：worker 在安全点（阶段/轮次/篇目边界）停止并保留断点
    es.addEventListener('paused', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as { stage?: string }
        setStatus('paused')
        setStage(data.stage || null)
        setMessage('已在安全点暂停，断点已保留，可随时继续')
      } catch {
        setStatus('paused')
      }
      es.close()
    })
    es.addEventListener('error', (e: Event) => {
      if (shouldIgnore()) return
      // EventSource 的原生网络 error 是普通 Event，浏览器会自行重连；
      // 服务端 `event: error` 才是带 JSON data 的 MessageEvent，表示任务失败。
      if (!('data' in e) || typeof e.data !== 'string') {
        if (es.readyState === 2) {
          setError('事件连接已关闭，请刷新页面后重试')
          setStatus('failed')
          return
        }
        setMessage('连接暂时中断，正在自动重连…')
        return
      }
      try { setError(JSON.parse(e.data).message || '失败') } catch { setError('研究失败') }
      setStatus('failed')
      es.close()
    })

    return () => { closed = true; es.close(); esRef.current = null }
  }, [runId, revision])

  const isCurrentRun = runId !== null && streamRunId === runId

  return {
    status: runId === null ? 'idle' : (isCurrentRun ? status : 'idle'),
    stage: isCurrentRun ? stage : null,
    message: isCurrentRun ? message : '',
    progress: isCurrentRun ? progress : 0,
    result: isCurrentRun ? result : null,
    synthesis: isCurrentRun ? synthesis : null,
    error: isCurrentRun ? error : null,
    stageTimings: isCurrentRun ? stageTimings : {},
    ...(isCurrentRun && activeSeconds !== null ? { activeSeconds } : {}),
  }
}
