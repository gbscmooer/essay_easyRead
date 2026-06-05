/**
 * HomePage 组件
 *
 * 应用首页，包含上传入口和最近论文概览。
 * 清新学术风格：简洁的 Hero 区域 + 论文卡片网格。
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
    <div className="max-w-4xl mx-auto px-6 py-12">
      {/* Hero 区域 */}
      <div className="text-center mb-14">
        <h1 className="text-3xl font-bold mb-3" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
          Easy Essay
        </h1>
        <p className="text-base mb-10" style={{ color: 'var(--text-muted)', maxWidth: '28rem', margin: '0 auto' }}>
          上传 PDF，自动提取方法论、实验结果和代码映射
        </p>

        <UploadZone onUpload={handleUpload} isUploading={isUploading} />
        {uploadError && (
          <p className="mt-4 text-sm" style={{ color: '#dc2626' }}>{uploadError}</p>
        )}
      </div>

      {/* 最近论文 */}
      {recentPapers.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-5">
            <h2 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>最近解析</h2>
            <button
              onClick={() => navigate('/library')}
              className="text-xs transition-colors"
              style={{ color: 'var(--text-muted)' }}
            >
              查看全部 &rarr;
            </button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {recentPapers.map(paper => (
              <div
                key={paper.id}
                onClick={() => navigate(`/reader/${paper.id}`)}
                className="rounded-xl p-4 cursor-pointer transition-all"
                style={{
                  backgroundColor: '#ffffff',
                  boxShadow: 'var(--shadow-card)',
                  border: '1px solid var(--border)',
                }}
                onMouseEnter={e => {
                  const el = e.currentTarget
                  el.style.boxShadow = 'var(--shadow-elevated)'
                  el.style.borderColor = 'var(--border-light)'
                }}
                onMouseLeave={e => {
                  const el = e.currentTarget
                  el.style.boxShadow = 'var(--shadow-card)'
                  el.style.borderColor = 'var(--border)'
                }}
              >
                <h3 className="text-sm font-medium mb-2 line-clamp-2 leading-snug" style={{ color: 'var(--text-primary)' }}>
                  {paper.title || paper.filename}
                </h3>
                <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
                  {paper.venue && <span>{paper.venue}</span>}
                  {paper.venue && paper.year && <span>&middot;</span>}
                  {paper.year && <span>{paper.year}</span>}
                </div>
                <div className="mt-3 flex items-center gap-1.5">
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{
                      backgroundColor:
                        paper.status === 'done' ? '#22c55e'
                        : paper.status === 'failed' ? '#ef4444'
                        : '#eab308'
                    }}
                  />
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
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
