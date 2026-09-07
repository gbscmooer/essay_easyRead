/**
 * 主题系统 — 构成主义 Seed Token 架构
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

  return {
    bg_base: seeds.bg,
    bg_surface: seeds.surface,
    bg_elevated: dark ? mixHex(seeds.surface, '#ffffff', 0.08) : '#f1f5f9',
    border: dark ? mixHex(seeds.surface, '#ffffff', 0.12) : '#e2e8f0',
    border_light: dark ? mixHex(seeds.surface, '#ffffff', 0.20) : '#cbd5e1',
    text_primary: seeds.fg,
    text_secondary: dark ? '#cbd5e1' : '#334155',
    text_muted: seeds.muted,
    accent: seeds.accent,
    success: dark ? '#34d399' : '#16a34a',
    warning: dark ? '#fbbf24' : '#d97706',
    error: dark ? '#f87171' : '#dc2626',
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
    radius: 8,
  }
}

/* ============================================================
 * 内置预设
 * ============================================================ */

// 现代化学术精读界面：清晰、高对比度、无干扰护眼
const LIGHT_SEEDS: SeedTokens = {
  bg: '#f8fafc',
  fg: '#0f172a',
  surface: '#ffffff',
  accent: '#2563eb',
  muted: '#64748b',
  radius: 8,
}

const DARK_SEEDS: SeedTokens = {
  bg: '#0b0f17',
  fg: '#f8fafc',
  surface: '#131c2e',
  accent: '#3b82f6',
  muted: '#94a3b8',
  radius: 8,
}

export const BUILTIN_THEMES: Theme[] = [
  {
    id: 'light',
    name: '明亮',
    builtin: true,
    seeds: LIGHT_SEEDS,
    colors: seedsToColors(LIGHT_SEEDS),
  },
  {
    id: 'dark',
    name: '深色',
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
    '--text-disabled': dark ? '#666970' : '#9b9b92',

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

    // Code area
    '--code-bg': dark ? '#1e1e2e' : '#f6f8fa',
    '--code-sidebar-bg': dark ? '#1e1e2e' : '#f6f8fa',
    '--code-fg': dark ? '#cdd6f4' : '#1f2328',
    '--code-text': dark ? '#cccccc' : '#24292f',
    '--code-text-muted': dark ? '#6a737d' : '#656d76',
    '--code-text-bright': dark ? '#e8e8e8' : '#1b1f23',
    '--code-selection': dark ? '#094771' : '#b6d7ff',
    '--code-hover': dark ? '#2a2d2e' : '#e8e8e8',
    '--code-border': dark ? '#404040' : '#d0d7de',
    '--code-folder': dark ? '#dcb67a' : '#9a6700',

    // Structural palette consumed by the migrated experience shell.
    '--paper': dark ? '#17181b' : '#eee9dd',
    '--paper-bright': dark ? '#222329' : '#f7f2e7',
    '--ink': dark ? '#f7f2e7' : '#17181b',
    '--void': '#17181b',
    '--blue': dark ? '#5f83b5' : '#3969a8',
    '--blue-deep': '#292b55',
    '--signal': '#d94a38',
    '--red': '#d94a38',
    '--ochre': '#d3a52f',
    '--gray': '#8f9594',
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
