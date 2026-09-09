import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { getDashboardSummary } from '@/api/dashboard'
import LoadingSpinner from '@/components/LoadingSpinner'
import type { DashboardSummary } from '@/types'

// ── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  color,
  icon,
}: {
  label: string
  value: number
  color: string
  icon: React.ReactNode
}): React.JSX.Element {
  return (
    <div className={`rounded-xl border ${color} bg-white p-5 shadow-sm`}>
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-slate-600">{label}</p>
        <span className="text-slate-400">{icon}</span>
      </div>
      <p className="mt-2 text-3xl font-bold text-slate-900">{value}</p>
    </div>
  )
}

// ── Workload bar ──────────────────────────────────────────────────────────────

function WorkloadBar({
  label,
  count,
  max,
}: {
  label: string
  count: number
  max: number
}): React.JSX.Element {
  const pct = max > 0 ? Math.round((count / max) * 100) : 0
  return (
    <div className="flex items-center gap-3">
      <span className="w-28 shrink-0 truncate text-right text-sm text-slate-700">{label}</span>
      <div className="flex-1 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div
          className="h-2.5 rounded-full bg-slate-600 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-6 shrink-0 text-right text-sm font-medium text-slate-600">{count}</span>
    </div>
  )
}

// ── Priority breakdown ────────────────────────────────────────────────────────

function PriorityRow({
  label,
  count,
  colorClass,
}: {
  label: string
  count: number
  colorClass: string
}): React.JSX.Element {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${colorClass}`}>
        {label}
      </span>
      <span className="text-sm font-medium text-slate-700">{count}</span>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function DashboardPage(): React.JSX.Element {
  const { data, isLoading, isError } = useQuery<DashboardSummary>({
    queryKey: ['dashboard-summary'],
    queryFn: getDashboardSummary,
    staleTime: 60_000,
    refetchInterval: 120_000,
    refetchOnWindowFocus: true, // L7: refresh counts when user returns to tab
  })

  if (isLoading) return <LoadingSpinner message="Loading dashboard…" />

  if (isError || !data) {
    return (
      <div className="p-6">
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Failed to load dashboard data. Please refresh.
        </div>
      </div>
    )
  }

  const maxOwnerCount = Math.max(...data.items_by_owner.map((o) => o.count), 1)

  return (
    <div className="flex flex-col gap-8 p-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Dashboard</h1>
        <p className="mt-1 text-sm text-slate-500">Overview of action items across your workspace</p>
      </div>

      {/* KPI cards */}
      <section aria-label="Key metrics">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard
            label="Open"
            value={data.total_open}
            color="border-blue-200"
            icon={
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clipRule="evenodd" />
              </svg>
            }
          />
          <StatCard
            label="In Progress"
            value={data.total_in_progress}
            color="border-amber-200"
            icon={
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path d="M11 17a1 1 0 001.447.894l4-2A1 1 0 0017 15V9.236a1 1 0 00-1.447-.894l-4 2a1 1 0 00-.553.894V17zM15.211 6.276a1 1 0 000-1.788l-4.764-2.382a1 1 0 00-.894 0L4.789 4.488a1 1 0 000 1.788l4.764 2.382a1 1 0 00.894 0l4.764-2.382zM4.447 8.342A1 1 0 003 9.236V15a1 1 0 00.553.894l4 2A1 1 0 009 17v-5.764a1 1 0 00-.553-.894l-4-2z" />
              </svg>
            }
          />
          <StatCard
            label="Done"
            value={data.total_done}
            color="border-green-200"
            icon={
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
            }
          />
          <StatCard
            label="Overdue"
            value={data.overdue_count}
            color="border-red-200"
            icon={
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
              </svg>
            }
          />
        </div>

        {/* Secondary stats */}
        <div className="mt-4 flex flex-wrap gap-4">
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm">
            <span className="font-medium text-amber-800">{data.needs_review_count}</span>
            <span className="ml-1 text-amber-600">need review</span>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-sm">
            <span className="font-medium text-slate-800">{data.meetings_this_week}</span>
            <span className="ml-1 text-slate-600">meetings this week</span>
          </div>
        </div>
      </section>

      {/* Two-column section */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Workload by owner */}
        <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-slate-800">Open items by owner</h2>
          {data.items_by_owner.length === 0 ? (
            <p className="text-sm text-slate-400">No open items</p>
          ) : (
            <div className="flex flex-col gap-3">
              {data.items_by_owner
                .sort((a, b) => b.count - a.count)
                .slice(0, 10)
                .map((o) => (
                  <WorkloadBar
                    key={o.owner_label}
                    label={o.owner_label}
                    count={o.count}
                    max={maxOwnerCount}
                  />
                ))}
            </div>
          )}
        </section>

        {/* Priority breakdown */}
        <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-slate-800">Items by priority</h2>
          <div className="divide-y divide-slate-100">
            <PriorityRow
              label="High"
              count={data.items_by_priority.High}
              colorClass="bg-red-100 text-red-700"
            />
            <PriorityRow
              label="Medium"
              count={data.items_by_priority.Medium}
              colorClass="bg-amber-100 text-amber-700"
            />
            <PriorityRow
              label="Low"
              count={data.items_by_priority.Low}
              colorClass="bg-green-100 text-green-700"
            />
          </div>

          {/* Visual proportion bars */}
          <div className="mt-5 flex h-4 overflow-hidden rounded-full">
            {(() => {
              const total = data.items_by_priority.High + data.items_by_priority.Medium + data.items_by_priority.Low
              if (total === 0) return <div className="flex-1 bg-slate-100" />
              return (
                <>
                  <div
                    className="bg-red-400"
                    style={{ width: `${(data.items_by_priority.High / total) * 100}%` }}
                    title={`High: ${data.items_by_priority.High}`}
                  />
                  <div
                    className="bg-amber-400"
                    style={{ width: `${(data.items_by_priority.Medium / total) * 100}%` }}
                    title={`Medium: ${data.items_by_priority.Medium}`}
                  />
                  <div
                    className="bg-green-400"
                    style={{ width: `${(data.items_by_priority.Low / total) * 100}%` }}
                    title={`Low: ${data.items_by_priority.Low}`}
                  />
                </>
              )
            })()}
          </div>
        </section>
      </div>
    </div>
  )
}
