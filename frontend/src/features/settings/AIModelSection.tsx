/**
 * AI 模型配置区块
 *
 * 翻译模型 / 解读模型共用的配置组：服务商预设、Base URL、API Key、模型名、
 * 高级参数。全部表单控件走 .er-field / .er-input 一档形制 —— 之前每个输入框
 * 各写一遍 `px-3 py-2 rounded-lg` 加一段内联 borderColor，同一列里三个框的
 * 高度和边框颜色都不一致。
 */
import { useState } from 'react'
import ProviderPresets from './ProviderPresets'
import type { ProviderPreset } from './ProviderPresets'
import type { AIModelConfig } from './config'

interface AIModelSectionProps {
  title: string
  description: string
  config: AIModelConfig
  onChange: (cfg: AIModelConfig) => void
  defaultMaxTokens: number
  testSlot?: React.ReactNode
}

export default function AIModelSection({
  title,
  description,
  config,
  onChange,
  defaultMaxTokens,
  testSlot,
}: AIModelSectionProps) {
  const [showKey, setShowKey] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const keyMissing = !config.api_key.trim()

  const handlePresetSelect = (preset: ProviderPreset) => {
    onChange({
      ...config,
      base_url: preset.base_url,
      model: preset.models[0] ?? '',
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

      <ProviderPresets baseUrl={config.base_url} onSelect={handlePresetSelect} />

      <div className="er-field">
        <label className="er-field__label" htmlFor={`${title}-base`}>API Base URL</label>
        <input
          id={`${title}-base`}
          type="text"
          className="er-input"
          value={config.base_url}
          onChange={e => onChange({ ...config, base_url: e.target.value })}
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
          </div>
        )}
      </div>
    </section>
  )
}
