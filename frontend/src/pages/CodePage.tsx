import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams } from 'react-router-dom'
import type { FileNode, CodeAnalysisResult } from '@/types'
import { getCodeTree, getCodeAnalysis, getCodeAnalysisSSEUrl, cloneRepository, openInVSCode } from '@/api/code'
import { getPaper } from '@/api/papers'
import type { Paper } from '@/types'
import { FileBrowser } from '@/features/code/FileBrowser'
import { CodeViewer } from '@/features/code/CodeViewer'
import { CodeAnalysisPanel } from '@/features/code/CodeAnalysisPanel'


export function CodePage() {
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

  // 可拖拽面板宽度
  const [leftWidth, setLeftWidth] = useState(240)
  const [rightWidth, setRightWidth] = useState(320)
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const dragType = useRef<'left' | 'right' | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)

  // 清理 EventSource
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }
    }
  }, [])

  // 加载数据
  useEffect(() => {
    if (!paperId) return

    setLoading(true)
    Promise.all([
      getPaper(paperId).then(setPaper),
      getCodeTree(paperId).then(setFileTree).catch(() => {}),
      getCodeAnalysis(paperId).then(setAnalysis).catch(() => {}),
    ])
      .catch(() => setError('加载失败'))
      .finally(() => setLoading(false))
  }, [paperId])

  // 触发代码分析（自动先 clone）
  const handleAnalyze = useCallback(async () => {
    if (!paperId) return
    setIsAnalyzing(true)
    setError(null)

    try {
      // 如果仓库还没 clone，先 clone
      if (!paper || paper.code_status !== 'ready') {
        setCloneStatus('正在克隆仓库...')
        await cloneRepository(paperId)
        // 刷新 paper 信息和文件树
        const [updatedPaper, tree] = await Promise.all([
          getPaper(paperId),
          getCodeTree(paperId),
        ])
        setPaper(updatedPaper)
        setFileTree(tree)
        setCloneStatus(null)
      }

      // 开始分析
      const eventSource = new EventSource(getCodeAnalysisSSEUrl(paperId))
      eventSourceRef.current = eventSource

      eventSource.onmessage = () => {}

      eventSource.addEventListener('done', () => {
        eventSource.close()
        setIsAnalyzing(false)
        getCodeAnalysis(paperId).then(setAnalysis).catch(() => {})
      })

      eventSource.addEventListener('error', () => {
        eventSource.close()
        setIsAnalyzing(false)
      })

      eventSource.onerror = () => {
        eventSource.close()
        setIsAnalyzing(false)
      }
    } catch (e) {
      setIsAnalyzing(false)
      setCloneStatus(null)
      setError('克隆失败，请检查仓库地址是否正确')
    }
  }, [paperId, paper])

  // 点击依赖图/映射中的文件
  const handleFileClick = useCallback((path: string, line?: number) => {
    setSelectedFile(path)
    setScrollToLine(line ?? null)
  }, [])

  // 拖拽分割条
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!dragType.current || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      if (dragType.current === 'left') {
        const w = Math.max(150, Math.min(400, e.clientX - rect.left))
        setLeftWidth(w)
      } else {
        const w = Math.max(200, Math.min(500, rect.right - e.clientX))
        setRightWidth(w)
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
      <div className="flex items-center justify-center h-full">
        <div className="w-8 h-8 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }} />
      </div>
    )
  }

  if (error || !paper) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-red-400">{error || '论文不存在'}</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-end px-4 py-1.5 border-b shrink-0"
        style={{ backgroundColor: 'var(--bg-surface)', opacity: 0.8, borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-2">
          {paper.code_url && (
            <a
              href={paper.code_url.startsWith('http') ? paper.code_url : `https://${paper.code_url}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-secondary)' }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)' }}
            >
              原始仓库 ↗
            </a>
          )}
          {paper.code_status === 'ready' && (
            <button
              onClick={() => {
                if (paper?.code_path) openInVSCode(paper.code_path)
              }}
              className="text-xs transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)' }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)' }}
              title="在 VSCode 中打开"
            >
              转到 VSCode ↗
            </button>
          )}
          {cloneStatus && (
            <span className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded"
              style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
            >
              <div className="w-3 h-3 border rounded-full animate-spin"
                style={{ borderColor: 'var(--text-muted)', borderTopColor: 'var(--accent)' }} />
              {cloneStatus}
            </span>
          )}
          {!analysis && !isAnalyzing && !cloneStatus && (
            <button
              onClick={handleAnalyze}
              className="px-3 py-1.5 text-xs rounded transition-colors"
              style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
              onMouseEnter={e => { e.currentTarget.style.opacity = '0.85' }}
              onMouseLeave={e => { e.currentTarget.style.opacity = '1' }}
            >
              开始分析
            </button>
          )}
          {isAnalyzing && !cloneStatus && (
            <span className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded"
              style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
            >
              <div className="w-3 h-3 border rounded-full animate-spin"
                style={{ borderColor: 'var(--text-muted)', borderTopColor: 'var(--accent)' }} />
              正在分析...
            </span>
          )}
        </div>
      </div>

      {/* 三栏布局 */}
      <div ref={containerRef} className="flex-1 flex overflow-hidden relative">
        {/* 左侧：文件浏览器 */}
        {!leftCollapsed && (
          <div className="shrink-0 overflow-auto border-r" style={{ width: leftWidth, borderColor: 'var(--border)' }}>
            <div className="px-3 py-2 border-b flex items-center justify-between" style={{ borderColor: 'var(--border)' }}>
              <h3 className="text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>文件</h3>
              <button
                onClick={() => setLeftCollapsed(true)}
                className="transition-colors"
                style={{ color: 'var(--text-muted)' }}
                onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-secondary)' }}
                onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)' }}
                title="收起文件栏"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </button>
            </div>
            <FileBrowser
              tree={fileTree}
              selected={selectedFile}
              onSelect={setSelectedFile}
            />
          </div>
        )}

        {/* 左侧展开按钮 */}
        {leftCollapsed && (
          <button
            onClick={() => setLeftCollapsed(false)}
            className="absolute left-0 top-0 bottom-0 z-10 w-6 flex items-center justify-center border-r transition-colors"
            style={{ backgroundColor: 'var(--bg-surface)', opacity: 0.8, borderColor: 'var(--border)' }}
            onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'var(--bg-surface)'; e.currentTarget.style.opacity = '0.8' }}
            title="展开文件栏"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--text-muted)' }}>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
        )}

        {/* 左侧拖拽条 */}
        {!leftCollapsed && (
          <div
            className="w-1 shrink-0 cursor-col-resize transition-colors"
            style={{ backgroundColor: 'var(--bg-elevated)' }}
            onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.opacity = '0.5' }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)'; e.currentTarget.style.opacity = '1' }}
            onMouseDown={(e) => {
              e.preventDefault()
              dragType.current = 'left'
              document.body.style.cursor = 'col-resize'
              document.body.style.userSelect = 'none'
            }}
          />
        )}

        {/* 中间：代码查看器 */}
        <div className="flex-1 overflow-hidden">
          <CodeViewer paperId={paperId} filePath={selectedFile} scrollToLine={scrollToLine} />
        </div>

        {/* 右侧拖拽条 */}
        {!rightCollapsed && (
          <div
            className="w-1 shrink-0 cursor-col-resize transition-colors"
            style={{ backgroundColor: 'var(--bg-elevated)' }}
            onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.opacity = '0.5' }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)'; e.currentTarget.style.opacity = '1' }}
            onMouseDown={(e) => {
              e.preventDefault()
              dragType.current = 'right'
              document.body.style.cursor = 'col-resize'
              document.body.style.userSelect = 'none'
            }}
          />
        )}

        {/* 右侧展开按钮 */}
        {rightCollapsed && (
          <button
            onClick={() => setRightCollapsed(false)}
            className="absolute right-0 top-0 bottom-0 z-10 w-6 flex items-center justify-center border-l transition-colors"
            style={{ backgroundColor: 'var(--bg-surface)', opacity: 0.8, borderColor: 'var(--border)' }}
            onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'var(--bg-surface)'; e.currentTarget.style.opacity = '0.8' }}
            title="展开分析栏"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--text-muted)' }}>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
        )}

        {/* 右侧：分析面板 */}
        {!rightCollapsed && (
          <div className="shrink-0 overflow-hidden" style={{ width: rightWidth }}>
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
