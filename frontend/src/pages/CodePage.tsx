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
  const [analysisStage, setAnalysisStage] = useState<string | null>(null)
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
      setAnalysisStage('正在启动代码分析…')

      /* 后端一路发着「正在分析文件结构 / AI 正在映射论文与代码」这类阶段消息，
         此前前端把 onmessage 置空、也没监听 status，几分钟的分析里只有一个转圈。 */
      eventSource.addEventListener('status', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data)
          if (data.message) setAnalysisStage(data.message)
        } catch {
          // 单条事件解析失败不影响整体进度
        }
      })

      eventSource.addEventListener('done', () => {
        eventSource.close()
        setIsAnalyzing(false)
        setAnalysisStage(null)
        getCodeAnalysis(paperId).then(setAnalysis).catch(() => { toast({ type: 'error', message: '加载代码分析失败' }) })
      })

      /* 同名的 'error' 有两种来源：后端主动发的错误事件（带 data），
         以及 EventSource 自己的连接错误（无 data）。两者的说法不该一样。 */
      eventSource.addEventListener('error', (e: MessageEvent) => {
        eventSource.close()
        setIsAnalyzing(false)
        setAnalysisStage(null)
        let message = '代码分析连接中断，请重试'
        try {
          const data = JSON.parse(e.data)
          if (data.message) message = data.message
        } catch {
          // 无 data 即连接错误，沿用默认文案
        }
        setError(message)
        toast({ type: 'error', message })
      })
    } catch (e) {
      setIsAnalyzing(false)
      setCloneStatus(null)
      setAnalysisStage(null)
      setError(e instanceof Error && e.message ? e.message : '克隆失败，请检查仓库地址是否正确')
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
      <div className="er-page er-page--code er-code-state" data-state="loading" role="status" aria-label="正在加载代码与分析">
        <span className="er-code-state__register" aria-hidden="true">05 / CODE—TRACE / REGISTERING</span>
        <LoadingSpinner size="lg" />
        <div>
          <strong>正在登记代码工作面</strong>
          <p>正在加载代码与分析…</p>
        </div>
      </div>
    )
  }

  if (error || !paper) {
    return (
      <div className="er-page er-page--code er-code-state" data-state="error" role="alert">
        <span className="er-code-state__register" aria-hidden="true">05 / CODE—TRACE / INTERRUPTED</span>
        <span className="er-code-state__signal" aria-hidden="true">!</span>
        <div>
          <strong>代码工作面未建立</strong>
          <p>{error || '论文不存在'}</p>
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
              role="status"
              aria-live="polite"
              aria-label="代码分析进度"
              title="AI 正在分析代码结构与论文映射，请稍候…">
              <LoadingSpinner size="xs" />
              {analysisStage ?? '正在分析…'}
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
