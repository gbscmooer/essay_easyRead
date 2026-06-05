import { useState, useEffect, useRef, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import mermaid from 'mermaid'
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
    <div className="prose prose-invert prose-sm max-w-none text-sm leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{dataFlow}</ReactMarkdown>
    </div>
  )
}


function sanitizeNodeId(path: string): string {
  return path.replace(/[^a-zA-Z0-9]/g, '_')
}

function sanitizeLabel(path: string): string {
  return path.replace(/[\[\]{}()#"']/g, '')
}

function edgesToMermaid(edges: DependencyEdge[]): string {
  const lines = ['graph LR']
  const seen = new Set<string>()
  for (const edge of edges) {
    const srcId = sanitizeNodeId(edge.source)
    const tgtId = sanitizeNodeId(edge.target)
    if (!seen.has(srcId)) {
      lines.push(`  ${srcId}["${sanitizeLabel(edge.source)}"]`)
      seen.add(srcId)
    }
    if (!seen.has(tgtId)) {
      lines.push(`  ${tgtId}["${sanitizeLabel(edge.target)}"]`)
      seen.add(tgtId)
    }
    const label = edge.imports.length > 0 ? sanitizeLabel(edge.imports.slice(0, 3).join(', ')) : ''
    if (label) {
      lines.push(`  ${srcId} -->|"${label}"| ${tgtId}`)
    } else {
      lines.push(`  ${srcId} --> ${tgtId}`)
    }
  }
  return lines.join('\n')
}

let mermaidInit = false
function ensureMermaid() {
  if (!mermaidInit) {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      themeVariables: {
        primaryColor: '#3b82f6',
        primaryTextColor: '#e5e7eb',
        primaryBorderColor: '#6b7280',
        lineColor: '#6b7280',
        secondaryColor: '#1f2937',
        tertiaryColor: '#111827',
        background: '#1f2937',
        mainBkg: '#1f2937',
        nodeBorder: '#6b7280',
        clusterBkg: '#111827',
        clusterBorder: '#374151',
        edgeLabelBackground: '#1f2937',
      },
    })
    mermaidInit = true
  }
}

function stripEventHandlers(svg: string): string {
  return svg
    .replace(/\bon\w+\s*=\s*["'][^"']*["']/gi, '')
    .replace(/\bon\w+\s*=\s*\{[^}]*\}/gi, '')
}


function DependencyGraph({ edges }: { edges: DependencyEdge[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [svg, setSvg] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  const chart = useMemo(() => edgesToMermaid(edges), [edges])

  useEffect(() => {
    if (!chart) return
    ensureMermaid()
    const id = `dep-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    mermaid.render(id, chart).then(
      ({ svg: rendered }) => { setSvg(stripEventHandlers(rendered)); setError(null) },
      (err) => { setError(err?.message || '渲染失败'); setSvg('') },
    )
  }, [chart])

  if (error) {
    return (
      <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700/50">
        <p className="text-red-400 text-xs mb-2">依赖图渲染失败</p>
        <pre className="text-gray-500 text-xs whitespace-pre-wrap">{chart}</pre>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="mermaid bg-gray-800/30 rounded-lg p-4 overflow-auto flex justify-center">
      {svg ? (
        <div dangerouslySetInnerHTML={{ __html: svg }} />
      ) : (
        <div className="flex items-center justify-center py-8">
          <div className="w-5 h-5 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
        </div>
      )}
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
    <div className="space-y-4">
      <DependencyGraph edges={edges} />

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
