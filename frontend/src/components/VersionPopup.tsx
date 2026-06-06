import { useEffect, useRef } from 'react'

const APP_VERSION = '1.5.5'
const GITHUB_URL = 'https://github.com/gbscmooer/essay_easyRead'

const CURRENT_RELEASE = {
  date: '2026-06-06',
  changes: [
    '论文库新增「深度研究发现的论文」专栏，展示联网扩展下载的论文，默认折叠',
    '首页及主论文库默认只显示用户上传的论文，扩展论文不再混入',
    '专栏每行提供「加入论文库」按钮，一键将扩展论文移入主库并在首页显示',
    '相似度阈值默认改为 0.9，可在设置持久化，每次打开研究面板自动加载',
  ],
}


export function VersionPopup({ onClose }: { onClose: () => void }) {
  const popupRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [onClose])

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center" style={{ backgroundColor: 'rgba(0,0,0,0.4)' }}>
      <div
        ref={popupRef}
        className="w-80 rounded-xl border overflow-hidden"
        style={{
          backgroundColor: 'var(--bg-elevated)',
          borderColor: 'var(--border)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="p-4 pb-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <span style={{ color: 'var(--accent)' }}>&#9670;</span>
              <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                Easy Essay
              </span>
            </div>
            <span
              className="text-[10px] px-1.5 py-0.5 rounded-full"
              style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}
            >
              v{APP_VERSION}
            </span>
          </div>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-xs mt-1 transition-opacity hover:opacity-80"
            style={{ color: 'var(--text-muted)' }}
          >
            <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
            </svg>
            GitHub
            <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        </div>

        {/* 当前版本更新日志 */}
        <div className="p-4 pt-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
              v{APP_VERSION}
            </span>
            <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
              {CURRENT_RELEASE.date}
            </span>
          </div>
          <ul className="space-y-0.5">
            {CURRENT_RELEASE.changes.map((change, i) => (
              <li key={i} className="text-[11px] leading-relaxed pl-2" style={{ color: 'var(--text-secondary)' }}>
                <span style={{ color: 'var(--text-muted)' }}>· </span>{change}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}

export { APP_VERSION }
