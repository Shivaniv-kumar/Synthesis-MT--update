import React from 'react'
import { Link } from 'react-router-dom'
import type { ActionItem, UpdateItemRequest } from '@/types'

// ── Column definitions ────────────────────────────────────────────────────────

interface Column {
  id: ActionItem['status'] | 'review'
  label: string
  accent: string      // top-border + count badge color classes
  headerBg: string
  emptyText: string
}

const COLUMNS: Column[] = [
  {
    id: 'review',
    label: 'Needs Review',
    accent: 'border-amber-400',
    headerBg: 'bg-amber-50',
    emptyText: 'No items need review',
  },
  {
    id: 'Open',
    label: 'Open',
    accent: 'border-slate-400',
    headerBg: 'bg-slate-50',
    emptyText: 'All caught up!',
  },
  {
    id: 'In progress',
    label: 'In Progress',
    accent: 'border-blue-500',
    headerBg: 'bg-blue-50',
    emptyText: 'Nothing in progress',
  },
  {
    id: 'Done',
    label: 'Done',
    accent: 'border-emerald-500',
    headerBg: 'bg-emerald-50',
    emptyText: 'Nothing completed yet',
  },
]

// ── Priority config ───────────────────────────────────────────────────────────

const PRIORITY_CONFIG: Record<ActionItem['priority'], { border: string; icon: React.JSX.Element; label: string }> = {
  High: {
    border: 'border-l-red-500',
    label: 'High',
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-red-500" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
        <path fillRule="evenodd" d="M12.395 2.553a1 1 0 00-1.45-.385c-.345.23-.614.558-.822.88-.214.33-.403.713-.57 1.116-.334.804-.614 1.768-.84 2.734a31.365 31.365 0 00-.613 3.58 2.64 2.64 0 01-.945-1.067c-.328-.68-.398-1.534-.398-2.654A1 1 0 005.05 6.05 6.981 6.981 0 003 11a7 7 0 1011.95-4.95c-.592-.591-.98-.985-1.348-1.467-.363-.476-.724-1.063-1.207-2.03zM12.12 15.12A3 3 0 017 13s.879.5 2.5.5c0-1 .5-4 1.25-4.5.5 1 .786 1.293 1.371 1.879A2.99 2.99 0 0113 13a2.99 2.99 0 01-.879 2.121z" clipRule="evenodd" />
      </svg>
    ),
  },
  Medium: {
    border: 'border-l-amber-400',
    label: 'Medium',
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-amber-500" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
        <path fillRule="evenodd" d="M14.707 12.707a1 1 0 01-1.414 0L10 9.414l-3.293 3.293a1 1 0 01-1.414-1.414l4-4a1 1 0 011.414 0l4 4a1 1 0 010 1.414z" clipRule="evenodd" />
      </svg>
    ),
  },
  Low: {
    border: 'border-l-slate-300',
    label: 'Low',
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-slate-400" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
        <path fillRule="evenodd" d="M3 10a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" clipRule="evenodd" />
      </svg>
    ),
  },
}

// ── Owner avatar ──────────────────────────────────────────────────────────────

const AVATAR_COLORS = [
  'bg-violet-500', 'bg-blue-500', 'bg-emerald-500',
  'bg-rose-500', 'bg-amber-500', 'bg-cyan-500', 'bg-pink-500',
]

function ownerColor(label: string): string {
  let hash = 0
  for (let i = 0; i < label.length; i++) hash = (hash * 31 + label.charCodeAt(i)) & 0xffff
  return AVATAR_COLORS[hash % AVATAR_COLORS.length]
}

function ownerInitials(label: string): string {
  return label.trim().split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? '').join('')
}

// ── Kanban card ───────────────────────────────────────────────────────────────

interface KanbanCardProps {
  item: ActionItem
  onStatusChange: (id: string, status: ActionItem['status']) => void
}

function KanbanCard({ item, onStatusChange }: KanbanCardProps): React.JSX.Element {
  const pri = PRIORITY_CONFIG[item.priority]
  const confidence = Math.round(item.confidence * 100)

  const formattedDate = item.due_date
    ? new Date(item.due_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : item.due_text || null

  const isOverdue =
    item.due_date != null &&
    item.status !== 'Done' &&
    new Date(item.due_date) < new Date()

  return (
    <div
      className={`group relative rounded-xl border border-slate-200 bg-white shadow-sm transition-shadow hover:shadow-md border-l-4 ${pri.border} flex flex-col gap-2 p-3`}
    >
      {/* Owner label (parent category placeholder) */}
      {item.owner_label && (
        <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
          {item.owner_label}
        </p>
      )}

      {/* Task title */}
      <p className="text-sm font-semibold leading-snug text-slate-800 line-clamp-2">
        {item.task}
      </p>

      {/* Confidence bar */}
      <div className="flex items-center gap-2">
        <div className="h-1 flex-1 rounded-full bg-slate-100">
          <div
            className="h-1 rounded-full bg-primary-600 transition-all"
            style={{ width: `${confidence}%` }}
          />
        </div>
        <span className="text-[10px] tabular-nums text-slate-400">{confidence}%</span>
      </div>

      {/* Footer row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          {formattedDate && (
            <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${isOverdue ? 'bg-red-50 text-red-600' : 'bg-slate-100 text-slate-500'}`}>
              <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path fillRule="evenodd" d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z" clipRule="evenodd" />
              </svg>
              {formattedDate}
            </span>
          )}
          <span className="inline-flex items-center gap-0.5" title={`${pri.label} priority`}>
            {pri.icon}
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          {/* Quick status advance */}
          {item.status !== 'Done' && (
            <button
              type="button"
              title={item.status === 'Open' ? 'Start' : 'Complete'}
              onClick={() => onStatusChange(item.id, item.status === 'Open' ? 'In progress' : 'Done')}
              className="hidden rounded p-0.5 text-slate-300 transition-colors hover:text-primary-600 group-hover:flex focus:outline-none"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path fillRule="evenodd" d="M10.293 3.293a1 1 0 011.414 0l6 6a1 1 0 010 1.414l-6 6a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-4.293-4.293a1 1 0 010-1.414z" clipRule="evenodd" />
              </svg>
            </button>
          )}

          {/* Owner avatar */}
          <div
            title={item.owner_label}
            className={`flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white select-none ${ownerColor(item.owner_label)}`}
          >
            {ownerInitials(item.owner_label) || '?'}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Column ────────────────────────────────────────────────────────────────────

interface ColumnProps {
  col: Column
  items: ActionItem[]
  onStatusChange: (id: string, status: ActionItem['status']) => void
}

function KanbanColumn({ col, items, onStatusChange }: ColumnProps): React.JSX.Element {
  return (
    <div className="flex min-w-[260px] max-w-[310px] flex-1 flex-col gap-3">
      {/* Column header */}
      <div className={`flex items-center justify-between rounded-xl border border-slate-200 px-3 py-2.5 ${col.headerBg}`}>
        <div className="flex items-center gap-2">
          <div className={`h-2.5 w-2.5 rounded-full border-2 ${col.accent.replace('border-', 'border-')}`} />
          <h3 className="text-sm font-semibold text-slate-700">{col.label}</h3>
          <span className="rounded-full bg-white/70 px-2 py-0.5 text-xs font-bold text-slate-600 ring-1 ring-inset ring-slate-200">
            {items.length}
          </span>
        </div>
        <button
          type="button"
          className="rounded p-0.5 text-slate-400 hover:text-slate-600 focus:outline-none"
          aria-label={`${col.label} column options`}
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM16 12a2 2 0 100-4 2 2 0 000 4z" />
          </svg>
        </button>
      </div>

      {/* Cards */}
      <div className={`flex flex-col gap-2.5 rounded-xl border border-dashed p-2.5 min-h-[120px] ${col.accent.replace('border-', 'border-')}/30 bg-slate-50/50`}>
        {items.length === 0 ? (
          <p className="py-4 text-center text-xs text-slate-400">{col.emptyText}</p>
        ) : (
          items.map((item) => (
            <KanbanCard
              key={item.id}
              item={item}
              onStatusChange={onStatusChange}
            />
          ))
        )}

        {/* Add a Task footer */}
        <Link
          to="/capture"
          className="mt-1 flex items-center justify-center gap-2 rounded-lg py-2 text-xs font-medium text-slate-400 transition-colors hover:bg-white hover:text-slate-600"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clipRule="evenodd" />
          </svg>
          Add a Task
        </Link>
      </div>
    </div>
  )
}

// ── Board ─────────────────────────────────────────────────────────────────────

interface KanbanBoardProps {
  items: ActionItem[]
  onUpdate: (id: string, data: UpdateItemRequest) => void
}

export default function KanbanBoard({ items, onUpdate }: KanbanBoardProps): React.JSX.Element {
  function handleStatusChange(id: string, status: ActionItem['status']): void {
    onUpdate(id, { status })
  }

  function columnItems(col: Column): ActionItem[] {
    if (col.id === 'review') return items.filter((i) => i.needs_review)
    return items.filter((i) => i.status === col.id && !i.needs_review)
  }

  return (
    <div className="flex gap-4 overflow-x-auto pb-4">
      {COLUMNS.map((col) => (
        <KanbanColumn
          key={col.id}
          col={col}
          items={columnItems(col)}
          onStatusChange={handleStatusChange}
        />
      ))}
    </div>
  )
}
