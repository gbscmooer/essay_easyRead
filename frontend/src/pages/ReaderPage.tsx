import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import type { Paper } from '@/types'
import { listPapers, deletePaper } from '@/api/papers'
import { useAnalysis } from '@/hooks/useAnalysis'
import { useUIConfig } from '@/hooks/useConfig'
import { Sidebar } from '@/components/Sidebar'
import { PdfViewer } from '@/features/viewer/PdfViewer'
import type { PdfViewerHandle } from '@/features/viewer/PdfViewer'
import { AnalysisPanel } from '@/features/analysis/AnalysisPanel'


const MIN_PANEL_PCT = 20
const MAX_PANEL_PCT = 60
const DEFAULT_PANEL_PCT = 35


export function ReaderPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const uiConfig = useUIConfig()
  const paperId = id ? Number(id) : undefined

  const [papers, setPapers] = useState<Paper[]>([])
  const [activePage, setActivePage] = useState<number | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [panelPct, setPanelPct] = useState(DEFAULT_PANEL_PCT)
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
    listPapers({ per_page: 100 })
      .then(res => setPapers(res.papers))
      .catch(() => {})
  }, [])

  // 拖拽逻辑
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    draggingRef.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!draggingRef.current || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const x = e.clientX - rect.left
      const pct = ((rect.width - x) / rect.width) * 100
      setPanelPct(Math.min(MAX_PANEL_PCT, Math.max(MIN_PANEL_PCT, pct)))
    }

    const handleMouseUp = () => {
      if (draggingRef.current) {
        draggingRef.current = false
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  // 清理 activeTimerRef
  useEffect(() => {
    return () => {
      if (activeTimerRef.current) clearTimeout(activeTimerRef.current)
    }
  }, [])

  // 大纲跳转：通过 ref 调用 PdfViewer 的 jumpToPage
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
      // ignore
    }
  }, [paperId, navigate])

  const pdfUrl = paperId ? `/api/papers/${paperId}/pdf` : ''

  return (
    <div ref={containerRef} className="flex h-full relative">
      {/* Sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-30 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* 左侧 Sidebar */}
      <div className={`
        fixed lg:static inset-y-0 left-0 z-40 overflow-hidden
        transform transition-all duration-200 ease-in-out
        ${sidebarOpen ? 'translate-x-0 w-64' : '-translate-x-full lg:translate-x-0 w-0 lg:w-0'}
      `}>
        <Sidebar
          papers={papers}
          activeId={paperId ?? null}
          onSelect={handleSelect}
          onDelete={handleDelete}
        />
      </div>

      {/* 中间 PDF 阅读区 */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {/* 顶部工具栏 */}
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

        {/* PDF 渲染区 */}
        <div className="flex-1 overflow-hidden p-2">
          {paperId && (
            <PdfViewer
              ref={pdfViewerRef}
              pdfUrl={pdfUrl}
              activePage={activePage}
              onPageChange={onPageChangeNoop}
            />
          )}
        </div>
      </div>

      {/* 可拖拽分割条 */}
      {panelOpen && (
        <div
          onMouseDown={handleMouseDown}
          className="w-1 shrink-0 cursor-col-resize transition-colors relative group z-10"
          style={{ backgroundColor: 'var(--border)' }}
        >
          <div className="absolute inset-y-0 -left-1 -right-1" />
        </div>
      )}

      {/* 右侧 AI 分析面板 */}
      <div
        className="overflow-auto transition-[width] duration-100 ease-out shrink-0"
        style={{ width: panelOpen ? `${panelPct}%` : 0, backgroundColor: 'var(--bg-surface)' }}
      >
        {panelOpen && (
          <div className="h-full overflow-auto">
            {error && status === 'failed' ? (
              <div className="flex flex-col items-center justify-center h-full gap-4 p-6">
                <p className="text-sm text-center" style={{ color: '#dc2626' }}>{error}</p>
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
    </div>
  )
}
