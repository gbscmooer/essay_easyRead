import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import hljs from 'highlight.js/lib/core'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { getCodeFile, type NotebookCellData, type NotebookOutput } from '@/api/code'
import { useThemeMode } from '@/theme/useTheme'

// highlight.js 语言按需加载映射
import type { LanguageFn } from 'highlight.js'
import { LoadingSpinner } from '@/ds-entry'
const LANG_MAP: Record<string, () => Promise<{ default: LanguageFn }>> = {
  python: () => import('highlight.js/lib/languages/python'),
  javascript: () => import('highlight.js/lib/languages/javascript'),
  typescript: () => import('highlight.js/lib/languages/typescript'),
  cpp: () => import('highlight.js/lib/languages/cpp'),
  java: () => import('highlight.js/lib/languages/java'),
  bash: () => import('highlight.js/lib/languages/bash'),
  json: () => import('highlight.js/lib/languages/json'),
  yaml: () => import('highlight.js/lib/languages/yaml'),
  markdown: () => import('highlight.js/lib/languages/markdown'),
  xml: () => import('highlight.js/lib/languages/xml'),
  html: () => import('highlight.js/lib/languages/xml'),
  css: () => import('highlight.js/lib/languages/css'),
}

// 已加载语言缓存，避免重复 import
const loadedLangs = new Set<string>()

async function ensureLanguage(lang: string) {
  if (loadedLangs.has(lang)) return
  const loader = LANG_MAP[lang]
  if (loader) {
    const mod = await loader()
    hljs.registerLanguage(lang, mod.default)
    loadedLangs.add(lang)
  }
}


interface CodeViewerProps {
  paperId: number
  filePath: string | null
  scrollToLine?: number | null
  navigationId?: number
}


function getLanguage(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  const langMap: Record<string, string> = {
    py: 'python', js: 'javascript', jsx: 'javascript',
    ts: 'typescript', tsx: 'typescript',
    cpp: 'cpp', cc: 'cpp', c: 'cpp', h: 'cpp', hpp: 'cpp',
    java: 'java', sh: 'bash', bash: 'bash',
    json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'yaml',
    md: 'markdown', html: 'html', xml: 'xml', css: 'css',
  }
  return langMap[ext] || 'plaintext'
}


function isMarkdown(filename: string): boolean {
  return filename.endsWith('.md') || filename.endsWith('.markdown')
}


function isNotebook(filename: string): boolean {
  return filename.endsWith('.ipynb')
}


interface NotebookCell {
  cell_type: 'code' | 'markdown' | 'raw'
  source: string | string[]
  outputs?: Array<{
    output_type: string
    text?: string | string[]
    data?: Record<string, string | string[]>
    ename?: string
    evalue?: string
    execution_count?: number | null
  }>
  execution_count?: number | null
}


function parseNotebook(content: string): NotebookCell[] | null {
  try {
    const nb = JSON.parse(content)
    const cells = nb.cells || []
    return cells.map((cell: Record<string, unknown>) => ({
      ...cell,
      source: Array.isArray(cell.source) ? cell.source.join('') : String(cell.source || ''),
      outputs: (cell.outputs as Array<Record<string, unknown>>)?.map((out: Record<string, unknown>) => ({
        ...out,
        text: Array.isArray(out.text) ? (out.text as string[]).join('') : out.text,
        data: out.data ? Object.fromEntries(
          Object.entries(out.data as Record<string, unknown>).map(([k, v]) => [k, Array.isArray(v) ? (v as string[]).join('') : v])
        ) : undefined,
      })),
    }))
  } catch {
    return null
  }
}


function NotebookRenderer({ cells }: { cells: NotebookCell[] }) {
  return (
    <div className="space-y-2 p-4">
      {cells.map((cell, idx) => (
        <NotebookCellView key={idx} cell={cell} index={idx} />
      ))}
    </div>
  )
}


function NotebookOutputView({ output }: { output: NotebookOutput }) {
  if (output.output_type === 'stream') {
    return (
      <pre className="p-4 overflow-x-auto text-sm whitespace-pre-wrap" style={{ backgroundColor: 'var(--bg-surface)', opacity: 0.5, color: 'var(--text-secondary)' }}>
        {output.text || ''}
      </pre>
    )
  }

  if (output.output_type === 'execute_result' || output.output_type === 'display_data') {
    const data = output.data || {}
    const imageKey = Object.keys(data).find(k => k.startsWith('image/'))
    if (imageKey) {
      const src = data[imageKey].startsWith('data:')
        ? data[imageKey]
        : `data:${imageKey};base64,${data[imageKey]}`
      return (
        <div className="p-4" style={{ backgroundColor: 'var(--bg-surface)', opacity: 0.5 }}>
          <img src={src} alt="notebook output" className="max-w-full mx-auto" />
        </div>
      )
    }
    if (data['text/plain']) {
      return (
        <pre className="p-4 overflow-x-auto text-sm whitespace-pre-wrap" style={{ backgroundColor: 'var(--bg-surface)', opacity: 0.5, color: 'var(--text-secondary)' }}>
          {data['text/plain']}
        </pre>
      )
    }
    if (output.text) {
      return (
        <pre className="p-4 overflow-x-auto text-sm whitespace-pre-wrap" style={{ backgroundColor: 'var(--bg-surface)', opacity: 0.5, color: 'var(--text-secondary)' }}>
          {output.text}
        </pre>
      )
    }
    return null
  }

  if (output.output_type === 'error') {
    return (
      <pre className="p-4 overflow-x-auto bg-red-900/20 text-sm text-red-300 whitespace-pre-wrap">
        {output.ename}: {output.evalue}
      </pre>
    )
  }

  return null
}


function NotebookCellView({ cell, index }: { cell: NotebookCell; index: number }) {
  const codeRef = useRef<HTMLElement>(null)
  const mode = useThemeMode()
  const proseClass = mode === 'dark' ? 'prose prose-invert' : 'prose'
  const source = Array.isArray(cell.source) ? cell.source.join('') : cell.source

  useEffect(() => {
    if (cell.cell_type === 'code' && codeRef.current) {
      const el = codeRef.current
      // 获取语言类名并按需加载
      const langClass = Array.from(el.classList).find(c => c.startsWith('language-'))
      const lang = langClass ? langClass.replace('language-', '') : 'python'
      ensureLanguage(lang).then(() => {
        el.removeAttribute('data-highlighted')
        try {
          hljs.highlightElement(el)
        } catch {
          // ignore
        }
      })
    }
  }, [cell.cell_type, source])

  if (cell.cell_type === 'markdown') {
    return (
      <div className="border overflow-hidden" style={{ borderColor: 'var(--border-light)', opacity: 0.5 }}>
        <div className="px-3 py-1 text-xs border-b" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-muted)', borderColor: 'var(--border-light)', opacity: 0.5 }}>
          Markdown
        </div>
        <div className={`p-4 ${proseClass} prose-sm max-w-none`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{source}</ReactMarkdown>
        </div>
      </div>
    )
  }

  if (cell.cell_type === 'code') {
    const execLabel = cell.execution_count != null ? `[${cell.execution_count}]` : `[${index}]`
    const hasOutput = cell.outputs && cell.outputs.length > 0

    return (
      <div className="border overflow-hidden" style={{ borderColor: 'var(--border-light)', opacity: 0.5 }}>
        <div className="px-3 py-1 text-xs border-b" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)', borderColor: 'var(--border-light)' }}>
          In {execLabel}
        </div>
        <pre className="p-4 overflow-x-auto" style={{ backgroundColor: 'var(--code-bg)' }}>
          <code ref={codeRef} className="language-python text-sm leading-5">
            {source}
          </code>
        </pre>
        {hasOutput && (
          <>
            <div className="px-3 py-1 text-xs border-t border-b" style={{ backgroundColor: 'var(--success-subtle)', color: 'var(--success)', borderColor: 'var(--border-light)' }}>
              Out {execLabel}
            </div>
            {cell.outputs!.map((output, oIdx) => (
              <NotebookOutputView key={oIdx} output={output as NotebookOutput} />
            ))}
          </>
        )}
      </div>
    )
  }

  return null
}


const VIRTUALIZE_THRESHOLD = 2000
const LINE_HEIGHT = 20 // matches leading-5
const VIRTUAL_BUFFER = 50

export function CodeViewer({ paperId, filePath, scrollToLine, navigationId }: CodeViewerProps) {
  const [content, setContent] = useState<string | null>(null)
  const [apiCells, setApiCells] = useState<NotebookCellData[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const codeRef = useRef<HTMLElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const lineBoxRef = useRef<HTMLDivElement>(null)
  const [highlightLine, setHighlightLine] = useState<number | null>(null)
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const [scrollTop, setScrollTop] = useState(0)
  const [containerHeight, setContainerHeight] = useState(0)
  const [copied, setCopied] = useState(false)
  const copyTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const mode = useThemeMode()
  const proseClass = mode === 'dark' ? 'prose prose-invert' : 'prose'

  // 复制当前文件内容到剪贴板（纯前端，不触后端）
  const handleCopy = useCallback(() => {
    if (content == null) return
    const write = navigator.clipboard?.writeText?.(content)
    Promise.resolve(write).then(() => {
      setCopied(true)
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current)
      copyTimerRef.current = setTimeout(() => setCopied(false), 1800)
    }).catch(() => { /* 剪贴板不可用时静默 */ })
  }, [content])

  useEffect(() => () => { if (copyTimerRef.current) clearTimeout(copyTimerRef.current) }, [])

  // Hooks must be called before any early returns
  const notebookCells = useMemo(
    () => apiCells ?? ((filePath && content && isNotebook(filePath)) ? parseNotebook(content) : null),
    [apiCells, content, filePath]
  )

  useEffect(() => {
    setHighlightLine(null)
    if (!filePath) {
      setContent(null)
      return
    }

    const controller = new AbortController()
    setLoading(true)
    setError(null)
    setApiCells(null)
    getCodeFile(paperId, filePath, controller.signal)
      .then(res => {
        if (!controller.signal.aborted) {
          setContent(res.content)
          setApiCells(res.cells ?? null)
        }
      })
      .catch(err => { if (err.name !== 'AbortError') setError('文件加载失败') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })

    return () => controller.abort()
  }, [paperId, filePath])

  useEffect(() => {
    if (codeRef.current && content && filePath && !isMarkdown(filePath) && !isNotebook(filePath)) {
      const lang = getLanguage(filePath)
      ensureLanguage(lang).then(() => {
        if (!codeRef.current) return
        codeRef.current.removeAttribute('data-highlighted')
        try {
          hljs.highlightElement(codeRef.current)
        } catch {
          // ignore
        }
      })
    }
  }, [content, filePath])

  useEffect(() => {
    if (scrollToLine == null || loading || content == null || !containerRef.current || error) return
    const container = containerRef.current
    const line = Math.min(content.split('\n').length, Math.max(1, scrollToLine))
    const frame = requestAnimationFrame(() => {
      const top = Math.max(0, (lineBoxRef.current?.offsetTop || 36) + (line - 1) * LINE_HEIGHT - container.clientHeight / 2)
      container.scrollTo({ top, behavior: 'auto' })
      setScrollTop(top)
      setHighlightLine(line)
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current)
      highlightTimerRef.current = setTimeout(() => setHighlightLine(null), 3000)
    })
    return () => {
      cancelAnimationFrame(frame)
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current)
    }
  }, [scrollToLine, navigationId, paperId, filePath, loading, content, error])

  // Hooks must be called before any early returns
  const lines = (content || '').split('\n')
  const language = filePath ? getLanguage(filePath) : 'plaintext'
  const shouldVirtualize = lines.length > VIRTUALIZE_THRESHOLD

  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    if (!shouldVirtualize) return
    const el = e.currentTarget
    setScrollTop(el.scrollTop)
    if (containerHeight !== el.clientHeight) {
      setContainerHeight(el.clientHeight)
    }
  }, [shouldVirtualize, containerHeight])

  // Virtualized line range
  const startLine = shouldVirtualize
    ? Math.max(0, Math.floor(scrollTop / LINE_HEIGHT) - VIRTUAL_BUFFER)
    : 0
  const endLine = shouldVirtualize
    ? Math.min(lines.length, startLine + Math.ceil((containerHeight || 600) / LINE_HEIGHT) + VIRTUAL_BUFFER * 2)
    : lines.length

  const lineNumbers = useMemo(() => {
    const nums: React.ReactNode[] = []
    for (let i = startLine; i < endLine; i++) {
      nums.push(
        <div
          key={i}
          id={`line-${i + 1}`}
          className="text-xs leading-5 px-2"
          style={highlightLine === i + 1
            ? { color: 'var(--accent)', backgroundColor: 'var(--accent-subtle)' }
            : { color: 'var(--text-muted)' }
          }
        >
          {i + 1}
        </div>
      )
    }
    return nums
  }, [startLine, endLine, highlightLine])

  if (!filePath) {
    return (
      <div className="flex items-center justify-center h-full text-sm" style={{ color: 'var(--text-muted)' }}>
        选择文件查看代码
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner size="md" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red-400 text-sm">
        {error}
      </div>
    )
  }

  return (
    <div ref={containerRef} className="h-full overflow-auto" style={{ backgroundColor: 'var(--code-bg)' }} onScroll={handleScroll}>
      {/* 文件路径 */}
      <div className="sticky top-0 z-10 px-4 py-2 border-b backdrop-blur-sm flex items-center justify-between gap-3" style={{ backgroundColor: 'var(--bg-surface)', opacity: 0.95, borderColor: 'var(--border)' }}>
        <span className="text-xs font-mono truncate" style={{ color: 'var(--text-secondary)' }} title={filePath}>{filePath}</span>
        {content != null && (
          <button
            type="button"
            onClick={handleCopy}
            className="focus-glow shrink-0 inline-flex items-center gap-1 px-2 py-0.5 text-[11px] transition-colors duration-200"
            style={{ color: copied ? 'var(--success)' : 'var(--text-muted)', backgroundColor: copied ? 'var(--success-subtle)' : 'transparent' }}
            onMouseEnter={e => { if (!copied) e.currentTarget.style.color = 'var(--accent)' }}
            onMouseLeave={e => { if (!copied) e.currentTarget.style.color = 'var(--text-muted)' }}
            aria-label="复制文件内容"
            title="复制文件内容"
          >
            {copied ? (
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
            ) : (
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
            )}
            {copied ? '已复制' : '复制'}
          </button>
        )}
      </div>

      {/* Jupyter Notebook 渲染 */}
      {notebookCells ? (
        <NotebookRenderer cells={notebookCells} />
      ) : isMarkdown(filePath) ? (
        /* Markdown 渲染 */
        <div className={`p-6 ${proseClass} prose-sm max-w-none`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || ''}</ReactMarkdown>
        </div>
      ) : (
        /* 普通代码渲染 */
        <div className="flex min-w-0">
          {/* 行号 */}
          <div ref={lineBoxRef} className="shrink-0 select-none text-right pr-4 pt-4 pb-4 border-r" style={{ borderColor: 'var(--border)' }}>
            {shouldVirtualize && startLine > 0 && (
              <div style={{ height: startLine * LINE_HEIGHT }} />
            )}
            {lineNumbers}
            {shouldVirtualize && endLine < lines.length && (
              <div style={{ height: (lines.length - endLine) * LINE_HEIGHT }} />
            )}
          </div>

          {/* 代码 */}
          <pre className="flex-1 min-w-0 p-4 overflow-x-auto">
            <code
              ref={codeRef}
              className={`language-${language} text-sm leading-5`}
            >
              {content}
            </code>
          </pre>
        </div>
      )}
    </div>
  )
}
