/**
 * localStorage 的最小封装。
 *
 * 界面偏好（阅读位置、面板宽度、论文库排序）都存在这里。存储是尽力而为的：
 * 隐私模式、配额满、storage 被禁用时读写一律静默失败，调用方永远拿到回落值，
 * 不需要写 try/catch。
 */

export function readJSON(key: string): unknown {
  try {
    const raw = window.localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function writeJSON(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage 不可用时忽略 */
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}
