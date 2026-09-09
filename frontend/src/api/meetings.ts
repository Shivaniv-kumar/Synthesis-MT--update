import { api } from '@/lib/api'
import type { Meeting, CreateMeetingRequest, PaginatedResponse } from '@/types'

// ── Upload response ───────────────────────────────────────────────────────────

export interface UploadTranscriptResponse {
  meeting_id: string
  status: string
  message: string
}

// ── Query params for listing meetings ─────────────────────────────────────────

export interface MeetingListParams {
  page?: number
  page_size?: number
  status?: Meeting['status'] | 'all'
}

// ── Extraction trigger response ───────────────────────────────────────────────

export interface ExtractionTriggerResponse {
  status: string
  meeting_id: string
}

// ── API functions ─────────────────────────────────────────────────────────────

/**
 * Create a new meeting record (optionally with transcript for paste source).
 */
export async function createMeeting(data: CreateMeetingRequest): Promise<Meeting> {
  // Backend schema uses 'transcript_text'; public interface uses 'transcript'
  const { transcript, ...rest } = data
  const payload = transcript !== undefined ? { ...rest, transcript_text: transcript } : rest
  return api.post<Meeting>('/meetings/', payload)
}

/**
 * Fetch a paginated list of meetings.
 */
export async function getMeetings(
  params: MeetingListParams = {},
): Promise<PaginatedResponse<Meeting>> {
  return api.get<PaginatedResponse<Meeting>>('/meetings/', { params })
}

/**
 * Fetch a single meeting by ID.
 */
export async function getMeeting(id: string): Promise<Meeting> {
  return api.get<Meeting>(`/meetings/${id}`)
}

/**
 * Kick off AI extraction for the given meeting.
 * The meeting status will transition: draft → extracting → extracted.
 */
export async function triggerExtraction(id: string): Promise<ExtractionTriggerResponse> {
  return api.post<ExtractionTriggerResponse>(`/meetings/${id}/extract`, undefined, {
    // Serverless deployments may keep this request open while the AI
    // extraction fallback completes.
    timeout: 180_000,
  })
}

/**
 * Delete a meeting and all its associated action items.
 */
export async function deleteMeeting(id: string): Promise<void> {
  return api.del<void>(`/meetings/${id}`)
}

/**
 * Upload a transcript file (.txt, .vtt, .srt) and immediately trigger extraction.
 * Returns a meeting_id that can be polled at /meeting/:id/status.
 */
export async function uploadTranscriptFile(
  file: File,
  onProgress?: (percent: number) => void,
  projectId?: string | null,
): Promise<UploadTranscriptResponse> {
  const form = new FormData()
  form.append('file', file)
  if (projectId) form.append('project_id', projectId)
  const response = await api.instance.post<UploadTranscriptResponse>('/upload/transcript-file', form, {
    // Axios v1.x does not automatically clear the instance-level Content-Type when
    // sending FormData. Setting it to undefined here forces the browser to set
    // multipart/form-data with the correct boundary string instead.
    headers: { 'Content-Type': undefined },
    // The backend can perform two sequential AI passes (actions + knowledge)
    // before the serverless invocation finishes. Do not inherit the 30s
    // default used by ordinary API requests.
    timeout: 180_000,
    onUploadProgress: (evt) => {
      if (onProgress && evt.total) {
        onProgress(Math.round((evt.loaded / evt.total) * 100))
      }
    },
  })
  return response.data
}
