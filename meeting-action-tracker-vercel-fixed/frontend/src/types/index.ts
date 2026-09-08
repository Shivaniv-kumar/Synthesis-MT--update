// ── Core domain types ────────────────────────────────────────────────────────

export interface Project {
  id: string
  name: string
  description: string | null
  color: string | null
  is_active: boolean
  created_at: string
  created_by: string | null
}

export interface ActionItem {
  id: string
  meeting_id: string
  task: string
  owner_user_id: string | null
  owner_label: string
  priority: 'High' | 'Medium' | 'Low'
  due_date: string | null
  due_text: string
  status: 'Open' | 'In progress' | 'Done'
  context: string
  confidence: number
  needs_review: boolean
  reviewed_by: string | null
  reviewed_at: string | null
  reviewed_by_name: string | null
  created_at: string
  updated_at: string
  meeting_title: string | null
  project_name: string | null
}

export interface Meeting {
  id: string
  title: string
  source_type: 'paste' | 'file' | 'audio' | 'integration'
  occurred_at: string | null
  attendees: string[]
  status: 'draft' | 'extracting' | 'extracted' | 'reviewed' | 'failed'
  created_at: string
  project_id: string | null
}

export interface User {
  id: string
  email: string
  display_name: string
  role: 'Admin' | 'Member' | 'Viewer'
  workspace_id: string
}

// ── Request / response shapes ────────────────────────────────────────────────

export interface CreateMeetingRequest {
  title?: string
  source_type: Meeting['source_type']
  occurred_at?: string | null
  attendees?: string[]
  /** Raw transcript text for 'paste' source_type */
  transcript?: string
  project_id?: string | null
}

export interface UpdateItemRequest {
  task?: string
  owner_user_id?: string | null
  owner_label?: string
  priority?: ActionItem['priority']
  due_date?: string | null
  due_text?: string
  status?: ActionItem['status']
  context?: string
  needs_review?: boolean  // false = mark reviewed; true = re-flag for review
}

// ── Filter / query params ────────────────────────────────────────────────────

export interface ItemFilters {
  meeting_id?: string
  project_id?: string
  status?: ActionItem['status'] | 'all'
  priority?: ActionItem['priority'] | 'all'
  owner_user_id?: string
  needs_review?: boolean
  search?: string
  page?: number
  page_size?: number
}

// ── Item comments ────────────────────────────────────────────────────────────

export interface ItemComment {
  id: string
  item_id: string
  user_id: string | null
  user_display_name: string
  content: string
  created_at: string
  updated_at: string
}

// ── Workspace ────────────────────────────────────────────────────────────────

export interface Workspace {
  id: string
  name: string
}

// ── Workspace admin ──────────────────────────────────────────────────────────

export interface WorkspaceMember {
  user_id: string
  email: string
  display_name: string
  role: 'Admin' | 'Member' | 'Viewer'
  workspace_id?: string
  invite_pending?: boolean
  joined_at?: string
}

export interface RetentionConfig {
  id: string
  tenant_id: string
  workspace_id: string
  transcript_retention_days: number
  item_retention_days: number
  audio_retention_days: number
  updated_at: string | null
  updated_by: string | null
}

export interface RetentionConfigPatch {
  transcript_retention_days?: number
  item_retention_days?: number
  audio_retention_days?: number
}

// ── Dashboard / analytics ────────────────────────────────────────────────────

export interface DashboardSummary {
  total_open: number
  total_in_progress: number
  total_done: number
  overdue_count: number
  needs_review_count: number
  meetings_this_week: number
  items_by_priority: {
    High: number
    Medium: number
    Low: number
  }
  items_by_owner: Array<{
    owner_label: string
    count: number
  }>
}

// ── Extraction polling ────────────────────────────────────────────────────────

export interface ExtractionStatus {
  meeting_id: string
  status: Meeting['status']
  items_extracted: number
  error?: string | null
}

// ── Knowledge base ────────────────────────────────────────────────────────────

export type KnowledgeCategory =
  | 'dependency'
  | 'constraint'
  | 'decision'
  | 'blocker'
  | 'risk'
  | 'tradeoff'
  | 'principle'
  | 'assumption'
  | 'open_question'

export interface KnowledgeEntry {
  id: string
  tenant_id: string
  workspace_id: string
  meeting_id: string
  project_id: string | null
  category: KnowledgeCategory
  content: string
  source_quote: string | null
  latest_update: string | null
  is_closed: boolean
  created_by: string | null
  edited_by: string | null
  edited_at: string | null
  created_at: string
  updated_at: string
  meeting_title: string | null
  project_name: string | null
}

export interface KnowledgeEntryCreate {
  meeting_id: string
  category: KnowledgeCategory
  content: string
  source_quote?: string | null
  latest_update?: string | null
  project_id?: string | null
}

export interface KnowledgeEntryPatch {
  category?: KnowledgeCategory
  content?: string
  source_quote?: string | null
  latest_update?: string | null
  is_closed?: boolean
}

export interface KnowledgeFilters {
  project_id?: string
  category?: KnowledgeCategory | KnowledgeCategory[]
  meeting_id?: string
  search?: string
  page?: number
  page_size?: number
}

// ── Pagination ────────────────────────────────────────────────────────────────

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  has_next: boolean
  has_prev: boolean
}
