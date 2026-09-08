import { api } from '@/lib/api'
import type {
  KnowledgeEntry,
  KnowledgeEntryCreate,
  KnowledgeEntryPatch,
  KnowledgeFilters,
  PaginatedResponse,
} from '@/types'

export async function listKnowledge(
  filters: KnowledgeFilters = {},
): Promise<PaginatedResponse<KnowledgeEntry>> {
  // FastAPI accepts repeated query params for list values: category=decision&category=risk
  const params: Record<string, unknown> = { ...filters }
  return api.get<PaginatedResponse<KnowledgeEntry>>('/knowledge/', { params })
}

export async function createKnowledgeEntry(
  data: KnowledgeEntryCreate,
): Promise<KnowledgeEntry> {
  return api.post<KnowledgeEntry>('/knowledge/', data)
}

export async function getKnowledgeEntry(id: string): Promise<KnowledgeEntry> {
  return api.get<KnowledgeEntry>(`/knowledge/${id}`)
}

export async function updateKnowledgeEntry(
  id: string,
  data: KnowledgeEntryPatch,
): Promise<KnowledgeEntry> {
  return api.patch<KnowledgeEntry>(`/knowledge/${id}`, data)
}

export async function deleteKnowledgeEntry(id: string): Promise<void> {
  return api.del<void>(`/knowledge/${id}`)
}
