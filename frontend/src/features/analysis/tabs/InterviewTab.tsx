/**
 * InterviewTab 组件
 *
 * 面试 Tab：以翻转卡片形式展示面试问答。
 *
 * Props:
 *   result: AnalysisResult - 解析结果
 *
 * 展示内容：
 *   - 问答卡片网格布局
 *   - 每张卡片：
 *     - 正面：面试问题（Q）
 *     - 背面：参考回答（A）
 *     - 点击翻转（CSS 3D transform）
 *   - 顶部统计：共 X 道题
 *
 * 交互：
 *   - 点击卡片翻转
 *   - 翻转动画：rotateY(180deg), 0.6s
 *   - 鼠标悬停时卡片微微上浮
 *
 * 样式：
 *   - 网格：grid-cols-1 md:grid-cols-2, gap-4
 *   - 卡片：perspective 容器, min-h-48（最小高度，允许内容撑开）
 *   - 正面：bg-gray-800, 居中文字
 *   - 背面：bg-blue-900/30, 左对齐文字, overflow-auto（长回答可滚动）
 *   - 翻转：transform-style: preserve-3d, backface-visibility: hidden
 */

import { useState } from 'react'
import type { AnalysisResult } from '@/types'


interface InterviewTabProps {
  result: AnalysisResult
}

export function InterviewTab({ result }: InterviewTabProps) {
  const [flippedSet, setFlippedSet] = useState<Set<number>>(new Set())

  const toggle = (idx: number) => {
    setFlippedSet(prev => {
      const next = new Set(prev)
      if (next.has(idx)) {
        next.delete(idx)
      } else {
        next.add(idx)
      }
      return next
    })
  }

  if (!result.interview_qa || result.interview_qa.length === 0) {
    return (
      <div className="text-center text-gray-500 py-8">
        <p>暂无面试问答数据</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* 统计 */}
      <p className="text-sm text-gray-400">共 {result.interview_qa.length} 道题，点击卡片查看答案</p>

      {/* 卡片网格 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {result.interview_qa.map((qa, idx) => {
          const isFlipped = flippedSet.has(idx)
          return (
            <div
              key={idx}
              className={`flip-card ${isFlipped ? 'flipped' : ''} min-h-48 cursor-pointer`}
              onClick={() => toggle(idx)}
            >
              <div className="flip-card-inner relative w-full min-h-48">
                {/* 正面 */}
                <div className="flip-card-front absolute inset-0 bg-gray-800 border border-gray-700 rounded-lg p-4 flex flex-col items-center justify-center text-center hover:translate-y-[-2px] transition-transform">
                  <span className="text-xs text-blue-400 font-medium mb-2">Q{idx + 1}</span>
                  <p className="text-gray-200 text-sm leading-relaxed">{qa.q}</p>
                  <span className="mt-auto pt-3 text-xs text-gray-500">点击翻转查看答案</span>
                </div>

                {/* 背面 */}
                <div className="flip-card-back absolute inset-0 bg-blue-900/30 border border-blue-800/50 rounded-lg p-4 overflow-auto">
                  <span className="text-xs text-blue-400 font-medium mb-2 block">A{idx + 1} 参考答案</span>
                  <p className="text-gray-300 text-sm leading-relaxed">{qa.a}</p>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
