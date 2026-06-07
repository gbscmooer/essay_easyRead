import { useEffect } from 'react'
import { useUIConfig } from '@/hooks/useConfig'
import { resolveTheme, applyTheme } from '@/theme/themes'

const FONT_MAP: Record<string, string> = {
  sans: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif",
  serif: "'Times New Roman', Georgia, 'Noto Serif', serif",
  mono: "'Menlo', 'Monaco', 'Courier New', monospace",
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
      fontFamily = `'${config.custom_font}', sans-serif`
    }
    document.documentElement.style.setProperty('--font-ui', fontFamily)
  }, [config.font, config.custom_font])

  return <>{children}</>
}
