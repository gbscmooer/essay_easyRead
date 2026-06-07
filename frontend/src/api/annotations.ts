import type { Annotation, AnnotationCreate } from '@/types'
import { get, post, del } from './client'

export async function listAnnotations(paperId: number): Promise<Annotation[]> {
  return get<Annotation[]>(`/annotations/paper/${paperId}`)
}

export async function createAnnotation(paperId: number, data: AnnotationCreate): Promise<Annotation> {
  return post<Annotation>(`/annotations/paper/${paperId}`, data)
}

export async function deleteAnnotation(annotationId: number): Promise<void> {
  await del(`/annotations/${annotationId}`)
}

export async function deleteAnnotationGroup(groupId: string): Promise<void> {
  await del(`/annotations/group/${groupId}`)
}
