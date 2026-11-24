// Studio: one shared copy of GET /api/studio for the Studio page and the home card, through a tiny
// external store (same pattern as artefacts/store.ts). One request at a time, at most one every
// 10 minutes (the service caches as long); « Actualiser » sends refresh=1, which empties its caches.
import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../../lib/api';
import type { StudioData } from '../../lib/types';

export interface StudioStore {
  data: StudioData | null;
  loading: boolean;
  /** Last failed request (the last good data is kept and still shown). */
  error: string;
}

const MAX_AGE = 10 * 60_000;
const RETRY_AFTER_ERROR = 60_000;

let state: StudioStore = { data: null, loading: false, error: '' };
let attemptAt = 0;
let seq = 0;
let inflight: Promise<void> | null = null;
const subs = new Set<() => void>();
const set = (next: Partial<StudioStore>) => { state = { ...state, ...next }; subs.forEach(fn => fn()); };
const subscribe = (fn: () => void) => { subs.add(fn); return () => { subs.delete(fn); }; };

/** Reads the service when the copy is stale (or always with `refresh`). */
export function loadStudio(refresh = false): Promise<void> {
  if (!refresh) {
    if (inflight) return inflight;
    const age = Date.now() - attemptAt;
    if (attemptAt && age < (state.error ? RETRY_AFTER_ERROR : MAX_AGE)) return Promise.resolve();
  }
  const id = ++seq;
  attemptAt = Date.now();
  set({ loading: true });
  const p = api.studio(refresh)
    .then(data => { if (id === seq) set({ data, error: '', loading: false }); })
    .catch((err: unknown) => { if (id === seq) set({ error: err instanceof Error ? err.message : 'Service local indisponible', loading: false }); })
    .finally(() => { if (inflight === p) inflight = null; });
  inflight = p;
  return p;
}

/** Paramètres › Studio changed: the next reading goes to the service again (no request from here). */
export function markStudioStale(): void { attemptAt = 0; }

/** The shared copy; loads it on mount and checks its age every minute while the tab is visible. */
export function useStudio(enabled: boolean): StudioStore & { refresh: () => void } {
  const snap = useSyncExternalStore(subscribe, () => state, () => state);
  useEffect(() => {
    if (!enabled) return;
    void loadStudio();
    const t = window.setInterval(() => { if (document.visibilityState === 'visible') void loadStudio(); }, 60_000);
    return () => window.clearInterval(t);
  }, [enabled]);
  return { ...snap, refresh: () => { void loadStudio(true); } };
}
