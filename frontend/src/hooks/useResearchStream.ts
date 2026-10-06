import { useState, useEffect, useRef } from 'react'
import type { GraphNode, GraphEdge, ResearchStage, ResearchStatus, RecallChannelStats, GraphSynthesis } from '@/types'

interface PaperAdded { paper_id: number; title: string; source_paper_id: number; total_expanded: number }
interface EmbedProgress { done: number; total: number }
interface GraphReady { nodes: GraphNode[]; edges: GraphEdge[]; stats: Record<string, unknown> }

const EMPTY_EMBED: EmbedProgress = { done: 0, total: 0 }
const EMPTY_PAPERS: PaperAdded[] = []
const EMPTY_RECALL: RecallChannelStats[] = []

export function useResearchStream(runId: number | null) {
  const [streamRunId, setStreamRunId] = useState<number | null>(null)
  const [status, setStatus] = useState<ResearchStatus | 'idle'>('idle')
  const [stage, setStage] = useState<ResearchStage | string | null>(null)
  const [progressMessage, setMsg] = useState('')
  const [papersAdded, setPapersAdded] = useState<PaperAdded[]>([])
  const [embed, setEmbed] = useState<EmbedProgress>(EMPTY_EMBED)
  const [graph, setGraph] = useState<GraphReady | null>(null)
  const [synthesis, setSynthesis] = useState<GraphSynthesis | null>(null)
  const [recallStats, setRecallStats] = useState<RecallChannelStats[]>(EMPTY_RECALL)
  const [error, setError] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const isUnmounted = useRef(false)

  useEffect(() => {
    isUnmounted.current = false
    return () => { isUnmounted.current = true }
  }, [])

  useEffect(() => {
    setStage(null)
    setMsg('')
    setPapersAdded([])
    setEmbed(EMPTY_EMBED)
    setGraph(null)
    setSynthesis(null)
    setRecallStats(EMPTY_RECALL)
    setError(null)

    if (runId === null) {
      setStreamRunId(null)
      setStatus('idle')
      return
    }

    setStreamRunId(runId)
    const es = new EventSource(`/api/research/runs/${runId}/stream`)
    esRef.current = es
    let closed = false
    const shouldIgnore = () => isUnmounted.current || closed

    setStatus('running')

    const mergeRecallStat = (incoming: RecallChannelStats) => {
      setRecallStats(prev => {
        const idx = prev.findIndex(s => s.channel === incoming.channel)
        if (idx < 0) return [...prev, incoming]
        const next = [...prev]
        const cur = next[idx]
        next[idx] = {
          ...cur,
          ...incoming,
          hit_count: (cur.hit_count ?? 0) + (incoming.hit_count ?? 0),
          failed: (cur.failed ?? 0) + (incoming.failed ?? 0),
          errors: [
            ...(cur.errors ?? []),
            ...(incoming.errors ?? []),
            ...(incoming.error ? [incoming.error] : []),
          ].filter((e, i, arr) => arr.indexOf(e) === i),
        }
        return next
      })
    }

    es.addEventListener('status', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try { setMsg(JSON.parse(e.data).message || '') } catch { /* noop */ }
    })
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
        setMsg(`${data.stage} 完成`)
      } catch { /* noop */ }
    })
    es.addEventListener('paper_added', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try { setPapersAdded(prev => [...prev, JSON.parse(e.data) as PaperAdded]) } catch { /* noop */ }
    })
    es.addEventListener('embed_progress', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try { setEmbed(JSON.parse(e.data) as EmbedProgress) } catch { /* noop */ }
    })
    es.addEventListener('recall_stats', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try { mergeRecallStat(JSON.parse(e.data) as RecallChannelStats) } catch { /* noop */ }
    })
    es.addEventListener('graph_ready', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try { setGraph(JSON.parse(e.data) as GraphReady) } catch { /* noop */ }
    })
    es.addEventListener('synthesis_ready', (e: MessageEvent) => {
      if (shouldIgnore()) return
      try { setSynthesis(JSON.parse(e.data) as GraphSynthesis) } catch { /* noop */ }
    })
    es.addEventListener('done', () => { if (!shouldIgnore()) setStatus('done'); es.close() })
    es.addEventListener('error', (e: MessageEvent) => {
      if (shouldIgnore()) return
      if (!('data' in e)) {
        setMsg('连接中断，正在重新连接…')
        return
      }
      try { setError(JSON.parse(e.data).message || '失败') } catch { setError('服务端错误事件格式无效') }
      setStatus('failed'); closed = true; es.close()
    })

    return () => { closed = true; es.close(); esRef.current = null }
  }, [runId])

  const isCurrentRun = runId !== null && streamRunId === runId

  return {
    // runId 已变但 streamRunId 尚未同步的一帧：不要误报 running（否则会短暂禁用控制面板）
    status: runId === null ? 'idle' : (isCurrentRun ? status : 'idle'),
    stage: isCurrentRun ? stage : null,
    progressMessage: isCurrentRun ? progressMessage : '',
    papersAdded: isCurrentRun ? papersAdded : EMPTY_PAPERS,
    embed: isCurrentRun ? embed : EMPTY_EMBED,
    graph: isCurrentRun ? graph : null,
    synthesis: isCurrentRun ? synthesis : null,
    recallStats: isCurrentRun ? recallStats : EMPTY_RECALL,
    error: isCurrentRun ? error : null,
  }
}
