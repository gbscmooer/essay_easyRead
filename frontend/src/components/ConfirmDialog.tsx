/**
 * ConfirmDialog 通用确认弹窗
 *
 * 硬边注册遮罩 + 语义确认平面。
 * 支持 danger/warning 变体，含焦点捕获和 Escape 关闭。
 */

import { useEffect, useRef } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  variant?: 'danger' | 'warning'
  onConfirm: () => void
  onCancel: () => void
}

const ICONS = {
  danger: (
    <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
        d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
      />
    </svg>
  ),
  warning: (
    <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
        d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
      />
    </svg>
  ),
}

const ICON_COLORS = {
  danger: 'var(--error)',
  warning: 'var(--warning)',
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
      className="er-dialog-backdrop"
      onMouseDown={onCancel}
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
    >
      <div
        ref={cardRef}
        className="er-dialog-plane"
        data-variant={variant}
        onMouseDown={e => e.stopPropagation()}
      >
        <span className="er-dialog-code">CONFIRM / {variant.toUpperCase()}</span>
        <div className="er-dialog-heading">
          <span className="er-dialog-icon" style={{ color: ICON_COLORS[variant] }}>
            {ICONS[variant]}
          </span>
          <div>
            <p
              id="confirm-dialog-title"
              className="er-dialog-title"
            >
              {title}
            </p>
            <p className="er-dialog-message">
              {message}
            </p>
          </div>
        </div>

        <div className="er-dialog-actions">
          <button
            ref={cancelRef}
            onClick={onCancel}
            className="er-dialog-action"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            className="er-dialog-action er-dialog-action--confirm"
            data-variant={variant}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
