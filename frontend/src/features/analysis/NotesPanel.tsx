import { useState, useEffect, useCallback, useRef } from 'react'
import type { Note } from '@/types'
import { listNotes, createNote, updateNote, deleteNote } from '@/api/notes'
import { toast } from '@/hooks/useToast'
import { ConfirmDialog } from '@/ds-entry'
import { clearNoteDraft, loadNoteDraft, saveNoteDraft } from '@/utils/noteDrafts'

/** 是否是「提交」组合键：⌘/Ctrl + Enter。 */
function isSubmitChord(e: React.KeyboardEvent): boolean {
  return e.key === 'Enter' && (e.metaKey || e.ctrlKey)
}

interface NotesPanelProps {
  paperId: number
}

export function NotesPanel({ paperId }: NotesPanelProps) {
  const [notes, setNotes] = useState<Note[]>([])
  // 草稿从存档里起手：Tab 切走会卸载这个组件，写了一半的笔记必须活过卸载
  const [newContent, setNewContent] = useState(() => loadNoteDraft(paperId))
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editContent, setEditContent] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false } }, [])

  // 换论文时载入那篇论文自己的草稿
  useEffect(() => { setNewContent(loadNoteDraft(paperId)) }, [paperId])

  // 防抖写入，避免每敲一个字都碰一次 storage
  useEffect(() => {
    const timer = setTimeout(() => saveNoteDraft(paperId, newContent), 400)
    return () => clearTimeout(timer)
  }, [paperId, newContent])

  const fetchNotes = useCallback(async () => {
    try {
      const result = await listNotes(paperId)
      if (mountedRef.current) setNotes(result)
    } catch (e: unknown) {
      if (!mountedRef.current) return
      const message = e instanceof Error ? e.message : '加载笔记失败'
      toast({ type: 'error', message })
    }
  }, [paperId])

  useEffect(() => { fetchNotes() }, [fetchNotes])

  const handleCreate = async () => {
    if (!newContent.trim()) return
    try {
      await createNote(paperId, newContent.trim())
      setNewContent('')
      clearNoteDraft(paperId)
      fetchNotes()
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '创建笔记失败'
      toast({ type: 'error', message })
    }
  }

  const handleUpdate = async (noteId: number) => {
    if (!editContent.trim()) return
    try {
      await updateNote(noteId, editContent.trim())
      setEditingId(null)
      fetchNotes()
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '更新笔记失败'
      toast({ type: 'error', message })
    }
  }

  const handleDelete = async (noteId: number) => {
    setDeleteTarget(null)
    try {
      await deleteNote(noteId)
      fetchNotes()
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '删除笔记失败'
      toast({ type: 'error', message })
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <textarea
          value={newContent}
          onChange={e => setNewContent(e.target.value)}
          onKeyDown={e => { if (isSubmitChord(e)) { e.preventDefault(); handleCreate() } }}
          placeholder="添加笔记…（⌘/Ctrl + Enter 提交）"
          aria-label="新建笔记"
          rows={2}
          className="flex-1 text-[13px] px-3 py-2.5 resize-none focus:outline-none focus-glow leading-relaxed"
          style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)', color: 'var(--text-primary)' }}
        />
        <button
          onClick={handleCreate}
          disabled={!newContent.trim()}
          className="self-end px-4 py-2 text-[13px] font-medium transition-all duration-200 disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 focus-glow press"
          style={{ backgroundColor: 'var(--accent)', color: '#ffffff' }}
        >
          添加
        </button>
      </div>

      {notes.length === 0 && (
        <div className="flex flex-col items-center gap-2.5 py-8 animate-reveal-fade">
          <div
            className="w-10 h-10 flex items-center justify-center"
            style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--text-muted)' }}>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
          </div>
          <p className="text-[12px]" style={{ color: 'var(--text-muted)' }}>暂无笔记</p>
        </div>
      )}

      {notes.map(note => (
        <div
          key={note.id}
          className="list-row"
        >
          {editingId === note.id ? (
            <div className="space-y-2.5 animate-reveal-fade">
              <textarea
                value={editContent}
                onChange={e => setEditContent(e.target.value)}
                onKeyDown={e => {
                  if (isSubmitChord(e)) { e.preventDefault(); handleUpdate(note.id) }
                  if (e.key === 'Escape') { e.preventDefault(); setEditingId(null) }
                }}
                aria-label="编辑笔记"
                rows={3}
                className="w-full text-[13px] px-3 py-2 resize-none focus:outline-none focus-glow leading-relaxed"
                style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-elevated)', color: 'var(--text-primary)' }}
              />
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => setEditingId(null)}
                  className="text-[12px] px-3 py-1.5 transition-all duration-200 hover:opacity-80 focus-glow press"
                  style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                >
                  取消
                </button>
                <button
                  onClick={() => handleUpdate(note.id)}
                  disabled={!editContent.trim()}
                  className="text-[12px] px-3 py-1.5 font-medium transition-all duration-200 hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed focus-glow press"
                  style={{ backgroundColor: 'var(--accent)', color: '#ffffff' }}
                >
                  保存
                </button>
              </div>
            </div>
          ) : (
            <>
              <p className="text-[13px] whitespace-pre-wrap leading-relaxed" style={{ color: 'var(--text-primary)' }}>{note.content}</p>
              <div className="flex items-center justify-between mt-2.5">
                <span className="text-[11px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
                  {new Date(note.created_at).toLocaleString('zh-CN')}
                </span>
                <div className="flex gap-3">
                  <button
                    onClick={() => { setEditingId(note.id); setEditContent(note.content) }}
                    className="text-[11px] transition-colors hover:opacity-80 focus-glow px-1 -mx-1"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    编辑
                  </button>
                  <button
                    onClick={() => setDeleteTarget(note.id)}
                    className="text-[11px] transition-colors hover:opacity-80 focus-glow px-1 -mx-1"
                    style={{ color: 'var(--error)' }}
                  >
                    删除
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      ))}

      {/* 笔记是手写内容，删掉不可恢复 —— 和论文、标注一样走确认 */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除笔记"
        message="确定删除这条笔记？删除后不可恢复。"
        variant="danger"
        confirmLabel="删除"
        cancelLabel="取消"
        onConfirm={() => { if (deleteTarget !== null) handleDelete(deleteTarget) }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
