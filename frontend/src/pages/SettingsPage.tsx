/**
 * 设置页面
 *
 * 管理配置状态、加载/保存逻辑，渲染各设置区块组件。
 * Deep Research 相关区块通过 Suspense 延迟加载。
 */
import { useState, useEffect, Suspense } from 'react'
import { post, get } from '@/api/client'
import { refreshConfig, clearUIPreview, hasUnsavedConfig } from '@/hooks/useConfig'
import { PromptEditor } from '@/features/settings/PromptEditor'
import { TestButton } from '@/features/settings/TestButton'
import AIModelSection from '@/features/settings/AIModelSection'
import UIConfigSection from '@/features/settings/UIConfigSection'
import {
  EmbeddingSection,
  ResearchProviderSection,
  RerankerSection,
  SimilaritySection,
  ArxivExpandSection,
  FindpapersSection,
} from '@/features/settings/DeepResearchSettings'
import type { AppConfig } from '@/features/settings/config'
import { DEFAULT_CONFIG } from '@/features/settings/config'


function LoadingPlaceholder() {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 flex items-center justify-center h-32">
      <div className="w-5 h-5 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
    </div>
  )
}


function useConfigInit() {
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG)
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

  return { config, setConfig, loading, simProviders }
}


export function SettingsPage() {
  const { config, setConfig, loading, simProviders } = useConfigInit()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

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

      <AIModelSection
        title="翻译模型"
        description="用于选中文本的翻译功能。"
        config={config.translate}
        onChange={cfg => setConfig(prev => ({ ...prev, translate: cfg }))}
        defaultMaxTokens={2048}
        testSlot={
          <TestButton
            type="translate"
            api_key={config.translate.api_key}
            base_url={config.translate.base_url}
            model={config.translate.model}
          />
        }
      />

      <AIModelSection
        title="解读模型"
        description="用于论文解析、总结等功能。"
        config={config.analysis}
        onChange={cfg => setConfig(prev => ({ ...prev, analysis: cfg }))}
        defaultMaxTokens={16384}
        testSlot={
          <TestButton
            type="analysis"
            api_key={config.analysis.api_key}
            base_url={config.analysis.base_url}
            model={config.analysis.model}
          />
        }
      />

      <PromptEditor
        value={config.analysis_prompt}
        onChange={ap => setConfig(prev => ({ ...prev, analysis_prompt: ap }))}
      />

      <UIConfigSection
        config={config.ui}
        onChange={cfg => setConfig(prev => ({ ...prev, ui: cfg }))}
      />

      <Suspense fallback={<LoadingPlaceholder />}>
        <EmbeddingSection config={config} setConfig={setConfig} />
        <ResearchProviderSection config={config} setConfig={setConfig} />
        <RerankerSection config={config} setConfig={setConfig} />
        <SimilaritySection config={config} setConfig={setConfig} simProviders={simProviders} />
        <ArxivExpandSection config={config} setConfig={setConfig} />
        <FindpapersSection config={config} setConfig={setConfig} />
      </Suspense>

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

export default SettingsPage
