import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

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
  { keys: ['1', '—', '6'], desc: '切换分析 Tab' },
]

function KeyBadge({ keyLabel }: { keyLabel: string }) {
  return <kbd className="er-key-badge">{keyLabel}</kbd>
}

export function ShortcutHelp({ open, onClose }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()

    const handleTab = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleTab)
    return () => document.removeEventListener('keydown', handleTab)
  }, [open])

  if (!open) return null

  return (
    <div
      className="er-shortcuts-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="shortcut-help-title"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        className="er-shortcuts-plane"
        onClick={event => event.stopPropagation()}
      >
        <header className="er-shortcuts-header">
          <div>
            <span>INPUT / KEY REGISTER</span>
            <h2 id="shortcut-help-title">键盘快捷键</h2>
          </div>
          <span aria-hidden="true">?</span>
          <button ref={closeRef} className="er-overlay-close" type="button" onClick={onClose} aria-label="关闭快捷键帮助">
            <span aria-hidden="true">×</span>
          </button>
        </header>

        <div className="er-shortcuts-body">
          <section aria-labelledby="global-shortcuts-title">
            <header>
              <span>01 / GLOBAL</span>
              <h3 id="global-shortcuts-title">全局</h3>
            </header>
            <ol>
              {GLOBAL_SHORTCUTS.map((shortcut, index) => (
                <li key={shortcut.desc}>
                  <span aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                  <strong>{shortcut.desc}</strong>
                  <div>
                    {shortcut.keys.map(keyLabel => <KeyBadge key={keyLabel} keyLabel={keyLabel} />)}
                  </div>
                </li>
              ))}
            </ol>
          </section>

          <section aria-labelledby="reader-shortcuts-title">
            <header>
              <span>02 / READER</span>
              <h3 id="reader-shortcuts-title">阅读页</h3>
            </header>
            <ol>
              {READER_SHORTCUTS.map((shortcut, index) => (
                <li key={shortcut.desc}>
                  <span aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                  <strong>{shortcut.desc}</strong>
                  <div>
                    {shortcut.keys.map(keyLabel => <KeyBadge key={keyLabel} keyLabel={keyLabel} />)}
                  </div>
                </li>
              ))}
            </ol>
          </section>
        </div>

        <footer className="er-shortcuts-footer" aria-hidden="true">
          <span>ESC / CLOSE</span>
          <span>ER—KEYMAP / 02</span>
        </footer>
      </div>
    </div>
  )
}
