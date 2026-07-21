/**
 * AI 模型配置区块
 *
 * 翻译模型 / 解读模型共用的配置卡片，
 * 包含服务商预设、Base URL、API Key、模型名、高级参数。
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
    <div
      className="panel-section space-y-4"
      style={{
        borderLeft: keyMissing ? '3px solid var(--error)' : undefined,
        paddingLeft: keyMissing ? '0.75rem' : undefined,
      }}
    >
      <div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2
              className="text-base font-semibold"
              style={{ color: 'var(--text-primary)' }}
            >
              {title}
            </h2>
            {keyMissing && (
              <span
                className="text-xs px-2 py-0.5 rounded-full"
                style={{
                  color: 'var(--error)',
                  backgroundColor: 'var(--error-subtle)',
                }}
              >
                未配置 API Key
              </span>
            )}
          </div>
          {testSlot}
        </div>
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
          {description}
        </p>
      </div>

      {/* 快捷选择 */}
      <ProviderPresets baseUrl={config.base_url} onSelect={handlePresetSelect} />

      {/* Base URL */}
      <div>
        <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
          API Base URL
        </label>
        <input
          type="text"
          value={config.base_url}
          onChange={e => onChange({ ...config, base_url: e.target.value })}
          placeholder="https://api.openai.com/v1"
          className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            borderColor: 'var(--border)',
            color: 'var(--text-primary)',
          }}
        />
      </div>

      {/* API Key */}
      <div>
        <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
          API Key
        </label>
        <div className="relative">
          <input
            type={showKey ? 'text' : 'password'}
            value={config.api_key}
            onChange={e => onChange({ ...config, api_key: e.target.value })}
            placeholder="sk-..."
            className="w-full px-3 py-2 pr-10 text-sm rounded-lg border focus:outline-none transition-colors font-mono"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              borderColor: keyMissing
                ? 'color-mix(in srgb, var(--error) 50%, transparent)'
                : 'var(--border)',
              color: 'var(--text-primary)',
            }}
          />
          <button
            onClick={() => setShowKey(v => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-xs"
            style={{ color: 'var(--text-muted)' }}
          >
            {showKey ? '隐藏' : '显示'}
          </button>
        </div>
      </div>

      {/* Model */}
      <div>
        <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
          模型名称
        </label>
        <input
          type="text"
          value={config.model}
          onChange={e => onChange({ ...config, model: e.target.value })}
          placeholder="gpt-4o-mini"
          className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            borderColor: 'var(--border)',
            color: 'var(--text-primary)',
          }}
        />
      </div>

      {/* 高级参数（折叠） */}
      <div>
        <button
          onClick={() => setShowAdvanced(v => !v)}
          className="flex items-center gap-1 text-xs transition-colors"
          style={{ color: 'var(--text-muted)' }}
        >
          <svg
            className={`w-3 h-3 transition-transform ${showAdvanced ? 'rotate-90' : ''}`}
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          高级参数
        </button>

        {showAdvanced && (
          <div
            className="mt-3 space-y-3 pl-2"
            style={{ borderLeft: '1px solid var(--border)' }}
          >
            {/* Temperature */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs" style={{ color: 'var(--text-muted)' }}>Temperature</label>
                <span className="text-xs" style={{ color: 'var(--text-disabled)' }}>{config.temperature}</span>
              </div>
              <input
                type="range"
                min={0}
                max={2}
                step={0.05}
                value={config.temperature}
                onChange={e => onChange({ ...config, temperature: parseFloat(e.target.value) })}
                className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-[var(--accent)]"
                style={{ backgroundColor: 'var(--border)' }}
              />
              <div className="flex justify-between text-[10px] mt-0.5" style={{ color: 'var(--text-disabled)' }}>
                <span>精确 (0)</span>
                <span>平衡 (1)</span>
                <span>随机 (2)</span>
              </div>
            </div>

            {/* Max Tokens */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs" style={{ color: 'var(--text-muted)' }}>最大输出 Token 数</label>
                <span className="text-xs" style={{ color: 'var(--text-disabled)' }}>{config.max_tokens}</span>
              </div>
              <input
                type="range"
                min={512}
                max={32768}
                step={512}
                value={config.max_tokens}
                onChange={e => onChange({ ...config, max_tokens: parseInt(e.target.value) })}
                className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-[var(--accent)]"
                style={{ backgroundColor: 'var(--border)' }}
              />
              <div className="flex justify-between text-[10px] mt-0.5" style={{ color: 'var(--text-disabled)' }}>
                <span>512</span>
                <span>默认 {defaultMaxTokens}</span>
                <span>32768</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
