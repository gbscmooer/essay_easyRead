import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const researchApi = vi.hoisted(() => ({ queryRag: vi.fn() }))
vi.mock('@/api/research', () => researchApi)

import { RagQueryBox } from '@/features/research/RagQueryBox'
import type { RagQueryResponse } from '@/types'

function answer(text: string): RagQueryResponse {
  return {
    answer: text,
    citations: [{
      paper_id: 1,
      paper_title: 'Attention Is All You Need',
      section_name: 'Method',
      snippet: '…',
      score: 0.9,
    }],
  }
}

async function ask(user: ReturnType<typeof userEvent.setup>, question: string) {
  await user.type(screen.getByLabelText('跨论文问答输入'), question)
  await user.click(screen.getByRole('button', { name: '提问' }))
}

beforeEach(() => {
  researchApi.queryRag.mockResolvedValue(answer('第一个答案'))
})

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('跨论文问答', () => {
  it('新问题不再冲掉上一轮的答案', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><RagQueryBox wsId={3} /></MemoryRouter>)

    await ask(user, '第一个问题')
    expect(await screen.findByText('第一个答案')).toBeInTheDocument()

    researchApi.queryRag.mockResolvedValueOnce(answer('第二个答案'))
    await ask(user, '第二个问题')

    expect(await screen.findByText('第二个答案')).toBeInTheDocument()
    expect(screen.getByText('第一个答案')).toBeInTheDocument()
    // 最新一轮排在最前
    const answers = screen.getAllByText(/第.个答案/).map(node => node.textContent)
    expect(answers).toEqual(['第二个答案', '第一个答案'])
  })

  it('提交后清空输入框，问题本身留在这一轮里', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><RagQueryBox wsId={3} /></MemoryRouter>)

    await ask(user, '这个方法怎么训练')

    await waitFor(() => expect((screen.getByLabelText('跨论文问答输入') as HTMLInputElement).value).toBe(''))
    expect(screen.getByText(/这个方法怎么训练/)).toBeInTheDocument()
  })

  it('检索期间提供中止，中止后不落一轮问答', async () => {
    const user = userEvent.setup()
    let rejectQuery: (reason?: unknown) => void = () => {}
    researchApi.queryRag.mockImplementation(
      (_ws, _q, _n, _ids, _mode, signal?: AbortSignal) => new Promise((_resolve, reject) => {
        rejectQuery = reject
        signal?.addEventListener('abort', () => reject(new Error('aborted')))
      }),
    )

    render(<MemoryRouter><RagQueryBox wsId={3} /></MemoryRouter>)
    await ask(user, '慢查询')

    const abortButton = await screen.findByRole('button', { name: '中止' })
    await user.click(abortButton)
    rejectQuery(new Error('aborted'))

    await waitFor(() => expect(screen.getByRole('button', { name: '提问' })).toBeInTheDocument())
    expect(screen.queryByText(/慢查询/)).not.toBeInTheDocument()
    expect(screen.getByText('构建图谱后可进行跨论文语义检索')).toBeInTheDocument()
  })

  it('复制按钮把问题、答案与引用一起写进剪贴板', async () => {
    const user = userEvent.setup()
    // 必须在 setup 之后覆盖：user-event 自己也会往 navigator 上装一份剪贴板桩
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    render(<MemoryRouter><RagQueryBox wsId={3} /></MemoryRouter>)

    await ask(user, '训练细节是什么')
    await user.click(await screen.findByRole('button', { name: '复制' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const copied = writeText.mock.calls[0][0] as string
    expect(copied).toContain('Q: 训练细节是什么')
    expect(copied).toContain('第一个答案')
    expect(copied).toContain('[1] Attention Is All You Need · Method')
  })

  it('查询失败时报错，且不产生空的一轮', async () => {
    const user = userEvent.setup()
    researchApi.queryRag.mockRejectedValueOnce(new Error('向量库未就绪'))
    render(<MemoryRouter><RagQueryBox wsId={3} /></MemoryRouter>)

    await ask(user, '会失败的问题')

    expect(await screen.findByText('向量库未就绪')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '复制' })).not.toBeInTheDocument()
  })
})
