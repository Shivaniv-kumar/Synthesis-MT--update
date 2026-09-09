import { api } from '@/lib/api'
import type { Workspace } from '@/types'
import type { LoginResponse } from './auth'

export async function getMyWorkspaces(): Promise<Workspace[]> {
  return api.get<Workspace[]>('/auth/my-workspaces')
}

export async function switchWorkspace(workspaceId: string): Promise<LoginResponse> {
  return api.post<LoginResponse>('/auth/switch-workspace', { workspace_id: workspaceId })
}

export async function createWorkspace(name: string): Promise<Workspace> {
  return api.post<Workspace>('/workspaces/', { name })
}

export async function listWorkspaces(): Promise<Workspace[]> {
  return api.get<Workspace[]>('/workspaces/')
}
