/**
 * Toast 通知容器 — 磨砂玻璃 + 左侧强调色条
 */
import { useToast, type Toast } from '@/hooks/useToast'

const ICONS: Record<Toast['type'], React.ReactNode> = {
  success: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  ),
  error: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="15" y1="9" x2="9" y2="15" />
      <line x1="9" y1="9" x2="15" y2="15" />
    </svg>
  ),
  warning: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  ),
  info: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  ),
}

const COLORS: Record<Toast['type'], { accent: string; subtle: string }> = {
  success: { accent: 'var(--success)', subtle: 'var(--success-subtle)' },
  error: { accent: 'var(--error)', subtle: 'var(--error-subtle)' },
  warning: { accent: 'var(--warning)', subtle: 'var(--warning-subtle)' },
  info: { accent: 'var(--accent)', subtle: 'var(--accent-subtle)' },
}

export function ToastContainer() {
  const { toasts, dismiss } = useToast()

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-6 right-6 z-[9999] flex flex-col gap-2.5 pointer-events-none">
      {toasts.map(t => {
        const c = COLORS[t.type]
        return (
          <div
            key={t.id}
            className="pointer-events-auto animate-slide-in flex items-start min-w-[300px] max-w-[420px] overflow-hidden"
            style={{
              borderRadius: 'var(--seed-radius, 12px)',
              boxShadow: '0 4px 24px rgba(0,0,0,0.12), 0 1px 4px rgba(0,0,0,0.06)',
            }}
          >
            {/* 左侧强调色条 */}
            <div className="shrink-0 w-[3px] self-stretch" style={{ backgroundColor: c.accent }} />

            {/* 内容区 */}
            <div
              className="flex items-start gap-3 px-4 py-3.5 flex-1"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--bg-surface) 85%, transparent)',
                backdropFilter: 'blur(20px) saturate(180%)',
                WebkitBackdropFilter: 'blur(20px) saturate(180%)',
              }}
            >
              <span className="shrink-0 mt-0.5" style={{ color: c.accent }}>
                {ICONS[t.type]}
              </span>
              <p className="text-[13px] flex-1 leading-relaxed" style={{ color: 'var(--text-primary)' }}>
                {t.message}
              </p>
              <button
                onClick={() => dismiss(t.id)}
                className="shrink-0 p-1 rounded-md transition-all duration-200 opacity-50 hover:opacity-100"
                style={{
                  color: 'var(--text-muted)',
                  backgroundColor: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                }}
                aria-label="关闭"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
