import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { textAction } from '@/api/textAction'


interface SelectionPopupProps {
  containerRef: React.RefObject<HTMLElement | null>
}


export function SelectionPopup({ containerRef }: SelectionPopupProps) {
  const [visible, setVisible] = useState(false)
  const [position, setPosition] = useState({ x: 0, y: 0 })
  const [selectedText, setSelectedText] = useState('')
  const [result, setResult] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [action, setAction] = useState<'translate' | 'summarize' | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const popupRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const dragging = useRef(false)
  const dragOffset = useRef({ x: 0, y: 0 })
  const timeoutRef = useRef<number | null>(null)


  const hidePopup = useCallback(() => {
    setVisible(false)
    setResult(null)
    setLoading(false)
    setAction(null)
    setSelectedText('')
    setConfigError(null)
    setCopied(false)
  }, [])


  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const handleMouseUp = () => {
      // 延迟检查，确保 selection 已更新
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      timeoutRef.current = window.setTimeout(() => {
        const selection = window.getSelection()
        const text = selection?.toString().trim() || ''

        if (text.length < 3) {
          // 选中文本太短，不显示
          return
        }

        // 检查选区是否在容器内
        if (!selection || selection.rangeCount === 0) return
        const range = selection.getRangeAt(0)
        if (!container.contains(range.commonAncestorContainer)) return

        // 计算弹窗位置
        const rect = range.getBoundingClientRect()
        const containerRect = container.getBoundingClientRect()

        setPosition({
          x: rect.left + rect.width / 2 - containerRect.left,
          y: rect.top - containerRect.top - 8,
        })
        setSelectedText(text)
        setResult(null)
        setLoading(false)
        setAction(null)
        setVisible(true)
      }, 10)
    }

    const handleMouseDown = (e: MouseEvent) => {
      // 点击弹窗外部时关闭
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        hidePopup()
      }
    }

    container.addEventListener('mouseup', handleMouseUp)
    document.addEventListener('mousedown', handleMouseDown)

    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      container.removeEventListener('mouseup', handleMouseUp)
      document.removeEventListener('mousedown', handleMouseDown)
    }
  }, [containerRef, hidePopup])


  // 拖拽
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return
      const container = containerRef.current
      if (!container) return
      const containerRect = container.getBoundingClientRect()
      // dragOffset 记录的是鼠标相对于弹窗左上角的偏移
      // transform: translate(-50%, -100%) 让 left/top 指向弹窗的中心底部
      // 所以需要补偿这个偏移
      const popup = popupRef.current
      const popupW = popup?.offsetWidth || 0
      const popupH = popup?.offsetHeight || 0
      setPosition({
        x: e.clientX - containerRect.left - dragOffset.current.x + popupW / 2,
        y: e.clientY - containerRect.top - dragOffset.current.y + popupH,
      })
    }
    const handleMouseUp = () => {
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [containerRef])


  const handleAction = useCallback(async (act: 'translate' | 'summarize') => {
    setAction(act)
    setLoading(true)
    setResult(null)
    setConfigError(null)

    try {
      const res = await textAction(selectedText, act)
      setResult(res)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      if (msg.includes('API Key') || msg.includes('配置')) {
        setConfigError(msg)
      } else {
        setResult(`处理失败: ${msg}`)
      }
    } finally {
      setLoading(false)
    }
  }, [selectedText])


  if (!visible || selectedText.length < 3) return null


  return (
    <div
      ref={popupRef}
      className="absolute z-50 pointer-events-auto"
      style={{
        left: `${position.x}px`,
        top: `${position.y}px`,
        transform: 'translate(-50%, -100%)',
      }}
    >
      <div
        className="rounded-lg shadow-2xl overflow-hidden cursor-move select-none transition-colors"
        style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-light)' }}
        onMouseDown={(e) => {
          e.preventDefault()
          const popup = popupRef.current
          if (!popup) return
          const rect = popup.getBoundingClientRect()
          dragging.current = true
          dragOffset.current = {
            x: e.clientX - rect.left,
            y: e.clientY - rect.top,
          }
          document.body.style.cursor = 'move'
          document.body.style.userSelect = 'none'
        }}
      >
        {/* 操作按钮行 */}
        {!result && !loading && (
          <div className="flex items-center gap-1 p-1.5">
            <button
              onMouseDown={e => e.stopPropagation()}
              onClick={() => handleAction('translate')}
              className="px-3 py-1.5 text-xs font-medium rounded transition-colors hover:opacity-90"
              style={{ color: 'var(--text-primary)', backgroundColor: 'var(--border)' }}
            >
              翻译
            </button>
            <button
              onMouseDown={e => e.stopPropagation()}
              onClick={() => handleAction('summarize')}
              className="px-3 py-1.5 text-xs font-medium rounded transition-colors hover:opacity-90"
              style={{ color: 'var(--text-primary)', backgroundColor: 'var(--border)' }}
            >
              总结
            </button>
          </div>
        )}

        {/* 加载状态 */}
        {loading && (
          <div className="flex items-center gap-2 px-4 py-3">
            <div
              className="w-4 h-4 border-2 rounded-full animate-spin"
              style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }}
            />
            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              {action === 'translate' ? '正在翻译...' : '正在总结...'}
            </span>
          </div>
        )}

        {/* 配置错误 */}
        {configError && (
          <div className="px-3 py-2 max-w-xs">
            <p className="text-xs mb-2" style={{ color: 'var(--error)' }}>{configError}</p>
            <button
              onMouseDown={e => e.stopPropagation()}
              onClick={() => {
                hidePopup()
                navigate('/settings')
              }}
              className="text-xs underline"
              style={{ color: 'var(--accent)' }}
            >
              前往配置页面 →
            </button>
          </div>
        )}

        {/* 结果展示 */}
        {result && (
          <div className="max-w-sm">
            <div className="flex items-center justify-between px-3 py-1.5 border-b" style={{ borderColor: 'var(--border-light)' }}>
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                {action === 'translate' ? '翻译结果' : '总结'}
              </span>
              <div className="flex items-center gap-2">
                <button
                  onMouseDown={e => e.stopPropagation()}
                  onClick={() => {
                    navigator.clipboard.writeText(result).then(() => {
                      setCopied(true)
                      setTimeout(() => setCopied(false), 1500)
                    })
                  }}
                  className="text-xs transition-colors"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  {copied ? '已复制' : '复制'}
                </button>
                <button
                  onMouseDown={e => e.stopPropagation()}
                  onClick={hidePopup}
                  className="text-xs"
                  style={{ color: 'var(--text-muted)' }}
                >
                  &times;
                </button>
              </div>
            </div>
            <div className="px-3 py-2 max-h-48 overflow-auto" onMouseDown={e => e.stopPropagation()}>
              <p className="text-sm leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--text-primary)' }}>
                {result}
              </p>
            </div>
            {/* 操作按钮 */}
            <div className="flex items-center gap-1 px-3 py-1.5 border-t" style={{ borderColor: 'var(--border-light)' }}>
              <button
                onMouseDown={e => e.stopPropagation()}
                onClick={() => handleAction('translate')}
                className="px-2 py-1 text-xs rounded transition-colors"
                style={
                  action === 'translate'
                    ? { backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }
                    : { color: 'var(--text-secondary)' }
                }
              >
                翻译
              </button>
              <button
                onMouseDown={e => e.stopPropagation()}
                onClick={() => handleAction('summarize')}
                className="px-2 py-1 text-xs rounded transition-colors"
                style={
                  action === 'summarize'
                    ? { backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }
                    : { color: 'var(--text-secondary)' }
                }
              >
                总结
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 小三角箭头 */}
      <div className="flex justify-center">
        <div
          className="w-2 h-2 transform rotate-45 -mt-1"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            borderBottom: '1px solid var(--border-light)',
            borderRight: '1px solid var(--border-light)',
          }}
        />
      </div>
    </div>
  )
}
