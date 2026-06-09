/**
 * useStreaming Hook
 *
 * 管理 SSE 连接，逐字段接收解析结果。
 * 提供连接状态、已接收数据、重连逻辑。
 *
 * paperId 为 null 时不建立连接（idle 状态）。
 * paperId 变化时自动断开旧连接、建立新连接。
 * 这样可以在 useAnalysis 顶层无条件调用此 hook。
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import type { AnalysisResult, AnalysisStatus } from '@/types'


interface UseStreamingReturn {
  /** 当前解析状态 */
  status: AnalysisStatus
  /** 已接收的部分解析结果（字段逐步填充） */
  partialResult: Partial<AnalysisResult>
  /** 错误信息（status='failed' 时有值） */
  error: string | null
  /** 进度消息（如 "正在分析方法论..."） */
  progressMessage: string
  /** 后端计算的进度百分比（0-100），无 progress 事件时为 -1 */
  progress: number
  /** 相关论文模块的独立状态：idle 未开始 / loading 搜索中 / ok 完成 / failed 超时或失败 */
  relatedStatus: 'idle' | 'loading' | 'ok' | 'failed'
  /** 手动重连 */
  reconnect: () => void
}


/**
 * 连接 SSE 端点，逐步接收解析结果。
 *
 * @param paperId 论文 ID，为 null 时不连接
 * @param shouldConnect 是否建立连接（默认 true，传 false 可延迟连接）
 * @returns 解析状态和部分结果
 */
export function useStreaming(paperId: number | null, shouldConnect: boolean = true): UseStreamingReturn {
  const [status, setStatus] = useState<AnalysisStatus>('idle')
  const [partialResult, setPartialResult] = useState<Partial<AnalysisResult>>({})
  const [error, setError] = useState<string | null>(null)
  const [progressMessage, setProgressMessage] = useState('')
  const [progress, setProgress] = useState(-1)
  const [relatedStatus, setRelatedStatus] = useState<'idle' | 'loading' | 'ok' | 'failed'>('idle')

  const eventSourceRef = useRef<EventSource | null>(null)
  const reconnectCountRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isUnmountedRef = useRef(false)
  // 主体完成后，相关论文阶段仍会发 status=analyzing 的 progress 事件，
  // 用此 ref 防止把已 done 的状态又拉回 analyzing（否则界面会闪回加载态）
  const mainDoneRef = useRef(false)

  const cleanup = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
    }
  }, [])

  const connect = useCallback(() => {
    cleanup()

    if (paperId === null || !shouldConnect) {
      setStatus('idle')
      setPartialResult({})
      setError(null)
      setProgressMessage('')
      setProgress(-1)
      setRelatedStatus('idle')
      return
    }

    const es = new EventSource(`/api/analysis/${paperId}/stream`)
    eventSourceRef.current = es

    es.addEventListener('status', (e: MessageEvent) => {
      if (isUnmountedRef.current) return
      try {
        const data = JSON.parse(e.data)
        setStatus(data.status as AnalysisStatus)
        setProgressMessage(data.message || '')
      } catch {
        // 忽略解析错误
      }
    })

    es.addEventListener('progress', (e: MessageEvent) => {
      if (isUnmountedRef.current) return
      try {
        const data = JSON.parse(e.data)
        if (typeof data.percent === 'number') setProgress(data.percent)
        // 主体完成后不再下调状态（相关论文阶段的 analyzing 进度不影响已展示的结果）
        if (data.status && !mainDoneRef.current) setStatus(data.status as AnalysisStatus)
        if (data.message) setProgressMessage(data.message)
      } catch {
        // 忽略解析错误
      }
    })

    es.addEventListener('field', (e: MessageEvent) => {
      if (isUnmountedRef.current) return
      try {
        const data = JSON.parse(e.data)
        const fieldName = data.field_name as keyof AnalysisResult
        const value = data.value
        setPartialResult(prev => ({ ...prev, [fieldName]: value }))
      } catch {
        // 忽略解析错误
      }
    })

    // 主体分析完成：立即切到 done 让前端渲染全部主体字段，但保持连接以接收相关论文
    // （相关论文模块的 loading/ok/failed 状态由后端的 related_status 事件驱动）
    es.addEventListener('main_done', () => {
      if (isUnmountedRef.current) return
      mainDoneRef.current = true
      setStatus('done')
    })

    // 相关论文模块的独立状态
    es.addEventListener('related_status', (e: MessageEvent) => {
      if (isUnmountedRef.current) return
      try {
        const data = JSON.parse(e.data)
        if (data.status) setRelatedStatus(data.status)
      } catch {
        // 忽略解析错误
      }
    })

    es.addEventListener('done', () => {
      if (isUnmountedRef.current) return
      setStatus('done')
      setProgressMessage('')
      setProgress(100)
      reconnectCountRef.current = 0
      es.close()
    })

    es.addEventListener('error', (e: MessageEvent) => {
      if (isUnmountedRef.current) return
      try {
        const data = JSON.parse(e.data)
        setStatus('failed')
        setError(data.detail || data.message || '分析失败')
      } catch {
        setStatus('failed')
        setError('连接错误')
      }
      es.close()
    })

    es.onopen = () => {
      reconnectCountRef.current = 0
    }

    es.onerror = () => {
      if (isUnmountedRef.current) return
      es.close()
      eventSourceRef.current = null

      // 自动重连（最多 3 次，间隔递增：1s, 2s, 4s）
      if (reconnectCountRef.current < 3) {
        const delay = Math.pow(2, reconnectCountRef.current) * 1000
        reconnectCountRef.current += 1
        reconnectTimerRef.current = setTimeout(() => {
          if (!isUnmountedRef.current) {
            connect()
          }
        }, delay)
      } else {
        setStatus('failed')
        setError('连接中断，请刷新重试')
      }
    }
  }, [paperId, shouldConnect, cleanup])

  const reconnect = useCallback(() => {
    reconnectCountRef.current = 0
    setError(null)
    setStatus('idle')
    connect()
  }, [connect])

  useEffect(() => {
    isUnmountedRef.current = false
    reconnectCountRef.current = 0
    mainDoneRef.current = false
    setPartialResult({})
    setError(null)
    setProgressMessage('')
    setProgress(-1)
    setRelatedStatus('idle')
    connect()

    return () => {
      isUnmountedRef.current = true
      cleanup()
    }
  }, [paperId, shouldConnect, connect, cleanup])

  return {
    status,
    partialResult,
    error,
    progressMessage,
    progress,
    relatedStatus,
    reconnect,
  }
}
