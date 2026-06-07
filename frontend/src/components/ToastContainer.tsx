import { useToast, type Toast } from '@/hooks/useToast'

const ICONS: Record<Toast['type'], string> = {
  success: '✓',
  error: '✕',
  warning: '⚠',
  info: 'ℹ',
}

const COLORS: Record<Toast['type'], { bg: string; border: string; icon: string }> = {
  success: { bg: 'var(--success-subtle)', border: 'var(--success)', icon: 'var(--success)' },
  error: { bg: 'var(--error-subtle)', border: 'var(--error)', icon: 'var(--error)' },
  warning: { bg: 'var(--warning-subtle)', border: 'var(--warning)', icon: 'var(--warning)' },
  info: { bg: 'var(--accent-subtle)', border: 'var(--accent)', icon: 'var(--accent)' },
}

export function ToastContainer() {
  const { toasts, dismiss } = useToast()

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => {
        const c = COLORS[t.type]
        return (
          <div
            key={t.id}
            className="pointer-events-auto rounded-lg shadow-lg border px-4 py-3 flex items-start gap-3 min-w-[280px] max-w-[400px] animate-slide-in"
            style={{ backgroundColor: c.bg, borderColor: c.border }}
          >
            <span className="text-sm font-bold shrink-0 mt-px" style={{ color: c.icon }}>
              {ICONS[t.type]}
            </span>
            <p className="text-sm flex-1" style={{ color: 'var(--text-primary)' }}>
              {t.message}
            </p>
            <button
              onClick={() => dismiss(t.id)}
              className="text-xs shrink-0 mt-px opacity-60 hover:opacity-100 transition-opacity"
              style={{ color: 'var(--text-muted)' }}
            >
              ✕
            </button>
          </div>
        )
      })}
    </div>
  )
}
