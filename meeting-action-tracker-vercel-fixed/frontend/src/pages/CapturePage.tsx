import React, { useState, useEffect, useRef, useCallback } from 'react'
import axios from 'axios'
import { useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createMeeting, triggerExtraction, getMeeting, uploadTranscriptFile } from '@/api/meetings'
import { getProjects, createProject } from '@/api/projects'
import LoadingSpinner from '@/components/LoadingSpinner'

// H12: sessionStorage key for draft persistence
const DRAFT_STORAGE_KEY = 'capture_page_draft'

// ── Tabs ──────────────────────────────────────────────────────────────────────

type TabId = 'paste' | 'upload'

interface Tab {
  id: TabId
  label: string
}

const TABS: Tab[] = [
  { id: 'paste', label: 'Paste Notes / Transcript' },
  { id: 'upload', label: 'Upload File' },
]

// ── Form data ─────────────────────────────────────────────────────────────────

interface PasteFormData {
  title: string
  occurred_at: string
  attendees: string
  transcript: string
}

// ── Polling hook ──────────────────────────────────────────────────────────────

type ExtractionPhase = 'idle' | 'creating' | 'triggering' | 'polling' | 'done' | 'error'

// Max polls before showing a timeout error (90 × 2 s = 3 minutes)
const MAX_POLL_COUNT = 90

function normaliseMeetingDate(value: string): string | null {
  const cleaned = value.trim()
  if (!cleaned) return null

  const isoDate = /^(\d{4})-(\d{2})-(\d{2})$/.exec(cleaned)
  if (isoDate) return `${cleaned}T00:00:00`

  const localDate = /^(\d{2})-(\d{2})-(\d{4})$/.exec(cleaned)
  if (localDate) {
    const [, day, month, year] = localDate
    return `${year}-${month}-${day}T00:00:00`
  }

  return cleaned
}

function describeApiError(error: unknown): string {
  if (!axios.isAxiosError(error)) {
    return 'Something went wrong. Please try again.'
  }

  if (!error.response) {
    return 'Cannot reach the API. Check VITE_API_URL and backend deployment.'
  }

  const detail = error.response.data?.detail
  if (Array.isArray(detail)) {
    return detail
      .map((entry) => {
        const path = Array.isArray(entry.loc) ? entry.loc.join('.') : 'request'
        return `${path}: ${entry.msg ?? 'Invalid value'}`
      })
      .join('; ')
  }
  if (typeof detail === 'string') return detail

  return `Request failed with HTTP ${error.response.status}.`
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function CapturePage(): React.JSX.Element {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<TabId>('paste')
  const [phase, setPhase] = useState<ExtractionPhase>('idle')
  const [phaseMessage, setPhaseMessage] = useState('')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const hasSeenExtractingRef = useRef(false)
  const pollCountRef = useRef(0)

  // Project selector state
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [showNewProjectInput, setShowNewProjectInput] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [creatingProject, setCreatingProject] = useState(false)

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => getProjects(),
    staleTime: 60_000,
  })

  async function handleCreateProject(): Promise<void> {
    const name = newProjectName.trim()
    if (!name) return
    setCreatingProject(true)
    try {
      const project = await createProject({ name })
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      setSelectedProjectId(project.id)
      setNewProjectName('')
      setShowNewProjectInput(false)
    } catch {
      // silently ignore — user can retry
    } finally {
      setCreatingProject(false)
    }
  }

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<PasteFormData>({
    defaultValues: { title: '', occurred_at: '', attendees: '', transcript: '' },
  })

  // H12: Restore draft from sessionStorage on mount
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(DRAFT_STORAGE_KEY)
      if (raw !== null) {
        const draft = JSON.parse(raw) as Partial<PasteFormData>
        if (draft.title !== undefined) setValue('title', draft.title)
        if (draft.occurred_at !== undefined) setValue('occurred_at', draft.occurred_at)
        if (draft.attendees !== undefined) setValue('attendees', draft.attendees)
        if (draft.transcript !== undefined) setValue('transcript', draft.transcript)
      }
    } catch {
      // Silently ignore corrupt draft data
    }
  }, [setValue])

  // H12: Persist draft to sessionStorage on every change
  useEffect(() => {
    const subscription = watch((data) => {
      try {
        sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(data))
      } catch {
        // Ignore storage quota errors
      }
    })
    return () => subscription.unsubscribe()
  }, [watch])

  // Clear polling on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current !== null) {
        clearInterval(pollIntervalRef.current)
      }
    }
  }, [])

  function startPolling(meetingId: string): void {
    setPhase('polling')
    setPhaseMessage('Extracting action items…')
    hasSeenExtractingRef.current = false
    pollCountRef.current = 0

    pollIntervalRef.current = setInterval(() => {
      void (async () => {
        pollCountRef.current++

        // Hard timeout after MAX_POLL_COUNT polls (3 minutes)
        if (pollCountRef.current >= MAX_POLL_COUNT) {
          if (pollIntervalRef.current !== null) {
            clearInterval(pollIntervalRef.current)
            pollIntervalRef.current = null
          }
          setPhase('error')
          setErrorMessage(
            'Extraction timed out after 3 minutes. ' +
            'Check that ANTHROPIC_API_KEY is set in Railway Backend Variables and try again.'
          )
          return
        }

        try {
          const meeting = await getMeeting(meetingId)
          const status = meeting.status as string

          if (status === 'extracted' || status === 'reviewed') {
            if (pollIntervalRef.current !== null) {
              clearInterval(pollIntervalRef.current)
              pollIntervalRef.current = null
            }
            setPhase('done')
            void navigate(`/review/${meetingId}`)
          } else if (status === 'extracting') {
            // Track that extraction has started so we can detect failure
            hasSeenExtractingRef.current = true
            const elapsed = pollCountRef.current * 2
            setPhaseMessage(`Extracting action items… (${elapsed}s)`)
          } else if (status === 'failed') {
            // Explicit failure status from backend
            if (pollIntervalRef.current !== null) {
              clearInterval(pollIntervalRef.current)
              pollIntervalRef.current = null
            }
            setPhase('error')
            setErrorMessage(
              'Extraction failed. Check that ANTHROPIC_API_KEY is set in Railway Backend Variables. ' +
              'View the Railway backend logs for details.'
            )
          } else if (status === 'draft' && hasSeenExtractingRef.current) {
            // Status reverted from 'extracting' back to 'draft' — extraction failed on the backend
            if (pollIntervalRef.current !== null) {
              clearInterval(pollIntervalRef.current)
              pollIntervalRef.current = null
            }
            setPhase('error')
            setErrorMessage(
              'Extraction failed. Check that ANTHROPIC_API_KEY is set in Railway Backend Variables. ' +
              'View the Railway backend logs for details.'
            )
          }
          // If status is 'draft' and we haven't seen 'extracting' yet, keep polling briefly
        } catch {
          if (pollIntervalRef.current !== null) {
            clearInterval(pollIntervalRef.current)
            pollIntervalRef.current = null
          }
          setPhase('error')
          setErrorMessage('Failed to check extraction status. Please try again.')
        }
      })()
    }, 2000)
  }

  async function onSubmit(data: PasteFormData): Promise<void> {
    setErrorMessage(null)

    try {
      setPhase('creating')
      setPhaseMessage('Creating meeting record…')

      const attendees = data.attendees
        .split(',')
        .map((attendee) => attendee.trim())
        .filter((attendee) => attendee.length > 0)

      const meeting = await createMeeting({
        title: data.title.trim() !== '' ? data.title.trim() : undefined,
        source_type: 'paste',
        occurred_at: normaliseMeetingDate(data.occurred_at),
        attendees,
        transcript: data.transcript,
        project_id: selectedProjectId ?? undefined,
      })

      setPhase('triggering')
      setPhaseMessage('Starting AI extraction…')
      await triggerExtraction(meeting.id)

      startPolling(meeting.id)
      // Clear draft on successful submission
      try { sessionStorage.removeItem(DRAFT_STORAGE_KEY) } catch { /* ignore */ }
    } catch (error: unknown) {
      setPhase('error')
      setErrorMessage(describeApiError(error))
    }
  }

  // C11: Allow user to retry from error state
  function handleRetry(): void {
    setPhase('idle')
    setErrorMessage(null)
    hasSeenExtractingRef.current = false
    pollCountRef.current = 0
    if (pollIntervalRef.current !== null) {
      clearInterval(pollIntervalRef.current)
      pollIntervalRef.current = null
    }
  }

  const isLoading = phase === 'creating' || phase === 'triggering' || phase === 'polling'

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-tight text-slate-900">Capture a Meeting</h2>
        <p className="mt-1 text-sm text-slate-500">
          Paste your meeting notes or transcript and we will extract action items automatically.
        </p>
      </div>

      {/* Tab bar */}
      <div className="mb-6 flex border-b border-slate-200">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={`-mb-px px-4 py-2.5 text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-primary-400 focus:ring-inset ${
              activeTab === tab.id
                ? 'border-b-2 border-primary-700 text-primary-700'
                : 'text-primary-500 hover:text-primary-700'
            }`}
            aria-selected={activeTab === tab.id}
            role="tab"
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Project selector */}
      <div className="mb-5 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
        <label className="mb-1.5 block text-sm font-medium text-primary-700">
          Project <span className="text-primary-400">(optional)</span>
        </label>
        <div className="flex items-center gap-2">
          <select
            value={selectedProjectId ?? ''}
            onChange={(e) => setSelectedProjectId(e.target.value || null)}
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm text-primary-800 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
          >
            <option value="">No project</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setShowNewProjectInput((v) => !v)}
            className="whitespace-nowrap rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-primary-700 transition-colors hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-primary-400"
          >
            + New
          </button>
        </div>

        {showNewProjectInput && (
          <div className="mt-2 flex items-center gap-2">
            <input
              type="text"
              placeholder="Project name"
              value={newProjectName}
              onChange={(e) => setNewProjectName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void handleCreateProject() } }}
              className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm text-primary-800 placeholder-primary-400 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
              autoFocus
            />
            <button
              type="button"
              onClick={() => void handleCreateProject()}
              disabled={creatingProject || newProjectName.trim() === ''}
              className="rounded-lg bg-primary-700 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-800 disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              {creatingProject ? '…' : 'Create'}
            </button>
            <button
              type="button"
              onClick={() => { setShowNewProjectInput(false); setNewProjectName('') }}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-primary-600 hover:bg-slate-50 focus:outline-none"
            >
              Cancel
            </button>
          </div>
        )}
      </div>

      {/* Error message */}
      {errorMessage !== null && (
        <div
          role="alert"
          className="mb-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
        >
          <div className="flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            <span className="flex-1">{errorMessage}</span>
          </div>
          {/* C11: Retry button in error state */}
          {phase === 'error' && (
            <button
              type="button"
              onClick={handleRetry}
              className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-red-300 bg-white px-3 py-1.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-400"
            >
              Try again
            </button>
          )}
        </div>
      )}

      {/* Loading overlay */}
      {isLoading && (
        <div className="mb-6 flex items-center justify-center rounded-xl border border-violet-200 bg-violet-50 px-6 py-10">
          <LoadingSpinner size="lg" message={phaseMessage} />
        </div>
      )}

      {/* Paste tab */}
      {activeTab === 'paste' && !isLoading && (
        <form onSubmit={(e) => void handleSubmit(onSubmit)(e)} noValidate className="space-y-5">
          {/* Title */}
          <div>
            <label htmlFor="title" className="mb-1.5 block text-sm font-medium text-primary-700">
              Meeting title <span className="text-primary-400">(optional)</span>
            </label>
            <input
              id="title"
              type="text"
              placeholder="e.g. Q3 Planning Sync"
              {...register('title')}
              className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm text-primary-800 placeholder-primary-400 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>

          {/* Date + Attendees */}
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <div>
              <label htmlFor="occurred_at" className="mb-1.5 block text-sm font-medium text-primary-700">
                Meeting date <span className="text-primary-400">(optional)</span>
              </label>
              <input
                id="occurred_at"
                type="date"
                {...register('occurred_at')}
                className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm text-primary-800 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
            </div>

            <div>
              <label htmlFor="attendees" className="mb-1.5 block text-sm font-medium text-primary-700">
                Attendees <span className="text-primary-400">(comma-separated, optional)</span>
              </label>
              <input
                id="attendees"
                type="text"
                placeholder="Alice, Bob, Carol"
                maxLength={500}
                {...register('attendees', { maxLength: 500 })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm text-primary-800 placeholder-primary-400 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
            </div>
          </div>

          {/* Transcript */}
          <div>
            <label htmlFor="transcript" className="mb-1.5 block text-sm font-medium text-primary-700">
              Notes / Transcript <span className="text-red-500">*</span>
            </label>
            <textarea
              id="transcript"
              rows={12}
              placeholder="Paste your meeting notes or transcript here…"
              {...register('transcript', {
                required: 'Transcript is required',
                minLength: { value: 20, message: 'Transcript must be at least 20 characters' },
              })}
              aria-invalid={errors.transcript !== undefined ? 'true' : 'false'}
              aria-describedby={errors.transcript !== undefined ? 'transcript-error' : undefined}
              className={`w-full resize-y rounded-lg border px-3 py-2.5 font-mono text-sm text-primary-800 placeholder-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-500 ${
                errors.transcript !== undefined
                  ? 'border-red-300 bg-red-50 focus:border-red-400 focus:ring-red-400'
                  : 'border-slate-300 bg-white focus:border-primary-500'
              }`}
            />
            {errors.transcript !== undefined && (
              <p id="transcript-error" role="alert" className="mt-1.5 text-xs text-red-600">
                {errors.transcript.message}
              </p>
            )}
          </div>

          {/* Submit */}
          <div className="flex justify-end">
            <button
              type="submit"
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-br from-primary-700 to-primary-900 px-6 py-2.5 text-sm font-semibold text-white shadow-md shadow-primary-900/20 transition-all hover:shadow-lg hover:shadow-primary-900/30 hover:-translate-y-px focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-8.707l-3-3a1 1 0 00-1.414 0l-3 3a1 1 0 001.414 1.414L9 9.414V13a1 1 0 102 0V9.414l1.293 1.293a1 1 0 001.414-1.414z" clipRule="evenodd" />
              </svg>
              Extract Action Items
            </button>
          </div>
        </form>
      )}

      {/* Upload tab */}
      {activeTab === 'upload' && !isLoading && (
        <UploadTab
          projectId={selectedProjectId}
          onUploaded={(meetingId) => void navigate(`/meeting/${meetingId}/status`)}
        />
      )}
    </div>
  )
}

// ── UploadTab ─────────────────────────────────────────────────────────────────

const ACCEPTED_EXTENSIONS = ['.txt', '.vtt', '.srt']
const MAX_FILE_BYTES = 10 * 1024 * 1024 // 10 MB — matches backend limit

function isAcceptedFile(file: File): boolean {
  const nameLower = file.name.toLowerCase()
  return ACCEPTED_EXTENSIONS.some((ext) => nameLower.endsWith(ext))
}

interface UploadTabProps {
  onUploaded: (meetingId: string) => void
  projectId?: string | null
}

function UploadTab({ onUploaded, projectId }: UploadTabProps): React.JSX.Element {
  const [dragOver, setDragOver] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  function selectFile(file: File): void {
    setUploadError(null)
    if (!isAcceptedFile(file)) {
      setUploadError('Unsupported file type. Please upload a .txt, .vtt, or .srt file.')
      return
    }
    if (file.size > MAX_FILE_BYTES) {
      setUploadError('File is too large. Maximum size is 10 MB.')
      return
    }
    setSelectedFile(file)
  }

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) selectFile(file)
  }, [])

  function handleFileInput(e: React.ChangeEvent<HTMLInputElement>): void {
    const file = e.target.files?.[0]
    if (file) selectFile(file)
  }

  async function handleUpload(): Promise<void> {
    if (!selectedFile) return
    setUploadError(null)
    setUploadProgress(0)
    try {
      const result = await uploadTranscriptFile(selectedFile, setUploadProgress, projectId)
      onUploaded(result.meeting_id)
    } catch (err: unknown) {
      setUploadProgress(null)
      const msg =
        err instanceof Error ? err.message : 'Upload failed. Please try again.'
      setUploadError(msg)
    }
  }

  const isUploading = uploadProgress !== null

  return (
    <div className="space-y-5">
      {/* Drop zone */}
      <div
        role="button"
        tabIndex={0}
        aria-label="Click or drag a transcript file here to upload"
        onClick={() => fileInputRef.current?.click()}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click() }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-8 py-14 text-center cursor-pointer transition-colors focus:outline-none focus:ring-2 focus:ring-primary-400 ${
          dragOver
            ? 'border-primary-500 bg-primary-50'
            : selectedFile
              ? 'border-green-400 bg-green-50'
              : 'border-slate-300 bg-slate-50 hover:border-primary-400 hover:bg-slate-100'
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.vtt,.srt"
          className="sr-only"
          onChange={handleFileInput}
          aria-hidden="true"
        />

        {selectedFile ? (
          <>
            <svg xmlns="http://www.w3.org/2000/svg" className="mb-3 h-10 w-10 text-green-500" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clipRule="evenodd" />
            </svg>
            <p className="text-sm font-semibold text-green-700">{selectedFile.name}</p>
            <p className="mt-1 text-xs text-green-600">{(selectedFile.size / 1024).toFixed(1)} KB — click to change</p>
          </>
        ) : (
          <>
            <svg xmlns="http://www.w3.org/2000/svg" className="mb-3 h-10 w-10 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
            </svg>
            <p className="text-sm font-semibold text-primary-700">
              {dragOver ? 'Drop to upload' : 'Drag & drop or click to browse'}
            </p>
            <p className="mt-1 text-xs text-primary-500">Supports .txt, .vtt, .srt — max 10 MB</p>
          </>
        )}
      </div>

      {/* Upload progress */}
      {isUploading && (
        <div>
          <div className="mb-1 flex items-center justify-between text-xs text-primary-600">
            <span>Uploading…</span>
            <span>{uploadProgress}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
            <div
              className="h-2 rounded-full bg-primary-600 transition-all"
              style={{ width: `${uploadProgress ?? 0}%` }}
            />
          </div>
        </div>
      )}

      {/* Error */}
      {uploadError !== null && (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {uploadError}
        </div>
      )}

      {/* Submit */}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => void handleUpload()}
          disabled={selectedFile === null || isUploading}
          className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-br from-primary-700 to-primary-900 px-6 py-2.5 text-sm font-semibold text-white shadow-md shadow-primary-900/20 transition-all hover:shadow-lg hover:shadow-primary-900/30 hover:-translate-y-px focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0 disabled:hover:shadow-md"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-8.707l-3-3a1 1 0 00-1.414 0l-3 3a1 1 0 001.414 1.414L9 9.414V13a1 1 0 102 0V9.414l1.293 1.293a1 1 0 001.414-1.414z" clipRule="evenodd" />
          </svg>
          {isUploading ? 'Uploading…' : 'Upload & Extract'}
        </button>
      </div>
    </div>
  )
}
