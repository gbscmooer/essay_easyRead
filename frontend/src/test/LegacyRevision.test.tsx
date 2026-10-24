import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { LegacyRevision } from '@/features/research/LegacyRevision'
import { getLegacyVersions, reviseResearch } from '@/api/exploration'
import type { ExplorationPaper, ExplorationRun } from '@/types'

vi.mock('@/api/exploration', () => ({ getLegacyVersions: vi.fn(), reviseResearch: vi.fn() }))

const RUN = { id: 5, question: '多模态大模型用于手术视频理解', depth: 'quick', status: 'done', provider: '', created_at: '2026-01-01', updated_at: '2026-01-01', workflow_version: 'legacy_v1', state_version: 0, options: { search_online: true } } as ExplorationRun
const PAPERS = [{ key: 'doi:10.1/a', title: 'Surgical VLM', year: 2025 }, { key: 'doi:10.1/b', title: 'Unrelated paper', year: 2019 }] as ExplorationPaper[]
const row = (id: number, question: string, verdict: string | null, conditions: string[] = []) => ({ id, question, status: 'done', depth: 'quick', created_at: '2026-01-01', verdict, conditions, reused: true })

describe('LegacyRevision', () => {
  it('展示版本链并提交修正：问题、补充条件、剔除文献', async () => {
    vi.mocked(getLegacyVersions).mockResolvedValue({ run_id: 5, workflow_version: 'legacy_v1', current: row(5, RUN.question, 'emerging'), ancestors: [row(3, '旧问题', 'done_extensively')], children: [] })
    vi.mocked(reviseResearch).mockResolvedValue({ ...RUN, id: 9 })
    const onCreated = vi.fn()
    render(<MemoryRouter><LegacyRevision run={RUN} papers={PAPERS} disabled={false} onCreated={onCreated} /></MemoryRouter>)

    const chain = await screen.findByRole('list', { name: '版本链' })
    expect(chain).toHaveTextContent('#3')
    expect(chain).toHaveTextContent('充分实现')
    expect(chain).toHaveTextContent('#5（当前）')

    fireEvent.click(screen.getByRole('button', { name: '修正问题或材料' }))
    fireEvent.change(screen.getByLabelText('修正后的研究问题'), { target: { value: '多模态大模型用于腹腔镜手术视频理解' } })
    fireEvent.change(screen.getByLabelText(/补充条件/), { target: { value: '只看 2023 年以后\n关注公开数据集' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /Unrelated paper/ }))
    fireEvent.click(screen.getByRole('button', { name: '生成修正版本' }))

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ id: 9 })))
    expect(reviseResearch).toHaveBeenCalledWith(5, {
      question: '多模态大模型用于腹腔镜手术视频理解', expected_state_version: 0, depth: 'quick',
      additional_questions: ['只看 2023 年以后', '关注公开数据集'],
      reuse_materials: true, exclude_asset_ids: ['doi:10.1/b'], search_online: true,
    })
  })

  it('既不复用也不联网时不能提交', async () => {
    vi.mocked(getLegacyVersions).mockResolvedValue({ run_id: 5, workflow_version: 'legacy_v1', current: row(5, RUN.question, null), ancestors: [], children: [] })
    render(<MemoryRouter><LegacyRevision run={RUN} papers={[]} disabled={false} onCreated={vi.fn()} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: '修正问题或材料' }))
    fireEvent.click(screen.getByRole('checkbox', { name: '复用本次已召回文献' }))
    fireEvent.click(screen.getByRole('checkbox', { name: '联网补充检索' }))
    expect(screen.getByRole('button', { name: '生成修正版本' })).toBeDisabled()
  })
})
