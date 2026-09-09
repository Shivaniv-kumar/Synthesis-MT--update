import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { usePolling } from '@/hooks/usePolling'
import type { ExtractionStatus } from '@/types'

// ── Constants ─────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 3_000
const TIMEOUT_MS = 5 * 60 * 1_000 // 5 minutes

// ── Spinner ───────────────────────────────────────────────────────────────────

function Spinner(): React.JSX.Element {
  return (
    <svg
      className="animate-spin h-6 w-6 text-blue-600"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v8H4z"
      />
    </svg>
  )
}

// ── Checkmark ─────────────────────────────────────────────────────────────────

function Checkmark(): React.JSX.Element {
  return (
    <div className="flex items-center justify-center w-10 h-10 rounded-full bg-green-100">
      <svg
        className="h-6 w-6 text-green-600"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
      </svg>
    </div>
  )
}

// ── Step indicator ────────────────────────────────────────────────────────────

interface StepProps {
  label: string
  isActive: boolean
  isDone: boolean
}

function Step({ label, isActive, isDone }: StepProps): React.JSX.Element {
  return (
    <div
      className={[
        'flex items-center gap-3 px-4 py-3 rounded-lg transition-all duration-300',
        isActive ? 'bg-blue-50 border border-blue-200' : '',
        isDone ? 'opacity-50' : '',
        !isActive && !isDone ? 'opacity-40' : '',
      ].join(' ')}
    >
      <div className="shrink-0 flex items-center justify-center w-8 h-8">
        {isDone ? (
          <svg
            className="h-5 w-5 text-green-500"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        ) : isActive ? (
          <Spinner />
        ) : (
          <span className="h-5 w-5 rounded-full border-2 border-gray-300" />
        )}
      </div>
      <span
        className={[
          'text-sm font-medium',
          isActive ? 'text-blue-700' : isDone ? 'text-gray-500' : 'text-gray-400',
        ].join(' ')}
      >
        {label}
      </span>
    </div>
  )
}

// ── MeetingStatusPage ─────────────────────────────────────────────────────────

/**
 * Polls the extraction status of a meeting and shows animated step indicators.
 * Redirects to the review page when extraction is complete.
 * Shows an error state after 5 minutes or if the API reports an error.
 */
export default function MeetingStatusPage(): React.JSX.Element {
  const { meetingId } = useParams<{ meetingId: string }>()
  const navigate = useNavigate()

  const [pollingEnabled, setPollingEnabled] = useState(true)
  const [timedOut, setTimedOut] = useState(false)
  const [extractionFailed, setExtractionFailed] = useState(false)
  const [extractionError, setExtractionError] = useState<string | null>(null)
  const [announced, setAnnounced] = useState('')

  const startTimeRef = useRef<number>(Date.now())
  const redirectingRef = useRef(false)
  // Track whether we've ever seen "extracting" so a reversion to "draft"
  // is recognised as a failure rather than the initial upload state.
  const seenExtractingRef = useRef(false)

  // Fetcher function for the polling hook
  const fetcher = useCallback((): Promise<ExtractionStatus> => {
    if (!meetingId) return Promise.reject(new Error('No meeting ID provided'))
    return api.get<ExtractionStatus>(`/upload/status/${meetingId}`)
  }, [meetingId])

  // Handle incoming data from the poll
  const handleData = useCallback(
    (data: ExtractionStatus) => {
      // Check 5-minute timeout
      if (Date.now() - startTimeRef.current > TIMEOUT_MS) {
        setTimedOut(true)
        setPollingEnabled(false)
        setAnnounced('Processing timed out. Please try again.')
        return
      }

      if (data.status === 'extracted') {
        setPollingEnabled(false)
        setAnnounced('Done! Redirecting to review…')
        if (!redirectingRef.current) {
          redirectingRef.current = true
          // Small delay so the "Done!" state is visible before redirect
          setTimeout(() => {
            void navigate(`/review/${meetingId}`, { replace: true })
          }, 1500)
        }
        return
      }

      if (data.status === 'extracting') {
        seenExtractingRef.current = true
        setAnnounced('Extracting action items…')
        return
      }

      // "draft" after we've seen "extracting" means extraction failed and the
      // worker rolled back the status.  Show the error immediately.
      if (data.status === 'draft' && seenExtractingRef.current) {
        setPollingEnabled(false)
        setExtractionFailed(true)
        setExtractionError(data.error ?? null)
        setAnnounced('Extraction failed. Please try again.')
      }
    },
    [meetingId, navigate],
  )

  const { data, error } = usePolling<ExtractionStatus>(
    fetcher,
    POLL_INTERVAL_MS,
    pollingEnabled,
    handleData,
  )

  // Stop polling on error
  useEffect(() => {
    if (error) {
      setPollingEnabled(false)
      setAnnounced('An error occurred. Please go back and try again.')
    }
  }, [error])

  // ── Derived state ──────────────────────────────────────────────────────────

  const status = data?.status ?? 'draft'
  const isError = !!error || timedOut || extractionFailed

  // Determine which step is active / done
  const isUploading = status === 'draft'
  const isExtracting = status === 'extracting'
  const isExtracted = status === 'extracted' || status === 'reviewed'

  const uploadDone = isExtracting || isExtracted
  const extractDone = isExtracted

  // ── Error state ────────────────────────────────────────────────────────────

  if (isError) {
    const errorMessage = timedOut
      ? 'Processing took too long. Please try uploading again.'
      : extractionError
        ? extractionError
        : error?.message ?? 'Extraction failed. Please go back and try again.'

    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
        <div className="max-w-md w-full text-center space-y-6">
          <div className="mx-auto w-16 h-16 rounded-full bg-red-100 flex items-center justify-center">
            <svg
              className="w-8 h-8 text-red-600"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </div>
          <h1 className="text-xl font-semibold text-gray-900">Processing failed</h1>
          <p className="text-sm text-gray-600">{errorMessage}</p>
          <Link
            to="/capture"
            className="inline-block px-5 py-2.5 rounded-lg text-sm font-medium
                       text-white bg-blue-600 hover:bg-blue-700 transition-colors
                       focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500
                       focus-visible:ring-offset-2"
          >
            Go back
          </Link>
        </div>
      </div>
    )
  }

  // ── Normal state ───────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <div className="max-w-sm w-full space-y-8">
        {/* Header */}
        <div className="text-center space-y-2">
          {isExtracted ? (
            <div className="flex justify-center">
              <Checkmark />
            </div>
          ) : (
            <div className="flex justify-center">
              <Spinner />
            </div>
          )}

          <h1 className="text-xl font-semibold text-gray-900 mt-4">
            {isExtracted ? 'Done! Redirecting…' : 'Processing your meeting'}
          </h1>
          <p className="text-sm text-gray-500">
            {isExtracted
              ? 'Action items have been extracted successfully.'
              : 'This usually takes less than a minute.'}
          </p>
        </div>

        {/* Steps */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 space-y-1">
          <Step
            label="Uploading…"
            isActive={isUploading}
            isDone={uploadDone}
          />
          <Step
            label="Extracting action items…"
            isActive={isExtracting}
            isDone={extractDone}
          />
          <Step
            label="Done! Redirecting…"
            isActive={isExtracted}
            isDone={false}
          />
        </div>

        {/* Cancel / go back link */}
        {!isExtracted && (
          <div className="text-center">
            <Link
              to="/capture"
              className="text-sm text-gray-500 hover:text-gray-700 underline
                         focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500
                         rounded"
            >
              Cancel and go back
            </Link>
          </div>
        )}

        {/* aria-live region for screen reader announcements */}
        <div aria-live="polite" aria-atomic="true" className="sr-only">
          {announced}
        </div>
      </div>
    </div>
  )
}
