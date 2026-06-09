/**
 * 主题编辑器
 *
 * - 内置 3 个主题（纯白/纯黑/摩卡）不可删除/编辑，可「复制为自定义主题」后再改。
 * - 自定义主题：新建、重命名、删除，12 个颜色用色盘(input type=color)自定义。
 * - 所有改动即时预览（setUIPreview），保存按钮持久化到后端。
 */

import { useState } from 'react'
import type { UIConfig } from '@/hooks/useConfig'
import { setUIPreview } from '@/hooks/useConfig'
import {
  BUILTIN_THEMES, COLOR_FIELDS, DEFAULT_THEME_ID, resolveTheme, themeKey,
  type Theme, type ThemeColors,
} from '@/theme/themes'


interface ThemeEditorProps {
  config: UIConfig
  onChange: (cfg: UIConfig) => void
}

function genId(): string {
  return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`
}

export function ThemeEditor({ config, onChange }: ThemeEditorProps) {
  const customThemes: Theme[] = config.custom_themes || []
  const allThemes: Theme[] = [...BUILTIN_THEMES, ...customThemes]
  const activeKey = config.theme || DEFAULT_THEME_ID
  const activeTheme = resolveTheme(activeKey, customThemes)
  const editingCustom = !activeTheme.builtin

  const [renaming, setRenaming] = useState(false)
  const [nameDraft, setNameDraft] = useState('')

  /** 应用改动：更新表单 + 即时预览 */
  function apply(next: Partial<UIConfig>) {
    const merged = { ...config, ...next }
    onChange(merged)
    setUIPreview({ theme: merged.theme, custom_themes: merged.custom_themes })
  }

  function selectTheme(key: string) {
    setRenaming(false)
    apply({ theme: key })
  }

  function createTheme(from: ThemeColors) {
    const id = genId()
    const n = customThemes.length + 1
    const theme: Theme = { id, name: `自定义主题 ${n}`, colors: { ...from } }
    apply({ custom_themes: [...customThemes, theme], theme: `custom:${id}` })
  }

  function updateActiveColors(key: keyof ThemeColors, value: string) {
    if (!editingCustom) return
    const next = customThemes.map(t =>
      t.id === activeTheme.id ? { ...t, colors: { ...t.colors, [key]: value } } : t
    )
    apply({ custom_themes: next })
  }

  function renameActive(name: string) {
    const next = customThemes.map(t => (t.id === activeTheme.id ? { ...t, name } : t))
    apply({ custom_themes: next })
  }

  function deleteActive() {
    const next = customThemes.filter(t => t.id !== activeTheme.id)
    apply({ custom_themes: next, theme: DEFAULT_THEME_ID })
  }

  // 颜色字段按 group 分组
  const groups = COLOR_FIELDS.reduce<Record<string, typeof COLOR_FIELDS>>((acc, f) => {
    (acc[f.group] ||= []).push(f)
    return acc
  }, {})

  return (
    <div>
      <label className="block text-xs mb-2" style={{ color: 'var(--text-muted)' }}>配色风格</label>

      {/* 主题卡片网格 */}
      <div className="grid grid-cols-3 gap-2">
        {allThemes.map(theme => {
          const key = themeKey(theme)
          const selected = key === activeKey
          return (
            <button
              key={key}
              onClick={() => selectTheme(key)}
              className="px-2.5 py-2 text-left rounded-lg border transition-colors"
              style={{
                borderColor: selected ? 'var(--accent)' : 'var(--border)',
                backgroundColor: selected ? 'var(--accent-subtle)' : 'transparent',
              }}
            >
              <div className="flex items-center gap-1.5 mb-1.5">
                {/* 色卡预览 */}
                {[theme.colors.bg_base, theme.colors.bg_elevated, theme.colors.accent].map((c, i) => (
                  <span key={i} className="w-3 h-3 rounded-full" style={{ backgroundColor: c, border: '1px solid var(--border)' }} />
                ))}
              </div>
              <span className="text-sm block truncate" style={{ color: selected ? 'var(--accent)' : 'var(--text-secondary)' }}>
                {theme.name}
              </span>
              <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                {theme.builtin ? '内置' : '自定义'}
              </span>
            </button>
          )
        })}

        {/* 新建主题 */}
        <button
          onClick={() => createTheme(activeTheme.colors)}
          className="px-2.5 py-2 rounded-lg border border-dashed transition-colors flex flex-col items-center justify-center gap-1"
          style={{ borderColor: 'var(--border-light)', color: 'var(--text-muted)' }}
          title="基于当前主题新建一个可自定义主题"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 4v16m8-8H4" />
          </svg>
          <span className="text-xs">新建主题</span>
        </button>
      </div>

      {/* 当前主题操作区 */}
      <div className="mt-3 rounded-lg border p-3" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-elevated)' }}>
        <div className="flex items-center justify-between mb-3">
          {renaming && editingCustom ? (
            <input
              autoFocus
              value={nameDraft}
              onChange={e => setNameDraft(e.target.value)}
              onBlur={() => { if (nameDraft.trim()) renameActive(nameDraft.trim()); setRenaming(false) }}
              onKeyDown={e => {
                if (e.key === 'Enter') { if (nameDraft.trim()) renameActive(nameDraft.trim()); setRenaming(false) }
                if (e.key === 'Escape') setRenaming(false)
              }}
              className="text-sm px-2 py-1 rounded border bg-transparent focus:outline-none"
              style={{ borderColor: 'var(--accent)', color: 'var(--text-primary)' }}
            />
          ) : (
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
              {activeTheme.name}
              <span className="ml-2 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                {editingCustom ? '可编辑' : '内置主题（只读）'}
              </span>
            </span>
          )}

          <div className="flex items-center gap-2">
            {editingCustom ? (
              <>
                <button
                  onClick={() => { setNameDraft(activeTheme.name); setRenaming(true) }}
                  className="text-xs px-2 py-1 rounded transition-colors"
                  style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-surface)' }}
                >
                  重命名
                </button>
                <button
                  onClick={deleteActive}
                  className="text-xs px-2 py-1 rounded transition-colors"
                  style={{ color: 'var(--error)', backgroundColor: 'var(--error-subtle)' }}
                >
                  删除
                </button>
              </>
            ) : (
              <button
                onClick={() => createTheme(activeTheme.colors)}
                className="text-xs px-2 py-1 rounded transition-colors"
                style={{ color: 'var(--accent)', backgroundColor: 'var(--accent-subtle)' }}
              >
                复制为自定义主题
              </button>
            )}
          </div>
        </div>

        {/* 颜色编辑（仅自定义主题可改） */}
        {editingCustom ? (
          <div className="space-y-3">
            {Object.entries(groups).map(([group, fields]) => (
              <div key={group}>
                <div className="text-[10px] mb-1.5" style={{ color: 'var(--text-muted)' }}>{group}</div>
                <div className="grid grid-cols-2 gap-2">
                  {fields.map(field => {
                    const val = activeTheme.colors[field.key]
                    return (
                      <label
                        key={field.key}
                        className="flex items-center gap-2 px-2 py-1.5 rounded border cursor-pointer"
                        style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)' }}
                      >
                        <input
                          type="color"
                          value={val}
                          onChange={e => updateActiveColors(field.key, e.target.value)}
                          className="w-6 h-6 rounded cursor-pointer shrink-0 bg-transparent border-0 p-0"
                          style={{ minWidth: '1.5rem' }}
                        />
                        <span className="flex-1 min-w-0">
                          <span className="block text-xs truncate" style={{ color: 'var(--text-secondary)' }}>{field.label}</span>
                          <span className="block text-[10px] font-mono uppercase" style={{ color: 'var(--text-muted)' }}>{val}</span>
                        </span>
                      </label>
                    )
                  })}
                </div>
              </div>
            ))}
            <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
              强调浅底、状态浅底、代码区配色会根据上述颜色与主题明暗自动派生。
            </p>
          </div>
        ) : (
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            内置主题不可直接编辑。点击「复制为自定义主题」即可在副本上用色盘自由调整全部颜色。
          </p>
        )}
      </div>
    </div>
  )
}
