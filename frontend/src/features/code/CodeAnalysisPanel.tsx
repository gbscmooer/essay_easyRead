import { useState } from 'react'
import type { CodeAnalysisResult, DependencyEdge, PaperCodeMapping } from '@/types'


interface CodeAnalysisPanelProps {
  analysis: CodeAnalysisResult | null
  isAnalyzing: boolean
  onFileClick?: (path: string) => void
}


const tabs = [
  { key: 'dataflow', label: '数据流' },
  { key: 'deps', label: '依赖图' },
  { key: 'mapping', label: '论文映射' },
] as const

type TabKey = typeof tabs[number]['key']


function DataFlowTab({ dataFlow }: { dataFlow: string | null }) {
  if (!dataFlow) {
    return <div className="text-gray-500 text-sm text-center py-8">暂无数据流分析</div>
  }
  return (
    <div className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap">
      {dataFlow}
    </div>
  )
}


function DependencyTab({ edges, onFileClick }: { edges: DependencyEdge[]; onFileClick?: (path: string) => void }) {
  if (!edges || edges.length === 0) {
    return <div className="text-gray-500 text-sm text-center py-8">暂无依赖分析</div>
  }

  // 按 source 分组
  const grouped = new Map<string, DependencyEdge[]>()
  for (const edge of edges) {
    const list = grouped.get(edge.source) || []
    list.push(edge)
    grouped.set(edge.source, list)
  }

  return (
    <div className="space-y-3">
      {Array.from(grouped.entries()).map(([source, deps]) => (
        <div key={source} className="bg-gray-800/50 rounded-lg p-3 border border-gray-700/50">
          <button
            onClick={() => onFileClick?.(source)}
            className="text-xs font-mono text-blue-400 hover:text-blue-300 transition-colors"
          >
            {source}
          </button>
          <div className="mt-1.5 space-y-1">
            {deps.map((dep, idx) => (
              <div key={idx} className="flex items-center gap-2 text-xs">
                <span className="text-gray-600">→</span>
                <button
                  onClick={() => onFileClick?.(dep.target)}
                  className="font-mono text-gray-400 hover:text-gray-200 transition-colors"
                >
                  {dep.target}
                </button>
                {dep.imports.length > 0 && (
                  <span className="text-gray-600">
                    ({dep.imports.join(', ')})
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}


function PaperMappingTab({ mappings, onFileClick }: { mappings: PaperCodeMapping[]; onFileClick?: (path: string) => void }) {
  if (!mappings || mappings.length === 0) {
    return <div className="text-gray-500 text-sm text-center py-8">暂无论文-代码映射</div>
  }

  return (
    <div className="space-y-3">
      {mappings.map((m, idx) => (
        <div key={idx} className="bg-gray-800/50 rounded-lg p-3 border border-gray-700/50">
          <div className="flex items-start gap-2 mb-2">
            <span className="flex-shrink-0 w-5 h-5 flex items-center justify-center bg-blue-500/20 text-blue-400 rounded-full text-xs font-medium">
              {idx + 1}
            </span>
            <span className="text-sm font-medium text-gray-200">{m.concept}</span>
          </div>
          <div className="pl-7 space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500">文件:</span>
              <button
                onClick={() => onFileClick?.(m.file)}
                className="text-xs font-mono text-blue-400 hover:text-blue-300 transition-colors"
              >
                {m.file}
              </button>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500">函数:</span>
              <span className="text-xs font-mono text-gray-300">{m.function}</span>
            </div>
            <p className="text-xs text-gray-400 leading-relaxed">{m.explanation}</p>
          </div>
        </div>
      ))}
    </div>
  )
}


export function CodeAnalysisPanel({ analysis, isAnalyzing, onFileClick }: CodeAnalysisPanelProps) {
  const [activeTab, setActiveTab] = useState<TabKey>('dataflow')

  return (
    <div className="flex flex-col h-full">
      {/* Tab 栏 */}
      <div className="flex items-center gap-0 shrink-0 border-b border-gray-800">
        {tabs.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-3 py-2 text-xs font-medium transition-colors relative ${
              activeTab === tab.key
                ? 'text-blue-400'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {tab.label}
            {activeTab === tab.key && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-blue-400" />
            )}
          </button>
        ))}
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-auto p-4">
        {isAnalyzing && !analysis ? (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <div className="w-6 h-6 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
            <p className="text-sm text-gray-500">正在分析代码...</p>
          </div>
        ) : (
          <>
            {activeTab === 'dataflow' && <DataFlowTab dataFlow={analysis?.data_flow || null} />}
            {activeTab === 'deps' && <DependencyTab edges={analysis?.dependency_graph || []} onFileClick={onFileClick} />}
            {activeTab === 'mapping' && <PaperMappingTab mappings={analysis?.paper_code_mapping || []} onFileClick={onFileClick} />}
          </>
        )}
      </div>
    </div>
  )
}
