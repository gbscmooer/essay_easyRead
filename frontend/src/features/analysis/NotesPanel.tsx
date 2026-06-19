import { useState, useEffect, useCallback, useRef } from 'react'
import type { Note } from '@/types'
import { listNotes, createNote, updateNote, deleteNote } from '@/api/notes'
import { toast } from '@/hooks/useToast'

interface NotesPanelProps {
  paperId: number
}

export function NotesPanel({ paperId }: NotesPanelProps) {
  const [notes, setNotes] = useState<Note[]>([])
  const [newContent, setNewContent] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editContent, setEditContent] = useState('')
  const mountedRef = useRef(true)

  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false } }, [])

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
          placeholder="添加笔记…"
          rows={2}
          className="flex-1 text-[13px] px-3 py-2.5 rounded-xl resize-none focus:outline-none leading-relaxed"
          style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)', color: 'var(--text-primary)' }}
        />
        <button
          onClick={handleCreate}
          disabled={!newContent.trim()}
          className="self-end px-4 py-2 text-[13px] font-medium rounded-full transition-opacity disabled:opacity-40 hover:opacity-90"
          style={{ backgroundColor: 'var(--accent)', color: '#ffffff' }}
        >
          添加
        </button>
      </div>

      {notes.length === 0 && (
        <p className="text-[12px] text-center py-6" style={{ color: 'var(--text-muted)' }}>暂无笔记</p>
      )}

      {notes.map(note => (
        <div
          key={note.id}
          className="rounded-xl p-3.5"
          style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
        >
          {editingId === note.id ? (
            <div className="space-y-2.5">
              <textarea
                value={editContent}
                onChange={e => setEditContent(e.target.value)}
                rows={3}
                className="w-full text-[13px] px-3 py-2 rounded-lg resize-none focus:outline-none leading-relaxed"
                style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-elevated)', color: 'var(--text-primary)' }}
              />
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => setEditingId(null)}
                  className="text-[12px] px-3 py-1.5 rounded-full transition-colors hover:opacity-80"
                  style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                >
                  取消
                </button>
                <button
                  onClick={() => handleUpdate(note.id)}
                  className="text-[12px] px-3 py-1.5 rounded-full font-medium transition-opacity hover:opacity-90"
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
                    className="text-[11px] transition-colors hover:opacity-80"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    编辑
                  </button>
                  <button
                    onClick={() => handleDelete(note.id)}
                    className="text-[11px] transition-colors hover:opacity-80"
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
    </div>
  )
}
