import { useState, useCallback } from 'react'
import type { AnalysisResult } from '@/types'
import { textAction } from '@/api/textAction'

const SPRING = 'cubic-bezier(0.25, 0.46, 0.45, 0.94)'

interface InterviewTabProps {
  result: AnalysisResult
}


export function InterviewTab({ result }: InterviewTabProps) {
  const [expandedSet, setExpandedSet] = useState<Set<number>>(new Set())
  const [explanations, setExplanations] = useState<Record<number, string>>({})
  const [loadingExplain, setLoadingExplain] = useState<Set<number>>(new Set())

  const toggle = (idx: number) => {
    setExpandedSet(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  const handleExplain = useCallback(async (idx: number, q: string, a: string) => {
    if (explanations[idx]) {
      // 已有解释，直接展开
      setExpandedSet(prev => new Set(prev).add(idx))
      return
    }

    setLoadingExplain(prev => new Set(prev).add(idx))
    try {
      const prompt = `请对以下问答进行更深入的解释，用通俗易懂的语言展开说明，补充背景知识和关键细节：\n\n问题：${q}\n回答：${a}`
      const res = await textAction(prompt, 'summarize')
      setExplanations(prev => ({ ...prev, [idx]: res }))
      setExpandedSet(prev => new Set(prev).add(idx))
    } catch {
      setExplanations(prev => ({ ...prev, [idx]: '解释生成失败，请检查配置' }))
    } finally {
      setLoadingExplain(prev => {
        const next = new Set(prev)
        next.delete(idx)
        return next
      })
    }
  }, [explanations])

  if (!result.interview_qa || result.interview_qa.length === 0) {
    return (
      <div className="text-center py-10 text-[13px]" style={{ color: 'var(--text-muted)' }}>
        <p>暂无问答数据</p>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <p className="text-[12px]" style={{ color: 'var(--text-muted)' }}>共 {result.interview_qa.length} 道题</p>

      <div className="space-y-2.5">
        {result.interview_qa.map((qa, idx) => {
          const isExpanded = expandedSet.has(idx)
          const isLoadingExplain = loadingExplain.has(idx)
          const explanation = explanations[idx]

          return (
            <div
              key={idx}
              className="rounded-xl overflow-hidden transition-all duration-200"
              style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', transitionTimingFunction: SPRING }}
            >
              {/* 问题 */}
              <button
                onClick={() => toggle(idx)}
                className="w-full text-left px-4 py-3.5 flex items-start gap-3 transition-colors"
                onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }}
                onMouseLeave={e => { e.currentTarget.style.backgroundColor = '' }}
              >
                <span
                  className="flex-shrink-0 w-6 h-6 flex items-center justify-center rounded-full text-[11px] font-medium mt-0.5 tabular-nums"
                  style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}
                >
                  Q{idx + 1}
                </span>
                <span className="text-[13px] leading-relaxed flex-1" style={{ color: 'var(--text-primary)' }}>
                  {qa.q}
                </span>
                <svg
                  className={`w-4 h-4 shrink-0 mt-1 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                  style={{ color: 'var(--text-muted)' }}
                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>

              {/* 答案区域 */}
              {isExpanded && (
                <div className="px-4 pb-4 space-y-3">
                  {/* 答案 */}
                  <div className="pl-9">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>回答</span>
                      {qa.source && (
                        <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ color: 'var(--text-muted)', backgroundColor: 'var(--bg-elevated)' }}>
                          {qa.source}
                        </span>
                      )}
                    </div>
                    <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                      {qa.a}
                    </p>
                  </div>

                  {/* 操作按钮 */}
                  <div className="pl-9 flex items-center gap-2">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleExplain(idx, qa.q, qa.a)
                      }}
                      disabled={isLoadingExplain}
                      className="flex items-center gap-1.5 px-3.5 py-1.5 text-[12px] rounded-full disabled:opacity-50 transition-all duration-200"
                      style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)', border: '1px solid var(--border)', transitionTimingFunction: SPRING }}
                      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }}
                      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
                    >
                      {isLoadingExplain ? (
                        <>
                          <div className="w-3 h-3 border rounded-full animate-spin" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }} />
                          生成中…
                        </>
                      ) : (
                        '进一步解释'
                      )}
                    </button>
                  </div>

                  {/* 解释 — surface-tint 高亮 */}
                  {explanation && (
                    <div className="ml-9 rounded-xl p-3.5" style={{ backgroundColor: 'var(--accent-subtle)', border: '1px solid var(--border)' }}>
                      <p className="text-label mb-1.5" style={{ color: 'var(--accent)' }}>深入解释</p>
                      <p className="text-[12px] leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--text-secondary)' }}>
                        {explanation}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default InterviewTab
