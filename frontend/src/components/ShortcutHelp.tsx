import { useEffect, useRef } from 'react'

interface Props {
  open: boolean
  onClose: () => void
}

const GLOBAL_SHORTCUTS = [
  { keys: ['⌘', 'N'], desc: '上传新论文' },
  { keys: ['/'], desc: '聚焦搜索' },
  { keys: ['?'], desc: '显示快捷键帮助' },
  { keys: ['Esc'], desc: '关闭弹窗 / 退出专注模式' },
]

const READER_SHORTCUTS = [
  { keys: ['1', '-', '6'], desc: '切换分析 Tab' },
]

function KeyBadge({ k }: { k: string }) {
  return (
    <kbd
      className="inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 text-[11px] font-mono rounded border"
      style={{
        backgroundColor: 'var(--bg-surface)',
        borderColor: 'var(--border)',
        color: 'var(--text-secondary)',
        boxShadow: '0 1.5px 0 var(--border)',
      }}
    >
      {k}
    </kbd>
  )
}

export function ShortcutHelp({ open, onClose }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  // Focus close button on open + trap Tab
  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()

    const handleTab = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !dialogRef.current) return
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey) {
        if (document.activeElement === first) { e.preventDefault(); last.focus() }
      } else {
        if (document.activeElement === last) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', handleTab)
    return () => document.removeEventListener('keydown', handleTab)
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[9998] flex items-center justify-center"
      onClick={onClose}
    >
      <div className="absolute inset-0 animate-reveal-fade" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }} />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcut-help-title"
        className="relative rounded-xl shadow-2xl border p-6 w-[400px] max-h-[80vh] overflow-auto animate-reveal-scale"
        style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 id="shortcut-help-title" className="text-base font-semibold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>键盘快捷键</h2>
          <button
            ref={closeRef}
            onClick={onClose}
            aria-label="关闭"
            className="press focus-glow flex items-center justify-center w-7 h-7 rounded-md transition-all duration-200 opacity-60 hover:opacity-100"
            style={{ color: 'var(--text-muted)', backgroundColor: 'transparent', border: 'none', cursor: 'pointer' }}
            onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)'; e.currentTarget.style.color = 'var(--text-primary)' }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--text-muted)' }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="space-y-5">
          <div>
            <h3 className="text-[11px] font-semibold uppercase mb-2" style={{ color: 'var(--text-muted)', letterSpacing: '0.06em' }}>全局</h3>
            <div className="space-y-0.5">
              {GLOBAL_SHORTCUTS.map((s, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between gap-3 py-1.5 px-2 -mx-2 rounded-md transition-colors"
                  onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }}
                  onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent' }}
                >
                  <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>{s.desc}</span>
                  <div className="flex items-center gap-1 shrink-0">
                    {s.keys.map((k, j) => <KeyBadge key={j} k={k} />)}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <h3 className="text-[11px] font-semibold uppercase mb-2" style={{ color: 'var(--text-muted)', letterSpacing: '0.06em' }}>阅读页</h3>
            <div className="space-y-0.5">
              {READER_SHORTCUTS.map((s, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between gap-3 py-1.5 px-2 -mx-2 rounded-md transition-colors"
                  onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }}
                  onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent' }}
                >
                  <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>{s.desc}</span>
                  <div className="flex items-center gap-1 shrink-0">
                    {s.keys.map((k, j) => <KeyBadge key={j} k={k} />)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
