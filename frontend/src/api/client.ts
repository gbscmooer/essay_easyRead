/**
 * API 客户端
 *
 * 封装 fetch 请求，统一处理错误和 base URL。
 * 所有 API 调用通过此模块进行。
 */

import type { ApiError } from '@/types'

// ============================================================
// 常量
// ============================================================
const API_BASE = '/api'  // Vite proxy 会转发到后端


// ============================================================
// 错误解析
// ============================================================

/**
 * 解析错误响应体，抛出结构化错误
 */
async function parseError(res: Response): Promise<never> {
  let detail = res.statusText
  let code = String(res.status)
  try {
    const body: ApiError = await res.json()
    detail = body.detail || detail
    code = body.code || code
  } catch {
    // 响应体不是 JSON，使用默认错误信息
  }
  const err = new Error(detail) as Error & { code: string }
  err.code = code
  throw err
}


// ============================================================
// 通用请求函数
// ============================================================

/**
 * 发送 GET 请求
 *
 * @param path API 路径，如 "/papers/1"
 * @returns 解析后的 JSON 响应
 * @throws ApiError 当响应状态码非 2xx
 */
async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { signal })
  if (!res.ok) await parseError(res)
  return res.json() as Promise<T>
}


/**
 * 发送 POST 请求（JSON body）
 *
 * @param path API 路径
 * @param body 请求体（会被 JSON 序列化）
 * @returns 解析后的 JSON 响应
 */
async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) await parseError(res)
  return res.json() as Promise<T>
}


/**
 * 上传文件（multipart/form-data）
 *
 * @param path API 路径
 * @param file File 对象
 * @returns 解析后的 JSON 响应
 */
async function upload<T>(path: string, file: File): Promise<T> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) await parseError(res)
  return res.json() as Promise<T>
}


/**
 * 发送 DELETE 请求
 *
 * @param path API 路径
 * @returns 解析后的 JSON 响应
 */
async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: 'DELETE' })
  if (!res.ok) await parseError(res)
  return res.json() as Promise<T>
}


export { get, post, upload, del }
