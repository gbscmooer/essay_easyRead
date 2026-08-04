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
import { useState, useEffect, Suspense, useCallback } from 'react'
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
  RelationsSection,
  ArxivExpandSection,
  FindpapersSection,
} from '@/features/settings/DeepResearchSettings'
import type { AppConfig } from '@/features/settings/config'
import { DEFAULT_CONFIG } from '@/features/settings/config'
import { LoadingSpinner } from '@/ds-entry'


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
            <span style={{ opacity: isActive ? 1 : 0.6 }}>{tab.icon}</span>
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


function useConfigInit() {
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG)
  const [loading, setLoading] = useState(true)

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
        },
        reranker: { ...DEFAULT_CONFIG.reranker, ...(cfg.reranker ?? {}) },
        relations: {
          calibrate: cfg.relations?.calibrate ?? DEFAULT_CONFIG.relations.calibrate,
          weights: { ...DEFAULT_CONFIG.relations.weights, ...(cfg.relations?.weights ?? {}) },
        },
        arxiv_expand: { ...DEFAULT_CONFIG.arxiv_expand, ...(cfg.arxiv_expand ?? {}) },
        findpapers: { ...DEFAULT_CONFIG.findpapers, ...(cfg.findpapers ?? {}) },
      }))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    return () => { clearUIPreview() }
  }, [])

  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (hasUnsavedConfig()) {
        e.preventDefault()
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  return { config, setConfig, loading }
}


/* ── 各 Tab 面板内容 ─────────────────────────────── */

function ModelsPanel({
  config,
  setConfig,
}: {
  config: AppConfig
  setConfig: React.Dispatch<React.SetStateAction<AppConfig>>
}) {
  return (
    <div className="space-y-4 stagger">
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
  const { config, setConfig, loading } = useConfigInit()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [activeTab, setActiveTab] = useState<TabKey>('models')

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

  const translateOk = !!config.translate.api_key.trim()
  const analysisOk = !!config.analysis.api_key.trim()
  const allConfigured = translateOk && analysisOk

  return (
    <div className="er-page er-page--settings max-w-2xl mx-auto px-8 py-10">
      {/* Header + Tab Bar */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-display text-[28px]" style={{ color: 'var(--text-primary)' }}>
          设置
        </h1>
        <TabBar active={activeTab} onChange={handleTabChange} />
      </div>

      {/* 配置状态提示 — 只在"模型配置" tab 显示 */}
      {!allConfigured && activeTab === 'models' && (
        <div
          className="p-4 mb-5"
          style={{
            borderRadius: 'var(--seed-radius, 12px)',
            backgroundColor: 'var(--error-subtle)',
            border: '1px solid color-mix(in srgb, var(--error) 30%, transparent)',
          }}
        >
          <div className="flex items-start gap-3">
            <span style={{ color: 'var(--error)' }}>
              <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} strokeLinecap="round">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </span>
            <div>
              <p className="text-[13px] font-medium" style={{ color: 'var(--error)' }}>配置不完整</p>
              <div className="text-[12px] mt-1.5 space-y-0.5" style={{ color: 'var(--error)', opacity: 0.85 }}>
                {!translateOk && <p>翻译模型未配置 API Key，画词翻译功能将不可用</p>}
                {!analysisOk && <p>解读模型未配置 API Key，论文分析功能将使用默认配置</p>}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Tab 面板内容 */}
      {activeTab === 'models' && (
        <ModelsPanel config={config} setConfig={setConfig} />
      )}
      {activeTab === 'appearance' && (
        <AppearancePanel config={config} setConfig={setConfig} />
      )}
      {activeTab === 'research' && (
        <ResearchPanel config={config} setConfig={setConfig} />
      )}

      {/* 保存按钮 — 始终固定在底部 */}
      <div className="flex items-center gap-3 pt-6">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          aria-busy={saving}
          className="er-btn er-btn--primary er-btn--lg"
        >
          {saving && <LoadingSpinner size="xs" />}
          {saving ? '保存中…' : '保存配置'}
        </button>
        {saved && (
          <span
            className="animate-reveal-scale inline-flex items-center gap-1.5 text-[13px] font-medium"
            style={{ color: 'var(--success)' }}
            role="status"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M20 6L9 17l-5-5" />
            </svg>
            已保存
          </span>
        )}
      </div>
    </div>
  )
}

export default SettingsPage
