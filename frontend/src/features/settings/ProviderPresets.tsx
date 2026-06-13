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

export const PROVIDER_PRESETS: ProviderPreset[] = [
  { label: 'OpenAI', base_url: 'https://api.openai.com/v1', models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1-mini'] },
  { label: 'Anthropic', base_url: 'https://api.anthropic.com', models: ['claude-sonnet-4-6', 'claude-haiku-4-5-20251001'] },
  { label: 'DeepSeek', base_url: 'https://api.deepseek.com', models: ['deepseek-v4-flash', 'deepseek-v4-pro', 'deepseek-chat'] },
]

interface ProviderPresetsProps {
  baseUrl: string
  onSelect: (preset: ProviderPreset) => void
}

export default function ProviderPresets({ baseUrl, onSelect }: ProviderPresetsProps) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1.5">服务商预设</label>
      <div className="flex flex-wrap gap-2">
        {PROVIDER_PRESETS.map(preset => (
          <button
            key={preset.label}
            onClick={() => onSelect(preset)}
            className={`px-3 py-1 text-xs rounded-full border transition-colors ${
              baseUrl === preset.base_url
                ? 'border-blue-500 bg-blue-500/10 text-blue-400'
                : 'border-gray-700 text-gray-400 hover:border-gray-600 hover:text-gray-300'
            }`}
          >
            {preset.label}
          </button>
        ))}
      </div>
    </div>
  )
}
