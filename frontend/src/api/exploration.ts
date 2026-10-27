import { get, post } from './client'
import type { ExplorationDepth, ExplorationRun, ExplorationRunSummary, ExplorationMessage, PreflightResponse, RunTokenUsage, SurveyEvidenceDetail, SurveyEvidenceIndex } from '@/types'

// 领域探索（问句驱动免读领域认知，见 GOAL.md）
export async function createExploration(question: string, depth: ExplorationDepth = 'standard', options: import('@/types').ResearchSourceOptions = {}): Promise<ExplorationRun> {
  return post<ExplorationRun>('/exploration/runs', { question, depth, ...options })
}

/** 数据源预检：带权限的真实最小请求探测各源（Scopus/OpenAlex…），401/403 在开始研究前就可见 */
export function preflightExploration(signal?: AbortSignal): Promise<PreflightResponse> {
  return get<PreflightResponse>('/exploration/preflight', signal)
}

export function regenerateReport(runId: number) {
  return post<ExplorationRun>(`/exploration/runs/${runId}/report`, {})
}

/** 请求在安全点（阶段/轮次/篇目边界）暂停研究；断点已保留，可随时继续 */
export function pauseExploration(runId: number) {
  return post<{ run_id: number; status: string; detail: string }>(`/exploration/runs/${runId}/pause`, {})
}

/** 从断点继续失败/暂停的研究（跳过已完成的阶段与已提取的篇目） */
export function resumeExploration(runId: number) {
  return post<{ run_id: number; status: string; detail: string }>(`/exploration/runs/${runId}/resume`, {})
}

/** Explicitly requeue failed tasks with their frozen payload and remaining budget. */
export function retryExploration(runId: number) {
  return post<{ run_id: number; status: string; retried_tasks: number }>(`/exploration/runs/${runId}/retry`, { failed_only: true })
}

export async function listExplorations(limit = 20): Promise<ExplorationRunSummary[]> {
  return get<ExplorationRunSummary[]>(`/exploration/runs?limit=${limit}`)
}

export async function getExploration(runId: number, signal?: AbortSignal): Promise<ExplorationRun> {
  return get<ExplorationRun>(`/exploration/runs/${runId}`, signal)
}

export function getExplorationMessages(runId: number, signal?: AbortSignal) {
  return get<ExplorationMessage[]>(`/exploration/runs/${runId}/messages`, signal)
}

export function getResearchQuestions(runId: number, signal?: AbortSignal) {
  return get<import('@/types').ResearchQuestionsResponse>(`/exploration/runs/${runId}/questions`, signal)
}

export function getResearchVersions(runId: number, signal?: AbortSignal) {
  return get<import('@/types/researchRevision').ResearchVersions>(`/exploration/runs/${runId}/versions`, signal)
}

export function getLegacyVersions(runId: number, signal?: AbortSignal) {
  return get<import('@/types/researchRevision').LegacyVersions>(`/exploration/runs/${runId}/versions`, signal)
}

export interface RunAdjustInput { exclude_keys: string[]; add_queries: string[]; note?: string }
export interface RunAdjustResponse {
  run_id: number
  status: string
  completed_stages: string[]
  intervention: { at: string; stage: string | null; excluded: string[]; queries: string[]; note: string; invalidated_stages: string[] }
}

export function adjustExploration(runId: number, input: RunAdjustInput) {
  return post<RunAdjustResponse>(`/exploration/runs/${runId}/adjust`, input)
}

export function reviseResearch(runId: number, input: import('@/types/researchRevision').ResearchRevisionInput) {
  return post<ExplorationRun>(`/exploration/runs/${runId}/revise`, input)
}

export function cancelExploration(runId: number) {
  return post<import('@/types').SurveyRunControlResponse>(`/exploration/runs/${runId}/cancel`, {})
}

export function getExplorationEvidenceIndex(runId: number, subquestionId?: string, cursor = 0, limit = 20) {
  const params = new URLSearchParams({ cursor: String(cursor), limit: String(limit) })
  if (subquestionId) params.set('subquestion_id', subquestionId)
  return get<SurveyEvidenceIndex>(`/exploration/runs/${runId}/evidence?${params}`)
}

export function getExplorationEvidence(runId: number, evidenceId: string) {
  return get<SurveyEvidenceDetail>(`/exploration/runs/${runId}/evidence/${encodeURIComponent(evidenceId)}`)
}

export function askExploration(runId: number, question: string, searchMore: boolean, selectedKeys: string[] = []) {
  return post<ExplorationMessage>(`/exploration/runs/${runId}/messages`, {
    question, search_more: searchMore, selected_keys: selectedKeys,
  })
}

export async function deleteExploration(runId: number): Promise<void> {
  await post(`/exploration/runs/${runId}/remove`, {})
}

/** 单次探索 run 的 LLM token 用量（含追问/报告重生成；旧 run 回退 result 台账快照） */
export function getRunTokenUsage(runId: number, signal?: AbortSignal): Promise<RunTokenUsage> {
  return get<RunTokenUsage>(`/exploration/runs/${runId}/token-usage`, signal)
}
