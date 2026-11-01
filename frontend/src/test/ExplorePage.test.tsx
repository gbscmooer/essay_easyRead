/**
 * 文献研究页（/explore、/explorer/:id）渲染冒烟：表单、预检、运行态、失败态、
 * 详情页的回答 / 检索过程 / 选中论文 / 领域概览、历史抽屉与计时面板。SSE 流用 mock。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ExplorePage from '@/pages/ExplorePage'
import { useExplorationStream } from '@/hooks/useExplorationStream'
import {
  createExploration, deleteExploration, getExploration, getExplorationMessages, listExplorations, preflightExploration,
} from '@/api/exploration'
import { getWorkspace } from '@/api/workspaces'
import type { CrawlReport, ExplorationResult } from '@/types'

function renderPage(path = '/explore') {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/explore" element={<ExplorePage />} />
    <Route path="/explorer/:id" element={<ExplorePage />} />
  </Routes></MemoryRouter>)
}

vi.mock('@/hooks/useExplorationStream', () => ({ useExplorationStream: vi.fn() }))
vi.mock('@/api/workspaces', () => ({
  listWorkspaces: vi.fn().mockResolvedValue([{ id: 1, name: 'Seed collection', paper_count: 1 }]),
  getWorkspace: vi.fn().mockResolvedValue({ id: 1, name: 'Seed collection', papers: [{ id: 10, title: 'Seed paper', level: 1 }] }),
}))
vi.mock('@/api/exploration', () => ({
  createExploration: vi.fn(),
  listExplorations: vi.fn().mockResolvedValue([]),
  deleteExploration: vi.fn(),
  getResearchVersions: vi.fn().mockResolvedValue({ run_id: 7, current: { id: 7, question: 'q', status: 'done', depth: 'standard', created_at: '2026-01-01', conditions: [], reused: false }, ancestors: [], children: [] }),
  reviseResearch: vi.fn(),
  pauseExploration: vi.fn(),
  resumeExploration: vi.fn(),
  cancelExploration: vi.fn(),
  getExploration: vi.fn(),
  getExplorationMessages: vi.fn().mockResolvedValue([]),
  askExploration: vi.fn(),
  reportUrl: (id: number, format = 'markdown') => `/api/exploration/runs/${id}/report?format=${format}`,
  preflightExploration: vi.fn().mockResolvedValue({ checked_at: '2026-01-01T00:00:00Z', sources: [] }),
  getRunTokenUsage: vi.fn().mockResolvedValue({
    run_id: 0,
    totals: { calls: 0, input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, cache_write_tokens: 0, reasoning_tokens: 0, total_tokens: 0 },
    by_stage: [], recorded: false, source: 'records',
  }),
}))

const mockUse = vi.mocked(useExplorationStream)
const mockCreate = vi.mocked(createExploration)
const IDLE = {
  status: 'idle' as const, stage: null, message: '', progress: 0, result: null, answer: null,
  crawl: { queries: [], candidates: 0, selected: 0, steps: [], recent: [] }, error: null, stageTimings: {},
}

const CRAWL: CrawlReport = {
  version: 'pasa_loop_v1', config: {}, phase: 'done', layer: 1, stop_reason: 'layers_done',
  candidates: 120, selected: 4, excluded: 0, expanded: 3,
  trace: [
    { action: 'search', query: 'tumor detection', hits: 40, new: 38, selected: 3, llm_calls: 4 },
    { action: 'expand', paper: 'k:a', title: 'Tumor detection with CNN', layer: 0, mode: 'sections', chosen: ['Related Work'], new: 20, selected: 1, llm_calls: 3 },
  ],
  selector: { seen: 120, judged: 118, llm_calls: 10, floor_rejected: 30, cap_rejected: 0, failed: 2, labels: { '2': 3, '1': 1, '0': 114 } },
  crawler: { llm_calls: 5, searches: 6, expanded: 3, expand_html: 2, expand_reflist: 1, expand_failed: 0 },
  sources: { arxiv: { calls: 6, hits: 60, errors: 0 }, openalex: { calls: 6, hits: 60, errors: 1 } },
  by_origin: { search: 3, expand: 1 }, by_depth: { '0': 3, '1': 1 },
}

function makeResult(overrides: Partial<ExplorationResult> = {}): ExplorationResult {
  const papers = [
    { key: 'k:a', title: 'Tumor detection with CNN', year: 2020, citation_count: 900, cluster: 0, cluster_label: 'tumor', doi: '10.1/a', abstract: 'We detect tumors.', label: 2 as const, origin: 'search', score: 1.2 },
    { key: 'k:b', title: 'Tumor segmentation transformer', year: 2023, citation_count: 300, cluster: 0, cluster_label: 'tumor', arxiv_id: '2310.1', abstract: null, label: 2 as const, origin: 'expand', via: 'Related Work', depth: 1, score: 1.1 },
    { key: 'k:c', title: 'Language model pretraining', year: 2019, citation_count: 5000, cluster: 1, cluster_label: 'language', doi: '10.1/c', abstract: 'Pretraining.', label: 1 as const, origin: 'search', score: 0.7 },
    { key: 'k:d', title: 'LLM for medicine', year: 2024, citation_count: 80, cluster: 1, cluster_label: 'language', abstract: null, label: 2 as const, origin: 'refine', score: 1.0 },
  ]
  const nodes = papers.map((p, i) => ({
    id: `x${i}`, kind: 'paper' as const, label: p.title,
    meta: { year: p.year, citation_count: p.citation_count, cluster: p.cluster, cluster_label: p.cluster_label, source: 'expanded' as const, importance: p.citation_count / 5000 },
  }))
  return {
    question: 'q', queries: ['tumor detection', 'cancer diagnosis AI'], papers, crawl: CRAWL,
    metrics: {
      total: 4, by_year: { '2019': 1, '2020': 1, '2023': 1, '2024': 1 }, recent_share: 0.5, year_span: [2019, 2024],
      top_cited: ['k:c', 'k:a', 'k:b', 'k:d'], rising: ['k:d'], clusters: 2, modularity: 0.4,
    },
    graph: { nodes, edges: [{ source: 'x0', target: 'x1', kind: 'related' as const, weight: 0.5 }],
      stats: { node_count: 4, edge_count: 1, by_kind: { related: 1 }, clusters: 2, modularity: 0.4 } },
    answer: {
      status: 'ok', text: 'CNNs detect tumors well [1].',
      references: [{ n: 1, key: 'k:a', title: 'Tumor detection with CNN', year: 2020, excerpt: 'We detect tumors.', excerpts: ['We detect tumors.'] }],
    },
    ...overrides,
  }
}

function detailRun(id: number, overrides: Record<string, unknown> = {}) {
  return { id, question: 'Saved question', depth: 'standard' as const, status: 'done' as const, provider: 'arxiv+openalex',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:10:00Z', result: makeResult(), ...overrides }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockUse.mockReturnValue(IDLE)
})

describe('ExplorePage', () => {
  it('初始态：表单 + 示例 + 引导文案，可提交研究', async () => {
    mockCreate.mockResolvedValue({ id: 7, question: 'q', depth: 'standard', status: 'pending', provider: '', created_at: '2026-01-01', updated_at: '2026-01-01' })
    vi.mocked(getExploration).mockResolvedValue({ id: 7, question: 'q', depth: 'standard', status: 'pending', provider: '', created_at: '2026-01-01', updated_at: '2026-01-01' })
    renderPage()
    expect(screen.getByRole('textbox', { name: '研究问题' })).toBeInTheDocument()
    expect(screen.getByText(/输入一个问题开始/)).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '研究问题' }), { target: { value: '肿瘤检测有人做吗' } })
    fireEvent.click(screen.getByRole('button', { name: '开始研究' }))
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith('肿瘤检测有人做吗', 'standard', { workspace_id: null, seed_paper_ids: null, search_online: true }))
  })

  it('工作区母论文进入同一任务并可关闭联网检索', async () => {
    mockCreate.mockResolvedValue({ id: 8, question: 'dataset survey', depth: 'standard', status: 'pending', provider: '', created_at: '2026-01-01', updated_at: '2026-01-01' })
    vi.mocked(getExploration).mockResolvedValue({ id: 8, question: 'dataset survey', depth: 'standard', status: 'pending', provider: '', created_at: '2026-01-01', updated_at: '2026-01-01' })
    renderPage('/explore?workspace_id=1')
    await waitFor(() => expect(getWorkspace).toHaveBeenCalledWith(1))
    await waitFor(() => expect(screen.getByRole('button', { name: '开始研究' })).toBeEnabled())
    fireEvent.change(screen.getByRole('textbox', { name: '研究问题' }), { target: { value: 'dataset survey' } })
    fireEvent.click(screen.getByRole('checkbox', { name: '联网补充文献' }))
    fireEvent.click(screen.getByRole('button', { name: '开始研究' }))
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith('dataset survey', 'standard', { workspace_id: 1, seed_paper_ids: [10], search_online: false }))
  })

  it('运行态：进度、当前阶段与实时检索过程', () => {
    mockUse.mockReturnValue({
      ...IDLE, status: 'running', stage: 'expand', message: '展开「Tumor detection with CNN」：新增 20 篇，选中 1 篇', progress: 45,
      crawl: { queries: ['tumor detection'], candidates: 120, selected: 4, steps: CRAWL.trace, recent: [{ key: 'k:b', title: 'Tumor segmentation transformer', label: 2 }] },
    })
    renderPage()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '45')
    expect(screen.getByText(/正在进行：② 引用展开/)).toBeInTheDocument()
    expect(screen.getByText(/新增 20 篇，选中 1 篇/)).toBeInTheDocument()
    expect(screen.getByLabelText('实时检索过程')).toHaveTextContent('候选 120 篇 · 选中 4 篇')
    expect(screen.getByText(/Tumor segmentation transformer · 直接相关/)).toBeInTheDocument()
  })

  it('失败态：展示错误信息与从断点继续', () => {
    mockUse.mockReturnValue({ ...IDLE, status: 'failed', error: 'provider 不可用' })
    renderPage()
    expect(screen.getByText(/研究失败：provider 不可用/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '从断点继续研究' })).toBeInTheDocument()
  })

  it('数据源体检：权限探测结果在开始研究前可见', async () => {
    vi.mocked(preflightExploration).mockResolvedValue({
      checked_at: '2026-09-08T00:00:00Z',
      sources: [
        { name: 'scopus', role: 'primary', status: 'auth_error', detail: 'HTTP 401：Key 无效', latency_ms: 210, http_status: 401 },
        { name: 'arxiv', role: 'supplement', status: 'rate_limited', detail: 'HTTP 429', http_status: 429 },
      ],
    })
    renderPage()
    expect(await screen.findByText('Scopus')).toBeInTheDocument()
    expect(screen.getByText('无权限')).toBeInTheDocument()
    expect(screen.getByText('限速中')).toBeInTheDocument()
  })

  it('详情页：回答带引用与摘录，检索过程列出动作与模型调用', async () => {
    vi.mocked(getExploration).mockResolvedValue(detailRun(31))
    renderPage('/explorer/31')
    await screen.findByRole('heading', { name: 'Saved question' })
    expect(screen.getByRole('region', { name: '直接回答' })).toHaveTextContent('CNNs detect tumors well')
    expect(screen.getByText('We detect tumors.', { selector: 'q' })).toBeInTheDocument()
    const process = screen.getByRole('region', { name: '检索过程' })
    expect(process).toHaveTextContent('候选 120 篇')
    expect(process).toHaveTextContent('直接相关 3 · 背景相关 1')
    expect(process).toHaveTextContent('模型调用 15 次')
    expect(process).toHaveTextContent('展开 3 篇（章节 2 · 参考文献列表 1）')
  })

  it('详情页：选中论文标出判定与来源，领域概览展示统计', async () => {
    vi.mocked(getExploration).mockResolvedValue(detailRun(32))
    renderPage('/explorer/32')
    await screen.findByRole('heading', { name: 'Saved question' })
    fireEvent.click(screen.getByRole('tab', { name: /选中论文/ }))
    const archive = screen.getByRole('tabpanel', { name: /选中论文/ })
    expect(archive).toHaveTextContent('引用展开 · 第 1 层')
    expect(archive).toHaveTextContent('背景相关')
    fireEvent.click(screen.getByRole('tab', { name: '领域概览' }))
    expect(screen.getByLabelText('选中论文统计')).toHaveTextContent('选中 4 篇')
    expect(screen.getByLabelText('证据列表')).toBeInTheDocument()
  })

  it('近期研究进入独立详情，刷新读取持久化对话', async () => {
    vi.mocked(listExplorations).mockResolvedValue([{ id: 17, question: 'Saved question', depth: 'standard', status: 'done', provider: 'arxiv', selected: 4, answer_status: 'ok', created_at: '2026-01-01' }])
    vi.mocked(getExploration).mockResolvedValue(detailRun(17))
    vi.mocked(getExplorationMessages).mockResolvedValue([{ id: 1, run_id: 17, question: 'Follow-up', answer: 'Saved answer [P1]', status: 'done', search_more: false, selected_keys: [], papers: [], evidence_keys: ['k:a'], context: { citations: { P1: 'k:a' } }, error_message: null, created_at: '2026-01-01', updated_at: '2026-01-01' }])
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /最近研究/ }))
    const link = await screen.findByRole('link', { name: /Saved question/ })
    expect(link).toHaveTextContent('选中 4 篇 · 已作答')
    fireEvent.click(link)
    await screen.findByRole('heading', { name: 'Saved question' })
    fireEvent.click(screen.getByRole('tab', { name: '连续追问' }))
    expect(await screen.findByText('Follow-up')).toBeVisible()
  })

  it('删除近期研究前要求二次确认', async () => {
    vi.mocked(listExplorations).mockResolvedValue([{ id: 18, question: 'Delete me', depth: 'standard', status: 'done', provider: 'arxiv', created_at: '2026-01-01' }])
    vi.mocked(deleteExploration).mockResolvedValue(undefined)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /最近研究/ }))
    fireEvent.click(await screen.findByRole('button', { name: '删除该记录' }))
    expect(deleteExploration).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '永久删除' }))
    await waitFor(() => expect(deleteExploration).toHaveBeenCalledWith(18))
  })

  it('计时面板：按单循环阶段展示耗时分配', async () => {
    vi.mocked(getExploration).mockResolvedValue(detailRun(21, {
      question: 'Timed question',
      result: makeResult({ sources: { stage_timeline: [
        { stage: 'search', started_at: '2026-01-01T00:00:00Z', duration_s: 120, status: 'completed' },
        { stage: 'expand', started_at: '2026-01-01T00:02:00Z', duration_s: 240, status: 'completed' },
        { stage: 'landscape', started_at: '2026-01-01T00:06:00Z', duration_s: 30, status: 'completed' },
        { stage: 'answer', started_at: '2026-01-01T00:06:30Z', duration_s: 210, status: 'completed' },
      ] } }),
    }))
    renderPage('/explorer/21')
    await screen.findByRole('heading', { name: 'Timed question' })
    const panel = screen.getByRole('region', { name: '研究耗时' })
    expect(panel).toHaveTextContent('10 分 0 秒')
    expect(panel).toHaveTextContent('① 检索与判定')
    expect(panel).toHaveTextContent('② 引用展开')
    expect(panel).toHaveTextContent('4 分 0 秒')
  })

  it('没有论文被选中时给出明确提示', async () => {
    vi.mocked(getExploration).mockResolvedValue(detailRun(40, { result: makeResult({ papers: [], answer: undefined }) }))
    renderPage('/explorer/40')
    await screen.findByRole('heading', { name: 'Saved question' })
    expect(screen.getByText('本次检索没有论文被判为相关')).toBeInTheDocument()
    expect(screen.getByText(/共检索到 120 篇候选/)).toBeInTheDocument()
  })
})
