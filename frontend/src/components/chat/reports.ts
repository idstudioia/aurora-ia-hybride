// « Signaler » (H3): the reports already made, shared by every answer card and by Paramètres › Qualité
// through a tiny external store (same pattern as prefs.ts). The list is read once (one request, not
// one per card); each save or cancel updates it in place.
import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../../lib/api';
import type { RunReport } from '../../lib/types';

let byRun = new Map<number, RunReport>();
let loading: Promise<void> | null = null;
let loaded = false;
const subs = new Set<() => void>();
const notify = () => subs.forEach(fn => fn());
const subscribe = (fn: () => void) => { subs.add(fn); return () => { subs.delete(fn); }; };

/** Read the reports from the service (once; again with force, e.g. after a list refresh). */
export function loadReports(force = false): Promise<void> {
  if ((loaded && !force) || loading) return loading ?? Promise.resolve();
  loading = api.reports()
    .then(list => { byRun = new Map(list.map(r => [r.run_id, r])); loaded = true; notify(); })
    .catch(() => undefined)   // service offline: the button still works, the state comes with the next save
    .finally(() => { loading = null; });
  return loading;
}

/** A report saved (or null: cancelled) for a run. */
export function setRunReport(runId: number, report: RunReport | null): void {
  const next = new Map(byRun);
  if (report) next.set(runId, report); else next.delete(runId);
  byRun = next;
  notify();
}

/** The report of a run, if any (undefined until the list is read). */
export function useRunReport(runId: number): RunReport | undefined {
  useEffect(() => { void loadReports(); }, []);
  return useSyncExternalStore(subscribe, () => byRun.get(runId), () => undefined);
}
