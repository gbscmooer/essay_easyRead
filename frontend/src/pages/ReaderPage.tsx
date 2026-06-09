import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import type { Paper } from '@/types'
import { listPapers, deletePaper } from '@/api/papers'
import { useAnalysis } from '@/hooks/useAnalysis'
import { toast } from '@/hooks/useToast'
import { useUIConfig } from '@/hooks/useConfig'
import { Sidebar } from '@/components/Sidebar'
import { PdfViewer } from '@/features/viewer/PdfViewer'
import type { PdfViewerHandle } from '@/features/viewer/PdfViewer'
import { AnalysisPanel } from '@/features/analysis/AnalysisPanel'
import { toast } from '@/hooks/useToast'


const MIN_PANEL_PCT = 20
const MAX_PANEL_PCT = 60
const DEFAULT_PANEL_PCT = 35


export default function ReaderPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const uiConfig = useUIConfig()
  const paperId = id ? Number(id) : undefined
  const autoAnalyzeRef = useRef((location.state as { autoAnalyze?: boolean })?.autoAnalyze ?? false)

  const [papers, setPapers] = useState<Paper[]>([])
  const [papersLoading, setPapersLoading] = useState(true)
  const [activePage, setActivePage] = useState<number | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [panelPct, setPanelPct] = useState(DEFAULT_PANEL_PCT)
  const [focusMode, setFocusMode] = useState(false)
  const draggingRef = useRef(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const pdfViewerRef = useRef<PdfViewerHandle>(null)
  const onPageChangeNoop = useCallback(() => {}, [])
  const activeTimerRef = useRef<ReturnType<typeof setTimeout>>()

  const {
    analysis,
    status,
    progressMessage,
    progress,
    relatedStatus,
    partialResult,
    error,
    reparse,
    startAnalysis,
  } = useAnalysis(paperId)

  useEffect(() => {
    setPapersLoading(true)
    listPapers({ per_page: 100 })
      .then(res => setPapers(res.papers))
      .catch(() => { toast({ type: 'error', message: '加载论文列表失败' }) })
      .finally(() => setPapersLoading(false))
  }, [])

  // Auto-start analysis for freshly uploaded papers
  useEffect(() => {
    if (autoAnalyzeRef.current && status === 'idle' && paperId) {
      autoAnalyzeRef.current = false
      startAnalysis()
    }
  }, [status, paperId, startAnalysis])

  // Focus mode: toggle data attribute + browser fullscreen
  useEffect(() => {
    document.documentElement.dataset.focusMode = focusMode ? 'true' : 'false'

    if (focusMode) {
      document.documentElement.requestFullscreen?.().catch(() => {})
    } else if (document.fullscreenElement) {
      document.exitFullscreen?.().catch(() => {})
    }

    return () => {
      document.documentElement.dataset.focusMode = 'false'
    }
  }, [focusMode])

  // Listen for fullscreen exit (e.g. user presses Esc natively)
  useEffect(() => {
    const handleFullscreenChange = () => {
      if (!document.fullscreenElement && focusMode) {
        setFocusMode(false)
      }
    }
    document.addEventListener('fullscreenchange', handleFullscreenChange)
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange)
  }, [focusMode])

  // Escape 退出专注模式
  useEffect(() => {
    if (!focusMode) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFocusMode(false)
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [focusMode])

  // 拖拽逻辑
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    draggingRef.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    e.preventDefault()
    draggingRef.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  useEffect(() => {
    const calcPct = (clientX: number) => {
      if (!containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const x = clientX - rect.left
      const pct = ((rect.width - x) / rect.width) * 100
      setPanelPct(Math.min(MAX_PANEL_PCT, Math.max(MIN_PANEL_PCT, pct)))
    }

    const handleMouseMove = (e: MouseEvent) => {
      if (!draggingRef.current) return
      calcPct(e.clientX)
    }

    const handleTouchMove = (e: TouchEvent) => {
      if (!draggingRef.current) return
      calcPct(e.touches[0].clientX)
    }

    const handleUp = () => {
      if (draggingRef.current) {
        draggingRef.current = false
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleUp)
    document.addEventListener('touchmove', handleTouchMove, { passive: false })
    document.addEventListener('touchend', handleUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleUp)
      document.removeEventListener('touchmove', handleTouchMove)
      document.removeEventListener('touchend', handleUp)
    }
  }, [])

  useEffect(() => {
    return () => {
      if (activeTimerRef.current) clearTimeout(activeTimerRef.current)
    }
  }, [])

  const handleOutlineJump = useCallback((page: number) => {
    pdfViewerRef.current?.jumpToPage(page)
    setActivePage(page)
    if (activeTimerRef.current) clearTimeout(activeTimerRef.current)
    activeTimerRef.current = setTimeout(() => setActivePage(null), uiConfig.highlight_duration)
  }, [uiConfig.highlight_duration])

  const handleSelect = useCallback((id: number) => {
    navigate(`/reader/${id}`)
    setActivePage(null)
    setSidebarOpen(false)
  }, [navigate])

  const handleDelete = useCallback(async (id: number) => {
    try {
      await deletePaper(id)
      setPapers(prev => {
        const remaining = prev.filter(p => p.id !== id)
        if (id === paperId) {
          if (remaining.length > 0) {
            navigate(`/reader/${remaining[0].id}`)
          } else {
            navigate('/')
          }
        }
        return remaining
      })
    } catch {
      toast({ type: 'error', message: '删除失败，请重试' })
    }
  }, [paperId, navigate])

  const pdfUrl = paperId ? `/api/papers/${paperId}/pdf` : ''

  return (
    <div ref={containerRef} className="flex h-full relative">
      {/* Sidebar overlay */}
      {sidebarOpen && !focusMode && (
        <div
          className="fixed inset-0 bg-black/40 z-30 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* 左侧 Sidebar */}
      {!focusMode && (
        <div className={`
          fixed lg:static inset-y-0 left-0 z-40 overflow-hidden
          transform transition-all duration-200 ease-in-out
          ${sidebarOpen ? 'translate-x-0 w-64' : '-translate-x-full lg:translate-x-0 w-0 lg:w-0'}
        `}>
          {papersLoading && papers.length === 0 ? (
            <div className="flex flex-col w-64 h-full items-center justify-center" style={{
              backgroundColor: 'var(--bg-surface)',
              borderRight: '1px solid var(--border)',
            }}>
              <div className="w-5 h-5 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }} role="status">
                <span className="sr-only">加载中</span>
              </div>
            </div>
          ) : (
          <Sidebar
            papers={papers}
            activeId={paperId ?? null}
            onSelect={handleSelect}
            onDelete={handleDelete}
          />
          )}
        </div>
      )}

      {/* 中间 PDF 阅读区 */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {/* 顶部工具栏 */}
        {!focusMode && (
          <div className="flex items-center justify-between px-3 py-1.5 shrink-0" style={{
            backgroundColor: 'var(--bg-surface)',
            borderBottom: '1px solid var(--border)',
          }}>
            <button
              onClick={() => setSidebarOpen(v => !v)}
              className="p-1.5 rounded-md transition-colors"
              style={{ color: 'var(--text-muted)' }}
              title={sidebarOpen ? '收起论文列表' : '展开论文列表'}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d={sidebarOpen
                    ? "M11 19l-7-7 7-7m8 14l-7-7 7-7"
                    : "M13 5l7 7-7 7M5 5l7 7-7 7"}
                />
              </svg>
            </button>

            <div />

            <div className="flex items-center gap-1">
              {/* 专注模式按钮 — 放大镜图标 */}
              <button
                onClick={() => setFocusMode(true)}
                className="p-1.5 rounded-md transition-colors"
                style={{ color: 'var(--text-muted)' }}
                title="专注模式"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607zM10.5 7.5v6m3-3h-6"
                  />
                </svg>
              </button>

              <button
                onClick={() => setPanelOpen(v => !v)}
                className="p-1.5 rounded-md transition-colors"
                style={{ color: 'var(--text-muted)' }}
                title={panelOpen ? '收起分析面板' : '展开分析面板'}
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d={panelOpen
                      ? "M13 5l7 7-7 7M5 5l7 7-7 7"
                      : "M11 19l-7-7 7-7m8 14l-7-7 7-7"}
                  />
                </svg>
              </button>
            </div>
          </div>
        )}

        {/* PDF 渲染区 */}
        <div className={`flex-1 overflow-hidden ${focusMode ? '' : 'p-2'}`}>
          {paperId && (
            <PdfViewer
              ref={pdfViewerRef}
              pdfUrl={pdfUrl}
              paperId={paperId}
              activePage={activePage}
              onPageChange={onPageChangeNoop}
            />
          )}
        </div>
      </div>

      {/* 可拖拽分割条 */}
      {panelOpen && !focusMode && (
        <div
          onMouseDown={handleMouseDown}
          onTouchStart={handleTouchStart}
          className="w-1 shrink-0 cursor-col-resize transition-colors relative group z-10"
          style={{ backgroundColor: 'var(--border)' }}
        >
          <div className="absolute inset-y-0 -left-1 -right-1" />
        </div>
      )}

      {/* 右侧 AI 分析面板 */}
      {!focusMode && (
        <div
          className="overflow-auto transition-[width] duration-100 ease-out shrink-0"
          style={{ width: panelOpen ? `${panelPct}%` : 0, backgroundColor: 'var(--bg-surface)' }}
        >
          {panelOpen && (
            <div className="h-full overflow-auto">
              {error && status === 'failed' ? (
                <div className="flex flex-col items-center justify-center h-full gap-4 p-6">
                  <p className="text-sm text-center" style={{ color: 'var(--error)' }}>{error}</p>
                  <button
                    onClick={reparse}
                    className="px-4 py-2 text-sm rounded-lg transition-colors"
                    style={{ backgroundColor: 'var(--bg-surface)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
                  >
                    重试
                  </button>
                </div>
              ) : (
                <AnalysisPanel
                  result={analysis}
                  partialResult={partialResult}
                  status={status}
                  progressMessage={progressMessage}
                  progress={progress}
                  relatedStatus={relatedStatus}
                  onJumpToPage={handleOutlineJump}
                  paperId={paperId}
                  onStartAnalysis={startAnalysis}
                  onReanalyze={reparse}
                />
              )}
            </div>
          )}
        </div>
      )}

      {/* 专注模式退出按钮 */}
      {focusMode && (
        <button
          onClick={() => setFocusMode(false)}
          className="fixed top-4 right-4 z-[9999] p-2 rounded-full shadow-lg transition-all opacity-60 hover:opacity-100"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
          title="退出专注模式 (Esc)"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25"
            />
          </svg>
        </button>
      )}
    </div>
  )
}
