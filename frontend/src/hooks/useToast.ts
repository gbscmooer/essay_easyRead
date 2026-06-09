import { useState, useEffect } from 'react'

export interface Toast {
  id: number
  type: 'success' | 'error' | 'warning' | 'info'
  message: string
  duration?: number
}

type ToastInput = Omit<Toast, 'id'>

const listeners = new Set<(toasts: Toast[]) => void>()
let globalToasts: Toast[] = []
let nextId = 1

function notify() {
  listeners.forEach(fn => fn([...globalToasts]))
}

export function toast(input: ToastInput | string) {
  const t: Toast = typeof input === 'string'
    ? { id: nextId++, type: 'info', message: input }
    : { id: nextId++, ...input }

  globalToasts = [...globalToasts, t]
  notify()

  const duration = t.duration ?? (t.type === 'error' ? 5000 : 3000)
  if (duration > 0) {
    setTimeout(() => dismiss(t.id), duration)
  }

  return t.id
}

export function dismiss(id: number) {
  globalToasts = globalToasts.filter(t => t.id !== id)
  notify()
}

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>(globalToasts)

  useEffect(() => {
    const listener = (next: Toast[]) => setToasts(next)
    listeners.add(listener)
    // Sync any toasts fired between initial render and effect commit
    setToasts([...globalToasts])
    return () => { listeners.delete(listener) }
  }, [])

  return { toasts, dismiss, toast }
}
