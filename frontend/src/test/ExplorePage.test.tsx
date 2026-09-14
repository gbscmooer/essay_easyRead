/**
 * 领域探索页（/explore）渲染冒烟：
 * 表单与引导态、结论卡 / 检索透明度 / 总览复用 / 证据列表 / 报告的成品视图，
 * 以及无 Key 回退与检索未命中两个降级态。SSE 流用 mock（jsdom 无 EventSource）。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ExplorePage from '@/pages/ExplorePage'
import { useExplorationStream } from '@/hooks/useExplorationStream'
import { createExploration, deleteExploration } from '@/api/exploration'
import type { ExplorationResult, ExplorationSynthesis } from '@/types'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { getExploration, getExplorationMessages, listExplorations, preflightExploration } from '@/api/exploration'
import { getWorkspace } from '@/api/workspaces'

function renderPage(path = '/explore') {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/explore" element={<ExplorePage />} />
    <Route path="/explorer/:id" element={<ExplorePage />} />
  </Routes></MemoryRouter>)
}

vi.mock('@/hooks/useExplorationStream', () => ({
  useExplorationStream: vi.fn(),
}))
vi.mock('@/api/workspaces', () => ({
  listWorkspaces: vi.fn().mockResolvedValue([{ id: 1, name: 'Seed collection', paper_count: 1 }]),
  getWorkspace: vi.fn().mockResolvedValue({ id: 1, name: 'Seed collection', papers: [{ id: 10, title: 'Seed paper', level: 1 }] }),
}))
vi.mock('@/api/exploration', () => ({
  createExploration: vi.fn(),
  listExplorations: vi.fn().mockResolvedValue([]),
  deleteExploration: vi.fn(),
  getExploration: vi.fn(),
  getExplorationMessages: vi.fn().mockResolvedValue([]),
  askExploration: vi.fn(),
  regenerateReport: vi.fn(),
  preflightExploration: vi.fn().mockResolvedValue({ checked_at: '2026-01-01T00:00:00Z', sources: [] }),
}))

const mockUse = vi.mocked(useExplorationStream)
const mockCreate = vi.mocked(createExploration)

const IDLE = { status: 'idle' as const, stage: null, message: '', progress: 0, result: null, synthesis: null, error: null }

function makeResult(overrides: Partial<ExplorationResult> = {}): ExplorationResult {
  const papers = [
    { key: 'k:a', title: 'Tumor detection with CNN', year: 2020, citation_count: 900, citation_velocity: 128.6, surge: false, cluster: 0, cluster_label: 'tumor · detection', doi: '10.1/a', arxiv_id: null, authors: 'A et al.', abstract: 'We detect tumors.', source_query: 'tumor detection' },
    { key: 'k:b', title: 'Tumor segmentation transformer', year: 2023, citation_count: 300, citation_velocity: 100.0, surge: true, cluster: 0, cluster_label: 'tumor · detection', doi: null, arxiv_id: '2310.1', authors: null, abstract: null, source_query: 'tumor detection' },
    { key: 'k:c', title: 'Language model pretraining', year: 2019, citation_count: 5000, citation_velocity: 714.3, surge: false, cluster: 1, cluster_label: 'language · model', doi: '10.1/c', arxiv_id: null, authors: null, abstract: 'Pretraining.', source_query: 'language model' },
    { key: 'k:d', title: 'LLM for medicine', year: 2024, citation_count: 80, citation_velocity: 40.0, surge: true, cluster: 1, cluster_label: 'language · model', doi: null, arxiv_id: null, authors: null, abstract: null, source_query: 'language model' },
  ]
  const nodes = papers.map((p, i) => ({
    id: `x${i}`,
    kind: 'paper' as const,
    label: p.title,
    meta: {
      year: p.year, citation_count: p.citation_count, cluster: p.cluster,
      cluster_label: p.cluster_label, source: 'expanded' as const,
      importance: p.citation_count / 5000,
    },
  }))
  return {
    decompose: { intent: 'novelty', en_queries: ['tumor detection', 'cancer diagnosis AI'], focus_terms: ['肿瘤检测'], fallback: false },
    provider: 'openalex',
    papers,
    metrics: {
      total: 4, by_year: { '2019': 1, '2020': 1, '2023': 1, '2024': 1 },
      recent_share: 0.5, year_span: [2019, 2024],
      top_cited: ['k:c', 'k:a', 'k:b', 'k:d'], rising: ['k:c', 'k:d', 'k:a'],
      clusters: 2, modularity: 0.4,
    },
    graph: { nodes, edges: [
      { source: 'x0', target: 'x1', kind: 'related' as const, weight: 0.5 },
      { source: 'x2', target: 'x3', kind: 'related' as const, weight: 0.4 },
    ], stats: { node_count: 4, edge_count: 2, by_kind: { related: 2 }, clusters: 2, modularity: 0.4 } },
    ...overrides,
  }
}

const SYNTHESIS: ExplorationSynthesis = {
  verdict: { status: 'done_extensively', confidence: 0.85, explanation: '多篇高被引工作直接覆盖该问题。', evidence_keys: ['k:c', 'k:a'] },
  maturity: '已在多个公开基准上达到可用水平。',
  landscape_summary: '两条主流路线。',
  hotspots: ['多模态医学影像'],
  gaps: ['可解释性不足'],
  follow_ups: ['tumor detection benchmark'],
  paradigms: [],
  markdown_report: '## 结论段落标题\n\n这里是报告正文。',
  partial: false,
}

beforeEach(() => {
  vi.clearAllMocks()
  mockUse.mockReturnValue(IDLE)
})

describe('ExplorePage', () => {
  it('初始态：表单 + 示例 + 引导文案，可提交探索', async () => {
    mockCreate.mockResolvedValue({
      id: 7, question: 'q', depth: 'standard', status: 'pending',
      provider: '', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    })
    vi.mocked(getExploration).mockResolvedValue({ id: 7, question: 'q', depth: 'standard', status: 'pending', provider: '', created_at: '2026-01-01', updated_at: '2026-01-01' })
    const { unmount } = renderPage()
    expect(screen.getByRole('textbox', { name: '研究问题' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始研究' })).toBeEnabled()
    expect(screen.getByText(/输入一个问题开始/)).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: '研究问题' }), { target: { value: '肿瘤检测有人做吗' } })
    fireEvent.click(screen.getByRole('button', { name: '开始研究' }))
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith('肿瘤检测有人做吗', 'standard', { workspace_id: null, seed_paper_ids: null, search_online: true }))
    unmount()
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

  it('成品视图：结论卡 / 透明度 / 总览复用 / 证据列表 / 报告齐备，证据可点开', () => {
    mockUse.mockReturnValue({ ...IDLE, status: 'done', result: makeResult(), synthesis: SYNTHESIS })
    renderPage()

    // 结论卡
    expect(screen.getByText('已被充分实现')).toBeInTheDocument()
    expect(screen.getByText(/多篇高被引工作直接覆盖该问题/)).toBeInTheDocument()
    expect(screen.getByText(/已在多个公开基准上达到可用水平/)).toBeInTheDocument()

    // 检索透明度
    expect(screen.getByText('tumor detection')).toBeInTheDocument()
    expect(screen.getByText(/去重后/)).toHaveTextContent('4')
    expect(screen.getByText(/openalex/i)).toBeInTheDocument()

    // 领域总览（复用 DomainOverview：统计条 + 证据列表标题）
    expect(screen.getByText('领域总览 · 子方向 × 年份')).toBeInTheDocument()
    const statsStrip = document.querySelector('.rs-overview__stats')
    expect(statsStrip?.textContent).toContain('4 篇论文')
    expect(statsStrip?.textContent).toContain('2 个子方向')
    expect(screen.getByText('样本内高被引')).toBeInTheDocument()
    expect(screen.getByText('旧版引用速度排序')).toBeInTheDocument()
    expect(screen.getByText('旧版指标（未重新计算）')).toBeInTheDocument()
    expect(screen.queryByTitle('证据充分度')).not.toBeInTheDocument()
    expect(document.querySelector('.ex-verdict__conf')).toBeNull()

    // 证据列表首行（被引最高，同时出现在两个列表）→ 点开详情卡
    fireEvent.click(screen.getAllByRole('button', { name: '查看摘要：Language model pretraining' })[0])
    expect(screen.getAllByText('Language model pretraining').length).toBeGreaterThanOrEqual(2)
    // 累计年均被引是描述指标。
    expect(screen.getAllByText(/714\.3\/年/).length).toBeGreaterThan(0)
    expect(screen.getByText('Pretraining.')).toBeInTheDocument()

    // 热点 / 空白 / 后续
    expect(screen.getByText('多模态医学影像')).toBeInTheDocument()
    expect(screen.getByText('可解释性不足')).toBeInTheDocument()
    expect(screen.getByText('tumor detection benchmark')).toBeInTheDocument()

    // Markdown 报告
    expect(screen.getByText('这里是报告正文。')).toBeInTheDocument()
  })

  it('新运行按方法元数据展示完整年份与显著性，旧运行不被重新解释', () => {
    const result = makeResult()
    result.metrics.citation_method = {
      algorithm: 'conditional_poisson_rate_test', version: 1,
      adjustment: 'benjamini_hochberg', alpha: 0.05,
      recent_years: [2024, 2025], baseline_years: [2022, 2023],
    }
    result.papers[0] = {
      ...result.papers[0], trajectory: 'rising', highly_cited: true,
      recent_citations: 60, citation_velocity_2y: 30,
    }
    mockUse.mockReturnValue({ ...IDLE, status: 'done', result, synthesis: SYNTHESIS })
    renderPage()
    expect(screen.getByText('精确 Poisson + BH（v1）')).toBeInTheDocument()
    expect(screen.getByText(/α = 0.05 是显著性阈值，不是准确率/)).toBeInTheDocument()
    expect(screen.queryByText('旧版引用速度排序')).not.toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: '查看摘要：Tumor detection with CNN' })[0])
    expect(screen.getByText('最近两个完整年份被引 60')).toBeInTheDocument()
    expect(screen.getByText('最近两个完整年份年均 30/年')).toBeInTheDocument()
    expect(screen.getByText('样本内同年高被引')).toBeInTheDocument()
    expect(screen.getAllByText('引用率显著上升')).toHaveLength(2)
  })

  it('历史趋势保留旧版标签，不显示新检验的显著性', () => {
    const result = makeResult()
    result.papers[0] = { ...result.papers[0], trajectory: 'rising', surge: true, recent_citations: 60 }
    mockUse.mockReturnValue({ ...IDLE, status: 'done', result, synthesis: SYNTHESIS })
    renderPage()
    fireEvent.click(screen.getAllByRole('button', { name: '查看摘要：Tumor detection with CNN' })[0])
    expect(screen.getByText('旧版引用爬升')).toBeInTheDocument()
    expect(screen.getByText('旧版突增候选')).toBeInTheDocument()
    expect(screen.getByText('旧版近2年被引 60')).toBeInTheDocument()
    expect(screen.queryByText('引用率显著上升')).not.toBeInTheDocument()
    expect(screen.queryByText('精确 Poisson + BH（v1）')).not.toBeInTheDocument()
  })

  it('降级态：无 Key 回退提示与检索未命中提示', () => {
    mockUse.mockReturnValue({
      ...IDLE, status: 'done',
      result: makeResult({
        decompose: { intent: 'landscape', en_queries: ['原句'], focus_terms: [], fallback: true },
        papers: [], metrics: {
          total: 0, by_year: {}, recent_share: 0, year_span: [],
          top_cited: [], rising: [], clusters: 0, modularity: 0,
        },
        graph: { nodes: [], edges: [], stats: { node_count: 0, edge_count: 0, by_kind: {} } },
      }),
    })
    renderPage()
    expect(screen.getByText(/未配置 LLM Key/)).toBeInTheDocument()
    expect(screen.getByText(/检索未命中/)).toBeInTheDocument()
  })

  it('失败态：展示错误信息', () => {
    mockUse.mockReturnValue({ ...IDLE, status: 'failed', error: 'provider 不可用' })
    renderPage()
    expect(screen.getByText(/探索失败：provider 不可用/)).toBeInTheDocument()
  })

  it('运行态：展示进度条、当前阶段百分比与动态分析反馈', () => {
    mockUse.mockReturnValue({
      ...IDLE,
      status: 'running',
      stage: 'recall',
      message: '检索「tumor detection」命中 15 篇（累计 15 篇）',
      progress: 45,
    })
    renderPage()
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '45')
    expect(screen.getByText('45%')).toBeInTheDocument()
    expect(screen.getByText(/正在进行：② 多源检索/)).toBeInTheDocument()
    expect(screen.getByText(/检索「tumor detection」命中 15 篇/)).toBeInTheDocument()
  })

  it('数据源体检：权限探测结果在开始研究前可见，主源失效展示回退提示', async () => {
    vi.mocked(preflightExploration).mockResolvedValue({
      checked_at: '2026-09-08T00:00:00Z',
      sources: [
        { name: 'scopus', role: 'primary', status: 'auth_error', detail: 'HTTP 401：Key 无效', latency_ms: 210, http_status: 401 },
        { name: 'openalex', role: 'fallback', status: 'ok', detail: '检索服务正常', latency_ms: 95, http_status: 200 },
      ],
    })
    renderPage()
    expect(await screen.findByText('Scopus')).toBeInTheDocument()
    expect(screen.getByText('无权限')).toBeInTheDocument()
    expect(screen.getByText('OpenAlex')).toBeInTheDocument()
    expect(screen.getByText(/开始研究将自动回退 OpenAlex/)).toBeInTheDocument()
  })

  it('近期探索进入独立详情，刷新读取资料和持久化对话', async () => {
    vi.mocked(listExplorations).mockResolvedValue([{ id: 17, question: 'Saved question', depth: 'standard', status: 'done', provider: 'openalex', created_at: '2026-01-01' }])
    vi.mocked(getExploration).mockResolvedValue({ id: 17, question: 'Saved question', depth: 'standard', status: 'done', provider: 'openalex', created_at: '2026-01-01', updated_at: '2026-01-01', result: makeResult() })
    vi.mocked(getExplorationMessages).mockResolvedValue([{ id: 1, run_id: 17, question: 'Follow-up', answer: 'Saved answer [P1]', status: 'done', search_more: false, selected_keys: [], papers: [], evidence_keys: ['k:a'], context: { citations: { P1: 'k:a' } }, error_message: null, created_at: '2026-01-01', updated_at: '2026-01-01' }])
    renderPage()
    // 历史入口为抽屉式二级菜单：先展开再断言链接
    fireEvent.click(await screen.findByRole('button', { name: /最近研究/ }))
    const link = await screen.findByRole('link', { name: /Saved question/ })
    expect(link).toHaveAttribute('href', '/explorer/17')
    fireEvent.click(link)
    await screen.findByRole('heading', { name: 'Saved question' })
    fireEvent.click(screen.getByRole('tab', { name: '连续追问' }))
    expect(await screen.findByText('Follow-up')).toBeVisible()
    expect(screen.getByRole('link', { name: '[P1]' })).toHaveAttribute('href', 'https://doi.org/10.1/a')
    expect(screen.queryByPlaceholderText(/用 AI 识别肿瘤/)).not.toBeInTheDocument()
  })

  it('删除近期研究前要求二次确认', async () => {
    vi.mocked(listExplorations).mockResolvedValue([{ id: 18, question: 'Delete me', depth: 'standard', status: 'done', provider: 'openalex', created_at: '2026-01-01' }])
    vi.mocked(deleteExploration).mockResolvedValue(undefined)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /最近研究/ }))
    fireEvent.click(await screen.findByRole('button', { name: '删除该记录' }))
    expect(deleteExploration).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: '删除研究记录' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '永久删除' }))
    await waitFor(() => expect(deleteExploration).toHaveBeenCalledWith(18))
  })
})
