import React, { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  listKnowledge,
  updateKnowledgeEntry,
  deleteKnowledgeEntry,
} from '@/api/knowledge'
import { getProjects } from '@/api/projects'
import type { KnowledgeEntry, KnowledgeCategory } from '@/types'

// ── Category metadata ─────────────────────────────────────────────────────────

interface CategoryConfig {
  label: string
  badgeBg: string
  badgeText: string
  borderColor: string
  description: string
}

const CATEGORY_CONFIG: Record<KnowledgeCategory, CategoryConfig> = {
  dependency: {
    label: 'Dependency',
    badgeBg: 'bg-indigo-100',
    badgeText: 'text-indigo-800',
    borderColor: 'border-l-indigo-500',
    description: 'External reliance outside the team\'s control',
  },
  constraint: {
    label: 'Constraint',
    badgeBg: 'bg-slate-100',
    badgeText: 'text-slate-700',
    borderColor: 'border-l-slate-500',
    description: 'Hard boundary — budget, regulatory, or technical lock-in',
  },
  decision: {
    label: 'Decision',
    badgeBg: 'bg-blue-100',
    badgeText: 'text-blue-800',
    borderColor: 'border-l-blue-500',
    description: 'A definitive choice or agreement made by participants',
  },
  blocker: {
    label: 'Blocker',
    badgeBg: 'bg-red-100',
    badgeText: 'text-red-800',
    borderColor: 'border-l-red-500',
    description: 'Something actively preventing progress right now',
  },
  risk: {
    label: 'Risk',
    badgeBg: 'bg-rose-100',
    badgeText: 'text-rose-800',
    borderColor: 'border-l-rose-400',
    description: 'A potential problem or uncertainty that could impact the project',
  },
  tradeoff: {
    label: 'Trade-off',
    badgeBg: 'bg-orange-100',
    badgeText: 'text-orange-800',
    borderColor: 'border-l-orange-500',
    description: 'What was gained vs. what was given up in a choice',
  },
  principle: {
    label: 'Principle',
    badgeBg: 'bg-violet-100',
    badgeText: 'text-violet-800',
    borderColor: 'border-l-violet-500',
    description: 'A guiding philosophy the team agreed to apply broadly',
  },
  assumption: {
    label: 'Context',
    badgeBg: 'bg-amber-100',
    badgeText: 'text-amber-800',
    borderColor: 'border-l-amber-500',
    description: 'Background information, context facts, or something treated as true without explicit confirmation',
  },
  open_question: {
    label: 'Open Question',
    badgeBg: 'bg-cyan-100',
    badgeText: 'text-cyan-800',
    borderColor: 'border-l-cyan-500',
    description: 'Unresolved question that needs a follow-up',
  },
}

const ALL_CATEGORIES = Object.keys(CATEGORY_CONFIG) as KnowledgeCategory[]

// Categories shown in filters and grouping — excludes 'assumption' (legacy 'context' rows
// are migrated to assumption but no longer exposed as a selectable classification)
const FILTER_CATEGORIES = ALL_CATEGORIES.filter((c) => c !== 'assumption')

// ── Edit draft state ──────────────────────────────────────────────────────────

interface EditDraft {
  category: KnowledgeCategory
  content: string
  source_quote: string
  latest_update: string
  is_closed: boolean
}

// ── Category badge ────────────────────────────────────────────────────────────

function CategoryBadge({ category, resolved }: { category: KnowledgeCategory; resolved?: boolean }): React.JSX.Element {
  if (resolved) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-500">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
        </svg>
        Resolved
      </span>
    )
  }
  const cfg = CATEGORY_CONFIG[category] ?? CATEGORY_CONFIG.assumption
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${cfg.badgeBg} ${cfg.badgeText}`}
      title={cfg.description}
    >
      {cfg.label}
    </span>
  )
}

// ── Entry card ────────────────────────────────────────────────────────────────

interface EntryCardProps {
  entry: KnowledgeEntry
  isEditing: boolean
  draft: EditDraft
  onEditStart: () => void
  onDraftChange: (d: EditDraft) => void
  onSave: () => void
  onCancel: () => void
  onDelete: () => void
  isSaving: boolean
  saveError: string | null
}

function EntryCard({
  entry,
  isEditing,
  draft,
  onEditStart,
  onDraftChange,
  onSave,
  onCancel,
  onDelete,
  isSaving,
  saveError,
}: EntryCardProps): React.JSX.Element {
  const cfg = CATEGORY_CONFIG[entry.category as KnowledgeCategory] ?? {
    label: entry.category,
    badgeBg: 'bg-slate-100',
    badgeText: 'text-slate-700',
    borderColor: 'border-l-slate-400',
    description: '',
  }
  const isClosed = entry.is_closed && entry.category === 'open_question'
  const borderColor = isClosed ? 'border-l-slate-300' : cfg.borderColor

  return (
    <div
      className={`rounded-lg border border-slate-200 border-l-4 ${borderColor} bg-white p-4 shadow-sm transition-shadow hover:shadow-md${isClosed ? ' opacity-60 grayscale' : ''}`}
    >
      {isEditing ? (
        // ── Edit mode ─────────────────────────────────────────────────────
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">Category</label>
            <select
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary-400"
              value={draft.category}
              onChange={(e) =>
                onDraftChange({ ...draft, category: e.target.value as KnowledgeCategory })
              }
            >
              {FILTER_CATEGORIES.map((cat) => (
                <option key={cat} value={cat}>
                  {CATEGORY_CONFIG[cat].label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">Content</label>
            <textarea
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-400"
              rows={3}
              value={draft.content}
              onChange={(e) => onDraftChange({ ...draft, content: e.target.value })}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              Source quote <span className="text-slate-400">(optional)</span>
            </label>
            <textarea
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-400"
              rows={2}
              value={draft.source_quote}
              placeholder="Direct quote from the transcript…"
              onChange={(e) => onDraftChange({ ...draft, source_quote: e.target.value })}
            />
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="text-xs font-medium text-slate-600">
                Latest update / comment <span className="text-slate-400">(optional)</span>
              </label>
              {draft.category === 'open_question' && (
                <label className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-slate-600 select-none">
                  <input
                    type="checkbox"
                    checked={draft.is_closed}
                    onChange={(e) => onDraftChange({ ...draft, is_closed: e.target.checked })}
                    className="h-3.5 w-3.5 rounded border-slate-300 text-primary-600 focus:ring-primary-400"
                  />
                  Mark as resolved
                </label>
              )}
            </div>
            <textarea
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-400"
              rows={2}
              value={draft.latest_update}
              placeholder="Add a note, status update, or follow-up comment…"
              onChange={(e) => onDraftChange({ ...draft, latest_update: e.target.value })}
            />
          </div>
          {saveError && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{saveError}</p>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onSave}
              disabled={isSaving || !draft.content.trim()}
              className="rounded-md bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50"
            >
              {isSaving ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        // ── Read mode ─────────────────────────────────────────────────────
        <div>
          <div className="mb-2 flex items-start justify-between gap-2">
            <CategoryBadge
              category={entry.category as KnowledgeCategory}
              resolved={isClosed}
            />
            <div className="flex shrink-0 gap-1">
              <button
                type="button"
                onClick={onEditStart}
                className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                title="Edit"
                aria-label="Edit entry"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
                </svg>
              </button>
              <button
                type="button"
                onClick={onDelete}
                className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-500"
                title="Delete"
                aria-label="Delete entry"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd" />
                </svg>
              </button>
            </div>
          </div>

          <p className="text-sm text-slate-800">{entry.content}</p>

          {entry.source_quote && (
            <blockquote className="mt-2 border-l-2 border-slate-300 pl-3 text-xs italic text-slate-500">
              "{entry.source_quote}"
            </blockquote>
          )}

          {entry.latest_update && (
            <div className="mt-3 rounded-md bg-slate-50 px-3 py-2">
              <p className="mb-0.5 flex items-center gap-1 text-xs font-medium text-slate-500">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path fillRule="evenodd" d="M18 10c0 3.866-3.582 7-8 7a8.841 8.841 0 01-4.083-.98L2 17l1.338-3.123C2.493 12.767 2 11.434 2 10c0-3.866 3.582-7 8-7s8 3.134 8 7zM7 9H5v2h2V9zm8 0h-2v2h2V9zM9 9h2v2H9V9z" clipRule="evenodd" />
                </svg>
                {isClosed ? 'Resolution note' : 'Latest update'}
              </p>
              <p className="text-xs text-slate-700">{entry.latest_update}</p>
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
            {entry.meeting_title && (
              <span>
                From:{' '}
                <Link
                  to={`/meeting/${entry.meeting_id}/status`}
                  className="text-primary-600 hover:underline"
                >
                  {entry.meeting_title}
                </Link>
              </span>
            )}
            {entry.edited_at && (
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
                Edited
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Group section ─────────────────────────────────────────────────────────────

function GroupSection({
  title,
  count,
  children,
}: {
  title: string
  count: number
  children: React.ReactNode
}): React.JSX.Element {
  const [open, setOpen] = useState(true)

  return (
    <section className="mb-6">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mb-3 flex w-full items-center gap-2 text-left"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className={`h-4 w-4 text-slate-400 transition-transform ${open ? 'rotate-90' : ''}`}
          viewBox="0 0 20 20"
          fill="currentColor"
          aria-hidden="true"
        >
          <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" />
        </svg>
        <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
          {count}
        </span>
      </button>
      {open && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {children}
        </div>
      )}
    </section>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

type ViewBy = 'project' | 'category'

export default function KnowledgeBasePage(): React.JSX.Element {
  const qc = useQueryClient()

  // ── Filter / view state ─────────────────────────────────────────────────
  const [viewBy, setViewBy] = useState<ViewBy>('category')
  const [filterProject, setFilterProject] = useState<string>('')
  const [filterCategories, setFilterCategories] = useState<KnowledgeCategory[]>([])
  const [searchText, setSearchText] = useState('')
  const [hideResolved, setHideResolved] = useState(false)

  // ── Edit state ──────────────────────────────────────────────────────────
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState<EditDraft>({
    category: 'decision',
    content: '',
    source_quote: '',
    latest_update: '',
    is_closed: false,
  })
  const [saveError, setSaveError] = useState<string | null>(null)

  // ── Data fetching ───────────────────────────────────────────────────────
  const filters = {
    ...(filterProject ? { project_id: filterProject } : {}),
    ...(filterCategories.length ? { category: filterCategories } : {}),
    ...(searchText.trim() ? { search: searchText.trim() } : {}),
    page_size: 200,
  }

  const { data, isLoading, isError } = useQuery({
    queryKey: ['knowledge', filters],
    queryFn: () => listKnowledge(filters),
  })

  const { data: projectsData } = useQuery({
    queryKey: ['projects'],
    queryFn: () => getProjects(),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: EditDraft }) =>
      updateKnowledgeEntry(id, {
        category: patch.category,
        content: patch.content,
        source_quote: patch.source_quote.trim() || null,
        latest_update: patch.latest_update.trim() || null,
        is_closed: patch.is_closed,
      }),
    onSuccess: () => {
      setSaveError(null)
      void qc.invalidateQueries({ queryKey: ['knowledge'] })
      setEditingId(null)
    },
    onError: () => {
      setSaveError('Failed to save. Please try again.')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteKnowledgeEntry,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['knowledge'] }),
  })

  // ── Grouping logic ──────────────────────────────────────────────────────
  const allEntries = data?.items ?? []

  const entries = useMemo(() => {
    return allEntries.filter((e) => {
      if (e.category === 'assumption') return false
      if (hideResolved && e.category === 'open_question' && e.is_closed) return false
      return true
    })
  }, [allEntries, hideResolved])

  const grouped = useMemo(() => {
    const map = new Map<string, KnowledgeEntry[]>()
    for (const e of entries) {
      const key =
        viewBy === 'project'
          ? (e.project_name ?? 'Unassigned')
          : (CATEGORY_CONFIG[e.category as KnowledgeCategory] ?? { label: e.category }).label
      const list = map.get(key) ?? []
      list.push(e)
      map.set(key, list)
    }
    if (viewBy === 'category') {
      const order = FILTER_CATEGORIES.map((c) => CATEGORY_CONFIG[c].label)
      return new Map(
        [...map.entries()].sort(
          (a, b) => order.indexOf(a[0]) - order.indexOf(b[0]),
        ),
      )
    }
    return new Map([...map.entries()].sort((a, b) => a[0].localeCompare(b[0])))
  }, [entries, viewBy])

  // ── Handlers ────────────────────────────────────────────────────────────
  function handleEditStart(entry: KnowledgeEntry): void {
    setSaveError(null)
    setEditingId(entry.id)
    const normalizedCategory: KnowledgeCategory =
      entry.category in CATEGORY_CONFIG
        ? (entry.category as KnowledgeCategory)
        : 'assumption'
    setEditDraft({
      category: normalizedCategory,
      content: entry.content,
      source_quote: entry.source_quote ?? '',
      latest_update: entry.latest_update ?? '',
      is_closed: entry.is_closed,
    })
  }

  function handleSave(): void {
    if (!editingId) return
    setSaveError(null)
    updateMutation.mutate({ id: editingId, patch: editDraft })
  }

  function handleCancel(): void {
    setSaveError(null)
    setEditingId(null)
  }

  function handleCategoryToggle(cat: KnowledgeCategory): void {
    setFilterCategories((prev) =>
      prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat],
    )
  }

  // count resolved open questions for the toggle label
  const resolvedCount = allEntries.filter(
    (e) => e.category === 'open_question' && e.is_closed,
  ).length

  // ── Render ──────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-slate-900">Knowledge Base</h1>
        <p className="mt-1 text-sm text-slate-500">
          Structured knowledge extracted from your meeting transcripts — decisions, dependencies,
          blockers, trade-offs, and more.
        </p>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
        {/* Search */}
        <div className="relative flex-1 min-w-[160px]">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="absolute left-2.5 top-2 h-4 w-4 text-slate-400"
            viewBox="0 0 20 20"
            fill="currentColor"
            aria-hidden="true"
          >
            <path fillRule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clipRule="evenodd" />
          </svg>
          <input
            type="search"
            placeholder="Search knowledge…"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="w-full rounded-md border border-slate-300 py-1.5 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-400"
          />
        </div>

        {/* Project filter */}
        <select
          value={filterProject}
          onChange={(e) => setFilterProject(e.target.value)}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary-400"
        >
          <option value="">All projects</option>
          {(projectsData ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>

        {/* Category filter pills */}
        <div className="flex flex-wrap gap-1.5">
          {FILTER_CATEGORIES.map((cat) => {
            const cfg = CATEGORY_CONFIG[cat]
            const active = filterCategories.includes(cat)
            return (
              <button
                key={cat}
                type="button"
                onClick={() => handleCategoryToggle(cat)}
                title={cfg.description}
                className={`rounded-full px-2.5 py-0.5 text-xs font-medium transition-opacity ${cfg.badgeBg} ${cfg.badgeText} ${active ? 'ring-2 ring-offset-1 ring-primary-400' : 'opacity-60 hover:opacity-100'}`}
              >
                {cfg.label}
              </button>
            )
          })}
        </div>

        {/* Hide resolved toggle — only shown when there are resolved questions */}
        {resolvedCount > 0 && (
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-slate-600 select-none">
            <input
              type="checkbox"
              checked={hideResolved}
              onChange={(e) => setHideResolved(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-slate-300 text-primary-600 focus:ring-primary-400"
            />
            Hide resolved ({resolvedCount})
          </label>
        )}

        {/* View by toggle */}
        <div className="ml-auto flex items-center gap-1 rounded-lg border border-slate-200 p-0.5">
          <button
            type="button"
            onClick={() => setViewBy('category')}
            className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${viewBy === 'category' ? 'bg-primary-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
          >
            By Category
          </button>
          <button
            type="button"
            onClick={() => setViewBy('project')}
            className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${viewBy === 'project' ? 'bg-primary-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
          >
            By Project
          </button>
        </div>
      </div>

      {/* Content */}
      {isLoading && (
        <div className="flex items-center justify-center py-16 text-sm text-slate-500">
          Loading knowledge base…
        </div>
      )}

      {isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Failed to load knowledge entries. Please refresh and try again.
        </div>
      )}

      {!isLoading && !isError && allEntries.length === 0 && (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white py-16 text-center">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="mb-3 h-10 w-10 text-slate-300"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <p className="font-medium text-slate-500">No knowledge entries found</p>
          <p className="mt-1 text-sm text-slate-400">
            Extract a transcript to automatically populate the knowledge base.
          </p>
        </div>
      )}

      {!isLoading && !isError && allEntries.length > 0 && (
        <div>
          {[...grouped.entries()].map(([groupKey, groupEntries]) => (
            <GroupSection key={groupKey} title={groupKey} count={groupEntries.length}>
              {groupEntries.map((entry) => (
                <EntryCard
                  key={entry.id}
                  entry={entry}
                  isEditing={editingId === entry.id}
                  draft={editDraft}
                  onEditStart={() => handleEditStart(entry)}
                  onDraftChange={setEditDraft}
                  onSave={handleSave}
                  onCancel={handleCancel}
                  onDelete={() => {
                    if (confirm('Delete this knowledge entry?')) {
                      deleteMutation.mutate(entry.id)
                    }
                  }}
                  isSaving={updateMutation.isPending && editingId === entry.id}
                  saveError={editingId === entry.id ? saveError : null}
                />
              ))}
            </GroupSection>
          ))}
          <p className="text-right text-xs text-slate-400">
            {data?.total ?? 0} total entries
          </p>
        </div>
      )}
    </div>
  )
}
