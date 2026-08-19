import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Sidebar } from '@/components/Sidebar'
import type { Paper } from '@/types'

function makePaper(id: number, title: string): Paper {
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
    created_at: '2026-08-19T00:00:00Z',
    updated_at: '2026-08-19T00:00:00Z',
  }
}

function renderSidebar(props: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
  const merged: React.ComponentProps<typeof Sidebar> = {
    papers: [makePaper(1, 'Attention')],
    activeId: 1,
    onSelect: vi.fn(),
    onDelete: vi.fn(),
    onSearch: vi.fn(),
    searching: false,
    recentLimit: 30,
    totalCount: 42,
    ...props,
  }
  render(<MemoryRouter><Sidebar {...merged} /></MemoryRouter>)
  return merged
}

beforeEach(() => { vi.useFakeTimers() })

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.resetAllMocks()
})

describe('阅读页侧边栏检索', () => {
  it('关键词交给上层查全库，而不是只在已加载的几篇里过滤', () => {
    const props = renderSidebar()

    fireEvent.change(screen.getByLabelText('搜索论文'), { target: { value: 'resnet' } })
    act(() => { vi.advanceTimersByTime(400) })

    expect(props.onSearch).toHaveBeenCalledWith('resnet')
  })

  it('连续输入只发一次查询', () => {
    const onSearch = vi.fn()
    renderSidebar({ onSearch })
    const input = screen.getByLabelText('搜索论文')

    fireEvent.change(input, { target: { value: 'res' } })
    act(() => { vi.advanceTimersByTime(150) })
    fireEvent.change(input, { target: { value: 'resnet' } })
    act(() => { vi.advanceTimersByTime(400) })

    // 首次挂载会以空串同步一次，真正的关键词查询只有一次
    expect(onSearch.mock.calls.filter((call: unknown[]) => call[0] !== '')).toEqual([['resnet']])
  })

  it('未搜索时说明「只列出最近 N 篇」，并给出去论文库的出口', () => {
    renderSidebar({ papers: [makePaper(1, 'Attention')], totalCount: 42 })

    expect(screen.getByText(/仅列出最近 30 篇（共 42 篇）/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '论文库' })).toHaveAttribute('href', '/library')
  })

  it('全部论文都已列出时不提示范围', () => {
    renderSidebar({ papers: [makePaper(1, 'Attention')], totalCount: 1 })
    expect(screen.queryByText(/仅列出最近/)).not.toBeInTheDocument()
  })

  it('搜索无结果时说无匹配，而不是「暂无论文」', () => {
    renderSidebar({ papers: [], totalCount: 42 })
    fireEvent.change(screen.getByLabelText('搜索论文'), { target: { value: 'zzz' } })
    act(() => { vi.advanceTimersByTime(400) })

    expect(screen.getByText('无匹配论文')).toBeInTheDocument()
  })
})
