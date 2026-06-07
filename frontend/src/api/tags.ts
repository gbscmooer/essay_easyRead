import type { Tag } from '@/types'
import { get, post, del } from './client'

export async function listTags(): Promise<Tag[]> {
  return get<Tag[]>('/tags')
}

export async function createTag(name: string, color?: string): Promise<Tag> {
  return post<Tag>('/tags', { name, color: color || '#3b82f6' })
}

export async function deleteTag(tagId: number): Promise<void> {
  await del(`/tags/${tagId}`)
}

export async function updateTag(tagId: number, name: string, color: string): Promise<Tag> {
  const res = await fetch(`/api/tags/${tagId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, color }),
  })
  if (!res.ok) throw new Error('Failed to update tag')
  return res.json()
}

export async function getPaperTags(paperId: number): Promise<Tag[]> {
  return get<Tag[]>(`/tags/paper/${paperId}`)
}

export async function addPaperTag(paperId: number, tagId: number): Promise<void> {
  await post(`/tags/paper/${paperId}`, { tag_id: tagId })
}

export async function removePaperTag(paperId: number, tagId: number): Promise<void> {
  await del(`/tags/paper/${paperId}/${tagId}`)
}
