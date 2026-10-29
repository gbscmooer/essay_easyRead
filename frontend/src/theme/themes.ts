/**
 * 主题系统 — Seed Token 架构（v10「朱批」：宣纸 / 墨 / 朱砂）
 *
 * 6 个 seed token 派生出全部 CSS 变量。
 * Light / Dark 两个内置预设，用户可在此基础上自定义。
 *
 * Seed tokens:
 *   --seed-bg       页面最底层背景
 *   --seed-fg       主文字色
 *   --seed-surface  面板/卡片表面
 *   --seed-accent   强调色
 *   --seed-muted    弱化文字色
 *   --seed-radius   基础圆角基数 (px)
 */

/* ============================================================
 * 接口定义（向后兼容 ThemeColors 消费方）
 * ============================================================ */

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

export interface SeedTokens {
  bg: string
  fg: string
  surface: string
  accent: string
  muted: string
  radius: number
}

export interface Theme {
  id: string
  name: string
  builtin?: boolean
  seeds: SeedTokens
  /** 兼容旧消费方 — 由 seeds 派生 */
  colors: ThemeColors
}

/* ============================================================
 * 颜色工具
 * ============================================================ */

function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace('#', '').trim()
  if (h.length === 3) h = h.split('').map(c => c + c).join('')
  const n = parseInt(h, 16)
  if (isNaN(n) || h.length !== 6) return [128, 128, 128]
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function rgbToHex(r: number, g: number, b: number): string {
  return '#' + [r, g, b].map(v => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0')).join('')
}

/** Mix two hex colors: ratio 0 = c1, 1 = c2 */
function mixHex(c1: string, c2: string, ratio: number): string {
  const [r1, g1, b1] = hexToRgb(c1)
  const [r2, g2, b2] = hexToRgb(c2)
  return rgbToHex(
    r1 + (r2 - r1) * ratio,
    g1 + (g2 - g1) * ratio,
    b1 + (b2 - b1) * ratio,
  )
}

export function rgba(hex: string, alpha: number): string {
  const [r, g, b] = hexToRgb(hex)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/** 相对亮度 (0~1) */
function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex)
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
}

export function isDarkTheme(colors: ThemeColors): boolean {
  return luminance(colors.bg_base) < 0.5
}

/* ============================================================
 * Seeds → 派生色
 * ============================================================ */

function seedsToColors(seeds: SeedTokens): ThemeColors {
  const dark = luminance(seeds.bg) < 0.5
  // 灰阶由纸色与墨色混合得出（不再写死冷色 slate）：暖纸配暖灰，自定义主题换底色也自洽
  const tone = (ratio: number) => mixHex(seeds.bg, seeds.fg, ratio)

  return {
    bg_base: seeds.bg,
    bg_surface: seeds.surface,
    bg_elevated: dark ? mixHex(seeds.surface, seeds.fg, 0.05) : tone(0.04),
    border: dark ? mixHex(seeds.surface, seeds.fg, 0.13) : tone(0.12),
    border_light: dark ? mixHex(seeds.surface, seeds.fg, 0.22) : tone(0.22),
    text_primary: seeds.fg,
    text_secondary: mixHex(seeds.fg, seeds.bg, dark ? 0.22 : 0.26),
    text_muted: seeds.muted,
    accent: seeds.accent,
    success: dark ? '#7db592' : '#3d7457',
    warning: dark ? '#d9a64e' : '#a8741b',
    error: dark ? '#e2705f' : '#a3302a',
  }
}

/** 反向：从一套 ThemeColors 反推种子令牌（自定义主题编辑器创建主题时用）。 */
export function colorsToSeeds(c: ThemeColors): SeedTokens {
  return {
    bg: c.bg_base,
    fg: c.text_primary,
    surface: c.bg_surface,
    accent: c.accent,
    muted: c.text_muted,
    radius: 2,
  }
}

/* ============================================================
 * 内置预设
 * ============================================================ */

// 「朱批」：宣纸底、墨色字，唯一强调色是朱砂（学者在书页上做批注的朱笔）
const LIGHT_SEEDS: SeedTokens = {
  bg: '#f3efe6',
  fg: '#1c1b18',
  surface: '#fbf9f3',
  accent: '#b5361c',
  muted: '#7a746a',
  radius: 2,
}

// 墨：深底米色字，朱砂提亮一档以保证对比
const DARK_SEEDS: SeedTokens = {
  bg: '#131311',
  fg: '#eae5da',
  surface: '#1b1a18',
  accent: '#e4683f',
  muted: '#8d877b',
  radius: 2,
}

export const BUILTIN_THEMES: Theme[] = [
  {
    id: 'light',
    name: '宣纸',
    builtin: true,
    seeds: LIGHT_SEEDS,
    colors: seedsToColors(LIGHT_SEEDS),
  },
  {
    id: 'dark',
    name: '墨',
    builtin: true,
    seeds: DARK_SEEDS,
    colors: seedsToColors(DARK_SEEDS),
  },
]

export const DEFAULT_THEME_ID = 'light'

/* ============================================================
 * 解析与应用
 * ============================================================ */

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

export function themeKey(theme: Theme): string {
  return theme.builtin ? theme.id : `custom:${theme.id}`
}

/** 由 seeds 派生全部 CSS 变量 */
export function buildCssVars(seeds: SeedTokens, colors?: ThemeColors): Record<string, string> {
  // 颜色变量以显式 colors 为准（自定义主题可逐项细调，编辑即生效）；
  // 缺省时（如仅传种子）从种子派生。seeds 仍提供 --seed-* 原始令牌与 radius。
  const c = colors ?? seedsToColors(seeds)
  const dark = luminance(c.bg_base) < 0.5
  const [accentR, accentG, accentB] = hexToRgb(c.accent)

  return {
    // Seed tokens (raw)——跟随有效颜色，避免自定义主题改色后漂移
    '--seed-bg': c.bg_base,
    '--seed-fg': c.text_primary,
    '--seed-surface': c.bg_surface,
    '--seed-accent': c.accent,
    '--seed-accent-rgb': `${accentR}, ${accentG}, ${accentB}`,
    '--seed-muted': c.text_muted,
    '--seed-radius': `${seeds.radius}px`,

    // Surface tokens
    '--bg-base': c.bg_base,
    '--bg-surface': c.bg_surface,
    '--bg-elevated': c.bg_elevated,

    // Border tokens
    '--border': c.border,
    '--border-light': c.border_light,

    // Text tokens
    '--text-primary': c.text_primary,
    '--text-secondary': c.text_secondary,
    '--text-muted': c.text_muted,
    '--text-disabled': mixHex(c.text_muted, c.bg_base, 0.38),

    // Accent + subtle variants
    '--accent': c.accent,
    '--accent-subtle': rgba(c.accent, dark ? 0.14 : 0.08),

    // Semantic status + subtle variants
    '--success': c.success,
    '--success-subtle': rgba(c.success, dark ? 0.16 : 0.10),
    '--warning': c.warning,
    '--warning-subtle': rgba(c.warning, dark ? 0.16 : 0.10),
    '--error': c.error,
    '--error-subtle': rgba(c.error, dark ? 0.16 : 0.10),

    // Code area（暖色底，与纸面一致）
    '--code-bg': dark ? '#191816' : '#efeadf',
    '--code-sidebar-bg': dark ? '#171614' : '#ebe5d9',
    '--code-fg': dark ? '#e2dccf' : '#26241f',
    '--code-text': dark ? '#d4cec2' : '#2b2924',
    '--code-text-muted': dark ? '#7f796e' : '#7a746a',
    '--code-text-bright': dark ? '#f1ece2' : '#14130f',
    '--code-selection': dark ? 'rgba(228, 104, 63, 0.28)' : 'rgba(181, 54, 28, 0.16)',
    '--code-hover': dark ? '#24221f' : '#e6dfd1',
    '--code-border': dark ? '#34322d' : '#d8d0c0',
    '--code-folder': dark ? '#d9a64e' : '#8a5f12',

    // 版面结构色（外壳、页头、账页、批注）
    '--paper': c.bg_base,
    '--paper-bright': c.bg_surface,
    '--ink': c.text_primary,
    '--void': dark ? '#0c0c0b' : '#1c1b18',
    '--cinnabar': c.accent,
    '--cinnabar-rgb': `${accentR}, ${accentG}, ${accentB}`,
    '--mark': dark ? 'rgba(217, 166, 78, 0.22)' : 'rgba(226, 184, 72, 0.30)',
    '--rule-ink': dark ? 'rgba(234, 229, 218, 0.55)' : 'rgba(28, 27, 24, 0.78)',
    '--blue': dark ? '#9fb3cc' : '#3a5679',
    '--blue-deep': dark ? '#c3d0e0' : '#26394f',
    '--signal': c.accent,
    '--red': c.error,
    '--ochre': c.warning,
    '--gray': c.text_muted,
  }
}

/**
 * 将主题应用到 <html>：注入 CSS 变量 + data-theme + data-mode。
 */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  const vars = buildCssVars(theme.seeds, theme.colors)
  for (const [k, v] of Object.entries(vars)) {
    root.style.setProperty(k, v)
  }
  root.setAttribute('data-theme', theme.builtin ? theme.id : 'custom')
  root.setAttribute('data-mode', isDarkTheme(theme.colors) ? 'dark' : 'light')
}

/* ============================================================
 * 向后兼容：COLOR_FIELDS 供设置页色盘使用
 * ============================================================ */

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
