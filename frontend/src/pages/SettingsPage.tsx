import { useState, useEffect } from 'react'
import { post, get } from '@/api/client'


interface AIModelConfig {
  api_key: string
  base_url: string
  model: string
  system_prompt: string
}

interface AppConfig {
  translate: AIModelConfig
  analysis: AIModelConfig
}


const DEFAULT_CONFIG: AppConfig = {
  translate: {
    api_key: '',
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    system_prompt: '',
  },
  analysis: {
    api_key: '',
    base_url: 'https://token-plan-cn.xiaomimimo.com/anthropic',
    model: 'mimo-v2.5-pro',
    system_prompt: '',
  },
}


const PROVIDER_PRESETS = [
  { label: 'OpenAI', base_url: 'https://api.openai.com/v1', models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1-mini'] },
  { label: 'MiMo (Anthropic 格式)', base_url: 'https://token-plan-cn.xiaomimimo.com/anthropic', models: ['mimo-v2.5-pro'] },
  { label: 'Anthropic', base_url: 'https://api.anthropic.com', models: ['claude-sonnet-4-6', 'claude-haiku-4-5-20251001'] },
  { label: 'DeepSeek', base_url: 'https://api.deepseek.com', models: ['deepseek-chat', 'deepseek-reasoner'] },
  { label: '自定义', base_url: '', models: [] },
]


function ConfigSection({
  title,
  description,
  config,
  onChange,
}: {
  title: string
  description: string
  config: AIModelConfig
  onChange: (cfg: AIModelConfig) => void
}) {
  const [showKey, setShowKey] = useState(false)
  const keyMissing = !config.api_key.trim()

  return (
    <div className={`bg-gray-900 rounded-xl border p-5 space-y-4 ${
      keyMissing ? 'border-red-500/50' : 'border-gray-800'
    }`}>
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-gray-100">{title}</h2>
          {keyMissing && (
            <span className="text-xs text-red-400 bg-red-500/10 px-2 py-0.5 rounded-full">
              未配置 API Key
            </span>
          )}
        </div>
        <p className="text-xs text-gray-500 mt-0.5">{description}</p>
      </div>

      {/* 快捷选择 */}
      <div>
        <label className="block text-xs text-gray-500 mb-1.5">服务商预设</label>
        <div className="flex flex-wrap gap-2">
          {PROVIDER_PRESETS.map(preset => (
            <button
              key={preset.label}
              onClick={() => {
                onChange({ ...config, base_url: preset.base_url })
                if (preset.models.length === 1) {
                  onChange({ ...config, base_url: preset.base_url, model: preset.models[0] })
                }
              }}
              className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                config.base_url === preset.base_url
                  ? 'border-blue-500 bg-blue-500/10 text-blue-400'
                  : 'border-gray-700 text-gray-400 hover:border-gray-600 hover:text-gray-300'
              }`}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>

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
    </div>
  )
}


export function SettingsPage() {
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    get<AppConfig>('/analysis/config')
      .then(cfg => setConfig(cfg))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    try {
      await post('/analysis/config', config)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {
      // ignore
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-6 h-6 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
      </div>
    )
  }

  const translateOk = !!config.translate.api_key.trim()
  const analysisOk = !!config.analysis.api_key.trim()
  const allConfigured = translateOk && analysisOk

  return (
    <div className="max-w-2xl mx-auto px-6 py-8 space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-100">AI 模型配置</h1>
        <p className="text-sm text-gray-500 mt-1">
          分别配置翻译和解读使用的 AI 模型。翻译用于画词翻译，解读用于论文分析、总结等。
        </p>
      </div>

      {/* 整体状态提示 */}
      {!allConfigured && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <span className="text-red-400 text-lg mt-0.5">!</span>
            <div>
              <p className="text-sm text-red-300 font-medium">配置不完整</p>
              <ul className="text-xs text-red-400/80 mt-1 space-y-0.5">
                {!translateOk && <li>- 翻译模型未配置 API Key，画词翻译功能将不可用</li>}
                {!analysisOk && <li>- 解读模型未配置 API Key，论文分析功能将使用默认配置</li>}
              </ul>
            </div>
          </div>
        </div>
      )}

      <ConfigSection
        title="翻译模型"
        description="用于选中文本的翻译功能。推荐使用轻量快速的模型。"
        config={config.translate}
        onChange={cfg => setConfig(prev => ({ ...prev, translate: cfg }))}
      />

      <ConfigSection
        title="解读模型"
        description="用于论文解析、AI 总结等功能。推荐使用能力较强的模型。"
        config={config.analysis}
        onChange={cfg => setConfig(prev => ({ ...prev, analysis: cfg }))}
      />

      <div className="flex items-center gap-3 pt-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-5 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-500 disabled:opacity-50 transition-colors"
        >
          {saving ? '保存中...' : '保存配置'}
        </button>
        {saved && (
          <span className="text-sm text-green-400">已保存</span>
        )}
      </div>
    </div>
  )
}
