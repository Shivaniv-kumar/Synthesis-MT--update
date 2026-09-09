import React from 'react'
import type { ActionItem, Meeting } from '@/types'

// ── Props ─────────────────────────────────────────────────────────────────────

type StatusValue = ActionItem['status'] | Meeting['status']

interface StatusBadgeProps {
  status: StatusValue
  className?: string
}

// ── Color map ─────────────────────────────────────────────────────────────────

const colorMap: Record<StatusValue, string> = {
  // Action item statuses
  Open: 'bg-sky-100 text-sky-700 ring-sky-200',
  'In progress': 'bg-amber-100 text-amber-700 ring-amber-200',
  Done: 'bg-green-100 text-green-700 ring-green-200',
  // Meeting statuses
  draft: 'bg-slate-100 text-slate-600 ring-slate-200',
  extracting: 'bg-violet-100 text-violet-700 ring-violet-200',
  extracted: 'bg-blue-100 text-blue-700 ring-blue-200',
  reviewed: 'bg-green-100 text-green-700 ring-green-200',
  failed: 'bg-red-100 text-red-700 ring-red-200',
}

const labelMap: Record<StatusValue, string> = {
  Open: 'Open',
  'In progress': 'In Progress',
  Done: 'Done',
  draft: 'Draft',
  extracting: 'Extracting…',
  extracted: 'Extracted',
  reviewed: 'Reviewed',
  failed: 'Failed',
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function StatusBadge({
  status,
  className = '',
}: StatusBadgeProps): React.JSX.Element {
  const colors = colorMap[status] ?? 'bg-slate-100 text-slate-600 ring-slate-200'
  const label = labelMap[status] ?? status

  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${colors} ${className}`}
    >
      {label}
    </span>
  )
}
