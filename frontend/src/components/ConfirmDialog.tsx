/**
 * ConfirmDialog 通用确认弹窗
 *
 * 磨砂玻璃遮罩 + 表面提升卡片 + 药丸/幽灵按钮组合。
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
      className="fixed inset-0 z-[200] flex items-center justify-center animate-reveal-fade"
      style={{
        backgroundColor: 'rgba(0,0,0,0.4)',
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
      }}
      onMouseDown={onCancel}
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
    >
      <div
        ref={cardRef}
        className="w-[340px] p-6 shadow-2xl animate-reveal-scale"
        style={{
          backgroundColor: 'var(--bg-elevated)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--seed-radius, 12px)',
          boxShadow: '0 24px 48px rgba(0,0,0,0.16), 0 8px 16px rgba(0,0,0,0.08)',
        }}
        onMouseDown={e => e.stopPropagation()}
      >
        {/* Header: icon + text */}
        <div className="flex items-start gap-3.5 mb-5">
          <span
            className="shrink-0 mt-0.5 flex items-center justify-center w-9 h-9 rounded-full"
            style={{
              color: ICON_COLORS[variant],
              backgroundColor: variant === 'danger' ? 'var(--error-subtle)' : 'var(--warning-subtle)',
            }}
          >
            {ICONS[variant]}
          </span>
          <div className="flex-1 min-w-0">
            <p
              id="confirm-dialog-title"
              className="text-[14px] font-medium mb-1.5"
              style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}
            >
              {title}
            </p>
            <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              {message}
            </p>
          </div>
        </div>

        {/* Actions: ghost cancel + pill confirm */}
        <div className="flex justify-end gap-2.5">
          <button
            ref={cancelRef}
            onClick={onCancel}
            className="press focus-glow px-4 py-2 text-[13px] rounded-full transition-all duration-200"
            style={{
              color: 'var(--text-secondary)',
              backgroundColor: 'transparent',
              border: '1px solid var(--border)',
              cursor: 'pointer',
              transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.backgroundColor = 'var(--bg-surface)'
              e.currentTarget.style.borderColor = 'var(--border-light)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.backgroundColor = 'transparent'
              e.currentTarget.style.borderColor = 'var(--border)'
              e.currentTarget.style.color = 'var(--text-secondary)'
            }}
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            className="press focus-glow px-4 py-2 text-[13px] rounded-full font-medium transition-all duration-200"
            style={{
              backgroundColor: variant === 'danger' ? 'var(--error)' : 'var(--accent)',
              color: '#fff',
              border: 'none',
              cursor: 'pointer',
              boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
              transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.filter = 'brightness(1.08)'
              e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.16)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.filter = 'none'
              e.currentTarget.style.boxShadow = '0 1px 3px rgba(0,0,0,0.12)'
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
