import { useState, useEffect, useRef } from 'react'
import type { ExplorationResult, ExplorationSynthesis } from '@/types'

/**
 * 领域探索 SSE 进度流（/api/exploration/runs/{id}/stream）。
 *
 * 事件协议（后端 explorer.py）：
 *   stage_start / stage_done   decompose | recall | enrich | metrics | synthesize | persist
 *   recall_progress            {query, hit_count, total_so_far}
 *   enrich_progress            {done, total}（OpenAlex 逐篇补逐年引用/摘要/关键词）
 *   metrics_ready              统计产物（decompose + papers + metrics + graph）——纯算法，无 Key 也有
 *   synthesis_ready            LLM 跨文献综合
 *   done / error
 */
const STAGE_DEFAULT_MESSAGES: Record<string, string> = {
  preflight: '正在用带 Key 的最小真实检索探测各数据源权限（Scopus / OpenAlex）…',
  decompose: '正在使用 AI 将自然语言问题拆解为学术检索式与意图…',
  recall: '正在多源检索学术文献（Scopus / OpenAlex）…',
  enrich: '正在获取文献被引轨迹、摘要、关键词与期刊指标…',
  metrics: '正在执行纯算法计量分析与子方向聚类…',
  synthesize: '正在深度研判文献证据、查新结论与技术成熟度…',
  report: '正在逐篇提取评估框架、指标、任务与数据集的原文证据…',
  persist: '正在持久化分析产物并构建知识图谱…',
}

export function useExplorationStream(runId: number | null, revision = 0) {
  const [streamRunId, setStreamRunId] = useState<number | null>(null)
  const [status, setStatus] = useState<'idle' | 'running' | 'done' | 'failed'>('idle')
  const [stage, setStage] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [progress, setProgress] = useState<number>(0)
  const [result, setResult] = useState<ExplorationResult | null>(null)
  const [synthesis, setSynthesis] = useState<ExplorationSynthesis | null>(null)
  const [error, setError] = useState<string | null>(null)
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

    es.addEventListener('stage_start', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as { stage?: unknown }
        if (typeof data.stage === 'string') {
          const st = data.stage
          setStage(st)
          if (STAGE_DEFAULT_MESSAGES[st]) {
            setMessage(STAGE_DEFAULT_MESSAGES[st])
          }
          if (st === 'preflight') setProgress(10)
          else if (st === 'decompose') setProgress(15)
          else if (st === 'recall') setProgress(30)
          else if (st === 'enrich') setProgress(52)
          else if (st === 'metrics') setProgress(74)
          else if (st === 'synthesize') setProgress(86)
          else if (st === 'report') setProgress(90)
          else if (st === 'persist') setProgress(96)
        }
      } catch { /* noop */ }
    })
    es.addEventListener('stage_done', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data)
        const st = String(data.stage || '')
        setStage(`${st} 完成`)
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
        setResult(JSON.parse(e.data) as ExplorationResult)
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
    es.addEventListener('error', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try { setError(JSON.parse(e.data).message || '失败') } catch { setError('连接错误') }
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
  }
}
