import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryResult,
  type UseMutationResult,
} from '@tanstack/react-query'
import {
  createMeeting,
  getMeetings,
  triggerExtraction,
  deleteMeeting,
  type MeetingListParams,
  type ExtractionTriggerResponse,
} from '@/api/meetings'
import type { Meeting, CreateMeetingRequest, PaginatedResponse } from '@/types'

// ── Query key factory ─────────────────────────────────────────────────────────

const meetingKeys = {
  all: ['meetings'] as const,
  list: (params?: MeetingListParams) => ['meetings', params] as const,
}

// ── useMeetings ───────────────────────────────────────────────────────────────

/**
 * Fetches a paginated list of meetings.
 */
export function useMeetings(
  params?: MeetingListParams,
): UseQueryResult<PaginatedResponse<Meeting>> {
  return useQuery({
    queryKey: meetingKeys.list(params),
    queryFn: () => getMeetings(params),
    staleTime: 60_000,
  })
}

// ── useCreateMeeting ──────────────────────────────────────────────────────────

/**
 * Mutation to create a new meeting.
 * Invalidates all meeting queries on success.
 */
export function useCreateMeeting(): UseMutationResult<
  Meeting,
  Error,
  CreateMeetingRequest
> {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: CreateMeetingRequest) => createMeeting(data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: meetingKeys.all })
    },
  })
}

// ── useTriggerExtraction ──────────────────────────────────────────────────────

/**
 * Mutation to trigger AI extraction for a meeting.
 * The meeting status transitions: draft → extracting → extracted.
 */
export function useTriggerExtraction(): UseMutationResult<
  ExtractionTriggerResponse,
  Error,
  string
> {
  return useMutation({
    mutationFn: (meetingId: string) => triggerExtraction(meetingId),
  })
}

// ── useDeleteMeeting ──────────────────────────────────────────────────────────

/**
 * Mutation to delete a meeting and all its action items.
 * Invalidates all meeting queries on success.
 */
export function useDeleteMeeting(): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => deleteMeeting(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: meetingKeys.all })
    },
  })
}
