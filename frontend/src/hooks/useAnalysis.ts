/**
 * useAnalysis Hook
 *
 * 管理论文解析的完整生命周期：上传 → 流式接收 → 完成。
 * 组合了 papers API 和 useStreaming。
 *
 * 重要：useStreaming 在组件顶层无条件调用，传入 activePaperId state。
 * uploadAndAnalyze 上传成功后 setState(activePaperId) 触发 SSE 连接。
 */

import type { Paper, AnalysisResult, AnalysisStatus } from '@/types'


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
 *
 * 实现要点：
 * 1. 内部 state: activePaperId: number | null
 * 2. 顶层调用 useStreaming(activePaperId) — 永远在顶层，无条件
 * 3. uploadAndAnalyze(file):
 *    - 调用 uploadPaper(file) 上传
 *    - setState(activePaperId = paper.id) → 触发 useStreaming 连接
 * 4. reparse():
 *    - 调用 reparsePaper(activePaperId)
 *    - useStreaming 自动重连（key 变化或手动 reconnect）
 * 5. 初始加载：
 *    - initialPaperId 传入时直接 setState(activePaperId)
 *    - 如果 paper.status === 'done'，直接 fetch 完整结果
 *    - 否则 useStreaming 自动连接 SSE
 *
 * 使用示例：
 * ```tsx
 * // 新上传
 * const { uploadAndAnalyze, status, analysis } = useAnalysis()
 *
 * // 加载已有论文
 * const { status, analysis } = useAnalysis(paperId)
 * ```
 */
// export function useAnalysis(initialPaperId?: number): UseAnalysisReturn
