import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PauseAdjust } from '@/features/research/PauseAdjust'
import { adjustExploration } from '@/api/exploration'
import type { ExplorationPaper } from '@/types'

vi.mock('@/api/exploration', () => ({ adjustExploration: vi.fn() }))

describe('PauseAdjust', () => {
  it('保存剔除与追加检索式，并提示继续时重做的阶段', async () => {
    vi.mocked(adjustExploration).mockResolvedValue({
      run_id: 3, status: 'paused', completed_stages: ['preflight', 'protocol', 'recall'],
      intervention: { at: '', stage: 'select', excluded: ['Review'], queries: ['laparoscopic video vlm'], note: '', invalidated_stages: ['screen', 'select'] },
    })
    const papers = [{ key: 'k:1', title: 'Review', year: 2020 }, { key: 'k:2', title: 'Surgical VLM', year: 2025 }] as ExplorationPaper[]
    render(<PauseAdjust runId={3} papers={papers} />)
    fireEvent.click(screen.getByRole('button', { name: '调整检索与材料' }))
    const save = screen.getByRole('button', { name: '保存调整' })
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/追加英文检索式/), { target: { value: 'laparoscopic video vlm\nab' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /Review/ }))
    fireEvent.click(save)
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('继续时将重新执行：筛选、选材'))
    expect(adjustExploration).toHaveBeenCalledWith(3, { exclude_keys: ['k:1'], add_queries: ['laparoscopic video vlm'], note: '' })
  })
})
