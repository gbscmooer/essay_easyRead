import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import type { Paper } from '@/types'
import { getPaper } from '@/api/papers'
import { extractCodeUrl } from '@/api/code'
import { toast } from '@/hooks/useToast'


interface CodeTabProps {
  paperId?: number
}

export function CodeTab({ paperId }: CodeTabProps) {
  const [paper, setPaper] = useState<Paper | null>(null)
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    if (paperId) {
      getPaper(paperId).then(setPaper).catch((e: any) => { toast({ type: 'error', message: e?.message || '加载论文信息失败' }) })
    }
  }, [paperId])

  const handleResearch = useCallback(async () => {
    if (!paperId) return
    setSearching(true)
    try {
      await extractCodeUrl(paperId)
      const updated = await getPaper(paperId)
      setPaper(updated)
    } catch (e: any) {
      toast({ type: 'error', message: e?.message || '提取代码 URL 失败' })
    } finally {
      setSearching(false)
    }
  }, [paperId])

  const codeUrl = paper?.code_url
  const codeStatus = paper?.code_status || 'none'

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-lg font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>代码仓库</h2>

        {codeUrl ? (
          <div className="rounded-lg p-4 space-y-3" style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)' }}>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-green-500" />
              <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>已找到代码仓库</span>
            </div>
            <a
              href={codeUrl.startsWith('http') ? codeUrl : `https://${codeUrl}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-blue-400 hover:text-blue-300 break-all transition-colors"
            >
              {codeUrl}
            </a>
            <div className="flex items-center gap-2 pt-1">
              {codeStatus === 'none' && paperId && (
                <Link
                  to={`/code/${paperId}`}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg transition-colors"
                  style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.opacity = '0.85' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.opacity = '1' }}
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                  </svg>
                  启用代码分析
                </Link>
              )}
              {codeStatus === 'ready' && paperId && (
                <Link
                  to={`/code/${paperId}`}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg transition-colors"
                  style={{ backgroundColor: 'var(--bg-surface)', color: 'var(--text-primary)' }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }}
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                  </svg>
                  查看代码分析
                </Link>
              )}
              {codeStatus === 'cloning' && (
                <span className="inline-flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg" style={{ backgroundColor: 'var(--bg-surface)', color: 'var(--text-secondary)' }}>
                  <div className="w-4 h-4 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--text-muted)', borderTopColor: 'var(--accent)' }} />
                  正在克隆...
                </span>
              )}
              {codeStatus === 'failed' && (
                <span className="text-sm text-red-400">克隆失败</span>
              )}
            </div>
          </div>
        ) : (
          <div className="rounded-lg p-4 text-center space-y-3" style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)' }}>
            <div>
              <p className="text-sm" style={{ color: 'var(--text-muted)' }}>未找到公开的源码地址</p>
              <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>论文中未发现 GitHub/GitLab 等代码仓库链接</p>
            </div>
            <button
              onClick={handleResearch}
              disabled={searching}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded disabled:opacity-50 transition-colors"
              style={{ backgroundColor: 'var(--bg-surface)', color: 'var(--text-secondary)' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }}
            >
              {searching ? (
                <>
                  <div className="w-3 h-3 border rounded-full animate-spin" style={{ borderColor: 'var(--text-muted)', borderTopColor: 'var(--accent)' }} />
                  搜索中...
                </>
              ) : (
                <>
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                  重新查找
                </>
              )}
            </button>
          </div>
        )}
      </section>
    </div>
  )
}

export default CodeTab
