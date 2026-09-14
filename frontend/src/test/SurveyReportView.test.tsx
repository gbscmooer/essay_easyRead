import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SurveyHeatmap, SurveyReportView } from '@/features/research/SurveyReportView'
import type { SurveyReport } from '@/types/survey'

const reference = { key: 'p1', title: 'Framework paper', doi: '10.1/framework', year: 2024 }
const evidence = [{ paper_key: 'p1', quote: 'Accuracy evaluates faithfulness.', source_kind: 'abstract' }]
const matrix = { rows: ['Faithfulness'], columns: ['Accuracy', 'Recall'], cells: [{ row: 0, column: 0, count: 1, paper_keys: ['p1'] }] }
const report: SurveyReport = {
  version: 1, title: 'Study', created_at: '2026-09-07', status: 'completed', summary: 'Summary [1]', findings: [], gaps: [],
  coverage: { corpus: 5, with_material: 3, selected: 2, examined: 2, failed: 0, full_text: 1, abstract_only: 1, rejected_rows: 0 },
  tables: { frameworks: [], taxonomy: [], metrics: [{ id: 'metrics-1', aspect: 'Faithfulness', metric: 'Accuracy', description: '', evidence }], tasks: [] },
  matrices: { aspect_metric: matrix, task_dataset: { rows: [], columns: [], cells: [] } },
  bibliometrics: { by_year: { '2024': 5 }, year_span: [2024, 2024] }, references: [reference],
  warnings: ['未提及不代表不适用'], markdown: '# Study',
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
})

describe('SurveyReportView', () => {
  it('renders evidence tables, coverage and links to complete exports', () => {
    const generate = vi.fn()
    render(<SurveyReportView report={report} runId={7} busy={false} onGenerate={generate} />)
    expect(screen.getByText('Summary of Metrics Applicable to Evaluation Aspects')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '下载 HTML 报告' })).toHaveAttribute('href', '/api/exploration/runs/7/report?format=html')
    expect(screen.getByRole('link', { name: '[1]' })).toHaveAttribute('href', 'https://doi.org/10.1/framework')
    fireEvent.click(screen.getByText('1 篇来源'))
    expect(screen.getByText('Accuracy evaluates faithfulness.')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '重新生成' }))
    expect(generate).toHaveBeenCalledOnce()
  })

  it('shows experimental conditions, stable evidence location and pending final audit', () => {
    const experimental: SurveyReport = {
      ...report,
      version: 2,
      quality_status: 'needs_review',
      tables: {
        ...report.tables,
        experimental_comparisons: [{
          id: 'exp-1', method: 'SF-Mamba-B', baseline_method: 'MambaVision-B', task: 'classification',
          dataset: 'ImageNet-1K', split: 'validation', metric: 'Throughput', value: '3534', baseline_value: '2974',
          unit: 'images/s', metric_direction: 'higher_is_better', hardware: 'NVIDIA A100', batch_size: '128',
          input_size: '224×224', precision: '', comparison_limit: 'Only the reported within-table setup is comparable.',
          evidence: [{ ...evidence[0], evidence_id: 'ev_123', page: 6, section: '4 Experiments', table: 'Table 1', location_status: 'resolved' }],
        }],
      },
      method: { final_audit: { status: 'failed' } },
    }
    render(<SurveyReportView report={experimental} runId={7} busy={false} onGenerate={vi.fn()} />)
    expect(screen.getByText('SF-Mamba-B')).toBeInTheDocument()
    expect(screen.getByText('NVIDIA A100')).toBeInTheDocument()
    const sourceToggles = screen.getAllByText('1 篇来源')
    fireEvent.click(sourceToggles[sourceToggles.length - 1])
    expect(screen.getByText('ev_123 · p.6 · 4 Experiments · Table 1')).toBeVisible()
    expect(screen.getByText(/终稿核验：failed/)).toBeInTheDocument()
    expect(screen.getByText('已生成 · 终稿待核验')).toBeInTheDocument()
  })

  it('heatmap opens supporting papers and keeps missing relations disabled', () => {
    render(<SurveyHeatmap matrix={matrix} title="维度 × 指标" references={new Map([['p1', reference]])} />)
    const empty = screen.getByRole('button', { name: 'Faithfulness，Recall：0 篇证据' })
    expect(empty).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Faithfulness，Accuracy：1 篇证据' }))
    expect(screen.getByRole('link', { name: 'Framework paper' })).toHaveAttribute('href', 'https://doi.org/10.1/framework')
  })

  it('busy/legacy records show recoverable report state', () => {
    render(<SurveyReportView runId={7} busy onGenerate={vi.fn()} />)
    expect(screen.getByRole('button', { name: '生成中' })).toBeDisabled()
    expect(screen.getByText(/报告生成中/)).toBeInTheDocument()
  })

  it('renders and exports a zero-result search audit instead of refusing a report', () => {
    const generate = vi.fn()
    const noResults: SurveyReport = {
      ...report, status: 'no_results', references: [], findings: [], gaps: [],
      coverage: { corpus: 0, with_material: 0, selected: 0, examined: 0, failed: 0, full_text: 0, abstract_only: 0, rejected_rows: 0 },
      tables: { frameworks: [], taxonomy: [], metrics: [], tasks: [] },
      matrices: { aspect_metric: { rows: [], columns: [], cells: [] }, task_dataset: { rows: [], columns: [], cells: [] } },
      search_summary: {
        queries: ['novel tumor idea'], sources: [{ name: 'openalex', status: 'ok' }],
        attempts: [{ phase: 'formal', round: 1, provider: 'openalex', query: 'novel tumor idea', order: 'relevance', limit: 40, hits: 0 }],
        attempt_count: 1, total_channel_hits: 0, directions: ['novel tumor idea review'],
        stop_reason: 'formal_recall_finished', interpretation: '零命中不能证明全领域不存在相关工作。',
      },
      warnings: ['系统不会用模型记忆填补结论。'],
    }
    render(<SurveyReportView report={noResults} runId={9} busy={false} onGenerate={generate} />)
    expect(screen.getByText('检索未命中 · 报告已生成')).toBeInTheDocument()
    expect(screen.getByText('novel tumor idea')).toBeInTheDocument()
    expect(screen.getByText('novel tumor idea review')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '下载 HTML 报告' })).toHaveAttribute('href', '/api/exploration/runs/9/report?format=html')
    fireEvent.click(screen.getByRole('button', { name: '重新生成' }))
    expect(generate).toHaveBeenCalledOnce()
  })
})
