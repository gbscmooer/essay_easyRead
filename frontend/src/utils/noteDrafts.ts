/**
 * 未提交的笔记草稿。
 *
 * 笔记输入框此前是纯组件内 state，而分析面板的 Tab 是条件渲染 —— 切去看一眼
 * 实验表格再回来，写了一半的笔记就没了。草稿属于用户的输入，不该由渲染时机决定
 * 生死，所以落到 localStorage，按论文分开存。
 */

import { isRecord, readJSON, writeJSON } from './storage'

const DRAFT_KEY = 'essay_note_drafts'

/** 最多保留多少篇论文的草稿（超出按最后编辑时间淘汰）。 */
export const DRAFT_LIMIT = 30

interface DraftEntry {
  text: string
  at: number
}

type DraftMap = Record<string, DraftEntry>

function loadDrafts(): DraftMap {
  const stored = readJSON(DRAFT_KEY)
  if (!isRecord(stored)) return {}
  const out: DraftMap = {}
  for (const [key, value] of Object.entries(stored)) {
    if (!isRecord(value) || typeof value.text !== 'string') continue
    out[key] = { text: value.text, at: typeof value.at === 'number' ? value.at : 0 }
  }
  return out
}

export function loadNoteDraft(paperId: number): string {
  if (!Number.isFinite(paperId)) return ''
  return loadDrafts()[String(paperId)]?.text ?? ''
}

/** 保存草稿；传入空串等同于清除，避免留下一堆空条目。 */
export function saveNoteDraft(paperId: number, text: string): void {
  if (!Number.isFinite(paperId)) return
  const drafts = loadDrafts()
  const key = String(paperId)

  if (!text.trim()) {
    if (!(key in drafts)) return
    delete drafts[key]
    writeJSON(DRAFT_KEY, drafts)
    return
  }

  drafts[key] = { text, at: Date.now() }
  const keys = Object.keys(drafts)
  if (keys.length > DRAFT_LIMIT) {
    keys
      .sort((a, b) => drafts[b].at - drafts[a].at)
      .slice(DRAFT_LIMIT)
      .forEach(k => { delete drafts[k] })
  }
  writeJSON(DRAFT_KEY, drafts)
}

export function clearNoteDraft(paperId: number): void {
  saveNoteDraft(paperId, '')
}
