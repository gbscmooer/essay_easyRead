import { useState, useEffect } from 'react'
import { get } from '@/api/client'
import { toast } from '@/hooks/useToast'
import type { Theme } from '@/theme/themes'
import { DEFAULT_THEME_ID } from '@/theme/themes'


export interface UIConfig {
  highlight_duration: number
  zoom_step: number
  zoom_min: number
  zoom_max: number
  pinch_zoom_step: number
  theme: string
  font: string
  custom_font: string
  custom_themes?: Theme[]
}

interface AppConfig {
  translate: { temperature: number; max_tokens: number; [key: string]: unknown }
  analysis: { temperature: number; max_tokens: number; [key: string]: unknown }
  ui: UIConfig
}


const DEFAULT_UI: UIConfig = {
  highlight_duration: 3000,
  zoom_step: 0.2,
  zoom_min: 0.4,
  zoom_max: 3.0,
  pinch_zoom_step: 0.02,
  theme: DEFAULT_THEME_ID,
  font: 'sans',
  custom_font: '',
  custom_themes: [],
}


let cachedConfig: AppConfig | null = null
let fetchPromise: Promise<AppConfig> | null = null
const listeners = new Set<() => void>()

// 预览配置（点击按钮立即生效，保存后才持久化）
let previewOverrides: Partial<UIConfig> | null = null
let dirtyListeners = new Set<() => void>()


async function fetchConfig(): Promise<AppConfig> {
  if (cachedConfig) return cachedConfig
  if (fetchPromise) return fetchPromise

  fetchPromise = get<AppConfig>('/analysis/config')
    .then(cfg => {
      cachedConfig = cfg
      fetchPromise = null
      return cfg
    })
    .catch(() => {
      fetchPromise = null
      toast({ type: 'warning', message: '配置加载失败，使用默认值' })
      return { translate: { temperature: 0, max_tokens: 0 }, analysis: { temperature: 0, max_tokens: 0 }, ui: DEFAULT_UI }
    })

  return fetchPromise
}


export function useUIConfig(): UIConfig {
  const [ui, setUI] = useState<UIConfig>(DEFAULT_UI)

  useEffect(() => {
    const update = () => {
      fetchConfig().then(cfg => {
        const saved = cfg.ui || DEFAULT_UI
        setUI({ ...saved, ...previewOverrides })
      })
    }
    update()
    listeners.add(update)
    return () => { listeners.delete(update) }
  }, [])

  return ui
}


/**
 * 设置预览配置（立即生效，不持久化）
 */
export function setUIPreview(overrides: Partial<UIConfig>): void {
  previewOverrides = { ...previewOverrides, ...overrides }
  listeners.forEach(fn => fn())
  dirtyListeners.forEach(fn => fn())
}


/**
 * 清除预览，恢复到已保存的配置
 */
export function clearUIPreview(): void {
  previewOverrides = null
  listeners.forEach(fn => fn())
  dirtyListeners.forEach(fn => fn())
}

export function useConfigDirty(): boolean {
  const [dirty, setDirty] = useState(!!previewOverrides)
  useEffect(() => {
    const update = () => setDirty(!!previewOverrides)
    dirtyListeners.add(update)
    return () => { dirtyListeners.delete(update) }
  }, [])
  return dirty
}

export function hasUnsavedConfig(): boolean {
  return !!previewOverrides
}


export function refreshConfig(): void {
  cachedConfig = null
  fetchPromise = null
  previewOverrides = null
  listeners.forEach(fn => fn())
}
