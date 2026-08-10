/**
 * 深度研究设置抽屉
 *
 * 在「深度研究」界面右侧滑出，内嵌与设置页共用的 DeepResearchSettings（6 块），
 * 就地调参、就地保存，免去来回切设置页。打开时拉取完整 config，保存时整份回传
 * （POST /analysis/config 校验整份 AppConfig）。关闭即丢弃未保存改动。
 */
import { useState, useEffect, useRef } from 'react'
import { get, post } from '@/api/client'
import { refreshConfig } from '@/hooks/useConfig'
import { toast } from '@/hooks/useToast'
import { DeepResearchSettings } from '@/features/settings/DeepResearchSettings'
import { DEFAULT_CONFIG } from '@/features/settings/config'
import type { AppConfig } from '@/features/settings/config'
import { LoadingSpinner } from '@/ds-entry'

interface ResearchSettingsDrawerProps {
  open: boolean
  onClose: () => void
}

export function ResearchSettingsDrawer({ open, onClose }: ResearchSettingsDrawerProps) {
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG)
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)
  const drawerRef = useRef<HTMLDivElement>(null)

  // 每次打开都重新拉取，确保与最新已保存配置一致（关闭即丢弃未保存改动）
  useEffect(() => {
    if (!open) return
    setLoaded(false)
    get<AppConfig>('/analysis/config')
      .then(cfg => {
        setConfig({
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
        })
        setLoaded(true)
      })
      .catch(() => setLoaded(true))
  }, [open])

  // Focus close button + Escape handler on open
  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()

    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleEsc)
    return () => document.removeEventListener('keydown', handleEsc)
  }, [open, onClose])

  if (!open) return null

  const handleSave = async () => {
    setSaving(true)
    try {
      await post('/analysis/config', config)
      refreshConfig()
      toast({ type: 'success', message: '研究设置已保存' })
      onClose()
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0" style={{ backgroundColor: 'rgba(0,0,0,0.4)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)' }} />
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        className="relative w-[30rem] max-w-full h-full flex flex-col animate-slide-in-x"
        style={{ backgroundColor: 'var(--bg-surface)', borderLeft: '1px solid var(--border)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <h3 className="text-heading text-[15px]" style={{ color: 'var(--text-primary)' }}>研究设置</h3>
          <button
            ref={closeRef}
            onClick={onClose}
            className="text-[12px] px-3 py-1.5 transition-colors hover:opacity-80"
            style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
          >
            关闭
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {loaded ? (
            <DeepResearchSettings config={config} setConfig={setConfig} />
          ) : (
            <div className="flex items-center justify-center h-32">
              <LoadingSpinner size="sm" />
            </div>
          )}
        </div>

        <div className="px-5 py-3.5 shrink-0 flex items-center gap-3" style={{ borderTop: '1px solid var(--border)' }}>
          <button
            onClick={handleSave}
            disabled={saving || !loaded}
            className="px-4 py-2 text-[13px] font-medium transition-opacity hover:opacity-90 disabled:opacity-50"
            style={{ backgroundColor: 'var(--accent)', color: '#ffffff' }}
          >
            {saving ? '保存中…' : '保存'}
          </button>
          <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>保存后立即生效于下一次研究运行</span>
        </div>
      </div>
    </div>
  )
}
