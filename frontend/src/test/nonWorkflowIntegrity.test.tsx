import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useStreaming } from '@/hooks/useStreaming'
import { RagQueryBox } from '@/features/research/RagQueryBox'
import { CitationChip } from '@/features/research/CitationChip'
import { queryRag } from '@/api/research'

vi.mock('@/api/research', () => ({ queryRag: vi.fn() }))

class Source {
  static current: Source
  listeners = new Map<string, (e: MessageEvent) => void>()
  onerror: (() => void) | null = null
  onopen: (() => void) | null = null
  closed = false
  constructor(public url: string) { Source.current = this }
  addEventListener(name: string, fn: (e: MessageEvent) => void) { this.listeners.set(name, fn) }
  close() { this.closed = true }
  emit(name: string, data: object) { this.listeners.get(name)?.({ data: JSON.stringify(data) } as MessageEvent) }
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); vi.clearAllMocks() })

describe('non workflow integrity', () => {
  it('keeps partial analysis below 100%, closes the stream and does not retry a terminal event', () => {
    vi.stubGlobal('EventSource', Source)
    vi.useFakeTimers()
    const { result } = renderHook(() => useStreaming(12))
    const source = Source.current
    act(() => source.emit('partial', { message: '部分保存', missing_fields: ['problem'] }))
    expect(result.current.status).toBe('partial')
    expect(result.current.progress).toBe(90)
    expect(source.closed).toBe(true)
    act(() => source.onerror?.())
    act(() => vi.advanceTimersByTime(8000))
    expect(Source.current).toBe(source)
    expect(result.current.status).toBe('partial')
  })

  it('does not reconnect after a business error', () => {
    vi.stubGlobal('EventSource', Source)
    vi.useFakeTimers()
    const { result } = renderHook(() => useStreaming(12))
    const source = Source.current
    act(() => { source.emit('error', { message: '模型未配置' }); source.onerror?.() })
    act(() => vi.advanceTimersByTime(8000))
    expect(Source.current).toBe(source)
    expect(result.current.error).toBe('模型未配置')
    expect(result.current.connectionLost).toBe(false)
  })

  it('ignores old paper events after switching papers', () => {
    vi.stubGlobal('EventSource', Source)
    const { result, rerender } = renderHook(({ id }) => useStreaming(id), { initialProps: { id: 12 } })
    const old = Source.current
    rerender({ id: 13 })
    act(() => old.emit('field', { field_name: 'title', value: 'Old paper' }))
    expect(result.current.partialResult.title).toBeUndefined()
    act(() => Source.current.emit('field', { field_name: 'title', value: 'Current paper' }))
    expect(result.current.partialResult.title).toBe('Current paper')
  })

  it('still reconnects to the related-paper stream after main_done', () => {
    vi.stubGlobal('EventSource', Source)
    vi.useFakeTimers()
    const { result } = renderHook(() => useStreaming(12))
    const first = Source.current
    act(() => first.emit('main_done', {}))
    act(() => first.onerror?.())
    act(() => vi.advanceTimersByTime(1000))
    expect(Source.current).not.toBe(first)
    expect(result.current.status).toBe('done')
  })

  it('blocks repeated Enter and click while a RAG request is pending', async () => {
    let resolve!: (value: { answer: string; citations: [] }) => void
    vi.mocked(queryRag).mockReturnValue(new Promise(done => { resolve = done }))
    render(<MemoryRouter><RagQueryBox wsId={2} /></MemoryRouter>)
    const input = screen.getByRole('textbox', { name: '跨论文问答输入' })
    fireEvent.change(input, { target: { value: 'Question' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: '…' }))
    expect(queryRag).toHaveBeenCalledTimes(1)
    await act(async () => resolve({ answer: 'Answer', citations: [] }))
    expect(screen.getByText('Answer')).toBeInTheDocument()
  })

  it('links citations to their real PDF page and displays ranking without a percentage', () => {
    render(<MemoryRouter><CitationChip index={1} citation={{ paper_id: 12, paper_title: 'Paper', section_name: 'Body',
      snippet: 'original excerpt', score: .034, page_number: 7, source_kind: 'pdf_original' }} /></MemoryRouter>)
    expect(screen.getByRole('link')).toHaveAttribute('href', '/reader/12?page=7')
    expect(screen.getByText('排序分 0.0340')).toBeInTheDocument()
    expect(screen.getByRole('link').textContent).not.toContain('%')
  })
})
