import { useState, useEffect, useRef, useCallback } from 'react'

// ── usePolling ────────────────────────────────────────────────────────────────

/**
 * A generic polling hook that repeatedly calls `fetcher` every `intervalMs`
 * milliseconds while `enabled` is true.
 *
 * - Calls fetcher immediately on mount / when enabled becomes true.
 * - Cleans up the interval on unmount or when `enabled` becomes false.
 * - Catches errors and stores them in state — never throws.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled: boolean,
  onData?: (data: T) => void,
): { data: T | null; error: Error | null; isLoading: boolean } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [isLoading, setIsLoading] = useState<boolean>(false)

  // Keep a stable ref to the latest callbacks so intervals don't close over stale values
  const fetcherRef = useRef(fetcher)
  const onDataRef = useRef(onData)

  useEffect(() => {
    fetcherRef.current = fetcher
  }, [fetcher])

  useEffect(() => {
    onDataRef.current = onData
  }, [onData])

  const runFetch = useCallback(async () => {
    setIsLoading(true)
    try {
      const result = await fetcherRef.current()
      setData(result)
      setError(null)
      onDataRef.current?.(result)
    } catch (err) {
      const normalized = err instanceof Error ? err : new Error(String(err))
      setError(normalized)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return

    // Immediate first fetch
    void runFetch()

    const timerId = setInterval(() => {
      void runFetch()
    }, intervalMs)

    return () => {
      clearInterval(timerId)
    }
  }, [enabled, intervalMs, runFetch])

  return { data, error, isLoading }
}
