import React, { useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import axios from 'axios'
import { getMeeting } from '@/api/meetings'
import { getItems, updateItem, deleteItem, bulkSaveItems } from '@/api/items'
import type { ActionItem, UpdateItemRequest } from '@/types'
import ActionItemCard from '@/components/ActionItemCard'
import LoadingSpinner from '@/components/LoadingSpinner'
import EmptyState from '@/components/EmptyState'
import StatusBadge from '@/components/StatusBadge'

function describeApiError(label: string, error: unknown): string {
  if (!axios.isAxiosError(error)) return `${label}: unexpected client error.`
  if (!error.response) return `${label}: no response from API. Check network and VITE_API_URL.`

  const detail = error.response.data?.detail
  const detailText =
    typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((entry) => entry?.msg ?? JSON.stringify(entry)).join('; ')
        : JSON.stringify(detail ?? error.response.data)

  return `${label}: HTTP ${error.response.status}${detailText ? ` - ${detailText}` : ''}`
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function ReviewPage(): React.JSX.Element {
  const { meetingId } = useParams<{ meetingId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirmError, setConfirmError] = useState<string | null>(null)

  // Guard: meetingId must be present (router guarantees this)
  if (meetingId === undefined) {
    return (
      <div className="p-8 text-sm text-red-600">Invalid meeting URL.</div>
    )
  }

  // ── Queries ──────────────────────────────────────────────────────────────────

  const {
    data: meeting,
    isLoading: meetingLoading,
    isError: meetingError,
    error: meetingQueryError,
    refetch: refetchMeeting,
  } = useQuery({
    queryKey: ['meeting', meetingId],
    queryFn: () => getMeeting(meetingId),
  })

  const {
    data: itemsPage,
    isLoading: itemsLoading,
    isError: itemsError,
    error: itemsQueryError,
    refetch: refetchItems,
  } = useQuery({
    // M14: stable primitive query key — avoids refetch on inline object identity change
    queryKey: ['items', 'meeting', meetingId],
    queryFn: () => getItems({ meeting_id: meetingId, page_size: 100 }),
    enabled: meeting !== undefined,
  })

  // ── Mutations ────────────────────────────────────────────────────────────────

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateItemRequest }) =>
      updateItem(id, data),
    onSuccess: () => {
      // M14: key matches the stable form used in useQuery above
      void queryClient.invalidateQueries({ queryKey: ['items', 'meeting', meetingId] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteItem(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['items', 'meeting', meetingId] })
    },
  })

  const bulkSaveMutation = useMutation({
    mutationFn: (items: ActionItem[]) => bulkSaveItems(items),
    onSuccess: () => {
      // M13: invalidate items list so tracker page reflects the newly saved items
      void queryClient.invalidateQueries({ queryKey: ['items'] })
      void navigate('/tracker')
    },
    onError: () => {
      setConfirmError('Failed to save items. Please try again.')
    },
  })

  // ── Handlers ─────────────────────────────────────────────────────────────────

  const handleUpdate = useCallback(
    (id: string, data: UpdateItemRequest): void => {
      updateMutation.mutate({ id, data })
    },
    [updateMutation],
  )

  const handleDelete = useCallback(
    (id: string): void => {
      deleteMutation.mutate(id)
    },
    [deleteMutation],
  )

  function handleConfirmAll(): void {
    if (itemsPage === undefined) return
    setConfirmError(null)
    bulkSaveMutation.mutate(itemsPage.items)
  }

  // ── Loading / error states ───────────────────────────────────────────────────

  if (meetingLoading || itemsLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" message="Loading extracted items…" />
      </div>
    )
  }

  if (meetingError || itemsError) {
    const failedError = meetingError ? meetingQueryError : itemsQueryError
    const is404 =
      axios.isAxiosError(failedError) && failedError.response?.status === 404

    const errorDescription = is404
      ? 'This meeting was not found — it may be from a different session or has been deleted. Go to Capture to start a new one.'
      : meetingError
        ? describeApiError('Meeting request failed', meetingQueryError)
        : describeApiError('Items request failed', itemsQueryError)

    return (
      <EmptyState
        title="Failed to load meeting"
        description={errorDescription}
        actionLabel={is404 ? 'Go to Capture' : 'Retry'}
        {...(is404
          ? { captureTo: '/capture' }
          : {
              onAction: () => {
                void refetchMeeting()
                void refetchItems()
              },
            })}
        className="mt-8"
      />
    )
  }

  if (meeting === undefined) {
    return <div className="p-8 text-sm text-primary-500">Meeting not found.</div>
  }

  const items = itemsPage?.items ?? []
  const reviewItems = items.filter((i) => i.needs_review)
  const normalItems = items.filter((i) => !i.needs_review)
  const sortedItems = [...reviewItems, ...normalItems]

  return (
    <div className="mx-auto max-w-3xl">
      {/* Header */}
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-primary-900">
            {meeting.title !== '' ? meeting.title : 'Untitled Meeting'}
          </h2>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <StatusBadge status={meeting.status} />
            <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-primary-600">
              {items.length} item{items.length !== 1 ? 's' : ''} extracted
            </span>
            {reviewItems.length > 0 && (
              <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-700 ring-1 ring-inset ring-amber-200">
                {reviewItems.length} need{reviewItems.length === 1 ? 's' : ''} review
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => void navigate('/capture')}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-primary-600 transition-colors hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-primary-400 focus:ring-offset-1"
          >
            Back
          </button>
          <button
            type="button"
            onClick={handleConfirmAll}
            disabled={bulkSaveMutation.isPending || items.length === 0}
            className="inline-flex items-center gap-2 rounded-lg bg-primary-700 px-5 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-primary-800 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {bulkSaveMutation.isPending ? (
              <>
                <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24" aria-hidden="true">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Saving…
              </>
            ) : (
              <>
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
                Confirm All
              </>
            )}
          </button>
        </div>
      </div>

      {/* Save error */}
      {confirmError !== null && (
        <div role="alert" className="mb-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <div className="flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            <span className="flex-1">{confirmError}</span>
            {/* C12: Retry button next to the save error */}
            <button
              type="button"
              onClick={() => {
                setConfirmError(null)
                bulkSaveMutation.reset()
              }}
              className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-700 transition-colors hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-400"
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Items or empty state */}
      {items.length === 0 ? (
        <EmptyState
          title="No items extracted"
          description="The AI did not find any action items in this transcript. You can go back and try with a different transcript."
          actionLabel="Back to Capture"
          onAction={() => void navigate('/capture')}
          className="mt-4"
        />
      ) : (
        <>
          {/* Needs-review section */}
          {reviewItems.length > 0 && (
            <div className="mb-6">
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-amber-700">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
                </svg>
                Items needing review ({reviewItems.length})
              </h3>
              <div className="space-y-3">
                {reviewItems.map((item) => (
                  <ActionItemCard
                    key={item.id}
                    item={item}
                    onUpdate={handleUpdate}
                    onDelete={handleDelete}
                    editMode={false}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Normal items */}
          {normalItems.length > 0 && (
            <div>
              {reviewItems.length > 0 && (
                <h3 className="mb-3 text-sm font-semibold text-primary-700">
                  All items ({normalItems.length})
                </h3>
              )}
              <div className="space-y-3">
                {(reviewItems.length > 0 ? normalItems : sortedItems).map((item) => (
                  <ActionItemCard
                    key={item.id}
                    item={item}
                    onUpdate={handleUpdate}
                    onDelete={handleDelete}
                    editMode={false}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
