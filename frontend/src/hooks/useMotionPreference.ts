import { useCallback, useEffect, useState } from 'react'

export const MOTION_STORAGE_KEY = 'essay_motion'
export const MOTION_MEDIA_QUERY = '(prefers-reduced-motion: reduce)'

export type MotionOverride = 'full' | 'reduced'

function readMotionOverride(): MotionOverride | null {
  try {
    const saved = window.localStorage.getItem(MOTION_STORAGE_KEY)
    return saved === 'full' || saved === 'reduced' ? saved : null
  } catch {
    return null
  }
}

function systemPrefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function'
    && window.matchMedia(MOTION_MEDIA_QUERY).matches
}

/** 系统动效偏好为默认值；应用内的明确选择优先。 */
export function useMotionPreference() {
  const [override, setOverride] = useState<MotionOverride | null>(readMotionOverride)
  const [systemReduced, setSystemReduced] = useState(systemPrefersReducedMotion)

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia(MOTION_MEDIA_QUERY)
    const update = (event: MediaQueryListEvent | MediaQueryList) => setSystemReduced(event.matches)

    update(media)
    if (typeof media.addEventListener === 'function') {
      media.addEventListener('change', update)
      return () => media.removeEventListener('change', update)
    }

    if (typeof media.addListener === 'function') {
      media.addListener(update)
      return () => media.removeListener?.(update)
    }
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (override === null) {
      root.removeAttribute('data-er-motion-override')
      return
    }

    root.setAttribute('data-er-motion-override', override)
    try {
      window.localStorage.setItem(MOTION_STORAGE_KEY, override)
    } catch {
      /* 存储不可用时，本次会话内的选择仍然生效。 */
    }
  }, [override])

  const enabled = override ? override === 'full' : !systemReduced
  const toggle = useCallback(() => {
    setOverride(enabled ? 'reduced' : 'full')
  }, [enabled])

  return { enabled, toggle, source: override ? 'user' as const : 'system' as const }
}
