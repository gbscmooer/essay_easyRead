import type { AnalysisResult } from '@/types'


interface OutlineTabProps {
  result: AnalysisResult
  onJumpToPage?: (page: number) => void
}

export function OutlineTab({ result, onJumpToPage }: OutlineTabProps) {
  if (!result.outline || result.outline.length === 0) {
    return (
      <div className="text-gray-500 text-sm text-center py-8">
        暂无大纲信息
      </div>
    )
  }

  const parsePage = (page: string): number | null => {
    if (!page) return null
    const match = String(page).match(/\d+/)
    return match ? parseInt(match[0], 10) : null
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-gray-200">论文大纲</h2>
      <p className="text-gray-500 text-xs">点击章节跳转到论文对应位置</p>

      <div className="relative">
        <div className="absolute left-3 top-2 bottom-2 w-px bg-gray-700" />

        <div className="space-y-2">
          {result.outline.map((item, idx) => {
            const pageNum = parsePage(item.page)
            const clickable = !!pageNum && !!onJumpToPage

            return (
              <div key={idx} className="relative pl-10">
                <div className="absolute left-1.5 top-2.5 w-3 h-3 rounded-full bg-blue-500 border-2 border-gray-900 z-10" />

                <button
                  onClick={() => pageNum && onJumpToPage?.(pageNum)}
                  disabled={!clickable}
                  className={`w-full text-left bg-gray-800/50 rounded-lg p-3 border border-gray-700/50 transition-colors ${
                    clickable
                      ? 'hover:bg-gray-800 hover:border-blue-500/30 cursor-pointer'
                      : 'cursor-default'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <h3 className={`text-sm font-medium ${
                      clickable ? 'text-blue-300 hover:text-blue-200' : 'text-gray-200'
                    }`}>
                      {item.section}
                    </h3>
                    {item.page && (
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        clickable
                          ? 'text-blue-400 bg-blue-500/10'
                          : 'text-gray-500 bg-gray-800'
                      }`}>
                        p.{item.page}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-gray-400 leading-relaxed">
                    {item.summary}
                  </p>
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
