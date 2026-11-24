// Real run data for the task flow: the run list (api.runs), each run's events (api.run(id)),
// and the live SSE stream merged in, deduplicated by event id. Nothing here executes anything.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import { useEventStream } from '../../lib/stream';
import type { ActivityEvent, RunDetail, TaskItem } from '../../lib/types';
import { flowConfig } from './config';
import { buildJourney, isTerminal, type Journey, type RunStatus } from './model';

export interface RunData {
  summaries: TaskItem[];
  journeys: Map<number, Journey>;
  details: Map<number, RunDetail>;
  /** Bumped on every change; use it as a memo dependency. */
  version: number;
  loading: boolean;
  error: string;
  refresh: () => void;
  refreshRun: (runId: number) => void;
}

const isManual = (t: TaskItem): boolean => (t.kind ?? 'manual') === 'manual';

export function useRunJourneys(): RunData {
  const [summaries, setSummaries] = useState<TaskItem[]>([]);
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const summaryById = useRef(new Map<number, TaskItem>());
  const eventsByRun = useRef(new Map<number, Map<number, ActivityEvent>>());
  const journeys = useRef(new Map<number, Journey>());
  const details = useRef(new Map<number, RunDetail>());
  const settled = useRef(new Set<number>());
  const queue = useRef<number[]>([]);
  const active = useRef(0);
  const alive = useRef(true);
  const runsTimer = useRef<number | null>(null);
  const runTimers = useRef(new Map<number, number>());

  const bump = useCallback(() => setVersion(v => v + 1), []);

  const rebuild = useCallback((runId: number) => {
    const events = [...(eventsByRun.current.get(runId)?.values() ?? [])];
    journeys.current.set(runId, buildJourney(runId, events, summaryById.current.get(runId)));
  }, []);

  const mergeEvents = useCallback((runId: number, events: readonly ActivityEvent[]) => {
    let map = eventsByRun.current.get(runId);
    if (!map) { map = new Map(); eventsByRun.current.set(runId, map); }
    events.forEach(e => { if (typeof e.id === 'number') map?.set(e.id, e); });
  }, []);

  // Small pool so a long list does not open hundreds of requests at once.
  const pump = useCallback(() => {
    while (alive.current && active.current < flowConfig.data.detailConcurrency && queue.current.length) {
      const runId = queue.current.shift();
      if (runId === undefined) break;
      active.current += 1;
      api.run(runId)
        .then(detail => {
          if (!alive.current) return;
          details.current.set(runId, detail);
          if (detail.task) summaryById.current.set(runId, { ...summaryById.current.get(runId), ...detail.task });
          mergeEvents(runId, detail.events ?? []);
          rebuild(runId);
          const status = (detail.task?.status ?? 'running') as RunStatus;
          if (isTerminal(status)) settled.current.add(runId);
          bump();
        })
        .catch(() => {
          // Detail unavailable: the run keeps its summary, without a journey on the figure.
          if (alive.current && !journeys.current.has(runId)) { rebuild(runId); bump(); }
        })
        .finally(() => { active.current -= 1; pump(); });
    }
  }, [bump, mergeEvents, rebuild]);

  const enqueue = useCallback((runId: number) => {
    if (!queue.current.includes(runId)) queue.current.push(runId);
    pump();
  }, [pump]);

  const loadRuns = useCallback(async () => {
    try {
      const list = (await api.runs(flowConfig.data.runsLimit)).filter(t => !isManual(t));
      if (!alive.current) return;
      list.forEach(t => summaryById.current.set(t.id, t));
      setSummaries(list);
      setError('');
      list.forEach(t => {
        if (!settled.current.has(t.id)) enqueue(t.id);
        else rebuild(t.id);
      });
      bump();
    } catch (err) {
      if (alive.current) setError(err instanceof Error ? err.message : 'Service local indisponible');
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [bump, enqueue, rebuild]);

  const scheduleRuns = useCallback(() => {
    if (runsTimer.current !== null) window.clearTimeout(runsTimer.current);
    runsTimer.current = window.setTimeout(() => { runsTimer.current = null; void loadRuns(); }, flowConfig.live.refreshDebounceMs);
  }, [loadRuns]);

  const refreshRun = useCallback((runId: number) => {
    const prev = runTimers.current.get(runId);
    if (prev !== undefined) window.clearTimeout(prev);
    runTimers.current.set(runId, window.setTimeout(() => {
      runTimers.current.delete(runId);
      settled.current.delete(runId);
      enqueue(runId);
    }, flowConfig.live.refreshDebounceMs));
  }, [enqueue]);

  useEffect(() => {
    alive.current = true;
    void loadRuns();
    const timers = runTimers.current;
    return () => {
      alive.current = false;
      if (runsTimer.current !== null) window.clearTimeout(runsTimer.current);
      timers.forEach(t => window.clearTimeout(t));
      timers.clear();
    };
  }, [loadRuns]);

  useEventStream(event => {
    const runId = event.run_id;
    if (runId === null || runId === undefined) return;
    mergeEvents(runId, [event]);
    if (!summaryById.current.has(runId)) scheduleRuns();
    rebuild(runId);
    if (event.kind.startsWith('run.') || event.kind.startsWith('permission.')) {
      scheduleRuns();
      if (details.current.has(runId)) refreshRun(runId);
    }
    bump();
  });

  return {
    summaries,
    journeys: journeys.current,
    details: details.current,
    version,
    loading,
    error,
    refresh: () => { void loadRuns(); },
    refreshRun,
  };
}
