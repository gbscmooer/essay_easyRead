import { get, post, del } from './client'
import type { ExplorationDepth, ExplorationRun, ExplorationRunSummary, ExplorationMessage, PreflightResponse } from '@/types'

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

export async function listExplorations(limit = 20): Promise<ExplorationRunSummary[]> {
  return get<ExplorationRunSummary[]>(`/exploration/runs?limit=${limit}`)
}

export async function getExploration(runId: number, signal?: AbortSignal): Promise<ExplorationRun> {
  return get<ExplorationRun>(`/exploration/runs/${runId}`, signal)
}

export function getExplorationMessages(runId: number, signal?: AbortSignal) {
  return get<ExplorationMessage[]>(`/exploration/runs/${runId}/messages`, signal)
}

export function askExploration(runId: number, question: string, searchMore: boolean, selectedKeys: string[] = []) {
  return post<ExplorationMessage>(`/exploration/runs/${runId}/messages`, {
    question, search_more: searchMore, selected_keys: selectedKeys,
  })
}

export async function deleteExploration(runId: number): Promise<void> {
  await del(`/exploration/runs/${runId}`)
}
