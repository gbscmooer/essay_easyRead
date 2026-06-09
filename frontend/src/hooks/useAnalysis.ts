/**
 * useAnalysis Hook
 *
 * 管理论文解析的完整生命周期：上传 → 流式接收 → 完成。
 * 组合了 papers API 和 useStreaming。
 *
 * 重要：useStreaming 在组件顶层无条件调用，传入 activePaperId state。
 * uploadAndAnalyze 上传成功后 setState(activePaperId) 触发 SSE 连接。
 */

import { useState, useEffect, useCallback } from 'react'
import type { Paper, AnalysisResult, AnalysisStatus } from '@/types'
import { uploadPaper, getPaper, getAnalysis, reparsePaper, startAnalysisJob } from '@/api/papers'
import { useStreaming } from './useStreaming'
import { toast } from '@/hooks/useToast'


interface UseAnalysisReturn {
  /** 当前论文记录 */
  paper: Paper | null
  /** 完整解析结果（解析完成后有值） */
  analysis: AnalysisResult | null
  /** 解析状态 */
  status: AnalysisStatus
  /** 进度消息 */
  progressMessage: string
  /** 后端计算的进度百分比（0-100），无 progress 事件时为 -1 */
  progress: number
  /** 相关论文模块的独立状态 */
  relatedStatus: 'idle' | 'loading' | 'ok' | 'failed'
  /** 部分解析结果（流式接收中） */
  partialResult: Partial<AnalysisResult>
  /** 错误信息 */
  error: string | null
  /** 上传并开始解析 */
  uploadAndAnalyze: (file: File) => Promise<void>
  /** 重新解析 */
  reparse: () => Promise<void>
  /** 开始分析（从 idle 状态触发） */
  startAnalysis: () => void
}


/**
 * 论文解析的完整流程管理。
 *
 * @param initialPaperId 初始论文 ID（ReaderPage 加载已有论文时传入）
 * @returns 解析状态和操作方法
 */
export function useAnalysis(initialPaperId?: number): UseAnalysisReturn {
  const [activePaperId, setActivePaperId] = useState<number | null>(initialPaperId ?? null)
  const [paper, setPaper] = useState<Paper | null>(null)
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null)
  const [shouldConnect, setShouldConnect] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  // 顶层无条件调用 useStreaming
  const {
    status: streamStatus,
    partialResult,
    error: streamError,
    progressMessage,
    progress,
    relatedStatus,
  } = useStreaming(activePaperId, shouldConnect)

  // 初始加载：如果传入 initialPaperId，加载论文信息
  useEffect(() => {
    if (initialPaperId === undefined) return

    let cancelled = false
    setActivePaperId(initialPaperId)

    getPaper(initialPaperId).then(p => {
      if (cancelled) return
      setPaper(p)
      // 如果已经完成，直接获取完整结果
      if (p.status === 'done') {
        setShouldConnect(true)
        getAnalysis(initialPaperId).then(result => {
          if (!cancelled) setAnalysis(result)
        }).catch(() => {
          toast({ type: 'error', message: '加载分析结果失败' })
        })
      } else if (p.status === 'failed') {
        // 失败状态也连接，以便显示错误
        setShouldConnect(true)
      } else if (p.status === 'extracting' || p.status === 'analyzing') {
        // 正在处理中，自动连接 SSE 显示进度
        setShouldConnect(true)
      } else {
        // pending：等待用户点击"开始分析"
        setShouldConnect(false)
      }
    }).catch(() => {
      toast({ type: 'error', message: '加载论文信息失败' })
    })

    return () => { cancelled = true }
  }, [initialPaperId])

  // 当 SSE 完成时，获取完整结果
  useEffect(() => {
    if (streamStatus === 'done' && activePaperId !== null) {
      getAnalysis(activePaperId).then(result => {
        setAnalysis(result)
      }).catch(() => {
        toast({ type: 'error', message: '加载完整分析失败' })
      })
    }
  }, [streamStatus, activePaperId])

  // 上传并开始解析
  const uploadAndAnalyze = useCallback(async (file: File) => {
    setAnalysis(null)
    setPaper(null)
    try {
      const uploaded = await uploadPaper(file)
      setPaper(uploaded)
      setActivePaperId(uploaded.id)
      // 先在后台启动分析（与 SSE 解耦），再连接流获取实时进度
      startAnalysisJob(uploaded.id).catch((e: unknown) => {
        setShouldConnect(false)
        const msg = e instanceof Error ? e.message : '启动分析任务失败'
        setLocalError(msg)
        toast({ type: 'error', message: msg })
      })
      setShouldConnect(true)
    } catch (err) {
      // 重新抛出异常，以便调用方处理
      throw err
    }
  }, [])

  // 重新解析
  const reparse = useCallback(async () => {
    if (activePaperId === null) return
    setAnalysis(null)
    setLocalError(null)
    setShouldConnect(false)
    try {
      const updated = await reparsePaper(activePaperId)
      setPaper(updated)
      await startAnalysisJob(activePaperId)
      setShouldConnect(true)
    } catch {
      setShouldConnect(false)
      setLocalError('重新解析失败，请检查网络连接后重试')
    }
  }, [activePaperId])

  // 开始分析（从 idle 状态触发）
  const startAnalysis = useCallback(() => {
    if (activePaperId === null) return
    // 后台启动分析任务，再连接 SSE 显示进度
    startAnalysisJob(activePaperId).catch((e: unknown) => {
      setShouldConnect(false)
      const msg = e instanceof Error ? e.message : '启动分析任务失败'
      setLocalError(msg)
      toast({ type: 'error', message: msg })
    })
    setShouldConnect(true)
  }, [activePaperId])

  // 合并状态：
  // - 如果已有完整分析结果，直接显示 done
  // - 如果 paper 已完成但结果未加载，也显示 done
  // - 如果未连接且 paper 待分析，显示 idle（等待用户点击"开始分析"）
  // - 否则使用 streamStatus（正在分析中）
  const status: AnalysisStatus = analysis
    ? 'done'
    : paper?.status === 'done'
    ? 'done'
    : !shouldConnect
    ? 'idle'
    : streamStatus

  const error = localError || streamError || paper?.error_message || null

  // 相关论文是主体完成后才异步返回的，用流式 partialResult 覆盖已加载结果，实现实时更新
  const displayAnalysis: AnalysisResult | null = analysis
    ? (partialResult.related_papers !== undefined
        ? { ...analysis, related_papers: partialResult.related_papers }
        : analysis)
    : null

  return {
    paper,
    analysis: displayAnalysis,
    status,
    progressMessage,
    progress,
    relatedStatus,
    partialResult,
    error,
    uploadAndAnalyze,
    reparse,
    startAnalysis,
  }
}
