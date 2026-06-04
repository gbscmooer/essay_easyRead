/**
 * HomePage 组件
 *
 * 应用首页，包含上传入口和最近论文概览。
 *
 * 布局：
 *   - Hero 区域：标题 + 副标题 + 上传组件
 *   - 最近论文区域：最近解析的 6 篇论文卡片
 *
 * 功能：
 *   - UploadZone 上传 PDF
 *   - 上传成功后自动跳转到 ReaderPage
 *   - 展示最近论文的缩略信息
 *   - 点击论文卡片跳转到 ReaderPage
 *
 * 样式：
 *   - Hero：居中，max-w-2xl, my-16
 *   - 最近论文：grid-cols-3, gap-4
 *   - 渐入动画（可选）
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Paper } from '@/types'
import { UploadZone } from '@/features/upload/UploadZone'
import { listPapers, uploadPaper } from '@/api/papers'


export function HomePage() {
  const navigate = useNavigate()
  const [recentPapers, setRecentPapers] = useState<Paper[]>([])
  const [isUploading, setIsUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  useEffect(() => {
    listPapers({ page: 1, per_page: 6 })
      .then(res => setRecentPapers(res.papers))
      .catch(() => {})
  }, [])

  const handleUpload = async (file: File) => {
    setIsUploading(true)
    setUploadError(null)
    try {
      const paper = await uploadPaper(file)
      navigate(`/reader/${paper.id}`)
    } catch {
      setUploadError('上传失败，请检查文件格式后重试')
    } finally {
      setIsUploading(false)
    }
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-16">
      {/* Hero 区域 */}
      <div className="text-center mb-16">
        <h1 className="text-4xl font-bold text-gray-100 mb-4">论文精读助手</h1>
        <p className="text-lg text-gray-400 mb-10">上传论文 PDF，AI 帮你快速理解方法、实验和代码</p>

        <UploadZone onUpload={handleUpload} isUploading={isUploading} />
        {uploadError && (
          <p className="mt-4 text-sm text-red-400">{uploadError}</p>
        )}
      </div>

      {/* 最近论文 */}
      {recentPapers.length > 0 && (
        <section>
          <h2 className="text-xl font-semibold text-gray-200 mb-6">最近解析</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {recentPapers.map(paper => (
              <div
                key={paper.id}
                onClick={() => navigate(`/reader/${paper.id}`)}
                className="bg-gray-900 border border-gray-800 rounded-lg p-4 hover:bg-gray-800 cursor-pointer transition-colors"
              >
                <h3 className="text-sm font-medium text-gray-200 mb-2 line-clamp-2">
                  {paper.title || paper.filename}
                </h3>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  {paper.venue && <span>{paper.venue}</span>}
                  {paper.year && <span>{paper.year}</span>}
                </div>
                <div className="mt-3 flex items-center gap-1.5">
                  <span
                    className={`w-2 h-2 rounded-full ${
                      paper.status === 'done'
                        ? 'bg-green-500'
                        : paper.status === 'failed'
                        ? 'bg-red-500'
                        : 'bg-yellow-500'
                    }`}
                  />
                  <span className="text-xs text-gray-500">
                    {paper.status === 'done' ? '已完成' : paper.status === 'failed' ? '失败' : '处理中'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
