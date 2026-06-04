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
}

const tabs = [
  { key: 'overview', label: '概览' },
  { key: 'outline', label: '大纲' },
  { key: 'method', label: '方法' },
  { key: 'experiments', label: '实验' },
  { key: 'code', label: '代码' },
  { key: 'interview', label: '面试' },
] as const

type TabKey = typeof tabs[number]['key']

export function AnalysisPanel({ result, partialResult, status, progressMessage }: AnalysisPanelProps) {
  const [activeTab, setActiveTab] = useState<TabKey>('overview')

  // 如果未完成解析，显示 LoadingState
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
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-auto p-6">
        {activeTab === 'overview' && <OverviewTab result={result} />}
        {activeTab === 'outline' && <OutlineTab result={result} />}
        {activeTab === 'method' && <MethodTab result={result} />}
        {activeTab === 'experiments' && <ExperimentsTab result={result} />}
        {activeTab === 'code' && <CodeTab result={result} />}
        {activeTab === 'interview' && <InterviewTab result={result} />}
      </div>
    </div>
  )
}
