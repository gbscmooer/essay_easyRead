import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ToastContainer } from '@/components/ToastContainer'
import { DEFAULT_CONFIG, type AppConfig } from '@/features/settings/config'
import { hasUnsavedConfig, setSettingsFormDirty } from '@/hooks/useConfig'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

vi.mock('@/api/client', () => api)

/** /analysis/llm-profiles 的空档案状态（配置档案区挂载即拉取） */
function emptyProfilesState() {
  return { active_id: '', profiles: [] }
}
vi.mock('@/features/settings/TestButton', () => ({ TestButton: () => null }))
vi.mock('@/features/settings/AIModelSection', () => ({
  default: ({ title, config, onChange }: {
    title: string
    config: AppConfig['llm']
    onChange: (config: AppConfig['llm']) => void
  }) => (
    <div>
      <span data-testid="llm-model">{config.model}</span>
      <button type="button" onClick={() => onChange({ ...config, model: `${config.model}-changed` })}>
        修改{title}
      </button>
    </div>
  ),
}))
vi.mock('@/features/settings/PromptEditor', () => ({
  PromptEditor: ({ value, onChange }: {
    value: AppConfig['analysis_prompt']
    onChange: (config: AppConfig['analysis_prompt']) => void
  }) => (
    <button type="button" onClick={() => onChange({ ...value, active: 'custom:test' })}>
      修改提示词
    </button>
  ),
}))
vi.mock('@/features/settings/UIConfigSection', () => ({
  default: ({ config, onChange }: {
    config: AppConfig['ui']
    onChange: (config: AppConfig['ui']) => void
  }) => (
    <button type="button" onClick={() => onChange({ ...config, font: 'serif' })}>
      修改界面
    </button>
  ),
}))
vi.mock('@/features/settings/DeepResearchSettings', () => ({
  EmbeddingSection: ({ setConfig }: SettingsSectionProps) => (
    <button type="button" onClick={() => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, batch_size: 64 } }))}>
      修改研究参数
    </button>
  ),
  ResearchProviderSection: () => null,
  RerankerSection: () => null,
  RelationsSection: () => null,
  ArxivExpandSection: () => null,
  FindpapersSection: () => null,
}))

interface SettingsSectionProps {
  config: AppConfig
  setConfig: React.Dispatch<React.SetStateAction<AppConfig>>
}

import SettingsPage from '@/pages/SettingsPage'

function serverConfig(): AppConfig {
  return structuredClone(DEFAULT_CONFIG)
}

async function renderSettings() {
  render(<SettingsPage />)
  await screen.findByRole('button', { name: '修改提示词' })
  expect(hasUnsavedConfig()).toBe(false)
}

beforeEach(() => {
  api.get.mockImplementation((path: string) => {
    if (String(path).includes('/llm-profiles')) return Promise.resolve(emptyProfilesState())
    return Promise.resolve(serverConfig())
  })
  api.post.mockResolvedValue({ message: 'ok' })
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  setSettingsFormDirty(false)
})

afterEach(() => {
  cleanup()
  setSettingsFormDirty(false)
  vi.restoreAllMocks()
})

describe('SettingsPage unsaved changes', () => {
  it.each([
    { tab: null, action: '修改提示词' },
    { tab: '界面与主题', action: '修改界面' },
    { tab: '深度研究', action: '修改研究参数' },
  ])('tracks complete config changes for $action', async ({ tab, action }) => {
    const user = userEvent.setup()
    await renderSettings()

    if (tab) await user.click(screen.getByRole('tab', { name: tab }))
    await user.click(screen.getByRole('button', { name: action }))

    await waitFor(() => expect(hasUnsavedConfig()).toBe(true))
    const beforeUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(beforeUnload)
    expect(beforeUnload.defaultPrevented).toBe(true)
  })

  it('keeps changes dirty and reports a save failure through the global toast', async () => {
    api.post.mockRejectedValueOnce(new Error('服务器拒绝保存'))
    const user = userEvent.setup()
    render(<><SettingsPage /><ToastContainer /></>)
    await screen.findByRole('button', { name: '修改提示词' })

    await user.click(screen.getByRole('button', { name: '修改提示词' }))
    await user.click(screen.getByRole('button', { name: '保存配置' }))

    expect(await screen.findByText('服务器拒绝保存')).toBeInTheDocument()
    expect(hasUnsavedConfig()).toBe(true)
  })

  it('reports a config load failure instead of silently showing defaults', async () => {
    api.get.mockRejectedValueOnce(new Error('配置服务不可用'))
    render(<><SettingsPage /><ToastContainer /></>)

    expect(await screen.findByText('配置服务不可用')).toBeInTheDocument()
  })

  it('clears the dirty state after a successful save', async () => {
    const user = userEvent.setup()
    await renderSettings()

    await user.click(screen.getByRole('button', { name: '修改提示词' }))
    await user.click(screen.getByRole('button', { name: '保存配置' }))

    await waitFor(() => expect(hasUnsavedConfig()).toBe(false))
    expect(screen.getByRole('status')).toHaveTextContent('已保存')
  })

  it('reloads the global config after activating a profile', async () => {
    const user = userEvent.setup()
    api.get.mockImplementation((path: string) => {
      if (path === '/analysis/config') return Promise.resolve(serverConfig())
      if (path === '/analysis/llm/providers') return Promise.resolve({ providers: [] })
      if (path.includes('/llm-profiles')) {
        return Promise.resolve({
          active_id: '',
          profiles: [{
            id: 'p1', name: 'DeepSeek', note: '', provider: 'deepseek', api_format: 'openai_chat',
            base_url: 'https://api.deepseek.com', model: 'deepseek-chat', api_key: '***key',
            temperature: 0.2, max_tokens: 16384, thinking_effort: 'off', timeout_seconds: 150,
            proxy: { enabled: false, base_url: '' },
          }],
        })
      }
      return Promise.resolve({})
    })
    api.post.mockResolvedValue({ message: '已启用「DeepSeek」', active_id: 'p1', profiles: [] })

    render(<SettingsPage />)
    await screen.findByText('DeepSeek')

    const configCalls = () => api.get.mock.calls.filter(([p]) => p === '/analysis/config').length
    const before = configCalls()
    await user.click(screen.getByRole('button', { name: '启用' }))

    await waitFor(() => expect(configCalls()).toBeGreaterThan(before))
    expect(api.post).toHaveBeenCalledWith('/analysis/llm-profiles/p1/activate', {})
  })

  it('copies a profile with its full API key into the new draft', async () => {
    const user = userEvent.setup()
    api.get.mockImplementation((path: string) => {
      if (path === '/analysis/config') return Promise.resolve(serverConfig())
      if (path === '/analysis/llm/providers') return Promise.resolve({ providers: [] })
      if (path === '/analysis/llm-profiles/p1/key') return Promise.resolve({ api_key: 'sk-real-full' })
      if (path.includes('/llm-profiles')) {
        return Promise.resolve({
          active_id: '',
          profiles: [{
            id: 'p1', name: 'DeepSeek', note: '', provider: 'deepseek', api_format: 'openai_chat',
            base_url: 'https://api.deepseek.com', model: 'deepseek-chat', api_key: '***key',
            temperature: 0.2, max_tokens: 16384, thinking_effort: 'off', timeout_seconds: 150,
            proxy: { enabled: false, base_url: '' },
          }],
        })
      }
      return Promise.resolve({})
    })
    api.post.mockResolvedValueOnce({ message: '已创建配置档案「DeepSeek 副本」', active_id: '', profiles: [] })

    render(<SettingsPage />)
    await screen.findByText('DeepSeek')
    await user.click(screen.getByRole('button', { name: '复制' }))

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/analysis/llm-profiles/p1/key'))
    await user.click(await screen.findByRole('button', { name: '保存档案' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/analysis/llm-profiles', expect.objectContaining({
      name: 'DeepSeek 副本',
      api_key: 'sk-real-full',
    })))
  })
})
