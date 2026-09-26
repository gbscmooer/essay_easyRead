/**
 * SettingsPage — 设置页
 *
 * Tab 切换式分区：
 *   1. 模型配置 — 翻译模型 / 解读模型 / 解读提示词
 *   2. 界面与主题 — 主题、字体、交互参数
 *   3. 深度研究 — Embedding / 研究源 / Reranker / 相似度 / arXiv / findpapers
 *
 * 顶部 pill 形 segmented control 切换当前分区，只显示对应内容。
 */
import { useState, useEffect, Suspense, useCallback, useMemo } from 'react'
import { post, get } from '@/api/client'
import { refreshConfig, clearUIPreview, setSettingsFormDirty } from '@/hooks/useConfig'
import { toast } from '@/hooks/useToast'
import { PromptEditor } from '@/features/settings/PromptEditor'
import { ConfigFileManagement } from '@/features/settings/ConfigFileManagement'
import LLMProfilesSection from '@/features/settings/LLMProfilesSection'
import TokenUsageSection from '@/features/settings/TokenUsageSection'
import { PROVIDER_PRESETS, type ProviderPreset } from '@/features/settings/ProviderPresets'
import UIConfigSection from '@/features/settings/UIConfigSection'
import {
  EmbeddingSection,
  ResearchProviderSection,
  RerankerSection,
  RelationsSection,
  ArxivExpandSection,
  FindpapersSection,
} from '@/features/settings/DeepResearchSettings'
import type { AppConfig } from '@/features/settings/config'
import { DEFAULT_CONFIG } from '@/features/settings/config'
import { LoadingSpinner } from '@/ds-entry'
import '@/styles/settings.css'


/* ── Tab 定义 ─────────────────────────────────────── */

type TabKey = 'models' | 'appearance' | 'research'

interface TabDef {
  key: TabKey
  label: string
  icon: React.ReactNode
}

const TABS: TabDef[] = [
  {
    key: 'models',
    label: '模型配置',
    icon: (
      <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2L2 7l10 5 10-5-10-5z" />
        <path d="M2 17l10 5 10-5" />
        <path d="M2 12l10 5 10-5" />
      </svg>
    ),
  },
  {
    key: 'appearance',
    label: '界面与主题',
    icon: (
      <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
      </svg>
    ),
  },
  {
    key: 'research',
    label: '深度研究',
    icon: (
      <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="8" />
        <path d="M21 21l-4.35-4.35" />
        <path d="M11 8v6M8 11h6" />
      </svg>
    ),
  },
]


/* ── Segmented Tab Bar ────────────────────────────── */

function TabBar({
  active,
  onChange,
}: {
  active: TabKey
  onChange: (key: TabKey) => void
}) {
  return (
    <div className="er-btn-group" role="tablist">
      {TABS.map(tab => {
        const isActive = tab.key === active
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={isActive}
            data-active={isActive || undefined}
            onClick={() => onChange(tab.key)}
            className="er-btn er-btn--ghost"
          >
            {tab.icon}
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}


/* ── Hooks ─────────────────────────────────────────── */

function LoadingPlaceholder() {
  return (
    <div
      className="flex items-center justify-center h-32"
      style={{
        backgroundColor: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--seed-radius, 12px)',
      }}
    >
      <LoadingSpinner size="sm" />
    </div>
  )
}

function mergeConfig(cfg: AppConfig): AppConfig {
  return {
    ...DEFAULT_CONFIG,
    ...cfg,
    embedding: { ...DEFAULT_CONFIG.embedding, ...(cfg.embedding ?? {}) },
    llm: { ...DEFAULT_CONFIG.llm, ...(cfg.llm ?? {}) },
    analysis_prompt: { ...DEFAULT_CONFIG.analysis_prompt, ...(cfg.analysis_prompt ?? {}) },
    ui: { ...DEFAULT_CONFIG.ui, ...(cfg.ui ?? {}) },
    research: {
      ...DEFAULT_CONFIG.research,
      ...(cfg.research ?? {}),
      recall: {
        ...DEFAULT_CONFIG.research.recall!,
        ...(cfg.research?.recall ?? {}),
        weights: {
          ...DEFAULT_CONFIG.research.recall!.weights,
          ...(cfg.research?.recall?.weights ?? {}),
        },
      },
      hybrid_retrieval: {
        ...DEFAULT_CONFIG.research.hybrid_retrieval!,
        ...(cfg.research?.hybrid_retrieval ?? {}),
        weights: {
          ...DEFAULT_CONFIG.research.hybrid_retrieval!.weights,
          ...(cfg.research?.hybrid_retrieval?.weights ?? {}),
        },
        graph: {
          ...DEFAULT_CONFIG.research.hybrid_retrieval!.graph,
          ...(cfg.research?.hybrid_retrieval?.graph ?? {}),
        },
      },
    },
    reranker: { ...DEFAULT_CONFIG.reranker, ...(cfg.reranker ?? {}) },
    relations: {
      method: cfg.relations?.method ?? DEFAULT_CONFIG.relations.method,
      calibrate: false,
      weights: { ...DEFAULT_CONFIG.relations.weights, ...(cfg.relations?.weights ?? {}) },
    },
    arxiv_expand: { ...DEFAULT_CONFIG.arxiv_expand, ...(cfg.arxiv_expand ?? {}) },
    findpapers: { ...DEFAULT_CONFIG.findpapers, ...(cfg.findpapers ?? {}) },
  }
}

function configSnapshot(config: AppConfig): string {
  return JSON.stringify(config)
}


function useConfigInit() {
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG)
  const [savedConfig, setSavedConfig] = useState<AppConfig>(DEFAULT_CONFIG)
  const [loading, setLoading] = useState(true)

  const reloadConfig = useCallback(() => {
    return get<AppConfig>('/analysis/config')
      .then(cfg => {
        const merged = mergeConfig(cfg)
        setConfig(merged)
        setSavedConfig(merged)
        refreshConfig()
      })
      .catch((error: unknown) => {
        toast({ type: 'error', message: error instanceof Error ? error.message : '加载配置失败' })
      })
  }, [])

  useEffect(() => {
    reloadConfig().finally(() => setLoading(false))
  }, [reloadConfig])

  useEffect(() => {
    return () => {
      setSettingsFormDirty(false)
      clearUIPreview()
    }
  }, [])

  const dirty = useMemo(
    () => configSnapshot(config) !== configSnapshot(savedConfig),
    [config, savedConfig],
  )

  useEffect(() => {
    setSettingsFormDirty(dirty)
    if (!dirty) clearUIPreview()
  }, [dirty])

  useEffect(() => {
    if (!dirty) return
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  return { config, setConfig, setSavedConfig, loading, dirty, reloadConfig }
}


/* ── 各 Tab 面板内容 ─────────────────────────────── */

function ModelsPanel({
  config,
  setConfig,
  reloadConfig,
}: {
  config: AppConfig
  setConfig: React.Dispatch<React.SetStateAction<AppConfig>>
  reloadConfig: () => void
}) {
  const [providerPresets, setProviderPresets] = useState<ProviderPreset[]>(PROVIDER_PRESETS)

  useEffect(() => {
    get<{ providers: ProviderPreset[] }>('/analysis/llm/providers')
      .then(result => {
        if (Array.isArray(result.providers) && result.providers.length > 0) {
          setProviderPresets(result.providers)
        }
      })
      .catch(() => undefined)
  }, [])

  return (
    <div className="space-y-4 stagger">
      <LLMProfilesSection
        providerPresets={providerPresets}
        currentLlm={config.llm}
        onEffectiveChange={reloadConfig}
      />
      <TokenUsageSection />
      <PromptEditor
        value={config.analysis_prompt}
        onChange={ap => setConfig(prev => ({ ...prev, analysis_prompt: ap }))}
      />
    </div>
  )
}

function AppearancePanel({
  config,
  setConfig,
}: {
  config: AppConfig
  setConfig: React.Dispatch<React.SetStateAction<AppConfig>>
}) {
  return (
    <UIConfigSection
      config={config.ui}
      onChange={cfg => setConfig(prev => ({ ...prev, ui: cfg }))}
    />
  )
}

function ResearchPanel({
  config,
  setConfig,
}: {
  config: AppConfig
  setConfig: React.Dispatch<React.SetStateAction<AppConfig>>
}) {
  return (
    <Suspense fallback={<LoadingPlaceholder />}>
      <div className="space-y-4 stagger">
        <EmbeddingSection config={config} setConfig={setConfig} />
        <ResearchProviderSection config={config} setConfig={setConfig} />
        <RerankerSection config={config} setConfig={setConfig} />
        <RelationsSection config={config} setConfig={setConfig} />
        <ArxivExpandSection config={config} setConfig={setConfig} />
        <FindpapersSection config={config} setConfig={setConfig} />
      </div>
    </Suspense>
  )
}


/* ── 主页面 ────────────────────────────────────────── */

export function SettingsPage() {
  const { config, setConfig, setSavedConfig, loading, dirty, reloadConfig } = useConfigInit()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [activeTab, setActiveTab] = useState<TabKey>('models')

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    try {
      await post('/analysis/config', config)
      setSavedConfig(config)
      refreshConfig()
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (error: unknown) {
      toast({ type: 'error', message: error instanceof Error ? error.message : '保存配置失败，请重试' })
    } finally {
      setSaving(false)
    }
  }

  /* 切换 tab 时滚到顶部 */
  const handleTabChange = useCallback((key: TabKey) => {
    setActiveTab(key)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <LoadingSpinner size="sm" />
      </div>
    )
  }

  const llmOk = !!config.llm.api_key.trim()
  const allConfigured = llmOk

  return (
    <div className="er-page er-page--settings">
      <header className="er-page-head">
        <p className="er-page-head__eyebrow">系统设置 · 模型与参数配置</p>
        <h1 className="er-page-head__title">设置</h1>
        <div className="er-page-head__actions">
          <TabBar active={activeTab} onChange={handleTabChange} />
        </div>
      </header>

      {/* 配置文件管理：指示当前文件、支持直接编辑/一键导出/快速导入 */}
      <ConfigFileManagement onConfigReload={reloadConfig} />

      {/* 配置状态提示 — 只在「模型配置」tab 显示 */}
      {!allConfigured && activeTab === 'models' && (
        <div className="set-alert" role="alert">
          <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <div>
            <strong>配置不完整</strong>
            <p>全局 LLM 未配置 API Key，论文精读、划词翻译与领域探索等功能将不可用。</p>
          </div>
        </div>
      )}

      <div className="set-body">
        {activeTab === 'models' && <ModelsPanel config={config} setConfig={setConfig} reloadConfig={reloadConfig} />}
        {activeTab === 'appearance' && <AppearancePanel config={config} setConfig={setConfig} />}
        {activeTab === 'research' && <ResearchPanel config={config} setConfig={setConfig} />}
      </div>

      {/* 保存栏贴在页底。它带偏移块，所以外层要留出影的空间，
          否则影会被粘性栏的上边框切掉。 */}
      <div className="set-savebar">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !dirty}
          aria-busy={saving}
          className="er-btn er-btn--primary er-btn--lg set-savebar__submit"
        >
          {saving && <LoadingSpinner size="xs" />}
          {saving ? '保存中…' : '保存配置'}
        </button>
        <span className="set-savebar__state" role="status" aria-live="polite">
          {saved && (
            <>
              <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M20 6L9 17l-5-5" />
              </svg>
              已保存
            </>
          )}
        </span>
      </div>
    </div>
  )
}

export default SettingsPage
