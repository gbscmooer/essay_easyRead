import type { Note } from '@/types'
import { get, post, del } from './client'

export async function listNotes(paperId: number, noteType?: string): Promise<Note[]> {
  const qs = noteType ? `?note_type=${noteType}` : ''
  return get<Note[]>(`/notes/paper/${paperId}${qs}`)
}

export async function createNote(
  paperId: number,
  content: string,
  noteType: string = 'general',
  sectionName?: string,
  pageNumber?: number,
): Promise<Note> {
  return post<Note>(`/notes/paper/${paperId}`, {
    content,
    note_type: noteType,
    section_name: sectionName || null,
    page_number: pageNumber ?? null,
  })
}

export async function updateNote(noteId: number, content: string): Promise<Note> {
  const res = await fetch(`/api/notes/${noteId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!res.ok) throw new Error('Failed to update note')
  return res.json()
}

export async function deleteNote(noteId: number): Promise<void> {
  await del(`/notes/${noteId}`)
}
