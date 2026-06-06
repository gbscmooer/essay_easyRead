import { get, post, del } from './client'


export interface WorkspaceItem {
  id: number
  name: string
  paper_count: number
  created_at: string
  updated_at: string
}

export interface WorkspacePaper {
  id: number
  filename: string
  title: string | null
  venue: string | null
  year: number | null
  status: string
  code_url: string | null
  code_status: string
  source?: 'uploaded' | 'expanded' | 'promoted'
  level?: number
  parent_paper_id?: number | null
  created_at: string | null
  added_at: string | null
}

export interface WorkspaceDetail {
  id: number
  name: string
  papers: WorkspacePaper[]
  created_at: string
  updated_at: string
}


export async function listWorkspaces(): Promise<WorkspaceItem[]> {
  return get<WorkspaceItem[]>('/workspaces')
}

export async function createWorkspace(name: string): Promise<WorkspaceItem> {
  return post<WorkspaceItem>('/workspaces', { name })
}

export async function getWorkspace(id: number): Promise<WorkspaceDetail> {
  return get<WorkspaceDetail>(`/workspaces/${id}`)
}

export async function updateWorkspace(id: number, name: string): Promise<WorkspaceItem> {
  const res = await fetch(`/api/workspaces/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!res.ok) throw new Error('更新失败')
  return res.json()
}

export async function deleteWorkspace(id: number): Promise<void> {
  await del(`/workspaces/${id}`)
}

export async function addPaperToWorkspace(workspaceId: number, paperId: number): Promise<void> {
  await post(`/workspaces/${workspaceId}/papers`, { paper_id: paperId })
}

export async function removePaperFromWorkspace(workspaceId: number, paperId: number): Promise<void> {
  await del(`/workspaces/${workspaceId}/papers/${paperId}`)
}

export async function promotePaper(workspaceId: number, paperId: number): Promise<void> {
  await post(`/workspaces/${workspaceId}/papers/${paperId}/promote`, {})
}
