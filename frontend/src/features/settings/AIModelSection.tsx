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
    const updates: Partial<AIModelConfig> = { base_url: preset.base_url }
    if (preset.models.length === 1) {
      updates.model = preset.models[0]
    }
    onChange({ ...config, ...updates })
  }

  return (
    <div className={`bg-gray-900 rounded-xl border p-5 space-y-4 ${
      keyMissing ? 'border-red-500/50' : 'border-gray-800'
    }`}>
      <div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-gray-100">{title}</h2>
            {keyMissing && (
              <span className="text-xs text-red-400 bg-red-500/10 px-2 py-0.5 rounded-full">
                未配置 API Key
              </span>
            )}
          </div>
          {testSlot}
        </div>
        <p className="text-xs text-gray-500 mt-0.5">{description}</p>
      </div>

      {/* 快捷选择 */}
      <ProviderPresets baseUrl={config.base_url} onSelect={handlePresetSelect} />

      {/* Base URL */}
      <div>
        <label className="block text-xs text-gray-500 mb-1">API Base URL</label>
        <input
          type="text"
          value={config.base_url}
          onChange={e => onChange({ ...config, base_url: e.target.value })}
          placeholder="https://api.openai.com/v1"
          className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
        />
      </div>

      {/* API Key */}
      <div>
        <label className="block text-xs text-gray-500 mb-1">API Key</label>
        <div className="relative">
          <input
            type={showKey ? 'text' : 'password'}
            value={config.api_key}
            onChange={e => onChange({ ...config, api_key: e.target.value })}
            placeholder="sk-..."
            className={`w-full px-3 py-2 pr-10 text-sm bg-gray-800 border rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none transition-colors font-mono ${
              keyMissing
                ? 'border-red-500/50 focus:border-red-500'
                : 'border-gray-700 focus:border-blue-500'
            }`}
          />
          <button
            onClick={() => setShowKey(v => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 text-xs"
          >
            {showKey ? '隐藏' : '显示'}
          </button>
        </div>
      </div>

      {/* Model */}
      <div>
        <label className="block text-xs text-gray-500 mb-1">模型名称</label>
        <input
          type="text"
          value={config.model}
          onChange={e => onChange({ ...config, model: e.target.value })}
          placeholder="gpt-4o-mini"
          className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
        />
      </div>

      {/* 高级参数（折叠） */}
      <div>
        <button
          onClick={() => setShowAdvanced(v => !v)}
          className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-400 transition-colors"
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
          <div className="mt-3 space-y-3 pl-2 border-l border-gray-800">
            {/* Temperature */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">Temperature</label>
                <span className="text-xs text-gray-600">{config.temperature}</span>
              </div>
              <input
                type="range"
                min={0}
                max={2}
                step={0.05}
                value={config.temperature}
                onChange={e => onChange({ ...config, temperature: parseFloat(e.target.value) })}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
              <div className="flex justify-between text-[10px] text-gray-600 mt-0.5">
                <span>精确 (0)</span>
                <span>平衡 (1)</span>
                <span>随机 (2)</span>
              </div>
            </div>

            {/* Max Tokens */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">最大输出 Token 数</label>
                <span className="text-xs text-gray-600">{config.max_tokens}</span>
              </div>
              <input
                type="range"
                min={512}
                max={32768}
                step={512}
                value={config.max_tokens}
                onChange={e => onChange({ ...config, max_tokens: parseInt(e.target.value) })}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
              <div className="flex justify-between text-[10px] text-gray-600 mt-0.5">
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
