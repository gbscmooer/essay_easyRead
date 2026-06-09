import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import DOMPurify from 'dompurify'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
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
    return <div className="text-sm text-center py-8" style={{ color: 'var(--text-muted)' }}>暂无数据流分析</div>
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

let mermaidModule: typeof import('mermaid') | null = null
let mermaidMode: 'light' | 'dark' | null = null

async function getMermaidInstance() {
  if (!mermaidModule) {
    mermaidModule = await import('mermaid')
  }
  return mermaidModule.default
}

async function ensureMermaid(mode: 'light' | 'dark') {
  const mermaid = await getMermaidInstance()
  if (mermaidMode === mode) return mermaid
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
  return mermaid
}

function sanitizeSvg(svg: string): string {
  return DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true },
    ADD_TAGS: ['use', 'foreignObject', 'style'],
    ADD_ATTR: ['xlink:href', 'style', 'transform', 'data-id', 'data-index'],
  })
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
    let cancelled = false
    ensureMermaid(mode).then(mermaid => {
      if (cancelled) return
      const id = `dep-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      mermaid.render(id, chart).then(
        ({ svg: rendered }) => { if (!cancelled) { setSvg(sanitizeSvg(rendered)); setError(null) } },
        (err) => { if (!cancelled) { setError(err?.message || '渲染失败'); setSvg('') } },
      )
    })
    return () => { cancelled = true }
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
      <div className="rounded-lg p-4 border" style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-light)' }}>
        <p className="text-xs mb-2" style={{ color: 'var(--error)' }}>依赖图渲染失败</p>
        <pre className="text-xs whitespace-pre-wrap" style={{ color: 'var(--text-muted)' }}>{chart}</pre>
      </div>
    )
  }

  return (
    <div className="relative">
      <div ref={containerRef} className="mermaid rounded-lg p-4 overflow-auto flex justify-center" style={{ backgroundColor: 'var(--bg-elevated)' }}>
        {svg ? (
          <div dangerouslySetInnerHTML={{ __html: svg }} />
        ) : (
          <div className="flex items-center justify-center py-8">
            <div className="w-5 h-5 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }} />
          </div>
        )}
      </div>
      {svg && onFullscreen && (
        <button
          onClick={onFullscreen}
          className="absolute top-2 right-2 p-1.5 rounded transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', opacity: 0.8, color: 'var(--text-secondary)' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)'; (e.currentTarget as HTMLElement).style.opacity = '1' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)'; (e.currentTarget as HTMLElement).style.opacity = '0.8' }}
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
    let cancelled = false
    ensureMermaid(mode).then(mermaid => {
      if (cancelled) return
      const id = `dep-fs-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      mermaid.render(id, chart).then(
        ({ svg: rendered }) => { if (!cancelled) { setSvg(sanitizeSvg(rendered)); setError(null) } },
        (err) => { if (!cancelled) { setError(err?.message || '渲染失败'); setSvg('') } },
      )
    })
    return () => { cancelled = true }
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
      <div className="relative w-[85vw] h-[80vh] rounded-xl shadow-2xl border flex flex-col overflow-hidden"
        style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border-light)' }}>
        <div className="flex items-center justify-between px-4 py-2 border-b shrink-0" style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border-light)' }}>
          <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>依赖图</span>
          <div className="flex items-center gap-2">
            <button onClick={zoomOut} className="px-2 py-1 text-xs rounded transition-colors" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)' }} onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }} onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}>缩小</button>
            <span className="text-xs w-10 text-center" style={{ color: 'var(--text-secondary)' }}>{Math.round(zoom * 100)}%</span>
            <button onClick={zoomIn} className="px-2 py-1 text-xs rounded transition-colors" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)' }} onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }} onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}>放大</button>
            <button onClick={zoomReset} className="px-2 py-1 text-xs rounded transition-colors" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)' }} onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }} onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}>重置</button>
            <button onClick={onClose} className="ml-2 p-1 rounded transition-colors"
              style={{ color: 'var(--text-secondary)' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)'; (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = ''; (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}>
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
            <div className="w-6 h-6 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }} />
          )}
        </div>
      </div>
    </div>
  )
}

function DependencyTab({ edges, onFileClick }: { edges: DependencyEdge[]; onFileClick?: (path: string) => void }) {
  const [showFullscreen, setShowFullscreen] = useState(false)

  if (!edges || edges.length === 0) {
    return <div className="text-sm text-center py-8" style={{ color: 'var(--text-muted)' }}>暂无依赖分析</div>
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
          <div key={source} className="rounded-lg p-3 border" style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-light)' }}>
            <button
              onClick={() => onFileClick?.(source)}
              className="text-xs font-mono text-blue-400 hover:text-blue-300 transition-colors"
            >
              {source}
            </button>
            <div className="mt-1.5 space-y-1">
              {deps.map((dep, idx) => (
                <div key={idx} className="flex items-center gap-2 text-xs">
                  <span style={{ color: 'var(--text-muted)' }}>→</span>
                  <button
                    onClick={() => onFileClick?.(dep.target)}
                    className="font-mono transition-colors" style={{ color: 'var(--text-secondary)' }} onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)' }} onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}
                  >
                    {dep.target}
                  </button>
                  {dep.imports.length > 0 && (
                    <span style={{ color: 'var(--text-muted)' }}>
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
    return <div className="text-sm text-center py-8" style={{ color: 'var(--text-muted)' }}>暂无论文-代码映射</div>
  }

  return (
    <div className="space-y-3">
      {mappings.map((m, idx) => (
        <div key={idx} className="rounded-lg p-3 border" style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-light)' }}>
          <div className="flex items-start gap-2 mb-2">
            <span className="flex-shrink-0 w-5 h-5 flex items-center justify-center bg-blue-500/20 text-blue-400 rounded-full text-xs font-medium">
              {idx + 1}
            </span>
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{m.concept}</span>
          </div>
          <div className="pl-7 space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>文件:</span>
              <button
                onClick={() => onFileClick?.(m.file)}
                className="text-xs font-mono text-blue-400 hover:text-blue-300 transition-colors"
              >
                {m.file}
              </button>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>函数:</span>
              {paperId != null && onFileClick ? (
                <FunctionNameButton
                  funcName={m.function}
                  paperId={paperId}
                  filePath={m.file}
                  onFileClick={onFileClick}
                />
              ) : (
                <span className="text-xs font-mono" style={{ color: 'var(--text-secondary)' }}>{m.function}</span>
              )}
            </div>
            <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{m.explanation}</p>
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
      <div className="flex items-center gap-0 shrink-0 border-b" style={{ borderColor: 'var(--border)' }}>
        {tabs.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className="px-3 py-2 text-xs font-medium transition-colors relative"
              style={activeTab === tab.key ? { color: 'var(--accent)' } : { color: 'var(--text-muted)' }}
              onMouseEnter={e => { if (activeTab !== tab.key) (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}
              onMouseLeave={e => { if (activeTab !== tab.key) (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
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
            <div className="w-6 h-6 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }} />
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>正在分析代码...</p>
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
