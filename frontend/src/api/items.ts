import { api } from '@/lib/api'
import type { ActionItem, UpdateItemRequest, ItemFilters, PaginatedResponse } from '@/types'

// ── API functions ─────────────────────────────────────────────────────────────

/**
 * Fetch a paginated, filtered list of action items.
 */
export async function getItems(
  filters: ItemFilters = {},
): Promise<PaginatedResponse<ActionItem>> {
  // Strip undefined values so they don't appear as "undefined" in query string
  const params: Record<string, string | number | boolean> = {}
  const entries = Object.entries(filters) as Array<[string, string | number | boolean | undefined]>
  for (const [key, value] of entries) {
    if (value !== undefined) {
      params[key] = value
    }
  }
  return api.get<PaginatedResponse<ActionItem>>('/items/', { params })
}

/**
 * Update a single action item by ID (partial PATCH).
 */
export async function updateItem(
  id: string,
  data: UpdateItemRequest,
): Promise<ActionItem> {
  return api.patch<ActionItem>(`/items/${id}`, data)
}

/**
 * Delete a single action item by ID.
 */
export async function deleteItem(id: string): Promise<void> {
  return api.del<void>(`/items/${id}`)
}

/**
 * Bulk-upsert a set of action items (used after review confirmation).
 * The server will update existing items by ID.
 */
export async function bulkSaveItems(items: ActionItem[]): Promise<ActionItem[]> {
  return api.post<ActionItem[]>('/items/bulk', { items })
}
