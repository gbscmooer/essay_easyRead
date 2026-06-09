import type { Note } from '@/types'
import { get, post, put, del } from './client'

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
  return put<Note>(`/notes/${noteId}`, { content })
}

export async function deleteNote(noteId: number): Promise<void> {
  await del(`/notes/${noteId}`)
}
