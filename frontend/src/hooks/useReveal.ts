import { useEffect, useRef, useState } from 'react'

interface RevealOptions {
  /** 触发可见所需的相交比例，默认 0.12 */
  threshold?: number
  /** 视口外扩边距，默认底部内缩 10% 让揭示更早发生 */
  rootMargin?: string
  /** 是否只触发一次（默认 true）；false 则离开视口会重置 */
  once?: boolean
}

/**
 * 元素进入视口时返回 visible=true，用于滚动揭示动画。
 * - SSR / 无 IntersectionObserver 环境下直接判定可见，保证内容不被隐藏。
 * - reduced-motion 由 CSS 层兜底降级（.reveal 在该模式下始终可见）。
 */
export function useReveal<T extends HTMLElement = HTMLDivElement>(options: RevealOptions = {}) {
  const { threshold = 0.12, rootMargin = '0px 0px -10% 0px', once = true } = options
  const ref = useRef<T | null>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true)
      return
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setVisible(true)
            if (once) observer.unobserve(entry.target)
          } else if (!once) {
            setVisible(false)
          }
        }
      },
      { threshold, rootMargin },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [threshold, rootMargin, once])

  return { ref, visible }
}
