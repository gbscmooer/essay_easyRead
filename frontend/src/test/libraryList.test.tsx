import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PaperList, type PaperSelection } from '@/features/library/PaperList'
import type { PaperFacets } from '@/api/papers'
import type { Paper } from '@/types'

function makePaper(id: number, title: string, extra: Partial<Paper> = {}): Paper {
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
    created_at: '2026-08-13T00:00:00Z',
    updated_at: '2026-08-13T00:00:00Z',
    ...extra,
  }
}

const FACETS: PaperFacets = {
  categories: ['cs.CV', 'cs.LG'],
  years: [2025, 2019],
  dates: ['2026-08-13'],
  sorts: [
    { value: 'recent', label: '最近添加' },
    { value: 'title', label: '标题 A→Z' },
  ],
}

function emptySelection(overrides: Partial<PaperSelection> = {}): PaperSelection {
  return {
    ids: new Set(),
    toggle: vi.fn(),
    selectPage: vi.fn(),
    clear: vi.fn(),
    actions: null,
    ...overrides,
  }
}

function renderList(props: Partial<React.ComponentProps<typeof PaperList>> = {}) {
  const defaults: React.ComponentProps<typeof PaperList> = {
    papers: [makePaper(1, 'Attention')],
    total: 1,
    onSelect: vi.fn(),
    onDelete: vi.fn(),
    onSearch: vi.fn(),
    onFilter: vi.fn(),
    onPageChange: vi.fn(),
    page: 1,
    perPage: 20,
    globalSearch: false,
    onToggleGlobal: vi.fn(),
    includeExpanded: false,
    onToggleExpanded: vi.fn(),
    searchResults: null,
    searchLoading: false,
    searchQuery: '',
    facets: FACETS,
    sort: 'recent',
    onSortChange: vi.fn(),
    selection: emptySelection(),
  }
  const merged = { ...defaults, ...props }
  render(<PaperList {...merged} />)
  return merged
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.resetAllMocks()
})

describe('论文库搜索', () => {
  beforeEach(() => { vi.useFakeTimers() })

  it('停止输入后自动发起检索，不必敲回车', () => {
    const props = renderList()
    fireEvent.change(screen.getByLabelText('搜索论文'), { target: { value: 'diffusion' } })

    expect(props.onSearch).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(400) })
    expect(props.onSearch).toHaveBeenCalledWith('diffusion')
  })

  it('连续输入只发一次请求', () => {
    const props = renderList()
    const input = screen.getByLabelText('搜索论文')
    fireEvent.change(input, { target: { value: 'de' } })
    act(() => { vi.advanceTimersByTime(200) })
    fireEvent.change(input, { target: { value: 'deep' } })
    act(() => { vi.advanceTimersByTime(400) })

    expect(props.onSearch).toHaveBeenCalledTimes(1)
    expect(props.onSearch).toHaveBeenCalledWith('deep')
  })

  it('清除按钮清空输入并退出搜索', () => {
    const props = renderList({ searchQuery: 'deep' })
    fireEvent.change(screen.getByLabelText('搜索论文'), { target: { value: 'deep' } })

    fireEvent.click(screen.getByLabelText('清除搜索'))
    expect((screen.getByLabelText('搜索论文') as HTMLInputElement).value).toBe('')
    expect(props.onSearch).toHaveBeenCalledWith('')
  })

  it('搜索框挂着全局 / 快捷键的锚点', () => {
    renderList()
    expect(screen.getByLabelText('搜索论文')).toHaveAttribute('data-shortcut-search')
  })
})

describe('筛选与排序', () => {
  it('筛选项来自全库 facets，而不是当前页的论文', () => {
    renderList({ papers: [makePaper(1, 'Attention', { category: 'cs.CL', year: 2017 })] })

    const categorySelect = screen.getByLabelText('按方向筛选')
    expect(Array.from(categorySelect.querySelectorAll('option')).map(o => o.textContent))
      .toEqual(['全部方向', 'cs.CV', 'cs.LG'])
    expect(Array.from(screen.getByLabelText('按年份筛选').querySelectorAll('option')).map(o => o.textContent))
      .toEqual(['全部年份', '2025', '2019'])
  })

  it('facets 不可用时回落到当前页派生的选项', () => {
    renderList({
      facets: null,
      papers: [makePaper(1, 'Attention', { category: 'cs.CL', year: 2017 })],
    })

    expect(Array.from(screen.getByLabelText('按方向筛选').querySelectorAll('option')).map(o => o.textContent))
      .toEqual(['全部方向', 'cs.CL'])
  })

  it('切换排序会把新排序报上去', () => {
    const props = renderList()
    fireEvent.change(screen.getByLabelText('排序方式'), { target: { value: 'title' } })
    expect(props.onSortChange).toHaveBeenCalledWith('title')
  })

  it('搜索结果按命中数排序，此时排序下拉禁用', () => {
    renderList({ searchResults: [], searchQuery: 'deep' })
    expect(screen.getByLabelText('排序方式')).toBeDisabled()
  })
})

describe('批量选择', () => {
  it('勾选一行不会顺带打开这篇论文', () => {
    const toggle = vi.fn()
    const props = renderList({ selection: emptySelection({ toggle }) })

    fireEvent.click(screen.getByLabelText('选择 Attention'))
    expect(toggle).toHaveBeenCalledWith(1)
    expect(props.onSelect).not.toHaveBeenCalled()
  })

  it('有勾选时才出现批量操作条，并显示已选数量', () => {
    renderList()
    expect(screen.queryByText('全选本页')).not.toBeInTheDocument()

    cleanup()
    renderList({
      papers: [makePaper(1, 'Attention'), makePaper(2, 'ResNet')],
      selection: emptySelection({ ids: new Set([1]) }),
    })
    expect(screen.getByText('全选本页')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('搜索结果模式下不出现批量操作条', () => {
    renderList({
      searchResults: [],
      searchQuery: 'deep',
      selection: emptySelection({ ids: new Set([1]) }),
    })
    expect(screen.queryByText('全选本页')).not.toBeInTheDocument()
  })
})
