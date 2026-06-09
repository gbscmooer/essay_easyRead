import { useState, useEffect, useRef } from 'react'
import type { GraphNode, GraphEdge, ResearchStage, ResearchStatus } from '@/types'

interface PaperAdded { paper_id: number; title: string; source_paper_id: number; total_expanded: number }
interface EmbedProgress { done: number; total: number }
interface GraphReady { nodes: GraphNode[]; edges: GraphEdge[]; stats: Record<string, unknown> }

export function useResearchStream(runId: number | null) {
  const [status, setStatus] = useState<ResearchStatus | 'idle'>('idle')
  const [stage, setStage] = useState<ResearchStage | null>(null)
  const [progressMessage, setMsg] = useState('')
  const [papersAdded, setPapersAdded] = useState<PaperAdded[]>([])
  const [embed, setEmbed] = useState<EmbedProgress>({ done: 0, total: 0 })
  const [graph, setGraph] = useState<GraphReady | null>(null)
  const [error, setError] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const isUnmounted = useRef(false)

  useEffect(() => {
    isUnmounted.current = false
    return () => { isUnmounted.current = true }
  }, [])

  useEffect(() => {
    if (runId === null) return
    const es = new EventSource(`/api/research/runs/${runId}/stream`)
    esRef.current = es

    setStatus('running'); setError(null)

    es.addEventListener('status', (e: MessageEvent) => {
      if (isUnmounted.current) return
      try { setMsg(JSON.parse(e.data).message || '') } catch { /* noop */ }
    })
    es.addEventListener('stage_start', (e: MessageEvent) => {
      if (isUnmounted.current) return
      try { setStage(JSON.parse(e.data).stage as ResearchStage) } catch { /* noop */ }
    })
    es.addEventListener('stage_done', (e: MessageEvent) => {
      if (isUnmounted.current) return
      try {
        const data = JSON.parse(e.data)
        setMsg(`${data.stage} 完成`)
      } catch { /* noop */ }
    })
    es.addEventListener('paper_added', (e: MessageEvent) => {
      if (isUnmounted.current) return
      try { setPapersAdded(prev => [...prev, JSON.parse(e.data) as PaperAdded]) } catch { /* noop */ }
    })
    es.addEventListener('embed_progress', (e: MessageEvent) => {
      if (isUnmounted.current) return
      try { setEmbed(JSON.parse(e.data) as EmbedProgress) } catch { /* noop */ }
    })
    es.addEventListener('graph_ready', (e: MessageEvent) => {
      if (isUnmounted.current) return
      try { setGraph(JSON.parse(e.data) as GraphReady) } catch { /* noop */ }
    })
    es.addEventListener('done', () => { if (!isUnmounted.current) setStatus('done'); es.close() })
    es.addEventListener('error', (e: MessageEvent) => {
      if (isUnmounted.current) return
      try { setError(JSON.parse(e.data).message || '失败') } catch { setError('连接错误') }
      setStatus('failed'); es.close()
    })

    return () => { es.close(); esRef.current = null }
  }, [runId])

  return { status, stage, progressMessage, papersAdded, embed, graph, error }
}
