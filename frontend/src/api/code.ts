/**
 * Code API 调用
 *
 * 封装代码仓库相关的 API 请求。
 */

import type { FileNode, CodeAnalysisResult } from '@/types'
import { get, post } from './client'


/** 重新提取代码仓库 URL */
export async function extractCodeUrl(paperId: number): Promise<{ code_url: string | null }> {
  return post(`/code/${paperId}/extract-url`)
}


/** Clone 代码仓库 */
export async function cloneRepository(paperId: number): Promise<{ message: string; path: string }> {
  return post(`/code/${paperId}/clone`)
}


/** 获取文件树 */
export async function getCodeTree(paperId: number): Promise<FileNode> {
  return get<FileNode>(`/code/${paperId}/tree`)
}


/** 获取文件内容 */
export async function getCodeFile(paperId: number, path: string, signal?: AbortSignal): Promise<{ path: string; content: string }> {
  return get(`/code/${paperId}/file?path=${encodeURIComponent(path)}`, signal)
}


/** 获取代码分析结果 */
export async function getCodeAnalysis(paperId: number): Promise<CodeAnalysisResult> {
  return get<CodeAnalysisResult>(`/code/${paperId}/analysis`)
}


/** 触发代码分析（返回 EventSource URL） */
export function getCodeAnalysisSSEUrl(paperId: number): string {
  return `/api/code/${paperId}/analyze`
}


/** 获取代码摘要 */
export async function getCodeSummary(paperId: number): Promise<{
  code_url: string | null
  code_status: string
  code_summary: string | null
}> {
  return get(`/code/${paperId}/summary`)
}


/** 在 VSCode 中打开代码仓库（使用 vscode:// 协议） */
export function openInVSCode(path: string): void {
  const url = `vscode://file${path.startsWith('/') ? '' : '/'}${path}`
  window.open(url, '_blank')
}
