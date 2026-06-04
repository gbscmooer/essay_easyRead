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

import type { AnalysisResult, StreamEvent, AnalysisStatus } from '@/types'


interface UseStreamingReturn {
  /** 当前解析状态 */
  status: AnalysisStatus
  /** 已接收的部分解析结果（字段逐步填充） */
  partialResult: Partial<AnalysisResult>
  /** 错误信息（status='failed' 时有值） */
  error: string | null
  /** 进度消息（如 "正在分析方法论..."） */
  progressMessage: string
  /** 手动重连 */
  reconnect: () => void
}


/**
 * 连接 SSE 端点，逐步接收解析结果。
 *
 * @param paperId 论文 ID，为 null 时不连接
 * @returns 解析状态和部分结果
 *
 * 实现要点：
 * 1. paperId 为 null → status='idle'，不建立连接
 * 2. paperId 变化 → useEffect 中：
 *    a. 创建 EventSource(GET /api/analysis/{paperId}/stream)
 *    b. 监听 status/field/done/error 四种事件
 *    c. status → 更新 progressMessage
 *    d. field → 根据 field_name 写入 partialResult
 *       - field_name="method" → data.value 对应 Method 类型
 *       - field_name="experiments" → data.value 对应 Experiments 类型
 *    e. done → status='done'
 *    f. error → status='failed' + error
 * 3. EventSource.onerror → 自动重连（最多 3 次，间隔 1s/2s/4s）
 * 4. 组件卸载或 paperId 变化 → 关闭 EventSource
 *
 * 使用示例：
 * ```tsx
 * // 在组件顶层无条件调用
 * const { status, partialResult, progressMessage } = useStreaming(paperId)
 *
 * if (status === 'analyzing') {
 *   return <ProgressBar message={progressMessage} />
 * }
 * if (partialResult.title) {
 *   // 可以提前展示已解析的字段
 * }
 * ```
 */
// export function useStreaming(paperId: number | null): UseStreamingReturn
