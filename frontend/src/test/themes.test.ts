/**
 * 主题系统单元测试
 */

import { describe, it, expect } from 'vitest'
import {
  rgba,
  isDarkTheme,
  resolveTheme,
  themeKey,
  buildCssVars,
  BUILTIN_THEMES,
  DEFAULT_THEME_ID,
  type ThemeColors,
  type SeedTokens,
  type Theme,
} from '@/theme/themes'


describe('rgba', () => {
  it('converts hex to rgba with full opacity', () => {
    expect(rgba('#ff0000', 1)).toBe('rgba(255, 0, 0, 1)')
  })

  it('converts hex to rgba with partial opacity', () => {
    expect(rgba('#00ff00', 0.5)).toBe('rgba(0, 255, 0, 0.5)')
  })

  it('handles 3-digit hex', () => {
    expect(rgba('#f00', 1)).toBe('rgba(255, 0, 0, 1)')
  })

  it('handles black', () => {
    expect(rgba('#000000', 0.8)).toBe('rgba(0, 0, 0, 0.8)')
  })

  it('handles white', () => {
    expect(rgba('#ffffff', 0.2)).toBe('rgba(255, 255, 255, 0.2)')
  })
})


describe('isDarkTheme', () => {
  it('identifies dark themes', () => {
    const darkColors: ThemeColors = {
      bg_base: '#000000', bg_surface: '#0a0a0a', bg_elevated: '#171717',
      border: '#262626', border_light: '#404040',
      text_primary: '#ffffff', text_secondary: '#a3a3a3', text_muted: '#737373',
      accent: '#ffffff', success: '#22c55e', warning: '#eab308', error: '#ef4444',
    }
    expect(isDarkTheme(darkColors)).toBe(true)
  })

  it('identifies light themes', () => {
    const lightColors: ThemeColors = {
      bg_base: '#ffffff', bg_surface: '#f8fafc', bg_elevated: '#f1f5f9',
      border: '#e2e8f0', border_light: '#cbd5e1',
      text_primary: '#0f172a', text_secondary: '#334155', text_muted: '#94a3b8',
      accent: '#1d4ed8', success: '#16a34a', warning: '#ca8a04', error: '#dc2626',
    }
    expect(isDarkTheme(lightColors)).toBe(false)
  })
})


describe('resolveTheme', () => {
  it('resolves builtin theme by id', () => {
    const theme = resolveTheme('dark')
    expect(theme.id).toBe('dark')
    expect(theme.name).toBe('深色')
  })

  it('resolves default theme for unknown id', () => {
    const theme = resolveTheme('nonexistent')
    expect(theme.id).toBe(DEFAULT_THEME_ID)
  })

  it('resolves default theme for undefined', () => {
    const theme = resolveTheme(undefined)
    expect(theme.id).toBe(DEFAULT_THEME_ID)
  })

  it('resolves custom theme', () => {
    const custom: Theme = {
      id: 'my-theme',
      name: '我的主题',
      seeds: BUILTIN_THEMES[0].seeds,
      colors: BUILTIN_THEMES[0].colors,
    }
    const theme = resolveTheme('custom:my-theme', [custom])
    expect(theme.id).toBe('my-theme')
  })

  it('falls back to default for missing custom theme', () => {
    const theme = resolveTheme('custom:missing')
    expect(theme.id).toBe(DEFAULT_THEME_ID)
  })
})


describe('themeKey', () => {
  it('returns id for builtin theme', () => {
    const theme = BUILTIN_THEMES[0]
    expect(themeKey(theme)).toBe(theme.id)
  })

  it('returns custom: prefix for custom theme', () => {
    const custom: Theme = {
      id: 'my-theme',
      name: '我的主题',
      seeds: BUILTIN_THEMES[0].seeds,
      colors: BUILTIN_THEMES[0].colors,
    }
    expect(themeKey(custom)).toBe('custom:my-theme')
  })
})


describe('buildCssVars', () => {
  it('generates all required CSS variables', () => {
    const vars = buildCssVars(BUILTIN_THEMES[0].seeds)
    const expectedKeys = [
      '--bg-base', '--bg-surface', '--bg-elevated',
      '--border', '--border-light',
      '--text-primary', '--text-secondary', '--text-muted',
      '--text-disabled',
      '--accent', '--accent-subtle',
      '--success', '--success-subtle',
      '--warning', '--warning-subtle',
      '--error', '--error-subtle',
      '--code-bg', '--code-sidebar-bg', '--code-fg', '--code-text',
      '--code-text-muted', '--code-text-bright', '--code-selection',
      '--code-hover', '--code-border', '--code-folder',
      '--paper', '--paper-bright', '--ink', '--blue', '--signal', '--ochre',
    ]
    for (const key of expectedKeys) {
      expect(vars).toHaveProperty(key)
    }
  })

  it('generates dark code colors for dark theme', () => {
    const darkSeeds: SeedTokens = {
      bg: '#000000', fg: '#ffffff', surface: '#0a0a0a',
      accent: '#ffffff', muted: '#737373', radius: 12,
    }
    const vars = buildCssVars(darkSeeds)
    expect(vars['--code-bg']).toBe('#1e1e2e')
    expect(vars['--code-fg']).toBe('#cdd6f4')
  })

  it('generates light code colors for light theme', () => {
    const lightSeeds: SeedTokens = {
      bg: '#ffffff', fg: '#0f172a', surface: '#f8fafc',
      accent: '#1d4ed8', muted: '#94a3b8', radius: 12,
    }
    const vars = buildCssVars(lightSeeds)
    expect(vars['--code-bg']).toBe('#f6f8fa')
    expect(vars['--code-fg']).toBe('#1f2328')
  })

  it('derives base colors from seeds', () => {
    const seeds = BUILTIN_THEMES[0].seeds
    const vars = buildCssVars(seeds)
    expect(vars['--bg-base']).toBe(seeds.bg)
    expect(vars['--accent']).toBe(seeds.accent)
  })
})


describe('BUILTIN_THEMES', () => {
  it('has 2 builtin themes', () => {
    expect(BUILTIN_THEMES).toHaveLength(2)
  })

  it('all themes have required fields', () => {
    for (const theme of BUILTIN_THEMES) {
      expect(theme).toHaveProperty('id')
      expect(theme).toHaveProperty('name')
      expect(theme).toHaveProperty('builtin', true)
      expect(theme).toHaveProperty('colors')
      expect(Object.keys(theme.colors)).toHaveLength(12)
    }
  })

  it('default theme exists', () => {
    const defaultTheme = BUILTIN_THEMES.find(t => t.id === DEFAULT_THEME_ID)
    expect(defaultTheme).toBeDefined()
  })
})
