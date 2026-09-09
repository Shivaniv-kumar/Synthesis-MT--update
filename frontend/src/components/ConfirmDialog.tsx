import React, { useEffect, useRef, useCallback } from 'react'

// ── Props ─────────────────────────────────────────────────────────────────────

interface ConfirmDialogProps {
  isOpen: boolean
  title: string
  message: string
  confirmLabel?: string
  onConfirm: () => void
  onCancel: () => void
  isDestructive?: boolean
}

// ── ConfirmDialog ─────────────────────────────────────────────────────────────

/**
 * Accessible modal dialog for confirming destructive actions.
 *
 * Accessibility features:
 *   - role="dialog", aria-modal="true"
 *   - aria-labelledby / aria-describedby wired to title and message
 *   - Focus trap: Tab/Shift+Tab cycle within the modal only
 *   - Focus moves to the confirm button on open
 *   - Escape key → onCancel
 *   - Backdrop click → onCancel
 */
export default function ConfirmDialog({
  isOpen,
  title,
  message,
  confirmLabel = 'Delete',
  onConfirm,
  onCancel,
  isDestructive = true,
}: ConfirmDialogProps): React.JSX.Element | null {
  const dialogRef = useRef<HTMLDivElement>(null)
  const confirmButtonRef = useRef<HTMLButtonElement>(null)
  const cancelButtonRef = useRef<HTMLButtonElement>(null)

  // Move focus to the confirm button when the dialog opens
  useEffect(() => {
    if (isOpen) {
      // Defer so the dialog has rendered before we attempt focus
      const timerId = setTimeout(() => {
        confirmButtonRef.current?.focus()
      }, 0)
      return () => clearTimeout(timerId)
    }
  }, [isOpen])

  // Restore focus to the previously focused element when the dialog closes
  const previousFocusRef = useRef<Element | null>(null)
  useEffect(() => {
    if (isOpen) {
      previousFocusRef.current = document.activeElement
    } else {
      if (previousFocusRef.current instanceof HTMLElement) {
        previousFocusRef.current.focus()
      }
    }
  }, [isOpen])

  // Focus trap — keep Tab/Shift+Tab within the two buttons
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === 'Escape') {
        onCancel()
        return
      }

      if (e.key !== 'Tab') return

      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )

      if (!focusable || focusable.length === 0) return

      const first = focusable[0]
      const last = focusable[focusable.length - 1]

      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault()
          last.focus()
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      }
    },
    [onCancel],
  )

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      // Only cancel when clicking the backdrop itself, not dialog contents
      if (e.target === e.currentTarget) {
        onCancel()
      }
    },
    [onCancel],
  )

  if (!isOpen) return null

  const titleId = 'confirm-dialog-title'
  const descId = 'confirm-dialog-desc'

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4
                 bg-black/40 backdrop-blur-sm
                 animate-in fade-in duration-200"
      onClick={handleBackdropClick}
      aria-hidden="false"
    >
      {/* Dialog panel */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        onKeyDown={handleKeyDown}
        className="relative w-full max-w-md rounded-xl bg-white shadow-2xl
                   p-6 flex flex-col gap-4
                   animate-in zoom-in-95 slide-in-from-bottom-2 duration-200"
      >
        {/* Title */}
        <h2 id={titleId} className="text-lg font-semibold text-gray-900">
          {title}
        </h2>

        {/* Message */}
        <p id={descId} className="text-sm text-gray-600 leading-relaxed">
          {message}
        </p>

        {/* Actions */}
        <div className="flex justify-end gap-3 mt-2">
          <button
            ref={cancelButtonRef}
            type="button"
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm font-medium text-gray-700
                       bg-gray-100 hover:bg-gray-200 transition-colors focus:outline-none
                       focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-gray-400"
          >
            Cancel
          </button>

          <button
            ref={confirmButtonRef}
            type="button"
            onClick={onConfirm}
            className={[
              'px-4 py-2 rounded-lg text-sm font-medium text-white transition-colors',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
              isDestructive
                ? 'bg-red-600 hover:bg-red-700 focus-visible:ring-red-500'
                : 'bg-blue-600 hover:bg-blue-700 focus-visible:ring-blue-500',
            ].join(' ')}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
