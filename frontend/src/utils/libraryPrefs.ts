/**
 * 论文库的视图偏好。
 *
 * 排序是「我习惯怎么看这批论文」，不是一次性的操作。每次进论文库都被打回
 * 「最近添加」，等于每次都要重新点一遍。
 */

import { isRecord, readJSON, writeJSON } from './storage'

const VIEW_KEY = 'essay_library_view'

/** 读取排序偏好；存的值不在当前支持的排序里就回落到默认。 */
export function loadLibrarySort(fallback: string, allowed: readonly string[]): string {
  const stored = readJSON(VIEW_KEY)
  if (!isRecord(stored)) return fallback
  return typeof stored.sort === 'string' && allowed.includes(stored.sort) ? stored.sort : fallback
}

export function saveLibrarySort(sort: string): void {
  writeJSON(VIEW_KEY, { sort })
}
