import { useToast, type Toast } from '@/hooks/useToast'

const ICONS: Record<Toast['type'], string> = {
  success: '✓',
  error: '✕',
  warning: '⚠',
  info: 'ℹ',
}

const COLORS: Record<Toast['type'], { bg: string; border: string; icon: string }> = {
  success: { bg: '#052e16', border: '#22c55e', icon: '#22c55e' },
  error: { bg: '#450a0a', border: '#ef4444', icon: '#ef4444' },
  warning: { bg: '#422006', border: '#f59e0b', icon: '#f59e0b' },
  info: { bg: '#172554', border: '#3b82f6', icon: '#3b82f6' },
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
            className="pointer-events-auto rounded-lg shadow-lg border px-4 py-3 flex items-start gap-3 min-w-[280px] max-w-[400px]"
            style={{ backgroundColor: c.bg, borderColor: c.border }}
          >
            <span className="text-sm font-bold shrink-0 mt-px" style={{ color: c.icon }}>
              {ICONS[t.type]}
            </span>
            <p className="text-sm flex-1 text-gray-200">
              {t.message}
            </p>
            <button
              onClick={() => dismiss(t.id)}
              className="text-xs shrink-0 p-1.5 rounded opacity-60 hover:opacity-100 hover:bg-white/10 transition-all text-gray-400"
              aria-label="关闭"
            >
              ✕
            </button>
          </div>
        )
      })}
    </div>
  )
}
