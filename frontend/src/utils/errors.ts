// ── ApiError ──────────────────────────────────────────────────────────────────

/**
 * Represents an error returned from the backend API.
 * Carries the HTTP status code and an optional machine-readable code.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
  ) {
    super(message)
    this.name = 'ApiError'
    // Restore prototype chain for instanceof checks in transpiled output
    Object.setPrototypeOf(this, ApiError.prototype)
  }
}

// ── formatApiError ────────────────────────────────────────────────────────────

/**
 * Converts an unknown thrown value into a user-friendly error string.
 */
export function formatApiError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        return 'Session expired, please log in'
      case 403:
        return "You don't have permission to do this"
      case 404:
        return 'Item not found'
      case 429:
        return 'Too many requests, please wait a moment'
      case 500:
        return 'Server error. Please try again later'
      default:
        return error.message
    }
  }

  if (error instanceof Error) {
    return error.message
  }

  return 'An unexpected error occurred'
}

// ── isAuthError ───────────────────────────────────────────────────────────────

/**
 * Returns true if the error is an ApiError with HTTP status 401.
 */
export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}
