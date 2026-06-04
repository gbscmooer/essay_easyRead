/**
 * Papers API 调用
 *
 * 封装所有论文相关的 API 请求。
 */

import type { Paper, PaperListResponse, AnalysisResult } from '@/types'


/**
 * 上传 PDF 文件
 *
 * @param file File 对象
 * @returns 创建的论文记录
 *
 * POST /api/papers/upload (multipart/form-data)
 */
// export async function uploadPaper(file: File): Promise<Paper>


/**
 * 获取论文列表
 *
 * @param params 筛选参数
 * @returns 论文列表和总数
 *
 * GET /api/papers?search=...&venue=...&year=...&page=...&per_page=...
 */
// export async function listPapers(params?: {
//   search?: string
//   venue?: string
//   year?: number
//   page?: number
//   per_page?: number
// }): Promise<PaperListResponse>


/**
 * 获取单篇论文信息
 *
 * @param paperId 论文 ID
 * @returns 论文记录
 *
 * GET /api/papers/{paperId}
 */
// export async function getPaper(paperId: number): Promise<Paper>


/**
 * 删除论文
 *
 * @param paperId 论文 ID
 *
 * DELETE /api/papers/{paperId}
 */
// export async function deletePaper(paperId: number): Promise<void>


/**
 * 获取完整解析结果
 *
 * @param paperId 论文 ID
 * @returns 解析结果
 *
 * GET /api/analysis/{paperId}
 */
// export async function getAnalysis(paperId: number): Promise<AnalysisResult>


/**
 * 导出论文解析结果为 Markdown
 *
 * @param paperId 论文 ID
 * @returns Markdown 文本
 *
 * GET /api/papers/{paperId}/export
 */
// export async function exportPaper(paperId: number): Promise<string>


/**
 * 重新解析论文
 *
 * @param paperId 论文 ID
 * @returns 更新后的论文记录
 *
 * POST /api/analysis/{paperId}/reparse
 */
// export async function reparsePaper(paperId: number): Promise<Paper>
