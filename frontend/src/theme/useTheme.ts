/**
 * 主题相关 Hook
 *
 * useThemeMode: 返回当前主题的明暗模式（'light' | 'dark'），主题切换时自动更新。
 * 供代码查看器、终端、Markdown(prose) 等需要按明暗切换的组件使用。
 */

import { useMemo } from 'react'
import { useUIConfig } from '@/hooks/useConfig'
import { resolveTheme, isDarkTheme, type Theme } from './themes'

export function useResolvedTheme(): Theme {
  const config = useUIConfig()
  return useMemo(
    () => resolveTheme(config.theme, config.custom_themes || []),
    [config.theme, config.custom_themes],
  )
}

export function useThemeMode(): 'light' | 'dark' {
  const theme = useResolvedTheme()
  return isDarkTheme(theme.colors) ? 'dark' : 'light'
}
