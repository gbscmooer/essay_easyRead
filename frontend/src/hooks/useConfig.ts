import { useState, useEffect } from 'react'
import { get } from '@/api/client'


interface UIConfig {
  highlight_duration: number
  zoom_step: number
  zoom_min: number
  zoom_max: number
  pinch_zoom_step: number
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
}


let cachedConfig: AppConfig | null = null
let fetchPromise: Promise<AppConfig> | null = null


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
      return { translate: { temperature: 0, max_tokens: 0 }, analysis: { temperature: 0, max_tokens: 0 }, ui: DEFAULT_UI }
    })

  return fetchPromise
}


export function useUIConfig(): UIConfig {
  const [ui, setUI] = useState<UIConfig>(DEFAULT_UI)

  useEffect(() => {
    fetchConfig().then(cfg => setUI(cfg.ui || DEFAULT_UI))
  }, [])

  return ui
}


export function refreshConfig(): void {
  cachedConfig = null
  fetchPromise = null
}
