// Shared `prefers-reduced-motion` helpers (one source for the globe, brain, simulations and home).
import { useEffect, useState } from 'react';

const QUERY = '(prefers-reduced-motion: reduce)';

/** Current value, safe outside the browser. */
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.(QUERY).matches;
}

/** Live flag: true when the user asked the OS to reduce motion. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);
  useEffect(() => {
    const mq = window.matchMedia?.(QUERY);
    if (!mq) return;
    const onChange = () => setReduced(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return reduced;
}
