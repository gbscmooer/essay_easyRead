import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams } from 'react-router-dom'
import type { FileNode, CodeAnalysisResult } from '@/types'
import { getCodeTree, getCodeAnalysis, getCodeAnalysisSSEUrl, cloneRepository, openInVSCode } from '@/api/code'
import { getPaper } from '@/api/papers'
import type { Paper } from '@/types'
import { FileBrowser } from '@/features/code/FileBrowser'
import { CodeViewer } from '@/features/code/CodeViewer'
import { CodeAnalysisPanel } from '@/features/code/CodeAnalysisPanel'
import { toast } from '@/hooks/useToast'
import { LoadingSpinner } from '@/ds-entry'


export default function CodePage() {
  const { id } = useParams<{ id: string }>()
  const paperId = Number(id)

  const [paper, setPaper] = useState<Paper | null>(null)
  const [fileTree, setFileTree] = useState<FileNode | null>(null)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [analysis, setAnalysis] = useState<CodeAnalysisResult | null>(null)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [cloneStatus, setCloneStatus] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [scrollToLine, setScrollToLine] = useState<number | null>(null)

  const [leftWidth, setLeftWidth] = useState(240)
  const [rightWidth, setRightWidth] = useState(320)
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const dragType = useRef<'left' | 'right' | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    if (!paperId) return
    setLoading(true)
    Promise.all([
      getPaper(paperId).then(setPaper),
      getCodeTree(paperId).then(setFileTree).catch(() => { toast({ type: 'error', message: '加载文件树失败' }) }),
      getCodeAnalysis(paperId).then(setAnalysis).catch(() => { toast({ type: 'error', message: '加载代码分析失败' }) }),
    ])
      .catch(() => setError('加载失败'))
      .finally(() => setLoading(false))
  }, [paperId])

  const handleAnalyze = useCallback(async () => {
    if (!paperId) return
    setIsAnalyzing(true)
    setError(null)

    try {
      if (!paper || paper.code_status !== 'ready') {
        setCloneStatus('正在克隆仓库...')
        await cloneRepository(paperId)
        const [updatedPaper, tree] = await Promise.all([
          getPaper(paperId),
          getCodeTree(paperId),
        ])
        setPaper(updatedPaper)
        setFileTree(tree)
        setCloneStatus(null)
      }

      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }

      const eventSource = new EventSource(getCodeAnalysisSSEUrl(paperId))
      eventSourceRef.current = eventSource
      eventSource.onmessage = () => {}
      eventSource.addEventListener('done', () => {
        eventSource.close()
        setIsAnalyzing(false)
        getCodeAnalysis(paperId).then(setAnalysis).catch(() => { toast({ type: 'error', message: '加载代码分析失败' }) })
      })
      eventSource.addEventListener('error', () => { eventSource.close(); setIsAnalyzing(false) })
      eventSource.onerror = () => { eventSource.close(); setIsAnalyzing(false) }
    } catch (e) {
      setIsAnalyzing(false)
      setCloneStatus(null)
      setError('克隆失败，请检查仓库地址是否正确')
    }
  }, [paperId, paper])

  const handleFileClick = useCallback((path: string, line?: number) => {
    setSelectedFile(path)
    setScrollToLine(line ?? null)
  }, [])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!dragType.current || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      if (dragType.current === 'left') {
        setLeftWidth(Math.max(150, Math.min(400, e.clientX - rect.left)))
      } else {
        setRightWidth(Math.max(200, Math.min(500, rect.right - e.clientX)))
      }
    }
    const handleMouseUp = () => {
      dragType.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  if (loading) {
    return (
      <div className="er-page er-page--code er-code-state flex flex-col items-center justify-center h-full gap-4" data-state="loading" role="status" aria-label="正在加载代码与分析">
        <LoadingSpinner size="lg" />
        <div className="text-center">
          <strong className="text-[15px] text-[var(--text-primary)] block mb-1 font-medium">正在加载代码</strong>
          <p className="text-[13px] text-[var(--text-muted)]">正在读取代码仓库与依赖分析…</p>
        </div>
      </div>
    )
  }

  if (error || !paper) {
    return (
      <div className="er-page er-page--code er-code-state flex flex-col items-center justify-center h-full gap-4" data-state="error" role="alert">
        <div className="w-12 h-12 rounded-full bg-red-500/10 text-red-500 flex items-center justify-center text-xl font-bold">!</div>
        <div className="text-center">
          <strong className="text-[15px] text-[var(--text-primary)] block mb-1 font-medium">代码暂不可用</strong>
          <p className="text-[13px] text-[var(--text-secondary)]">{error || '论文不存在'}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="er-page er-page--code flex flex-col h-full">
      {/* 顶部工具栏 — 紧凑 ghost 风格 */}
      <div className="cd-toolbar">
        <div className="flex items-center gap-1.5">
          {paper.code_url && (
            <a
              href={paper.code_url.startsWith('http') ? paper.code_url : `https://${paper.code_url}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[12px] px-2.5 py-1 transition-all duration-200 press focus-glow"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.backgroundColor = '' }}
            >
              原始仓库 ↗
            </a>
          )}
          {paper.code_status === 'ready' && (
            <button
              onClick={() => { if (paper?.code_path) openInVSCode(paper.code_path) }}
              className="text-[12px] px-2.5 py-1 transition-all duration-200 press focus-glow"
              style={{ color: 'var(--text-muted)' }}
              title="在 VSCode 中打开"
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.backgroundColor = '' }}
            >
              VSCode ↗
            </button>
          )}
          {cloneStatus && (
            <span className="flex items-center gap-1.5 px-3 py-1 text-[12px] animate-reveal-fade"
              style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
              title="正在从远程仓库拉取代码，请稍候…">
              <LoadingSpinner size="xs" />
              {cloneStatus}
            </span>
          )}
          {!analysis && !isAnalyzing && !cloneStatus && (
            <button
              onClick={handleAnalyze}
              className="px-4 py-1.5 text-[12px] font-medium transition-all duration-200 press focus-glow hover-lift"
              style={{ backgroundColor: 'var(--accent)', color: '#fff' }}
              title="克隆代码仓库并运行 AI 代码分析"
            >
              开始分析
            </button>
          )}
          {isAnalyzing && !cloneStatus && (
            <span className="flex items-center gap-1.5 px-3 py-1 text-[12px] animate-reveal-fade"
              style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
              title="AI 正在分析代码结构与论文映射，请稍候…">
              <LoadingSpinner size="xs" />
              正在分析...
            </span>
          )}
        </div>
      </div>

      {/* 三栏布局 */}
      <div ref={containerRef} className="cd-planes flex-1 flex overflow-hidden relative">
        {/* 左侧：文件浏览器 */}
        {!leftCollapsed && (
          <div className="cd-tree shrink-0 overflow-auto" style={{ width: leftWidth }}>
            <div className="px-3 py-2 border-b flex items-center justify-between" style={{ borderColor: 'var(--border)' }}>
              <h3 className="text-label" style={{ color: 'var(--text-muted)' }}>文件</h3>
              <button
                onClick={() => setLeftCollapsed(true)}
                className="p-1 transition-all duration-200 press focus-glow"
                style={{ color: 'var(--text-muted)' }}
                title="收起文件栏"
                aria-label="收起文件栏"
                onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }}
                onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.backgroundColor = '' }}
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </button>
            </div>
            <FileBrowser tree={fileTree} selected={selectedFile} onSelect={setSelectedFile} />
          </div>
        )}

        {/* 左侧展开 */}
        {leftCollapsed && (
          <button
            onClick={() => setLeftCollapsed(false)}
            className="absolute left-0 top-0 bottom-0 z-10 w-6 flex items-center justify-center transition-all duration-200 focus-glow group"
            style={{ backgroundColor: 'var(--bg-surface)', borderRight: '1px solid var(--border)' }}
            title="展开文件栏"
            aria-label="展开文件栏"
            onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'var(--bg-surface)' }}
          >
            <svg className="w-3.5 h-3.5 transition-transform duration-200 group-hover:translate-x-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--text-muted)' }}>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
        )}

        {/* 左侧拖拽条 */}
        {!leftCollapsed && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="调整文件栏宽度"
            title="拖拽调整文件栏宽度"
            className="shrink-0 cursor-col-resize transition-all duration-200 group flex items-center justify-center relative"
            style={{ width: 4, backgroundColor: 'var(--border)' }}
            onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.boxShadow = '0 0 0 1px var(--accent-subtle)' }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'var(--border)'; e.currentTarget.style.boxShadow = '' }}
            onMouseDown={(e) => {
              e.preventDefault()
              dragType.current = 'left'
              document.body.style.cursor = 'col-resize'
              document.body.style.userSelect = 'none'
            }}
          >
            <span className="absolute h-8 w-0.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200" style={{ backgroundColor: 'var(--bg-surface)' }} />
          </div>
        )}

        {/* 中间：代码查看器 */}
        <div className="flex-1 overflow-hidden">
          <CodeViewer paperId={paperId} filePath={selectedFile} scrollToLine={scrollToLine} />
        </div>

        {/* 右侧拖拽条 */}
        {!rightCollapsed && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="调整分析栏宽度"
            title="拖拽调整分析栏宽度"
            className="shrink-0 cursor-col-resize transition-all duration-200 group flex items-center justify-center relative"
            style={{ width: 4, backgroundColor: 'var(--border)' }}
            onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.boxShadow = '0 0 0 1px var(--accent-subtle)' }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'var(--border)'; e.currentTarget.style.boxShadow = '' }}
            onMouseDown={(e) => {
              e.preventDefault()
              dragType.current = 'right'
              document.body.style.cursor = 'col-resize'
              document.body.style.userSelect = 'none'
            }}
          >
            <span className="absolute h-8 w-0.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200" style={{ backgroundColor: 'var(--bg-surface)' }} />
          </div>
        )}

        {/* 右侧展开 */}
        {rightCollapsed && (
          <button
            onClick={() => setRightCollapsed(false)}
            className="absolute right-0 top-0 bottom-0 z-10 w-6 flex items-center justify-center transition-all duration-200 focus-glow group"
            style={{ backgroundColor: 'var(--bg-surface)', borderLeft: '1px solid var(--border)' }}
            title="展开分析栏"
            aria-label="展开分析栏"
            onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'var(--bg-surface)' }}
          >
            <svg className="w-3.5 h-3.5 transition-transform duration-200 group-hover:-translate-x-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--text-muted)' }}>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
        )}

        {/* 右侧：分析面板 */}
        {!rightCollapsed && (
          <div className="cd-analysis shrink-0 overflow-hidden" style={{ width: rightWidth }}>
            <CodeAnalysisPanel
              analysis={analysis}
              isAnalyzing={isAnalyzing}
              paperId={paperId}
              onFileClick={handleFileClick}
              onCollapse={() => setRightCollapsed(true)}
            />
          </div>
        )}
      </div>
    </div>
  )
}
