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
        backgroundColor: 'var(--bg-elevated)',
        borderColor: 'var(--border)',
        color: 'var(--text-secondary)',
        boxShadow: '0 1px 0 var(--border)',
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
        <div className="flex items-center justify-between mb-4">
          <h2 id="shortcut-help-title" className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>键盘快捷键</h2>
          <button
            ref={closeRef}
            onClick={onClose}
            className="text-sm opacity-60 hover:opacity-100 transition-opacity"
            style={{ color: 'var(--text-muted)' }}
          >
            ✕
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <h3 className="text-xs font-medium mb-2" style={{ color: 'var(--text-muted)' }}>全局</h3>
            <div className="space-y-1.5">
              {GLOBAL_SHORTCUTS.map((s, i) => (
                <div key={i} className="flex items-center justify-between py-1">
                  <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>{s.desc}</span>
                  <div className="flex items-center gap-1">
                    {s.keys.map((k, j) => <KeyBadge key={j} k={k} />)}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <h3 className="text-xs font-medium mb-2" style={{ color: 'var(--text-muted)' }}>阅读页</h3>
            <div className="space-y-1.5">
              {READER_SHORTCUTS.map((s, i) => (
                <div key={i} className="flex items-center justify-between py-1">
                  <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>{s.desc}</span>
                  <div className="flex items-center gap-1">
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
