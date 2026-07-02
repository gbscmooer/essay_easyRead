import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import type { Paper } from '@/types'
import { getPaper } from '@/api/papers'
import { extractCodeUrl } from '@/api/code'
import { toast } from '@/hooks/useToast'

const SPRING = 'cubic-bezier(0.25, 0.46, 0.45, 0.94)'

interface CodeTabProps {
  paperId?: number
}

export function CodeTab({ paperId }: CodeTabProps) {
  const [paper, setPaper] = useState<Paper | null>(null)
  const [searching, setSearching] = useState(false)
  // 纯展示层：复制反馈，不触后端、不改既有数据流
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (paperId) {
      getPaper(paperId).then(setPaper).catch((e: unknown) => { toast({ type: 'error', message: e instanceof Error ? e.message : '加载论文信息失败' }) })
    }
  }, [paperId])

  const handleResearch = useCallback(async () => {
    if (!paperId) return
    setSearching(true)
    try {
      await extractCodeUrl(paperId)
      const updated = await getPaper(paperId)
      setPaper(updated)
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '提取代码 URL 失败' })
    } finally {
      setSearching(false)
    }
  }, [paperId])

  const codeUrl = paper?.code_url
  const codeStatus = paper?.code_status || 'none'

  const handleCopyUrl = useCallback(() => {
    if (!codeUrl) return
    const full = codeUrl.startsWith('http') ? codeUrl : `https://${codeUrl}`
    navigator.clipboard?.writeText(full).then(
      () => { setCopied(true); window.setTimeout(() => setCopied(false), 1600) },
      () => { /* clipboard 不可用时静默 */ },
    )
  }, [codeUrl])

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-heading text-[15px] mb-3.5" style={{ color: 'var(--text-primary)' }}>代码仓库</h2>

        {codeUrl ? (
          <div className="panel-section space-y-3.5">
            <div className="flex items-center gap-2 text-[13px]" style={{ color: 'var(--success)' }}>
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="9" strokeWidth={1.5} />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.5 12l2.5 2.5 4.5-4.5" />
              </svg>
              <span>已找到代码仓库</span>
            </div>
            <div className="flex items-start gap-2">
              <a
                href={codeUrl.startsWith('http') ? codeUrl : `https://${codeUrl}`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1 min-w-0 text-[13px] break-all transition-opacity hover:opacity-80 focus-glow rounded"
                style={{ color: 'var(--accent)' }}
              >
                {codeUrl}
              </a>
              <button
                onClick={handleCopyUrl}
                aria-label={copied ? '已复制仓库地址' : '复制仓库地址'}
                title={copied ? '已复制' : '复制地址'}
                className="shrink-0 inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded-full transition-all duration-200 focus-glow press"
                style={{ color: copied ? 'var(--success)' : 'var(--text-muted)', border: '1px solid var(--border)', backgroundColor: 'var(--bg-elevated)', transitionTimingFunction: SPRING }}
              >
                {copied ? (
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                ) : (
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                  </svg>
                )}
                {copied ? '已复制' : '复制'}
              </button>
            </div>
            <div className="flex items-center gap-2 pt-1">
              {codeStatus === 'none' && paperId && (
                <Link
                  to={`/code/${paperId}`}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-[13px] font-medium rounded-full transition-all duration-200 focus-glow press"
                  style={{ backgroundColor: 'var(--accent)', color: '#ffffff', transitionTimingFunction: SPRING }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.opacity = '0.9' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.opacity = '1' }}
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                  </svg>
                  启用代码分析
                </Link>
              )}
              {codeStatus === 'ready' && paperId && (
                <Link
                  to={`/code/${paperId}`}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-[13px] rounded-full transition-all duration-200 focus-glow press"
                  style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-primary)', border: '1px solid var(--border)', transitionTimingFunction: SPRING }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                  </svg>
                  查看代码分析
                </Link>
              )}
              {codeStatus === 'cloning' && (
                <span className="inline-flex items-center gap-1.5 px-4 py-2 text-[13px] rounded-full" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}>
                  <div className="w-4 h-4 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }} />
                  正在克隆…
                </span>
              )}
              {codeStatus === 'failed' && (
                <span className="text-[13px]" style={{ color: 'var(--error)' }}>克隆失败</span>
              )}
            </div>
          </div>
        ) : (
          <div className="content-section text-center space-y-3.5 flex flex-col items-center">
            <div
              className="w-11 h-11 rounded-2xl flex items-center justify-center"
              style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--text-muted)' }}>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
              </svg>
            </div>
            <div>
              <p className="text-[13px]" style={{ color: 'var(--text-secondary)' }}>未找到公开的源码地址</p>
              <p className="text-[12px] mt-1" style={{ color: 'var(--text-muted)' }}>论文中未发现 GitHub/GitLab 等代码仓库链接</p>
            </div>
            <button
              onClick={handleResearch}
              disabled={searching}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-[12px] rounded-full disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200 focus-glow press"
              style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)', border: '1px solid var(--border)', transitionTimingFunction: SPRING }}
              onMouseEnter={e => { if (!searching) (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }}
            >
              {searching ? (
                <>
                  <div className="w-3 h-3 border rounded-full animate-spin" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }} />
                  搜索中…
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
