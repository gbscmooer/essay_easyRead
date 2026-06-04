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
  const popupRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()


  const hidePopup = useCallback(() => {
    setVisible(false)
    setResult(null)
    setLoading(false)
    setAction(null)
    setSelectedText('')
    setConfigError(null)
  }, [])


  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const handleMouseUp = () => {
      // 延迟检查，确保 selection 已更新
      setTimeout(() => {
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
      container.removeEventListener('mouseup', handleMouseUp)
      document.removeEventListener('mousedown', handleMouseDown)
    }
  }, [containerRef, hidePopup])


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
      <div className="bg-gray-800 border border-gray-600 rounded-lg shadow-2xl overflow-hidden">
        {/* 操作按钮行 */}
        {!result && !loading && (
          <div className="flex items-center gap-1 p-1.5">
            <button
              onClick={() => handleAction('translate')}
              className="px-3 py-1.5 text-xs font-medium text-gray-200 bg-gray-700 hover:bg-blue-600 rounded transition-colors"
            >
              翻译
            </button>
            <button
              onClick={() => handleAction('summarize')}
              className="px-3 py-1.5 text-xs font-medium text-gray-200 bg-gray-700 hover:bg-blue-600 rounded transition-colors"
            >
              AI 总结
            </button>
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
                {action === 'translate' ? '翻译结果' : 'AI 总结'}
              </span>
              <button
                onClick={hidePopup}
                className="text-gray-500 hover:text-gray-300 text-xs"
              >
                &times;
              </button>
            </div>
            <div className="px-3 py-2 max-h-48 overflow-auto">
              <p className="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap">
                {result}
              </p>
            </div>
            {/* 操作按钮 */}
            <div className="flex items-center gap-1 px-3 py-1.5 border-t border-gray-700">
              <button
                onClick={() => handleAction('translate')}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  action === 'translate'
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-700'
                }`}
              >
                翻译
              </button>
              <button
                onClick={() => handleAction('summarize')}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  action === 'summarize'
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-700'
                }`}
              >
                总结
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 小三角箭头 */}
      <div className="flex justify-center">
        <div className="w-2 h-2 bg-gray-800 border-b border-r border-gray-600 transform rotate-45 -mt-1" />
      </div>
    </div>
  )
}
