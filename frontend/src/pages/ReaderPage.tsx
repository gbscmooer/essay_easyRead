/**
 * ReaderPage 组件
 *
 * 论文精读主页面，左右分栏布局。
 *
 * 路由：/reader/:id
 *
 * 布局：
 *   - 左侧（40%）：PDF 预览
 *     - Sidebar（可折叠的论文列表）
 *     - PdfViewer
 *   - 右侧（60%）：解析结果
 *     - AnalysisPanel（含 Tab 切换）
 *
 * 功能：
 *   - 从 URL 参数获取 paperId
 *   - 加载论文数据和解析结果
 *   - 如果解析中，显示 LoadingState
 *   - 如果解析完成，显示 AnalysisPanel
 *   - 左侧可切换到其他论文
 *   - 支持拖拽调整左右比例（可选）
 *
 * 样式：
 *   - 容器：flex, h-screen (减去导航栏高度)
 *   - 左侧面板：w-2/5, border-r, border-gray-800
 *   - 右侧面板：w-3/5, overflow-auto
 *   - 响应式：移动端上下布局
 */

import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import type { Paper } from '@/types'
import { listPapers, deletePaper } from '@/api/papers'
import { useAnalysis } from '@/hooks/useAnalysis'
import { Sidebar } from '@/components/Sidebar'
import { PdfViewer } from '@/features/viewer/PdfViewer'
import { AnalysisPanel } from '@/features/analysis/AnalysisPanel'


export function ReaderPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const paperId = id ? Number(id) : undefined

  const [papers, setPapers] = useState<Paper[]>([])
  const [currentPage, setCurrentPage] = useState(1)

  const {
    analysis,
    status,
    progressMessage,
    partialResult,
    error,
  } = useAnalysis(paperId)

  // 加载论文列表
  useEffect(() => {
    listPapers({ per_page: 100 })
      .then(res => setPapers(res.papers))
      .catch(() => {})
  }, [])

  const handleSelect = useCallback((id: number) => {
    navigate(`/reader/${id}`)
    setCurrentPage(1)
  }, [navigate])

  const handleDelete = useCallback(async (id: number) => {
    try {
      await deletePaper(id)
      setPapers(prev => prev.filter(p => p.id !== id))
      // 如果删除的是当前论文，跳转到首页
      if (id === paperId) {
        const remaining = papers.filter(p => p.id !== id)
        if (remaining.length > 0) {
          navigate(`/reader/${remaining[0].id}`)
        } else {
          navigate('/')
        }
      }
    } catch {
      // 删除失败
    }
  }, [paperId, papers, navigate])

  const pdfUrl = paperId ? `/api/papers/${paperId}/pdf` : ''

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      {/* 左侧：Sidebar + PDF 预览 */}
      <div className="flex w-2/5 border-r border-gray-800 shrink-0">
        <Sidebar
          papers={papers}
          activeId={paperId ?? null}
          onSelect={handleSelect}
          onDelete={handleDelete}
        />
        <div className="flex-1 overflow-hidden p-2">
          {paperId && (
            <PdfViewer
              pdfUrl={pdfUrl}
              currentPage={currentPage}
              onPageChange={setCurrentPage}
            />
          )}
        </div>
      </div>

      {/* 右侧：解析结果 */}
      <div className="flex-1 overflow-auto">
        {error && status === 'failed' ? (
          <div className="flex flex-col items-center justify-center h-full gap-4">
            <p className="text-red-400">{error}</p>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 text-sm bg-gray-800 text-gray-300 rounded hover:bg-gray-700 transition-colors"
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
          />
        )}
      </div>
    </div>
  )
}
