import { useMemo } from 'react'
import type { AnalysisStatus, AnalysisResult } from '@/types'


interface LoadingStateProps {
  status: AnalysisStatus
  message: string
  partialResult: Partial<AnalysisResult>
}

// 分析阶段产出的字段（按典型到达顺序）
const ANALYSIS_FIELDS = [
  { key: 'title', label: '标题' },
  { key: 'venue', label: '会议' },
  { key: 'year', label: '年份' },
  { key: 'one_sentence', label: '核心贡献' },
  { key: 'problem', label: '研究问题' },
  { key: 'key_contributions', label: '核心贡献' },
  { key: 'outline', label: '大纲' },
  { key: 'method', label: '方法论' },
  { key: 'experiments', label: '实验' },
  { key: 'code_mapping', label: '代码映射' },
  { key: 'interview_qa', label: '面试问答' },
  { key: 'limitations', label: '局限性' },
  { key: 'related_papers', label: '相关论文' },
  { key: 'mermaid_chart', label: '流程图' },
] as const

const TOTAL_FIELDS = ANALYSIS_FIELDS.length

const STEPS = [
  { key: 'extracting', label: '提取文本', icon: '📄' },
  { key: 'analyzing', label: '分析中', icon: '🧠' },
] as const


function countReceivedFields(partialResult: Partial<AnalysisResult>): number {
  let count = 0
  for (const field of ANALYSIS_FIELDS) {
    const value = partialResult[field.key as keyof AnalysisResult]
    if (value !== undefined && value !== null) {
      count++
    }
  }
  return count
}


function getStepProgress(status: AnalysisStatus, receivedFields: number): number {
  if (status === 'done') return 100
  if (status === 'failed') return 0
  if (status === 'idle') return 0

  // extracting: 0-10%
  if (status === 'extracting') return 5

  // generating: 5-95% based on fields received
  if (status === 'generating') {
    const fieldProgress = receivedFields / TOTAL_FIELDS
    return Math.round(5 + fieldProgress * 90)
  }

  // analyzing: 10-95% based on fields received
  if (status === 'analyzing') {
    const fieldProgress = receivedFields / TOTAL_FIELDS
    return Math.round(10 + fieldProgress * 85)
  }

  return 0
}


export function LoadingState({ status, message, partialResult }: LoadingStateProps) {
  const receivedFields = useMemo(
    () => countReceivedFields(partialResult),
    [partialResult]
  )

  const progress = useMemo(
    () => getStepProgress(status, receivedFields),
    [status, receivedFields]
  )

  const isActive = status !== 'done' && status !== 'failed' && status !== 'idle'

  return (
    <div className="flex flex-col h-full">
      {/* 顶部进度条 */}
      <div className="h-1 shrink-0" style={{ backgroundColor: 'var(--bg-surface)' }}>
        <div
          className="h-full transition-all duration-500 ease-out"
          style={{
            width: `${progress}%`,
            backgroundColor: status === 'failed' ? '#ef4444' : 'var(--accent)',
          }}
        />
      </div>

      <div className="flex-1 flex flex-col items-center justify-center p-6 gap-5 max-w-md mx-auto w-full">
        {/* 百分比数字 */}
        {isActive && (
          <div className="text-3xl font-light tabular-nums" style={{ color: 'var(--accent)' }}>
            {progress}%
          </div>
        )}

        {/* 步骤指示器 */}
        <div className="w-full space-y-2">
          {STEPS.map(step => {
            const isCurrentStep =
              (step.key === 'extracting' && (status === 'extracting')) ||
              (step.key === 'analyzing' && (status === 'analyzing'))

            const isDone =
              (step.key === 'extracting' && ['analyzing', 'done'].includes(status)) ||
              (step.key === 'analyzing' && status === 'done')

            return (
              <div
                key={step.key}
                className="flex items-center gap-3 px-3 py-2 rounded-lg transition-colors"
                style={{
                  backgroundColor: isCurrentStep ? 'var(--accent-subtle)' : isDone ? 'rgba(34,197,94,0.05)' : 'transparent',
                  border: isCurrentStep ? '1px solid rgba(37,99,235,0.2)' : isDone ? '1px solid rgba(34,197,94,0.15)' : '1px solid transparent',
                }}
              >
                {/* 状态图标 */}
                <div className="w-6 h-6 flex items-center justify-center rounded-full text-xs shrink-0" style={{
                  backgroundColor: isDone ? '#22c55e' : isCurrentStep ? 'var(--accent)' : 'var(--bg-surface)',
                  color: isDone || isCurrentStep ? '#ffffff' : 'var(--text-muted)',
                }}>
                  {isDone ? '\u2713' : isCurrentStep ? (
                    <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  ) : '\u25CB'}
                </div>

                {/* 步骤信息 */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium" style={{
                      color: isDone ? '#16a34a' : isCurrentStep ? 'var(--accent)' : 'var(--text-muted)',
                    }}>
                      {step.label}
                    </span>
                    {isCurrentStep && step.key === 'analyzing' && (
                      <span className="text-xs tabular-nums" style={{ color: 'var(--accent)', opacity: 0.7 }}>
                        {receivedFields}/{TOTAL_FIELDS}
                      </span>
                    )}
                  </div>

                  {/* 分析阶段的子进度条 */}
                  {isCurrentStep && step.key === 'analyzing' && (
                    <div className="mt-1.5 h-1 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--bg-surface)' }}>
                      <div
                        className="h-full transition-all duration-300"
                        style={{ width: `${(receivedFields / TOTAL_FIELDS) * 100}%`, backgroundColor: 'var(--accent)', opacity: 0.6 }}
                      />
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {/* 当前状态消息 */}
        {message && (
          <p className="text-xs text-center" style={{ color: 'var(--text-muted)' }}>{message}</p>
        )}

        {/* 已接收的字段标签 */}
        {status === 'analyzing' && receivedFields > 0 && (
          <div className="w-full">
            <p className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>已解析：</p>
            <div className="flex flex-wrap gap-1.5">
              {ANALYSIS_FIELDS.map(field => {
                const value = partialResult[field.key as keyof AnalysisResult]
                const received = value !== undefined && value !== null
                return (
                  <span
                    key={field.key}
                    className="text-xs px-2 py-0.5 rounded-full transition-colors"
                    style={received
                      ? { backgroundColor: 'rgba(34,197,94,0.1)', color: '#16a34a', border: '1px solid rgba(34,197,94,0.2)' }
                      : { backgroundColor: 'var(--bg-surface)', color: 'var(--text-muted)', border: '1px solid var(--border)' }
                    }
                  >
                    {field.label}
                  </span>
                )
              })}
            </div>
          </div>
        )}

        {/* 已识别的标题 */}
        {partialResult.title && (
          <div className="w-full rounded-lg p-3" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
            <p className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>已识别论文</p>
            <p className="text-sm font-medium leading-snug" style={{ color: 'var(--text-primary)' }}>
              {partialResult.title}
            </p>
          </div>
        )}

        {/* 错误状态 */}
        {status === 'failed' && (
          <div className="text-center">
            <p className="text-sm mb-2" style={{ color: '#dc2626' }}>解析失败</p>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>请在左侧点击「重试」按钮重新分析</p>
          </div>
        )}
      </div>
    </div>
  )
}
