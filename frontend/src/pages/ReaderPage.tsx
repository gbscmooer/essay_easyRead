import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import type { Paper } from '@/types'
import { listPapers, deletePaper, getHeadings, type HeadingPosition } from '@/api/papers'
import { useAnalysis } from '@/hooks/useAnalysis'
import { toast } from '@/hooks/useToast'
import { useUIConfig } from '@/hooks/useConfig'
import { Sidebar } from '@/components/Sidebar'
import { PdfViewer } from '@/features/viewer/PdfViewer'
import type { PdfViewerHandle } from '@/features/viewer/PdfViewer'
import { AnalysisPanel } from '@/features/analysis/AnalysisPanel'
import { LoadingSpinner } from '@/ds-entry'
import { loadLayout, saveLayout, forgetReadingPage, type ReaderLayout } from '@/utils/readerPrefs'


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
  // 面板宽度与开合是用户对这台「阅读工位」的调校，不该每次进页面复位
  const [layout, setLayout] = useState<ReaderLayout>(
    () => loadLayout({ panelPct: DEFAULT_PANEL_PCT, panelOpen: true }, MIN_PANEL_PCT, MAX_PANEL_PCT),
  )
  const { panelPct, panelOpen } = layout
  const [focusMode, setFocusMode] = useState(false)
  const draggingRef = useRef(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const pdfViewerRef = useRef<PdfViewerHandle>(null)
  const onPageChangeNoop = useCallback(() => {}, [])
  const activeTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const [headings, setHeadings] = useState<HeadingPosition[]>([])

  const setPanelPct = useCallback((pct: number) => {
    setLayout(prev => ({ ...prev, panelPct: Math.min(MAX_PANEL_PCT, Math.max(MIN_PANEL_PCT, pct)) }))
  }, [])

  const togglePanel = useCallback(() => {
    setLayout(prev => ({ ...prev, panelOpen: !prev.panelOpen }))
  }, [])

  const resetPanelWidth = useCallback(() => {
    setLayout(prev => ({ ...prev, panelPct: DEFAULT_PANEL_PCT }))
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => saveLayout(layout), 300)
    return () => clearTimeout(timer)
  }, [layout])

  const {
    analysis,
    status,
    progressMessage,
    progress,
    relatedStatus,
    partialResult,
    error,
    connectionLost,
    reconnect,
    reparse,
    startAnalysis,
  } = useAnalysis(paperId)

  useEffect(() => {
    setPapersLoading(true)
    listPapers({ per_page: 30 })
      .then(res => setPapers(res.papers))
      .catch(() => { toast({ type: 'error', message: '加载论文列表失败' }) })
      .finally(() => setPapersLoading(false))
  }, [])

  useEffect(() => {
    if (autoAnalyzeRef.current && status === 'idle' && paperId) {
      autoAnalyzeRef.current = false
      startAnalysis()
    }
  }, [status, paperId, startAnalysis])

  useEffect(() => {
    if (!paperId) { setHeadings([]); return }
    getHeadings(paperId).then(setHeadings).catch(() => setHeadings([]))
  }, [paperId])

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

  useEffect(() => {
    const handleFullscreenChange = () => {
      if (!document.fullscreenElement && focusMode) {
        setFocusMode(false)
      }
    }
    document.addEventListener('fullscreenchange', handleFullscreenChange)
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange)
  }, [focusMode])

  useEffect(() => {
    if (!focusMode) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFocusMode(false)
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [focusMode])

  /** 分割条的键盘语义：← 加宽面板、→ 收窄、Home 复位。 */
  const handleResizerKeyDown = useCallback((e: React.KeyboardEvent) => {
    const STEP = 2
    if (e.key === 'ArrowLeft') setPanelPct(panelPct + STEP)
    else if (e.key === 'ArrowRight') setPanelPct(panelPct - STEP)
    else if (e.key === 'PageUp') setPanelPct(panelPct + STEP * 5)
    else if (e.key === 'PageDown') setPanelPct(panelPct - STEP * 5)
    else if (e.key === 'Home' || e.key === 'Enter') resetPanelWidth()
    else return
    e.preventDefault()
    e.stopPropagation()
  }, [panelPct, setPanelPct, resetPanelWidth])

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
      setPanelPct(((rect.width - x) / rect.width) * 100)
    }
    const handleMouseMove = (e: MouseEvent) => { if (draggingRef.current) calcPct(e.clientX) }
    const handleTouchMove = (e: TouchEvent) => { if (draggingRef.current) calcPct(e.touches[0].clientX) }
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
  }, [setPanelPct])

  useEffect(() => {
    return () => {
      if (activeTimerRef.current) clearTimeout(activeTimerRef.current)
    }
  }, [])

  const handleOutlineJump = useCallback((page: number, section?: string) => {
    if (section) {
      pdfViewerRef.current?.jumpToSection(page, section)
    } else {
      pdfViewerRef.current?.jumpToPage(page)
    }
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
      forgetReadingPage(id)
      setPapers(prev => {
        const remaining = prev.filter(p => p.id !== id)
        if (id === paperId) {
          if (remaining.length > 0) navigate(`/reader/${remaining[0].id}`)
          else navigate('/')
        }
        return remaining
      })
    } catch {
      toast({ type: 'error', message: '删除失败，请重试' })
    }
  }, [paperId, navigate])

  const pdfUrl = paperId ? `/api/papers/${paperId}/pdf` : ''

  return (
    <div ref={containerRef} className="er-page er-page--reader rdr-root flex h-full relative">
      {/* Sidebar overlay */}
      {sidebarOpen && !focusMode && (
        <div
          className="fixed inset-0 bg-black/40 z-30 lg:hidden"
          style={{ backdropFilter: 'blur(4px)' }}
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* 左侧 Sidebar */}
      {!focusMode && (
        <div className={`
          fixed lg:static inset-y-0 left-0 z-40 overflow-hidden
          transform transition-all duration-200
          ${sidebarOpen ? 'translate-x-0 w-64' : '-translate-x-full lg:translate-x-0 w-0 lg:w-0'}
        `} style={{ transitionTimingFunction: 'var(--ease-press)' }}>
          {papersLoading && papers.length === 0 ? (
            <div className="flex flex-col w-64 h-full items-center justify-center" style={{
              backgroundColor: 'var(--bg-surface)',
              borderRight: '1px solid var(--border)',
            }}>
              <LoadingSpinner size="sm" />
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
      <div className="rdr-doc flex-1 flex flex-col min-w-0 relative">
        {/* 顶部工具栏 */}
        {!focusMode && (
          <div className="rdr-toolbar">
            <button
              onClick={() => setSidebarOpen(v => !v)}
              aria-label="切换侧边栏"
              aria-expanded={sidebarOpen}
              className="focus-glow p-2 transition-all duration-200 hover:bg-[var(--bg-elevated)]"
              style={{ color: 'var(--text-muted)', transitionTimingFunction: 'var(--ease-press)' }}
              title={sidebarOpen ? '收起论文列表' : '展开论文列表'}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d={sidebarOpen
                    ? "M11 19l-7-7 7-7m8 14l-7-7 7-7"
                    : "M13 5l7 7-7 7M5 5l7 7-7 7"}
                />
              </svg>
            </button>

            <div />

            <div className="flex items-center gap-0.5">
              <button
                onClick={() => setFocusMode(true)}
                className="focus-glow p-2 transition-all duration-200 hover:bg-[var(--bg-elevated)]"
                style={{ color: 'var(--text-muted)' }}
                title="专注模式"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607zM10.5 7.5v6m3-3h-6"
                  />
                </svg>
              </button>

              <button
                onClick={togglePanel}
                aria-label="切换分析面板"
                aria-expanded={panelOpen}
                className="focus-glow p-2 transition-all duration-200 hover:bg-[var(--bg-elevated)]"
                style={{ color: 'var(--text-muted)' }}
                title={panelOpen ? '收起分析面板' : '展开分析面板'}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
        <div className={`rdr-canvas flex-1 overflow-hidden ${focusMode ? '' : 'p-2'}`}>
          {paperId && (
            <PdfViewer
              ref={pdfViewerRef}
              pdfUrl={pdfUrl}
              paperId={paperId}
              citationPage={Number(new URLSearchParams(location.search).get('page')) || null}
              activePage={activePage}
              headings={headings}
              onPageChange={onPageChangeNoop}
            />
          )}
        </div>
      </div>

      {/* 可拖拽分割条 — 精致 glass 风格 */}
      {panelOpen && !focusMode && (
        <div
          data-reader-resizer
          tabIndex={0}
          onMouseDown={handleMouseDown}
          onTouchStart={handleTouchStart}
          onDoubleClick={resetPanelWidth}
          onKeyDown={handleResizerKeyDown}
          className="rdr-resizer focus-glow shrink-0 cursor-col-resize relative group z-10 flex items-center justify-center"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整分析面板宽度（方向键调节，Home 复位）"
          aria-valuenow={Math.round(panelPct)}
          aria-valuemin={MIN_PANEL_PCT}
          aria-valuemax={MAX_PANEL_PCT}
          title="拖动调整宽度 · 双击复位"
        >
          <div className="absolute inset-y-0 -left-1.5 -right-1.5" />
          <div
            className="w-1 h-8 opacity-0 group-hover:opacity-100 transition-opacity duration-200"
            style={{ backgroundColor: 'var(--accent)' }}
          />
        </div>
      )}

      {/* 右侧 AI 分析面板 */}
      {!focusMode && (
        <div
          className="rdr-panel overflow-auto shrink-0"
          style={{ width: panelOpen ? `${panelPct}%` : 0 }}
        >
          {panelOpen && (
            <div className="h-full overflow-auto">
              {error && status === 'failed' ? (
                /* 连接断了和分析失败是两件事：前者只需重新订阅，后者才要重跑。
                   把它们混成一个「重试」按钮，等于让一次网络抖动重烧一遍模型调用。 */
                <div className="flex flex-col items-center justify-center h-full gap-4 p-6">
                  <p
                    className="text-[13px] text-center"
                    style={{ color: connectionLost ? 'var(--warning)' : 'var(--error)' }}
                  >
                    {error}
                  </p>
                  <div className="flex items-center gap-2">
                    {connectionLost && (
                      <button
                        onClick={reconnect}
                        className="focus-glow press px-4 py-2 text-[13px] transition-all duration-200 hover:opacity-90"
                        style={{ backgroundColor: 'var(--accent)', color: '#ffffff' }}
                      >
                        重新连接
                      </button>
                    )}
                    <button
                      onClick={reparse}
                      className="focus-glow press px-4 py-2 text-[13px] transition-all duration-200 hover:opacity-90"
                      style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
                    >
                      {connectionLost ? '重新分析' : '重试'}
                    </button>
                  </div>
                  {connectionLost && (
                    <p className="text-[11px] text-center max-w-[18rem]" style={{ color: 'var(--text-muted)' }}>
                      后台分析任务仍在运行，重新连接即可继续接收进度，无需重跑。
                    </p>
                  )}
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
          aria-label="退出专注模式"
          className="focus-glow fixed top-4 right-4 z-[9999] p-2.5 transition-all duration-200 opacity-60 hover:opacity-100"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
            backdropFilter: 'blur(12px)',
          }}
          title="退出专注模式 (Esc)"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25"
            />
          </svg>
        </button>
      )}
    </div>
  )
}
