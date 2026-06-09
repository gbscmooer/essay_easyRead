import { useEffect, useCallback, useRef } from 'react'

let helpCallback: (() => void) | null = null

export function setHelpCallback(fn: (() => void) | null) {
  helpCallback = fn
}

export function useGlobalShortcuts(navigate: (path: string) => void) {
  const navRef = useRef(navigate)
  navRef.current = navigate

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    // Skip if user is typing in an input/textarea
    const target = e.target as HTMLElement
    const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable

    // ? for help — only when not in input
    if (e.key === '?' && !isInput) {
      e.preventDefault()
      helpCallback?.()
      return
    }

    // / to focus search — only when not in input
    if (e.key === '/' && !isInput) {
      e.preventDefault()
      const searchInput = document.querySelector<HTMLInputElement>('[data-shortcut-search]')
      searchInput?.focus()
      return
    }

    // mod+N to upload
    if (e.key === 'n' && (e.metaKey || e.ctrlKey) && !e.shiftKey) {
      e.preventDefault()
      navRef.current('/')
      // Focus the upload zone after navigation
      setTimeout(() => {
        const uploadInput = document.querySelector<HTMLInputElement>('[data-shortcut-upload]')
        uploadInput?.click()
      }, 100)
      return
    }
  }, [])

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])
}
