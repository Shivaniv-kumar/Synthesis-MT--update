import { format, differenceInCalendarDays, parseISO } from 'date-fns'

// ── formatDate ────────────────────────────────────────────────────────────────

/**
 * Formats an ISO date string as "Jun 20, 2026".
 * Returns "—" for null or undefined input.
 */
export function formatDate(date: string | null | undefined): string {
  if (!date) return '—'
  try {
    return format(parseISO(date), 'MMM d, yyyy')
  } catch {
    return '—'
  }
}

// ── formatRelativeDate ────────────────────────────────────────────────────────

/**
 * Returns a human-readable relative due date string:
 *   - "3 days overdue"
 *   - "due tomorrow"
 *   - "due today"
 *   - "due in 5 days"
 *   - "—" if date is null or undefined
 *
 * @param date     ISO date string of the due date
 * @param today    Reference date (defaults to `new Date()`)
 */
export function formatRelativeDate(
  date: string | null | undefined,
  today: Date = new Date(),
): string {
  if (!date) return '—'

  try {
    const dueDate = parseISO(date)
    const diffDays = differenceInCalendarDays(dueDate, today)

    if (diffDays < 0) {
      const overdueDays = Math.abs(diffDays)
      return `${overdueDays} ${overdueDays === 1 ? 'day' : 'days'} overdue`
    }
    if (diffDays === 0) return 'due today'
    if (diffDays === 1) return 'due tomorrow'
    return `due in ${diffDays} days`
  } catch {
    return '—'
  }
}

// ── formatConfidence ──────────────────────────────────────────────────────────

/**
 * Formats a confidence score (0–1 or 0–100) as a percentage string.
 * E.g. 0.87 → "87%" or 87 → "87%"
 */
export function formatConfidence(n: number): string {
  const pct = n <= 1 ? Math.round(n * 100) : Math.round(n)
  return `${pct}%`
}

// ── truncate ──────────────────────────────────────────────────────────────────

/**
 * Truncates `text` to `maxLength` characters and appends "…" if it exceeds
 * that length.
 */
export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text
  return text.slice(0, maxLength) + '…'
}

// ── priorityToColorClass ──────────────────────────────────────────────────────

/**
 * Returns Tailwind color utility classes for a priority label.
 */
export function priorityToColorClass(priority: string): string {
  switch (priority) {
    case 'High':
      return 'bg-red-100 text-red-700'
    case 'Medium':
      return 'bg-amber-100 text-amber-700'
    case 'Low':
      return 'bg-green-100 text-green-700'
    default:
      return 'bg-gray-100 text-gray-700'
  }
}

// ── statusToColorClass ────────────────────────────────────────────────────────

/**
 * Returns Tailwind color utility classes for an action item status label.
 */
export function statusToColorClass(status: string): string {
  switch (status) {
    case 'Open':
      return 'bg-blue-100 text-blue-700'
    case 'In progress':
      return 'bg-amber-100 text-amber-700'
    case 'Done':
      return 'bg-green-100 text-green-700'
    default:
      return 'bg-gray-100 text-gray-700'
  }
}
