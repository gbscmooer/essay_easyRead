import { useState, useCallback } from 'react'
import type { AnalysisResult } from '@/types'
import { textAction } from '@/api/textAction'


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
      <div className="text-center text-gray-500 py-8">
        <p>暂无问答数据</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-400">共 {result.interview_qa.length} 道题</p>

      <div className="space-y-3">
        {result.interview_qa.map((qa, idx) => {
          const isExpanded = expandedSet.has(idx)
          const isLoadingExplain = loadingExplain.has(idx)
          const explanation = explanations[idx]

          return (
            <div
              key={idx}
              className="bg-gray-800/50 border border-gray-700/50 rounded-lg overflow-hidden"
            >
              {/* 问题 */}
              <button
                onClick={() => toggle(idx)}
                className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-gray-800 transition-colors"
              >
                <span className="flex-shrink-0 w-6 h-6 flex items-center justify-center bg-blue-500/20 text-blue-400 rounded-full text-xs font-medium mt-0.5">
                  Q{idx + 1}
                </span>
                <span className="text-sm text-gray-200 leading-relaxed flex-1">
                  {qa.q}
                </span>
                <svg
                  className={`w-4 h-4 text-gray-500 shrink-0 mt-1 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
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
                      <span className="text-xs text-green-400 font-medium">回答</span>
                      {qa.source && (
                        <span className="text-xs text-gray-500 bg-gray-700/50 px-2 py-0.5 rounded">
                          {qa.source}
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-300 leading-relaxed">
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
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-gray-700 text-gray-300 rounded hover:bg-gray-600 disabled:opacity-50 transition-colors"
                    >
                      {isLoadingExplain ? (
                        <>
                          <div className="w-3 h-3 border border-gray-500 border-t-blue-400 rounded-full animate-spin" />
                          生成中...
                        </>
                      ) : (
                        '进一步解释'
                      )}
                    </button>
                  </div>

                  {/* 解释 */}
                  {explanation && (
                    <div className="pl-9 bg-blue-500/5 border border-blue-500/20 rounded-lg p-3">
                      <p className="text-xs text-blue-400 font-medium mb-1">深入解释</p>
                      <p className="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap">
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
