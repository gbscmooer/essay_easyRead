/**
 * AnalysisPanel 组件
 *
 * 解析结果展示的主面板，包含 Tab 切换。
 *
 * Props:
 *   result: AnalysisResult - 完整解析结果
 *   partialResult: Partial<AnalysisResult> - 流式接收中的部分结果
 *   status: AnalysisStatus - 解析状态
 *   progressMessage: string - 进度消息
 *
 * Tab 结构：
 *   1. 概览 (OverviewTab) - 默认选中
 *   2. 方法 (MethodTab)
 *   3. 实验 (ExperimentsTab)
 *   4. 代码 (CodeTab)
 *   5. 面试 (InterviewTab)
 *
 * 功能：
 *   - 顶部 Tab 栏切换不同视图
 *   - 如果 status 不是 done，显示 LoadingState
 *   - 如果 status 是 done，显示对应 Tab 的内容
 *   - 每个 Tab 独立渲染，切换时不丢失状态
 *
 * 样式：
 *   - Tab 栏：sticky top-0, bg-gray-900, border-b
 *   - Tab 按钮：px-4, py-2, 选中时 text-blue-400, border-b-2, border-blue-400
 *   - 内容区：p-6, overflow-auto
 */

import { useState } from 'react'
import type { AnalysisResult, AnalysisStatus } from '@/types'
import { LoadingState } from '@/components/LoadingState'
import { OverviewTab } from './tabs/OverviewTab'
import { OutlineTab } from './tabs/OutlineTab'
import { MethodTab } from './tabs/MethodTab'
import { ExperimentsTab } from './tabs/ExperimentsTab'
import { CodeTab } from './tabs/CodeTab'
import { InterviewTab } from './tabs/InterviewTab'


interface AnalysisPanelProps {
  result: AnalysisResult | null
  partialResult: Partial<AnalysisResult>
  status: AnalysisStatus
  progressMessage: string
  onJumpToPage?: (page: number) => void
  paperId?: number
  onStartAnalysis?: () => void
  onReanalyze?: () => void
}

const tabs = [
  { key: 'overview', label: '概览' },
  { key: 'outline', label: '大纲' },
  { key: 'method', label: '方法' },
  { key: 'experiments', label: '实验' },
  { key: 'code', label: '代码' },
  { key: 'interview', label: 'Q&A' },
] as const

type TabKey = typeof tabs[number]['key']

export function AnalysisPanel({ result, partialResult, status, progressMessage, onJumpToPage, paperId, onStartAnalysis, onReanalyze }: AnalysisPanelProps) {
  const [activeTab, setActiveTab] = useState<TabKey>('overview')

  // 空闲状态：显示"开始分析"按钮
  if (status === 'idle' && !result) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-6 p-8">
        <div className="w-16 h-16 rounded-full bg-gray-800 flex items-center justify-center">
          <svg className="w-8 h-8 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
            />
          </svg>
        </div>
        <div className="text-center">
          <h3 className="text-lg font-medium text-gray-300 mb-2">尚未启用全局分析</h3>
          <p className="text-sm text-gray-500 mb-6">点击下方按钮，使用 AI 对论文进行深度分析</p>
          <button
            onClick={onStartAnalysis}
            className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
          >
            AI 分析
          </button>
        </div>
      </div>
    )
  }

  // 如果正在解析中，显示 LoadingState
  if (status !== 'done' || !result) {
    return (
      <div className="flex flex-col h-full">
        <LoadingState status={status} message={progressMessage} partialResult={partialResult} />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Tab 栏 */}
      <div className="flex items-center gap-0 sticky top-0 z-10 bg-gray-900 border-b border-gray-800 shrink-0">
        {tabs.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors relative ${
              activeTab === tab.key
                ? 'text-blue-400'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {tab.label}
            {activeTab === tab.key && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-blue-400" />
            )}
          </button>
        ))}
        <div className="flex-1" />
        {onReanalyze && (
          <button
            onClick={onReanalyze}
            className="px-3 py-1.5 mr-2 text-xs text-gray-400 hover:text-blue-400 hover:bg-gray-800 rounded transition-colors"
            title="全部重新分析"
          >
            <svg className="w-3.5 h-3.5 inline-block mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            重新分析
          </button>
        )}
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-auto p-6">
        {activeTab === 'overview' && <OverviewTab result={result} paperId={paperId} />}
        {activeTab === 'outline' && <OutlineTab result={result} onJumpToPage={onJumpToPage} />}
        {activeTab === 'method' && <MethodTab result={result} />}
        {activeTab === 'experiments' && <ExperimentsTab result={result} />}
        {activeTab === 'code' && <CodeTab paperId={paperId} />}
        {activeTab === 'interview' && <InterviewTab result={result} />}
      </div>
    </div>
  )
}
