// P2 « Pages »: which page the viewer shows, shared by every « Faire une page » button and the list
// through a tiny external store (same pattern as chat/reports.ts). The viewer is mounted once in App.tsx;
// `changes` counts creations and deletions so the lists read the service again.
import { useSyncExternalStore } from 'react';

/** The page to show (its latest version when `version` is null); `seq` changes at each opening. */
export interface OpenArtefact { id: string; version: number | null; seq: number }

let open: OpenArtefact | null = null;
let seq = 0;
let changes = 0;
let returnTo: HTMLElement | null = null;
const subs = new Set<() => void>();
const notify = () => subs.forEach(fn => fn());
const subscribe = (fn: () => void) => { subs.add(fn); return () => { subs.delete(fn); }; };

/** Show a page in the viewer; the focus comes back to what opened it on close. */
export function openArtefact(id: string, version?: number): void {
  if (!open && document.activeElement instanceof HTMLElement) returnTo = document.activeElement;
  seq += 1;
  open = { id, version: version ?? null, seq };
  notify();
}

export function closeArtefact(): void {
  if (!open) return;
  open = null;
  notify();
  const back = returnTo;
  returnTo = null;
  if (back?.isConnected) back.focus();
}

/** A page was made or deleted. */
export function artefactsChanged(): void {
  changes += 1;
  notify();
}

export function useOpenArtefact(): OpenArtefact | null {
  return useSyncExternalStore(subscribe, () => open, () => null);
}

/** Grows at each creation or deletion: a dependency for the lists to reload. */
export function useArtefactChanges(): number {
  return useSyncExternalStore(subscribe, () => changes, () => 0);
}
