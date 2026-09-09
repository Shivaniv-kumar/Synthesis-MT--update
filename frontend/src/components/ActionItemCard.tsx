import React, { useState, useRef, useEffect } from 'react'
import { format, parseISO, isValid, formatDistanceToNow } from 'date-fns'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/contexts/AuthContext'
import { getComments, addComment, deleteComment } from '@/api/comments'
import type { ActionItem, ItemComment, UpdateItemRequest } from '@/types'
import StatusBadge from './StatusBadge'
import PriorityBadge from './PriorityBadge'

// ── Props ─────────────────────────────────────────────────────────────────────

interface ActionItemCardProps {
  item: ActionItem
  onUpdate: (id: string, data: UpdateItemRequest) => void
  onDelete: (id: string) => void
  /** When true, renders extra fields as editable inputs */
  editMode?: boolean
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDueDate(dueDateStr: string | null): string {
  if (dueDateStr === null) return ''
  try {
    const parsed = parseISO(dueDateStr)
    return isValid(parsed) ? format(parsed, 'MMM d, yyyy') : dueDateStr
  } catch {
    return dueDateStr
  }
}

function initials(label: string): string {
  return label
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
}

const AVATAR_COLORS = [
  'bg-violet-500', 'bg-blue-500', 'bg-teal-500', 'bg-emerald-500',
  'bg-amber-500',  'bg-rose-500', 'bg-pink-500', 'bg-indigo-500',
  'bg-cyan-600',   'bg-orange-500',
]

function ownerAvatarColor(label: string): string {
  if (!label) return 'bg-slate-400'
  let hash = 0
  for (let i = 0; i < label.length; i++) {
    hash = (hash * 31 + label.charCodeAt(i)) & 0xffffffff
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length]
}

const PRIORITY_STRIPE: Record<ActionItem['priority'], string> = {
  High:   'bg-red-500',
  Medium: 'bg-amber-400',
  Low:    'bg-emerald-500',
}

// ── Comments thread ───────────────────────────────────────────────────────────

function commentAvatarColor(name: string): string {
  if (!name) return 'bg-slate-400'
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffffffff
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length]
}

function commentInitials(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase() ?? '').join('') || '?'
}

interface CommentsThreadProps {
  itemId: string
}

function CommentsThread({ itemId }: CommentsThreadProps): React.JSX.Element {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const { data: comments = [], isLoading } = useQuery({
    queryKey: ['comments', itemId],
    queryFn: () => getComments(itemId),
    staleTime: 15_000,
  })

  const addMutation = useMutation({
    mutationFn: (content: string) => addComment(itemId, content),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['comments', itemId] })
      setDraft('')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (commentId: string) => deleteComment(itemId, commentId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['comments', itemId] }),
  })

  function handleSubmit(e: React.FormEvent): void {
    e.preventDefault()
    const text = draft.trim()
    if (!text) return
    addMutation.mutate(text)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      const text = draft.trim()
      if (text) addMutation.mutate(text)
    }
  }

  // Auto-grow textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [draft])

  return (
    <div className="border-t border-slate-100 px-5 pb-4 pt-3">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-400">
        Updates · {comments.length}
      </h3>

      {/* Comment list */}
      {isLoading ? (
        <p className="py-2 text-xs text-slate-400">Loading…</p>
      ) : comments.length === 0 ? (
        <p className="mb-3 text-xs text-slate-400 italic">No updates yet. Add the first one below.</p>
      ) : (
        <ul className="mb-4 space-y-3">
          {comments.map((c: ItemComment) => (
            <li key={c.id} className="flex items-start gap-2.5">
              {/* Avatar */}
              <div className={`mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white select-none ${commentAvatarColor(c.user_display_name)}`}>
                {commentInitials(c.user_display_name)}
              </div>

              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-xs font-semibold text-slate-700">{c.user_display_name || 'Unknown'}</span>
                  <span className="flex-shrink-0 text-[10px] text-slate-400" title={c.created_at}>
                    {formatDistanceToNow(new Date(c.created_at), { addSuffix: true })}
                  </span>
                </div>
                <p className="mt-0.5 whitespace-pre-wrap text-xs leading-relaxed text-slate-600">{c.content}</p>
              </div>

              {/* Delete — own comment or Admin */}
              {(c.user_id === user?.id || user?.role === 'Admin') && (
                <button
                  type="button"
                  aria-label="Delete comment"
                  onClick={() => deleteMutation.mutate(c.id)}
                  disabled={deleteMutation.isPending}
                  className="mt-0.5 flex-shrink-0 rounded p-0.5 text-slate-300 opacity-0 transition-all hover:text-red-400 group-hover:opacity-100 focus:opacity-100 focus:outline-none"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                  </svg>
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Add comment form */}
      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <div className={`mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white select-none ${commentAvatarColor(user?.display_name ?? '')}`}>
          {commentInitials(user?.display_name ?? '')}
        </div>
        <div className="flex-1">
          <textarea
            ref={textareaRef}
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Add an update… (Ctrl+Enter to submit)"
            className="w-full resize-none overflow-hidden rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-800 placeholder-slate-400 transition-colors focus:border-primary-400 focus:bg-white focus:outline-none focus:ring-1 focus:ring-primary-400"
          />
        </div>
        <button
          type="submit"
          disabled={!draft.trim() || addMutation.isPending}
          className="flex-shrink-0 rounded-lg bg-primary-800 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-primary-900 focus:outline-none focus:ring-2 focus:ring-primary-500 disabled:opacity-40"
        >
          {addMutation.isPending ? '…' : 'Post'}
        </button>
      </form>
      {addMutation.isError && (
        <p className="mt-1 text-xs text-red-500">Failed to post. Please try again.</p>
      )}
    </div>
  )
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function ActionItemCard({
  item,
  onUpdate,
  onDelete,
  editMode = false,
}: ActionItemCardProps): React.JSX.Element {
  const { user } = useAuth()
  const isViewer = user?.role === 'Viewer'
  const [contextExpanded, setContextExpanded] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [commentsOpen, setCommentsOpen] = useState(false)

  // Local edit state (mirrors item fields while editing)
  const [editTask, setEditTask] = useState(item.task)
  const [editOwner, setEditOwner] = useState(item.owner_label)
  const [editPriority, setEditPriority] = useState<ActionItem['priority']>(item.priority)
  const [editDueDate, setEditDueDate] = useState(item.due_date ?? '')
  const [editDueText, setEditDueText] = useState(item.due_text)
  const [editContext, setEditContext] = useState(item.context)
  const [isEditing, setIsEditing] = useState(editMode)

  function handleStatusChange(e: React.ChangeEvent<HTMLSelectElement>): void {
    onUpdate(item.id, { status: e.target.value as ActionItem['status'] })
  }

  function handleSaveEdit(): void {
    onUpdate(item.id, {
      task: editTask,
      owner_label: editOwner,
      priority: editPriority,
      due_date: editDueDate !== '' ? editDueDate : null,
      due_text: editDueText,
      context: editContext,
    })
    setIsEditing(false)
  }

  function handleCancelEdit(): void {
    setEditTask(item.task)
    setEditOwner(item.owner_label)
    setEditPriority(item.priority)
    setEditDueDate(item.due_date ?? '')
    setEditDueText(item.due_text)
    setEditContext(item.context)
    setIsEditing(false)
  }

  function handleDeleteConfirm(): void {
    if (confirmDelete) {
      onDelete(item.id)
    } else {
      setConfirmDelete(true)
    }
  }

  const formattedDue = formatDueDate(item.due_date)
  const showConfidence = item.confidence < 0.7
  const confidencePct = Math.round(item.confidence * 100)

  return (
    <article
      className={`group relative overflow-hidden rounded-xl border bg-white shadow-sm transition-all hover:shadow-md hover:-translate-y-px ${
        item.needs_review ? 'border-amber-300' : 'border-slate-200'
      }`}
    >
      {/* Priority stripe */}
      <div
        className={`absolute inset-y-0 left-0 w-1 ${PRIORITY_STRIPE[item.priority]}`}
        aria-hidden="true"
      />

      <div className="py-4 pl-5 pr-4">
        {isEditing ? (
          /* ── Edit mode ───────────────────────────────────────────────────── */
          <div className="space-y-3">
            {/* Source info — read-only */}
            {(item.meeting_title || item.project_name) && (
              <div className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 px-3 py-2">
                {item.meeting_title && (
                  <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                      <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clipRule="evenodd" />
                    </svg>
                    <span className="font-medium text-slate-700">{item.meeting_title}</span>
                  </span>
                )}
                {item.project_name && (
                  <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                      <path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
                    </svg>
                    <span className="font-medium text-slate-700">{item.project_name}</span>
                  </span>
                )}
              </div>
            )}

            <div>
              <label htmlFor={`task-${item.id}`} className="mb-1 block text-xs font-medium text-primary-600">
                Task
              </label>
              <textarea
                id={`task-${item.id}`}
                value={editTask}
                onChange={(e) => setEditTask(e.target.value)}
                rows={2}
                maxLength={2000}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-primary-800 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor={`owner-${item.id}`} className="mb-1 block text-xs font-medium text-primary-600">
                  Owner
                </label>
                <input
                  id={`owner-${item.id}`}
                  type="text"
                  value={editOwner}
                  onChange={(e) => setEditOwner(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-primary-800 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>

              <div>
                <label htmlFor={`priority-${item.id}`} className="mb-1 block text-xs font-medium text-primary-600">
                  Priority
                </label>
                <select
                  id={`priority-${item.id}`}
                  value={editPriority}
                  onChange={(e) => setEditPriority(e.target.value as ActionItem['priority'])}
                  className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-primary-800 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                >
                  <option value="High">High</option>
                  <option value="Medium">Medium</option>
                  <option value="Low">Low</option>
                </select>
              </div>

              <div>
                <label htmlFor={`due-${item.id}`} className="mb-1 block text-xs font-medium text-primary-600">
                  Due date
                </label>
                <input
                  id={`due-${item.id}`}
                  type="date"
                  value={editDueDate}
                  onChange={(e) => setEditDueDate(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-primary-800 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>

              <div>
                <label htmlFor={`duetext-${item.id}`} className="mb-1 block text-xs font-medium text-primary-600">
                  Due text
                </label>
                <input
                  id={`duetext-${item.id}`}
                  type="text"
                  value={editDueText}
                  onChange={(e) => setEditDueText(e.target.value)}
                  placeholder="e.g. end of week"
                  className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-primary-800 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>
            </div>

            <div>
              <label htmlFor={`context-${item.id}`} className="mb-1 block text-xs font-medium text-primary-600">
                Context
              </label>
              <textarea
                id={`context-${item.id}`}
                value={editContext}
                onChange={(e) => setEditContext(e.target.value)}
                rows={3}
                maxLength={2000}
                placeholder="Add context from the meeting…"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-primary-800 placeholder-primary-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
            </div>

            <div className="flex gap-2 pt-1">
              <button
                type="button"
                onClick={handleSaveEdit}
                className="rounded-lg bg-primary-700 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-primary-800 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-1"
              >
                Save
              </button>
              <button
                type="button"
                onClick={handleCancelEdit}
                className="rounded-lg border border-slate-300 px-4 py-1.5 text-sm font-medium text-primary-600 transition-colors hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-primary-400 focus:ring-offset-1"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          /* ── View mode ───────────────────────────────────────────────────── */
          <>
            {/* Header row: task text + actions */}
            <div className="flex items-start justify-between gap-3">
              <p className="flex-1 text-sm font-medium leading-snug text-primary-800">
                {item.task}
              </p>

              {!isViewer && (
                <div className="flex flex-shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setIsEditing(true)}
                    aria-label="Edit action item"
                    className="rounded p-1 text-primary-400 transition-colors hover:bg-slate-100 hover:text-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-400"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                      <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
                    </svg>
                  </button>
                  {/* H13: delete confirmation uses explicit Cancel button, not onBlur */}
                  {confirmDelete ? (
                    <>
                      <button
                        type="button"
                        onClick={handleDeleteConfirm}
                        aria-label="Confirm delete"
                        className="rounded px-2 py-1 text-xs font-medium bg-red-100 text-red-600 hover:bg-red-200 transition-colors focus:outline-none focus:ring-2 focus:ring-red-400"
                      >
                        Confirm
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmDelete(false)}
                        aria-label="Cancel delete"
                        className="rounded px-2 py-1 text-xs font-medium border border-slate-300 text-primary-600 hover:bg-slate-100 transition-colors focus:outline-none focus:ring-2 focus:ring-primary-400"
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={handleDeleteConfirm}
                      aria-label="Delete action item"
                      className="rounded p-1 text-primary-400 hover:bg-slate-100 hover:text-red-500 transition-colors focus:outline-none focus:ring-2 focus:ring-red-400"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                        <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd" />
                      </svg>
                    </button>
                  )}
                </div>
              )}
            </div>

            {/* Metadata row */}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {/* Owner badge */}
              <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-primary-700">
                <span className={`flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold text-white ${ownerAvatarColor(item.owner_label)}`}>
                  {initials(item.owner_label)}
                </span>
                {item.owner_label}
              </span>

              <PriorityBadge priority={item.priority} />

              {/* Inline status selector — hidden for Viewers */}
              {isViewer ? (
                <StatusBadge status={item.status} />
              ) : (
                <select
                  value={item.status}
                  onChange={handleStatusChange}
                  aria-label="Update status"
                  className="rounded-full border-0 bg-transparent py-0.5 pl-1 pr-6 text-xs font-medium text-primary-600 ring-1 ring-inset ring-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-400"
                >
                  <option value="Open">Open</option>
                  <option value="In progress">In progress</option>
                  <option value="Done">Done</option>
                </select>
              )}

              {/* Due date */}
              {(formattedDue !== '' || item.due_text !== '') && (
                <span className="inline-flex items-center gap-1 text-xs text-primary-500">
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path fillRule="evenodd" d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z" clipRule="evenodd" />
                  </svg>
                  {formattedDue !== '' ? formattedDue : item.due_text}
                </span>
              )}

              {/* Confidence indicator (only when below threshold) */}
              {showConfidence && (
                <span className="inline-flex items-center gap-1 rounded-full bg-orange-50 px-2 py-0.5 text-xs font-medium text-orange-600 ring-1 ring-inset ring-orange-200">
                  {confidencePct}% confidence
                </span>
              )}
            </div>

            {/* Source badges — meeting title and project name */}
            {(item.meeting_title || item.project_name) && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {item.meeting_title && (
                  <span className="inline-flex items-center gap-1 rounded-md bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700 ring-1 ring-inset ring-indigo-100">
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                      <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clipRule="evenodd" />
                    </svg>
                    {item.meeting_title}
                  </span>
                )}
                {item.project_name && (
                  <span className="inline-flex items-center gap-1 rounded-md bg-violet-50 px-2 py-0.5 text-xs text-violet-700 ring-1 ring-inset ring-violet-100">
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                      <path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
                    </svg>
                    {item.project_name}
                  </span>
                )}
              </div>
            )}

            {/* Reviewed checkbox — unchecked by default; disabled for Viewers */}
            <label className={`mt-2 inline-flex items-center gap-2 ${isViewer ? 'cursor-default' : 'cursor-pointer'}`}>
              <input
                type="checkbox"
                checked={!item.needs_review}
                disabled={isViewer}
                onChange={() => onUpdate(item.id, { needs_review: !item.needs_review })}
                className="h-4 w-4 rounded border-2 border-slate-300 transition-colors focus:ring-2 focus:ring-emerald-400 focus:ring-offset-1 disabled:cursor-default"
                style={{ accentColor: '#10b981' }}
              />
              <span className="text-xs font-medium text-slate-600">
                Reviewed
                {!item.needs_review && item.reviewed_by_name && (
                  <span className="ml-1 font-normal text-emerald-600">
                    by <span className="font-semibold">{item.reviewed_by_name}</span>
                    {item.reviewed_at && (
                      <span className="ml-1 text-emerald-500">
                        · {new Date(item.reviewed_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                      </span>
                    )}
                  </span>
                )}
              </span>
            </label>

            {/* Context (expandable) */}
            {item.context !== '' && (
              <div className="mt-3">
                <button
                  type="button"
                  onClick={() => setContextExpanded((prev) => !prev)}
                  className="flex items-center gap-1 text-xs text-primary-400 transition-colors hover:text-primary-600 focus:outline-none"
                  aria-expanded={contextExpanded}
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    className={`h-3.5 w-3.5 transition-transform ${contextExpanded ? 'rotate-90' : ''}`}
                    viewBox="0 0 20 20"
                    fill="currentColor"
                    aria-hidden="true"
                  >
                    <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" />
                  </svg>
                  {contextExpanded ? 'Hide context' : 'Show context'}
                </button>

                {contextExpanded && (
                  <p className="mt-2 rounded-lg border border-slate-100 bg-gradient-to-br from-slate-50 to-white px-3 py-2.5 text-xs leading-relaxed text-primary-600 italic">
                    {item.context}
                  </p>
                )}
              </div>
            )}

            {/* Comments toggle */}
            <div className="mt-3">
              <button
                type="button"
                onClick={() => setCommentsOpen((prev) => !prev)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-500 transition-colors hover:border-primary-300 hover:bg-primary-50 hover:text-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-400"
                aria-expanded={commentsOpen}
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path fillRule="evenodd" d="M18 10c0 3.866-3.582 7-8 7a8.841 8.841 0 01-4.083-.98L2 17l1.338-3.123C2.493 12.767 2 11.434 2 10c0-3.866 3.582-7 8-7s8 3.134 8 7zM7 9H5v2h2V9zm8 0h-2v2h2V9zM9 9h2v2H9V9z" clipRule="evenodd" />
                </svg>
                {commentsOpen ? 'Hide updates' : 'Updates'}
              </button>
            </div>
          </>
        )}
      </div>

      {/* Comments thread — lazy-loaded when toggled open */}
      {commentsOpen && !isEditing && <CommentsThread itemId={item.id} />}
    </article>
  )
}
