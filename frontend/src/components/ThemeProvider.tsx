/**
 * 主题应用器 — 注入 seed token + 字体配置
 */
import { useEffect } from 'react'
import { useUIConfig } from '@/hooks/useConfig'
import { resolveTheme, applyTheme } from '@/theme/themes'

/* 中文段永远接在拉丁字族之后：拉丁字形取自托管的变量字体，汉字自动回落到
   'CJK Adjusted'（typography.css 里改写过垂直度量的 PingFang SC）。 */
const CJK_TAIL = "'CJK Adjusted', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"

const FONT_MAP: Record<string, string> = {
  sans: `'Inter Variable', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, ${CJK_TAIL}`,
  /* Charter / New York 都是为屏幕设计的过渡体，x-height 高、字重扎实，
     和展示层的 Archivo（无衬线窄体）是一组正经的编辑型搭配。Georgia 排在
     后面当兜底 —— 它在正文尺寸下字腔偏松，和这套紧凑版面不是一路。 */
  serif: `Charter, 'New York', 'Iowan Old Style', Palatino, Georgia, 'Songti SC', serif, ${CJK_TAIL}`,
  mono: `'JetBrains Mono Variable', 'SFMono-Regular', Menlo, Monaco, Consolas, monospace, ${CJK_TAIL}`,
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
      fontFamily = `'${config.custom_font}', 'Inter Variable', 'Helvetica Neue', Arial, ${CJK_TAIL}`
    }
    document.documentElement.style.setProperty('--font-ui', fontFamily)
  }, [config.font, config.custom_font])

  return <>{children}</>
}
