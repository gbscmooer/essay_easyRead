/**
 * LoadingState 组件
 *
 * 解析过程中的加载状态展示。
 *
 * Props:
 *   status: AnalysisStatus - 当前解析状态
 *   message: string - 进度消息
 *   partialResult: Partial<AnalysisResult> - 已解析的部分结果
 *
 * 功能：
 *   - 显示当前阶段的 spinner 动画
 *   - 显示进度消息文字
 *   - 显示解析阶段的步骤指示器：
 *     1. 提取文本 ✓/⟳/○
 *     2. 分析内容 ✓/⟳/○
 *     3. 生成流程图 ✓/⟳/○
 *   - 如果有 partialResult，展示已解析的标题等基本信息
 *
 * 样式：
 *   - 居中展示
 *   - Spinner: border-blue-500 动画
 *   - 步骤指示器：竖向排列，已完成为绿色，进行中为蓝色脉冲
 */

import type { AnalysisStatus, AnalysisResult } from '@/types'


interface LoadingStateProps {
  status: AnalysisStatus
  message: string
  partialResult: Partial<AnalysisResult>
}

const steps = [
  { key: 'extracting', label: '提取文本' },
  { key: 'analyzing', label: '分析内容' },
  { key: 'generating', label: '生成流程图' },
] as const

function getStepStatus(stepKey: string, currentStatus: AnalysisStatus): 'done' | 'active' | 'pending' {
  const order = ['extracting', 'analyzing', 'generating']
  const currentIdx = order.indexOf(currentStatus)
  const stepIdx = order.indexOf(stepKey)

  if (currentStatus === 'done') return 'done'
  if (currentStatus === 'failed') return stepIdx < currentIdx ? 'done' : 'pending'
  if (stepIdx < currentIdx) return 'done'
  if (stepIdx === currentIdx) return 'active'
  return 'pending'
}

export function LoadingState({ status, message, partialResult }: LoadingStateProps) {
  return (
    <div className="flex flex-col items-center justify-center p-8 gap-6">
      {/* Spinner */}
      {status !== 'done' && status !== 'failed' && (
        <div className="w-10 h-10 border-3 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
      )}

      {/* 进度消息 */}
      {message && (
        <p className="text-gray-300 text-sm">{message}</p>
      )}

      {/* 步骤指示器 */}
      <div className="flex flex-col gap-3 w-full max-w-xs">
        {steps.map(step => {
          const stepStatus = getStepStatus(step.key, status)
          return (
            <div key={step.key} className="flex items-center gap-3">
              <span
                className={`w-5 h-5 flex items-center justify-center text-xs rounded-full shrink-0 ${
                  stepStatus === 'done'
                    ? 'bg-green-600 text-white'
                    : stepStatus === 'active'
                    ? 'bg-blue-500 text-white animate-pulse'
                    : 'bg-gray-700 text-gray-500'
                }`}
              >
                {stepStatus === 'done' ? '✓' : stepStatus === 'active' ? '⟳' : '○'}
              </span>
              <span
                className={`text-sm ${
                  stepStatus === 'done'
                    ? 'text-green-400'
                    : stepStatus === 'active'
                    ? 'text-blue-400'
                    : 'text-gray-500'
                }`}
              >
                {step.label}
              </span>
            </div>
          )
        })}
      </div>

      {/* 已解析的基本信息 */}
      {partialResult.title && (
        <div className="mt-4 text-center">
          <p className="text-sm text-gray-500 mb-1">已识别标题</p>
          <p className="text-gray-200 font-medium">{partialResult.title}</p>
        </div>
      )}

      {/* 错误状态 */}
      {status === 'failed' && (
        <p className="text-red-400 text-sm">解析失败，请重试</p>
      )}
    </div>
  )
}
