import { useMemo } from 'react'
import type { AnalysisStatus, AnalysisResult } from '@/types'


interface LoadingStateProps {
  status: AnalysisStatus
  message: string
  partialResult: Partial<AnalysisResult>
  /** 后端计算的进度百分比（0-100）。<0 时回退到前端字段估算 */
  progress?: number
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

  // 提取阶段：0-10%
  if (status === 'extracting') return 5

  // 生成阶段：5-95%，根据已接收字段数计算
  if (status === 'generating') {
    const fieldProgress = receivedFields / TOTAL_FIELDS
    return Math.round(5 + fieldProgress * 90)
  }

  // 分析阶段：10-95%，根据已接收字段数计算
  if (status === 'analyzing') {
    const fieldProgress = receivedFields / TOTAL_FIELDS
    return Math.round(10 + fieldProgress * 85)
  }

  return 0
}


export function LoadingState({ status, message, partialResult, progress: serverProgress = -1 }: LoadingStateProps) {
  const receivedFields = useMemo(
    () => countReceivedFields(partialResult),
    [partialResult]
  )

  // 优先使用后端计算的进度；无 progress 事件（如旧连接、回放）时回退到前端估算
  const estimated = useMemo(
    () => getStepProgress(status, receivedFields),
    [status, receivedFields]
  )
  const progress = status === 'done'
    ? 100
    : serverProgress >= 0
    ? Math.max(serverProgress, estimated)
    : estimated

  const isActive = status !== 'done' && status !== 'failed' && status !== 'idle'

  return (
    <div className="flex flex-col h-full">
      {/* 顶部进度条 */}
      <div className="h-1 shrink-0 overflow-hidden" style={{ backgroundColor: 'var(--bg-surface)' }}>
        <div
          className="h-full"
          style={{
            width: `${progress}%`,
            backgroundColor: status === 'failed' ? 'var(--error)' : 'var(--accent)',
            transition: 'width 0.5s var(--ease-press), background-color 0.3s var(--ease-press)',
          }}
        />
      </div>

      <div className="flex-1 flex flex-col items-center justify-center p-6 gap-5 max-w-md mx-auto w-full">
        {/* 百分比数字 */}
        {isActive && (
          <div
            className="text-4xl font-light tabular-nums leading-none"
            style={{
              color: 'var(--accent)',
              transition: 'color 0.3s var(--ease-press)',
            }}
          >
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
                  backgroundColor: isCurrentStep ? 'var(--accent-subtle)' : isDone ? 'var(--success-subtle)' : 'transparent',
                  border: isCurrentStep ? '1px solid var(--accent)' : isDone ? '1px solid var(--success)' : '1px solid transparent',
                }}
              >
                {/* 状态图标 */}
                <div className="w-6 h-6 flex items-center justify-center rounded-full text-xs font-semibold shrink-0" style={{
                  backgroundColor: isDone ? 'var(--success)' : isCurrentStep ? 'var(--accent)' : 'var(--bg-surface)',
                  color: isDone || isCurrentStep ? 'var(--bg-base)' : 'var(--text-muted)',
                  border: isDone || isCurrentStep ? '1px solid transparent' : '1px solid var(--border)',
                  transition: 'background-color 0.3s var(--ease-press), color 0.3s var(--ease-press)',
                }}>
                  {isDone ? '\u2713' : isCurrentStep ? (
                    <div
                      className="w-2.5 h-2.5 rounded-full"
                      style={{
                        backgroundColor: 'var(--bg-base)',
                        animation: 'skeleton-breathe 1.5s ease-in-out infinite',
                      }}
                      role="status"
                    ><span className="sr-only">加载中</span></div>
                  ) : '\u25CB'}
                </div>

                {/* 步骤信息 */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium" style={{
                      color: isDone ? 'var(--success)' : isCurrentStep ? 'var(--accent)' : 'var(--text-muted)',
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
                    className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full transition-all duration-300"
                    style={received
                      ? { backgroundColor: 'var(--success-subtle)', color: 'var(--success)', border: '1px solid transparent' }
                      : { backgroundColor: 'var(--bg-surface)', color: 'var(--text-muted)', border: '1px solid var(--border)' }
                    }
                  >
                    {received && <span aria-hidden="true" className="text-[10px] leading-none">✓</span>}
                    {field.label}
                  </span>
                )
              })}
            </div>
          </div>
        )}

        {/* 已识别的标题 */}
        {partialResult.title && (
          <div className="w-full rounded-lg p-3 animate-reveal-up" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
            <p className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>已识别论文</p>
            <p className="text-sm font-medium leading-snug" style={{ color: 'var(--text-primary)' }}>
              {partialResult.title}
            </p>
          </div>
        )}

        {/* 错误状态 */}
        {status === 'failed' && (
          <div
            className="w-full flex flex-col items-center text-center rounded-lg px-4 py-5 animate-reveal-up"
            style={{ backgroundColor: 'var(--error-subtle)', border: '1px solid var(--border-light)' }}
          >
            <div
              className="w-10 h-10 rounded-full flex items-center justify-center mb-3"
              style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--error)' }}>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
            </div>
            <p className="text-sm font-medium mb-1.5" style={{ color: 'var(--error)' }}>解析失败</p>
            <p className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>请在左侧点击「重试」按钮重新分析</p>
          </div>
        )}
      </div>
    </div>
  )
}
