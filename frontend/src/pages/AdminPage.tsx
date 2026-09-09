import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/contexts/AuthContext'
import { getRetentionConfig, updateRetentionConfig } from '@/api/admin'
import { listWorkspaces, createWorkspace } from '@/api/workspaces'

type TabKey = 'workspaces' | 'retention'

// ── Admin page ────────────────────────────────────────────────────────────────

export default function AdminPage(): React.JSX.Element {
  const { user } = useAuth()
  const [activeTab, setActiveTab] = useState<TabKey>('workspaces')

  if (user?.role !== 'Admin') {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-slate-500">
        <svg xmlns="http://www.w3.org/2000/svg" className="mb-3 h-10 w-10 text-slate-300" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path fillRule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clipRule="evenodd" />
        </svg>
        <p className="text-sm font-medium">Admin access required</p>
        <p className="mt-1 text-xs text-slate-400">This page is only accessible to workspace admins.</p>
      </div>
    )
  }

  const tabs: { key: TabKey; label: string }[] = [
    { key: 'workspaces', label: 'Workspaces' },
    { key: 'retention', label: 'Data Retention' },
  ]

  return (
    <div className="flex flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Admin Console</h1>
        <p className="mt-0.5 text-sm text-slate-500">Manage team members and workspace settings.</p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-slate-200">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'border-b-2 border-primary-700 text-primary-700'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'workspaces' && <WorkspacesTab />}
      {activeTab === 'retention' && <RetentionTab />}
    </div>
  )
}

// ── Retention tab ─────────────────────────────────────────────────────────────

function RetentionTab() {
  const queryClient = useQueryClient()

  const { data: config, isLoading, isError } = useQuery({
    queryKey: ['admin', 'retention'],
    queryFn: getRetentionConfig,
    staleTime: 60_000,
  })

  const [transcript, setTranscript] = useState<number | ''>('')
  const [items, setItems] = useState<number | ''>('')
  const [audio, setAudio] = useState<number | ''>('')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveSuccess, setSaveSuccess] = useState(false)

  const mutation = useMutation({
    mutationFn: updateRetentionConfig,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin', 'retention'] })
      setSaveSuccess(true)
      setSaveError(null)
      setTimeout(() => setSaveSuccess(false), 3000)
    },
    onError: (err: any) => {
      const detail = err?.response?.data?.detail
      const msg = Array.isArray(detail)
        ? detail.map((e: any) => (typeof e === 'object' ? (e.msg ?? JSON.stringify(e)) : String(e))).join('; ')
        : typeof detail === 'string'
        ? detail
        : (err?.message ?? 'Failed to save retention settings.')
      setSaveError(msg)
    },
  })

  function handleSave(e: React.FormEvent) {
    e.preventDefault()
    const patch: Record<string, number> = {}
    if (transcript !== '') patch.transcript_retention_days = Number(transcript)
    if (items !== '') patch.item_retention_days = Number(items)
    if (audio !== '') patch.audio_retention_days = Number(audio)
    if (Object.keys(patch).length === 0) return
    mutation.mutate(patch)
  }

  if (isLoading) return <div className="py-12 text-center text-sm text-slate-400">Loading retention settings…</div>
  if (isError) return <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600">Failed to load retention settings.</div>

  return (
    <div className="max-w-lg">
      <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="mb-1 text-sm font-semibold text-slate-700">Data Retention Policy</h2>
        <p className="mb-5 text-xs text-slate-500">
          Minimum 30 days. Data older than the configured threshold is automatically purged.
          Leave a field blank to keep its current value.
        </p>

        {config && (
          <div className="mb-5 grid grid-cols-3 gap-3 rounded-xl bg-slate-50 p-4 text-center">
            <div>
              <p className="text-lg font-bold text-slate-800">{config.transcript_retention_days}</p>
              <p className="text-xs text-slate-500">Transcript days</p>
            </div>
            <div>
              <p className="text-lg font-bold text-slate-800">{config.item_retention_days}</p>
              <p className="text-xs text-slate-500">Item days</p>
            </div>
            <div>
              <p className="text-lg font-bold text-slate-800">{config.audio_retention_days}</p>
              <p className="text-xs text-slate-500">Audio days</p>
            </div>
          </div>
        )}

        <form onSubmit={handleSave} className="space-y-4">
          {([
            ['transcript_retention_days', 'Transcript retention (days)', transcript, setTranscript],
            ['item_retention_days', 'Action item retention (days)', items, setItems],
            ['audio_retention_days', 'Audio file retention (days)', audio, setAudio],
          ] as [string, string, number | '', React.Dispatch<React.SetStateAction<number | ''>>][]).map(([id, label, val, set]) => (
            <div key={id}>
              <label htmlFor={id} className="mb-1 block text-xs font-medium text-slate-600">{label}</label>
              <input
                id={id}
                type="number"
                min={30}
                placeholder={`current: ${config?.[id as keyof typeof config] ?? '—'}`}
                value={val}
                onChange={(e) => set(e.target.value === '' ? '' : Number(e.target.value))}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
            </div>
          ))}

          {saveError !== null && (
            <p className="text-xs text-red-600">{saveError}</p>
          )}
          {saveSuccess && (
            <p className="text-xs text-emerald-600">Retention settings saved successfully.</p>
          )}

          <button
            type="submit"
            disabled={mutation.isPending}
            className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-br from-primary-700 to-primary-900 px-5 py-2 text-sm font-semibold text-white shadow-md shadow-primary-900/20 transition-all hover:-translate-y-px hover:shadow-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 disabled:opacity-60 disabled:hover:translate-y-0"
          >
            {mutation.isPending ? 'Saving…' : 'Save Changes'}
          </button>
        </form>
      </div>
    </div>
  )
}

// ── Workspaces tab ────────────────────────────────────────────────────────────

function WorkspacesTab(): React.JSX.Element {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const { data: workspaces = [], isLoading } = useQuery({
    queryKey: ['admin', 'workspaces'],
    queryFn: listWorkspaces,
  })

  const createMut = useMutation({
    mutationFn: (wsName: string) => createWorkspace(wsName),
    onSuccess: (ws) => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'workspaces'] })
      queryClient.invalidateQueries({ queryKey: ['my-workspaces'] })
      setName('')
      setError(null)
      setSuccess(`Workspace "${ws.name}" created. Switch to it using the workspace selector in the top bar.`)
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'Failed to create workspace. Please try again.')
      setSuccess(null)
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setError(null)
    setSuccess(null)
    createMut.mutate(trimmed)
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Create workspace */}
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="mb-1 text-base font-semibold text-slate-800">Create a new workspace</h2>
        <p className="mb-4 text-sm text-slate-500">
          Workspaces keep teams and their meetings, action items, and knowledge base separate within your organisation.
        </p>
        <form onSubmit={handleSubmit} className="flex items-end gap-3">
          <div className="flex-1">
            <label htmlFor="ws-name" className="mb-1 block text-xs font-medium text-slate-600">
              Workspace name
            </label>
            <input
              id="ws-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Engineering, Sales, Product"
              maxLength={255}
              required
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </div>
          <button
            type="submit"
            disabled={!name.trim() || createMut.isPending}
            className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {createMut.isPending ? 'Creating…' : 'Create'}
          </button>
        </form>
        {error && (
          <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
        )}
        {success && (
          <p className="mt-3 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">{success}</p>
        )}
      </div>

      {/* Workspace list */}
      <div className="rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-700">All workspaces in this organisation</h2>
        </div>
        {isLoading ? (
          <p className="px-5 py-8 text-center text-sm text-slate-500">Loading…</p>
        ) : workspaces.length === 0 ? (
          <p className="px-5 py-8 text-center text-sm text-slate-500">No workspaces found.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {workspaces.map((ws) => (
              <li key={ws.id} className="flex items-center gap-3 px-5 py-3">
                <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md bg-primary-50 text-primary-700">
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z" />
                  </svg>
                </div>
                <div>
                  <p className="text-sm font-medium text-slate-800">{ws.name}</p>
                  <p className="text-xs text-slate-400">{ws.id}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
