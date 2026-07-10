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

export async function queryRag(wsId: number, question: string, top_k = 5, paper_ids?: number[], mode: 'vector' | 'hybrid' | 'graph' = 'vector'): Promise<RagQueryResponse> {
  return post<RagQueryResponse>(`/research/workspaces/${wsId}/query`, { question, top_k, paper_ids, mode })
}

export async function clearResearch(wsId: number): Promise<void> {
  await del(`/research/workspaces/${wsId}`)
}

// 图谱速读（轻量解读）——只为知识图谱抽 facet + 简短总结，token 成本远低于全量精读
// 批量速读改为后台异步执行：digestBatch 立即返回，再用 digestStatus 轮询进度
export interface DigestBatchResult {
  workspace_id: number
  started: boolean         // 是否真正启动了后台任务
  already_running?: boolean // 已有任务在跑
  candidates?: number      // 本次待速读的篇数
  total?: number           // 待速读总数（= candidates）
  skipped_no_text?: number // 没全文跳过
  // already_running 时附带当前进度字段
  running?: boolean
  done?: number
  failed?: number
}

export interface DigestStatus {
  workspace_id: number
  running: boolean
  total: number
  done: number
  failed: number
  skipped_no_text: number
  last_error: string | null
}

export async function digestBatch(wsId: number, force = false): Promise<DigestBatchResult> {
  return post<DigestBatchResult>(`/analysis/digest/batch`, { workspace_id: wsId, force })
}

export async function digestStatus(wsId: number): Promise<DigestStatus> {
  return get<DigestStatus>(`/analysis/digest/status/${wsId}`)
}

export async function digestPaper(paperId: number): Promise<{ paper_id: number; digest: Record<string, unknown> }> {
  return post(`/analysis/digest/${paperId}`, {})
}
