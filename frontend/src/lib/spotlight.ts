/**
 * 全局聚光效果 —— 鼠标在 .spotlight 元素内移动时写入 --mx/--my，
 * 驱动 CSS 光晕跟随光标（Linear / Vercel 风格的卡片高光）。
 * 单一委托监听，仅在指针位于 .spotlight 上时更新，开销可忽略。
 */
export function initSpotlight() {
  if (typeof window === 'undefined') return

  window.addEventListener(
    'pointermove',
    (e) => {
      const el = (e.target as HTMLElement | null)?.closest?.('.spotlight') as HTMLElement | null
      if (!el) return
      const x = e.clientX
      const y = e.clientY
      requestAnimationFrame(() => {
        const rect = el.getBoundingClientRect()
        el.style.setProperty('--mx', `${x - rect.left}px`)
        el.style.setProperty('--my', `${y - rect.top}px`)
      })
    },
    { passive: true },
  )
}
