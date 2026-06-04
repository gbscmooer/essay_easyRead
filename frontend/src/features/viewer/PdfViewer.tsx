/**
 * PdfViewer 组件
 *
 * PDF 文件预览，使用 react-pdf 渲染。
 *
 * Props:
 *   pdfUrl: string - PDF 文件的 URL（如 /api/papers/{id}/pdf 或 blob URL）
 *   currentPage: number - 当前页码（1-based）
 *   onPageChange: (page: number) => void - 翻页回调
 *
 * 功能：
 *   - 渲染当前页的 PDF
 *   - 上一页/下一页按钮
 *   - 页码显示和跳转输入框
 *   - 缩放控制（+/-/重置）
 *   - 自适应容器宽度
 *   - 加载中显示 skeleton
 *
 * 依赖：
 *   - react-pdf (Document, Page, pdfjs)
 *   - 需要配置 pdfjs worker
 *
 * 样式：
 *   - 容器：bg-gray-900, 圆角, 阴影
 *   - 控制栏：底部固定，半透明背景
 *   - 页面：居中显示，阴影
 */

import React, { useState, useCallback } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/esm/Page/AnnotationLayer.css'
import 'react-pdf/dist/esm/Page/TextLayer.css'


// 配置 pdfjs worker
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()


interface PdfViewerProps {
  pdfUrl: string
  currentPage: number
  onPageChange: (page: number) => void
}

export function PdfViewer({ pdfUrl, currentPage, onPageChange }: PdfViewerProps) {
  const [numPages, setNumPages] = useState(0)
  const [scale, setScale] = useState(1.0)
  const [pageInput, setPageInput] = useState('')
  const [isLoading, setIsLoading] = useState(true)

  const onDocumentLoadSuccess = useCallback(({ numPages: total }: { numPages: number }) => {
    setNumPages(total)
    setIsLoading(false)
  }, [])

  const onDocumentLoadError = useCallback(() => {
    setIsLoading(false)
  }, [])

  const goToPage = useCallback((page: number) => {
    if (page >= 1 && page <= numPages) {
      onPageChange(page)
    }
  }, [numPages, onPageChange])

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
    <div className="flex flex-col h-full bg-gray-900 rounded-lg overflow-hidden">
      {/* PDF 内容区 */}
      <div className="flex-1 overflow-auto flex justify-center p-4">
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
          <Page
            pageNumber={currentPage}
            scale={scale}
            renderTextLayer={true}
            renderAnnotationLayer={true}
            className="shadow-xl"
          />
        </Document>
      </div>

      {/* 底部控制栏 */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-900/90 border-t border-gray-800 backdrop-blur-sm">
        {/* 翻页控制 */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => goToPage(currentPage - 1)}
            disabled={currentPage <= 1}
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
              placeholder={String(currentPage)}
              className="w-10 text-center bg-gray-800 border border-gray-700 rounded text-gray-200 text-sm py-0.5 focus:outline-none focus:border-blue-500"
            />
            <span>/ {numPages || '--'}</span>
          </div>

          <button
            onClick={() => goToPage(currentPage + 1)}
            disabled={currentPage >= numPages}
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
