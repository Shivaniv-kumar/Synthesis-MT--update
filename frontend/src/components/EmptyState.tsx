import React from 'react'
import { Link } from 'react-router-dom'

// ── Props ─────────────────────────────────────────────────────────────────────

interface EmptyStateProps {
  title: string
  description: string
  actionLabel?: string
  /** Render action as a router Link (navigation) instead of a button (callback). */
  captureTo?: string
  onAction?: () => void
  className?: string
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function EmptyState({
  title,
  description,
  actionLabel,
  captureTo,
  onAction,
  className = '',
}: EmptyStateProps): React.JSX.Element {
  const actionClass =
    'mt-6 inline-flex items-center gap-2 rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-primary-800 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2'

  return (
    <div
      className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-200 bg-slate-50 px-6 py-16 text-center ${className}`}
    >
      {/* Icon */}
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-slate-200">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="h-7 w-7 text-slate-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M9 12h6m-3-3v6M3 12l2.25-4.5A2.25 2.25 0 017.5 6h9a2.25 2.25 0 012.25 1.5L21 12v6a1.5 1.5 0 01-1.5 1.5h-15A1.5 1.5 0 013 18v-6z"
          />
        </svg>
      </div>

      <h3 className="text-base font-semibold text-slate-800">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-slate-500">{description}</p>

      {actionLabel !== undefined && (
        captureTo !== undefined ? (
          <Link to={captureTo} className={actionClass}>
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clipRule="evenodd" />
            </svg>
            {actionLabel}
          </Link>
        ) : onAction !== undefined ? (
          <button type="button" onClick={onAction} className={actionClass}>
            {actionLabel}
          </button>
        ) : null
      )}
    </div>
  )
}
