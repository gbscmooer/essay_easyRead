import React, { useState, useCallback, useRef, useEffect, forwardRef, useImperativeHandle } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import { SelectionPopup } from '@/components/SelectionPopup'
import { AnnotationLayer } from './AnnotationLayer'
import { useUIConfig } from '@/hooks/useConfig'
import { listAnnotations, createAnnotation, deleteAnnotation, deleteAnnotationGroup } from '@/api/annotations'
import type { Annotation, AnnotationType } from '@/types'
import 'react-pdf/dist/esm/Page/AnnotationLayer.css'
import 'react-pdf/dist/esm/Page/TextLayer.css'


pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.js',
  import.meta.url,
).toString()


interface PdfViewerProps {
  pdfUrl: string
  paperId?: number
  activePage?: number | null
  onPageChange: (page: number) => void
}

export interface PdfViewerHandle {
  jumpToPage: (page: number) => void
}

export const PdfViewer = forwardRef<PdfViewerHandle, PdfViewerProps>(function PdfViewer(
  { pdfUrl, paperId, activePage, onPageChange },
  ref,
) {
  const uiConfig = useUIConfig()
  const [numPages, setNumPages] = useState(0)
  const [scale, setScale] = useState(1.0)
  const [pageInput, setPageInput] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [visiblePage, setVisiblePage] = useState(1)
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const scrollRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const isScrollingRef = useRef(false)
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const visiblePageRef = useRef(visiblePage)

  // Load annotations when paperId changes
  useEffect(() => {
    if (!paperId) {
      setAnnotations([])
      return
    }
    listAnnotations(paperId)
      .then(setAnnotations)
      .catch(() => setAnnotations([]))
  }, [paperId])

  const handleAnnotate = useCallback(async (data: {
    pageNumber: number
    x: number
    y: number
    width: number
    height: number
    annotationType: AnnotationType
    color: string
    text: string
    groupId: string
  }) => {
    if (!paperId) return
    try {
      const ann = await createAnnotation(paperId, {
        page_number: data.pageNumber,
        x: data.x,
        y: data.y,
        width: data.width,
        height: data.height,
        annotation_type: data.annotationType,
        color: data.color,
        text: data.text,
        group_id: data.groupId,
      })
      setAnnotations(prev => [...prev, ann])
    } catch {
      // ignore
    }
  }, [paperId])

  const handleDeleteAnnotation = useCallback(async (id: number) => {
    try {
      // Find the annotation to check its group_id
      const target = annotations.find(a => a.id === id)
      if (target?.group_id) {
        // Delete the entire group
        await deleteAnnotationGroup(target.group_id)
        const gid = target.group_id
        setAnnotations(prev => prev.filter(a => a.group_id !== gid))
      } else {
        // Fallback: single delete (legacy annotations without group_id)
        await deleteAnnotation(id)
        setAnnotations(prev => prev.filter(a => a.id !== id))
      }
    } catch {
      // ignore
    }
  }, [annotations])

  const onDocumentLoadSuccess = useCallback(({ numPages: total }: { numPages: number }) => {
    setNumPages(total)
    setIsLoading(false)
  }, [])

  const onDocumentLoadError = useCallback(() => {
    setIsLoading(false)
  }, [])

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

  useImperativeHandle(ref, () => ({
    jumpToPage: (page: number) => {
      if (page >= 1 && page <= numPages) {
        scrollToPage(page)
      }
    },
  }), [numPages, scrollToPage])

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
    <div
      className="flex flex-col h-full rounded-lg overflow-hidden relative"
      style={{ backgroundColor: 'var(--bg-surface)' }}
    >
      {/* 文本选择弹窗 */}
      <SelectionPopup containerRef={scrollRef} onAnnotate={handleAnnotate} />

      {/* PDF 内容区 — 连续滚动 */}
      <div ref={scrollRef} className="flex-1 overflow-auto">
        {isLoading && (
          <div className="w-full h-full flex items-center justify-center">
            <div
              className="w-8 h-8 border-2 rounded-full animate-spin"
              style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }}
            />
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
                style={activePage === pageNum ? { '--tw-ring-offset-color': 'var(--bg-surface)' } as React.CSSProperties : undefined}
              >
                <Page
                  pageNumber={pageNum}
                  scale={scale}
                  renderTextLayer={true}
                  renderAnnotationLayer={true}
                  className="shadow-xl"
                />
                {/* 标注叠加层 */}
                <AnnotationLayer
                  annotations={annotations}
                  pageNumber={pageNum}
                  onDelete={handleDeleteAnnotation}
                />
                {/* 页码标注 */}
                <div className="absolute -left-12 top-0 text-xs w-8 text-right select-none" style={{ color: 'var(--text-muted)' }}>
                  {pageNum}
                </div>
                {/* 跳转高亮光晕 */}
                {activePage === pageNum && (
                  <div className="absolute inset-0 pointer-events-none rounded" style={{ backgroundColor: 'var(--accent-subtle)' }} />
                )}
              </div>
            ))}
          </div>
        </Document>
      </div>

      {/* 底部控制栏 */}
      <div
        className="flex items-center justify-between px-4 py-2 backdrop-blur-sm border-t"
        style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)', opacity: 0.95 }}
      >
        {/* 翻页控制 */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => goToPage(Math.max(1, visiblePage - 1))}
            disabled={visiblePage <= 1}
            className="px-2 py-1 text-sm rounded hover:opacity-80 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
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
              className="w-10 text-center rounded text-sm py-0.5 focus:outline-none"
              style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-light)', color: 'var(--text-primary)' }}
            />
            <span>/ {numPages || '--'}</span>
          </div>

          <button
            onClick={() => goToPage(Math.min(numPages, visiblePage + 1))}
            disabled={visiblePage >= numPages}
            className="px-2 py-1 text-sm rounded hover:opacity-80 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
          >
            下一页
          </button>
        </div>

        {/* 标注统计 */}
        {annotations.length > 0 && (
          <span className="text-xs text-gray-500">
            {annotations.length} 个标注
          </span>
        )}

        {/* 缩放控制 */}
        <div className="flex items-center gap-2">
          <button
            onClick={zoomOut}
            className="px-2 py-1 text-sm rounded hover:opacity-80 transition-colors"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
          >
            −
          </button>
          <button
            onClick={resetZoom}
            className="px-2 py-1 text-xs rounded hover:opacity-80 transition-colors min-w-[3rem] text-center"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
          >
            {Math.round(scale * 100)}%
          </button>
          <button
            onClick={zoomIn}
            className="px-2 py-1 text-sm rounded hover:opacity-80 transition-colors"
            style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-elevated)' }}
          >
            +
          </button>
        </div>
      </div>
    </div>
  )
})
