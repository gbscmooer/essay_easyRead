import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ResearchRevision } from '@/features/research/ResearchRevision'
import { getResearchVersions, pauseExploration, reviseResearch } from '@/api/exploration'
import type { ExplorationRun } from '@/types'
import type { ResearchVersions } from '@/types/researchRevision'

vi.mock('@/api/exploration', () => ({ getResearchVersions: vi.fn(), pauseExploration: vi.fn(), reviseResearch: vi.fn() }))
vi.mock('@/api/workspaces', () => ({
  listWorkspaces: vi.fn().mockResolvedValue([{ id: 4, name: '补充材料库' }]),
  getWorkspace: vi.fn().mockResolvedValue({ papers: [{ id: 9, title: '用户补充论文' }] }),
}))
const parent = { id: 34, question: '原问题', depth: 'quick', status: 'completed', options: { search_online: true }, workflow_version: 'academic_survey_v2' } as ExplorationRun
const initial: ResearchVersions = { run_id: 34, state_version: 7, status: 'completed', worker_active: false,
  ancestors: [], children: [], materials: [{ asset_id: 'source-a', title: '旧论文A', source_kind: 'full_text' }] }

beforeEach(() => { vi.clearAllMocks(); vi.mocked(getResearchVersions).mockResolvedValue(initial) })

function show(run = parent) {
  const created = vi.fn()
  render(<MemoryRouter><ResearchRevision run={run} onCreated={created} /></MemoryRouter>)
  return created
}

async function edit() {
  const button = await screen.findByRole('button', { name: '修改问题或材料' })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
}

describe('用户研究修订', () => {
  it('sends changed question, supplements and material exclusion as a new version', async () => {
    const child: ExplorationRun = { ...parent, id: 35, question: '新的研究问题', status: 'pending' }
    vi.mocked(reviseResearch).mockResolvedValue(child)
    const created = show(); await edit()
    fireEvent.change(screen.getByLabelText('修订后的研究问题'), { target: { value: '新的研究问题' } })
    fireEvent.change(screen.getByLabelText('补充问题（每行一项，最多7项）'), { target: { value: '补充问题一\n补充问题二' } })
    fireEvent.click(screen.getByLabelText('旧论文A（全文）'))
    fireEvent.change(screen.getByLabelText('加入工作区论文'), { target: { value: '4' } })
    fireEvent.click(await screen.findByLabelText('用户补充论文'))
    fireEvent.click(screen.getByRole('button', { name: '生成修订版' }))
    await waitFor(() => expect(created).toHaveBeenCalledWith(child))
    expect(reviseResearch).toHaveBeenCalledWith(34, {
      question: '新的研究问题', expected_state_version: 7, depth: 'quick', additional_questions: ['补充问题一', '补充问题二'],
      reuse_materials: true, exclude_asset_ids: ['source-a'], search_online: true, workspace_id: 4, seed_paper_ids: [9],
    })
    expect(pauseExploration).not.toHaveBeenCalled()
  })

  it('pauses an active run and waits for the worker to exit before creating the child', async () => {
    const live = { ...initial, status: 'running', worker_active: true }
    vi.mocked(getResearchVersions).mockResolvedValueOnce(live).mockResolvedValueOnce(live)
      .mockResolvedValueOnce({ ...initial, status: 'paused', state_version: 8 })
    vi.mocked(pauseExploration).mockResolvedValue({ run_id: 34, status: 'paused', detail: '已请求暂停' })
    vi.mocked(reviseResearch).mockResolvedValue({ ...parent, id: 35 })
    show({ ...parent, status: 'running' }); await edit()
    fireEvent.click(screen.getByRole('button', { name: '暂停并生成修订版' }))
    await waitFor(() => expect(pauseExploration).toHaveBeenCalledWith(34))
    expect(reviseResearch).not.toHaveBeenCalled()
    await waitFor(() => expect(reviseResearch).toHaveBeenCalledWith(34, expect.objectContaining({ expected_state_version: 8 })), { timeout: 2500 })
  })

  it('shows parent links and preserves a readable error when creation conflicts', async () => {
    vi.mocked(getResearchVersions).mockResolvedValue({ ...initial, ancestors: [{ run_id: 30, question: '旧范围', status: 'partial', depth: 'lite',
      created_at: '', parent_run_id: null, usage: [{ resource: 'llm_tokens', used: 1000, usage_known: false }] }] })
    vi.mocked(reviseResearch).mockRejectedValue(new Error('已有其他活动研究'))
    show(); await edit()
    expect(screen.getByRole('link', { name: '研究 #30：旧范围' })).toHaveAttribute('href', '/explorer/30')
    expect(screen.getByText(/保守记账 1,000 token/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '生成修订版' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('已有其他活动研究')
    expect(screen.getByLabelText('修订后的研究问题')).toHaveValue('原问题')
  })
})
