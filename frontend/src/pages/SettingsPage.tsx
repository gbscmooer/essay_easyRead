import { useState, useEffect } from 'react'
import { useBlocker } from 'react-router-dom'
import { post, get } from '@/api/client'
import { refreshConfig, setUIPreview, clearUIPreview, hasUnsavedConfig } from '@/hooks/useConfig'
import { ThemeEditor } from '@/features/settings/ThemeEditor'
import { PromptEditor } from '@/features/settings/PromptEditor'
import { TestButton } from '@/features/settings/TestButton'
import { DeepResearchSettings } from '@/features/settings/DeepResearchSettings'
import { DEFAULT_CONFIG } from '@/features/settings/config'
import type { AIModelConfig, UIConfig, AppConfig } from '@/features/settings/config'




const PROVIDER_PRESETS = [
  { label: 'OpenAI', base_url: 'https://api.openai.com/v1', models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1-mini'] },
  { label: 'Anthropic', base_url: 'https://api.anthropic.com', models: ['claude-sonnet-4-6', 'claude-haiku-4-5-20251001'] },
]


function ConfigSection({
  title,
  description,
  config,
  onChange,
  defaultMaxTokens,
  testType,
}: {
  title: string
  description: string
  config: AIModelConfig
  onChange: (cfg: AIModelConfig) => void
  defaultMaxTokens: number
  testType: 'translate' | 'analysis'
}) {
  const [showKey, setShowKey] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const keyMissing = !config.api_key.trim()

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
          <TestButton
            type={testType}
            api_key={config.api_key}
            base_url={config.base_url}
            model={config.model}
          />
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
                const updates: Partial<AIModelConfig> = { base_url: preset.base_url }
                if (preset.models.length === 1) {
                  updates.model = preset.models[0]
                }
                onChange({ ...config, ...updates })
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


const FONTS = [
  { key: 'sans', label: 'Arial（非衬线）' },
  { key: 'serif', label: 'Times New Roman（衬线）' },
  { key: 'mono', label: '等宽字体' },
  { key: 'custom', label: '自定义字体' },
]


function UIConfigSection({
  config,
  onChange,
}: {
  config: UIConfig
  onChange: (cfg: UIConfig) => void
}) {
  const [showAdvanced, setShowAdvanced] = useState(false)

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-gray-100">界面设置</h2>
        <p className="text-xs text-gray-500 mt-0.5">主题、字体和交互行为参数</p>
      </div>

      {/* 主题选择 + 自定义编辑器 */}
      <ThemeEditor config={config} onChange={onChange} />

      {/* 字体选择 */}
      <div>
        <label className="block text-xs text-gray-500 mb-2">界面字体</label>
        <div className="space-y-1.5">
          {FONTS.map(font => (
            <button
              key={font.key}
              onClick={() => { onChange({ ...config, font: font.key }); setUIPreview({ font: font.key }) }}
              className={`w-full text-left px-3 py-1.5 text-sm rounded border transition-colors ${
                config.font === font.key
                  ? 'border-blue-500 bg-blue-500/10 text-blue-400'
                  : 'border-gray-700 text-gray-400 hover:border-gray-600'
              }`}
            >
              {font.label}
            </button>
          ))}
        </div>
        {config.font === 'custom' && (
          <input
            type="text"
            value={config.custom_font}
            onChange={e => { onChange({ ...config, custom_font: e.target.value }); setUIPreview({ custom_font: e.target.value }) }}
            placeholder="输入字体名称，如 PingFang SC"
            className="mt-2 w-full px-3 py-1.5 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500"
          />
        )}
        <p className="text-[10px] text-gray-600 mt-1">仅作用于界面文字，不影响源码展示</p>
      </div>

      {/* 大纲跳转高亮时长 */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs text-gray-500">大纲跳转高亮时长</label>
          <span className="text-xs text-gray-600">{(config.highlight_duration / 1000).toFixed(1)}s</span>
        </div>
        <input
          type="range"
          min={500}
          max={10000}
          step={500}
          value={config.highlight_duration}
          onChange={e => onChange({ ...config, highlight_duration: parseInt(e.target.value) })}
          className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
        />
        <div className="flex justify-between text-[10px] text-gray-600 mt-0.5">
          <span>0.5s</span>
          <span>默认 3s</span>
          <span>10s</span>
        </div>
      </div>

      {/* PDF 缩放参数（折叠） */}
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
          PDF 缩放参数
        </button>

        {showAdvanced && (
          <div className="mt-3 space-y-3 pl-2 border-l border-gray-800">
            {/* Zoom Step */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">每次缩放步进</label>
                <span className="text-xs text-gray-600">{Math.round(config.zoom_step * 100)}%</span>
              </div>
              <input
                type="range"
                min={5}
                max={50}
                step={5}
                value={Math.round(config.zoom_step * 100)}
                onChange={e => onChange({ ...config, zoom_step: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
            </div>

            {/* Zoom Min */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">最小缩放倍率</label>
                <span className="text-xs text-gray-600">{Math.round(config.zoom_min * 100)}%</span>
              </div>
              <input
                type="range"
                min={10}
                max={100}
                step={10}
                value={Math.round(config.zoom_min * 100)}
                onChange={e => onChange({ ...config, zoom_min: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
            </div>

            {/* Zoom Max */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">最大缩放倍率</label>
                <span className="text-xs text-gray-600">{Math.round(config.zoom_max * 100)}%</span>
              </div>
              <input
                type="range"
                min={100}
                max={500}
                step={50}
                value={Math.round(config.zoom_max * 100)}
                onChange={e => onChange({ ...config, zoom_max: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
            </div>

            {/* Pinch Zoom Step */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">触控板捏合灵敏度</label>
                <span className="text-xs text-gray-600">{Math.round(config.pinch_zoom_step * 100)}%</span>
              </div>
              <input
                type="range"
                min={1}
                max={10}
                step={1}
                value={Math.round(config.pinch_zoom_step * 100)}
                onChange={e => onChange({ ...config, pinch_zoom_step: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
              <div className="flex justify-between text-[10px] text-gray-600 mt-0.5">
                <span>低 (1%)</span>
                <span>默认 2%</span>
                <span>高 (10%)</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}


export function SettingsPage() {
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)
  const [simProviders, setSimProviders] = useState<string[]>(['bge_centroid'])
  // 已保存配置快照：用于判定表单是否有未保存改动（脏检查）
  const [savedConfig, setSavedConfig] = useState<AppConfig | null>(null)

  useEffect(() => {
    get<AppConfig>('/analysis/config')
      .then(cfg => {
        const merged: AppConfig = {
          ...DEFAULT_CONFIG,
          ...cfg,
          embedding: { ...DEFAULT_CONFIG.embedding, ...(cfg.embedding ?? {}) },
          translate: { ...DEFAULT_CONFIG.translate, ...(cfg.translate ?? {}) },
          analysis: { ...DEFAULT_CONFIG.analysis, ...(cfg.analysis ?? {}) },
          analysis_prompt: { ...DEFAULT_CONFIG.analysis_prompt, ...(cfg.analysis_prompt ?? {}) },
          ui: { ...DEFAULT_CONFIG.ui, ...(cfg.ui ?? {}) },
          research: { ...DEFAULT_CONFIG.research, ...(cfg.research ?? {}) },
          reranker: { ...DEFAULT_CONFIG.reranker, ...(cfg.reranker ?? {}) },
          similarity: { ...DEFAULT_CONFIG.similarity, ...(cfg.similarity ?? {}) },
          arxiv_expand: { ...DEFAULT_CONFIG.arxiv_expand, ...(cfg.arxiv_expand ?? {}) },
          findpapers: { ...DEFAULT_CONFIG.findpapers, ...(cfg.findpapers ?? {}) },
        }
        setConfig(merged)
        setSavedConfig(merged)
      })
      .catch(() => {})
      .finally(() => setLoading(false))

    get<{ providers: string[] }>('/analysis/similarity/providers')
      .then(r => { if (r?.providers?.length) setSimProviders(r.providers) })
      .catch(() => {})
  }, [])

  // 离开页面时清除预览（不保存则恢复原样）
  useEffect(() => {
    return () => { clearUIPreview() }
  }, [])

  // 表单是否有未保存改动：config 与已保存快照深比较（纯 JSON，可靠）
  const dirty = savedConfig != null && JSON.stringify(config) !== JSON.stringify(savedConfig)

  // 站内切换路由时拦截（含侧边导航 / 浏览器后退），弹「是否保存」
  const blocker = useBlocker(dirty)

  // 浏览器关闭/刷新提示（表单脏或主题预览未保存时）
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirty || hasUnsavedConfig()) {
        e.preventDefault()
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  const handleSave = async (): Promise<boolean> => {
    setSaving(true)
    setSaved(false)
    try {
      await post('/analysis/config', config)
      refreshConfig()
      setSavedConfig(config)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      return true
    } catch {
      return false
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
        <h1 className="text-xl font-bold text-gray-100">设置</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
          配置模型参数和界面行为。点击"高级参数"可调整 temperature、token 上限等。
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
        description="用于选中文本的翻译功能。"
        config={config.translate}
        onChange={cfg => setConfig(prev => ({ ...prev, translate: cfg }))}
        defaultMaxTokens={2048}
        testType="translate"
      />

      <ConfigSection
        title="解读模型"
        description="用于论文解析、总结等功能。"
        config={config.analysis}
        onChange={cfg => setConfig(prev => ({ ...prev, analysis: cfg }))}
        defaultMaxTokens={16384}
        testType="analysis"
      />

      <PromptEditor
        value={config.analysis_prompt}
        onChange={ap => setConfig(prev => ({ ...prev, analysis_prompt: ap }))}
      />

      <UIConfigSection
        config={config.ui}
        onChange={cfg => setConfig(prev => ({ ...prev, ui: cfg }))}
      />

      {/* 深度研究设置（6 块）：与「深度研究」界面的设置抽屉共用同一组件 */}
      <DeepResearchSettings config={config} setConfig={setConfig} simProviders={simProviders} />

      <div className="flex items-center gap-3 pt-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-5 py-2 text-sm font-medium bg-blue-600 text-black rounded-lg hover:bg-blue-500 disabled:opacity-50 transition-colors"
        >
          {saving ? '保存中...' : '保存配置'}
        </button>
        {saved && (
          <span className="text-sm text-green-400">已保存</span>
        )}
      </div>

      {/* 未保存离开确认：站内切换路由时拦截 */}
      {blocker.state === 'blocked' && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center" onClick={() => blocker.reset?.()}>
          <div className="absolute inset-0 bg-black/40" />
          <div
            className="relative w-80 rounded-xl p-5 border"
            style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)' }}
            onClick={e => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>是否保存？</h3>
            <p className="text-xs mt-1.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              当前设置有未保存的修改。离开前是否保存？
            </p>
            <div className="flex items-center justify-end gap-2 mt-4">
              <button
                onClick={() => blocker.proceed?.()}
                disabled={saving}
                className="px-3 py-1.5 text-xs rounded-lg border transition-colors disabled:opacity-50"
                style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)', backgroundColor: 'var(--bg-surface)' }}
              >
                否
              </button>
              <button
                onClick={async () => { const ok = await handleSave(); if (ok) blocker.proceed?.() }}
                disabled={saving}
                className="px-3 py-1.5 text-xs rounded-lg font-medium bg-blue-600 text-black hover:bg-blue-500 transition-colors disabled:opacity-50"
              >
                {saving ? '保存中…' : '是'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
