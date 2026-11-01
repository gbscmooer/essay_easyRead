/**
 * 主题应用器 — 注入 seed token + 字体配置
 */
import { useEffect } from 'react'
import { useUIConfig } from '@/hooks/useConfig'
import { resolveTheme, applyTheme } from '@/theme/themes'

/* 中文段接在拉丁字族之后，并放在泛用字族前，让系统匹配原生中文字重。 */
const CJK_FONTS = "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC'"

const FONT_MAP: Record<string, string> = {
  sans: `'Instrument Sans Variable', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, ${CJK_FONTS}, sans-serif`,
  /* 衬线正文：Newsreader（为屏幕阅读设计、带光学尺寸轴）+ 思源宋体，与展示层同一组字族 */
  serif: `'Newsreader Variable', 'Noto Serif SC Variable', 'Songti SC', Georgia, ${CJK_FONTS}, serif`,
  mono: `'JetBrains Mono Variable', 'SFMono-Regular', Menlo, Monaco, Consolas, ${CJK_FONTS}, monospace`,
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
      fontFamily = `'${config.custom_font}', 'Instrument Sans Variable', 'Helvetica Neue', Arial, ${CJK_FONTS}, sans-serif`
    }
    document.documentElement.style.setProperty('--font-ui', fontFamily)
  }, [config.font, config.custom_font])

  return <>{children}</>
}
