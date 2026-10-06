import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ResearchQuestions } from '@/features/research/ResearchQuestions'
import { getResearchQuestions } from '@/api/exploration'
import type { ResearchQuestionState } from '@/types'

vi.mock('@/api/exploration', () => ({ getResearchQuestions: vi.fn() }))
const question: ResearchQuestionState = { question_id: 'main', protocol_version: 1, text: '比较两个队列的处理机制', required: true,
  priority: 1, intent: 'understand', answer_criteria: ['保留用户限定条件'], expected_evidence: ['fact'], status: 'insufficient',
  gaps: ['BetaNet 的处理仍需核查'], answer_revision_id: null }
beforeEach(() => vi.clearAllMocks())

describe('冻结的逐项研究计划', () => {
  it('shows the source question, mode and planning gap separately from answer criteria', async () => {
    vi.mocked(getResearchQuestions).mockResolvedValue({ run_id: 37, legacy: false, items: [{ ...question,
      requirements: [{ unit_id: 'req_a', requirement: '说明 AlphaNet 的参考输入', subject: 'AlphaNet', phase: 'inference', mode: '单参考',
        source_quote: '推理时是否需要参考输入', source_origin: 'question' }], planning_gaps: ['另一方法的要求尚未核查'],
        scope_constraints: ['只讨论图像', '不做排名'], reading_role: 'mechanism' }] })
    render(<ResearchQuestions runId={37} refreshKey="completed" />)
    fireEvent.click(await screen.findByText('逐项研究计划（1 项）'))
    expect(screen.getByText('说明 AlphaNet 的参考输入（AlphaNet · 单参考 · 推理）')).toBeInTheDocument()
    expect(screen.getByText('用户原句：推理时是否需要参考输入')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('需求拆解仍需核查：另一方法的要求尚未核查')
    expect(screen.getByText('研究范围：只讨论图像；不做排名')).toBeInTheDocument()
    expect(screen.getByText('保留用户限定条件')).toBeInTheDocument()
  })
  it('keeps historical questions readable without inventing a request plan', async () => {
    vi.mocked(getResearchQuestions).mockResolvedValue({ run_id: 36, legacy: false, items: [question] })
    render(<ResearchQuestions runId={36} refreshKey="completed" />)
    expect(await screen.findByText('比较两个队列的处理机制')).toBeInTheDocument()
    expect(screen.queryByText(/逐项研究计划/)).not.toBeInTheDocument()
    expect(screen.getByText('BetaNet 的处理仍需核查')).toBeInTheDocument()
  })
})
