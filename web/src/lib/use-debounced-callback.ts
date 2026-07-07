import { useCallback, useEffect, useRef } from 'react'

/** A stable debounced wrapper around `fn`, plus a canceller.
 *
 * `run(...)` schedules `fn(...)` after `delayMs`, replacing any pending call;
 * `cancel()` drops the pending call (used when an external navigation adopts
 * a new URL value mid-debounce). The latest `fn` is always invoked, and any
 * pending call is cancelled on unmount.
 */
export function useDebouncedCallback<A extends unknown[]>(
  fn: (...args: A) => void,
  delayMs: number,
): { run: (...args: A) => void; cancel: () => void } {
  const fnRef = useRef(fn)
  useEffect(() => {
    fnRef.current = fn
  })

  const timerRef = useRef<number | undefined>(undefined)

  const cancel = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current)
      timerRef.current = undefined
    }
  }, [])

  const run = useCallback(
    (...args: A) => {
      cancel()
      timerRef.current = window.setTimeout(() => {
        timerRef.current = undefined
        fnRef.current(...args)
      }, delayMs)
    },
    [cancel, delayMs],
  )

  useEffect(() => cancel, [cancel])

  return { run, cancel }
}
