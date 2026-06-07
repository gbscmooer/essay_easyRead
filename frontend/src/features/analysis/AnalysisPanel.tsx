/**
 * AnalysisPanel 组件
 *
 * 解析结果展示的主面板，包含 Tab 切换。
 * 清新学术风格：简洁 Tab 栏 + 清晰内容区。
 */

import { useState, useEffect } from 'react'
import type { AnalysisResult, AnalysisStatus } from '@/types'
import { LoadingState } from '@/components/LoadingState'
import { OverviewTab } from './tabs/OverviewTab'
import { OutlineTab } from './tabs/OutlineTab'
import { MethodTab } from './tabs/MethodTab'
import { ExperimentsTab } from './tabs/ExperimentsTab'
import { CodeTab } from './tabs/CodeTab'
import { InterviewTab } from './tabs/InterviewTab'
import { NotesPanel } from './NotesPanel'


interface AnalysisPanelProps {
  result: AnalysisResult | null
  partialResult: Partial<AnalysisResult>
  status: AnalysisStatus
  progressMessage: string
  progress?: number
  relatedStatus?: 'idle' | 'loading' | 'ok' | 'failed'
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
  { key: 'notes', label: '笔记' },
] as const

type TabKey = typeof tabs[number]['key']

export function AnalysisPanel({ result, partialResult, status, progressMessage, progress, relatedStatus, onJumpToPage, paperId, onStartAnalysis, onReanalyze }: AnalysisPanelProps) {
  const [activeTab, setActiveTab] = useState<TabKey>('overview')
  const [showReanalyzeConfirm, setShowReanalyzeConfirm] = useState(false)

  // Keyboard shortcuts: 1-7 to switch tabs
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const num = parseInt(e.key)
      if (num >= 1 && num <= tabs.length) {
        e.preventDefault()
        setActiveTab(tabs[num - 1].key)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  // 空闲状态：显示"开始分析"按钮
  if (status === 'idle' && !result) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-6 p-8">
        <div className="w-16 h-16 rounded-full flex items-center justify-center" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
          <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--text-muted)' }}>
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
            />
          </svg>
        </div>
        <div className="text-center">
          <h3 className="text-lg font-medium mb-2" style={{ color: 'var(--text-primary)' }}>尚未分析</h3>
          <p className="text-sm mb-6" style={{ color: 'var(--text-muted)' }}>点击下方按钮开始分析这篇论文</p>
          <button
            onClick={onStartAnalysis}
            className="px-6 py-2.5 text-sm font-medium rounded-lg transition-colors"
            style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
          >
            开始分析
          </button>
        </div>
      </div>
    )
  }

  // 如果正在解析中，显示 LoadingState
  if (status !== 'done' || !result) {
    return (
      <div className="flex flex-col h-full">
        <LoadingState status={status} message={progressMessage} partialResult={partialResult} progress={progress} />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Tab 栏 */}
      <div className="flex items-center gap-0 sticky top-0 z-10 shrink-0" style={{
        backgroundColor: 'var(--bg-surface)',
        borderBottom: '1px solid var(--border)',
      }}>
        {tabs.map((tab, i) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className="px-4 py-2.5 text-sm font-medium transition-colors relative"
            title={`${tab.label} (${i + 1})`}
            style={
              activeTab === tab.key
                ? { color: 'var(--accent)' }
                : { color: 'var(--text-muted)' }
            }
          >
            {tab.label}
            {activeTab === tab.key && (
              <span className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full" style={{ backgroundColor: 'var(--accent)' }} />
            )}
          </button>
        ))}
        <div className="flex-1" />
        {onReanalyze && (
          <>
            <button
              onClick={() => setShowReanalyzeConfirm(true)}
              className="px-3 py-1.5 mr-2 text-xs rounded-md transition-colors"
              style={{ color: 'var(--text-muted)' }}
              title="全部重新分析"
            >
              <svg className="w-3.5 h-3.5 inline-block mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              重新分析
            </button>
            {showReanalyzeConfirm && (
              <div
                className="fixed inset-0 z-[200] flex items-center justify-center"
                style={{ backgroundColor: 'rgba(0,0,0,0.45)' }}
                onMouseDown={() => setShowReanalyzeConfirm(false)}
              >
                <div
                  className="w-80 rounded-xl border p-5 shadow-2xl"
                  style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)' }}
                  onMouseDown={e => e.stopPropagation()}
                >
                  <div className="flex items-start gap-3 mb-4">
                    <span className="mt-0.5 text-amber-400 shrink-0">
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                      </svg>
                    </span>
                    <div>
                      <p className="text-sm font-medium mb-1" style={{ color: 'var(--text-primary)' }}>确认重新分析？</p>
                      <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                        将消耗大量 Token 重新对整篇论文进行 AI 解析，原有分析结果将被覆盖。
                      </p>
                    </div>
                  </div>
                  <div className="flex justify-end gap-2">
                    <button
                      onClick={() => setShowReanalyzeConfirm(false)}
                      className="px-3 py-1.5 text-xs rounded-md border transition-colors hover:opacity-80"
                      style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
                    >
                      取消
                    </button>
                    <button
                      onClick={() => { setShowReanalyzeConfirm(false); onReanalyze() }}
                      className="px-3 py-1.5 text-xs rounded-md transition-colors"
                      style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
                    >
                      确认重新分析
                    </button>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-auto p-6">
        {activeTab === 'overview' && <OverviewTab result={result} paperId={paperId} relatedStatus={relatedStatus} />}
        {activeTab === 'outline' && <OutlineTab result={result} onJumpToPage={onJumpToPage} />}
        {activeTab === 'method' && <MethodTab result={result} />}
        {activeTab === 'experiments' && <ExperimentsTab result={result} onJumpToPage={onJumpToPage} />}
        {activeTab === 'code' && <CodeTab paperId={paperId} />}
        {activeTab === 'interview' && <InterviewTab result={result} />}
        {activeTab === 'notes' && paperId && <NotesPanel paperId={paperId} />}
      </div>
    </div>
  )
}
