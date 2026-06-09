import { get, post, del } from './client'
import type {
  ResearchRunCreate, ResearchRun, ResearchGraph, RagQueryResponse,
} from '@/types'

export async function createRun(wsId: number, body: ResearchRunCreate): Promise<ResearchRun> {
  return post<ResearchRun>(`/research/workspaces/${wsId}/runs`, body)
}

export async function getRun(runId: number): Promise<ResearchRun> {
  return get<ResearchRun>(`/research/runs/${runId}`)
}

function isNotFound(e: unknown): boolean {
  return typeof e === 'object' && e !== null && 'code' in e
    && ((e as { code: string }).code === 'NOT_FOUND' || (e as { code: string }).code === '404')
}

export async function getLatestRun(wsId: number): Promise<ResearchRun | null> {
  try { return await get<ResearchRun>(`/research/workspaces/${wsId}/latest-run`) }
  catch (e: unknown) { if (isNotFound(e)) return null; throw e }
}

export async function getGraph(wsId: number): Promise<ResearchGraph | null> {
  try { return await get<ResearchGraph>(`/research/workspaces/${wsId}/graph`) }
  catch (e: unknown) { if (isNotFound(e)) return null; throw e }
}

export async function queryRag(wsId: number, question: string, top_k = 5, paper_ids?: number[]): Promise<RagQueryResponse> {
  return post<RagQueryResponse>(`/research/workspaces/${wsId}/query`, { question, top_k, paper_ids })
}

export async function clearResearch(wsId: number): Promise<void> {
  await del(`/research/workspaces/${wsId}`)
}
