export interface SurveyEvidence {
  paper_key: string
  quote: string
  source_kind: string
  evidence_id?: string
  page?: number | null
  section?: string | null
  table?: string | null
  location_status?: 'resolved' | 'partial' | 'unavailable'
}
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
  quality_status?: 'unchecked' | 'internally_checked' | 'needs_review' | 'human_reviewed'
  summary: string
  findings: string[]
  gaps: string[]
  tables: Record<'frameworks' | 'metrics' | 'tasks' | 'taxonomy', SurveyRow[]> & { experimental_comparisons?: SurveyRow[] }
  limitations?: string[]
  novelty?: string
  subquestions?: { id: string; label: string; query: string }[]
  evidence_store?: { version: number; evidence_version: string; count: number; records: unknown[] }
  evidence_coverage?: Record<string, { evidence_count: number; verified_count: number }>
  matrices: { aspect_metric: CoverageMatrix; task_dataset: CoverageMatrix }
  coverage: { corpus: number; with_material: number; selected: number; examined: number; failed: number; full_text: number; abstract_only: number; rejected_rows: number }
  bibliometrics: { by_year: Record<string, number>; year_span: number[] }
  references: { key: string; title: string; year?: number | null; doi?: string | null; arxiv_id?: string | null; url?: string | null; pdf_url?: string | null; paper_id?: number | null }[]
  warnings: string[]
  search_summary?: SurveySearchSummary
  method?: Record<string, unknown>
  markdown: string
}
