import { get, post, del } from './client'
import type { ExplorationDepth, ExplorationRun, ExplorationRunSummary } from '@/types'

// 领域探索（问句驱动免读领域认知，见 GOAL.md）
export async function createExploration(question: string, depth: ExplorationDepth = 'standard'): Promise<ExplorationRun> {
  return post<ExplorationRun>('/exploration/runs', { question, depth })
}

export async function listExplorations(limit = 20): Promise<ExplorationRunSummary[]> {
  return get<ExplorationRunSummary[]>(`/exploration/runs?limit=${limit}`)
}

export async function getExploration(runId: number): Promise<ExplorationRun> {
  return get<ExplorationRun>(`/exploration/runs/${runId}`)
}

export async function deleteExploration(runId: number): Promise<void> {
  await del(`/exploration/runs/${runId}`)
}
