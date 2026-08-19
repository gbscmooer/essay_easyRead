import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const notesApi = vi.hoisted(() => ({
  listNotes: vi.fn(),
  createNote: vi.fn(),
  updateNote: vi.fn(),
  deleteNote: vi.fn(),
}))
vi.mock('@/api/notes', () => notesApi)

import { NotesPanel } from '@/features/analysis/NotesPanel'
import { loadNoteDraft } from '@/utils/noteDrafts'

const store = new Map<string, string>()
const storageMock: Storage = {
  get length() { return store.size },
  clear: () => store.clear(),
  getItem: key => store.get(key) ?? null,
  key: index => Array.from(store.keys())[index] ?? null,
  removeItem: key => { store.delete(key) },
  setItem: (key, value) => { store.set(key, String(value)) },
}

const NOTE = {
  id: 1,
  paper_id: 7,
  content: '这里是一条已有笔记',
  created_at: '2026-08-19T00:00:00Z',
  updated_at: '2026-08-19T00:00:00Z',
}

beforeEach(() => {
  store.clear()
  Object.defineProperty(window, 'localStorage', { configurable: true, value: storageMock })
  notesApi.listNotes.mockResolvedValue([NOTE])
  notesApi.createNote.mockResolvedValue(undefined)
  notesApi.deleteNote.mockResolvedValue(undefined)
})

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('笔记草稿', () => {
  it('切走再回来，写了一半的笔记还在', async () => {
    const user = userEvent.setup()
    const { unmount } = render(<NotesPanel paperId={7} />)

    await user.type(screen.getByLabelText('新建笔记'), '这个方法的假设是')
    await waitFor(() => expect(loadNoteDraft(7)).toBe('这个方法的假设是'))

    unmount()   // Tab 切走 = 组件卸载
    render(<NotesPanel paperId={7} />)

    expect(await screen.findByDisplayValue('这个方法的假设是')).toBeInTheDocument()
  })

  it('草稿按论文分开存', async () => {
    const user = userEvent.setup()
    render(<NotesPanel paperId={7} />)
    await user.type(screen.getByLabelText('新建笔记'), '论文 7 的草稿')
    await waitFor(() => expect(loadNoteDraft(7)).toBe('论文 7 的草稿'))

    cleanup()
    render(<NotesPanel paperId={8} />)
    expect((screen.getByLabelText('新建笔记') as HTMLTextAreaElement).value).toBe('')
  })

  it('提交成功后清掉草稿', async () => {
    const user = userEvent.setup()
    render(<NotesPanel paperId={7} />)

    await user.type(screen.getByLabelText('新建笔记'), '提交这条')
    await user.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(notesApi.createNote).toHaveBeenCalledWith(7, '提交这条'))
    await waitFor(() => expect(loadNoteDraft(7)).toBe(''))
  })
})

describe('笔记快捷键与确认', () => {
  it('⌘/Ctrl + Enter 直接提交', async () => {
    const user = userEvent.setup()
    render(<NotesPanel paperId={7} />)

    await user.type(screen.getByLabelText('新建笔记'), '用快捷键提交')
    await user.keyboard('{Control>}{Enter}{/Control}')

    await waitFor(() => expect(notesApi.createNote).toHaveBeenCalledWith(7, '用快捷键提交'))
  })

  it('删除笔记要先确认，取消则不删', async () => {
    const user = userEvent.setup()
    render(<NotesPanel paperId={7} />)

    await user.click(await screen.findByRole('button', { name: '删除' }))
    expect(screen.getByText('删除笔记')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(notesApi.deleteNote).not.toHaveBeenCalled()
  })

  it('确认后才真的删除', async () => {
    const user = userEvent.setup()
    render(<NotesPanel paperId={7} />)

    await user.click(await screen.findByRole('button', { name: '删除' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '删除' }))

    await waitFor(() => expect(notesApi.deleteNote).toHaveBeenCalledWith(1))
  })
})
