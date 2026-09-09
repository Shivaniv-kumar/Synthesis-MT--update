import { api } from '@/lib/api'
import type { DashboardSummary, Meeting, PaginatedResponse } from '@/types'

// ── Dashboard item count per meeting ─────────────────────────────────────────

export interface MeetingWithItemCount extends Meeting {
  item_count: number
}

// ── API functions ─────────────────────────────────────────────────────────────

/**
 * Fetch aggregated dashboard statistics.
 */
export async function getDashboardSummary(): Promise<DashboardSummary> {
  return api.get<DashboardSummary>('/dashboard/summary')
}

/**
 * Fetch the most recently extracted meetings with their item counts.
 */
export async function getRecentMeetings(
  limit = 5,
): Promise<PaginatedResponse<MeetingWithItemCount>> {
  return api.get<PaginatedResponse<MeetingWithItemCount>>('/meetings', {
    params: { page: 1, page_size: limit, status: 'extracted' },
  })
}
