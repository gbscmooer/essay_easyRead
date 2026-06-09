import { useCallback, useState, useRef } from 'react'

export interface ToastOptions {
  type?: 'error' | 'success' | 'info'
  message: string
  duration?: number
}

interface ToastItem extends ToastOptions {
  id: number
}

let _showToast: ((opts: ToastOptions) => void) | null = null

export function toast(opts: ToastOptions) {
  if (_showToast) {
    _showToast(opts)
  } else {
    // Fallback if provider not mounted
    console.error('[toast]', opts.message)
  }
}

export function useToastProvider() {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(0)

  const showToast = useCallback((opts: ToastOptions) => {
    const id = nextId.current++
    setToasts(prev => [...prev, { ...opts, id }])
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, opts.duration ?? 3000)
  }, [])

  // Register globally
  _showToast = showToast

  const ToastContainer = useCallback(() => {
    if (toasts.length === 0) return null
    return (
      <div className="fixed bottom-6 right-6 z-[9999] flex flex-col gap-2">
        {toasts.map(t => (
          <div
            key={t.id}
            className="px-4 py-2.5 rounded-lg shadow-lg text-sm animate-slide-in"
            style={{
              backgroundColor: t.type === 'error' ? 'var(--error)' : t.type === 'success' ? '#22c55e' : 'var(--accent)',
              color: '#fff',
            }}
          >
            {t.message}
          </div>
        ))}
      </div>
    )
  }, [toasts])

  return { ToastContainer }
}
