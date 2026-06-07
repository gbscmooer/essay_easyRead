/**
 * 主题系统
 *
 * 一个主题 = 12 个可自定义颜色（语义色 + 状态色）。
 * 其余 CSS 变量（accent-subtle、状态浅底、代码区配色）由这 12 个色 + 明暗模式自动派生。
 *
 * 内置 3 个主题（不可删除）：纯白 light / 纯黑 pure / 摩卡 mocha。
 * 用户可在此基础上新建、重命名、删除自定义主题，全部颜色用色盘自定义。
 */

export interface ThemeColors {
  bg_base: string
  bg_surface: string
  bg_elevated: string
  border: string
  border_light: string
  text_primary: string
  text_secondary: string
  text_muted: string
  accent: string
  success: string
  warning: string
  error: string
}

export interface Theme {
  id: string
  name: string
  builtin?: boolean
  colors: ThemeColors
}

/** 12 个可编辑颜色的元信息（用于设置页生成色盘） */
export const COLOR_FIELDS: { key: keyof ThemeColors; label: string; group: string }[] = [
  { key: 'bg_base', label: '背景 · 底层', group: '背景' },
  { key: 'bg_surface', label: '背景 · 面板', group: '背景' },
  { key: 'bg_elevated', label: '背景 · 悬浮', group: '背景' },
  { key: 'border', label: '边框', group: '边框' },
  { key: 'border_light', label: '边框 · 亮', group: '边框' },
  { key: 'text_primary', label: '文字 · 主', group: '文字' },
  { key: 'text_secondary', label: '文字 · 次', group: '文字' },
  { key: 'text_muted', label: '文字 · 弱', group: '文字' },
  { key: 'accent', label: '强调色', group: '强调' },
  { key: 'success', label: '状态 · 成功', group: '状态' },
  { key: 'warning', label: '状态 · 警告', group: '状态' },
  { key: 'error', label: '状态 · 错误', group: '状态' },
]

export const BUILTIN_THEMES: Theme[] = [
  {
    id: 'light',
    name: '纯白',
    builtin: true,
    colors: {
      bg_base: '#ffffff', bg_surface: '#f8fafc', bg_elevated: '#f1f5f9',
      border: '#e2e8f0', border_light: '#cbd5e1',
      text_primary: '#0f172a', text_secondary: '#334155', text_muted: '#94a3b8',
      accent: '#1d4ed8', success: '#16a34a', warning: '#ca8a04', error: '#dc2626',
    },
  },
  {
    id: 'pure',
    name: '纯黑',
    builtin: true,
    colors: {
      bg_base: '#000000', bg_surface: '#0a0a0a', bg_elevated: '#171717',
      border: '#262626', border_light: '#404040',
      text_primary: '#ffffff', text_secondary: '#a3a3a3', text_muted: '#737373',
      accent: '#ffffff', success: '#22c55e', warning: '#eab308', error: '#ef4444',
    },
  },
  {
    id: 'mocha',
    name: '摩卡',
    builtin: true,
    colors: {
      bg_base: '#1a1614', bg_surface: '#252019', bg_elevated: '#302a22',
      border: '#4a4136', border_light: '#5c5246',
      text_primary: '#faf0e4', text_secondary: '#dcc8a8', text_muted: '#a89888',
      accent: '#f0a050', success: '#22c55e', warning: '#eab308', error: '#ef4444',
    },
  },
]

export const DEFAULT_THEME_ID = 'light'


// ---------- 颜色工具 ----------

function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace('#', '').trim()
  if (h.length === 3) h = h.split('').map(c => c + c).join('')
  const n = parseInt(h, 16)
  if (isNaN(n) || h.length !== 6) return [128, 128, 128]
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

export function rgba(hex: string, alpha: number): string {
  const [r, g, b] = hexToRgb(hex)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/** 相对亮度（0~1），用于判断主题明暗 */
function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex)
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
}

export function isDarkTheme(colors: ThemeColors): boolean {
  return luminance(colors.bg_base) < 0.5
}


// ---------- 主题解析与应用 ----------

/** 把主题 key（内置 id 或 custom:<id>）解析为完整 Theme */
export function resolveTheme(key: string | undefined, customThemes: Theme[] = []): Theme {
  if (key) {
    if (key.startsWith('custom:')) {
      const id = key.slice('custom:'.length)
      const found = customThemes.find(t => t.id === id)
      if (found) return found
    }
    const builtin = BUILTIN_THEMES.find(t => t.id === key)
    if (builtin) return builtin
  }
  return BUILTIN_THEMES.find(t => t.id === DEFAULT_THEME_ID) || BUILTIN_THEMES[0]
}

/** 主题 key 用于持久化：内置直接用 id，自定义用 custom:<id> */
export function themeKey(theme: Theme): string {
  return theme.builtin ? theme.id : `custom:${theme.id}`
}

/** 由 12 个颜色派生出全部 CSS 变量 */
export function buildCssVars(colors: ThemeColors): Record<string, string> {
  const dark = isDarkTheme(colors)
  return {
    '--bg-base': colors.bg_base,
    '--bg-surface': colors.bg_surface,
    '--bg-elevated': colors.bg_elevated,
    '--border': colors.border,
    '--border-light': colors.border_light,
    '--text-primary': colors.text_primary,
    '--text-secondary': colors.text_secondary,
    '--text-muted': colors.text_muted,
    '--accent': colors.accent,
    '--accent-subtle': rgba(colors.accent, dark ? 0.14 : 0.08),
    '--success': colors.success,
    '--success-subtle': rgba(colors.success, dark ? 0.16 : 0.1),
    '--warning': colors.warning,
    '--warning-subtle': rgba(colors.warning, dark ? 0.16 : 0.1),
    '--error': colors.error,
    '--error-subtle': rgba(colors.error, dark ? 0.16 : 0.1),
    // 代码区：浅色主题用浅底深字，深色主题用深底浅字
    '--code-bg': dark ? '#1e1e2e' : '#f6f8fa',
    '--code-fg': dark ? '#cdd6f4' : '#1f2328',
  }
}

/**
 * 将主题应用到 <html>：注入 CSS 变量 + data-theme(id) + data-mode(light/dark)。
 * data-mode 驱动代码区/语法高亮/终端的明暗切换。
 */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  const vars = buildCssVars(theme.colors)
  for (const [k, v] of Object.entries(vars)) {
    root.style.setProperty(k, v)
  }
  root.setAttribute('data-theme', theme.builtin ? theme.id : 'custom')
  root.setAttribute('data-mode', isDarkTheme(theme.colors) ? 'dark' : 'light')
}
