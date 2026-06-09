/**
 * ConfirmDialog 通用确认弹窗
 *
 * 可复用的确认对话框组件，支持 danger/warning 两种变体，
 * 包含焦点捕获、Escape 关闭、点击背景关闭等无障碍功能。
 */

import { useEffect, useRef } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  message: string
  confirmLabel?: string  // default "确认"
  cancelLabel?: string   // default "取消"
  variant?: 'danger' | 'warning'  // default 'danger'
  onConfirm: () => void
  onCancel: () => void
}

const ICONS = {
  danger: (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
      />
    </svg>
  ),
  warning: (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
      />
    </svg>
  ),
}

const ICON_COLORS = {
  danger: 'var(--error)',
  warning: 'text-amber-400',
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = '确认',
  cancelLabel = '取消',
  variant = 'danger',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  const cardRef = useRef<HTMLDivElement>(null)

  // Focus trap: focus cancel button on open, Tab cycles within dialog
  useEffect(() => {
    if (!open) return
    cancelRef.current?.focus()

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCancel()
        return
      }
      if (e.key === 'Tab' && cardRef.current) {
        const focusable = cardRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        )
        if (focusable.length === 0) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault()
            last.focus()
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault()
            first.focus()
          }
        }
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onCancel])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.45)' }}
      onMouseDown={onCancel}
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
    >
      <div
        ref={cardRef}
        className="w-80 rounded-xl border p-5 shadow-2xl"
        style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)' }}
        onMouseDown={e => e.stopPropagation()}
      >
        <div className="flex items-start gap-3 mb-4">
          <span className={`mt-0.5 shrink-0 ${variant === 'warning' ? ICON_COLORS.warning : ''}`}
            style={variant === 'danger' ? { color: ICON_COLORS.danger } : undefined}
          >
            {ICONS[variant]}
          </span>
          <div>
            <p id="confirm-dialog-title" className="text-sm font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
              {title}
            </p>
            <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              {message}
            </p>
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <button
            ref={cancelRef}
            onClick={onCancel}
            className="px-3 py-1.5 text-xs rounded-md border transition-colors hover:opacity-80"
            style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            className="px-3 py-1.5 text-xs rounded-md transition-colors"
            style={
              variant === 'danger'
                ? { backgroundColor: 'var(--error)', color: '#fff' }
                : { backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }
            }
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
