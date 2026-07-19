/**
 * 服务商预设配置列表与选择器
 *
 * 供 AIModelSection 内部使用，也单独导出供其他组件复用。
 */

export interface ProviderPreset {
  label: string
  base_url: string
  models: string[]
}

/** 仅两种协议：OpenAI 兼容 / Anthropic 兼容。厂商差异只体现在 base_url + model。 */
export const PROVIDER_PRESETS: ProviderPreset[] = [
  { label: 'OpenAI', base_url: 'https://api.openai.com/v1', models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1-mini'] },
  { label: 'Anthropic', base_url: 'https://api.anthropic.com', models: ['claude-sonnet-4-6', 'claude-haiku-4-5-20251001'] },
]

interface ProviderPresetsProps {
  baseUrl: string
  onSelect: (preset: ProviderPreset) => void
}

export default function ProviderPresets({ baseUrl, onSelect }: ProviderPresetsProps) {
  return (
    <div>
      <label className="block text-xs mb-1.5" style={{ color: 'var(--text-muted)' }}>
        服务商预设
      </label>
      <div className="flex flex-wrap gap-2">
        {PROVIDER_PRESETS.map(preset => {
          const active = baseUrl === preset.base_url
          return (
            <button
              key={preset.label}
              onClick={() => onSelect(preset)}
              className="px-3 py-1 text-xs rounded-full border transition-colors"
              style={{
                borderColor: active ? 'var(--accent)' : 'var(--border)',
                backgroundColor: active ? 'var(--accent-subtle)' : 'transparent',
                color: active ? 'var(--accent)' : 'var(--text-secondary)',
              }}
            >
              {preset.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
