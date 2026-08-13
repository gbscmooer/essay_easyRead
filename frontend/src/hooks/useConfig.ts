import { useState, useEffect, useMemo } from 'react'
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
let settingsFormDirty = false
let dirtyListeners = new Set<() => void>()

function notifyDirtyListeners(): void {
  dirtyListeners.forEach(fn => fn())
}

function isConfigDirty(): boolean {
  return !!previewOverrides || settingsFormDirty
}


/** 从 localStorage 读取保存的主题 key。首屏渲染与后端不可达时的回落都用它。 */
function getSavedThemeKey(): string | null {
  try { return localStorage.getItem('essay_theme') } catch { return null }
}

/** 自定义主题同样要能在后端不可达时还原，否则 custom:* 主题会解析不到定义。 */
function getSavedCustomThemes(): UIConfig['custom_themes'] {
  try {
    const raw = localStorage.getItem('essay_custom_themes')
    return raw ? JSON.parse(raw) : undefined
  } catch {
    return undefined
  }
}

/**
 * 把后端已保存的主题写入 localStorage，供 index.html 内联脚本首屏读取。
 * 只持久化后端真实配置（不含预览覆盖），避免未保存的预览污染首屏。
 */
function persistThemeToStorage(ui: UIConfig): void {
  try {
    localStorage.setItem('essay_theme', ui.theme)
    localStorage.setItem('essay_custom_themes', JSON.stringify(ui.custom_themes ?? []))
  } catch { /* localStorage 不可用时忽略 */ }
}

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
      toast({ type: 'warning', message: '配置加载失败，暂用本地缓存的界面设置' })
      // 后端不可达时保留上一次保存到 localStorage 的主题。回落到 DEFAULT_UI
      // 会把用户选的深色主题当场翻成浅色 —— 首屏内联脚本已经按缓存画成深色，
      // 挂载后又被这里改回浅色，结果是一次刺眼的闪白，而且成因完全不可见。
      // 注意这个结果不写进 cachedConfig：下一次调用仍会重试请求。
      const fallbackTheme = getSavedThemeKey()
      const fallbackUI: UIConfig = fallbackTheme
        ? { ...DEFAULT_UI, theme: fallbackTheme, custom_themes: getSavedCustomThemes() ?? DEFAULT_UI.custom_themes }
        : DEFAULT_UI
      return { translate: { temperature: 0, max_tokens: 0 }, analysis: { temperature: 0, max_tokens: 0 }, ui: fallbackUI }
    })

  return fetchPromise
}


export function useUIConfig(): UIConfig {
  // 初始化时从 localStorage 读取保存的主题，避免等待 fetch 导致首屏闪白
  const savedTheme = getSavedThemeKey()
  const initialUI: UIConfig = savedTheme
    ? { ...DEFAULT_UI, theme: savedTheme, custom_themes: getSavedCustomThemes() ?? DEFAULT_UI.custom_themes }
    : DEFAULT_UI
  const [ui, setUI] = useState<UIConfig>(initialUI)

  useEffect(() => {
    const update = () => {
      fetchConfig().then(cfg => {
        const saved = cfg.ui || DEFAULT_UI
        persistThemeToStorage(saved)
        setUI({ ...saved, ...previewOverrides })
      }).catch(() => {})
    }
    update()
    listeners.add(update)
    return () => { listeners.delete(update) }
  }, [])

  return ui
}


/** Focused hook: only theme/font-related config fields */
export function useThemeConfig() {
  const { theme, font, custom_font, custom_themes } = useUIConfig()
  return useMemo(() => ({ theme, font, custom_font, custom_themes }), [theme, font, custom_font, custom_themes])
}


/** Focused hook: only zoom/highlight config fields */
export function useViewerConfig() {
  const { highlight_duration, zoom_step, zoom_min, zoom_max, pinch_zoom_step } = useUIConfig()
  return useMemo(() => ({
    highlight_duration, zoom_step, zoom_min, zoom_max, pinch_zoom_step,
  }), [highlight_duration, zoom_step, zoom_min, zoom_max, pinch_zoom_step])
}


/**
 * 设置预览配置（立即生效，不持久化）
 */
export function setUIPreview(overrides: Partial<UIConfig>): void {
  previewOverrides = { ...previewOverrides, ...overrides }
  listeners.forEach(fn => fn())
  notifyDirtyListeners()
}


/**
 * 清除预览，恢复到已保存的配置
 */
export function clearUIPreview(): void {
  previewOverrides = null
  listeners.forEach(fn => fn())
  notifyDirtyListeners()
}

/** 设置页完整表单的脏状态，覆盖模型、提示词、研究参数与 UI 配置。 */
export function setSettingsFormDirty(dirty: boolean): void {
  if (settingsFormDirty === dirty) return
  settingsFormDirty = dirty
  notifyDirtyListeners()
}

export function useConfigDirty(): boolean {
  const [dirty, setDirty] = useState(isConfigDirty)
  useEffect(() => {
    const update = () => setDirty(isConfigDirty())
    dirtyListeners.add(update)
    return () => { dirtyListeners.delete(update) }
  }, [])
  return dirty
}

export function hasUnsavedConfig(): boolean {
  return isConfigDirty()
}


export function refreshConfig(): void {
  cachedConfig = null
  fetchPromise = null
  previewOverrides = null
  listeners.forEach(fn => fn())
  notifyDirtyListeners()
}
