import { useState, useEffect } from 'react'
import type { AnalysisResult, RelatedPaper } from '@/types'
import { refreshRelatedPapers } from '@/api/papers'


interface OverviewTabProps {
  result: AnalysisResult
  paperId?: number
}

export function OverviewTab({ result, paperId }: OverviewTabProps) {
  const [relatedPapers, setRelatedPapers] = useState<RelatedPaper[]>(result.related_papers || [])
  const [isRefreshing, setIsRefreshing] = useState(false)

  useEffect(() => {
    setRelatedPapers(result.related_papers || [])
  }, [result.related_papers])

  const handleRefresh = async () => {
    if (!paperId) return
    setIsRefreshing(true)
    try {
      const papers = await refreshRelatedPapers(paperId)
      setRelatedPapers(papers)
    } catch {
      // 保持现有数据
    } finally {
      setIsRefreshing(false)
    }
  }

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

      {/* 作者 */}
      {result.authors && (
        <p className="text-sm text-gray-400">{result.authors}</p>
      )}

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
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-200">相关论文推荐</h2>
          {paperId && (
            <button
              onClick={handleRefresh}
              disabled={isRefreshing}
              className="flex items-center gap-1.5 px-3 py-1 text-xs text-gray-400 hover:text-blue-400 bg-gray-800 hover:bg-gray-700 rounded transition-colors disabled:opacity-50"
            >
              {isRefreshing ? (
                <>
                  <div className="w-3 h-3 border border-gray-500 border-t-blue-400 rounded-full animate-spin" />
                  搜索中...
                </>
              ) : (
                <>
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  刷新
                </>
              )}
            </button>
          )}
        </div>

        {relatedPapers.length > 0 ? (
          <div className="space-y-3">
            {relatedPapers.map((paper, idx) => (
              <div key={idx} className="bg-gray-800/50 rounded-lg p-3 border border-gray-700/50">
                <p className="text-sm font-medium text-gray-200 mb-1">
                  {paper.url && paper.url.match(/^https?:\/\//) ? (
                    <a href={paper.url} target="_blank" rel="noopener noreferrer"
                       className="hover:text-blue-400 transition-colors">
                      {paper.title}
                    </a>
                  ) : paper.title}
                </p>
                {paper.authors && (
                  <p className="text-xs text-gray-400 mb-1">{paper.authors}</p>
                )}
                <div className="flex flex-wrap items-center gap-2 mb-1.5">
                  {paper.venue && (
                    <span className="text-xs bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">
                      {paper.venue}
                    </span>
                  )}
                  {paper.year > 0 && (
                    <span className="text-xs text-gray-500">{paper.year}</span>
                  )}
                  {paper.citation_count > 0 && (
                    <span className="text-xs text-yellow-500/80">
                      {paper.citation_count} citations
                    </span>
                  )}
                  {paper.doi && (
                    <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noopener noreferrer"
                       className="text-xs text-blue-400/70 hover:text-blue-400 transition-colors">
                      DOI: {paper.doi}
                    </a>
                  )}
                </div>
                {paper.relevance && (
                  <p className="text-xs text-gray-500 italic">{paper.relevance}</p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-6">
            <p className="text-sm text-gray-500 mb-2">暂无相关论文推荐</p>
            {paperId && (
              <button
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="text-xs text-blue-400 hover:text-blue-300 transition-colors disabled:opacity-50"
              >
                {isRefreshing ? '搜索中...' : '通过 Semantic Scholar 搜索'}
              </button>
            )}
          </div>
        )}
      </section>

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
