export interface SurveyEvidence { paper_key: string; quote: string; source_kind: string }
export interface SurveyRow {
  id: string
  evidence: SurveyEvidence[]
  [key: string]: string | string[] | SurveyEvidence[]
}
export interface CoverageMatrix {
  rows: string[]
  columns: string[]
  cells: { row: number; column: number; count: number; paper_keys: string[] }[]
}
export interface SurveySearchAttempt {
  phase: string
  round?: number | null
  provider: string
  query: string
  order: string
  limit?: number | null
  hits: number
}
export interface SurveySearchSummary {
  queries: string[]
  sources: { name?: string; role?: string; status?: string; detail?: string }[]
  attempts: SurveySearchAttempt[]
  attempt_count: number
  total_channel_hits: number
  directions: string[]
  stop_reason: string
  interpretation: string
}
export interface SurveyReport {
  version: number
  title: string
  created_at: string
  status: 'completed' | 'partial' | 'unavailable' | 'no_results'
  summary: string
  findings: string[]
  gaps: string[]
  tables: Record<'frameworks' | 'metrics' | 'tasks' | 'taxonomy', SurveyRow[]>
  matrices: { aspect_metric: CoverageMatrix; task_dataset: CoverageMatrix }
  coverage: { corpus: number; with_material: number; selected: number; examined: number; failed: number; full_text: number; abstract_only: number; rejected_rows: number }
  bibliometrics: { by_year: Record<string, number>; year_span: number[] }
  references: { key: string; title: string; year?: number | null; doi?: string | null; arxiv_id?: string | null; url?: string | null; pdf_url?: string | null; paper_id?: number | null }[]
  warnings: string[]
  search_summary?: SurveySearchSummary
  method?: Record<string, unknown>
  markdown: string
}
