import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { textAction } from '@/api/textAction'
import type { AnnotationType } from '@/types'


interface SelectionPopupProps {
  containerRef: React.RefObject<HTMLElement | null>
  onAnnotate?: (data: {
    pageNumber: number
    x: number
    y: number
    width: number
    height: number
    annotationType: AnnotationType
    color: string
    text: string
  }) => void
}


function mergeLineRects(rects: DOMRect[]): DOMRect[] {
  const valid = rects.filter(r => r.width > 1 && r.height > 1)
  if (valid.length === 0) return []
  valid.sort((a, b) => a.top - b.top)
  const merged: DOMRect[] = []
  for (const r of valid) {
    const last = merged[merged.length - 1]
    if (last && Math.abs(r.top - last.top) < last.height * 0.6) {
      const left = Math.min(last.left, r.left)
      const top = Math.min(last.top, r.top)
      const right = Math.max(last.right, r.right)
      const bottom = Math.max(last.bottom, r.bottom)
      merged[merged.length - 1] = new DOMRect(left, top, right - left, bottom - top)
    } else {
      merged.push(new DOMRect(r.left, r.top, r.width, r.height))
    }
  }
  return merged
}


const HIGHLIGHT_COLORS = [
  { name: '黄', value: '#fde047' },
  { name: '绿', value: '#86efac' },
  { name: '蓝', value: '#93c5fd' },
  { name: '粉', value: '#f9a8d4' },
  { name: '橙', value: '#fdba74' },
]


export function SelectionPopup({ containerRef, onAnnotate }: SelectionPopupProps) {
  const [visible, setVisible] = useState(false)
  const [position, setPosition] = useState({ x: 0, y: 0 })
  const [selectedText, setSelectedText] = useState('')
  const [result, setResult] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [action, setAction] = useState<'translate' | 'summarize' | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [showColorPicker, setShowColorPicker] = useState(false)
  const [pendingAnnotationType, setPendingAnnotationType] = useState<AnnotationType | null>(null)
  const [flipY, setFlipY] = useState(false)
  const popupRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const dragging = useRef(false)
  const dragOffset = useRef({ x: 0, y: 0 })
  const timeoutRef = useRef<number | null>(null)
  const selectionRectRef = useRef<DOMRect | null>(null)
  const selectionLineRectsRef = useRef<DOMRect[]>([])
  const pageElementRef = useRef<HTMLElement | null>(null)


  const hidePopup = useCallback(() => {
    setVisible(false)
    setResult(null)
    setLoading(false)
    setAction(null)
    setSelectedText('')
    setConfigError(null)
    setCopied(false)
    setShowColorPicker(false)
    setPendingAnnotationType(null)
    selectionRectRef.current = null
    selectionLineRectsRef.current = []
    pageElementRef.current = null
  }, [])


  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const handleMouseUp = () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      timeoutRef.current = window.setTimeout(() => {
        const selection = window.getSelection()
        const text = selection?.toString().trim() || ''

        if (text.length < 3) return

        if (!selection || selection.rangeCount === 0) return
        const range = selection.getRangeAt(0)
        if (!container.contains(range.commonAncestorContainer)) return

        const rect = range.getBoundingClientRect()
        const containerRect = container.getBoundingClientRect()

        // 查找包含选区的页面元素
        let pageEl: HTMLElement | null = null
        let node: Node | null = range.commonAncestorContainer
        while (node && node !== container) {
          if (node instanceof HTMLElement && node.dataset.page) {
            pageEl = node
            break
          }
          node = node.parentNode
        }

        selectionRectRef.current = rect
        selectionLineRectsRef.current = mergeLineRects(Array.from(range.getClientRects()))
        pageElementRef.current = pageEl

        const posY = rect.top - containerRect.top - 8
        setFlipY(posY < 60)
        setPosition({
          x: rect.left + rect.width / 2 - containerRect.left,
          y: posY,
        })
        setSelectedText(text)
        setResult(null)
        setLoading(false)
        setAction(null)
        setShowColorPicker(false)
        setPendingAnnotationType(null)
        setVisible(true)
      }, 10)
    }

    const handleMouseDown = (e: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        hidePopup()
      }
    }

    // Mobile: selectionchange fires during long-press text selection
    let selectionTimer: number | null = null
    const handleSelectionChange = () => {
      if (selectionTimer) clearTimeout(selectionTimer)
      selectionTimer = window.setTimeout(() => {
        const sel = document.getSelection()
        if (!sel || sel.isCollapsed || !sel.rangeCount) return
        const range = sel.getRangeAt(0)
        const text = sel.toString().trim()
        if (text.length < 3) return
        // Check if selection is within the container
        if (!container.contains(range.commonAncestorContainer)) return
        const containerRect = container.getBoundingClientRect()
        const rect = range.getBoundingClientRect()
        if (rect.width === 0) return
        const posX = rect.left + rect.width / 2 - containerRect.left
        const posY = rect.top - containerRect.top - 8
        setFlipY(posY < 60)
        setPosition({ x: posX, y: posY })
        setSelectedText(text)
        setResult(null)
        setLoading(false)
        setAction(null)
        setShowColorPicker(false)
        setPendingAnnotationType(null)
        setVisible(true)
      }, 300)
    }

    container.addEventListener('mouseup', handleMouseUp)
    document.addEventListener('mousedown', handleMouseDown)
    document.addEventListener('selectionchange', handleSelectionChange)

    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      if (selectionTimer) clearTimeout(selectionTimer)
      container.removeEventListener('mouseup', handleMouseUp)
      document.removeEventListener('mousedown', handleMouseDown)
      document.removeEventListener('selectionchange', handleSelectionChange)
    }
  }, [containerRef, hidePopup])


  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return
      const container = containerRef.current
      if (!container) return
      const containerRect = container.getBoundingClientRect()
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


  const doAnnotate = useCallback((type: AnnotationType, color: string) => {
    const pageEl = pageElementRef.current
    if (!pageEl || !onAnnotate) return

    const lineRects = selectionLineRectsRef.current.length > 0
      ? selectionLineRectsRef.current
      : selectionRectRef.current ? [selectionRectRef.current] : []

    if (lineRects.length === 0) return

    const pageRect = pageEl.getBoundingClientRect()
    const pageNumber = parseInt(pageEl.dataset.page || '1', 10)

    for (const selRect of lineRects) {
      const x = Math.max(0, (selRect.left - pageRect.left) / pageRect.width)
      const y = Math.max(0, (selRect.top - pageRect.top) / pageRect.height)
      const width = Math.min(1, selRect.width / pageRect.width)
      const height = Math.min(1, selRect.height / pageRect.height)
      if (width < 0.001 || height < 0.001) continue
      onAnnotate({
        pageNumber,
        x, y, width, height,
        annotationType: type,
        color,
        text: selectedText,
      })
    }

    hidePopup()
    window.getSelection()?.removeAllRanges()
  }, [onAnnotate, selectedText, hidePopup])


  const handleMarkupClick = useCallback((type: AnnotationType) => {
    setPendingAnnotationType(type)
    if (type === 'highlight') {
      setShowColorPicker(true)
    } else {
      doAnnotate(type, '#ef4444')
    }
  }, [doAnnotate])


  const handleColorSelect = useCallback((color: string) => {
    if (pendingAnnotationType) {
      doAnnotate(pendingAnnotationType, color)
    }
    setShowColorPicker(false)
  }, [pendingAnnotationType, doAnnotate])


  if (!visible || selectedText.length < 3) return null


  return (
    <div
      ref={popupRef}
      className="absolute z-50 pointer-events-auto"
      style={{
        left: `${position.x}px`,
        top: `${position.y}px`,
        transform: flipY ? 'translate(-50%, 0)' : 'translate(-50%, -100%)',
      }}
    >
      <div
        className="bg-gray-800 border border-gray-600 rounded-lg shadow-2xl overflow-hidden cursor-move select-none hover:border-gray-500 transition-colors"
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
        {!result && !loading && !showColorPicker && (
          <div className="flex items-center gap-1 p-1.5">
            <button
              onMouseDown={e => e.stopPropagation()}
              onClick={() => handleAction('translate')}
              className="px-3 py-1.5 text-xs font-medium bg-gray-700 hover:bg-blue-600 rounded transition-colors"
              style={{ color: 'var(--text-primary)' }}
            >
              翻译
            </button>
            <button
              onMouseDown={e => e.stopPropagation()}
              onClick={() => handleAction('summarize')}
              className="px-3 py-1.5 text-xs font-medium bg-gray-700 hover:bg-blue-600 rounded transition-colors"
              style={{ color: 'var(--text-primary)' }}
            >
              总结
            </button>
            <div className="w-px h-5 bg-gray-600 mx-0.5" />
            <button
              onMouseDown={e => e.stopPropagation()}
              onClick={() => handleMarkupClick('highlight')}
              className="px-2.5 py-1.5 text-xs font-medium bg-gray-700 hover:bg-yellow-600 rounded transition-colors"
              style={{ color: 'var(--text-primary)' }}
              title="荧光笔"
            >
              <svg className="w-3.5 h-3.5 inline-block mr-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
              </svg>
            </button>
            <button
              onMouseDown={e => e.stopPropagation()}
              onClick={() => handleMarkupClick('underline')}
              className="px-2.5 py-1.5 text-xs font-medium bg-gray-700 hover:bg-blue-600 rounded transition-colors"
              style={{ color: 'var(--text-primary)' }}
              title="下划线"
            >
              <svg className="w-3.5 h-3.5 inline-block mr-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 4v7a5 5 0 0010 0V4M5 20h14" />
              </svg>
              划线
            </button>
            <button
              onMouseDown={e => e.stopPropagation()}
              onClick={() => handleMarkupClick('strikethrough')}
              className="px-2.5 py-1.5 text-xs font-medium bg-gray-700 hover:bg-red-600 rounded transition-colors"
              style={{ color: 'var(--text-primary)' }}
              title="删除线"
            >
              <svg className="w-3.5 h-3.5 inline-block mr-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 12h16M8 6h8a2 2 0 012 2v0a2 2 0 01-2 2H8M8 14h8a2 2 0 012 2v0a2 2 0 01-2 2H8" />
              </svg>
              删除
            </button>
          </div>
        )}

        {/* 颜色选择器 */}
        {showColorPicker && (
          <div className="p-2">
            <p className="text-xs text-gray-400 mb-1.5">选择颜色</p>
            <div className="flex items-center gap-1.5">
              {HIGHLIGHT_COLORS.map(c => (
                <button
                  key={c.value}
                  onMouseDown={e => e.stopPropagation()}
                  onClick={() => handleColorSelect(c.value)}
                  className="w-6 h-6 rounded-full border-2 border-gray-600 hover:border-white transition-colors"
                  style={{ backgroundColor: c.value }}
                  title={c.name}
                />
              ))}
              <button
                onMouseDown={e => e.stopPropagation()}
                onClick={() => setShowColorPicker(false)}
                className="ml-1 text-xs text-gray-400 hover:text-gray-200"
              >
                取消
              </button>
            </div>
          </div>
        )}

        {/* 加载状态 */}
        {loading && (
          <div className="flex items-center gap-2 px-4 py-3">
            <div className="w-4 h-4 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
            <span className="text-xs text-gray-400">
              {action === 'translate' ? '正在翻译...' : '正在总结...'}
            </span>
          </div>
        )}

        {/* 配置错误 */}
        {configError && (
          <div className="px-3 py-2 max-w-xs">
            <p className="text-xs text-red-400 mb-2">{configError}</p>
            <button
              onMouseDown={e => e.stopPropagation()}
              onClick={() => {
                hidePopup()
                navigate('/settings')
              }}
              className="text-xs text-blue-400 hover:text-blue-300 underline"
            >
              前往配置页面 →
            </button>
          </div>
        )}

        {/* 结果展示 */}
        {result && (
          <div className="max-w-sm">
            <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-700">
              <span className="text-xs text-gray-400">
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
                  className="text-gray-400 hover:text-gray-200 text-xs transition-colors"
                >
                  {copied ? '已复制' : '复制'}
                </button>
                <button
                  onMouseDown={e => e.stopPropagation()}
                  onClick={hidePopup}
                  className="text-gray-500 hover:text-gray-300 text-xs"
                >
                  &times;
                </button>
              </div>
            </div>
            <div className="px-3 py-2 max-h-48 overflow-auto" onMouseDown={e => e.stopPropagation()}>
              <p className="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap">
                {result}
              </p>
            </div>
            <div className="flex items-center gap-1 px-3 py-1.5 border-t border-gray-700">
              <button
                onMouseDown={e => e.stopPropagation()}
                onClick={() => handleAction('translate')}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  action === 'translate'
                    ? 'bg-blue-600'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-700'
                }`}
                style={action === 'translate' ? { color: 'var(--bg-base)' } : undefined}
              >
                翻译
              </button>
              <button
                onMouseDown={e => e.stopPropagation()}
                onClick={() => handleAction('summarize')}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  action === 'summarize'
                    ? 'bg-blue-600'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-700'
                }`}
                style={action === 'summarize' ? { color: 'var(--bg-base)' } : undefined}
              >
                总结
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 小三角箭头 */}
      <div className={`flex ${flipY ? 'justify-center -mb-1' : 'justify-center'}`}>
        <div
          className="w-2 h-2 transform rotate-45"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            borderRight: '1px solid var(--border)',
            borderBottom: flipY ? 'none' : '1px solid var(--border)',
            borderTop: flipY ? '1px solid var(--border)' : 'none',
            borderLeft: flipY ? '1px solid var(--border)' : 'none',
            marginTop: flipY ? 0 : '-4px',
            marginBottom: flipY ? 0 : undefined,
          }}
        />
      </div>
    </div>
  )
}
