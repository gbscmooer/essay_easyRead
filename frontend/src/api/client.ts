/**
 * API 客户端
 *
 * 封装 fetch 请求，统一处理错误和 base URL。
 * 所有 API 调用通过此模块进行。
 */

// ============================================================
// 常量
// ============================================================
// const API_BASE = '/api'  // Vite proxy 会转发到后端


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
// async function get<T>(path: string): Promise<T>


/**
 * 发送 POST 请求（JSON body）
 *
 * @param path API 路径
 * @param body 请求体（会被 JSON 序列化）
 * @returns 解析后的 JSON 响应
 */
// async function post<T>(path: string, body?: unknown): Promise<T>


/**
 * 上传文件（multipart/form-data）
 *
 * @param path API 路径
 * @param file File 对象
 * @returns 解析后的 JSON 响应
 */
// async function upload<T>(path: string, file: File): Promise<T>


/**
 * 发送 DELETE 请求
 *
 * @param path API 路径
 * @returns 解析后的 JSON 响应
 */
// async function del<T>(path: string): Promise<T>
