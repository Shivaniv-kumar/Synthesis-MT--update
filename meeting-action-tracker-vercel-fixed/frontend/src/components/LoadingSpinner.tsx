import React from 'react'

// ── Props ─────────────────────────────────────────────────────────────────────

interface LoadingSpinnerProps {
  message?: string
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

// ── Size map ──────────────────────────────────────────────────────────────────

const sizeClasses = {
  sm: 'h-5 w-5 border-2',
  md: 'h-8 w-8 border-2',
  lg: 'h-12 w-12 border-4',
} as const

// ── Component ─────────────────────────────────────────────────────────────────

export default function LoadingSpinner({
  message,
  size = 'md',
  className = '',
}: LoadingSpinnerProps): React.JSX.Element {
  return (
    <div
      className={`flex flex-col items-center justify-center gap-3 ${className}`}
      role="status"
      aria-live="polite"
    >
      <div
        className={`animate-spin rounded-full border-primary-200 border-t-primary-600 ${sizeClasses[size]}`}
        aria-hidden="true"
      />
      {message !== undefined && message !== '' && (
        <p className="text-sm text-primary-500">{message}</p>
      )}
      <span className="sr-only">{message ?? 'Loading...'}</span>
    </div>
  )
}
