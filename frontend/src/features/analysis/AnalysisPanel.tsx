/**
 * AnalysisPanel 组件
 *
 * 论文分析结果展示面板。
 * 药丸形 Tab 指示器 + 精致排版 + 28px 内容区留白。
 */

import React, { useState, useEffect, useRef, useCallback, Suspense } from 'react'
import type { AnalysisResult, AnalysisStatus } from '@/types'
import { LoadingState, SkeletonAnalysis } from '@/ds-entry'
import { OverviewTab } from './tabs/OverviewTab'
import { OutlineTab } from './tabs/OutlineTab'
import { MethodTab } from './tabs/MethodTab'

const ExperimentsTab = React.lazy(() => import('./tabs/ExperimentsTab'))
const CodeTab = React.lazy(() => import('./tabs/CodeTab'))
const InterviewTab = React.lazy(() => import('./tabs/InterviewTab'))

function TabLoadingFallback() {
  return <SkeletonAnalysis />
}
import { NotesPanel } from './NotesPanel'

interface AnalysisPanelProps {
  result: AnalysisResult | null
  partialResult: Partial<AnalysisResult>
  status: AnalysisStatus
  progressMessage: string
  progress?: number
  relatedStatus?: 'idle' | 'loading' | 'ok' | 'failed'
  onJumpToPage?: (page: number, section?: string) => void
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
  const tabRefs = useRef<Map<TabKey, HTMLButtonElement>>(new Map())

  const registerTabRef = useCallback((key: TabKey, el: HTMLButtonElement | null) => {
    if (el) tabRefs.current.set(key, el)
    else tabRefs.current.delete(key)
  }, [])

  const handleTabKeyDown = useCallback((e: React.KeyboardEvent) => {
    const idx = tabs.findIndex(t => t.key === activeTab)
    if (e.key === 'ArrowRight') {
      e.preventDefault()
      const next = tabs[(idx + 1) % tabs.length]
      setActiveTab(next.key)
      tabRefs.current.get(next.key)?.focus()
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      const prev = tabs[(idx - 1 + tabs.length) % tabs.length]
      setActiveTab(prev.key)
      tabRefs.current.get(prev.key)?.focus()
    }
  }, [activeTab])

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

  // 空闲状态
  if (status === 'idle' && !result) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-8 p-8">
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center"
          style={{
            backgroundColor: 'var(--bg-surface)',
            border: '1px solid var(--border)',
          }}
        >
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"
            style={{ color: 'var(--text-muted)' }}>
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
            />
          </svg>
        </div>
        <div className="text-center">
          <h3 className="text-heading text-[17px] mb-2" style={{ color: 'var(--text-primary)' }}>
            尚未分析
          </h3>
          <p className="text-[13px] mb-6" style={{ color: 'var(--text-muted)' }}>
            点击下方按钮开始分析这篇论文
          </p>
          <button
            onClick={onStartAnalysis}
            className="px-6 py-2.5 text-[13px] font-medium rounded-full transition-all duration-200 focus-glow press"
            style={{
              backgroundColor: 'var(--accent)',
              color: '#ffffff',
              transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
            }}
          >
            开始分析
          </button>
        </div>
      </div>
    )
  }

  // 解析中
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
      <div
        role="tablist"
        className="flex items-center gap-0.5 sticky top-0 z-10 shrink-0 px-2 py-1.5"
        style={{
          backgroundColor: 'var(--bg-surface)',
          borderBottom: '1px solid var(--border)',
        }}
      >
        {tabs.map((tab, i) => {
          const isActive = activeTab === tab.key
          return (
            <button
              key={tab.key}
              ref={el => registerTabRef(tab.key, el)}
              role="tab"
              aria-selected={isActive}
              aria-controls={`panel-${tab.key}`}
              tabIndex={isActive ? 0 : -1}
              onClick={() => setActiveTab(tab.key)}
              onKeyDown={handleTabKeyDown}
              className="px-3 py-1.5 text-[13px] transition-all duration-200 rounded-full relative focus-glow press"
              title={`${tab.label} (${i + 1})`}
              style={{
                color: isActive ? 'var(--accent)' : 'var(--text-muted)',
                backgroundColor: isActive ? 'var(--accent-subtle)' : 'transparent',
                fontWeight: isActive ? 500 : 400,
                transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
              }}
            >
              {tab.label}
            </button>
          )
        })}

        <div className="flex-1" />

        {onReanalyze && (
          <>
            <button
              onClick={() => setShowReanalyzeConfirm(true)}
              aria-label="重新分析"
              className="px-2.5 py-1.5 mr-1 text-[12px] rounded-full transition-all duration-200 focus-glow press"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent' }}
              title="全部重新分析"
            >
              <svg className="w-3 h-3 inline-block mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              重新分析
            </button>
            {showReanalyzeConfirm && (
              <div
                className="fixed inset-0 z-[200] flex items-center justify-center"
                style={{
                  backgroundColor: 'rgba(0,0,0,0.4)',
                  backdropFilter: 'blur(8px)',
                  WebkitBackdropFilter: 'blur(8px)',
                }}
                onMouseDown={() => setShowReanalyzeConfirm(false)}
              >
                <div
                  className="w-[340px] p-6 shadow-2xl animate-reveal-scale"
                  style={{
                    backgroundColor: 'var(--bg-elevated)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--seed-radius, 12px)',
                  }}
                  onMouseDown={e => e.stopPropagation()}
                >
                  <div className="flex items-start gap-3.5 mb-5">
                    <span
                      className="shrink-0 flex items-center justify-center w-9 h-9 rounded-full"
                      style={{
                        color: 'var(--warning)',
                        backgroundColor: 'var(--warning-subtle)',
                      }}
                    >
                      <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                      </svg>
                    </span>
                    <div>
                      <p className="text-[14px] font-medium mb-1.5" style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
                        确认重新分析？
                      </p>
                      <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                        将消耗大量 Token 重新对整篇论文进行 AI 解析，原有分析结果将被覆盖。
                      </p>
                    </div>
                  </div>
                  <div className="flex justify-end gap-2.5">
                    <button
                      onClick={() => setShowReanalyzeConfirm(false)}
                      className="px-4 py-2 text-[13px] rounded-full transition-all duration-200 focus-glow press"
                      style={{
                        color: 'var(--text-secondary)',
                        backgroundColor: 'transparent',
                        border: '1px solid var(--border)',
                        cursor: 'pointer',
                      }}
                    >
                      取消
                    </button>
                    <button
                      onClick={() => { setShowReanalyzeConfirm(false); onReanalyze() }}
                      className="px-4 py-2 text-[13px] rounded-full font-medium transition-all duration-200 focus-glow press"
                      style={{
                        backgroundColor: 'var(--accent)',
                        color: '#fff',
                        border: 'none',
                        cursor: 'pointer',
                      }}
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

      {/* 内容区 — 28px 留白 */}
      <div role="tabpanel" id={`panel-${activeTab}`} className="flex-1 overflow-auto px-7 py-6">
        {/* 切 Tab 时按 key 重挂载并淡入上移，内容更有节奏感 */}
        <div key={activeTab} className="animate-reveal-up">
          {activeTab === 'overview' && <OverviewTab result={result} paperId={paperId} analysisStatus={status} relatedStatus={relatedStatus} />}
          {activeTab === 'outline' && <OutlineTab result={result} onJumpToPage={onJumpToPage} />}
          {activeTab === 'method' && <MethodTab result={result} />}
          {activeTab === 'experiments' && (
            <Suspense fallback={<TabLoadingFallback />}>
              <ExperimentsTab result={result} onJumpToPage={onJumpToPage} />
            </Suspense>
          )}
          {activeTab === 'code' && (
            <Suspense fallback={<TabLoadingFallback />}>
              <CodeTab paperId={paperId} />
            </Suspense>
          )}
          {activeTab === 'interview' && (
            <Suspense fallback={<TabLoadingFallback />}>
              <InterviewTab result={result} />
            </Suspense>
          )}
          {activeTab === 'notes' && paperId && <NotesPanel paperId={paperId} />}
        </div>
      </div>
    </div>
  )
}
