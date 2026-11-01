import { useState, useEffect, useRef } from 'react'
import type { CrawlStep, DirectAnswer, ExplorationResult } from '@/types'

/**
 * 研究 SSE 进度流（/api/exploration/runs/{id}/stream），事件来自 backend loop/runner.py：
 *   run_started                 {resume, config}
 *   stage_start / stage_done    search | expand | landscape | answer
 *   queries_ready               {queries}
 *   layer_start                 {layer, frontier}
 *   crawl_step                  CrawlStep + {candidates, total_selected, top?}
 *   metrics_ready               完整快照（papers + crawl + metrics + graph）
 *   answer_ready                DirectAnswer
 *   done / paused / cancelled / error
 */
const STAGE_MESSAGES: Record<string, string> = {
  search: '正在写检索式并多源检索，每篇论文由 Selector 判定相关性…',
  expand: '正在展开高分论文：读章节名、追所选章节的引用，再逐篇判定…',
  landscape: '正在计算选中论文的计量、聚类与图谱…',
  answer: '正在起草答案并为每句查找原文证据…',
}
const STAGE_PROGRESS: Record<string, number> = { search: 10, expand: 40, landscape: 75, answer: 82 }

export interface StageTimingEntry {
  startTs: number
  endTs?: number
}

export interface LiveCrawl {
  queries: string[]
  candidates: number
  selected: number
  steps: CrawlStep[]
  recent: { key: string; title: string; label?: number | null }[]
}

const EMPTY_CRAWL: LiveCrawl = { queries: [], candidates: 0, selected: 0, steps: [], recent: [] }

export function useExplorationStream(runId: number | null, revision = 0) {
  const [streamRunId, setStreamRunId] = useState<number | null>(null)
  const [status, setStatus] = useState<'idle' | 'running' | 'done' | 'failed' | 'paused'>('idle')
  const [stage, setStage] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [progress, setProgress] = useState<number>(0)
  const [result, setResult] = useState<ExplorationResult | null>(null)
  const [answer, setAnswer] = useState<DirectAnswer | null>(null)
  const [crawl, setCrawl] = useState<LiveCrawl>(EMPTY_CRAWL)
  const [error, setError] = useState<string | null>(null)
  const [stageTimings, setStageTimings] = useState<Record<string, StageTimingEntry>>({})
  const isUnmounted = useRef(false)

  useEffect(() => {
    isUnmounted.current = false
    return () => { isUnmounted.current = true }
  }, [])

  useEffect(() => {
    setStage(null); setMessage(''); setProgress(0); setResult(null); setAnswer(null)
    setCrawl(EMPTY_CRAWL); setError(null); setStageTimings({})
    if (runId === null) {
      setStreamRunId(null)
      setStatus('idle')
      return
    }
    setStreamRunId(runId)
    const es = new EventSource(`/api/exploration/runs/${runId}/stream`)
    let closed = false
    const ignore = () => isUnmounted.current || closed
    const parse = (e: Event) => {
      try { return JSON.parse((e as MessageEvent).data) as Record<string, unknown> } catch { return null }
    }
    const eventTs = (data: Record<string, unknown>) => (typeof data.ts === 'number' && data.ts > 0 ? data.ts * 1000 : Date.now())

    setStatus('running')
    setProgress(5)
    setMessage('正在连接研究事件流…')

    es.addEventListener('stage_start', e => {
      const data = parse(e)
      if (ignore() || !data || typeof data.stage !== 'string') return
      const st = data.stage
      setStage(st)
      setStageTimings(prev => ({ ...prev, [st]: { startTs: eventTs(data) } }))
      setMessage(STAGE_MESSAGES[st] ?? st)
      setProgress(prev => Math.max(prev, STAGE_PROGRESS[st] ?? prev))
    })
    es.addEventListener('stage_done', e => {
      const data = parse(e)
      if (ignore() || !data) return
      const st = String(data.stage || '')
      setStage(`${st} 完成`)
      setStageTimings(prev => ({ ...prev, [st]: { ...(prev[st] ?? { startTs: eventTs(data) }), endTs: eventTs(data) } }))
    })
    es.addEventListener('queries_ready', e => {
      const data = parse(e)
      if (ignore() || !data) return
      const queries = Array.isArray(data.queries) ? data.queries.map(String) : []
      setCrawl(prev => ({ ...prev, queries }))
      setMessage(`生成 ${queries.length} 条检索式，开始检索`)
    })
    es.addEventListener('layer_start', e => {
      const data = parse(e)
      if (ignore() || !data) return
      setMessage(`第 ${Number(data.layer ?? 0) + 1} 层展开：${data.frontier ?? 0} 篇高分论文`)
    })
    es.addEventListener('crawl_step', e => {
      const data = parse(e)
      if (ignore() || !data) return
      const step = data as unknown as CrawlStep & { candidates?: number; total_selected?: number; top?: LiveCrawl['recent'] }
      setCrawl(prev => ({
        queries: step.action !== 'expand' && step.query && !prev.queries.includes(step.query) ? [...prev.queries, step.query] : prev.queries,
        candidates: step.candidates ?? prev.candidates,
        selected: step.total_selected ?? prev.selected,
        steps: [...prev.steps, step].slice(-200),
        recent: [...(step.top ?? []), ...prev.recent].slice(0, 12),
      }))
      setMessage(step.action === 'expand'
        ? `展开「${step.title ?? step.paper}」${step.chosen?.length ? `（${step.chosen.join('、')}）` : ''}：新增 ${step.new} 篇，选中 ${step.selected} 篇`
        : `${step.action === 'refine' ? '补检' : '检索'}「${step.query}」：新增 ${step.new} 篇，选中 ${step.selected} 篇`)
      setProgress(prev => Math.min(step.action === 'expand' ? 72 : 38, prev + 2))
    })
    es.addEventListener('metrics_ready', e => {
      const data = parse(e)
      if (ignore() || !data) return
      setResult(data as unknown as ExplorationResult)
      if ((data as { answer?: DirectAnswer }).answer) setAnswer((data as { answer: DirectAnswer }).answer)
      setProgress(prev => Math.max(prev, 80))
    })
    es.addEventListener('answer_ready', e => {
      const data = parse(e)
      if (ignore() || !data) return
      setAnswer(data as unknown as DirectAnswer)
      setProgress(prev => Math.max(prev, 98))
    })
    es.addEventListener('done', () => {
      if (!ignore()) { setStatus('done'); setProgress(100) }
      es.close()
    })
    es.addEventListener('cancelled', () => {
      if (!ignore()) { setStatus('done'); setMessage('研究已取消，已获材料保留') }
      es.close()
    })
    es.addEventListener('paused', e => {
      const data = parse(e)
      if (!ignore()) {
        setStatus('paused')
        setStage(typeof data?.stage === 'string' ? data.stage : null)
        setMessage('已暂停，断点已保留，可随时继续')
      }
      es.close()
    })
    es.addEventListener('error', (e: Event) => {
      if (ignore()) return
      // 原生网络 error 是普通 Event（浏览器自动重连）；服务端 `event: error` 带 JSON data，表示研究失败
      if (!('data' in e) || typeof (e as MessageEvent).data !== 'string') {
        if (es.readyState === 2) { setError('事件连接已关闭，请刷新页面后重试'); setStatus('failed'); return }
        setMessage('连接暂时中断，正在自动重连…')
        return
      }
      const data = parse(e)
      setError(typeof data?.message === 'string' ? data.message : '研究失败')
      setStatus('failed')
      es.close()
    })
    return () => { closed = true; es.close() }
  }, [runId, revision])

  const current = runId !== null && streamRunId === runId
  return {
    status: runId === null ? 'idle' : (current ? status : 'idle'),
    stage: current ? stage : null,
    message: current ? message : '',
    progress: current ? progress : 0,
    result: current ? result : null,
    answer: current ? answer : null,
    crawl: current ? crawl : EMPTY_CRAWL,
    error: current ? error : null,
    stageTimings: current ? stageTimings : {},
  }
}
