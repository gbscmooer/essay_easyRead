/**
 * Papers API 调用
 *
 * 封装所有论文相关的 API 请求。
 */

import type { Paper, PaperListResponse, AnalysisResult, RelatedPaper, SearchResponse } from '@/types'
import { get, post, upload, del, uploadWithProgress } from './client'


/**
 * 上传 PDF 文件
 *
 * @param file File 对象
 * @returns 创建的论文记录
 *
 * POST /api/papers/upload (multipart/form-data)
 */
export async function uploadPaper(file: File): Promise<Paper> {
  return upload<Paper>('/papers/upload', file)
}

export async function uploadPaperWithProgress(file: File, onProgress?: (pct: number) => void): Promise<Paper> {
  return uploadWithProgress<Paper>('/papers/upload', file, onProgress)
}


/**
 * 获取论文列表
 *
 * @param params 筛选参数
 * @returns 论文列表和总数
 *
 * GET /api/papers?search=...&venue=...&year=...&page=...&per_page=...
 */
export async function listPapers(params?: {
  search?: string
  category?: string
  year?: number
  date_added?: string
  page?: number
  per_page?: number
  source?: 'uploaded' | 'expanded' | 'all'
}): Promise<PaperListResponse> {
  const query = new URLSearchParams()
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        query.set(key, String(value))
      }
    })
  }
  const qs = query.toString()
  return get<PaperListResponse>(`/papers${qs ? `?${qs}` : ''}`)
}


/**
 * 获取单篇论文信息
 *
 * @param paperId 论文 ID
 * @returns 论文记录
 *
 * GET /api/papers/{paperId}
 */
export async function getPaper(paperId: number): Promise<Paper> {
  return get<Paper>(`/papers/${paperId}`)
}


/**
 * 删除论文
 *
 * @param paperId 论文 ID
 *
 * DELETE /api/papers/{paperId}
 */
export async function deletePaper(paperId: number): Promise<void> {
  await del<unknown>(`/papers/${paperId}`)
}


export async function promoteExpandedPaper(paperId: number): Promise<void> {
  await post<unknown>(`/papers/${paperId}/promote`)
}


/**
 * 重新索引元数据缺失（年份/标题/分类）或索引失败的论文。
 *
 * includeExpanded=true 时一并重索引联网扩展下载的论文。
 * 后台异步执行，返回本次排队重跑的论文数；前端可轮询列表观察 index_status。
 *
 * POST /api/papers/reindex?include_expanded=...
 */
export async function reindexLibrary(includeExpanded = false): Promise<{ queued: number }> {
  const qs = includeExpanded ? '?include_expanded=true' : ''
  return post<{ queued: number }>(`/papers/reindex${qs}`)
}


/**
 * 获取完整解析结果
 *
 * @param paperId 论文 ID
 * @returns 解析结果
 *
 * GET /api/analysis/{paperId}
 */
export async function getAnalysis(paperId: number): Promise<AnalysisResult> {
  return get<AnalysisResult>(`/analysis/${paperId}`)
}


/**
 * 导出论文解析结果为 Markdown
 *
 * @param paperId 论文 ID
 * @returns Markdown 文本
 *
 * GET /api/papers/{paperId}/export
 */
export async function exportPaper(paperId: number): Promise<string> {
  const res = await fetch(`/api/papers/${paperId}/export`)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || detail
    } catch {
      // 忽略解析错误
    }
    throw new Error(detail)
  }
  return res.text()
}


/**
 * 在后台启动论文分析（不依赖 SSE 连接）。
 *
 * 幂等：任务已运行时返回当前进度。前端先调用本接口，再连接 /stream，
 * 即使中途切换页面分析也会在后台继续。
 *
 * POST /api/analysis/{paperId}/start
 */
export interface AnalysisProgress {
  status: string
  percent: number
  message: string
  running?: boolean
}

export async function startAnalysisJob(paperId: number): Promise<AnalysisProgress> {
  return post<AnalysisProgress>(`/analysis/${paperId}/start`)
}


/**
 * 查询后台分析进度（无需保持 SSE 连接）。
 *
 * GET /api/analysis/{paperId}/progress
 */
export async function getAnalysisProgress(paperId: number): Promise<AnalysisProgress> {
  return get<AnalysisProgress>(`/analysis/${paperId}/progress`)
}


/**
 * 重新解析论文
 *
 * @param paperId 论文 ID
 * @returns 更新后的论文记录
 *
 * POST /api/analysis/{paperId}/reparse
 */
export async function reparsePaper(paperId: number): Promise<Paper> {
  return post<Paper>(`/analysis/${paperId}/reparse`)
}


/**
 * 刷新相关论文（通过 Semantic Scholar 搜索）
 *
 * @param paperId 论文 ID
 * @returns 新的相关论文列表
 *
 * POST /api/analysis/{paperId}/related-papers
 */
export async function refreshRelatedPapers(paperId: number): Promise<RelatedPaper[]> {
  return post<RelatedPaper[]>(`/analysis/${paperId}/related-papers`)
}


/** 导入结果中单篇论文的状态 */
export interface ImportPaperResult {
  title: string
  status: 'local' | 'downloaded' | 'failed'
  paper_id?: number
  message: string
}

/** 批量导入相关论文的响应 */
export interface ImportRelatedPapersResponse {
  results: ImportPaperResult[]
}

/** 导入单篇论文的入参 */
export interface ImportPaperItem {
  title: string
  doi?: string
  url?: string
}


/**
 * 批量导入相关论文到工作区
 *
 * 检查本地论文库 → 已有则直接关联 → 没有则通过 S2 下载 PDF → 创建记录并关联
 *
 * POST /api/analysis/import-related-papers
 */
export async function importRelatedPapers(
  workspaceId: number,
  papers: ImportPaperItem[],
): Promise<ImportRelatedPapersResponse> {
  return post<ImportRelatedPapersResponse>('/analysis/import-related-papers', {
    workspace_id: workspaceId,
    papers,
  })
}


export async function exportBibtex(paperId: number): Promise<string> {
  const res = await fetch(`/api/analysis/${paperId}/bibtex`)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || detail
    } catch {
      // 忽略解析错误
    }
    throw new Error(detail)
  }
  return res.text()
}


/** 论文库统一智能搜索：元数据 + 解读内容 + 可选全局正文全文 */
export async function smartSearch(
  q: string,
  opts: { category?: string; year?: number; dateAdded?: string; global?: boolean; includeExpanded?: boolean; page?: number; perPage?: number } = {},
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q })
  if (opts.category) params.set('category', opts.category)
  if (opts.year) params.set('year', String(opts.year))
  if (opts.dateAdded) params.set('date_added', opts.dateAdded)
  if (opts.global) params.set('global', 'true')
  if (opts.includeExpanded) params.set('include_expanded', 'true')
  if (opts.page) params.set('page', String(opts.page))
  if (opts.perPage) params.set('per_page', String(opts.perPage))
  return get<SearchResponse>(`/papers/smart-search?${params.toString()}`)
}


/** 获取论文 PDF 标题位置（用于大纲精确定位） */
export interface HeadingPosition {
  page: number
  y_percent: number
  text: string
}

export async function getHeadings(paperId: number): Promise<HeadingPosition[]> {
  return get<HeadingPosition[]>(`/papers/${paperId}/headings`)
}
