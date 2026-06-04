/**
 * OverviewTab 组件
 *
 * 概览 Tab：展示论文的核心信息摘要。
 *
 * Props:
 *   result: AnalysisResult - 解析结果
 *
 * 展示内容：
 *   1. 论文标题（大字）
 *   2. 会议 + 年份（标签样式）
 *   3. 一句话核心贡献（蓝色高亮卡片）
 *   4. 研究问题（Problem 区块）
 *   5. 核心贡献列表（带序号）
 *   6. 相关论文推荐
 *   7. 局限性
 *
 * 样式：
 *   - 标题：text-2xl, font-bold
 *   - one_sentence：bg-blue-500/10, border-l-4, border-blue-500, p-4
 *   - 贡献列表：有序列表，每项有左侧蓝色竖线
 *   - 各区块间有合理间距
 */

import type { AnalysisResult } from '@/types'


interface OverviewTabProps {
  result: AnalysisResult
}

export function OverviewTab({ result }: OverviewTabProps) {
  return (
    <div className="space-y-6">
      {/* 标题 + 会议 + 年份 */}
      <div>
        <h1 className="text-2xl font-bold text-gray-100 mb-2">{result.title}</h1>
        <div className="flex items-center gap-2">
          {result.venue && (
            <span className="px-2.5 py-0.5 text-xs font-medium bg-gray-700 text-gray-300 rounded-full">
              {result.venue}
            </span>
          )}
          {result.year && (
            <span className="px-2.5 py-0.5 text-xs font-medium bg-gray-700 text-gray-300 rounded-full">
              {result.year}
            </span>
          )}
        </div>
      </div>

      {/* 一句话核心贡献 */}
      {result.one_sentence && (
        <div className="bg-blue-500/10 border-l-4 border-blue-500 p-4 rounded-r-lg">
          <p className="text-blue-300 text-sm leading-relaxed">{result.one_sentence}</p>
        </div>
      )}

      {/* 研究问题 */}
      {result.problem && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-2">研究问题</h2>
          <p className="text-gray-300 text-sm leading-relaxed">{result.problem}</p>
        </section>
      )}

      {/* 核心贡献列表 */}
      {result.key_contributions && result.key_contributions.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">核心贡献</h2>
          <ol className="space-y-2">
            {result.key_contributions.map((item, idx) => (
              <li
                key={idx}
                className="flex gap-3 text-sm text-gray-300"
              >
                <span className="flex-shrink-0 w-6 h-6 flex items-center justify-center bg-blue-500/20 text-blue-400 rounded-full text-xs font-medium">
                  {idx + 1}
                </span>
                <span className="leading-relaxed">{item}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* 相关论文推荐 */}
      {result.related_papers && result.related_papers.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">相关论文推荐</h2>
          <ul className="space-y-1.5">
            {result.related_papers.map((paper, idx) => (
              <li key={idx} className="flex items-start gap-2 text-sm text-gray-400">
                <span className="text-gray-600 mt-0.5">-</span>
                <span>{paper}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 局限性 */}
      {result.limitations && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-2">局限性</h2>
          <p className="text-gray-400 text-sm leading-relaxed">{result.limitations}</p>
        </section>
      )}
    </div>
  )
}
