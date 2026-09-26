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
function normalizeErrorBody(body: unknown, fallbackDetail: string, fallbackCode: string) {
  if (!body || typeof body !== 'object') {
    return { detail: fallbackDetail, code: fallbackCode }
  }

  const payload = body as Record<string, unknown>
  const rawDetail = payload.detail

  if (typeof rawDetail === 'string') {
    return {
      detail: rawDetail || fallbackDetail,
      code: typeof payload.code === 'string' ? payload.code : fallbackCode,
    }
  }

  if (rawDetail && typeof rawDetail === 'object' && !Array.isArray(rawDetail)) {
    const nested = rawDetail as Record<string, unknown>
    return {
      detail: typeof nested.detail === 'string' ? nested.detail : fallbackDetail,
      code: typeof nested.code === 'string' ? nested.code : fallbackCode,
    }
  }

  if (Array.isArray(rawDetail) && rawDetail.length > 0) {
    const first = rawDetail[0] as Record<string, unknown>
    const msg = typeof first?.msg === 'string' ? first.msg : fallbackDetail
    const loc = Array.isArray(first?.loc) ? first.loc.join('.') : ''
    return {
      detail: loc ? `${loc}: ${msg}` : msg,
      code: typeof payload.code === 'string' ? payload.code : fallbackCode,
    }
  }

  return {
    detail: fallbackDetail,
    code: typeof payload.code === 'string' ? payload.code : fallbackCode,
  }
}

async function parseError(res: Response): Promise<never> {
  let detail = res.statusText || 'Unknown Error'
  let code = String(res.status)
  try {
    const text = await res.text()
    if (text) {
      const body = JSON.parse(text) as ApiError | Record<string, unknown>
      const parsed = normalizeErrorBody(body, detail, code)
      detail = parsed.detail
      code = parsed.code
    }
  } catch {
    // 响应体不是 JSON，使用默认错误信息
  }
  const err = new Error(detail) as Error & { code: string }
  err.code = code
  throw err
}

/**
 * 安全解析响应体，处理 204 No Content 或空响应体情况
 */
async function parseResponse<T>(res: Response): Promise<T> {
  if (res.status === 204) {
    return {} as T
  }
  const text = await res.text()
  if (!text) {
    return {} as T
  }
  return JSON.parse(text) as T
}

/**
 * 包装 fetch 请求，捕获并格式化网络错误
 */
async function safeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init)
  } catch (e) {
    const message = e instanceof Error ? e.message : '网络请求失败'
    const err = new Error(message === 'Failed to fetch' ? '网络错误，请检查连接' : message) as Error & { code: string }
    err.code = 'NETWORK_ERROR'
    throw err
  }
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
  const res = await safeFetch(`${API_BASE}${path}`, { signal })
  if (!res.ok) await parseError(res)
  return parseResponse<T>(res)
}

/** 下载二进制响应，沿用 JSON API 的网络和状态码错误处理。 */
async function getBinary(path: string): Promise<Response> {
  const res = await safeFetch(`${API_BASE}${path}`)
  if (!res.ok) await parseError(res)
  return res
}


/**
 * 发送 POST 请求（JSON body）
 *
 * @param path API 路径
 * @param body 请求体（会被 JSON 序列化）
 * @returns 解析后的 JSON 响应
 */
async function post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await safeFetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    ...(signal ? { signal } : {}),
  })
  if (!res.ok) await parseError(res)
  return parseResponse<T>(res)
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
  const res = await safeFetch(`${API_BASE}${path}`, {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) await parseError(res)
  return parseResponse<T>(res)
}


/**
 * 发送 PUT 请求（JSON body）
 *
 * @param path API 路径
 * @param body 请求体（会被 JSON 序列化）
 * @returns 解析后的 JSON 响应
 */
async function put<T>(path: string, body?: unknown): Promise<T> {
  const res = await safeFetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) await parseError(res)
  return parseResponse<T>(res)
}


/**
 * 发送 DELETE 请求
 *
 * @param path API 路径
 * @returns 解析后的 JSON 响应
 */
async function del<T>(path: string): Promise<T> {
  const res = await safeFetch(`${API_BASE}${path}`, { method: 'DELETE' })
  if (!res.ok) await parseError(res)
  return parseResponse<T>(res)
}


/**
 * 上传文件（带进度回调）
 *
 * @param path API 路径
 * @param file File 对象
 * @param onProgress 上传进度回调 (0-100)
 * @returns 解析后的 JSON 响应
 */
function uploadWithProgress<T>(path: string, file: File, onProgress?: (pct: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}${path}`)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        if (xhr.status === 204 || !xhr.responseText) {
          resolve({} as T)
        } else {
          try { resolve(JSON.parse(xhr.responseText)) } catch { resolve({} as T) }
        }
      } else {
        let detail = xhr.statusText || 'Unknown Error'
        let code = String(xhr.status)
        try {
          const body = JSON.parse(xhr.responseText)
          detail = body.detail || detail
          code = body.code || code
        } catch {}
        const err = new Error(detail) as Error & { code: string }
        err.code = code
        reject(err)
      }
    }
    xhr.onerror = () => reject(new Error('网络错误'))
    const formData = new FormData()
    formData.append('file', file)
    xhr.send(formData)
  })
}


export { get, getBinary, post, put, upload, del, uploadWithProgress }
