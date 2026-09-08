import React, { type ReactNode, type ErrorInfo } from 'react'

// ── Props & State ─────────────────────────────────────────────────────────────

interface ErrorBoundaryProps {
  children: ReactNode
  /** Optional custom fallback UI. If provided, replaces the default error screen. */
  fallback?: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

// ── ErrorBoundary ─────────────────────────────────────────────────────────────

/**
 * React class-based error boundary.
 *
 * Catches JavaScript errors anywhere in the child component tree, logs them,
 * and displays a user-friendly fallback UI — never the raw stack trace.
 */
export default class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // In production this would forward to an error-tracking service (e.g. Sentry)
    console.error('[ErrorBoundary] Uncaught error:', error, info.componentStack)
  }

  private handleReload = (): void => {
    window.location.reload()
  }

  private handleGoToTracker = (): void => {
    window.location.href = '/tracker'
  }

  render(): ReactNode {
    if (this.state.hasError) {
      // Use a custom fallback if provided
      if (this.props.fallback) {
        return this.props.fallback
      }

      // Default user-friendly error UI
      return (
        <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
          <div className="max-w-md w-full text-center space-y-6">
            {/* Icon */}
            <div className="mx-auto w-16 h-16 rounded-full bg-red-100 flex items-center justify-center">
              <svg
                className="w-8 h-8 text-red-600"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
                />
              </svg>
            </div>

            {/* Heading */}
            <h1 className="text-2xl font-semibold text-gray-900">Something went wrong</h1>

            {/* Message — never expose stack trace */}
            <p className="text-gray-600">
              An unexpected error occurred. You can try reloading the page or return to the
              tracker.
            </p>

            {/* Actions */}
            <div className="flex flex-col sm:flex-row gap-3 justify-center">
              <button
                type="button"
                onClick={this.handleReload}
                className="px-5 py-2.5 rounded-lg text-sm font-medium text-white
                           bg-blue-600 hover:bg-blue-700 transition-colors
                           focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500
                           focus-visible:ring-offset-2"
              >
                Try again
              </button>

              <button
                type="button"
                onClick={this.handleGoToTracker}
                className="px-5 py-2.5 rounded-lg text-sm font-medium text-gray-700
                           bg-gray-100 hover:bg-gray-200 transition-colors
                           focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-400
                           focus-visible:ring-offset-2"
              >
                Go to tracker
              </button>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
