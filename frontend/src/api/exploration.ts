import { get, post } from './client'
import type {
  ExplorationDepth, ExplorationMessage, ExplorationRun, ExplorationRunSummary, PreflightResponse,
  ResearchRevisionInput, ResearchSourceOptions, ResearchVersions, RunControlResponse, RunTokenUsage,
} from '@/types'

// 文献研究（单循环：检索 → 逐篇判定 → 按章节追引用 → 指针式作答）
export async function createExploration(question: string, depth: ExplorationDepth = 'standard', options: ResearchSourceOptions = {}): Promise<ExplorationRun> {
  return post<ExplorationRun>('/exploration/runs', { question, depth, ...options })
}

/** 数据源预检：带权限的真实最小请求探测各源（Scopus/OpenAlex/arXiv），401/403 在开始研究前就可见 */
export function preflightExploration(signal?: AbortSignal): Promise<PreflightResponse> {
  return get<PreflightResponse>('/exploration/preflight', signal)
}

/** 请求暂停：当前动作（一次检索 / 一批展开）结束后停止，断点保留 */
export function pauseExploration(runId: number) {
  return post<RunControlResponse>(`/exploration/runs/${runId}/pause`, {})
}

/** 从断点继续（同一动作继续，已判定的论文不再重判） */
export function resumeExploration(runId: number) {
  return post<RunControlResponse>(`/exploration/runs/${runId}/resume`, {})
}

export function cancelExploration(runId: number) {
  return post<RunControlResponse>(`/exploration/runs/${runId}/cancel`, {})
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

export function getResearchVersions(runId: number, signal?: AbortSignal) {
  return get<ResearchVersions>(`/exploration/runs/${runId}/versions`, signal)
}

export interface RunAdjustInput { exclude_keys: string[]; add_queries: string[]; note?: string }
export interface RunAdjustResponse {
  run_id: number
  status: string
  intervention: { at: string; stage: string | null; excluded: string[]; queries: string[]; note: string }
}

export function adjustExploration(runId: number, input: RunAdjustInput) {
  return post<RunAdjustResponse>(`/exploration/runs/${runId}/adjust`, input)
}

export function reviseResearch(runId: number, input: ResearchRevisionInput) {
  return post<ExplorationRun>(`/exploration/runs/${runId}/revise`, input)
}

/** 导出地址（Markdown / HTML / JSON） */
export function reportUrl(runId: number, format: 'markdown' | 'html' | 'json' = 'markdown') {
  return `/api/exploration/runs/${runId}/report?format=${format}`
}

export function askExploration(runId: number, question: string, searchMore: boolean, selectedKeys: string[] = []) {
  return post<ExplorationMessage>(`/exploration/runs/${runId}/messages`, {
    question, search_more: searchMore, selected_keys: selectedKeys,
  })
}

export async function deleteExploration(runId: number): Promise<void> {
  await post(`/exploration/runs/${runId}/remove`, {})
}

/** 单次研究的 LLM token 用量（按阶段：crawl_queries / select / crawl_sections / answer_* …） */
export function getRunTokenUsage(runId: number, signal?: AbortSignal): Promise<RunTokenUsage> {
  return get<RunTokenUsage>(`/exploration/runs/${runId}/token-usage`, signal)
}
