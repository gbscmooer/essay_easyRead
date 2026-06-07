import { useState, useEffect, useRef, useMemo } from 'react'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import cpp from 'highlight.js/lib/languages/cpp'
import java from 'highlight.js/lib/languages/java'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import markdownLang from 'highlight.js/lib/languages/markdown'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { getCodeFile, type NotebookCellData, type NotebookOutput } from '@/api/code'
import { useThemeMode } from '@/theme/useTheme'

// 注册常用语言
hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('java', java)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('markdown', markdownLang)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('css', css)


interface CodeViewerProps {
  paperId: number
  filePath: string | null
  scrollToLine?: number | null
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
      <pre className="p-4 overflow-x-auto bg-gray-900/50 text-sm text-gray-300 whitespace-pre-wrap">
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
        <div className="p-4 bg-gray-900/50">
          <img src={src} alt="notebook output" className="max-w-full mx-auto" />
        </div>
      )
    }
    if (data['text/plain']) {
      return (
        <pre className="p-4 overflow-x-auto bg-gray-900/50 text-sm text-gray-300 whitespace-pre-wrap">
          {data['text/plain']}
        </pre>
      )
    }
    if (output.text) {
      return (
        <pre className="p-4 overflow-x-auto bg-gray-900/50 text-sm text-gray-300 whitespace-pre-wrap">
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
      codeRef.current.removeAttribute('data-highlighted')
      try {
        hljs.highlightElement(codeRef.current)
      } catch {
        // ignore
      }
    }
  }, [cell.cell_type, source])

  if (cell.cell_type === 'markdown') {
    return (
      <div className="border border-gray-700/50 rounded-lg overflow-hidden">
        <div className="px-3 py-1 bg-gray-800/50 text-xs text-gray-500 border-b border-gray-700/50">
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
      <div className="border border-gray-700/50 rounded-lg overflow-hidden">
        <div className="px-3 py-1 bg-blue-900/20 text-xs text-blue-400/70 border-b border-gray-700/50">
          In {execLabel}
        </div>
        <pre className="p-4 overflow-x-auto" style={{ backgroundColor: 'var(--code-bg)' }}>
          <code ref={codeRef} className="language-python text-sm leading-5">
            {source}
          </code>
        </pre>
        {hasOutput && (
          <>
            <div className="px-3 py-1 bg-green-900/20 text-xs text-green-400/70 border-t border-b border-gray-700/50">
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


export function CodeViewer({ paperId, filePath, scrollToLine }: CodeViewerProps) {
  const [content, setContent] = useState<string | null>(null)
  const [apiCells, setApiCells] = useState<NotebookCellData[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const codeRef = useRef<HTMLElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [highlightLine, setHighlightLine] = useState<number | null>(null)
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const mode = useThemeMode()
  const proseClass = mode === 'dark' ? 'prose prose-invert' : 'prose'

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
      codeRef.current.removeAttribute('data-highlighted')
      try {
        hljs.highlightElement(codeRef.current)
      } catch {
        // ignore
      }
    }
  }, [content, filePath])

  useEffect(() => {
    if (scrollToLine == null || !containerRef.current) return

    if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current)

    const lineEl = containerRef.current.querySelector(`#line-${scrollToLine}`)
    if (lineEl) {
      lineEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setHighlightLine(scrollToLine)
      highlightTimerRef.current = setTimeout(() => setHighlightLine(null), 3000)
    }

    return () => {
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current)
    }
  }, [scrollToLine])

  if (!filePath) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm">
        选择文件查看代码
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="w-6 h-6 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
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

  const lines = (content || '').split('\n')
  const language = getLanguage(filePath)

  return (
    <div ref={containerRef} className="h-full overflow-auto" style={{ backgroundColor: 'var(--code-bg)' }}>
      {/* 文件路径 */}
      <div className="sticky top-0 z-10 px-4 py-2 bg-gray-900/90 border-b border-gray-800 backdrop-blur-sm">
        <span className="text-xs text-gray-400 font-mono">{filePath}</span>
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
        <div className="flex">
          {/* 行号 */}
          <div className="shrink-0 select-none text-right pr-4 pt-4 pb-4 border-r border-gray-800/50">
            {lines.map((_, i) => (
              <div
                key={i}
                id={`line-${i + 1}`}
                className={`text-xs leading-5 px-2 ${
                  highlightLine === i + 1 ? 'text-blue-400 bg-blue-500/15' : 'text-gray-600'
                }`}
              >
                {i + 1}
              </div>
            ))}
          </div>

          {/* 代码 */}
          <pre className="flex-1 p-4 overflow-x-auto">
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
