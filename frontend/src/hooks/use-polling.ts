import { useCallback, useEffect, useRef, useState } from "react"

interface UsePollingOptions<T> {
  fetcher: () => Promise<T>
  interval: number
  enabled?: boolean
  /** Change this value to force an immediate re-fetch (e.g. filter key). */
  key?: string | number
}

interface UsePollingResult<T> {
  data: T | null
  error: string | null
  loading: boolean
  refresh: () => void
}

export function usePolling<T>({
  fetcher,
  interval,
  enabled = true,
  key,
}: UsePollingOptions<T>): UsePollingResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const mountedRef = useRef(true)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const doFetch = useCallback(async (isInitial: boolean) => {
    if (isInitial) setLoading(true)
    try {
      const result = await fetcherRef.current()
      if (mountedRef.current) {
        setData(result)
        setError(null)
      }
    } catch (e) {
      if (mountedRef.current) {
        setError(e instanceof Error ? e.message : "Unknown error")
      }
    } finally {
      if (mountedRef.current && isInitial) setLoading(false)
    }
  }, [])

  const refresh = useCallback(() => {
    doFetch(false)
  }, [doFetch])

  // Initial fetch + polling timer. Restarts when key changes (filter switch).
  useEffect(() => {
    mountedRef.current = true
    if (!enabled) return

    doFetch(true)
    const timer = setInterval(() => doFetch(false), interval)

    return () => {
      mountedRef.current = false
      clearInterval(timer)
    }
  }, [enabled, interval, doFetch, key])

  return { data, error, loading, refresh }
}
