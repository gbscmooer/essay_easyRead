import { useToast, type Toast } from '@/hooks/useToast'
import '@/styles/overlays.css'

const ICONS: Record<Toast['type'], React.ReactNode> = {
  success: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">
      <path d="m5 12 4 4L19 6" />
    </svg>
  ),
  error: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  ),
  warning: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
      <path d="M12 4v11M12 19v1" />
    </svg>
  ),
  info: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
      <path d="M12 10v10M12 4v2" />
    </svg>
  ),
}

const TYPE_LABELS: Record<Toast['type'], string> = {
  success: 'SUCCESS',
  error: 'ERROR',
  warning: 'WARNING',
  info: 'NOTICE',
}

export function ToastContainer() {
  const { toasts, dismiss } = useToast()

  if (toasts.length === 0) return null

  return (
    <div className="er-toast-stack" role="region" aria-label="系统通知" aria-live="polite">
      {toasts.map((toastItem, index) => (
        <div
          key={toastItem.id}
          className="er-toast"
          data-type={toastItem.type}
          role={toastItem.type === 'error' ? 'alert' : 'status'}
        >
          <span className="er-toast__index" aria-hidden="true">
            {String(index + 1).padStart(2, '0')}
          </span>
          <span className="er-toast__signal" aria-hidden="true">
            {ICONS[toastItem.type]}
          </span>
          <div className="er-toast__copy">
            <span>{TYPE_LABELS[toastItem.type]} / SYSTEM</span>
            <p>{toastItem.message}</p>
          </div>
          <button type="button" onClick={() => dismiss(toastItem.id)} aria-label="关闭通知">
            <span aria-hidden="true">×</span>
          </button>
        </div>
      ))}
    </div>
  )
}
