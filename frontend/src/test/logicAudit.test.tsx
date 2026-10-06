import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnalysisResult, GraphNode } from '@/types'

vi.mock('@/api/research', () => ({ digestPaper: vi.fn() }))
vi.mock('@/api/code', () => ({ getCodeFile: vi.fn() }))
vi.mock('@/api/notes', () => ({ listNotes: vi.fn(), createNote: vi.fn(), updateNote: vi.fn(), deleteNote: vi.fn() }))
vi.mock('@/api/papers', () => ({ importRelatedPapers: vi.fn(), refreshRelatedPapers: vi.fn(), exportBibtex: vi.fn() }))
vi.mock('@/api/workspaces', () => ({ listWorkspaces: vi.fn() }))
vi.mock('@/hooks/useConfig', () => ({ useUIConfig: () => ({ theme: 'light', custom_themes: [] }) }))
vi.mock('@/hooks/useToast', () => ({ toast: vi.fn() }))
vi.mock('@/features/analysis/TagManager', () => ({ TagManager: () => null }))

import { digestPaper } from '@/api/research'
import { getCodeFile } from '@/api/code'
import { listNotes } from '@/api/notes'
import { importRelatedPapers } from '@/api/papers'
import { listWorkspaces } from '@/api/workspaces'
import { NodeDetail } from '@/features/research/NodeDetail'
import { NotesPanel } from '@/features/analysis/NotesPanel'
import { CodeViewer } from '@/features/code/CodeViewer'
import { FileBrowser } from '@/features/code/FileBrowser'
import { OverviewTab } from '@/features/analysis/tabs/OverviewTab'
import { useResearchStream } from '@/hooks/useResearchStream'

const deferred = <T,>() => {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}

class MockEventSource {
  static current: MockEventSource
  listeners = new Map<string, (event: Event) => void>()
  close = vi.fn()
  constructor() { MockEventSource.current = this }
  addEventListener(name: string, listener: (event: Event) => void) { this.listeners.set(name, listener) }
  emit(name: string, value?: object) { this.listeners.get(name)?.(value ? { data: JSON.stringify(value) } as MessageEvent : new Event(name)) }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0))
  vi.stubGlobal('cancelAnimationFrame', clearTimeout)
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('审计回归', () => {
  it('工作区研究断网保留自动重连，服务端失败才关闭订阅', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const { result } = renderHook(() => useResearchStream(2))
    act(() => MockEventSource.current.emit('error'))
    expect(result.current.status).toBe('running')
    expect(result.current.progressMessage).toContain('重新连接')
    expect(MockEventSource.current.close).not.toHaveBeenCalled()
    act(() => MockEventSource.current.emit('error', { message: '真实失败' }))
    expect(result.current.status).toBe('failed')
    expect(result.current.error).toBe('真实失败')
    expect(MockEventSource.current.close).toHaveBeenCalledOnce()
  })

  it('换论文后速读的旧响应不能显示在新节点', async () => {
    const pending = deferred<Awaited<ReturnType<typeof digestPaper>>>()
    vi.mocked(digestPaper).mockReturnValue(pending.promise)
    const a: GraphNode = { id: 'a', kind: 'paper', label: 'A', meta: { paper_id: 1 } }
    const b: GraphNode = { id: 'b', kind: 'paper', label: 'B', meta: { paper_id: 2 } }
    const view = render(<MemoryRouter><NodeDetail node={a} onClose={() => {}} /></MemoryRouter>)
    fireEvent.click(screen.getByText('速读这篇（轻量，省 token）'))
    view.rerender(<MemoryRouter><NodeDetail node={b} onClose={() => {}} /></MemoryRouter>)
    await act(async () => pending.resolve({ paper_id: 1, digest: { summary: 'A summary' } } as Awaited<ReturnType<typeof digestPaper>>))
    expect(screen.queryByText('A summary')).not.toBeInTheDocument()
    expect(screen.getByText('未解读')).toBeInTheDocument()
    expect(screen.getByText('速读这篇（轻量，省 token）')).toBeInTheDocument()
  })

  it('切论文立即清空笔记和编辑态，并忽略旧列表响应', async () => {
    const old = deferred<Awaited<ReturnType<typeof listNotes>>>()
    vi.mocked(listNotes).mockReturnValueOnce(old.promise).mockResolvedValueOnce([])
    const view = render(<NotesPanel paperId={1} />)
    fireEvent.change(screen.getByPlaceholderText('添加笔记…'), { target: { value: 'A draft' } })
    view.rerender(<NotesPanel paperId={2} />)
    await act(async () => old.resolve([{ id: 1, content: 'A notes', paper_id: 1 } as Awaited<ReturnType<typeof listNotes>>[number]]))
    expect(screen.queryByText('A notes')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText('添加笔记…')).toHaveValue('')
  })

  it('代码加载完成后跳到虚拟窗口外的行，重复请求再次跳转', async () => {
    const pending = deferred<Awaited<ReturnType<typeof getCodeFile>>>()
    vi.mocked(getCodeFile).mockReturnValue(pending.promise)
    const view = render(<CodeViewer paperId={1} filePath="large.txt" scrollToLine={2950} navigationId={1} />)
    expect(HTMLElement.prototype.scrollTo).not.toHaveBeenCalled()
    await act(async () => pending.resolve({ content: Array.from({ length: 3000 }, (_, i) => `line ${i}`).join('\n') } as Awaited<ReturnType<typeof getCodeFile>>))
    await waitFor(() => expect(HTMLElement.prototype.scrollTo).toHaveBeenCalled())
    expect(vi.mocked(HTMLElement.prototype.scrollTo).mock.calls[0][0]).toMatchObject({ top: expect.any(Number) })
    expect((vi.mocked(HTMLElement.prototype.scrollTo).mock.calls[0][0] as ScrollToOptions).top).toBeGreaterThan(50000)
    expect(view.container.querySelector('#line-2950')).toBeInTheDocument()
    const count = vi.mocked(HTMLElement.prototype.scrollTo).mock.calls.length
    view.rerender(<CodeViewer paperId={1} filePath="large.txt" scrollToLine={2950} navigationId={2} />)
    await waitFor(() => expect(vi.mocked(HTMLElement.prototype.scrollTo).mock.calls.length).toBeGreaterThan(count))
  })

  it('从其他面板选中深层文件会展开祖先目录', () => {
    const tree = { name: 'repo', path: '.', type: 'dir' as const, children: [{ name: 'a', path: 'a', type: 'dir' as const, children: [{ name: 'b', path: 'a/b', type: 'dir' as const, children: [{ name: 'deep.py', path: 'a/b/deep.py', type: 'file' as const }] }] }] }
    const view = render(<FileBrowser tree={tree} selected={null} onSelect={() => {}} />)
    expect(screen.queryByText('deep.py')).not.toBeInTheDocument()
    view.rerender(<FileBrowser tree={tree} selected="a/b/deep.py" onSelect={() => {}} />)
    expect(screen.getByText('deep.py')).toBeInTheDocument()
  })

  it('乱序勾选只把导入结果显示在对应论文，登记状态单独汇总', async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([{ id: 1, name: '目标工作区', paper_count: 0 }] as Awaited<ReturnType<typeof listWorkspaces>>)
    vi.mocked(importRelatedPapers).mockResolvedValue({ results: [
      { title: 'Paper C', status: 'registered', message: 'C已登记' },
      { title: 'Paper A', status: 'downloaded', message: 'A已下载' },
    ] })
    const result = { related_papers: ['A', 'B', 'C'].map(letter => ({ title: `Paper ${letter}`, doi: '', url: '' })) } as AnalysisResult
    render(<OverviewTab result={result} paperId={1} />)
    fireEvent.click(screen.getByText('导入到工作区'))
    await screen.findByText('导入选中 (0)')
    const row = (letter: string) => screen.getByText(`Paper ${letter}`).closest('.list-row') as HTMLElement
    fireEvent.click(within(row('C')).getByRole('button'))
    fireEvent.click(within(row('A')).getByRole('button'))
    fireEvent.click(screen.getByText('导入选中 (2)'))
    fireEvent.click(await screen.findByText('目标工作区'))
    await screen.findByText('C已登记')
    expect(within(row('C')).getByText('C已登记')).toBeInTheDocument()
    expect(within(row('A')).getByText('A已下载')).toBeInTheDocument()
    expect(within(row('B')).queryByText(/已登记|已下载/)).not.toBeInTheDocument()
    expect(screen.getByText('已登记 1')).toBeInTheDocument()
  })
})
