/**
 * 领域探索页（/explore）渲染冒烟：
 * 表单与引导态、结论卡 / 检索透明度 / 总览复用 / 证据列表 / 报告的成品视图，
 * 以及无 Key 回退与检索未命中两个降级态。SSE 流用 mock（jsdom 无 EventSource）。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ExplorePage from '@/pages/ExplorePage'
import { useExplorationStream } from '@/hooks/useExplorationStream'
import { createExploration } from '@/api/exploration'
import type { ExplorationResult, ExplorationSynthesis } from '@/types'

vi.mock('@/hooks/useExplorationStream', () => ({
  useExplorationStream: vi.fn(),
}))
vi.mock('@/api/exploration', () => ({
  createExploration: vi.fn(),
  listExplorations: vi.fn().mockResolvedValue([]),
  deleteExploration: vi.fn(),
  getExploration: vi.fn(),
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
    const { unmount } = render(<ExplorePage />)
    expect(screen.getByPlaceholderText(/用 AI 识别肿瘤/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始探索' })).toBeEnabled()
    expect(screen.getByText(/输入一个问题开始/)).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText(/用 AI 识别肿瘤/), { target: { value: '肿瘤检测有人做吗' } })
    fireEvent.click(screen.getByRole('button', { name: '开始探索' }))
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith('肿瘤检测有人做吗', 'standard'))
    unmount()
  })

  it('成品视图：结论卡 / 透明度 / 总览复用 / 证据列表 / 报告齐备，证据可点开', () => {
    mockUse.mockReturnValue({ ...IDLE, status: 'done', result: makeResult(), synthesis: SYNTHESIS })
    render(<ExplorePage />)

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
    expect(screen.getByText('🏛️ 高被引经典')).toBeInTheDocument()
    expect(screen.getByText('🔥 近期上升')).toBeInTheDocument()

    // 证据列表首行（被引最高，同时出现在两个列表）→ 点开详情卡
    fireEvent.click(screen.getAllByText('Language model pretraining')[0])
    expect(screen.getAllByText('Language model pretraining').length).toBeGreaterThanOrEqual(2)
    // 详情卡的「速度 714.3/年」读数（列表行里也有同串，断言存在即可）
    expect(screen.getAllByText(/714\.3\/年/).length).toBeGreaterThan(0)
    expect(screen.getByText('Pretraining.')).toBeInTheDocument()

    // 热点 / 空白 / 后续
    expect(screen.getByText('多模态医学影像')).toBeInTheDocument()
    expect(screen.getByText('可解释性不足')).toBeInTheDocument()
    expect(screen.getByText('tumor detection benchmark')).toBeInTheDocument()

    // Markdown 报告
    expect(screen.getByText('这里是报告正文。')).toBeInTheDocument()
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
    render(<ExplorePage />)
    expect(screen.getByText(/未配置 LLM Key/)).toBeInTheDocument()
    expect(screen.getByText(/检索未命中/)).toBeInTheDocument()
  })

  it('失败态：展示错误信息', () => {
    mockUse.mockReturnValue({ ...IDLE, status: 'failed', error: 'provider 不可用' })
    render(<ExplorePage />)
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
    render(<ExplorePage />)
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '45')
    expect(screen.getByText('45%')).toBeInTheDocument()
    expect(screen.getByText(/正在进行：② 多源检索/)).toBeInTheDocument()
    expect(screen.getByText(/检索「tumor detection」命中 15 篇/)).toBeInTheDocument()
  })
})
