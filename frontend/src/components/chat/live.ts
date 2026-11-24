// Live run tracking from the SSE stream. Every component that shows progress feeds the same
// module-level buffer (deduplicated by event id), so the command bar, the drawer and the chat
// page agree on which run belongs to which pending message.
import { useSyncExternalStore } from 'react';
import { useEventStream } from '../../lib/stream';
import type { ActivityEvent } from '../../lib/types';
import { textKey } from './text';

interface CreatedRun { runId: number; text: string; source: string; seenAt: number }

const MAX_SEEN = 4000;
const MAX_RUNS = 80;
const MAX_EVENTS_PER_RUN = 400;
export const TERMINAL_KINDS = new Set(['run.done', 'run.error', 'run.cancelled', 'run.interrupted']);

const seen = new Set<number>();
const seenOrder: number[] = [];
const byRun = new Map<number, ActivityEvent[]>();
const created: CreatedRun[] = [];
const claims = new Map<string, number>();
const claimedRuns = new Set<number>();
let version = 0;
const subs = new Set<() => void>();

function remember(id: number) {
  seen.add(id);
  seenOrder.push(id);
  if (seenOrder.length > MAX_SEEN) seen.delete(seenOrder.shift()!);
}

/** B4: the text of an answer while the model writes it (`llm.delta`, sent live, never stored: no id). */
const partials = new Map<number, string>();
/** 11/11/2026: the runs whose text the service allows to be said aloud while it is written (`speak: true` in the
 *  event; absent or false: shown only — an answer that may show a sensitive memory, or an older service). */
const speakable = new Set<number>();

export function ingest(ev: ActivityEvent): void {
  if (ev?.kind === 'llm.delta' && typeof ev.run_id === 'number') {   // the latest text wins
    partials.set(ev.run_id, String(ev.payload?.text ?? ''));
    if (ev.payload?.speak === true) speakable.add(ev.run_id); else speakable.delete(ev.run_id);
    if (partials.size > MAX_RUNS) { const oldest = partials.keys().next().value as number; partials.delete(oldest); speakable.delete(oldest); }
    version++;
    subs.forEach(fn => fn());
    return;
  }
  if (TERMINAL_KINDS.has(ev?.kind) && typeof ev.run_id === 'number') { partials.delete(ev.run_id); speakable.delete(ev.run_id); }
  if (typeof ev?.id !== 'number' || seen.has(ev.id)) return;
  remember(ev.id);
  if (ev.run_id === null || ev.run_id === undefined) return;
  let list = byRun.get(ev.run_id);
  if (!list) {
    list = [];
    byRun.set(ev.run_id, list);
    if (byRun.size > MAX_RUNS) {
      const oldest = byRun.keys().next().value;
      if (oldest !== undefined) byRun.delete(oldest);
    }
  }
  list.push(ev);
  if (list.length > 1 && list[list.length - 2].id > ev.id) list.sort((a, b) => a.id - b.id);
  if (list.length > MAX_EVENTS_PER_RUN) list.splice(0, list.length - MAX_EVENTS_PER_RUN);
  if (ev.kind === 'run.created') {
    created.push({ runId: ev.run_id, text: textKey(ev.payload?.text), source: String(ev.payload?.source ?? ''), seenAt: Date.now() });
    if (created.length > MAX_RUNS) created.shift();
  }
  version++;
  subs.forEach(fn => fn());
}

const subscribe = (fn: () => void) => { subs.add(fn); return () => { subs.delete(fn); }; };

/** run.created carries text[:2000] (tasks/runs.py): compare user texts on the same cut, in code points as Python does. */
const RUN_TEXT_MAX = 2000;
const runTextKey = (s: string) => textKey(Array.from(s.trim()).slice(0, RUN_TEXT_MAX).join(''));

/**
 * The single SSE listener feeding this buffer. Mounted once, permanently (by ChatDrawer, always
 * present in the shell), so the frames replayed right after a reconnection (since_id) are not
 * missed; lib/stream.ts guards against duplicate EventSources while it is connecting.
 */
export function LiveFeedBridge(): null {
  useEventStream(ingest);
  return null;
}

/**
 * Re-render the calling component on every new run event (fed by LiveFeedBridge) while
 * `active`; inactive callers keep a constant snapshot and are not re-rendered.
 */
export function useLiveFeed(active = true): number {
  return useSyncExternalStore(subscribe, () => (active ? version : -1), () => -1);
}

export function runEvents(runId: number | null | undefined): ActivityEvent[] {
  if (runId === null || runId === undefined) return [];
  return byRun.get(runId) ?? [];
}

export function lastEventId(runId: number | null | undefined): number {
  const list = runEvents(runId);
  return list.length ? list[list.length - 1].id : 0;
}

/**
 * Bind a pending message (key) to the run whose `run.created` payload has the same text and
 * arrived after the message was sent. The binding is stable once made.
 */
export function claimRun(key: string, text: string, sentAtMs: number): number | null {
  const existing = claims.get(key);
  if (existing !== undefined) return existing;
  const k = runTextKey(text);
  if (!k) return null;
  const since = Number.isFinite(sentAtMs) ? sentAtMs - 2000 : 0;
  const pick = (exact: boolean) => created.find(c => !claimedRuns.has(c.runId) && c.seenAt >= since
    && (exact ? c.text === k : c.text.toLowerCase() === k.toLowerCase()));
  const found = pick(true) ?? pick(false);
  if (!found) return null;
  claims.set(key, found.runId);
  claimedRuns.add(found.runId);
  return found.runId;
}

/** B4: what the model has written so far for this run ('' when nothing or once the run ended). */
export function partialText(runId: number | null | undefined): string {
  return runId === null || runId === undefined ? '' : partials.get(runId) ?? '';
}

/** 11/11/2026: true when the service allowed the text being written for this run to be said aloud right away. */
export function partialSpeakable(runId: number | null | undefined): boolean {
  return runId !== null && runId !== undefined && speakable.has(runId);
}

export function isTerminal(runId: number): boolean {
  return runEvents(runId).some(e => TERMINAL_KINDS.has(e.kind));
}

/** True when the last permission request of the run has not been answered yet. */
export function isWaitingPermission(runId: number): boolean {
  const list = runEvents(runId);
  for (let i = list.length - 1; i >= 0; i--) {
    const k = list[i].kind;
    if (k === 'permission.granted' || k === 'permission.denied' || k === 'run.resumed') return false;
    if (k === 'permission.requested') return true;
  }
  return false;
}

/**
 * Most recent run still executing whose text was sent from this window (`texts` = user
 * messages of the current conversation). Runs waiting for a permission are not included.
 */
export function activeRunId(texts: string[]): number | null {
  const keys = new Set(texts.map(runTextKey).filter(Boolean));
  for (let i = created.length - 1; i >= 0; i--) {
    const c = created[i];
    if (!keys.has(c.text)) continue;
    if (!isTerminal(c.runId) && !isWaitingPermission(c.runId)) return c.runId;
  }
  return null;
}
