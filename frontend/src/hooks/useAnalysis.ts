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
import { uploadPaper, getPaper, getAnalysis, reparsePaper } from '@/api/papers'
import { useStreaming } from './useStreaming'


interface UseAnalysisReturn {
  /** 当前论文记录 */
  paper: Paper | null
  /** 完整解析结果（解析完成后有值） */
  analysis: AnalysisResult | null
  /** 解析状态 */
  status: AnalysisStatus
  /** 进度消息 */
  progressMessage: string
  /** 部分解析结果（流式接收中） */
  partialResult: Partial<AnalysisResult>
  /** 错误信息 */
  error: string | null
  /** 上传并开始解析 */
  uploadAndAnalyze: (file: File) => Promise<void>
  /** 重新解析 */
  reparse: () => Promise<void>
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

  // 顶层无条件调用 useStreaming
  const {
    status: streamStatus,
    partialResult,
    error: streamError,
    progressMessage,
    reconnect,
  } = useStreaming(activePaperId)

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
        getAnalysis(initialPaperId).then(result => {
          if (!cancelled) setAnalysis(result)
        }).catch(() => {
          // 解析结果获取失败，SSE 会自动尝试
        })
      }
    }).catch(() => {
      // 论文加载失败
    })

    return () => { cancelled = true }
  }, [initialPaperId])

  // 当 SSE 完成时，获取完整结果
  useEffect(() => {
    if (streamStatus === 'done' && activePaperId !== null) {
      getAnalysis(activePaperId).then(result => {
        setAnalysis(result)
      }).catch(() => {
        // 获取失败，partialResult 已有数据
      })
    }
  }, [streamStatus, activePaperId])

  // 上传并开始解析
  const uploadAndAnalyze = useCallback(async (file: File) => {
    setAnalysis(null)
    setPaper(null)
    const uploaded = await uploadPaper(file)
    setPaper(uploaded)
    setActivePaperId(uploaded.id)
  }, [])

  // 重新解析
  const reparse = useCallback(async () => {
    if (activePaperId === null) return
    setAnalysis(null)
    const updated = await reparsePaper(activePaperId)
    setPaper(updated)
    reconnect()
  }, [activePaperId, reconnect])

  // 合并状态：优先使用 streamStatus，但如果 paper 已完成则为 done
  const status: AnalysisStatus = paper?.status === 'done' && streamStatus === 'idle'
    ? 'done'
    : streamStatus

  const error = streamError || paper?.error_message || null

  return {
    paper,
    analysis,
    status,
    progressMessage,
    partialResult,
    error,
    uploadAndAnalyze,
    reparse,
  }
}
