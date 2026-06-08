import { useState, useEffect, useCallback } from 'react'
import type { Note } from '@/types'
import { listNotes, createNote, updateNote, deleteNote } from '@/api/notes'

interface NotesPanelProps {
  paperId: number
}

export function NotesPanel({ paperId }: NotesPanelProps) {
  const [notes, setNotes] = useState<Note[]>([])
  const [newContent, setNewContent] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editContent, setEditContent] = useState('')

  const fetchNotes = useCallback(async () => {
    try {
      const result = await listNotes(paperId)
      setNotes(result)
    } catch {
      // ignore
    }
  }, [paperId])

  useEffect(() => { fetchNotes() }, [fetchNotes])

  const handleCreate = async () => {
    if (!newContent.trim()) return
    try {
      await createNote(paperId, newContent.trim())
      setNewContent('')
      fetchNotes()
    } catch {
      // ignore
    }
  }

  const handleUpdate = async (noteId: number) => {
    if (!editContent.trim()) return
    try {
      await updateNote(noteId, editContent.trim())
      setEditingId(null)
      fetchNotes()
    } catch {
      // ignore
    }
  }

  const handleDelete = async (noteId: number) => {
    try {
      await deleteNote(noteId)
      fetchNotes()
    } catch {
      // ignore
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <textarea
          value={newContent}
          onChange={e => setNewContent(e.target.value)}
          placeholder="添加笔记..."
          rows={2}
          className="flex-1 text-sm px-3 py-2 rounded-lg border resize-none focus:outline-none"
          style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-base)', color: 'var(--text-primary)' }}
        />
        <button
          onClick={handleCreate}
          disabled={!newContent.trim()}
          className="self-end px-3 py-2 text-sm rounded-lg transition-colors disabled:opacity-40"
          style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
        >
          添加
        </button>
      </div>

      {notes.length === 0 && (
        <p className="text-xs text-center py-4" style={{ color: 'var(--text-muted)' }}>暂无笔记</p>
      )}

      {notes.map(note => (
        <div
          key={note.id}
          className="rounded-lg border p-3"
          style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-base)' }}
        >
          {editingId === note.id ? (
            <div className="space-y-2">
              <textarea
                value={editContent}
                onChange={e => setEditContent(e.target.value)}
                rows={3}
                className="w-full text-sm px-2 py-1 rounded border resize-none focus:outline-none"
                style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)', color: 'var(--text-primary)' }}
              />
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => setEditingId(null)}
                  className="text-xs px-2 py-1 rounded border transition-colors"
                  style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
                >
                  取消
                </button>
                <button
                  onClick={() => handleUpdate(note.id)}
                  className="text-xs px-2 py-1 rounded transition-colors"
                  style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
                >
                  保存
                </button>
              </div>
            </div>
          ) : (
            <>
              <p className="text-sm whitespace-pre-wrap" style={{ color: 'var(--text-primary)' }}>{note.content}</p>
              <div className="flex items-center justify-between mt-2">
                <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  {new Date(note.created_at).toLocaleString('zh-CN')}
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => { setEditingId(note.id); setEditContent(note.content) }}
                    className="text-[10px] transition-colors"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    编辑
                  </button>
                  <button
                    onClick={() => handleDelete(note.id)}
                    className="text-[10px] transition-colors"
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
