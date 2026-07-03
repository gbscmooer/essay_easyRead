import { get, post, put, del } from './client'


export interface WorkspaceItem {
  id: number
  name: string
  paper_count: number
  created_at: string
  updated_at: string
}

export interface WorkspacePaper {
  id: number
  filename: string
  title: string | null
  venue: string | null
  year: number | null
  status: string
  code_url: string | null
  code_status: string
  source?: 'uploaded' | 'expanded' | 'promoted'
  level?: number
  parent_paper_id?: number | null
  read_level?: 'full' | 'digest' | 'none'   // 解读层级（持久化）：已精读 / 已速读 / 未解读
  created_at: string | null
  added_at: string | null
}

export interface WorkspaceDetail {
  id: number
  name: string
  papers: WorkspacePaper[]
  created_at: string
  updated_at: string
}

export interface WorkspaceInsightPaper {
  paper_id: number
  title: string
  venue: string | null
  year: number | null
  source: 'uploaded' | 'expanded' | 'promoted'
  read_level: 'full' | 'digest' | 'none'
  importance: number
  bridge_score: number
  cluster: number
  cluster_label: string | null
  reason: string
}

export interface WorkspaceClusterSummary {
  cluster: number
  label: string
  paper_count: number
  unread_count: number
  digest_count: number
  full_count: number
  top_paper_id: number | null
  top_paper_title: string | null
  average_importance: number
}

export interface WorkspaceInsightResponse {
  workspace_id: number
  graph_available: boolean
  paper_count: number
  read_counts: Record<'full' | 'digest' | 'none', number>
  source_counts: Record<string, number>
  modularity: number | null
  cluster_count: number
  clusters: WorkspaceClusterSummary[]
  top_papers: WorkspaceInsightPaper[]
  bridge_papers: WorkspaceInsightPaper[]
  next_reads: WorkspaceInsightPaper[]
  updated_at: string
}

export interface WorkspaceReadingPlanPaper {
  paper_id: number
  title: string
  venue: string | null
  year: number | null
  source: 'uploaded' | 'expanded' | 'promoted'
  read_level: 'full' | 'digest' | 'none'
  detail_level: 'full' | 'digest' | 'metadata'
  cluster_label: string | null
  stage: number
  score: number
  prerequisites: number[]
  reason: string
}

export interface WorkspaceReadingPlanStage {
  stage: number
  label: string
  objective: string
  papers: WorkspaceReadingPlanPaper[]
}

export interface WorkspaceReadingPlanEdge {
  source_paper_id: number
  target_paper_id: number
  kind: string
  weight: number
  reason: string
}

export interface WorkspaceReadingPlanResponse {
  workspace_id: number
  target_paper_id: number | null
  graph_available: boolean
  strategy: string
  coverage: {
    paper_count: number
    total_workspace_papers: number
    full: number
    digest: number
    none: number
    citation_edges: number
    graph_nodes: number
  }
  stages: WorkspaceReadingPlanStage[]
  edges: WorkspaceReadingPlanEdge[]
  updated_at: string
}

export interface WorkspaceSurveyMatrixRow {
  paper_id: number
  title: string
  venue: string | null
  year: number | null
  detail_level: 'full' | 'digest' | 'metadata'
  task: string
  method: string
  datasets: string[]
  baselines: string[]
  contributions: string[]
  limitations: string
  code_concepts: string[]
}

export interface WorkspaceSurveyCluster {
  label: string
  paper_count: number
  paper_ids: number[]
  years: number[]
  common_methods: string[]
  datasets: string[]
  summary: string
}

export interface WorkspaceSurveyDraftSection {
  key: 'overview' | 'methods' | 'experiments' | 'limitations'
  title: string
  content: string
}

export interface WorkspaceSurveyResponse {
  workspace_id: number
  paper_count: number
  detail_counts: Record<string, number>
  topics: string[]
  datasets: string[]
  baselines: string[]
  matrix: WorkspaceSurveyMatrixRow[]
  clusters: WorkspaceSurveyCluster[]
  draft_sections: WorkspaceSurveyDraftSection[]
  updated_at: string
}

export interface WorkspaceComparisonPaper {
  paper_id: number
  title: string
  venue: string | null
  year: number | null
  source: 'uploaded' | 'expanded' | 'promoted'
  read_level: 'full' | 'digest' | 'none'
  detail_level: 'full' | 'digest' | 'metadata'
}

export interface WorkspaceComparisonSection {
  key: 'purpose' | 'method' | 'experiments' | 'implementation' | 'metadata'
  label: string
  score: number
  summary: string
  shared: string[]
  unique_a: string[]
  unique_b: string[]
}

export interface WorkspaceComparisonOrder {
  first_paper_id: number
  first_title: string
  second_paper_id: number
  second_title: string
  reason: string
}

export interface WorkspaceComparisonResponse {
  workspace_id: number
  paper_a: WorkspaceComparisonPaper
  paper_b: WorkspaceComparisonPaper
  overall_score: number
  relation: string
  relation_reason: string
  shared_topics: string[]
  same_cluster: boolean
  cluster_label: string | null
  direct_edge_kinds: string[]
  reading_order: WorkspaceComparisonOrder
  sections: WorkspaceComparisonSection[]
  updated_at: string
}


export async function listWorkspaces(): Promise<WorkspaceItem[]> {
  return get<WorkspaceItem[]>('/workspaces')
}

export async function createWorkspace(name: string): Promise<WorkspaceItem> {
  return post<WorkspaceItem>('/workspaces', { name })
}

export async function getWorkspace(id: number): Promise<WorkspaceDetail> {
  return get<WorkspaceDetail>(`/workspaces/${id}`)
}

export async function getWorkspaceInsights(id: number): Promise<WorkspaceInsightResponse> {
  return get<WorkspaceInsightResponse>(`/workspaces/${id}/insights`)
}

export async function getWorkspaceReadingPlan(
  id: number,
  targetPaperId?: number | null,
): Promise<WorkspaceReadingPlanResponse> {
  const query = targetPaperId ? `?target_paper_id=${targetPaperId}` : ''
  return get<WorkspaceReadingPlanResponse>(`/workspaces/${id}/reading-plan${query}`)
}

export async function getWorkspaceSurvey(id: number): Promise<WorkspaceSurveyResponse> {
  return get<WorkspaceSurveyResponse>(`/workspaces/${id}/survey`)
}

export async function compareWorkspacePapers(
  workspaceId: number,
  paperAId: number,
  paperBId: number,
): Promise<WorkspaceComparisonResponse> {
  return post<WorkspaceComparisonResponse>(`/workspaces/${workspaceId}/compare`, {
    paper_a_id: paperAId,
    paper_b_id: paperBId,
  })
}

export async function updateWorkspace(id: number, name: string): Promise<WorkspaceItem> {
  return put<WorkspaceItem>(`/workspaces/${id}`, { name })
}

export async function deleteWorkspace(id: number): Promise<void> {
  await del(`/workspaces/${id}`)
}

export async function addPaperToWorkspace(workspaceId: number, paperId: number): Promise<void> {
  await post(`/workspaces/${workspaceId}/papers`, { paper_id: paperId })
}

export async function removePaperFromWorkspace(workspaceId: number, paperId: number): Promise<void> {
  await del(`/workspaces/${workspaceId}/papers/${paperId}`)
}

export async function promotePaper(workspaceId: number, paperId: number): Promise<void> {
  await post(`/workspaces/${workspaceId}/papers/${paperId}/promote`, {})
}
