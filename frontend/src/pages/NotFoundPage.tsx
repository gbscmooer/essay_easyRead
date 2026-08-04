import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <div className="er-page er-page--not-found flex flex-col items-center justify-center h-full gap-6 px-6 text-center animate-reveal-up">
      {/* 友好插画 — 漂浮的「丢失文档」图标 */}
      <div
        className="w-20 h-20 rounded-3xl flex items-center justify-center"
        style={{
          backgroundColor: 'var(--bg-elevated)',
          border: '1px solid var(--border)',
          boxShadow: '0 8px 24px -10px rgba(0, 0, 0, 0.18)',
        }}
      >
        <svg
          width="36"
          height="36"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ color: 'var(--text-muted)' }}
          aria-hidden="true"
        >
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="9.5" y1="13" x2="14.5" y2="18" />
          <line x1="14.5" y1="13" x2="9.5" y2="18" />
        </svg>
      </div>

      <div className="flex flex-col items-center gap-2">
        <p className="text-display text-7xl leading-none" style={{ color: 'var(--text-muted)', opacity: 0.35 }}>
          404
        </p>
        <p className="text-heading text-base" style={{ color: 'var(--text-primary)' }}>
          页面不存在
        </p>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          你访问的页面可能已被移除或从未存在
        </p>
      </div>

      <Link
        to="/"
        className="press focus-glow inline-flex items-center gap-2 px-5 py-2.5 text-sm font-medium rounded-xl transition-all duration-200"
        style={{
          backgroundColor: 'var(--accent)',
          color: 'var(--bg-base)',
          boxShadow: '0 4px 12px -4px rgba(var(--seed-accent-rgb, 37, 99, 235), 0.4)',
          transitionTimingFunction: 'var(--ease-press)',
        }}
        onMouseEnter={e => { (e.currentTarget as HTMLElement).style.opacity = '0.92' }}
        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.opacity = '1' }}
      >
        <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 12l9-9 9 9" />
          <path d="M9 21V12h6v9" />
        </svg>
        返回首页
      </Link>
    </div>
  )
}
