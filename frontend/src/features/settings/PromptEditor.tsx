/**
 * 解读系统提示词编辑器
 *
 * - 内置提示词(builtin)只读，可展开查看、可「复制为副本」。
 * - 自定义副本：新建/重命名/编辑(文本域)/删除，并可选为「当前生效」替换内置。
 * - 生效的提示词由后端 get_active_analysis_prompt() 解析，用于论文解读。
 */

import { useEffect, useState } from 'react'
import { getBuiltinAnalysisPrompt } from '@/api/settings'

export interface PromptTemplate {
  id: string
  name: string
  content: string
}

export interface AnalysisPromptConfig {
  active: string
  custom: PromptTemplate[]
}

interface PromptEditorProps {
  value: AnalysisPromptConfig
  onChange: (next: AnalysisPromptConfig) => void
}

function genId(): string {
  return `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`
}

export function PromptEditor({ value, onChange }: PromptEditorProps) {
  const [builtin, setBuiltin] = useState<string>('')
  const [showBuiltin, setShowBuiltin] = useState(false)
  const custom = value.custom || []
  const active = value.active || 'builtin'

  useEffect(() => {
    getBuiltinAnalysisPrompt().then(setBuiltin).catch(() => setBuiltin('（无法加载内置提示词）'))
  }, [])

  const activeCustom = active.startsWith('custom:')
    ? custom.find(t => t.id === active.slice('custom:'.length))
    : undefined

  function setActive(key: string) {
    onChange({ ...value, active: key })
  }

  function createCopy() {
    const id = genId()
    const base = activeCustom?.content || builtin || ''
    const n = custom.length + 1
    const tpl: PromptTemplate = { id, name: `自定义提示词 ${n}`, content: base }
    onChange({ active: `custom:${id}`, custom: [...custom, tpl] })
  }

  function updateActive(patch: Partial<PromptTemplate>) {
    if (!activeCustom) return
    onChange({
      ...value,
      custom: custom.map(t => (t.id === activeCustom.id ? { ...t, ...patch } : t)),
    })
  }

  function deleteActive() {
    if (!activeCustom) return
    onChange({ active: 'builtin', custom: custom.filter(t => t.id !== activeCustom.id) })
  }

  return (
    <div className="rounded-xl border p-5 space-y-4" style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
      <div>
        <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>解读提示词</h2>
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
          论文解读所用的系统提示词。内置版本只读，可复制为副本后自由修改并选用替换。
        </p>
      </div>

      {/* 选用哪个提示词 */}
      <div className="space-y-1.5">
        <label className="block text-xs" style={{ color: 'var(--text-muted)' }}>当前生效</label>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setActive('builtin')}
            className="px-3 py-1.5 text-sm rounded-lg border transition-colors"
            style={{
              borderColor: active === 'builtin' ? 'var(--accent)' : 'var(--border)',
              backgroundColor: active === 'builtin' ? 'var(--accent-subtle)' : 'transparent',
              color: active === 'builtin' ? 'var(--accent)' : 'var(--text-secondary)',
            }}
          >
            内置（只读）
          </button>
          {custom.map(t => {
            const key = `custom:${t.id}`
            const sel = active === key
            return (
              <button
                key={t.id}
                onClick={() => setActive(key)}
                className="px-3 py-1.5 text-sm rounded-lg border transition-colors max-w-[12rem] truncate"
                style={{
                  borderColor: sel ? 'var(--accent)' : 'var(--border)',
                  backgroundColor: sel ? 'var(--accent-subtle)' : 'transparent',
                  color: sel ? 'var(--accent)' : 'var(--text-secondary)',
                }}
                title={t.name}
              >
                {t.name}
              </button>
            )
          })}
          <button
            onClick={createCopy}
            className="px-3 py-1.5 text-sm rounded-lg border border-dashed transition-colors"
            style={{ borderColor: 'var(--border-light)', color: 'var(--text-muted)' }}
          >
            + 复制为副本
          </button>
        </div>
      </div>

      {/* 内置提示词：只读查看 */}
      <div className="rounded-lg border" style={{ borderColor: 'var(--border)' }}>
        <button
          onClick={() => setShowBuiltin(v => !v)}
          className="w-full flex items-center justify-between px-3 py-2 text-xs"
          style={{ color: 'var(--text-secondary)' }}
        >
          <span>内置提示词（只读）</span>
          <span>{showBuiltin ? '收起 ▲' : '展开查看 ▼'}</span>
        </button>
        {showBuiltin && (
          <textarea
            readOnly
            value={builtin}
            className="w-full px-3 py-2 text-xs font-mono rounded-b-lg resize-y focus:outline-none"
            style={{ minHeight: '14rem', backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)', borderTop: '1px solid var(--border)' }}
          />
        )}
      </div>

      {/* 自定义副本编辑 */}
      {activeCustom ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <input
              value={activeCustom.name}
              onChange={e => updateActive({ name: e.target.value })}
              className="flex-1 px-3 py-1.5 text-sm rounded border focus:outline-none"
              style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)', color: 'var(--text-primary)' }}
              placeholder="副本名称"
            />
            <button
              onClick={deleteActive}
              className="text-xs px-2.5 py-1.5 rounded transition-colors"
              style={{ color: 'var(--error)', backgroundColor: 'var(--error-subtle)' }}
            >
              删除
            </button>
          </div>
          <textarea
            value={activeCustom.content}
            onChange={e => updateActive({ content: e.target.value })}
            className="w-full px-3 py-2 text-xs font-mono rounded border resize-y focus:outline-none"
            style={{ minHeight: '18rem', backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)', color: 'var(--text-primary)' }}
            placeholder="在此编辑你的解读系统提示词…"
          />
          <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            提示：保留原内置提示词要求的 JSON 输出结构，否则解读结果可能无法正确解析。保存后对新解读生效。
          </p>
        </div>
      ) : (
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
          当前使用内置提示词。点击「复制为副本」可创建一个可编辑版本并替换使用。
        </p>
      )}
    </div>
  )
}
