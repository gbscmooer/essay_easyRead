import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChartView } from '@/features/research/ChartView'
import { EvidenceMarkdown, paperLink } from '@/features/research/paperLinks'
import { ExplorationConversation } from '@/features/research/ExplorationConversation'
import { askExploration, getExplorationMessages } from '@/api/exploration'

vi.mock('@/api/exploration', () => ({ askExploration: vi.fn(), getExplorationMessages: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})

describe('探索图表和论文链接', () => {
  it('图表放大、缩放、重置和关闭恢复焦点', () => {
    render(<ChartView title="趋势"><p>Very long full topic name</p></ChartView>)
    const trigger = screen.getByRole('button', { name: '放大趋势' })
    fireEvent.click(trigger)
    expect(screen.getByRole('dialog')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: /^放大$/ }))
    expect(screen.getByLabelText('缩放比例')).toHaveTextContent('125%')
    fireEvent.click(screen.getByRole('button', { name: '重置缩放' }))
    expect(screen.getByLabelText('缩放比例')).toHaveTextContent('100%')
    fireEvent.click(screen.getByRole('button', { name: '关闭放大视图' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('完整标题、唯一简称和引用编号链接到已知论文，拒绝危险链接', () => {
    const paper = { key: 'a', title: 'Pan-Mamba: Effective pan-sharpening', doi: '10.1/a' }
    render(<EvidenceMarkdown text="Pan-Mamba: Effective pan-sharpening 和 Pan-Mamba [P1] SuperPan-Mamba Pan-Mamba-Net" papers={[paper]} citations={{ P1: 'a' }} />)
    expect(screen.getAllByRole('link')).toHaveLength(3)
    expect(screen.getByRole('link', { name: '[P1]' })).toHaveAttribute('href', 'https://doi.org/10.1/a')
    expect(paperLink({ title: 'bad', url: 'javascript:alert(1)' })).toBeNull()
    expect(paperLink({ title: 'old', scopus_id: '123' })).toContain('2-s2.0-123')
  })

  it('追问发送所选证据与补充检索选项，失败保留输入', async () => {
    vi.mocked(getExplorationMessages).mockResolvedValue([])
    vi.mocked(askExploration).mockRejectedValue(new Error('模型未配置'))
    const setQuestion = vi.fn()
    render(<ExplorationConversation runId={17} papers={[]} disabled={false} onMessages={vi.fn()}
      question="Compare their limitations" setQuestion={setQuestion}
      selected={{ key: 'paper1', title: 'Selected', citation_count: 0, cluster: 0 }} onClearSelection={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: '发送' })).toBeEnabled())
    fireEvent.click(screen.getByRole('checkbox', { name: '补充检索' }))
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await screen.findByText('模型未配置')
    expect(askExploration).toHaveBeenCalledWith(17, 'Compare their limitations', true, ['paper1'])
    expect(setQuestion).not.toHaveBeenCalled()
    expect(screen.getByLabelText('继续提问')).toHaveValue('Compare their limitations')
  })
})
