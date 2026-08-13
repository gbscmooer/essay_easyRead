import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PaperList } from '@/features/library/PaperList'
import type { Paper } from '@/types'

const workspaceApi = vi.hoisted(() => ({
  listWorkspaces: vi.fn(),
  createWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
}))

vi.mock('@/api/workspaces', () => workspaceApi)

import WorkspacePage from '@/pages/WorkspacePage'

const PAPER: Paper = {
  id: 7,
  filename: 'indexed.pdf',
  title: 'Indexed Paper',
  venue: null,
  year: null,
  status: 'indexed',
  error_message: null,
  code_url: null,
  code_path: null,
  code_status: 'none',
  read_level: 'none',
  created_at: '2026-08-13T00:00:00Z',
  updated_at: '2026-08-13T00:00:00Z',
}

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('ledger inline keyboard interactions', () => {
  it.each(['Enter', ' '])('does not activate a paper row when delete handles %j', key => {
    const onSelect = vi.fn()
    render(
      <PaperList
        papers={[PAPER]}
        total={1}
        onSelect={onSelect}
        onDelete={vi.fn()}
        onSearch={vi.fn()}
        onFilter={vi.fn()}
        onPageChange={vi.fn()}
        page={1}
        perPage={20}
        globalSearch={false}
        onToggleGlobal={vi.fn()}
        includeExpanded={false}
        onToggleExpanded={vi.fn()}
        searchResults={null}
        searchLoading={false}
        searchQuery=""
      />,
    )

    expect(screen.getByText('已索引 · 待解析')).toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('button', { name: '删除论文 Indexed Paper' }), { key })
    expect(onSelect).not.toHaveBeenCalled()
  })

  it.each(['Enter', ' '])('does not navigate a workspace row when delete handles %j', async key => {
    workspaceApi.listWorkspaces.mockResolvedValueOnce([{
      id: 3,
      name: 'Vision Lab',
      paper_count: 2,
      created_at: '2026-08-13T00:00:00Z',
      updated_at: '2026-08-13T00:00:00Z',
    }])

    render(
      <MemoryRouter initialEntries={['/workspace']}>
        <Routes>
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="/workspace/:id" element={<p>已进入工作区</p>} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('Vision Lab')).toBeInTheDocument())

    fireEvent.keyDown(screen.getByRole('button', { name: '删除工作区 Vision Lab' }), { key })
    expect(screen.queryByText('已进入工作区')).not.toBeInTheDocument()
    expect(screen.getByText('Vision Lab')).toBeInTheDocument()
  })
})
