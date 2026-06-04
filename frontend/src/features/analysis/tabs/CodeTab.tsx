/**
 * CodeTab 组件
 *
 * 代码 Tab：展示论文概念到代码实现的映射。
 *
 * Props:
 *   result: AnalysisResult - 解析结果
 *
 * 展示内容：
 *   1. 代码映射列表
 *      - 每项是一个卡片
 *      - 左侧：论文概念/公式（学术风格）
 *      - 右侧：代码实现思路（代码风格，等宽字体）
 *      - 可以展开/折叠
 *   2. 关键模块的伪代码框架（如果有）
 *      - PyTorch 风格
 *      - 语法高亮（可用简单的 CSS 实现）
 *
 * 样式：
 *   - 卡片：bg-gray-800, border, border-gray-700, 圆角
 *   - 概念区：左侧蓝色竖线
 *   - 代码区：bg-gray-900, font-mono, p-4
 *   - 深色主题的语法高亮
 */

import { useState } from 'react'
import type { AnalysisResult } from '@/types'


interface CodeTabProps {
  result: AnalysisResult
}

export function CodeTab({ result }: CodeTabProps) {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(0)

  if (!result.code_mapping || result.code_mapping.length === 0) {
    return (
      <div className="text-center text-gray-500 py-8">
        <p>暂无代码映射数据</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-gray-200 mb-3">概念 → 代码映射</h2>

      {result.code_mapping.map((mapping, idx) => {
        const isExpanded = expandedIndex === idx
        return (
          <div
            key={idx}
            className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden"
          >
            {/* 标题栏：可点击展开/折叠 */}
            <button
              onClick={() => setExpandedIndex(isExpanded ? null : idx)}
              className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-750 transition-colors"
            >
              <div className="flex items-center gap-3">
                <span className="w-1 h-6 bg-blue-500 rounded-full" />
                <span className="text-gray-200 font-medium text-sm">{mapping.concept}</span>
              </div>
              <svg
                className={`w-4 h-4 text-gray-500 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {/* 代码区 */}
            {isExpanded && (
              <div className="border-t border-gray-700">
                <div className="bg-gray-900 p-4 font-mono text-sm text-gray-300 overflow-x-auto whitespace-pre-wrap">
                  {mapping.likely_implementation}
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
