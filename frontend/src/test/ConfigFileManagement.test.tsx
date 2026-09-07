import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfigFileManagement } from '@/features/settings/ConfigFileManagement'

const settingsApi = vi.hoisted(() => ({
  getConfigFileInfo: vi.fn(),
  getExportConfigUrl: vi.fn(),
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
  settingsApi.getExportConfigUrl.mockReturnValue('/api/analysis/config/export?format=yaml&include_secrets=true')
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
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
})
