/**
 * 主题应用器 — 注入 seed token + 字体配置
 */
import { useEffect } from 'react'
import { useUIConfig } from '@/hooks/useConfig'
import { resolveTheme, applyTheme } from '@/theme/themes'

/* 中文段接在拉丁字族之后，并放在泛用字族前，让系统匹配原生中文字重。 */
const CJK_FONTS = "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC'"

const FONT_MAP: Record<string, string> = {
  sans: `'Inter Variable', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, ${CJK_FONTS}, sans-serif`,
  /* Charter / New York 都是为屏幕设计的过渡体，x-height 高、字重扎实，
     和展示层的 Archivo（无衬线窄体）是一组正经的编辑型搭配。Georgia 排在
     后面当兜底 —— 它在正文尺寸下字腔偏松，和这套紧凑版面不是一路。 */
  serif: `Charter, 'New York', 'Iowan Old Style', Palatino, Georgia, 'Songti SC', ${CJK_FONTS}, serif`,
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
      fontFamily = `'${config.custom_font}', 'Inter Variable', 'Helvetica Neue', Arial, ${CJK_FONTS}, sans-serif`
    }
    document.documentElement.style.setProperty('--font-ui', fontFamily)
  }, [config.font, config.custom_font])

  return <>{children}</>
}
