// Live event stream (SSE). Every animation in AURORA is driven by these real, persisted events.
import { useEffect, useRef, useState } from 'react';
import { API_BASE, api, ensureToken, resetToken } from './api';
import type { ActivityEvent } from './types';

type Listener = (event: ActivityEvent) => void;
const listeners = new Set<Listener>();
let source: EventSource | null = null;
let retryTimer: number | null = null;
let connected = false;
// True while open() awaits the session token: several components subscribe at startup, and
// without this guard each of them would create its own EventSource (leaked, duplicate events).
let opening = false;
const statusListeners = new Set<(ok: boolean) => void>();
// Highest event id received: a reconnection asks the server to replay what came after it
// (?since_id=, main.py sse_stream). Ids already delivered are dropped, so a replay never
// hands the same event twice to the listeners.
let lastId = 0;
const MAX_SEEN = 2000;
const seen = new Set<number>();
const seenOrder: number[] = [];

function firstDelivery(id: unknown): boolean {
  if (typeof id !== 'number' || !Number.isFinite(id)) return true;
  if (seen.has(id)) return false;
  seen.add(id);
  seenOrder.push(id);
  if (seenOrder.length > MAX_SEEN) seen.delete(seenOrder.shift()!);
  if (id > lastId) lastId = id;
  return true;
}

function setConnected(ok: boolean) {
  if (connected === ok) return;
  connected = ok;
  statusListeners.forEach(fn => fn(ok));
}

async function open() {
  if (source || opening || listeners.size === 0) return;
  opening = true;
  try {
    const token = await ensureToken();
    // Everyone may have unsubscribed while the token was being fetched.
    if (source || listeners.size === 0) return;
    const since = lastId > 0 ? `&since_id=${lastId}` : '';
    const es = new EventSource(`${API_BASE}/api/stream?token=${encodeURIComponent(token)}${since}`);
    source = es;
    es.addEventListener('open', () => {
      setConnected(true);
      // Nothing received yet: the latest persisted id becomes the replay point, so the events
      // emitted while a later reconnection is pending are replayed too.
      if (lastId === 0) {
        void api.events({ limit: 1 }).then(list => {
          const id = list[0]?.id;
          if (typeof id === 'number' && id > lastId) lastId = id;
        }).catch(() => undefined);
      }
    });
    es.addEventListener('aurora', (msg) => {
      try {
        const event = JSON.parse((msg as MessageEvent).data) as ActivityEvent;
        // B4: live text frames are never stored and all carry id 0: they skip the « already seen » filter.
        if (!event?.transient && !firstDelivery(event?.id)) return;
        listeners.forEach(fn => fn(event));
      } catch { /* trame invalide ignorée */ }
    });
    es.addEventListener('error', () => {
      es.close();
      if (source !== es) return; // already replaced or closed by the last unsubscribe
      setConnected(false);
      source = null;
      // After a backend restart the cached token is refused (401) and only POSTs would renew
      // it: always reopen with a token freshly read from /api/bootstrap.
      resetToken();
      scheduleRetry();
    });
  } catch {
    setConnected(false);
    scheduleRetry();
  } finally {
    opening = false;
  }
}

function scheduleRetry() {
  if (retryTimer !== null || listeners.size === 0) return;
  retryTimer = window.setTimeout(() => { retryTimer = null; void open(); }, 3000);
}

/** Subscribe to every live event. The callback is kept fresh without resubscribing. */
export function useEventStream(onEvent: Listener): void {
  const ref = useRef(onEvent);
  ref.current = onEvent;
  useEffect(() => {
    const fn: Listener = e => ref.current(e);
    listeners.add(fn);
    void open();
    return () => {
      listeners.delete(fn);
      if (listeners.size === 0) { source?.close(); source = null; setConnected(false); }
    };
  }, []);
}

/** True while the SSE channel is open. */
export function useStreamStatus(): boolean {
  const [ok, setOk] = useState(connected);
  useEffect(() => {
    statusListeners.add(setOk);
    setOk(connected);   // the channel may have opened between the render and this subscription
    return () => { statusListeners.delete(setOk); };
  }, []);
  return ok;
}
