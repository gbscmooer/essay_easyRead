import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const codeApi = vi.hoisted(() => ({
  getCodeTree: vi.fn(),
  getCodeAnalysis: vi.fn(),
  getCodeAnalysisSSEUrl: vi.fn((id: number) => `/api/code/${id}/analyze`),
  cloneRepository: vi.fn(),
  openInVSCode: vi.fn(),
}))
const papersApi = vi.hoisted(() => ({ getPaper: vi.fn() }))

vi.mock('@/api/code', () => codeApi)
vi.mock('@/api/papers', () => papersApi)

import CodePage from '@/pages/CodePage'

/** 只实现 CodePage 用到的那部分 EventSource 表面。 */
class FakeEventSource {
  static last: FakeEventSource | null = null
  listeners = new Map<string, ((event: MessageEvent) => void)[]>()
  closed = false
  onerror: (() => void) | null = null

  constructor(public url: string) {
    FakeEventSource.last = this
  }

  addEventListener(type: string, handler: (event: MessageEvent) => void) {
    const existing = this.listeners.get(type) ?? []
    this.listeners.set(type, [...existing, handler])
  }

  close() { this.closed = true }

  emit(type: string, data?: unknown) {
    const event = { data: data === undefined ? undefined : JSON.stringify(data) } as MessageEvent
    this.listeners.get(type)?.forEach(handler => handler(event))
  }
}

function renderCodePage() {
  return render(
    <MemoryRouter initialEntries={['/code/7']}>
      <Routes>
        <Route path="/code/:id" element={<CodePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  FakeEventSource.last = null
  vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource)
  papersApi.getPaper.mockResolvedValue({
    id: 7,
    filename: 'paper.pdf',
    title: 'Paper',
    code_url: 'github.com/acme/repo',
    code_path: '/tmp/repo',
    code_status: 'ready',
    status: 'done',
  })
  codeApi.getCodeTree.mockResolvedValue({ name: 'repo', path: '', type: 'directory', children: [] })
  codeApi.getCodeAnalysis.mockRejectedValue(new Error('尚未分析'))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.resetAllMocks()
})

describe('代码分析进度', () => {
  it('把后端推来的阶段消息显示出来，而不是一直「正在分析」', async () => {
    const user = userEvent.setup()
    renderCodePage()

    await user.click(await screen.findByRole('button', { name: '开始分析' }))
    await waitFor(() => expect(FakeEventSource.last).not.toBeNull())

    expect(screen.getByRole('status', { name: '代码分析进度' })).toHaveTextContent('正在启动代码分析…')

    FakeEventSource.last!.emit('status', { message: '正在分析模块依赖...' })
    await waitFor(() => expect(screen.getByRole('status', { name: '代码分析进度' })).toHaveTextContent('正在分析模块依赖...'))

    FakeEventSource.last!.emit('status', { message: 'AI 正在映射论文与代码...' })
    await waitFor(() => expect(screen.getByRole('status', { name: '代码分析进度' })).toHaveTextContent('AI 正在映射论文与代码...'))
  })

  it('完成后取回分析结果并收起进度', async () => {
    const user = userEvent.setup()
    codeApi.getCodeAnalysis
      .mockRejectedValueOnce(new Error('尚未分析'))
      .mockResolvedValueOnce({ paper_id: 7, data_flow: 'flow', paper_code_mapping: [], dependencies: [] })

    renderCodePage()
    await user.click(await screen.findByRole('button', { name: '开始分析' }))
    await waitFor(() => expect(FakeEventSource.last).not.toBeNull())

    FakeEventSource.last!.emit('done', { message: '代码分析完成' })

    await waitFor(() => expect(FakeEventSource.last!.closed).toBe(true))
    await waitFor(() => expect(screen.queryByRole('status', { name: '代码分析进度' })).not.toBeInTheDocument())
    expect(codeApi.getCodeAnalysis).toHaveBeenCalledTimes(2)
  })

  it('后端报错时把错误原文显示出来', async () => {
    const user = userEvent.setup()
    renderCodePage()

    await user.click(await screen.findByRole('button', { name: '开始分析' }))
    await waitFor(() => expect(FakeEventSource.last).not.toBeNull())

    FakeEventSource.last!.emit('error', { message: '保存失败: 磁盘写入错误' })

    expect(await screen.findByRole('alert')).toHaveTextContent('保存失败: 磁盘写入错误')
  })

  it('连接中断（无 data）时给出连接层的说法', async () => {
    const user = userEvent.setup()
    renderCodePage()

    await user.click(await screen.findByRole('button', { name: '开始分析' }))
    await waitFor(() => expect(FakeEventSource.last).not.toBeNull())

    FakeEventSource.last!.emit('error')

    expect(await screen.findByRole('alert')).toHaveTextContent('代码分析连接中断，请重试')
  })
})
