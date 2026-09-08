import React, { useState, useMemo, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/contexts/AuthContext'
import { getItems, updateItem, deleteItem } from '@/api/items'
import { getProjects } from '@/api/projects'
import ActionItemCard from '@/components/ActionItemCard'
import KanbanBoard from '@/components/KanbanBoard'
import EmptyState from '@/components/EmptyState'
import LoadingSpinner from '@/components/LoadingSpinner'
import type { ActionItem, ItemFilters, UpdateItemRequest } from '@/types'

// ── Filter state ───────────────────────────────────────────────────────────────

type ViewMode = 'list' | 'board'

interface Filters {
  status: ActionItem['status'] | 'all'
  priority: ActionItem['priority'] | 'all'
  needs_review: boolean
  search: string
  project_id: string | 'all'
}

const DEFAULT_FILTERS: Filters = {
  status: 'all',
  priority: 'all',
  needs_review: false,
  search: '',
  project_id: 'all',
}

// ── View toggle button ─────────────────────────────────────────────────────────

interface ViewBtnProps {
  active: boolean
  onClick: () => void
  title: string
  children: React.ReactNode
}

function ViewBtn({ active, onClick, title, children }: ViewBtnProps): React.JSX.Element {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={`inline-flex items-center justify-center rounded-lg p-2 transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-1 ${
        active
          ? 'bg-primary-800 text-white shadow-sm'
          : 'text-primary-400 hover:bg-primary-800/60 hover:text-white'
      }`}
    >
      {children}
    </button>
  )
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function TrackerPage(): React.JSX.Element {
  const { user } = useAuth()
  const isViewer = user?.role === 'Viewer'
  const queryClient = useQueryClient()
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const [page, setPage] = useState(1)
  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [filtersOpen, setFiltersOpen] = useState(true)

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => getProjects(),
    staleTime: 60_000,
  })

  // List view query (paginated)
  const listApiFilters = useMemo((): ItemFilters => {
    const f: ItemFilters = { page, page_size: 20 }
    if (filters.status !== 'all') f.status = filters.status
    if (filters.priority !== 'all') f.priority = filters.priority
    if (filters.needs_review) f.needs_review = true
    if (filters.search.trim() !== '') f.search = filters.search.trim()
    if (filters.project_id !== 'all') f.project_id = filters.project_id
    return f
  }, [filters, page])

  // Board view query (all items, no pagination)
  const boardApiFilters = useMemo((): ItemFilters => {
    const f: ItemFilters = { page: 1, page_size: 200 }
    if (filters.search.trim() !== '') f.search = filters.search.trim()
    if (filters.priority !== 'all') f.priority = filters.priority
    if (filters.project_id !== 'all') f.project_id = filters.project_id
    return f
  }, [filters])

  const { data: listData, isLoading: listLoading, isError: listError } = useQuery({
    queryKey: ['items', listApiFilters],
    queryFn: () => getItems(listApiFilters),
    staleTime: 30_000,
    enabled: viewMode === 'list',
  })

  const { data: boardData, isLoading: boardLoading, isError: boardError } = useQuery({
    queryKey: ['items-board', boardApiFilters],
    queryFn: () => getItems(boardApiFilters),
    staleTime: 30_000,
    enabled: viewMode === 'board',
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateItemRequest }) =>
      updateItem(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['items'] })
      queryClient.invalidateQueries({ queryKey: ['items-board'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteItem(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['items'] })
      queryClient.invalidateQueries({ queryKey: ['items-board'] })
    },
  })

  const handleUpdate = useCallback(
    (id: string, data: UpdateItemRequest) => updateMutation.mutate({ id, data }),
    [updateMutation],
  )

  const handleDelete = useCallback(
    (id: string) => deleteMutation.mutate(id),
    [deleteMutation],
  )

  function handleFilterChange<K extends keyof Filters>(key: K, value: Filters[K]): void {
    setFilters((prev) => ({ ...prev, [key]: value }))
    setPage(1)
  }

  function clearFilters(): void {
    setFilters(DEFAULT_FILTERS)
    setPage(1)
  }

  const isFiltered =
    filters.status !== 'all' ||
    filters.priority !== 'all' ||
    filters.needs_review ||
    filters.search !== '' ||
    filters.project_id !== 'all'

  const listItems: ActionItem[] = listData?.items ?? []
  const boardItems: ActionItem[] = boardData?.items ?? []
  const total = listData?.total ?? boardData?.total ?? 0
  const hasNext = listData?.has_next ?? false
  const hasPrev = listData?.has_prev ?? false

  const isLoading = viewMode === 'list' ? listLoading : boardLoading
  const isError = viewMode === 'list' ? listError : boardError

  return (
    <div className="flex flex-col gap-0 min-h-full bg-slate-50">
      {/* ── Top toolbar ─────────────────────────────────────────────────── */}
      <div className="sticky top-14 z-10 flex items-center justify-between gap-4 border-b border-slate-200 bg-white px-6 py-3 shadow-sm">
        {/* Left: title + count */}
        <div className="flex items-center gap-3">
          <h1 className="text-base font-bold text-slate-900">Tracker</h1>
          {total > 0 && (
            <span className="rounded-full bg-primary-800 px-2.5 py-0.5 text-xs font-bold text-white">
              {total}
            </span>
          )}
        </div>

        {/* Center: view toggle */}
        <div className="flex items-center gap-1 rounded-xl bg-primary-900 p-1">
          {/* List view */}
          <ViewBtn active={viewMode === 'list'} onClick={() => setViewMode('list')} title="List view">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M3 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" clipRule="evenodd" />
            </svg>
          </ViewBtn>

          {/* Board view */}
          <ViewBtn active={viewMode === 'board'} onClick={() => setViewMode('board')} title="Board view">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path d="M2 4a1 1 0 011-1h4a1 1 0 011 1v12a1 1 0 01-1 1H3a1 1 0 01-1-1V4zM8 4a1 1 0 011-1h4a1 1 0 011 1v6a1 1 0 01-1 1H9a1 1 0 01-1-1V4zM15 3a1 1 0 00-1 1v4a1 1 0 001 1h2a1 1 0 001-1V4a1 1 0 00-1-1h-2z" />
            </svg>
          </ViewBtn>

          {/* Filter toggle */}
          <ViewBtn
            active={filtersOpen}
            onClick={() => setFiltersOpen((v) => !v)}
            title={filtersOpen ? 'Hide filters' : 'Show filters'}
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M3 3a1 1 0 011-1h12a1 1 0 011 1v3a1 1 0 01-.293.707L12 11.414V15a1 1 0 01-.293.707l-2 2A1 1 0 018 17v-5.586L3.293 6.707A1 1 0 013 6V3z" clipRule="evenodd" />
            </svg>
          </ViewBtn>
        </div>

        {/* Right: capture button — hidden for Viewers */}
        {!isViewer && (
          <Link
            to="/capture"
            className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-br from-primary-700 to-primary-900 px-4 py-2 text-sm font-semibold text-white shadow-md shadow-primary-900/20 transition-all hover:-translate-y-px hover:shadow-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clipRule="evenodd" />
            </svg>
            Capture
          </Link>
        )}
      </div>

      <div className="flex flex-col gap-4 p-6">
        {/* ── Filter bar ────────────────────────────────────────────────── */}
        {filtersOpen && (
          <div className="flex flex-wrap items-end gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            {/* Search */}
            <div className="min-w-[200px] flex-1">
              <label htmlFor="search" className="mb-1 block text-xs font-medium text-slate-500">Search</label>
              <div className="relative">
                <svg xmlns="http://www.w3.org/2000/svg" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path fillRule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clipRule="evenodd" />
                </svg>
                <input
                  id="search"
                  type="search"
                  placeholder="Search tasks…"
                  value={filters.search}
                  onChange={(e) => handleFilterChange('search', e.target.value)}
                  className="w-full rounded-lg border border-slate-300 py-1.5 pl-9 pr-3 text-sm text-slate-800 placeholder-slate-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>
            </div>

            {/* Status — only in list view */}
            {viewMode === 'list' && (
              <div>
                <label htmlFor="status-filter" className="mb-1 block text-xs font-medium text-slate-500">Status</label>
                <select id="status-filter" value={filters.status} onChange={(e) => handleFilterChange('status', e.target.value as Filters['status'])} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-800 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500">
                  <option value="all">All statuses</option>
                  <option value="Open">Open</option>
                  <option value="In progress">In progress</option>
                  <option value="Done">Done</option>
                </select>
              </div>
            )}

            {/* Priority */}
            <div>
              <label htmlFor="priority-filter" className="mb-1 block text-xs font-medium text-slate-500">Priority</label>
              <select id="priority-filter" value={filters.priority} onChange={(e) => handleFilterChange('priority', e.target.value as Filters['priority'])} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-800 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500">
                <option value="all">All priorities</option>
                <option value="High">High</option>
                <option value="Medium">Medium</option>
                <option value="Low">Low</option>
              </select>
            </div>

            {/* Project */}
            <div>
              <label htmlFor="project-filter" className="mb-1 block text-xs font-medium text-slate-500">Project</label>
              <select id="project-filter" value={filters.project_id} onChange={(e) => handleFilterChange('project_id', e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-800 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500">
                <option value="all">All projects</option>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>

            {/* Needs review toggle */}
            {viewMode === 'list' && (
              <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50">
                <input type="checkbox" checked={filters.needs_review} onChange={(e) => handleFilterChange('needs_review', e.target.checked)} className="h-4 w-4 rounded border-slate-300 text-amber-500 focus:ring-amber-400" />
                Needs review only
              </label>
            )}

            {/* Clear */}
            {isFiltered && (
              <button type="button" onClick={clearFilters} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
                Clear
              </button>
            )}
          </div>
        )}

        {/* ── Loading / error states ─────────────────────────────────────── */}
        {isLoading && <LoadingSpinner message="Loading action items…" />}

        {isError && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            Failed to load items. Please refresh and try again.
          </div>
        )}

        {/* ── Board view ─────────────────────────────────────────────────── */}
        {!isLoading && !isError && viewMode === 'board' && (
          boardItems.length === 0 ? (
            <EmptyState
              title="No action items yet"
              description={isViewer ? 'No action items have been added to this workspace yet.' : 'Capture a meeting to extract action items — they\'ll appear on the board.'}
              actionLabel={isViewer ? undefined : 'Capture a Meeting'}
              captureTo={isViewer ? undefined : '/capture'}
            />
          ) : (
            <KanbanBoard items={boardItems} onUpdate={handleUpdate} />
          )
        )}

        {/* ── List view ──────────────────────────────────────────────────── */}
        {!isLoading && !isError && viewMode === 'list' && (
          <>
            {listItems.length === 0 ? (
              <EmptyState
                title={isFiltered ? 'No items match your filters' : 'No action items yet'}
                description={
                  isFiltered
                    ? 'Try adjusting or clearing the filters above.'
                    : 'Paste notes, upload a transcript, or capture a meeting to extract action items.'
                }
                actionLabel={isFiltered ? 'Clear filters' : (isViewer ? undefined : 'Capture a Meeting')}
                onAction={isFiltered ? clearFilters : undefined}
                captureTo={!isFiltered && !isViewer ? '/capture' : undefined}
              />
            ) : (
              <>
                {/* Needs review section */}
                {listItems.some((i) => i.needs_review) && !filters.needs_review && (
                  <section>
                    <h2 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber-700">
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-1 ring-1 ring-inset ring-amber-200">
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                          <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
                        </svg>
                        Needs review · {listItems.filter((i) => i.needs_review).length}
                      </span>
                    </h2>
                    <div className="flex flex-col gap-3">
                      {listItems.filter((i) => i.needs_review).map((item) => (
                        <ActionItemCard key={item.id} item={item} onUpdate={handleUpdate} onDelete={handleDelete} />
                      ))}
                    </div>
                  </section>
                )}

                {/* Main list */}
                <section>
                  {!filters.needs_review && listItems.some((i) => i.needs_review) && (
                    <h2 className="mb-3 text-sm font-semibold text-slate-500">
                      All items ({listItems.filter((i) => !i.needs_review).length})
                    </h2>
                  )}
                  <div className="flex flex-col gap-3">
                    {(filters.needs_review ? listItems : listItems.filter((i) => !i.needs_review)).map((item) => (
                      <ActionItemCard key={item.id} item={item} onUpdate={handleUpdate} onDelete={handleDelete} />
                    ))}
                  </div>
                </section>

                {/* Pagination */}
                {(hasPrev || hasNext) && (
                  <div className="flex items-center justify-center gap-3 pt-2">
                    <button type="button" onClick={() => setPage((p) => p - 1)} disabled={!hasPrev} className="rounded-lg border border-slate-300 px-4 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">Previous</button>
                    <span className="text-sm text-slate-500">Page {page}</span>
                    <button type="button" onClick={() => setPage((p) => p + 1)} disabled={!hasNext} className="rounded-lg border border-slate-300 px-4 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">Next</button>
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
