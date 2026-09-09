/**
 * AI 模型配置区块
 *
 * 全局 LLM 的唯一配置组：服务商预设、显式接口协议、Base URL、API Key、模型名、
 * 高级参数（Temperature / 最大输出 Token / 思考强度）。全部表单控件走
 * .er-field / .er-input 一档形制 —— 之前每个输入框各写一遍 `px-3 py-2 rounded-lg`
 * 加一段内联 borderColor，同一列里三个框的高度和边框颜色都不一致。
 */
import { useState } from 'react'
import ProviderPresets from './ProviderPresets'
import type { ProviderPreset } from './ProviderPresets'
import type { AIModelConfig, ThinkingEffort } from './config'

const THINKING_EFFORT_OPTIONS: { value: ThinkingEffort; label: string }[] = [
  { value: 'off', label: '关闭' },
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
]

interface AIModelSectionProps {
  title: string
  description: string
  config: AIModelConfig
  onChange: (cfg: AIModelConfig) => void
  defaultMaxTokens: number
  testSlot?: React.ReactNode
  providerPresets?: ProviderPreset[]
}

export default function AIModelSection({
  title,
  description,
  config,
  onChange,
  defaultMaxTokens,
  testSlot,
  providerPresets,
}: AIModelSectionProps) {
  const [showKey, setShowKey] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const keyMissing = !config.api_key.trim()

  const handlePresetSelect = (preset: ProviderPreset) => {
    onChange({
      ...config,
      provider: preset.id,
      api_format: preset.api_format,
      base_url: preset.base_url,
      model: preset.default_model,
    })
  }

  return (
    <section className="set-group" data-incomplete={keyMissing || undefined}>
      <header className="set-group__head">
        <div className="set-group__identity">
          <h2 className="set-group__title">{title}</h2>
          {keyMissing && <span className="er-tag er-tag--signal">缺 API Key</span>}
        </div>
        {testSlot}
        <p className="set-group__hint">{description}</p>
      </header>

      <ProviderPresets provider={config.provider} presets={providerPresets} onSelect={handlePresetSelect} />

      <div className="er-field">
        <span className="er-field__label" id={`${title}-protocol-label`}>接口协议</span>
        <div className="er-btn-group er-btn-group--block" role="radiogroup" aria-labelledby={`${title}-protocol-label`}>
          {([
            ['openai_chat', 'OpenAI Chat Completions'],
            ['anthropic_messages', 'Anthropic Messages'],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={config.api_format === value}
              data-active={config.api_format === value || undefined}
              onClick={() => onChange({ ...config, provider: 'custom', api_format: value })}
              className="er-btn er-btn--ghost er-btn--sm"
            >
              {label}
            </button>
          ))}
        </div>
        <p className="set-group__hint">协议由配置明确指定，不再根据 URL 猜测。</p>
      </div>

      <div className="er-field">
        <label className="er-field__label" htmlFor={`${title}-base`}>API Base URL</label>
        <input
          id={`${title}-base`}
          type="text"
          className="er-input"
          value={config.base_url}
          onChange={e => onChange({ ...config, provider: 'custom', base_url: e.target.value })}
          placeholder="https://api.openai.com/v1"
        />
      </div>

      <div className="er-field">
        <label className="er-field__label" htmlFor={`${title}-key`}>API Key</label>
        <div className="set-key">
          <input
            id={`${title}-key`}
            type={showKey ? 'text' : 'password'}
            className="er-input set-key__input"
            data-missing={keyMissing || undefined}
            value={config.api_key}
            onChange={e => onChange({ ...config, api_key: e.target.value })}
            placeholder="sk-…"
          />
          <button
            type="button"
            onClick={() => setShowKey(v => !v)}
            className="er-btn er-btn--ghost er-btn--sm set-key__reveal"
            aria-pressed={showKey}
          >
            {showKey ? '隐藏' : '显示'}
          </button>
        </div>
      </div>

      <div className="er-field">
        <label className="er-field__label" htmlFor={`${title}-model`}>模型名称</label>
        <input
          id={`${title}-model`}
          type="text"
          className="er-input"
          value={config.model}
          onChange={e => onChange({ ...config, model: e.target.value })}
          placeholder="gpt-4o-mini"
        />
      </div>

      <div className="set-advanced">
        <button
          type="button"
          onClick={() => setShowAdvanced(v => !v)}
          className="set-advanced__handle"
          aria-expanded={showAdvanced}
        >
          <span className="set-advanced__caret" aria-hidden="true" />
          高级参数
        </button>

        {showAdvanced && (
          <div className="set-advanced__body">
            <div className="set-slider">
              <div className="set-slider__head">
                <label htmlFor={`${title}-temp`}>Temperature</label>
                <output htmlFor={`${title}-temp`}>{config.temperature}</output>
              </div>
              <input
                id={`${title}-temp`}
                type="range"
                min={0}
                max={2}
                step={0.05}
                value={config.temperature}
                onChange={e => onChange({ ...config, temperature: parseFloat(e.target.value) })}
              />
              <div className="set-slider__scale">
                <span>精确 0</span>
                <span>平衡 1</span>
                <span>随机 2</span>
              </div>
            </div>

            <div className="er-field">
              <label className="er-field__label" htmlFor={`${title}-timeout`}>请求超时（秒）</label>
              <input
                id={`${title}-timeout`}
                type="number"
                className="er-input"
                min={1}
                max={600}
                value={config.timeout_seconds}
                onChange={e => onChange({ ...config, timeout_seconds: Number(e.target.value) })}
              />
            </div>

            <div className="set-slider">
              <div className="set-slider__head">
                <label htmlFor={`${title}-tokens`}>最大输出 Token</label>
                <output htmlFor={`${title}-tokens`}>{config.max_tokens}</output>
              </div>
              <input
                id={`${title}-tokens`}
                type="range"
                min={512}
                max={32768}
                step={512}
                value={config.max_tokens}
                onChange={e => onChange({ ...config, max_tokens: parseInt(e.target.value) })}
              />
              <div className="set-slider__scale">
                <span>512</span>
                <span>默认 {defaultMaxTokens}</span>
                <span>32768</span>
              </div>
            </div>

            <div className="set-slider">
              <div className="set-slider__head">
                <label id={`${title}-thinking-label`}>思考强度</label>
                <output aria-hidden="true">{config.thinking_effort}</output>
              </div>
              <div
                className="er-btn-group er-btn-group--block"
                role="radiogroup"
                aria-labelledby={`${title}-thinking-label`}
              >
                {THINKING_EFFORT_OPTIONS.map(opt => (
                  <button
                    key={opt.value}
                    type="button"
                    role="radio"
                    aria-checked={config.thinking_effort === opt.value}
                    data-active={config.thinking_effort === opt.value || undefined}
                    onClick={() => onChange({ ...config, thinking_effort: opt.value })}
                    className="er-btn er-btn--ghost er-btn--sm"
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <p className="set-group__hint">
                仅对支持的推理模型生效（Claude 3.7+/4+、OpenAI o 系 / gpt-5、DeepSeek v4+），
                其余沿用服务商默认；Anthropic 协议开启思考时强制 temperature=1。
              </p>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
