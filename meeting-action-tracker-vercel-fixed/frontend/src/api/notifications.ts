import { api } from '@/lib/api'

export interface NotificationRule {
  id: string
  workspace_id: string
  rule_type: 'assignment' | 'due_soon' | 'overdue' | 'digest' | 'sharing_summary'
  channel: 'email' | 'slack' | 'teams'
  is_active: boolean
  config: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface NotificationLog {
  id: string
  user_id: string
  item_id: string | null
  rule_type: string
  channel: string
  status: 'sent' | 'failed'
  sent_at: string
  error: string | null
  item_task: string | null
}

export interface CreateRulePayload {
  rule_type: 'assignment' | 'due_soon' | 'overdue' | 'digest' | 'sharing_summary'
  channel: NotificationRule['channel']
  config?: Record<string, unknown>
}

export async function getRules(): Promise<NotificationRule[]> {
  return api.get<NotificationRule[]>('/notifications/rules')
}

export async function createRule(payload: CreateRulePayload): Promise<NotificationRule> {
  return api.post<NotificationRule>('/notifications/rules', payload)
}

export async function toggleRule(ruleId: string, is_active: boolean): Promise<NotificationRule> {
  return api.patch<NotificationRule>(`/notifications/rules/${ruleId}`, { is_active })
}

export async function deleteRule(ruleId: string): Promise<void> {
  return api.del<void>(`/notifications/rules/${ruleId}`)
}

export async function getNotificationLog(): Promise<NotificationLog[]> {
  return api.get<NotificationLog[]>('/notifications/log')
}

export interface TestRuleResponse {
  success: boolean
  message: string
}

export async function testRule(ruleId: string): Promise<TestRuleResponse> {
  return api.post<TestRuleResponse>(`/notifications/rules/${ruleId}/test`, {})
}

export async function runRule(ruleId: string): Promise<TestRuleResponse> {
  return api.post<TestRuleResponse>(`/notifications/rules/${ruleId}/run`, {})
}
