import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  MOTION_MEDIA_QUERY,
  MOTION_STORAGE_KEY,
  useMotionPreference,
} from '@/hooks/useMotionPreference'

type ChangeListener = (event: MediaQueryListEvent) => void

const storageValues = new Map<string, string>()
const storageMock: Storage = {
  get length() { return storageValues.size },
  clear: () => storageValues.clear(),
  getItem: key => storageValues.get(key) ?? null,
  key: index => Array.from(storageValues.keys())[index] ?? null,
  removeItem: key => { storageValues.delete(key) },
  setItem: (key, value) => { storageValues.set(key, String(value)) },
}

function installMotionMedia(initial: boolean) {
  let matches = initial
  const listeners = new Set<ChangeListener>()
  const media = {
    media: MOTION_MEDIA_QUERY,
    onchange: null,
    get matches() { return matches },
    addEventListener: (_type: string, listener: ChangeListener) => listeners.add(listener),
    removeEventListener: (_type: string, listener: ChangeListener) => listeners.delete(listener),
    addListener: (listener: ChangeListener) => listeners.add(listener),
    removeListener: (listener: ChangeListener) => listeners.delete(listener),
    dispatchEvent: () => true,
  } as MediaQueryList

  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => media),
  })
  return (next: boolean) => {
    matches = next
    listeners.forEach(listener => listener({ matches: next } as MediaQueryListEvent))
  }
}

beforeEach(() => {
  Object.defineProperty(window, 'localStorage', { configurable: true, value: storageMock })
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-er-motion-override')
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useMotionPreference', () => {
  it('无显式选择时跟随系统变化且不写入覆盖值', () => {
    const setReduced = installMotionMedia(true)
    const { result } = renderHook(() => useMotionPreference())

    expect(result.current.enabled).toBe(false)
    expect(result.current.source).toBe('system')
    expect(window.localStorage.getItem(MOTION_STORAGE_KEY)).toBeNull()
    expect(document.documentElement).not.toHaveAttribute('data-er-motion-override')

    act(() => setReduced(false))
    expect(result.current.enabled).toBe(true)
    expect(window.localStorage.getItem(MOTION_STORAGE_KEY)).toBeNull()
  })

  it('用户显式选择优先于后续系统变化', () => {
    const setReduced = installMotionMedia(true)
    window.localStorage.setItem(MOTION_STORAGE_KEY, 'full')
    const { result } = renderHook(() => useMotionPreference())

    expect(result.current.enabled).toBe(true)
    expect(result.current.source).toBe('user')
    expect(document.documentElement).toHaveAttribute('data-er-motion-override', 'full')

    act(() => setReduced(false))
    act(() => setReduced(true))
    expect(result.current.enabled).toBe(true)
  })

  it('仅在用户切换控件后持久化覆盖值', () => {
    installMotionMedia(false)
    const { result } = renderHook(() => useMotionPreference())

    act(() => result.current.toggle())
    expect(result.current.enabled).toBe(false)
    expect(result.current.source).toBe('user')
    expect(window.localStorage.getItem(MOTION_STORAGE_KEY)).toBe('reduced')
  })
})
