// Small hooks used by the memory page. (prefers-reduced-motion: reused from ../sim/hooks.)
import { useCallback, useEffect, useRef } from 'react';

/** Trailing-edge debounce of `fn` (latest closure is always used). */
export function useDebounced(fn: () => void, ms: number): () => void {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current !== null) window.clearTimeout(timer.current); }, []);
  return useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { timer.current = null; fnRef.current(); }, ms);
  }, [ms]);
}

export const errorText = (err: unknown, fallback = 'Service local indisponible') =>
  err instanceof Error && err.message ? err.message : fallback;
