import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const workspaceApi = vi.hoisted(() => ({
  getWorkspace: vi.fn(),
  getWorkspaceInsights: vi.fn(),
  getWorkspaceReadingPlan: vi.fn(),
  getWorkspaceSurvey: vi.fn(),
  updateWorkspace: vi.fn(),
  addPaperToWorkspace: vi.fn(),
  removePaperFromWorkspace: vi.fn(),
  promotePaper: vi.fn(),
}))
const papersApi = vi.hoisted(() => ({ listPapers: vi.fn() }))

vi.mock('@/api/workspaces', () => workspaceApi)
vi.mock('@/api/papers', () => papersApi)
vi.mock('@/features/workspace/WorkspaceComparison', () => ({ WorkspaceComparison: () => null }))
vi.mock('@/features/workspace/WorkspaceInnovationPanel', () => ({ WorkspaceInnovationPanel: () => null }))
vi.mock('@/features/workspace/WorkspaceInsights', () => ({ WorkspaceInsights: () => null }))

import WorkspaceDetailPage from '@/pages/WorkspaceDetailPage'

function paper(id: number, title: string) {
  return {
    id,
    filename: `${title}.pdf`,
    title,
    venue: null,
    year: null,
    status: 'done',
    error_message: null,
    code_url: null,
    code_path: null,
    code_status: 'none',
    read_level: 'none',
    level: 1,
    created_at: '2026-08-19T00:00:00Z',
    updated_at: '2026-08-19T00:00:00Z',
  }
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/workspace/3']}>
      <Routes>
        <Route path="/workspace/:id" element={<WorkspaceDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  workspaceApi.getWorkspace.mockResolvedValue({
    id: 3,
    name: '视觉方向',
    papers: [paper(1, '已在工作区的论文')],
    created_at: '2026-08-19T00:00:00Z',
    updated_at: '2026-08-19T00:00:00Z',
  })
  workspaceApi.getWorkspaceInsights.mockResolvedValue(null)
  workspaceApi.getWorkspaceReadingPlan.mockResolvedValue(null)
  workspaceApi.getWorkspaceSurvey.mockResolvedValue(null)
  papersApi.listPapers.mockResolvedValue({ papers: [paper(2, 'ResNet'), paper(1, '已在工作区的论文')], total: 320 })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.resetAllMocks()
})

describe('工作区添加论文', () => {
  it('打开面板即列出候选，并排除已在工作区里的论文', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '从论文库添加' }))

    expect(await screen.findByText('ResNet')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '添加' })).toBeInTheDocument()
    // 已在工作区的那篇不出现在候选里（工作区自己的列表另有一处渲染）
    await waitFor(() => expect(screen.getAllByText('已在工作区的论文')).toHaveLength(1))
  })

  it('关键词交给后端查全库，而不是在前 100 篇里翻', async () => {
    vi.useFakeTimers()
    renderPage()
    fireEvent.click(await vi.waitFor(() => screen.getByRole('button', { name: '从论文库添加' })))

    await act(async () => { vi.advanceTimersByTime(50) })
    papersApi.listPapers.mockClear()

    fireEvent.change(screen.getByLabelText('搜索要加入工作区的论文'), { target: { value: 'diffusion' } })
    await act(async () => { vi.advanceTimersByTime(400) })

    expect(papersApi.listPapers).toHaveBeenCalledWith({ per_page: 50, search: 'diffusion' })
  })

  it('说明候选数量与库内总数，让「找不到」有据可依', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '从论文库添加' }))

    expect(await screen.findByText(/篇可添加（库内共 320 篇）/)).toBeInTheDocument()
  })

  it('搜不到时给的是「换个关键词」，不是「都已加入」', async () => {
    papersApi.listPapers.mockResolvedValue({ papers: [], total: 0 })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '从论文库添加' }))

    fireEvent.change(await screen.findByLabelText('搜索要加入工作区的论文'), { target: { value: 'zzz' } })

    expect(await screen.findByText('没有匹配的论文，换个关键词试试。')).toBeInTheDocument()
  })
})
