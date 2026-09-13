/**
 * 全局 LLM 模型 · 配置档案区块
 *
 * 全局大模型配置的唯一入口（CC Switch 式）：每张卡片是一套可命名的接口配置，
 * 点「启用」即全局切换——论文精读、划词翻译、领域探索等全部文本生成跟随生效；
 * 启用中的卡片标「使用中」。支持 启用 / 测试 / 编辑 / 复制 / 删除，操作即时持久化，
 * 不走页面底部的「保存配置」。各档案的 API Key 与全局密钥一样只存根目录
 * config_key，不进数据库。编辑器复用 AIModelSection，字段与预设和全局表单一致。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  activateLLMProfile,
  createLLMProfile,
  deleteLLMProfile,
  listLLMProfiles,
  updateLLMProfile,
} from '@/api/settings'
import { toast } from '@/hooks/useToast'
import { ConfirmDialog, LoadingSpinner } from '@/ds-entry'
import AIModelSection from './AIModelSection'
import { TestButton } from './TestButton'
import type { ProviderPreset } from './ProviderPresets'
import type { AIModelConfig, LLMProfile, LLMProfilesState } from './config'

interface ProfileDraft extends AIModelConfig {
  name: string
  note: string
}

type EditorState = { mode: 'create' } | { mode: 'edit'; profile: LLMProfile } | null

interface LLMProfilesSectionProps {
  providerPresets: ProviderPreset[]
  /** 当前生效的全局 llm 配置，「新增档案」时带出当前端点作为起点 */
  currentLlm: AIModelConfig
  /** 档案启用了或启用中的档案被编辑，全局生效配置已变，父级需重新加载 /analysis/config */
  onEffectiveChange: () => void
}

/** 名称取前 1-2 个可见字符作为卡片头像缩写 */
function avatarInitials(name: string): string {
  const trimmed = name.trim()
  if (!trimmed) return '?'
  const ascii = trimmed.replace(/[^A-Za-z0-9]/g, '')
  const base = ascii || trimmed
  return base.slice(0, base.length > 1 && /^[A-Za-z0-9]{2,}/.test(ascii) ? 2 : 1).toUpperCase()
}

export default function LLMProfilesSection({
  providerPresets,
  currentLlm,
  onEffectiveChange,
}: LLMProfilesSectionProps) {
  const [state, setState] = useState<LLMProfilesState>({ active_id: '', profiles: [] })
  const [editor, setEditor] = useState<EditorState>(null)
  const [draft, setDraft] = useState<ProfileDraft | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<LLMProfile | null>(null)
  const [busy, setBusy] = useState(false)

  const reload = useCallback(async () => {
    try {
      setState(await listLLMProfiles())
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '加载配置档案失败' })
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  const openCreate = () => {
    // 新档案从当前生效配置起步（密钥不带出——表单里只是掩码），用户再按预设改
    setDraft({ ...currentLlm, api_key: '', name: '', note: '' })
    setEditor({ mode: 'create' })
  }

  const openEdit = (profile: LLMProfile) => {
    setDraft({
      name: profile.name,
      note: profile.note,
      provider: profile.provider,
      api_format: profile.api_format,
      base_url: profile.base_url,
      model: profile.model,
      api_key: profile.api_key, // 掩码回显；保存时后端识别掩码并保留真 key
      temperature: profile.temperature,
      max_tokens: profile.max_tokens,
      thinking_effort: profile.thinking_effort,
      timeout_seconds: profile.timeout_seconds,
    })
    setEditor({ mode: 'edit', profile })
  }

  const openDuplicate = (profile: LLMProfile) => {
    // 复制不带密钥（表单里只有掩码），保存后需为新档案填一个 key
    setDraft({
      name: `${profile.name} 副本`,
      note: profile.note,
      provider: profile.provider,
      api_format: profile.api_format,
      base_url: profile.base_url,
      model: profile.model,
      api_key: '',
      temperature: profile.temperature,
      max_tokens: profile.max_tokens,
      thinking_effort: profile.thinking_effort,
      timeout_seconds: profile.timeout_seconds,
    })
    setEditor({ mode: 'create' })
  }

  const closeEditor = () => {
    setEditor(null)
    setDraft(null)
  }

  const handleSave = async () => {
    if (!draft || !editor || busy) return
    if (!draft.name.trim()) {
      toast({ type: 'warning', message: '请先填写配置名称' })
      return
    }
    setBusy(true)
    try {
      const res = editor.mode === 'edit'
        ? await updateLLMProfile(editor.profile.id, draft)
        : await createLLMProfile(draft)
      setState({ active_id: res.active_id, profiles: res.profiles })
      toast({ type: 'success', message: res.message || '配置档案已保存' })
      closeEditor()
      // 编辑启用中的档案时后端已同步生效配置，父级表单需要刷新
      if (editor.mode === 'edit' && editor.profile.id === res.active_id) onEffectiveChange()
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '保存配置档案失败' })
    } finally {
      setBusy(false)
    }
  }

  const handleActivate = async (profile: LLMProfile) => {
    if (busy || profile.id === state.active_id) return
    setBusy(true)
    try {
      const res = await activateLLMProfile(profile.id)
      setState({ active_id: res.active_id, profiles: res.profiles })
      toast({ type: 'success', message: res.message || `已启用「${profile.name}」` })
      onEffectiveChange()
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '启用配置档案失败' })
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget || busy) return
    setBusy(true)
    try {
      const res = await deleteLLMProfile(deleteTarget.id)
      setState({ active_id: res.active_id, profiles: res.profiles })
      toast({ type: 'success', message: res.message || '配置档案已删除' })
      if (deleteTarget.id === state.active_id) onEffectiveChange()
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '删除配置档案失败' })
    } finally {
      setDeleteTarget(null)
      setBusy(false)
    }
  }

  return (
    <section className="set-group">
      <header className="set-group__head">
        <div className="set-group__identity">
          <h2 className="set-group__title">全局 LLM 模型</h2>
          {state.profiles.length > 0 && (
            <span className="er-tag er-tag--mute">{state.profiles.length} 套</span>
          )}
        </div>
        <button
          type="button"
          onClick={openCreate}
          disabled={!!editor}
          className="er-btn er-btn--secondary er-btn--sm"
        >
          ＋ 新增配置
        </button>
        <p className="set-group__hint">
          全项目统一大模型配置：每张卡片是一套接口设置，点「启用」即全局切换，论文精读、
          划词翻译、领域探索、问句拆解与图谱速读全部跟随生效。各档案的 API Key
          安全保存在根目录 config_key 中，不存数据库。
        </p>
      </header>

      {state.profiles.length === 0 ? (
        <p className="set-profiles-empty">
          还没有配置档案。点击右上角「＋ 新增配置」，选一个服务商预设并填入 API Key 即可开始使用。
        </p>
      ) : (
        <ul className="set-profiles">
          {state.profiles.map(profile => {
            const isActive = profile.id === state.active_id
            return (
              <li key={profile.id} className="set-profile" data-active={isActive || undefined}>
                <span className="set-profile__avatar" aria-hidden="true">{avatarInitials(profile.name)}</span>
                <div className="set-profile__meta">
                  <div className="set-profile__name">
                    <span className="set-profile__title" title={profile.name}>{profile.name}</span>
                    {isActive && <span className="er-tag er-tag--signal">✓ 使用中</span>}
                    {profile.note && <span className="set-profile__note" title={profile.note}>{profile.note}</span>}
                  </div>
                  <div className="set-profile__url" title={`${profile.base_url || '未填写 Base URL'} · ${profile.model || '未填写模型'}`}>
                    {profile.base_url || '未填写 Base URL'}
                    <span className="set-profile__model">{profile.model || '未填写模型'}</span>
                  </div>
                </div>
                <div className="set-profile__actions">
                  {isActive ? (
                    <button type="button" disabled className="er-btn er-btn--primary er-btn--sm">✓ 使用中</button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => handleActivate(profile)}
                      disabled={busy}
                      className="er-btn er-btn--primary er-btn--sm"
                    >
                      启用
                    </button>
                  )}
                  <TestButton
                    type="llm"
                    profile_id={profile.id}
                    base_url={profile.base_url}
                    model={profile.model}
                    provider={profile.provider}
                    api_format={profile.api_format}
                  />
                  <button
                    type="button"
                    onClick={() => openEdit(profile)}
                    className="er-btn er-btn--ghost er-btn--sm"
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    onClick={() => openDuplicate(profile)}
                    className="er-btn er-btn--ghost er-btn--sm"
                  >
                    复制
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(profile)}
                    className="er-btn er-btn--ghost er-btn--sm set-profile__delete"
                  >
                    删除
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {editor && draft && (
        <div className="set-profile-editor">
          <div className="set-profile-editor__head">
            <h3>{editor.mode === 'create' ? '新增配置' : `编辑：${editor.profile.name}`}</h3>
            <button
              type="button"
              onClick={closeEditor}
              className="er-btn er-btn--ghost er-btn--sm"
            >
              取消
            </button>
          </div>

          <div className="er-field">
            <label className="er-field__label" htmlFor="llm-profile-name">配置名称</label>
            <input
              id="llm-profile-name"
              type="text"
              className="er-input"
              value={draft.name}
              onChange={e => setDraft({ ...draft, name: e.target.value })}
              placeholder="例如：猫肥 Neko API"
            />
          </div>
          <div className="er-field">
            <label className="er-field__label" htmlFor="llm-profile-note">备注</label>
            <input
              id="llm-profile-note"
              type="text"
              className="er-input"
              value={draft.note}
              onChange={e => setDraft({ ...draft, note: e.target.value })}
              placeholder="例如：公司专用账号（可选）"
            />
          </div>

          <AIModelSection
            title="档案接口配置"
            description="字段含义与全局模型一致；保存后在列表中一键启用。"
            config={draft}
            onChange={cfg => setDraft({ ...draft, ...cfg })}
            defaultMaxTokens={16384}
            providerPresets={providerPresets}
          />

          <div className="set-profile-editor__foot">
            <button
              type="button"
              onClick={handleSave}
              disabled={busy}
              aria-busy={busy}
              className="er-btn er-btn--primary"
            >
              {busy && <LoadingSpinner size="xs" />}
              保存档案
            </button>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除配置档案"
        message={
          deleteTarget
            ? `确定删除「${deleteTarget.name}」？${
              deleteTarget.id === state.active_id
                ? '它正在使用中，删除后全局配置保持不变，仅解除绑定。'
                : '删除后不可恢复。'
            }`
            : ''
        }
        variant="danger"
        confirmLabel="删除"
        cancelLabel="取消"
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </section>
  )
}
