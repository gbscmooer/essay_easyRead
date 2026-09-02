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
export function useExplorationStream(runId: number | null) {
  const [streamRunId, setStreamRunId] = useState<number | null>(null)
  const [status, setStatus] = useState<'idle' | 'running' | 'done' | 'failed'>('idle')
  const [stage, setStage] = useState<string | null>(null)
  const [message, setMessage] = useState('')
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

    es.addEventListener('stage_start', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as { stage?: unknown }
        if (typeof data.stage === 'string') setStage(data.stage)
      } catch { /* noop */ }
    })
    es.addEventListener('stage_done', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data)
        setStage(`${data.stage} 完成`)
      } catch { /* noop */ }
    })
    es.addEventListener('recall_progress', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as { query: string; hit_count: number; total_so_far: number }
        setMessage(`检索「${data.query}」命中 ${data.hit_count} 篇（累计 ${data.total_so_far}）`)
      } catch { /* noop */ }
    })
    es.addEventListener('enrich_progress', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try {
        const data = JSON.parse(e.data) as { done: number; total: number }
        setMessage(`指标富化：逐年引用 / 摘要 / 期刊指标（${data.done}/${data.total} 篇）`)
      } catch { /* noop */ }
    })
    es.addEventListener('metrics_ready', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try { setResult(JSON.parse(e.data) as ExplorationResult) } catch { /* noop */ }
    })
    es.addEventListener('synthesis_ready', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try { setSynthesis(JSON.parse(e.data) as ExplorationSynthesis) } catch { /* noop */ }
    })
    es.addEventListener('done', () => { if (!shouldIgnore()) setStatus('done'); es.close() })
    es.addEventListener('error', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try { setError(JSON.parse(e.data).message || '失败') } catch { setError('连接错误') }
      setStatus('failed'); es.close()
    })

    return () => { closed = true; es.close(); esRef.current = null }
  }, [runId])

  const isCurrentRun = runId !== null && streamRunId === runId

  return {
    status: runId === null ? 'idle' : (isCurrentRun ? status : 'idle'),
    stage: isCurrentRun ? stage : null,
    message: isCurrentRun ? message : '',
    result: isCurrentRun ? result : null,
    synthesis: isCurrentRun ? synthesis : null,
    error: isCurrentRun ? error : null,
  }
}
