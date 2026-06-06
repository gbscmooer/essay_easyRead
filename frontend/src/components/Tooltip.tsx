import { useState, useRef, useCallback, useLayoutEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

interface TooltipProps {
  content: ReactNode
  children: ReactNode
  /** 鼠标停留多久后显示，单位 ms，默认 1500 */
  delay?: number
  /** 相对触发元素的位置，默认 'bottom' */
  placement?: 'top' | 'bottom'
  /** 触发元素的容器样式 */
  className?: string
}

/**
 * 通用 hover tooltip。
 * - 默认 1.5s 才显示，符合"用户犹豫时才弹"的体验
 * - 用 Portal 渲染到 body，避免被父容器 overflow:hidden 裁掉
 * - 跟随触发元素的 rect 自动定位
 */
export function Tooltip({ content, children, delay = 1500, placement = 'bottom', className }: TooltipProps) {
  const [open, setOpen] = useState(false)
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null)
  const triggerRef = useRef<HTMLSpanElement | null>(null)
  const tipRef = useRef<HTMLDivElement | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const compute = useCallback(() => {
    if (!triggerRef.current) return
    const r = triggerRef.current.getBoundingClientRect()
    // 临时定位，等 layout 后再校正水平居中
    setCoords({
      top: placement === 'bottom' ? r.bottom + 8 : r.top - 8,
      left: r.left + r.width / 2,
    })
  }, [placement])

  const show = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      compute()
      setOpen(true)
    }, delay)
  }, [delay, compute])

  const hide = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setOpen(false)
    setCoords(null)
  }, [])

  // 显示后校正水平位置（居中对齐 + 视口边界）
  useLayoutEffect(() => {
    if (!open || !tipRef.current || !coords) return
    const tipW = tipRef.current.offsetWidth
    const tipH = tipRef.current.offsetHeight
    const margin = 8
    let left = coords.left - tipW / 2
    let top = placement === 'bottom' ? coords.top : coords.top - tipH
    left = Math.max(margin, Math.min(left, window.innerWidth - tipW - margin))
    if (top < margin) top = margin
    if (top + tipH > window.innerHeight - margin) top = window.innerHeight - tipH - margin
    tipRef.current.style.left = `${left}px`
    tipRef.current.style.top = `${top}px`
  }, [open, coords, placement])

  return (
    <>
      <span
        ref={triggerRef}
        className={className}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
      >
        {children}
      </span>
      {open && coords && createPortal(
        <div
          ref={tipRef}
          role="tooltip"
          className="fixed z-[9999] pointer-events-none rounded-lg px-3 py-2 text-xs shadow-lg border max-w-xs leading-relaxed"
          style={{
            top: coords.top,
            left: 0,             // useLayoutEffect 会重写
            backgroundColor: 'var(--bg-elevated)',
            color: 'var(--text-primary)',
            borderColor: 'var(--border)',
          }}
        >
          {content}
        </div>,
        document.body
      )}
    </>
  )
}
