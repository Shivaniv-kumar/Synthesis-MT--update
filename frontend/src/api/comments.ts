import { api } from '@/lib/api'
import type { ItemComment } from '@/types'

export async function getComments(itemId: string): Promise<ItemComment[]> {
  return api.get<ItemComment[]>(`/items/${itemId}/comments`)
}

export async function addComment(itemId: string, content: string): Promise<ItemComment> {
  return api.post<ItemComment>(`/items/${itemId}/comments`, { content })
}

export async function deleteComment(itemId: string, commentId: string): Promise<void> {
  return api.del<void>(`/items/${itemId}/comments/${commentId}`)
}
