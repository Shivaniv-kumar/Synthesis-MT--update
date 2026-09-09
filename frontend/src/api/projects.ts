import { api } from '@/lib/api'
import type { Project } from '@/types'

export interface CreateProjectRequest {
  name: string
  description?: string | null
  color?: string | null
}

export interface UpdateProjectRequest {
  name?: string
  description?: string | null
  color?: string | null
  is_active?: boolean
}

export async function getProjects(includeArchived = false): Promise<Project[]> {
  return api.get<Project[]>('/projects/', { params: includeArchived ? { include_archived: true } : {} })
}

export async function createProject(data: CreateProjectRequest): Promise<Project> {
  return api.post<Project>('/projects/', data)
}

export async function updateProject(id: string, data: UpdateProjectRequest): Promise<Project> {
  return api.patch<Project>(`/projects/${id}`, data)
}

export async function archiveProject(id: string): Promise<void> {
  return api.del<void>(`/projects/${id}`)
}
