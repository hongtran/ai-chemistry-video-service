import { useEffect, useRef, useState } from 'react'

/**
 * Poll `fetcher` every `intervalMs` while `active` is true (plus one
 * immediate fetch). The caller flips `active` off on terminal states.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  active: boolean,
): { data: T | null; error: Error | null } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    if (!active) return
    let cancelled = false

    const tick = async () => {
      try {
        const result = await fetcherRef.current()
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err as Error)
      }
    }

    tick()
    const timer = setInterval(tick, intervalMs)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [intervalMs, active])

  return { data, error }
}
