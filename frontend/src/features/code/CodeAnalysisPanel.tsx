import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import mermaid from 'mermaid'
import type { CodeAnalysisResult, DependencyEdge, PaperCodeMapping } from '@/types'
import { getCodeFile } from '@/api/code'
import { useThemeMode } from '@/theme/useTheme'


interface CodeAnalysisPanelProps {
  analysis: CodeAnalysisResult | null
  isAnalyzing: boolean
  paperId?: number
  onFileClick?: (path: string, line?: number) => void
  onCollapse?: () => void
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


function nodeIdHash(path: string): string {
  let h = 0
  for (let i = 0; i < path.length; i++) {
    h = ((h << 5) - h + path.charCodeAt(i)) | 0
  }
  return 'n_' + Math.abs(h).toString(36)
}

function sanitizeLabel(path: string): string {
  return path.replace(/[\[\]{}()#"']/g, '')
}

function edgesToMermaid(edges: DependencyEdge[]): string {
  const lines = ['graph LR']
  const seen = new Set<string>()
  for (const edge of edges) {
    const srcId = nodeIdHash(edge.source)
    const tgtId = nodeIdHash(edge.target)
    if (!seen.has(srcId)) {
      lines.push(`  ${srcId}["${sanitizeLabel(edge.source)}"]`)
      seen.add(srcId)
    }
    if (!seen.has(tgtId)) {
      lines.push(`  ${tgtId}["${sanitizeLabel(edge.target)}"]`)
      seen.add(tgtId)
    }
    const label = edge.imports.length > 0 ? sanitizeLabel(edge.imports.join(', ')) : ''
    const arrow = edge.circular ? '-.->' : '-->'
    if (label) {
      lines.push(`  ${srcId} ${arrow}|"${label}"| ${tgtId}`)
    } else {
      lines.push(`  ${srcId} ${arrow} ${tgtId}`)
    }
  }
  return lines.join('\n')
}

let mermaidMode: 'light' | 'dark' | null = null
function ensureMermaid(mode: 'light' | 'dark') {
  if (mermaidMode === mode) return
  const cs = getComputedStyle(document.documentElement)
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
  mermaid.initialize({
    startOnLoad: false,
    theme: mode === 'dark' ? 'dark' : 'default',
    themeVariables: {
      primaryColor: v('--bg-elevated', '#1f2937'),
      primaryTextColor: v('--text-primary', '#e5e7eb'),
      primaryBorderColor: v('--border-light', '#6b7280'),
      lineColor: v('--text-muted', '#6b7280'),
      secondaryColor: v('--bg-surface', '#1f2937'),
      tertiaryColor: v('--bg-base', '#111827'),
      background: v('--bg-surface', '#1f2937'),
      mainBkg: v('--bg-elevated', '#1f2937'),
      nodeBorder: v('--border-light', '#6b7280'),
      nodeTextColor: v('--text-primary', '#e5e7eb'),
      clusterBkg: v('--bg-base', '#111827'),
      clusterBorder: v('--border', '#374151'),
      edgeLabelBackground: v('--bg-elevated', '#1f2937'),
    },
  })
  mermaidMode = mode
}

function stripEventHandlers(svg: string): string {
  return svg
    .replace(/\bon\w+\s*=\s*["'][^"']*["']/gi, '')
    .replace(/\bon\w+\s*=\s*\{[^}]*\}/gi, '')
}


function buildLabelToPathMap(edges: DependencyEdge[]): Map<string, string> {
  const map = new Map<string, string>()
  for (const edge of edges) {
    const srcLabel = sanitizeLabel(edge.source)
    const tgtLabel = sanitizeLabel(edge.target)
    if (!map.has(srcLabel)) map.set(srcLabel, edge.source)
    if (!map.has(tgtLabel)) map.set(tgtLabel, edge.target)
  }
  return map
}

function DependencyGraph({
  edges,
  onFileClick,
  onFullscreen,
}: {
  edges: DependencyEdge[]
  onFileClick?: (path: string) => void
  onFullscreen?: () => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [svg, setSvg] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  const mode = useThemeMode()
  const chart = useMemo(() => edgesToMermaid(edges), [edges])
  const labelToPath = useMemo(() => buildLabelToPathMap(edges), [edges])

  useEffect(() => {
    if (!chart) return
    ensureMermaid(mode)
    const id = `dep-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    mermaid.render(id, chart).then(
      ({ svg: rendered }) => { setSvg(stripEventHandlers(rendered)); setError(null) },
      (err) => { setError(err?.message || '渲染失败'); setSvg('') },
    )
  }, [chart, mode])

  useEffect(() => {
    if (!svg || !containerRef.current) return
    const container = containerRef.current
    const nodes = container.querySelectorAll('g.node')
    const cleanups: Array<() => void> = []
    nodes.forEach((node) => {
      const text = node.textContent?.trim()
      if (!text) return
      const filePath = labelToPath.get(text)
      if (filePath && onFileClick) {
        (node as HTMLElement).style.cursor = 'pointer'
        const handler = () => onFileClick(filePath)
        node.addEventListener('click', handler)
        cleanups.push(() => node.removeEventListener('click', handler))
      }
    })
    return () => { cleanups.forEach(fn => fn()) }
  }, [svg, labelToPath, onFileClick])

  if (error) {
    return (
      <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700/50">
        <p className="text-red-400 text-xs mb-2">依赖图渲染失败</p>
        <pre className="text-gray-500 text-xs whitespace-pre-wrap">{chart}</pre>
      </div>
    )
  }

  return (
    <div className="relative">
      <div ref={containerRef} className="mermaid bg-gray-800/30 rounded-lg p-4 overflow-auto flex justify-center">
        {svg ? (
          <div dangerouslySetInnerHTML={{ __html: svg }} />
        ) : (
          <div className="flex items-center justify-center py-8">
            <div className="w-5 h-5 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
          </div>
        )}
      </div>
      {svg && onFullscreen && (
        <button
          onClick={onFullscreen}
          className="absolute top-2 right-2 p-1.5 rounded bg-gray-800/80 hover:bg-gray-700/80 text-gray-400 hover:text-gray-200 transition-colors"
          title="全屏查看"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 3H5a2 2 0 0 0-2 2v3" /><path d="M21 8V5a2 2 0 0 0-2-2h-3" />
            <path d="M3 16v3a2 2 0 0 0 2 2h3" /><path d="M16 21h3a2 2 0 0 0 2-2v-3" />
          </svg>
        </button>
      )}
    </div>
  )
}


function FullscreenGraph({
  edges,
  onFileClick,
  onClose,
}: {
  edges: DependencyEdge[]
  onFileClick?: (path: string) => void
  onClose: () => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [svg, setSvg] = useState<string>('')
  const [zoom, setZoom] = useState(3)
  const [error, setError] = useState<string | null>(null)

  const mode = useThemeMode()
  const chart = useMemo(() => edgesToMermaid(edges), [edges])
  const labelToPath = useMemo(() => buildLabelToPathMap(edges), [edges])

  useEffect(() => {
    if (!chart) return
    ensureMermaid(mode)
    const id = `dep-fs-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    mermaid.render(id, chart).then(
      ({ svg: rendered }) => { setSvg(stripEventHandlers(rendered)); setError(null) },
      (err) => { setError(err?.message || '渲染失败'); setSvg('') },
    )
  }, [chart, mode])

  useEffect(() => {
    if (!svg || !containerRef.current) return
    const container = containerRef.current
    const nodes = container.querySelectorAll('g.node')
    const cleanups: Array<() => void> = []
    nodes.forEach((node) => {
      const text = node.textContent?.trim()
      if (!text) return
      const filePath = labelToPath.get(text)
      if (filePath && onFileClick) {
        (node as HTMLElement).style.cursor = 'pointer'
        const handler = () => onFileClick(filePath)
        node.addEventListener('click', handler)
        cleanups.push(() => node.removeEventListener('click', handler))
      }
    })
    return () => { cleanups.forEach(fn => fn()) }
  }, [svg, labelToPath, onFileClick])

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  const zoomIn = useCallback(() => setZoom((z) => Math.min(3, +(z + 0.2).toFixed(1))), [])
  const zoomOut = useCallback(() => setZoom((z) => Math.max(0.5, +(z - 0.2).toFixed(1))), [])
  const zoomReset = useCallback(() => setZoom(3), [])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative w-[85vw] h-[80vh] bg-gray-900 rounded-xl shadow-2xl border border-gray-700 flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-700 shrink-0">
          <span className="text-sm font-medium text-gray-200">依赖图</span>
          <div className="flex items-center gap-2">
            <button onClick={zoomOut} className="px-2 py-1 text-xs rounded bg-gray-800 hover:bg-gray-700 text-gray-300 transition-colors">缩小</button>
            <span className="text-xs text-gray-400 w-10 text-center">{Math.round(zoom * 100)}%</span>
            <button onClick={zoomIn} className="px-2 py-1 text-xs rounded bg-gray-800 hover:bg-gray-700 text-gray-300 transition-colors">放大</button>
            <button onClick={zoomReset} className="px-2 py-1 text-xs rounded bg-gray-800 hover:bg-gray-700 text-gray-300 transition-colors">重置</button>
            <button onClick={onClose} className="ml-2 p-1 rounded hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-auto flex items-center justify-center p-8">
          {error ? (
            <p className="text-red-400 text-sm">{error}</p>
          ) : svg ? (
            <div ref={containerRef} style={{ transform: `scale(${zoom})`, transformOrigin: 'center center' }}>
              <div dangerouslySetInnerHTML={{ __html: svg }} />
            </div>
          ) : (
            <div className="w-6 h-6 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
          )}
        </div>
      </div>
    </div>
  )
}

function DependencyTab({ edges, onFileClick }: { edges: DependencyEdge[]; onFileClick?: (path: string) => void }) {
  const [showFullscreen, setShowFullscreen] = useState(false)

  if (!edges || edges.length === 0) {
    return <div className="text-gray-500 text-sm text-center py-8">暂无依赖分析</div>
  }

  const grouped = new Map<string, DependencyEdge[]>()
  for (const edge of edges) {
    const list = grouped.get(edge.source) || []
    list.push(edge)
    grouped.set(edge.source, list)
  }

  return (
    <div className="space-y-4">
      <DependencyGraph
        edges={edges}
        onFileClick={onFileClick}
        onFullscreen={() => setShowFullscreen(true)}
      />

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

      {showFullscreen && (
        <FullscreenGraph
          edges={edges}
          onFileClick={onFileClick}
          onClose={() => setShowFullscreen(false)}
        />
      )}
    </div>
  )
}


function findFunctionLine(content: string, funcName: string): number | null {
  const name = funcName.includes('.') ? funcName.split('.').pop()! : funcName
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const patterns = [
    new RegExp(`^\\s*def\\s+${escaped}\\b`),
    new RegExp(`^\\s*function\\s+${escaped}\\b`),
    new RegExp(`^\\s*func\\s+${escaped}\\b`),
    new RegExp(`^\\s*class\\s+${escaped}\\b`),
    new RegExp(`^\\s*${escaped}\\s*=`),
    new RegExp(`^\\s*${escaped}\\s*:`),
  ]
  const lines = content.split('\n')
  for (let i = 0; i < lines.length; i++) {
    for (const pat of patterns) {
      if (pat.test(lines[i])) return i + 1
    }
  }
  return null
}

function FunctionNameButton({
  funcName,
  paperId,
  filePath,
  onFileClick,
}: {
  funcName: string
  paperId: number
  filePath: string
  onFileClick: (path: string, line?: number) => void
}) {
  const [loading, setLoading] = useState(false)

  const handleClick = async () => {
    setLoading(true)
    try {
      const res = await getCodeFile(paperId, filePath)
      if (res.content) {
        const line = findFunctionLine(res.content, funcName)
        if (line != null) {
          onFileClick(filePath, line)
        } else {
          onFileClick(filePath)
        }
      } else {
        onFileClick(filePath)
      }
    } catch {
      onFileClick(filePath)
    } finally {
      setLoading(false)
    }
  }

  return (
    <button
      onClick={handleClick}
      disabled={loading}
      className="text-xs font-mono text-blue-400 hover:text-blue-300 transition-colors cursor-pointer disabled:opacity-50"
    >
      {loading ? '...' : funcName}
    </button>
  )
}

function PaperMappingTab({
  mappings,
  onFileClick,
  paperId,
}: {
  mappings: PaperCodeMapping[]
  onFileClick?: (path: string, line?: number) => void
  paperId?: number
}) {
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
              {paperId != null && onFileClick ? (
                <FunctionNameButton
                  funcName={m.function}
                  paperId={paperId}
                  filePath={m.file}
                  onFileClick={onFileClick}
                />
              ) : (
                <span className="text-xs font-mono text-gray-300">{m.function}</span>
              )}
            </div>
            <p className="text-xs text-gray-400 leading-relaxed">{m.explanation}</p>
          </div>
        </div>
      ))}
    </div>
  )
}


export function CodeAnalysisPanel({ analysis, isAnalyzing, paperId, onFileClick, onCollapse: _onCollapse }: CodeAnalysisPanelProps) {
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
            {activeTab === 'mapping' && <PaperMappingTab mappings={analysis?.paper_code_mapping || []} onFileClick={onFileClick} paperId={paperId} />}
          </>
        )}
      </div>
    </div>
  )
}
