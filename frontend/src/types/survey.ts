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
export interface ReportDelivery {
  /** 冻结答案的实际交付状态，创建报告版本不等于有答案。 */
  answer_status?: 'none' | 'partial' | 'complete'
  /** v3 交付契约：旧报告缺失该字段时按未知处理，不猜测 */
  delivery_status: 'ready' | 'partial' | 'unavailable'
  narrative_source: 'final_draft' | 'preliminary' | 'none' | 'frozen_answers'
  evidence_version?: string
  citation_map_version?: string | null
  failure_stage?: string | null
  error_code?: string | null
  attempts?: Record<string, unknown>
}
export interface NarrativeEntry {
  text: string
  display_text: string
  evidence_ids: string[]
  citation_numbers: number[]
  subquestion_id?: string | null
  role?: string | null
  statement_kind: 'fact' | 'synthesis_inference' | 'process_note'
  citation_status: 'ok' | 'missing' | 'unknown' | 'not_required'
  unknown_evidence?: string[]
}
export interface ReportNarrative {
  summary: NarrativeEntry[]
  findings: NarrativeEntry[]
  limitations: NarrativeEntry[]
  gaps: NarrativeEntry[]
  novelty: NarrativeEntry[]
}
export interface SurveyReport {
  workflow_revision?: string
  dossier_revision_id?: string
  chapters?: { question_id: string; title: string; text: string; status: string; answer_revision_id: string | null;
    requirement_ledger?: { unit_id: string; requirement: string; subject: string; resolved: boolean; gap: string }[];
    relations?: { relation_id: string; subject: string; predicate: string; object: string; scope: string | null;
      evidence_ids: string[]; verification_state: string; conditions: { answer_revision_id: string; description: string } }[];
    evidence: { evidence_id: string; asset_id: string; paper_key?: string; fact: string; source_kind: string; spans: import('./index').SurveyEvidenceSpan[] }[] }[]
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
  coverage: { corpus: number; with_material: number; selected: number; examined: number; used?: number; failed: number; full_text: number; abstract_only: number; rejected_rows: number }
  bibliometrics: { by_year: Record<string, number>; year_span: number[] }
  references: { key: string; title: string; year?: number | null; doi?: string | null; arxiv_id?: string | null; url?: string | null; pdf_url?: string | null; paper_id?: number | null }[]
  warnings: string[]
  search_summary?: SurveySearchSummary
  method?: Record<string, unknown>
  delivery?: ReportDelivery
  narrative?: ReportNarrative
  markdown: string
}
