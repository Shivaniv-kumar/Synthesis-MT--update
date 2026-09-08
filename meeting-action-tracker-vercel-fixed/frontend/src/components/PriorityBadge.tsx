import React from 'react'
import type { ActionItem } from '@/types'

// ── Props ─────────────────────────────────────────────────────────────────────

interface PriorityBadgeProps {
  priority: ActionItem['priority']
  className?: string
}

// ── Color map ─────────────────────────────────────────────────────────────────

const colorMap: Record<ActionItem['priority'], string> = {
  High: 'bg-red-100 text-red-700 ring-red-200',
  Medium: 'bg-amber-100 text-amber-700 ring-amber-200',
  Low: 'bg-green-100 text-green-700 ring-green-200',
}

const dotColorMap: Record<ActionItem['priority'], string> = {
  High: 'bg-red-500',
  Medium: 'bg-amber-500',
  Low: 'bg-green-500',
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function PriorityBadge({
  priority,
  className = '',
}: PriorityBadgeProps): React.JSX.Element {
  const colors = colorMap[priority]
  const dotColor = dotColorMap[priority]

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${colors} ${className}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dotColor}`} aria-hidden="true" />
      {priority}
    </span>
  )
}
