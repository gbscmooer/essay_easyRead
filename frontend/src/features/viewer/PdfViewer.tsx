import React, { useState, useCallback, useRef, useEffect, forwardRef, useImperativeHandle } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import { SelectionPopup } from '@/components/SelectionPopup'
import { useUIConfig } from '@/hooks/useConfig'
import 'react-pdf/dist/esm/Page/AnnotationLayer.css'
import 'react-pdf/dist/esm/Page/TextLayer.css'


pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.js',
  import.meta.url,
).toString()


interface PdfViewerProps {
  pdfUrl: string
  activePage?: number | null
  onPageChange: (page: number) => void
}

export interface PdfViewerHandle {
  jumpToPage: (page: number) => void
}

export const PdfViewer = forwardRef<PdfViewerHandle, PdfViewerProps>(function PdfViewer(
  { pdfUrl, activePage, onPageChange },
  ref,
) {
  const uiConfig = useUIConfig()
  const [numPages, setNumPages] = useState(0)
  const [scale, setScale] = useState(1.0)
  const [pageInput, setPageInput] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [visiblePage, setVisiblePage] = useState(1)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const isScrollingRef = useRef(false)
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const visiblePageRef = useRef(visiblePage)

  const onDocumentLoadSuccess = useCallback(({ numPages: total }: { numPages: number }) => {
    setNumPages(total)
    setIsLoading(false)
  }, [])

  const onDocumentLoadError = useCallback(() => {
    setIsLoading(false)
  }, [])

  // 滚动到指定页面
  const scrollToPage = useCallback((page: number) => {
    const container = scrollRef.current
    const el = pageRefs.current.get(page)
    if (!container || !el) return

    isScrollingRef.current = true
    if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current)

    const containerRect = container.getBoundingClientRect()
    const elRect = el.getBoundingClientRect()
    const offset = elRect.top - containerRect.top + container.scrollTop - 16

    container.scrollTo({ top: offset, behavior: 'smooth' })

    scrollTimerRef.current = setTimeout(() => {
      isScrollingRef.current = false
      setVisiblePage(page)
      onPageChange(page)
    }, 800)
  }, [onPageChange])

  // 暴露 jumpToPage 方法给父组件
  useImperativeHandle(ref, () => ({
    jumpToPage: (page: number) => {
      if (page >= 1 && page <= numPages) {
        scrollToPage(page)
      }
    },
  }), [numPages, scrollToPage])

  // 滚动事件：用户滚动时锁定 Observer
  useEffect(() => {
    const container = scrollRef.current
    if (!container) return

    let scrollTimer: ReturnType<typeof setTimeout>
    const handleScroll = () => {
      isScrollingRef.current = true
      clearTimeout(scrollTimer)
      scrollTimer = setTimeout(() => {
        isScrollingRef.current = false
      }, 150)
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      container.removeEventListener('scroll', handleScroll)
      clearTimeout(scrollTimer)
    }
  }, [])

  useEffect(() => { visiblePageRef.current = visiblePage }, [visiblePage])

  // IntersectionObserver 跟踪当前可见页（仅非滚动时）
  useEffect(() => {
    const container = scrollRef.current
    if (!container || numPages === 0) return

    const observer = new IntersectionObserver(
      (entries) => {
        if (isScrollingRef.current) return

        let maxRatio = 0
        let mostVisible = 1
        for (const entry of entries) {
          if (entry.intersectionRatio > maxRatio) {
            maxRatio = entry.intersectionRatio
            const pageNum = Number(entry.target.getAttribute('data-page'))
            if (pageNum) mostVisible = pageNum
          }
        }
        if (maxRatio > 0 && mostVisible !== visiblePageRef.current) {
          setVisiblePage(mostVisible)
        }
      },
      {
        root: container,
        threshold: [0, 0.25, 0.5, 0.75, 1],
      }
    )

    pageRefs.current.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
  }, [numPages])

  // 两指捏合缩放（触控板 pinch 触发 ctrlKey+wheel）
  useEffect(() => {
    const container = scrollRef.current
    if (!container) return

    const handleWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return
      e.preventDefault()
      const delta = -e.deltaY
      setScale(s => {
        const step = uiConfig.pinch_zoom_step || 0.02
        const max = uiConfig.zoom_max || 3
        const min = uiConfig.zoom_min || 0.4
        if (delta > 0) return Math.min(s + step, max)
        if (delta < 0) return Math.max(s - step, min)
        return s
      })
    }

    container.addEventListener('wheel', handleWheel, { passive: false })
    return () => container.removeEventListener('wheel', handleWheel)
  }, [uiConfig])

  // 清理定时器
  useEffect(() => {
    return () => {
      if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current)
    }
  }, [])

  const goToPage = useCallback((page: number) => {
    if (page >= 1 && page <= numPages) {
      scrollToPage(page)
    }
  }, [numPages, scrollToPage])

  const handlePageInputKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      const page = parseInt(pageInput, 10)
      if (!isNaN(page)) goToPage(page)
      setPageInput('')
    }
  }, [pageInput, goToPage])

  const zoomIn = useCallback(() => setScale(s => Math.min(s + (uiConfig.zoom_step || 0.2), uiConfig.zoom_max || 3)), [uiConfig])
  const zoomOut = useCallback(() => setScale(s => Math.max(s - (uiConfig.zoom_step || 0.2), uiConfig.zoom_min || 0.4)), [uiConfig])
  const resetZoom = useCallback(() => setScale(1.0), [])

  return (
    <div className="flex flex-col h-full rounded-lg overflow-hidden relative" style={{ backgroundColor: 'var(--bg-surface)' }}>
      {/* 文本选择弹窗 */}
      <SelectionPopup containerRef={scrollRef} />

      {/* PDF 内容区 — 连续滚动 */}
      <div ref={scrollRef} className="flex-1 overflow-auto">
        {isLoading && (
          <div className="w-full h-full flex items-center justify-center">
            <div className="w-8 h-8 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }} />
          </div>
        )}
        <Document
          file={pdfUrl}
          onLoadSuccess={onDocumentLoadSuccess}
          onLoadError={onDocumentLoadError}
          loading={null}
        >
          <div className="flex flex-col items-center gap-4 py-4">
            {Array.from({ length: numPages }, (_, i) => i + 1).map(pageNum => (
              <div
                key={pageNum}
                data-page={pageNum}
                ref={el => {
                  if (el) pageRefs.current.set(pageNum, el)
                  else pageRefs.current.delete(pageNum)
                }}
                className={`relative transition-all duration-300 ${
                  activePage === pageNum
                    ? 'ring-2 ring-blue-500/60 ring-offset-2 ring-offset-gray-900 scale-[1.01]'
                    : ''
                }`}
              >
                <Page
                  pageNumber={pageNum}
                  scale={scale}
                  renderTextLayer={true}
                  renderAnnotationLayer={true}
                  className="shadow-xl"
                />
                {/* 页码标注 */}
                <div className="absolute -left-12 top-0 text-xs w-8 text-right select-none" style={{ color: 'var(--text-muted)' }}>
                  {pageNum}
                </div>
                {/* 跳转高亮光晕 */}
                {activePage === pageNum && (
                  <div className="absolute inset-0 pointer-events-none bg-blue-500/10 rounded" />
                )}
              </div>
            ))}
          </div>
        </Document>
      </div>

      {/* 底部控制栏 */}
      <div className="flex items-center justify-between px-4 py-2 border-t backdrop-blur-sm" style={{ backgroundColor: 'var(--bg-surface)', opacity: 0.9, borderColor: 'var(--border)' }}>
        {/* 翻页控制 */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => goToPage(Math.max(1, visiblePage - 1))}
            disabled={visiblePage <= 1}
            className="px-2 py-1 text-sm rounded disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
          >
            上一页
          </button>

          <div className="flex items-center gap-1 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <input
              type="text"
              value={pageInput}
              onChange={e => setPageInput(e.target.value)}
              onKeyDown={handlePageInputKeyDown}
              placeholder={String(visiblePage)}
              className="w-10 text-center rounded text-sm py-0.5 focus:outline-none" style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
            />
            <span>/ {numPages || '--'}</span>
          </div>

          <button
            onClick={() => goToPage(Math.min(numPages, visiblePage + 1))}
            disabled={visiblePage >= numPages}
            className="px-2 py-1 text-sm rounded disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
          >
            下一页
          </button>
        </div>

        {/* 缩放控制 */}
        <div className="flex items-center gap-2">
          <button
            onClick={zoomOut}
            className="px-2 py-1 text-sm rounded transition-colors" style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }} onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }} onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
          >
            −
          </button>
          <button
            onClick={resetZoom}
            className="px-2 py-1 text-xs rounded transition-colors min-w-[3rem] text-center" style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }} onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }} onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
          >
            {Math.round(scale * 100)}%
          </button>
          <button
            onClick={zoomIn}
            className="px-2 py-1 text-sm rounded transition-colors" style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }} onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }} onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
          >
            +
          </button>
        </div>
      </div>
    </div>
  )
})
