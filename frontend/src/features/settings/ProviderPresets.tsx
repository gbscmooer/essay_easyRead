/**
 * 服务商预设配置列表与选择器
 *
 * 供 AIModelSection 内部使用，也单独导出供其他组件复用。
 */

export interface ProviderPreset {
  id: string
  label: string
  api_format: 'openai_chat' | 'anthropic_messages'
  base_url: string
  default_model: string
}

/** 仅两种协议：OpenAI 兼容 / Anthropic 兼容。厂商差异只体现在 base_url + model。 */
export const PROVIDER_PRESETS: ProviderPreset[] = [
  { id: 'openai', label: 'OpenAI', api_format: 'openai_chat', base_url: 'https://api.openai.com/v1', default_model: 'gpt-4o-mini' },
  { id: 'anthropic', label: 'Anthropic', api_format: 'anthropic_messages', base_url: 'https://api.anthropic.com', default_model: 'claude-sonnet-4-6' },
  { id: 'openrouter', label: 'OpenRouter', api_format: 'openai_chat', base_url: 'https://openrouter.ai/api/v1', default_model: 'openai/gpt-4o-mini' },
  { id: 'deepseek', label: 'DeepSeek', api_format: 'openai_chat', base_url: 'https://api.deepseek.com', default_model: 'deepseek-chat' },
  { id: 'siliconflow', label: 'SiliconFlow', api_format: 'openai_chat', base_url: 'https://api.siliconflow.cn/v1', default_model: 'deepseek-ai/DeepSeek-V3' },
  { id: 'custom', label: '自定义', api_format: 'openai_chat', base_url: '', default_model: '' },
]

interface ProviderPresetsProps {
  provider: string
  presets?: ProviderPreset[]
  onSelect: (preset: ProviderPreset) => void
}

export default function ProviderPresets({ provider, presets = PROVIDER_PRESETS, onSelect }: ProviderPresetsProps) {
  return (
    <div className="er-field">
      <span className="er-field__label">服务商预设</span>
      {/* 一组互斥选项 —— 用连排分段控件表达互斥，比几个各自描边的胶囊更准确。 */}
      <div className="er-btn-group set-presets" role="group" aria-label="服务商预设">
        {presets.map(preset => (
          <button
            key={preset.id}
            type="button"
            onClick={() => onSelect(preset)}
            data-active={provider === preset.id || undefined}
            aria-pressed={provider === preset.id}
            className="er-btn er-btn--ghost er-btn--sm"
          >
            {preset.label}
          </button>
        ))}
      </div>
    </div>
  )
}
