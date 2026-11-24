// Replay of persisted events. It only re-reads the event journal (GET /api/events) and
// animates it in order: it never calls an endpoint that executes anything.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import type { ActivityEvent } from '../../lib/types';
import { classifyEvent, isBrainEvent, runKey } from './model';

export type Speed = 1 | 4 | 16;
export const SPEEDS: Speed[] = [1, 4, 16];

const MIN_GAP_MS = 260;
const MAX_GAP_MS = 1800;

/** Memory + run events of the journal, oldest first. */
export async function loadBrainEvents(limit = 500): Promise<ActivityEvent[]> {
  const list = await api.events({ limit });
  return sortBrainEvents(list);
}

export function sortBrainEvents(list: ActivityEvent[]): ActivityEvent[] {
  return list.filter(isBrainEvent).sort((a, b) => a.id - b.id);
}

/** Real time gap between two events, compressed into [MIN, MAX] then divided by the speed. */
export function replayDelay(a: ActivityEvent, b: ActivityEvent, speed: number): number {
  const gap = Date.parse(b.created_at) - Date.parse(a.created_at);
  const clamped = Number.isFinite(gap) ? Math.min(MAX_GAP_MS, Math.max(MIN_GAP_MS, gap)) : MIN_GAP_MS;
  return Math.max(30, clamped / speed);
}

/** Run statuses implied by events[0..index] (keyed "run#<id>"). */
export function statusesAt(events: ActivityEvent[], index: number): Map<string, string> {
  const map = new Map<string, string>();
  for (let i = 0; i <= index && i < events.length; i++) {
    const be = classifyEvent(events[i]);
    if (be?.runId != null && be.status) map.set(runKey(be.runId), be.status);
  }
  return map;
}

/** Time cursor: nodes created after it are hidden during the replay. */
export function cutoffAt(events: ActivityEvent[], index: number): number | null {
  if (!events.length) return null;
  const e = index >= 0 ? events[Math.min(index, events.length - 1)] : events[0];
  const t = Date.parse(e.created_at);
  if (!Number.isFinite(t)) return null;
  return index >= 0 ? t : t - 1;
}

export interface ReplayControls {
  active: boolean;
  events: ActivityEvent[];
  index: number;
  playing: boolean;
  speed: Speed;
  current: ActivityEvent | null;
  start: (events: ActivityEvent[]) => void;
  pause: () => void;
  resume: () => void;
  seek: (index: number, events?: ActivityEvent[]) => void;
  setSpeed: (s: Speed) => void;
  stop: () => void;
}

/** Replay state machine; `onStep` animates one recorded event. */
export function useReplay(onStep: (event: ActivityEvent, speed: Speed) => void): ReplayControls {
  const [active, setActive] = useState(false);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [index, setIndex] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(1);
  const stepRef = useRef(onStep);
  stepRef.current = onStep;

  useEffect(() => {
    if (!playing || !active) return;
    if (index >= events.length - 1) { setPlaying(false); return; }
    const delay = index < 0 ? 450 / speed : replayDelay(events[index], events[index + 1], speed);
    const timer = window.setTimeout(() => {
      const next = index + 1;
      setIndex(next);
      stepRef.current(events[next], speed);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [playing, active, index, events, speed]);

  const start = useCallback((list: ActivityEvent[]) => {
    setEvents(list); setIndex(-1); setActive(true); setPlaying(list.length > 0);
  }, []);
  const pause = useCallback(() => setPlaying(false), []);
  const resume = useCallback(() => {
    setActive(true);
    setIndex(i => (i >= events.length - 1 ? -1 : i));
    setPlaying(events.length > 0);
  }, [events.length]);
  const seek = useCallback((i: number, list?: ActivityEvent[]) => {
    const source = list ?? events;
    if (!source.length) return;
    const idx = Math.max(0, Math.min(source.length - 1, i));
    if (list) setEvents(list);
    setActive(true); setPlaying(false); setIndex(idx);
    stepRef.current(source[idx], speed);
  }, [events, speed]);
  const stop = useCallback(() => { setActive(false); setPlaying(false); setIndex(-1); }, []);

  const current = active && index >= 0 ? events[index] ?? null : null;
  return { active, events, index, playing, speed, current, start, pause, resume, seek, setSpeed, stop };
}
