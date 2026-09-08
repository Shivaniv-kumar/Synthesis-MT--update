import { api } from '@/lib/api'
import type { RetentionConfig, RetentionConfigPatch, WorkspaceMember } from '@/types'

export interface CreateUserPayload {
  display_name: string
  email: string
  role: WorkspaceMember['role']
  password: string
  project_ids?: string[]
}

// ── Members ───────────────────────────────────────────────────────────────────

export async function getMembers(workspaceId: string): Promise<WorkspaceMember[]> {
  return api.get<WorkspaceMember[]>(`/workspaces/${workspaceId}/members`)
}

export async function inviteMember(
  workspaceId: string,
  email: string,
  role: WorkspaceMember['role'],
  projectIds: string[] = [],
): Promise<WorkspaceMember> {
  return api.post<WorkspaceMember>(`/workspaces/${workspaceId}/members`, {
    email,
    role,
    project_ids: projectIds,
  })
}

export async function updateMemberRole(
  workspaceId: string,
  userId: string,
  role: WorkspaceMember['role'],
): Promise<WorkspaceMember> {
  return api.patch<WorkspaceMember>(`/workspaces/${workspaceId}/members/${userId}`, { role })
}

export async function removeMember(workspaceId: string, userId: string): Promise<void> {
  return api.del<void>(`/workspaces/${workspaceId}/members/${userId}`)
}

export async function createUser(
  workspaceId: string,
  payload: CreateUserPayload,
): Promise<WorkspaceMember> {
  return api.post<WorkspaceMember>(`/workspaces/${workspaceId}/members/create`, payload)
}

// ── Retention config ──────────────────────────────────────────────────────────

export async function getRetentionConfig(): Promise<RetentionConfig> {
  return api.get<RetentionConfig>('/compliance/retention-config')
}

export async function updateRetentionConfig(patch: RetentionConfigPatch): Promise<RetentionConfig> {
  return api.patch<RetentionConfig>('/compliance/retention-config', patch)
}
