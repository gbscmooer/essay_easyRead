import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import LibraryPage from '@/pages/LibraryPage'
import { getPaperFacets, getMetadataStatus, listPapers, smartSearch } from '@/api/papers'

vi.mock('@/api/papers', () => ({ listPapers: vi.fn(), getPaperFacets: vi.fn(), getMetadataStatus: vi.fn(), smartSearch: vi.fn(),
  deletePaper: vi.fn(), refreshMetadata: vi.fn(), updatePaperMetadata: vi.fn(), promoteExpandedPaper: vi.fn() }))
vi.mock('@/api/workspaces', () => ({ listWorkspaces: vi.fn().mockResolvedValue([]), addPaperToWorkspace: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listPapers).mockResolvedValue({ papers: [], total: 0 })
  vi.mocked(smartSearch).mockResolvedValue({ results: [], total: 0, query: '' })
  vi.mocked(getPaperFacets).mockResolvedValue({ categories: [], subjects: ['Vision'], years: [], dates: [], sorts: [{ value: 'recent', label: '最近添加' }] })
  vi.mocked(getMetadataStatus).mockResolvedValue({ running: false, total: 0, completed: 0, unmatched: 0 })
})

it('含扩展改变浏览列表范围，后续筛选保留相同范围', async () => {
  render(<MemoryRouter><LibraryPage /></MemoryRouter>)
  await waitFor(() => expect(listPapers).toHaveBeenCalledWith(expect.objectContaining({ source: 'uploaded' }), expect.any(AbortSignal)))
  fireEvent.click(screen.getByLabelText('含扩展'))
  await waitFor(() => expect(listPapers).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'all', page: 1 }), expect.any(AbortSignal)))
  fireEvent.click(screen.getByText('筛选', { exact: true }))
  fireEvent.change(screen.getByLabelText('按方向筛选'), { target: { value: 'Vision' } })
  await waitFor(() => expect(listPapers).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'all', subject: 'Vision' }), expect.any(AbortSignal)))
})

it('切换筛选会取消旧请求，旧结果不会覆盖新列表', async () => {
  let resolveOld: (value: { papers: []; total: number }) => void = () => undefined
  vi.mocked(listPapers).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
  render(<MemoryRouter><LibraryPage /></MemoryRouter>)
  await waitFor(() => expect(listPapers).toHaveBeenCalledOnce())
  const oldSignal = vi.mocked(listPapers).mock.calls[0][1]!
  fireEvent.click(screen.getByLabelText('含扩展'))
  await screen.findByText('0 篇论文（含扩展）')
  expect(oldSignal.aborted).toBe(true)
  resolveOld({ papers: [], total: 99 })
  await waitFor(() => expect(screen.queryByText('99 篇论文（含扩展）')).not.toBeInTheDocument())
})
