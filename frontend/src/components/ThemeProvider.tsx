/**
 * 主题应用器 — 注入 seed token + 字体配置
 */
import { useEffect } from 'react'
import { useUIConfig } from '@/hooks/useConfig'
import { resolveTheme, applyTheme } from '@/theme/themes'

const FONT_MAP: Record<string, string> = {
  sans: "'Helvetica Neue', Helvetica, Arial, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
  serif: "Georgia, 'Songti SC', 'STSong', 'Times New Roman', serif",
  mono: "'SFMono-Regular', Menlo, Monaco, Consolas, 'Liberation Mono', monospace",
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const config = useUIConfig()

  useEffect(() => {
    const theme = resolveTheme(config.theme, config.custom_themes || [])
    applyTheme(theme)
  }, [config.theme, config.custom_themes])

  useEffect(() => {
    const font = config.font || 'sans'
    let fontFamily = FONT_MAP[font] || FONT_MAP.sans
    if (font === 'custom' && config.custom_font) {
      fontFamily = `'${config.custom_font}', 'Helvetica Neue', Arial, 'PingFang SC', sans-serif`
    }
    document.documentElement.style.setProperty('--font-ui', fontFamily)
  }, [config.font, config.custom_font])

  return <>{children}</>
}
