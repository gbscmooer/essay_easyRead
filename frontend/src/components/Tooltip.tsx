import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import '@/styles/overlays.css'

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

/** Delayed, portal-backed tooltip with viewport-aware positioning. */
export function Tooltip({ content, children, delay = 1500, placement = 'bottom', className }: TooltipProps) {
  const [open, setOpen] = useState(false)
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null)
  const tooltipId = useId()
  const triggerRef = useRef<HTMLSpanElement | null>(null)
  const tipRef = useRef<HTMLDivElement | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const exitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearTimers = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    if (exitTimerRef.current) clearTimeout(exitTimerRef.current)
    timerRef.current = null
    exitTimerRef.current = null
  }, [])

  const compute = useCallback(() => {
    if (!triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    setCoords({
      top: placement === 'bottom' ? rect.bottom + 9 : rect.top - 9,
      left: rect.left + rect.width / 2,
    })
  }, [placement])

  const show = useCallback(() => {
    clearTimers()
    timerRef.current = setTimeout(() => {
      compute()
      setOpen(true)
      timerRef.current = null
    }, delay)
  }, [clearTimers, compute, delay])

  const hide = useCallback(() => {
    clearTimers()
    setOpen(false)
    exitTimerRef.current = setTimeout(() => {
      setCoords(null)
      exitTimerRef.current = null
    }, 150)
  }, [clearTimers])

  useEffect(() => clearTimers, [clearTimers])

  useLayoutEffect(() => {
    if (!open || !tipRef.current || !coords) return
    const tipWidth = tipRef.current.offsetWidth
    const tipHeight = tipRef.current.offsetHeight
    const margin = 8
    let left = coords.left - tipWidth / 2
    let top = placement === 'bottom' ? coords.top : coords.top - tipHeight

    left = Math.max(margin, Math.min(left, window.innerWidth - tipWidth - margin))
    top = Math.max(margin, Math.min(top, window.innerHeight - tipHeight - margin))
    tipRef.current.style.left = `${left}px`
    tipRef.current.style.top = `${top}px`
  }, [open, coords, placement])

  return (
    <>
      <span
        ref={triggerRef}
        className={className}
        aria-describedby={open ? tooltipId : undefined}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
      >
        {children}
      </span>
      {coords && createPortal(
        <div
          ref={tipRef}
          id={tooltipId}
          role="tooltip"
          className="er-tooltip"
          data-open={open || undefined}
          data-placement={placement}
          style={{ top: coords.top, left: 0 }}
        >
          <span className="er-tooltip__register" aria-hidden="true" />
          <span>{content}</span>
        </div>,
        document.body,
      )}
    </>
  )
}
