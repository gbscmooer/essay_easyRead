import type { AnalysisResult } from '@/types'


interface OutlineTabProps {
  result: AnalysisResult
}

export function OutlineTab({ result }: OutlineTabProps) {
  if (!result.outline || result.outline.length === 0) {
    return (
      <div className="text-gray-500 text-sm text-center py-8">
        暂无大纲信息
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-gray-200">论文大纲</h2>
      <p className="text-gray-500 text-xs">按章节结构了解论文内容</p>

      <div className="relative">
        {/* 时间线竖线 */}
        <div className="absolute left-3 top-2 bottom-2 w-px bg-gray-700" />

        <div className="space-y-3">
          {result.outline.map((item, idx) => (
            <div key={idx} className="relative pl-10">
              {/* 节点 */}
              <div className="absolute left-1.5 top-1.5 w-3 h-3 rounded-full bg-blue-500 border-2 border-gray-900 z-10" />

              <div className="bg-gray-800/50 rounded-lg p-3 border border-gray-700/50">
                <div className="flex items-center justify-between mb-1.5">
                  <h3 className="text-sm font-medium text-gray-200">
                    {item.section}
                  </h3>
                  {item.page && (
                    <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">
                      p.{item.page}
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-400 leading-relaxed">
                  {item.summary}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
