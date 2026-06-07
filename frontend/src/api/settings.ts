/**
 * 设置页专用 API：内置提示词、GROBID 服务状态/下载
 */

import { get, post } from './client'

export interface GrobidStatus {
  url: string
  alive: boolean
  image: string
  image_present: boolean
  pulling: boolean
  pull_ok: boolean | null
  pull_log: string
}

/** 内置（只读）解读系统提示词 */
export async function getBuiltinAnalysisPrompt(): Promise<string> {
  const r = await get<{ content: string }>('/analysis/builtin-analysis-prompt')
  return r.content
}

/** 查询 GROBID 服务情况（服务可达 + 镜像是否已下载 + 是否正在拉取） */
export async function getGrobidStatus(): Promise<GrobidStatus> {
  return get<GrobidStatus>('/analysis/grobid/status')
}

/** 触发后台下载 GROBID 镜像 */
export async function pullGrobidImage(): Promise<{ running: boolean; image: string }> {
  return post<{ running: boolean; image: string }>('/analysis/grobid/pull')
}
