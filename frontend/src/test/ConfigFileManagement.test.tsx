import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfigFileManagement } from '@/features/settings/ConfigFileManagement'
import { ToastContainer } from '@/components/ToastContainer'

const settingsApi = vi.hoisted(() => ({
  getConfigFileInfo: vi.fn(),
  exportConfigFile: vi.fn(),
  importConfigFile: vi.fn(),
  importConfigText: vi.fn(),
}))

vi.mock('@/api/settings', () => settingsApi)

beforeEach(() => {
  settingsApi.getConfigFileInfo.mockResolvedValue({
    active_path: 'config.yaml',
    full_path: '/project/config.yaml',
    exists: true,
    format: 'yaml',
    supported_formats: ['yaml', 'toml', 'json'],
  })
  settingsApi.exportConfigFile.mockResolvedValue({ blob: new Blob(['masked']), masked: true })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

describe('ConfigFileManagement component', () => {
  it('renders active config file path and badge', async () => {
    render(<ConfigFileManagement onConfigReload={vi.fn()} />)
    const elements = await screen.findAllByText('config.yaml')
    expect(elements.length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/YAML · 已生效/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导出配置/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导入配置/ })).toBeInTheDocument()
  })

  it('opens and closes export modal with format choices', async () => {
    const user = userEvent.setup()
    render(<ConfigFileManagement onConfigReload={vi.fn()} />)

    const exportBtn = await screen.findByRole('button', { name: /导出配置/ })
    await user.click(exportBtn)

    expect(screen.getByText('导出系统配置文件')).toBeInTheDocument()
    expect(screen.getByText('YAML (推荐)')).toBeInTheDocument()
    expect(screen.getByText('TOML')).toBeInTheDocument()
    expect(screen.getByText('JSON')).toBeInTheDocument()

    // Close modal
    await user.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => {
      expect(screen.queryByText('导出系统配置文件')).not.toBeInTheDocument()
    })
  })

  it('opens and closes import modal with file upload and text paste tabs', async () => {
    const user = userEvent.setup()
    render(<ConfigFileManagement onConfigReload={vi.fn()} />)

    const importBtn = await screen.findByRole('button', { name: /导入配置/ })
    await user.click(importBtn)

    expect(screen.getByText('导入配置文件')).toBeInTheDocument()
    expect(screen.getByText('上传文件 (.yaml / .toml / .json)')).toBeInTheDocument()
    expect(screen.getByText('直接粘贴文本')).toBeInTheDocument()

    // Switch to paste tab
    await user.click(screen.getByText('直接粘贴文本'))
    expect(screen.getByPlaceholderText(/在此粘贴 YAML、TOML 或 JSON 配置文本/)).toBeInTheDocument()
  })

  it('defaults to masked export and downloads only after a successful response', async () => {
    const user = userEvent.setup()
    const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:config')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    render(<><ConfigFileManagement onConfigReload={vi.fn()} /><ToastContainer /></>)

    await user.click(await screen.findByRole('button', { name: /导出配置/ }))
    expect(screen.getByRole('checkbox', { name: /包含 API 密钥/ })).not.toBeChecked()
    await user.click(screen.getByRole('button', { name: '下载配置文件' }))

    await waitFor(() => expect(settingsApi.exportConfigFile).toHaveBeenCalledWith('yaml', false))
    expect(createUrl).toHaveBeenCalledOnce()
    expect(click).toHaveBeenCalledOnce()
    expect(screen.getByText(/密钥已脱敏/)).toBeInTheDocument()
  })

  it('keeps export dialog open and does not download an error response', async () => {
    const user = userEvent.setup()
    settingsApi.exportConfigFile.mockRejectedValueOnce(new Error('本地访问限定'))
    const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:config')
    render(<><ConfigFileManagement onConfigReload={vi.fn()} /><ToastContainer /></>)

    await user.click(await screen.findByRole('button', { name: /导出配置/ }))
    await user.click(screen.getByRole('checkbox', { name: /包含 API 密钥/ }))
    await user.click(screen.getByRole('button', { name: '下载配置文件' }))

    await waitFor(() => expect(settingsApi.exportConfigFile).toHaveBeenCalledWith('yaml', true))
    expect(screen.getByText('本地访问限定')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(createUrl).not.toHaveBeenCalled()
  })
})
