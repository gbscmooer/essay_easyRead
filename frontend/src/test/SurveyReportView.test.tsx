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
})
