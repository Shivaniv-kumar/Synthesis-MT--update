import React, { useEffect, useCallback } from 'react'
import { create } from 'zustand'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Toast {
  id: string
  type: 'success' | 'error' | 'info' | 'warning'
  message: string
  duration?: number
}

// ── Zustand store (inline) ────────────────────────────────────────────────────

interface ToastStore {
  toasts: Toast[]
  addToast: (toast: Omit<Toast, 'id'>) => void
  removeToast: (id: string) => void
}

const useToastStore = create<ToastStore>((set) => ({
  toasts: [],

  addToast: (toast) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    set((state) => ({
      toasts: [...state.toasts, { ...toast, id }],
    }))
  },

  removeToast: (id) => {
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    }))
  },
}))

// ── useToast — public hook ────────────────────────────────────────────────────

/**
 * Hook for triggering toast notifications from any component.
 */
export function useToast(): {
  addToast: (toast: Omit<Toast, 'id'>) => void
  removeToast: (id: string) => void
} {
  const addToast = useToastStore((s) => s.addToast)
  const removeToast = useToastStore((s) => s.removeToast)
  return { addToast, removeToast }
}

// ── ToastItem ─────────────────────────────────────────────────────────────────

const borderColorMap: Record<Toast['type'], string> = {
  success: 'border-l-green-500',
  error: 'border-l-red-500',
  info: 'border-l-blue-500',
  warning: 'border-l-amber-500',
}

const iconMap: Record<Toast['type'], string> = {
  success: '✓',
  error: '✕',
  info: 'ℹ',
  warning: '⚠',
}

const iconColorMap: Record<Toast['type'], string> = {
  success: 'text-green-600',
  error: 'text-red-600',
  info: 'text-blue-600',
  warning: 'text-amber-600',
}

interface ToastItemProps {
  toast: Toast
}

function ToastItem({ toast }: ToastItemProps): React.JSX.Element {
  const removeToast = useToastStore((s) => s.removeToast)

  const dismiss = useCallback(() => {
    removeToast(toast.id)
  }, [removeToast, toast.id])

  // Auto-dismiss after `duration` ms (default 4000)
  useEffect(() => {
    const duration = toast.duration ?? 4000
    const timerId = setTimeout(dismiss, duration)
    return () => clearTimeout(timerId)
  }, [dismiss, toast.duration])

  return (
    <div
      role="alert"
      className={[
        'flex items-start gap-3 w-80 max-w-xs rounded-lg shadow-lg bg-white',
        'border border-gray-200 border-l-4 px-4 py-3',
        'transition-all duration-300 ease-out',
        'animate-in slide-in-from-right-4 fade-in',
        borderColorMap[toast.type],
      ].join(' ')}
    >
      {/* Icon */}
      <span className={`mt-0.5 text-sm font-bold shrink-0 ${iconColorMap[toast.type]}`}>
        {iconMap[toast.type]}
      </span>

      {/* Message */}
      <p className="flex-1 text-sm text-gray-800 leading-snug">{toast.message}</p>

      {/* Dismiss button */}
      <button
        type="button"
        aria-label="Dismiss notification"
        onClick={dismiss}
        className="shrink-0 text-gray-400 hover:text-gray-600 transition-colors text-base leading-none mt-0.5"
      >
        ×
      </button>
    </div>
  )
}

// ── ToastContainer ────────────────────────────────────────────────────────────

/**
 * Renders all active toasts in a fixed bottom-right container.
 * Uses aria-live="polite" so screen readers announce new messages.
 */
export function ToastContainer(): React.JSX.Element {
  const toasts = useToastStore((s) => s.toasts)

  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      aria-label="Notifications"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 items-end"
    >
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} />
      ))}
    </div>
  )
}
