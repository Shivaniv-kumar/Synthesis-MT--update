import { api } from '@/lib/api'

export interface ChatHistoryMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatSource {
  meeting_id: string
  meeting_title: string
}

export interface ChatResponse {
  reply: string
  sources: ChatSource[]
}

export async function sendChatMessage(
  message: string,
  conversationHistory: ChatHistoryMessage[],
): Promise<ChatResponse> {
  return api.post<ChatResponse>('/chat/message', {
    message,
    conversation_history: conversationHistory,
  })
}

export interface ShareRequest {
  content: string
  channel: 'email' | 'teams' | 'slack'
  target: string
}

export interface ShareResponse {
  success: boolean
  message: string
}

export async function shareResponse(payload: ShareRequest): Promise<ShareResponse> {
  return api.post<ShareResponse>('/chat/share', payload)
}
