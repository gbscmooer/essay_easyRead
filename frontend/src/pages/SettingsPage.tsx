import { useState, useEffect, useCallback } from 'react'
import { post, get } from '@/api/client'
import { refreshConfig, setUIPreview, clearUIPreview, hasUnsavedConfig } from '@/hooks/useConfig'
import { ThemeEditor } from '@/features/settings/ThemeEditor'
import { PromptEditor } from '@/features/settings/PromptEditor'
import { GrobidPanel } from '@/features/settings/GrobidPanel'
import type { Theme } from '@/theme/themes'


interface TestResult {
  ok: boolean
  message: string
  latency_ms: number | null
}


/** 测试按钮：点击后调用后端 test-connection，显示结果 */
function TestButton({
  type,
  api_key,
  base_url,
  model,
  provider,
}: {
  type: 'translate' | 'analysis' | 'embedding'
  api_key?: string
  base_url?: string
  model?: string
  provider?: string
}) {
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<TestResult | null>(null)
  const [showError, setShowError] = useState(false)

  const handleTest = useCallback(async () => {
    setTesting(true)
    setResult(null)
    setShowError(false)
    try {
      const res = await post<TestResult>('/analysis/test-connection', {
        type,
        api_key: api_key ?? '',
        base_url: base_url ?? '',
        model: model ?? '',
        provider: provider ?? '',
      })
      setResult(res)
    } catch (e: any) {
      setResult({ ok: false, message: e?.message || '请求失败', latency_ms: null })
    } finally {
      setTesting(false)
    }
  }, [type, api_key, base_url, model, provider])

  return (
    <div className="flex items-center gap-2 relative">
      <button
        onClick={() => {
          if (result && !result.ok) {
            setShowError(v => !v)
          } else {
            handleTest()
          }
        }}
        disabled={testing}
        className="px-3 py-1 text-xs rounded-lg border transition-colors disabled:opacity-50"
          style={result === null ? { borderColor: 'var(--border-light)', color: 'var(--text-secondary)' } : result.ok ? { borderColor: 'var(--success)', color: 'var(--success)', backgroundColor: 'var(--success-subtle)' } : { borderColor: 'var(--error)', color: 'var(--error)', backgroundColor: 'var(--error-subtle)', cursor: 'pointer' }}
      >
        {testing ? (
          <span className="flex items-center gap-1.5">
            <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
            </svg>
            测试中
          </span>
        ) : result?.ok
          ? `测试通过${result.latency_ms != null ? ` (${result.latency_ms}ms)` : ''}`
          : result
            ? '测试失败'
            : '测试'}
      </button>

      {/* 失败时点击按钮弹出错误详情 */}
      {showError && result && !result.ok && (
        <>
          {/* 遮罩 */}
          <div
            className="fixed inset-0 z-50"
            onClick={() => setShowError(false)}
          />
          {/* 弹窗 */}
          <div
            className="absolute right-0 top-full mt-2 z-50 w-80 rounded-xl p-4 border"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              borderColor: 'var(--border)',
            }}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-red-400">连接失败</span>
              <button
                onClick={() => setShowError(false)}
                className="text-xs hover:opacity-80"
                style={{ color: 'var(--text-muted)' }}
              >
                &times;
              </button>
            </div>
            <p className="text-xs leading-relaxed break-all" style={{ color: 'var(--text-secondary)' }}>
              {result.message}
            </p>
            {result.latency_ms != null && (
              <p className="text-[11px] mt-2" style={{ color: 'var(--text-muted)' }}>
                耗时 {result.latency_ms}ms
              </p>
            )}
            <button
              onClick={() => { setShowError(false); handleTest() }}
              className="mt-3 px-3 py-1 text-xs rounded-lg border transition-colors"
              style={{
                borderColor: 'var(--border)',
                color: 'var(--text-secondary)',
                backgroundColor: 'var(--bg-surface)',
              }}
            >
              重试
            </button>
          </div>
        </>
      )}
    </div>
  )
}interface AIModelConfig {
  api_key: string
  base_url: string
  model: string
  system_prompt: string
  temperature: number
  max_tokens: number
}

interface UIConfig {
  highlight_duration: number
  zoom_step: number
  zoom_min: number
  zoom_max: number
  pinch_zoom_step: number
  theme: string
  font: string
  custom_font: string
  custom_themes?: Theme[]
}

interface EmbeddingConfig {
  provider: 'local' | 'openai' | 'siliconflow'
  model: string
  api_key: string
  base_url: string
  batch_size: number
  device: string
}

interface ResearchConfig {
  provider: 'openalex' | 'semantic_scholar' | 'local'
  email: string
  api_key: string
  similarity_threshold: number
}

interface RerankerConfig {
  enabled: boolean
  provider: 'siliconflow' | 'custom'
  model: string
  api_key: string
  base_url: string
  candidate_k: number
  top_n: number
}

interface SimilarityConfig {
  provider: string
}

interface ArxivExpandConfig {
  enabled: boolean
  parser: 'grobid' | 'regex'
  grobid_url: string
  top_k: number
  use_llm_title: boolean
  max_llm_refs: number
}

interface PromptTemplate {
  id: string
  name: string
  content: string
}

interface AnalysisPromptConfig {
  active: string            // builtin | custom:<id>
  custom: PromptTemplate[]
}

interface FindpapersConfig {
  enabled: boolean
  databases: string[]
  max_results: number
}

interface AppConfig {
  translate: AIModelConfig
  analysis: AIModelConfig
  analysis_prompt: AnalysisPromptConfig
  ui: UIConfig
  embedding: EmbeddingConfig
  research: ResearchConfig
  reranker: RerankerConfig
  similarity: SimilarityConfig
  arxiv_expand: ArxivExpandConfig
  findpapers: FindpapersConfig
}


const DEFAULT_CONFIG: AppConfig = {
  translate: {
    api_key: '',
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    system_prompt: '',
    temperature: 0.3,
    max_tokens: 2048,
  },
  analysis: {
    api_key: '',
    base_url: 'https://token-plan-cn.xiaomimimo.com/anthropic',
    model: 'mimo-v2.5-pro',
    system_prompt: '',
    temperature: 0.1,
    max_tokens: 16384,
  },
  analysis_prompt: {
    active: 'builtin',
    custom: [],
  },
  ui: {
    highlight_duration: 3000,
    zoom_step: 0.2,
    zoom_min: 0.4,
    zoom_max: 3.0,
    pinch_zoom_step: 0.02,
    theme: 'light',
    font: 'sans',
    custom_font: '',
    custom_themes: [],
  },
  embedding: {
    provider: 'local',
    model: 'BAAI/bge-m3',
    api_key: '',
    base_url: 'https://api.openai.com/v1',
    batch_size: 32,
    device: 'auto',
  },
  research: {
    provider: 'openalex',
    email: '',
    api_key: '',
    similarity_threshold: 0.9,
  },
  reranker: {
    enabled: true,
    provider: 'siliconflow',
    model: 'BAAI/bge-reranker-v2-m3',
    api_key: '',
    base_url: '',
    candidate_k: 30,
    top_n: 5,
  },
  similarity: {
    provider: 'bge_centroid',
  },
  arxiv_expand: {
    enabled: true,
    parser: 'grobid',
    grobid_url: 'http://localhost:8070',
    top_k: 5,
    use_llm_title: true,
    max_llm_refs: 15,
  },
  findpapers: {
    enabled: false,
    databases: ['arxiv', 'openalex', 'pubmed', 'semantic_scholar'],
    max_results: 10,
  },
}


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
    <div className="rounded-xl border p-5 space-y-4"
      style={{ backgroundColor: 'var(--bg-surface)', borderColor: keyMissing ? 'var(--error)' : 'var(--border)' }}>
      <div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>{title}</h2>
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
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{description}</p>
      </div>

      {/* 快捷选择 */}
      <div>
        <label className="block text-xs mb-1.5" style={{ color: 'var(--text-muted)' }}>服务商预设</label>
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
              className="px-3 py-1 text-xs rounded-full border transition-colors"
              style={config.base_url === preset.base_url ? { borderColor: 'var(--accent)', backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' } : { borderColor: 'var(--border-light)', color: 'var(--text-secondary)' }}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>

      {/* Base URL */}
      <div>
        <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>API Base URL</label>
        <input
          type="text"
          value={config.base_url}
          onChange={e => onChange({ ...config, base_url: e.target.value })}
          placeholder="https://api.openai.com/v1"
          className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
        />
      </div>

      {/* API Key */}
      <div>
        <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>API Key</label>
        <div className="relative">
          <input
            type={showKey ? 'text' : 'password'}
            value={config.api_key}
            onChange={e => onChange({ ...config, api_key: e.target.value })}
            placeholder="sk-..."
            className="w-full px-3 py-2 pr-10 text-sm rounded-lg focus:outline-none transition-colors font-mono"
            style={{ backgroundColor: 'var(--bg-elevated)', border: `1px solid ${keyMissing ? 'var(--error)' : 'var(--border-light)'}`, color: 'var(--text-primary)' }}
          />
          <button
            onClick={() => setShowKey(v => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-xs"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
          >
            {showKey ? '隐藏' : '显示'}
          </button>
        </div>
      </div>

      {/* Model */}
      <div>
        <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>模型名称</label>
        <input
          type="text"
          value={config.model}
          onChange={e => onChange({ ...config, model: e.target.value })}
          placeholder="gpt-4o-mini"
          className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
        />
      </div>

      {/* 高级参数（折叠） */}
      <div>
        <button
          onClick={() => setShowAdvanced(v => !v)}
          className="flex items-center gap-1 text-xs transition-colors"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
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
          <div className="mt-3 space-y-3 pl-2 border-l" style={{ borderColor: 'var(--border)' }}>
            {/* Temperature */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs" style={{ color: 'var(--text-muted)' }}>Temperature</label>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{config.temperature}</span>
              </div>
              <input
                type="range"
                min={0}
                max={2}
                step={0.05}
                value={config.temperature}
                onChange={e => onChange({ ...config, temperature: parseFloat(e.target.value) })}
                className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-blue-500"
                style={{ backgroundColor: 'var(--border)' }}
              />
              <div className="flex justify-between text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
                <span>精确 (0)</span>
                <span>平衡 (1)</span>
                <span>随机 (2)</span>
              </div>
            </div>

            {/* Max Tokens */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs" style={{ color: 'var(--text-muted)' }}>最大输出 Token 数</label>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{config.max_tokens}</span>
              </div>
              <input
                type="range"
                min={512}
                max={32768}
                step={512}
                value={config.max_tokens}
                onChange={e => onChange({ ...config, max_tokens: parseInt(e.target.value) })}
                className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-blue-500"
                style={{ backgroundColor: 'var(--border)' }}
              />
              <div className="flex justify-between text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
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
    <div className="rounded-xl border p-5 space-y-4" style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
      <div>
        <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>界面设置</h2>
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>主题、字体和交互行为参数</p>
      </div>

      {/* 主题选择 + 自定义编辑器 */}
      <ThemeEditor config={config} onChange={onChange} />

      {/* 字体选择 */}
      <div>
        <label className="block text-xs mb-2" style={{ color: 'var(--text-muted)' }}>界面字体</label>
        <div className="space-y-1.5">
          {FONTS.map(font => (
            <button
              key={font.key}
              onClick={() => { onChange({ ...config, font: font.key }); setUIPreview({ font: font.key }) }}
              className="w-full text-left px-3 py-1.5 text-sm rounded border transition-colors"
              style={config.font === font.key ? { borderColor: 'var(--accent)', backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' } : { borderColor: 'var(--border-light)', color: 'var(--text-secondary)' }}
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
            className="mt-2 w-full px-3 py-1.5 text-sm rounded focus:outline-none"
            style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
          />
        )}
        <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>仅作用于界面文字，不影响源码展示</p>
      </div>

      {/* 大纲跳转高亮时长 */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs" style={{ color: 'var(--text-muted)' }}>大纲跳转高亮时长</label>
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{(config.highlight_duration / 1000).toFixed(1)}s</span>
        </div>
        <input
          type="range"
          min={500}
          max={10000}
          step={500}
          value={config.highlight_duration}
          onChange={e => onChange({ ...config, highlight_duration: parseInt(e.target.value) })}
          className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-blue-500"
                style={{ backgroundColor: 'var(--border)' }}
        />
        <div className="flex justify-between text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
          <span>0.5s</span>
          <span>默认 3s</span>
          <span>10s</span>
        </div>
      </div>

      {/* PDF 缩放参数（折叠） */}
      <div>
        <button
          onClick={() => setShowAdvanced(v => !v)}
          className="flex items-center gap-1 text-xs transition-colors"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
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
          <div className="mt-3 space-y-3 pl-2 border-l" style={{ borderColor: 'var(--border)' }}>
            {/* Zoom Step */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs" style={{ color: 'var(--text-muted)' }}>每次缩放步进</label>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{Math.round(config.zoom_step * 100)}%</span>
              </div>
              <input
                type="range"
                min={5}
                max={50}
                step={5}
                value={Math.round(config.zoom_step * 100)}
                onChange={e => onChange({ ...config, zoom_step: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-blue-500"
                style={{ backgroundColor: 'var(--border)' }}
              />
            </div>

            {/* Zoom Min */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs" style={{ color: 'var(--text-muted)' }}>最小缩放倍率</label>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{Math.round(config.zoom_min * 100)}%</span>
              </div>
              <input
                type="range"
                min={10}
                max={100}
                step={10}
                value={Math.round(config.zoom_min * 100)}
                onChange={e => onChange({ ...config, zoom_min: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-blue-500"
                style={{ backgroundColor: 'var(--border)' }}
              />
            </div>

            {/* Zoom Max */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs" style={{ color: 'var(--text-muted)' }}>最大缩放倍率</label>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{Math.round(config.zoom_max * 100)}%</span>
              </div>
              <input
                type="range"
                min={100}
                max={500}
                step={50}
                value={Math.round(config.zoom_max * 100)}
                onChange={e => onChange({ ...config, zoom_max: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-blue-500"
                style={{ backgroundColor: 'var(--border)' }}
              />
            </div>

            {/* Pinch Zoom Step */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs" style={{ color: 'var(--text-muted)' }}>触控板捏合灵敏度</label>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{Math.round(config.pinch_zoom_step * 100)}%</span>
              </div>
              <input
                type="range"
                min={1}
                max={10}
                step={1}
                value={Math.round(config.pinch_zoom_step * 100)}
                onChange={e => onChange({ ...config, pinch_zoom_step: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-blue-500"
                style={{ backgroundColor: 'var(--border)' }}
              />
              <div className="flex justify-between text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
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

  useEffect(() => {
    get<AppConfig>('/analysis/config')
      .then(cfg => setConfig({
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
      }))
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

  // 浏览器关闭/刷新提示
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (hasUnsavedConfig()) {
        e.preventDefault()
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    try {
      await post('/analysis/config', config)
      refreshConfig()
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
        <div className="w-6 h-6 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }} />
      </div>
    )
  }

  const translateOk = !!config.translate.api_key.trim()
  const analysisOk = !!config.analysis.api_key.trim()
  const allConfigured = translateOk && analysisOk

  return (
    <div className="max-w-2xl mx-auto px-6 py-8 space-y-6">
      <div>
        <h1 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>设置</h1>
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

      {/* Embedding 配置 */}
      <div className="rounded-xl border p-5 space-y-4" style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>Embedding 模型</h2>
            <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>用于深度研究的向量嵌入模型。本地模式首次运行需下载模型（~2GB）。</p>
          </div>
          <TestButton
            type="embedding"
            api_key={config.embedding.api_key}
            base_url={config.embedding.base_url}
            model={config.embedding.model}
            provider={config.embedding.provider}
          />
        </div>

        {/* Provider 选择 */}
        <div>
          <label className="block text-xs mb-2" style={{ color: 'var(--text-muted)' }}>Provider</label>
          <div className="flex gap-2">
            {([
              { key: 'local' as const, label: '本地 (BGE-M3)' },
              { key: 'siliconflow' as const, label: '硅基流动' },
              { key: 'openai' as const, label: 'OpenAI' },
            ]).map(p => (
              <button
                key={p.key}
                onClick={() => setConfig(prev => {
                  const defaults: Record<string, Partial<EmbeddingConfig>> = {
                    local: { model: 'BAAI/bge-m3', base_url: 'https://api.openai.com/v1' },
                    siliconflow: { model: 'BAAI/bge-m3', base_url: 'https://api.siliconflow.cn/v1' },
                    openai: { model: 'text-embedding-3-small', base_url: 'https://api.openai.com/v1' },
                  }
                  return { ...prev, embedding: { ...prev.embedding, provider: p.key, ...defaults[p.key] } }
                })}
                className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                  config.embedding.provider === p.key
                    ? 'border-blue-500 bg-blue-500/10 text-blue-400'
                    : { borderColor: 'var(--border-light)', color: 'var(--text-secondary)' }
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* 模型名 */}
        <div>
          <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>模型名称</label>
          <input
            type="text"
            value={config.embedding.model}
            onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, model: e.target.value } }))}
            placeholder={config.embedding.provider === 'local' ? 'BAAI/bge-m3' : config.embedding.provider === 'siliconflow' ? 'BAAI/bge-m3' : 'text-embedding-3-small'}
            className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
          />
        </div>

        {/* OpenAI / SiliconFlow 专用配置 */}
        {(config.embedding.provider === 'openai' || config.embedding.provider === 'siliconflow') && (
          <>
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>API Base URL</label>
              <input
                type="text"
                value={config.embedding.base_url}
                onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, base_url: e.target.value } }))}
                className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
              />
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>API Key</label>
              <input
                type="password"
                value={config.embedding.api_key}
                onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, api_key: e.target.value } }))}
                placeholder="sk-..."
                className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors font-mono"
                style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
              />
            </div>
          </>
        )}

        {config.embedding.provider === 'local' && (
          <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            本地模式使用 sentence-transformers 加载模型，首次运行会自动下载。切换 provider 后建议重建索引以避免向量维度不匹配。
          </p>
        )}
      </div>

      {/* 深度研究 Provider */}
      <div className="rounded-xl border p-5 space-y-4" style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
        <div>
          <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>深度研究 — 论文扩展源</h2>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
            决定"联网扩展"阶段从哪里抓取参考文献。本地模式表示不联网，只用工作区内已上传的论文构建图谱。
          </p>
        </div>

        <div>
          <label className="block text-xs mb-2" style={{ color: 'var(--text-muted)' }}>Provider</label>
          <div className="flex gap-2 flex-wrap">
            {([
              { key: 'openalex' as const, label: 'OpenAlex（推荐）' },
              { key: 'semantic_scholar' as const, label: 'Semantic Scholar' },
              { key: 'local' as const, label: '本地（不联网）' },
            ]).map(p => (
              <button
                key={p.key}
                onClick={() => setConfig(prev => ({
                  ...prev,
                  research: { ...prev.research, provider: p.key },
                }))}
                className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                  config.research.provider === p.key
                    ? 'border-blue-500 bg-blue-500/10 text-blue-400'
                    : { borderColor: 'var(--border-light)', color: 'var(--text-secondary)' }
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {config.research.provider === 'openalex' && (
          <>
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                邮箱（可选 · polite pool）
              </label>
              <input
                type="email"
                value={config.research.email}
                onChange={e => setConfig(prev => ({
                  ...prev,
                  research: { ...prev.research, email: e.target.value },
                }))}
                placeholder="you@example.com"
                className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
              />
              <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
                填入邮箱可进入 OpenAlex polite pool，获得更稳定的速率配额。不填也能用。
              </p>
            </div>
          </>
        )}

        {config.research.provider === 'semantic_scholar' && (
          <>
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                API Key（强烈建议填写）
              </label>
              <input
                type="password"
                value={config.research.api_key}
                onChange={e => setConfig(prev => ({
                  ...prev,
                  research: { ...prev.research, api_key: e.target.value },
                }))}
                placeholder="留空使用公共配额（容易 429）"
                className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors font-mono"
                style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
              />
              <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
                申请地址：
                <a
                  href="https://www.semanticscholar.org/product/api"
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-400 hover:underline ml-1"
                >
                  semanticscholar.org/product/api
                </a>
                （免费，约 1–2 天通过）
              </p>
            </div>
          </>
        )}

        {config.research.provider === 'local' && (
          <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            本地模式下，"联网扩展"阶段会被直接跳过；图谱仅基于工作区已上传的论文构建。
          </p>
        )}

        {/* 相似度阈值 */}
        <div className="pt-2 border-t" style={{ borderColor: 'var(--border)' }}>
          <div className="flex items-center justify-between mb-1">
            <div>
              <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>默认相似度阈值</span>
              <p className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
                两篇论文向量余弦相似度超过此值时图谱连一条「相似」边。值越大边越少越精炼。可在每次开始研究时临时调整。
              </p>
            </div>
            <span className="text-xs font-mono ml-4 shrink-0" style={{ color: 'var(--text-secondary)' }}>
              {config.research.similarity_threshold.toFixed(2)}
            </span>
          </div>
          <input
            type="range"
            min={0.5}
            max={0.95}
            step={0.01}
            value={config.research.similarity_threshold}
            onChange={e => setConfig(prev => ({
              ...prev,
              research: { ...prev.research, similarity_threshold: Number(e.target.value) },
            }))}
            className="w-full h-1 accent-blue-500"
          />
          <div className="flex justify-between text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
            <span>0.50 宽松</span>
            <span>0.95 严格</span>
          </div>
        </div>
      </div>

      {/* 重排序模型 */}
      <div className="rounded-xl border p-5 space-y-4" style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>重排序模型（Reranker）</h2>
            <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
              在 RAG 跨论文问答时使用。向量检索召回 candidate_k 条候选，再由重排序模型按语义重排，最终取 top_n 喂给 LLM。提升答非所问的概率。
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs cursor-pointer" style={{ color: 'var(--text-secondary)' }}>
            <input
              type="checkbox"
              checked={config.reranker.enabled}
              onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, enabled: e.target.checked } }))}
              className="accent-blue-500"
            />
            启用
          </label>
        </div>

        {config.reranker.enabled && (
          <>
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>模型</label>
              <input
                type="text"
                value={config.reranker.model}
                onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, model: e.target.value } }))}
                placeholder="BAAI/bge-reranker-v2-m3"
                className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
              />
              <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>默认 BAAI/bge-reranker-v2-m3（硅基流动）</p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>API Base URL</label>
                <input
                  type="text"
                  value={config.reranker.base_url}
                  onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, base_url: e.target.value } }))}
                  placeholder="留空 = 继承 embedding 的 base_url"
                  className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
                />
              </div>
              <div>
                <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>API Key</label>
                <input
                  type="password"
                  value={config.reranker.api_key}
                  onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, api_key: e.target.value } }))}
                  placeholder="留空 = 继承 embedding 的 api_key"
                  className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors font-mono"
                style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>candidate_k（召回数）</label>
                <input
                  type="number"
                  min={5}
                  max={200}
                  value={config.reranker.candidate_k}
                  onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, candidate_k: Number(e.target.value) || 30 } }))}
                  className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
                />
                <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>向量检索的初筛数量，越大重排越充分但越慢</p>
              </div>
              <div>
                <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>top_n（送 LLM）</label>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={config.reranker.top_n}
                  onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, top_n: Number(e.target.value) || 5 } }))}
                  className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
                />
                <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>重排序后保留的最终 chunk 数</p>
              </div>
            </div>
          </>
        )}
      </div>

      {/* 论文级相似度 Provider */}
      <div className="rounded-xl border p-5 space-y-4" style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
        <div>
          <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>论文级相似度计算（Similarity Provider）</h2>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
            深度研究知识图谱中"相似"边的计算方式。可插拔接口，可在 <code className="px-1 rounded" style={{ backgroundColor: 'var(--bg-elevated)' }}>backend/services/deep_research/similarity/</code> 自行新增 provider 并在 REGISTRY 注册。
          </p>
        </div>

        <div>
          <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>Provider</label>
          <select
            value={config.similarity.provider}
            onChange={e => setConfig(prev => ({ ...prev, similarity: { ...prev.similarity, provider: e.target.value } }))}
            className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
          >
            {simProviders.map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
            当前默认 <code className="px-1 rounded" style={{ backgroundColor: 'var(--bg-elevated)' }}>bge_centroid</code>：复用 BGE chunk 向量取归一化均值后算余弦。
            适合粗略相似度；细分方向区分有限。
          </p>
          <div className="mt-3 px-3 py-2 rounded-lg border" style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-light)' }}>
            <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              <span className="font-medium" style={{ color: 'var(--text-secondary)' }}>未来可扩展（已在 base.py 列出 TODO）：</span><br />
              ·  <span style={{ color: 'var(--text-secondary)' }}>specter2 / scincl</span> —— 科研论文专用 embedding，用引用对训练，区分研究主题更准<br />
              ·  <span style={{ color: 'var(--text-secondary)' }}>bibcoupling</span> —— 两篇论文共享引用的 Jaccard，捕获"被同一领域引用"<br />
              ·  <span style={{ color: 'var(--text-secondary)' }}>hybrid</span> —— 多信号融合<br />
              ·  <span style={{ color: 'var(--text-secondary)' }}>cross_encoder</span> —— 复用 BAAI/bge-reranker-v2-m3 当 cross-encoder
            </p>
          </div>
        </div>
      </div>

      {/* arXiv 引用列表扩展 */}
      <div className="rounded-xl border p-5 space-y-4" style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>arXiv 引用列表扩展</h2>
            <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
              联网模式下，从 L1 论文 PDF 文本解析 <code className="px-1 rounded" style={{ backgroundColor: 'var(--bg-elevated)' }}>[N]</code> 引用块，
              提取 arXiv ID（正则优先，可选 LLM 兜底），从 arXiv 下载 PDF。
              与 OpenAlex/S2 扩展互补。
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs cursor-pointer shrink-0 ml-4" style={{ color: 'var(--text-secondary)' }}>
            <input
              type="checkbox"
              checked={config.arxiv_expand.enabled}
              onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, enabled: e.target.checked } }))}
              className="accent-blue-500"
            />
            启用
          </label>
        </div>

        {config.arxiv_expand.enabled && (
          <>
            <GrobidPanel
              parser={config.arxiv_expand.parser}
              grobidUrl={config.arxiv_expand.grobid_url}
              onChangeParser={p => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, parser: p } }))}
              onChangeUrl={u => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, grobid_url: u } }))}
            />

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>Top-K（按引用数取前 K 篇）</label>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={config.arxiv_expand.top_k}
                  onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, top_k: Number(e.target.value) || 5 } }))}
                  className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
                />
                <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>从候选论文中取前 K 篇（引用列表越靠前越重要）</p>
              </div>
            </div>

            <div className="flex items-start gap-4">
              <label className="flex items-center gap-2 text-xs cursor-pointer" style={{ color: 'var(--text-secondary)' }}>
                <input
                  type="checkbox"
                  checked={config.arxiv_expand.use_llm_title}
                  onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, use_llm_title: e.target.checked } }))}
                  className="accent-blue-500"
                />
                LLM 标题兜底
              </label>
              {config.arxiv_expand.use_llm_title && (
                <div className="flex-1">
                  <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                    每篇 seed 最多 LLM+arXiv 搜索次数
                  </label>
                  <input
                    type="number"
                    min={0}
                    max={50}
                    value={config.arxiv_expand.max_llm_refs}
                    onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, max_llm_refs: Number(e.target.value) || 15 } }))}
                    className="w-48 px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
                    style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
                  />
                  <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
                    对无 arXiv ID 的引用，调用解读模型提取标题再搜索（消耗 Token）。
                    每次 arXiv 搜索间隔 3 秒，过多会显著增加运行时间。
                  </p>
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* findpapers 多源学术搜索 */}
      <div className="rounded-xl border p-5 space-y-4" style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>findpapers 多源搜索</h2>
            <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
              启用后使用 findpapers 库（arXiv、CrossRef、OpenAlex、PubMed 等 8 源并行搜索 + 自动去重）
              替代 Semantic Scholar 进行相关论文推荐和 PDF 下载。
              需要 <code className="px-1 rounded" style={{ backgroundColor: 'var(--bg-elevated)' }}>pip install findpapers</code>。
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs cursor-pointer shrink-0 ml-4" style={{ color: 'var(--text-secondary)' }}>
            <input
              type="checkbox"
              checked={config.findpapers.enabled}
              onChange={e => setConfig(prev => ({ ...prev, findpapers: { ...prev.findpapers, enabled: e.target.checked } }))}
              className="accent-blue-500"
            />
            启用
          </label>
        </div>

        {config.findpapers.enabled && (
          <>
            <div>
              <label className="block text-xs mb-2" style={{ color: 'var(--text-muted)' }}>启用的数据库</label>
              <div className="flex flex-wrap gap-2">
                {[
                  { key: 'arxiv', label: 'arXiv', free: true },
                  { key: 'openalex', label: 'OpenAlex', free: true },
                  { key: 'pubmed', label: 'PubMed', free: true },
                  { key: 'semantic_scholar', label: 'Semantic Scholar', free: true },
                  { key: 'ieee', label: 'IEEE Xplore', free: false },
                  { key: 'scopus', label: 'Scopus', free: false },
                  { key: 'wos', label: 'Web of Science', free: false },
                ].map(db => (
                  <label key={db.key} className="flex items-center gap-1.5 text-xs cursor-pointer" style={{ color: 'var(--text-secondary)' }}>
                    <input
                      type="checkbox"
                      checked={config.findpapers.databases.includes(db.key)}
                      onChange={e => {
                        const dbs = e.target.checked
                          ? [...config.findpapers.databases, db.key]
                          : config.findpapers.databases.filter(d => d !== db.key)
                        setConfig(prev => ({ ...prev, findpapers: { ...prev.findpapers, databases: dbs } }))
                      }}
                      className="accent-blue-500"
                    />
                    {db.label}
                    {!db.free && <span className="text-[10px] text-amber-500">(需 key)</span>}
                  </label>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs mb-1" style={{ color: 'var(--text-muted)' }}>搜索结果数量上限</label>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={config.findpapers.max_results}
                  onChange={e => setConfig(prev => ({ ...prev, findpapers: { ...prev.findpapers, max_results: Number(e.target.value) || 10 } }))}
                  className="w-full px-3 py-2 text-sm rounded-lg focus:outline-none transition-colors"
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
                />
              </div>
            </div>
          </>
        )}
      </div>

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
