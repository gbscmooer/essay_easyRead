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
    <div className="er-field">
      <span className="er-field__label">服务商预设</span>
      {/* 一组互斥选项 —— 用连排分段控件表达互斥，比几个各自描边的胶囊更准确。 */}
      <div className="er-btn-group set-presets" role="group" aria-label="服务商预设">
        {PROVIDER_PRESETS.map(preset => (
          <button
            key={preset.label}
            type="button"
            onClick={() => onSelect(preset)}
            data-active={baseUrl === preset.base_url || undefined}
            aria-pressed={baseUrl === preset.base_url}
            className="er-btn er-btn--ghost er-btn--sm"
          >
            {preset.label}
          </button>
        ))}
      </div>
    </div>
  )
}
