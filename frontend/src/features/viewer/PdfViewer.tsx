import React, { useState, useCallback, useRef, useEffect } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import { SelectionPopup } from '@/components/SelectionPopup'
import 'react-pdf/dist/esm/Page/AnnotationLayer.css'
import 'react-pdf/dist/esm/Page/TextLayer.css'


pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.js',
  import.meta.url,
).toString()


interface PdfViewerProps {
  pdfUrl: string
  currentPage: number
  onPageChange: (page: number) => void
}

export function PdfViewer({ pdfUrl, currentPage: _currentPage, onPageChange }: PdfViewerProps) {
  const [numPages, setNumPages] = useState(0)
  const [scale, setScale] = useState(1.0)
  const [pageInput, setPageInput] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [visiblePage, setVisiblePage] = useState(1)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map())

  const onDocumentLoadSuccess = useCallback(({ numPages: total }: { numPages: number }) => {
    setNumPages(total)
    setIsLoading(false)
  }, [])

  const onDocumentLoadError = useCallback(() => {
    setIsLoading(false)
  }, [])

  // IntersectionObserver 跟踪当前可见页
  useEffect(() => {
    const container = scrollRef.current
    if (!container || numPages === 0) return

    const observer = new IntersectionObserver(
      (entries) => {
        // 找到最可见的页面
        let maxRatio = 0
        let mostVisible = visiblePage
        for (const entry of entries) {
          if (entry.intersectionRatio > maxRatio) {
            maxRatio = entry.intersectionRatio
            const pageNum = Number(entry.target.getAttribute('data-page'))
            if (pageNum) mostVisible = pageNum
          }
        }
        if (maxRatio > 0) {
          setVisiblePage(mostVisible)
          onPageChange(mostVisible)
        }
      },
      {
        root: container,
        threshold: [0, 0.25, 0.5, 0.75, 1],
      }
    )

    pageRefs.current.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
  }, [numPages, onPageChange])

  // 外部跳页：滚动到目标页
  const scrollToPage = useCallback((page: number) => {
    const el = pageRefs.current.get(page)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
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

  const zoomIn = useCallback(() => setScale(s => Math.min(s + 0.2, 3.0)), [])
  const zoomOut = useCallback(() => setScale(s => Math.max(s - 0.2, 0.4)), [])
  const resetZoom = useCallback(() => setScale(1.0), [])

  return (
    <div className="flex flex-col h-full bg-gray-900 rounded-lg overflow-hidden relative">
      {/* 文本选择弹窗 */}
      <SelectionPopup containerRef={scrollRef} />

      {/* PDF 内容区 — 连续滚动 */}
      <div ref={scrollRef} className="flex-1 overflow-auto">
        {isLoading && (
          <div className="w-full h-full flex items-center justify-center">
            <div className="w-8 h-8 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
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
                className="relative"
              >
                <Page
                  pageNumber={pageNum}
                  scale={scale}
                  renderTextLayer={true}
                  renderAnnotationLayer={true}
                  className="shadow-xl"
                />
                {/* 页码标注 */}
                <div className="absolute -left-12 top-0 text-xs text-gray-600 w-8 text-right select-none">
                  {pageNum}
                </div>
              </div>
            ))}
          </div>
        </Document>
      </div>

      {/* 底部控制栏 */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-900/90 border-t border-gray-800 backdrop-blur-sm">
        {/* 翻页控制 */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => goToPage(Math.max(1, visiblePage - 1))}
            disabled={visiblePage <= 1}
            className="px-2 py-1 text-sm text-gray-300 bg-gray-800 rounded hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            上一页
          </button>

          <div className="flex items-center gap-1 text-sm text-gray-400">
            <input
              type="text"
              value={pageInput}
              onChange={e => setPageInput(e.target.value)}
              onKeyDown={handlePageInputKeyDown}
              placeholder={String(visiblePage)}
              className="w-10 text-center bg-gray-800 border border-gray-700 rounded text-gray-200 text-sm py-0.5 focus:outline-none focus:border-blue-500"
            />
            <span>/ {numPages || '--'}</span>
          </div>

          <button
            onClick={() => goToPage(Math.min(numPages, visiblePage + 1))}
            disabled={visiblePage >= numPages}
            className="px-2 py-1 text-sm text-gray-300 bg-gray-800 rounded hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            下一页
          </button>
        </div>

        {/* 缩放控制 */}
        <div className="flex items-center gap-2">
          <button
            onClick={zoomOut}
            className="px-2 py-1 text-sm text-gray-300 bg-gray-800 rounded hover:bg-gray-700 transition-colors"
          >
            −
          </button>
          <button
            onClick={resetZoom}
            className="px-2 py-1 text-xs text-gray-400 bg-gray-800 rounded hover:bg-gray-700 transition-colors min-w-[3rem] text-center"
          >
            {Math.round(scale * 100)}%
          </button>
          <button
            onClick={zoomIn}
            className="px-2 py-1 text-sm text-gray-300 bg-gray-800 rounded hover:bg-gray-700 transition-colors"
          >
            +
          </button>
        </div>
      </div>
    </div>
  )
}
