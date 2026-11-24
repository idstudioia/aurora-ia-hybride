// Adapté de Request Flow Replay — Copyright (c) 2026 Cliff Pyles — licence MIT, voir third_party/request-flow-replay/LICENSE.txt
// The original model.ts simulated a day of traffic. Here nothing is simulated: every journey,
// count and duration is derived from the real events AURORA persisted (CONTRACT §3).
// Pure; no DOM, no network.

import type { ActivityEvent, TaskItem } from '../../lib/types';

// ----------------------------------------
// Stations (columns of the figure)
// ----------------------------------------
export type StationId = 'input' | 'decision' | 'tool' | 'local_ai' | 'cloud_ai' | 'permission' | 'verify' | 'response' | 'memory';

export interface StationDef {
  id: StationId;
  column: number;
  /** Order used to stack ribbons at a node (sources and targets). */
  order: number;
  colorVar: string;
}

export const STATIONS: readonly StationDef[] = [
  { id: 'input', column: 0, order: 0, colorVar: '--st-input' },
  { id: 'decision', column: 1, order: 1, colorVar: '--st-decision' },
  { id: 'tool', column: 2, order: 2, colorVar: '--st-tool' },
  { id: 'local_ai', column: 2, order: 3, colorVar: '--st-local' },
  { id: 'cloud_ai', column: 2, order: 4, colorVar: '--st-cloud' },
  { id: 'permission', column: 2, order: 5, colorVar: '--st-permission' },
  { id: 'verify', column: 3, order: 6, colorVar: '--st-verify' },
  { id: 'response', column: 4, order: 7, colorVar: '--st-response' },
  { id: 'memory', column: 5, order: 8, colorVar: '--st-memory' },
];

export const stationById = new Map<StationId, StationDef>(STATIONS.map(s => [s.id, s]));
export const isStation = (stage: string | null | undefined): stage is StationId => !!stage && stationById.has(stage as StationId);
export const linkKey = (source: StationId, target: StationId): string => `${source}>${target}`;

// ----------------------------------------
// Status
// ----------------------------------------
export type RunStatus = 'pending' | 'running' | 'waiting_permission' | 'paused' | 'done' | 'error' | 'cancelled' | 'interrupted';
export const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set<RunStatus>(['done', 'error', 'cancelled', 'interrupted']);
export const isTerminal = (s: RunStatus): boolean => TERMINAL_STATUSES.has(s);

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  pending: 'En file', running: 'En cours', waiting_permission: "En attente d'autorisation", paused: 'En pause',
  done: 'Terminé', error: 'Erreur', cancelled: 'Annulé', interrupted: 'Interrompu',
};

/** Colour role of a status, shared by dots, list rows and badges. */
export type StatusTone = 'cyan' | 'green' | 'red' | 'amber' | 'grey';
export const statusTone = (s: RunStatus): StatusTone => {
  if (s === 'done') return 'green';
  if (s === 'error') return 'red';
  if (s === 'waiting_permission' || s === 'paused') return 'amber';
  if (s === 'cancelled' || s === 'interrupted') return 'grey';
  return 'cyan';
};

const asStatus = (value: string | null | undefined): RunStatus | null => {
  switch (value) {
    case 'pending': case 'running': case 'waiting_permission': case 'paused':
    case 'done': case 'error': case 'cancelled': case 'interrupted':
      return value;
    default:
      return null;
  }
};

/** Status after one event, from the event kinds of CONTRACT §3 (legacy ASTRA kinds included). */
export const statusAfter = (prev: RunStatus, kind: string): RunStatus => {
  switch (kind) {
    case 'run.created': case 'run.resumed': case 'permission.granted': case 'running': case 'task.running':
      return 'running';
    case 'permission.requested':
      return 'waiting_permission';
    case 'permission.denied':
      return prev === 'waiting_permission' ? 'running' : prev;
    case 'run.done': case 'done': case 'task.done':
      return 'done';
    case 'run.error': case 'error': case 'task.error':
      return 'error';
    case 'run.cancelled':
      return 'cancelled';
    case 'run.interrupted':
      return 'interrupted';
    default:
      return prev;
  }
};

// ----------------------------------------
// Journeys: segments (one per stage/step) and visits (consecutive segments at one station)
// ----------------------------------------
export type SegmentStatus = 'running' | 'done' | 'error' | 'skipped' | 'waiting' | 'granted' | 'denied' | 'passed' | 'failed' | 'ready' | 'cancelled' | 'interrupted' | '';

export interface Segment {
  key: string;
  stage: StationId;
  stepId: number | null;
  title: string;
  tool: string;
  model: string;
  provider: string;
  start: number;
  end: number;
  /** Real duration: duration_ms of the step/tool/model events when present, else elapsed wall time. */
  ms: number;
  measured: boolean;
  status: SegmentStatus;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  events: ActivityEvent[];
}

export interface Visit {
  stage: StationId;
  start: number;
  ms: number;
  /** Still in progress: last visit of a run that has not finished. */
  open: boolean;
  statusAfter: RunStatus;
  segments: Segment[];
}

export interface Journey {
  runId: number;
  title: string;
  intent: string;
  engine: string;
  status: RunStatus;
  createdAt: number;
  endAt: number | null;
  events: ActivityEvent[];
  segments: Segment[];
  visits: Visit[];
  costUsd: number;
  durationMs: number | null;
  /** True when the run's events were loaded (REST detail or live stream). */
  hasEvents: boolean;
}

export const toMs = (iso: string | null | undefined): number => {
  if (!iso) return Number.NaN;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : Number.NaN;
};

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const str = (v: unknown): string => (typeof v === 'string' ? v : v === null || v === undefined ? '' : String(v));
const stepIdOf = (e: ActivityEvent): number | null => {
  const raw = e.payload?.step_id;
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (e.subject_type === 'step' && e.subject_id) return e.subject_id;
  return null;
};

const segmentStatus = (kind: string, prev: SegmentStatus, payload: Record<string, any>): SegmentStatus => {
  switch (kind) {
    case 'step.started': return prev || 'running';
    case 'step.done': return 'done';
    case 'step.error': return 'error';
    case 'step.skipped': return 'skipped';
    case 'tool.result': return payload?.ok === false ? 'error' : prev === 'error' ? prev : 'done';
    case 'model.result': return prev === 'error' ? prev : 'done';
    case 'permission.requested': return 'waiting';
    case 'permission.granted': return 'granted';
    case 'permission.denied': return 'denied';
    case 'verify.passed': return prev === 'failed' ? prev : 'passed';
    case 'verify.failed': return 'failed';
    case 'response.ready': return 'ready';
    case 'run.done': return prev || 'done';
    case 'run.error': return 'error';
    case 'run.cancelled': return 'cancelled';
    case 'run.interrupted': return 'interrupted';
    default: return prev;
  }
};

const byOrder = (a: ActivityEvent, b: ActivityEvent): number => (a.id - b.id) || (toMs(a.created_at) - toMs(b.created_at));

/**
 * Build a run's journey from its events. `summary` (the tasks row) fills what events cannot
 * say; the fresher of the two decides the status.
 */
export function buildJourney(runId: number, rawEvents: readonly ActivityEvent[], summary?: TaskItem | null): Journey {
  const events = [...rawEvents].filter(e => e.run_id === runId || (e.run_id === null && e.subject_id === runId && e.subject_type === 'task')).sort(byOrder);
  const segments: Segment[] = [];
  const statusTrail: RunStatus[] = [];
  let status: RunStatus = 'pending';
  let current: Segment | null = null;
  let cost = 0;

  events.forEach(e => {
    status = statusAfter(status, e.kind);
    statusTrail.push(status);
    if (!isStation(e.stage)) return;
    const at = toMs(e.created_at);
    const stepId = stepIdOf(e);
    // An event without a step (verify.passed, response.ready, run.done…) joins the current
    // segment of its stage; a new step at the same stage opens a new segment.
    const key = stepId !== null ? `${e.stage}|${stepId}` : current && current.stage === e.stage ? current.key : `${e.stage}|`;
    if (!current || current.key !== key) {
      current = {
        key, stage: e.stage, stepId, title: '', tool: '', model: '', provider: '', start: at, end: at, ms: 0,
        measured: false, status: '', inputTokens: 0, outputTokens: 0, costUsd: 0, events: [],
      };
      segments.push(current);
    }
    const seg: Segment = current;
    seg.events.push(e);
    seg.end = Math.max(seg.end, at);
    const p = e.payload ?? {};
    if (!seg.title) seg.title = str(p.title) || e.label;
    if (!seg.tool && p.tool) seg.tool = str(p.tool);
    if (e.kind === 'model.result') {
      seg.model = str(p.model) || seg.model;
      seg.provider = str(p.provider) || seg.provider;
      seg.inputTokens += num(p.input_tokens);
      seg.outputTokens += num(p.output_tokens);
      seg.costUsd += num(p.cost_usd);
      cost += num(p.cost_usd);
    }
    seg.status = segmentStatus(e.kind, seg.status, p);
    // run.* durations cover the whole run, not this station: keep them out of the step's time.
    if (!e.kind.startsWith('run.')) {
      const d = e.duration_ms ?? (typeof p.ms === 'number' ? p.ms : null);
      if (typeof d === 'number' && Number.isFinite(d) && d >= 0) {
        seg.ms = seg.measured ? Math.max(seg.ms, d) : d;
        seg.measured = true;
      }
    }
  });

  // Unmeasured segments take their elapsed time up to the next segment.
  segments.forEach((s, i) => {
    if (!s.measured) {
      const next = segments[i + 1];
      s.ms = Math.max(0, (next ? next.start : s.end) - s.start);
    }
  });

  // Status as of the end of each segment, for dot colours.
  const statusAtEvent = new Map<number, RunStatus>(events.map((e, i) => [e.id, statusTrail[i] ?? 'running']));
  const lastStatusOf = (s: Segment): RunStatus => statusAtEvent.get(s.events[s.events.length - 1]?.id ?? -1) ?? 'running';

  const visits: Visit[] = [];
  segments.forEach(s => {
    const last = visits[visits.length - 1];
    if (last && last.stage === s.stage) {
      last.segments.push(s);
      last.ms += s.ms;
      last.statusAfter = lastStatusOf(s);
    } else {
      visits.push({ stage: s.stage, start: s.start, ms: s.ms, open: false, statusAfter: lastStatusOf(s), segments: [s] });
    }
  });

  // Freshest source wins for the run status.
  const derived = events.length ? status : null;
  const summaryStatus = asStatus(summary?.status);
  const lastEventAt = events.length ? toMs(events[events.length - 1]?.created_at) : Number.NaN;
  const summaryAt = toMs(summary?.updated_at);
  let finalStatus: RunStatus = derived ?? summaryStatus ?? 'pending';
  if (derived && summaryStatus && Number.isFinite(summaryAt) && (!Number.isFinite(lastEventAt) || summaryAt > lastEventAt + 50)) {
    finalStatus = summaryStatus;
  }
  const lastVisit = visits[visits.length - 1];
  if (lastVisit && !isTerminal(finalStatus) && finalStatus !== 'paused') lastVisit.open = true;
  if (lastVisit && isTerminal(finalStatus)) lastVisit.statusAfter = finalStatus;
  // "running" only holds for the segment still in progress; others never reported an end.
  const openSegment = lastVisit?.open ? lastVisit.segments[lastVisit.segments.length - 1] : undefined;
  segments.forEach(s => { if (s.status === 'running' && s !== openSegment) s.status = ''; });

  const created = events.find(e => e.kind === 'run.created');
  const createdAt = Number.isFinite(toMs(created?.created_at)) ? toMs(created?.created_at)
    : Number.isFinite(toMs(summary?.created_at)) ? toMs(summary?.created_at)
      : toMs(events[0]?.created_at);
  const terminalEvent = [...events].reverse().find(e => e.kind === 'run.done' || e.kind === 'run.error' || e.kind === 'run.cancelled' || e.kind === 'run.interrupted');
  const endAt = isTerminal(finalStatus)
    ? (Number.isFinite(toMs(terminalEvent?.created_at)) ? toMs(terminalEvent?.created_at)
      : Number.isFinite(toMs(summary?.finished_at)) ? toMs(summary?.finished_at) : lastEventAt)
    : null;
  const decided = events.find(e => e.kind === 'route.decided');

  return {
    runId,
    title: summary?.input_text || summary?.title || str(created?.payload?.text) || created?.label || `Requête n° ${runId}`,
    intent: summary?.intent || str(decided?.payload?.intent),
    engine: summary?.engine || str(decided?.payload?.engine),
    status: finalStatus,
    createdAt,
    endAt: endAt !== null && Number.isFinite(endAt) ? endAt : null,
    events,
    segments,
    visits,
    costUsd: summary && summary.cost_usd ? summary.cost_usd : cost,
    durationMs: summary?.duration_ms ?? (endAt !== null && Number.isFinite(endAt) && Number.isFinite(createdAt) ? endAt - createdAt : null),
    hasEvents: events.length > 0,
  };
}

/** Real duration of a visit; an open visit counts up with the wall clock. */
export const visitMs = (v: Visit, nowMs: number): number => (v.open ? Math.max(v.ms, nowMs - v.start) : v.ms);

/** Index of the visit a run is at, at real time `t` (-1 before it starts). */
export const visitIndexAt = (j: Journey, t: number): number => {
  let idx = -1;
  for (let i = 0; i < j.visits.length; i += 1) {
    const v = j.visits[i];
    if (v && v.start <= t) idx = i; else break;
  }
  return idx;
};

/** True while the run is in progress at real time `t`. */
export const inFlightAt = (j: Journey, t: number): boolean =>
  Number.isFinite(j.createdAt) && j.createdAt <= t && (j.endAt === null || t < j.endAt);

// ----------------------------------------
// Graph: passages per station and per link in a window
// ----------------------------------------
export interface StationStat { count: number; durations: number[]; inFlight: number }
export interface LinkStat { key: string; source: StationId; target: StationId; count: number }
export interface FlowGraph {
  stations: Map<StationId, StationStat>;
  links: Map<string, LinkStat>;
  runs: number;
  runsWithoutEvents: number;
}

export const emptyGraph = (): FlowGraph => ({
  stations: new Map(STATIONS.map(s => [s.id, { count: 0, durations: [], inFlight: 0 }])),
  links: new Map(),
  runs: 0,
  runsWithoutEvents: 0,
});

export const inWindow = (j: Journey, from: number, to: number): boolean =>
  Number.isFinite(j.createdAt) && j.createdAt >= from && j.createdAt <= to;

/**
 * Count real passages. `until` keeps only visits started by then (the replay's progress);
 * runs in flight at `until` are counted at their current station.
 */
export function buildGraph(journeys: Iterable<Journey>, from: number, to: number, until = Number.POSITIVE_INFINITY): FlowGraph {
  const graph = emptyGraph();
  for (const j of journeys) {
    if (!inWindow(j, from, to)) continue;
    graph.runs += 1;
    if (!j.hasEvents || j.visits.length === 0) { graph.runsWithoutEvents += 1; continue; }
    let prev: Visit | null = null;
    for (const v of j.visits) {
      if (v.start > until) break;
      const stat = graph.stations.get(v.stage);
      if (stat) {
        stat.count += 1;
        if (!v.open) stat.durations.push(v.ms);
      }
      if (prev) {
        const key = linkKey(prev.stage, v.stage);
        const link = graph.links.get(key) ?? { key, source: prev.stage, target: v.stage, count: 0 };
        link.count += 1;
        graph.links.set(key, link);
      }
      prev = v;
    }
    const at = Number.isFinite(until) ? until : Date.now();
    if (inFlightAt(j, at)) {
      const idx = visitIndexAt(j, at);
      const here = j.visits[idx];
      const stat = here ? graph.stations.get(here.stage) : undefined;
      if (stat) stat.inFlight += 1;
    }
  }
  return graph;
}

// ----------------------------------------
// Statistics on real runs
// ----------------------------------------
/** Linear-interpolated quantile of an ascending list; null when empty. */
export const quantile = (sorted: readonly number[], q: number): number | null => {
  if (sorted.length === 0) return null;
  const pos = (sorted.length - 1) * Math.min(1, Math.max(0, q));
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  const a = sorted[lo] ?? 0;
  const b = sorted[hi] ?? a;
  return a + (b - a) * (pos - lo);
};

export interface DayStats {
  runsToday: number;
  finished: number;
  succeeded: number;
  successRate: number | null;
  p50: number | null;
  p95: number | null;
  inProgress: number;
}

export const startOfToday = (now = Date.now()): number => {
  const d = new Date(now);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
};

export function dayStats(runs: readonly TaskItem[], now = Date.now()): DayStats {
  const from = startOfToday(now);
  const today = runs.filter(r => toMs(r.created_at) >= from);
  const finished = today.filter(r => r.status === 'done' || r.status === 'error' || r.status === 'cancelled' || r.status === 'interrupted');
  const succeeded = finished.filter(r => r.status === 'done').length;
  const durations = finished.map(r => r.duration_ms).filter((d): d is number => typeof d === 'number' && Number.isFinite(d)).sort((a, b) => a - b);
  return {
    runsToday: today.length,
    finished: finished.length,
    succeeded,
    successRate: finished.length ? succeeded / finished.length : null,
    p50: quantile(durations, 0.5),
    p95: quantile(durations, 0.95),
    inProgress: today.filter(r => r.status === 'running' || r.status === 'pending' || r.status === 'waiting_permission').length,
  };
}

// ----------------------------------------
// Time helpers for the transport
// ----------------------------------------
export interface SlowWindow { from: number; to: number }

/** Real activity spans (merged), where the playback slows to show what happened. */
export function activityWindows(journeys: Iterable<Journey>, from: number, to: number, padMs: number, nowMs: number): SlowWindow[] {
  const spans: SlowWindow[] = [];
  for (const j of journeys) {
    if (!inWindow(j, from, to)) continue;
    const end = j.endAt ?? Math.min(nowMs, to);
    spans.push({ from: j.createdAt - padMs, to: end + padMs });
  }
  spans.sort((a, b) => a.from - b.from);
  return spans.reduce<SlowWindow[]>((acc, s) => {
    const prev = acc[acc.length - 1];
    if (prev && s.from <= prev.to) prev.to = Math.max(prev.to, s.to);
    else acc.push({ ...s });
    return acc;
  }, []);
}

/** Sorted distinct times of every station event in the window (for ←/→ steps). */
export function eventTimes(journeys: Iterable<Journey>, from: number, to: number): number[] {
  const times = new Set<number>();
  for (const j of journeys) {
    if (!inWindow(j, from, to)) continue;
    j.visits.forEach(v => v.segments.forEach(s => s.events.forEach(e => {
      const t = toMs(e.created_at);
      if (Number.isFinite(t)) times.add(t);
    })));
  }
  return [...times].sort((a, b) => a - b);
}

/** Sorted visit start times (progress steps of the replay overlay). */
export function visitStarts(journeys: Iterable<Journey>, from: number, to: number): number[] {
  const out: number[] = [];
  for (const j of journeys) {
    if (!inWindow(j, from, to)) continue;
    j.visits.forEach(v => out.push(v.start));
  }
  return out.sort((a, b) => a - b);
}

/** Number of values ≤ t in an ascending list. */
export const countUpTo = (sorted: readonly number[], t: number): number => {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((sorted[mid] ?? 0) <= t) lo = mid + 1; else hi = mid;
  }
  return lo;
};
