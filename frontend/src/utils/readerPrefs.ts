/**
 * 阅读状态记忆 —— 面板布局、PDF 缩放、每篇论文读到哪一页。
 *
 * 精读工具的连续性靠的是「回到上次的样子」：把面板拖到 45%、把页面缩到 130%、
 * 读到第 12 页，这三件事都属于用户对这篇论文的**工作状态**，不该在切论文或刷新
 * 时被清零。这里只做一件事：把这些状态放进 localStorage，并在读回来时校验。
 *
 * 存储是尽力而为的 —— 隐私模式或禁用 storage 时全部静默回落到默认值，
 * 调用方不需要处理异常。
 */

import { clamp, isRecord, readJSON, writeJSON } from './storage'

const LAYOUT_KEY = 'essay_reader_layout'
const ZOOM_KEY = 'essay_reader_zoom'
const POSITION_KEY = 'essay_reader_positions'

/** 阅读位置最多记住多少篇论文（超出按最近使用淘汰）。 */
export const POSITION_LIMIT = 60

export type FitMode = 'none' | 'width' | 'page'

export interface ReaderLayout {
  /** 右侧分析面板宽度占比（%） */
  panelPct: number
  /** 分析面板是否展开 */
  panelOpen: boolean
}

export interface ZoomPref {
  scale: number
  fitMode: FitMode
}

interface PositionEntry {
  page: number
  at: number
}

type PositionMap = Record<string, PositionEntry>

/**
 * 读取面板布局。越界的宽度会被夹回 [min, max] —— 上一版的取值范围可能和
 * 当前版本不同，读回来的数字不能直接信。
 */
export function loadLayout(fallback: ReaderLayout, min: number, max: number): ReaderLayout {
  const stored = readJSON(LAYOUT_KEY)
  if (!isRecord(stored)) return fallback
  const pct = typeof stored.panelPct === 'number' && Number.isFinite(stored.panelPct)
    ? clamp(stored.panelPct, min, max)
    : fallback.panelPct
  return {
    panelPct: pct,
    panelOpen: typeof stored.panelOpen === 'boolean' ? stored.panelOpen : fallback.panelOpen,
  }
}

export function saveLayout(layout: ReaderLayout): void {
  writeJSON(LAYOUT_KEY, layout)
}

/** 读取缩放偏好。缩放是跨论文的习惯（视力、屏幕），因此不按论文分开存。 */
export function loadZoom(fallback: ZoomPref, min: number, max: number): ZoomPref {
  const stored = readJSON(ZOOM_KEY)
  if (!isRecord(stored)) return fallback
  const scale = typeof stored.scale === 'number' && Number.isFinite(stored.scale)
    ? clamp(stored.scale, min, max)
    : fallback.scale
  const fitMode = stored.fitMode === 'width' || stored.fitMode === 'page' || stored.fitMode === 'none'
    ? stored.fitMode
    : fallback.fitMode
  return { scale, fitMode }
}

export function saveZoom(zoom: ZoomPref): void {
  writeJSON(ZOOM_KEY, zoom)
}

function loadPositions(): PositionMap {
  const stored = readJSON(POSITION_KEY)
  if (!isRecord(stored)) return {}
  const out: PositionMap = {}
  for (const [key, value] of Object.entries(stored)) {
    if (!isRecord(value)) continue
    const page = value.page
    const at = value.at
    if (typeof page !== 'number' || !Number.isFinite(page) || page < 1) continue
    out[key] = { page: Math.floor(page), at: typeof at === 'number' ? at : 0 }
  }
  return out
}

/** 这篇论文上次读到第几页；没有记录返回 null。 */
export function loadReadingPage(paperId: number): number | null {
  if (!Number.isFinite(paperId)) return null
  return loadPositions()[String(paperId)]?.page ?? null
}

/**
 * 记住阅读位置。超过 POSITION_LIMIT 篇时按最后访问时间淘汰最旧的，
 * 避免这份记录在长期使用后无声地涨成几百条。
 */
export function saveReadingPage(paperId: number, page: number): void {
  if (!Number.isFinite(paperId) || !Number.isFinite(page) || page < 1) return
  const positions = loadPositions()
  positions[String(paperId)] = { page: Math.floor(page), at: Date.now() }

  const keys = Object.keys(positions)
  if (keys.length > POSITION_LIMIT) {
    keys
      .sort((a, b) => positions[b].at - positions[a].at)
      .slice(POSITION_LIMIT)
      .forEach(key => { delete positions[key] })
  }
  writeJSON(POSITION_KEY, positions)
}

/** 删除论文后清掉它的阅读位置，避免 ID 复用时读到别人的页码。 */
export function forgetReadingPage(paperId: number): void {
  const positions = loadPositions()
  if (!(String(paperId) in positions)) return
  delete positions[String(paperId)]
  writeJSON(POSITION_KEY, positions)
}
