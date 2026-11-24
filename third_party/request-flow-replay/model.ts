// Intended use: the traffic simulation. Steps the platform minute by minute through
// one day, carrying each client's share through every hop, queueing at saturated
// stores, and feeding failures back as retries. Pure; no DOM.

import { config } from './config.js';
import {
  actualLevers, batchJobs, clientIds, clients, events, nodes, scenario, serviceMix, storeErrors, storeMix,
  type ClientId, type Levers, type NodeId, type OutcomeId, type PlatformNode, type ServiceId, type StoreId,
} from './data.js';

// ----------------------------------------
// Topology
// ----------------------------------------
export interface FlowLink {
  index: number;
  source: NodeId;
  target: NodeId;
}

export const minutesInDay = 1440;
export const clientCount = clientIds.length;
const serviceIds: readonly ServiceId[] = ['auth', 'catalog', 'search', 'checkout'];
const storeIds: readonly StoreId[] = ['redis', 'primary', 'replica', 'kafka'];
const outcomeIds: readonly OutcomeId[] = ['ok', 'clientErr', 'serverErr', 'timeout'];

const pairs: readonly [NodeId, NodeId][] = [
  ...clientIds.map((c): [NodeId, NodeId] => [c, clients[c].entry]),
  ['cdn', 'gateway'], ['cdn', 'edgeHit'], ['cdn', 'waf'], ['lb', 'gateway'],
  ...serviceIds.map((s): [NodeId, NodeId] => ['gateway', s]),
  ['gateway', 'shed'],
  ...serviceIds.flatMap((s) => Object.keys(storeMix[s]).map((t): [NodeId, NodeId] => [s, t as StoreId])),
  ...storeIds.flatMap((s) => outcomeIds.map((o): [NodeId, NodeId] => [s, o])),
];

export const links: readonly FlowLink[] = pairs.map(([source, target], index) => ({ index, source, target }));
export const nodeIndex = new Map<NodeId, number>(nodes.map((n, i) => [n.id, i]));
export const nodeById = new Map<NodeId, PlatformNode>(nodes.map((n) => [n.id, n]));
const linkIndex = new Map<string, number>(links.map((l) => [`${l.source}>${l.target}`, l.index]));
export const linkIndexOf = (source: NodeId, target: NodeId): number | undefined => linkIndex.get(`${source}>${target}`);
export const outgoing = new Map<NodeId, FlowLink[]>(nodes.map((n) => [n.id, links.filter((l) => l.source === n.id)]));
export const incoming = new Map<NodeId, FlowLink[]>(nodes.map((n) => [n.id, links.filter((l) => l.target === n.id)]));

// ----------------------------------------
// Frame: the platform at one minute
// ----------------------------------------
export interface Frame {
  minute: number;
  flow: Float64Array;
  linkTotal: Float64Array;
  throughput: Float64Array;
  queue: Float64Array;
  waitMs: Float64Array;
  base: Float64Array;
  retry: Float64Array;
  rps: number;
  errorRate: number;
  burn: number;
  hitRate: number;
}

const emptyFrame = (minute: number): Frame => ({
  minute,
  flow: new Float64Array(links.length * clientCount),
  linkTotal: new Float64Array(links.length),
  throughput: new Float64Array(nodes.length),
  queue: new Float64Array(nodes.length),
  waitMs: new Float64Array(nodes.length),
  base: new Float64Array(clientCount),
  retry: new Float64Array(clientCount),
  rps: 0,
  errorRate: 0,
  burn: 0,
  hitRate: 0,
});

// ----------------------------------------
// Inputs over time
// ----------------------------------------
const smooth = (x: number): number => {
  const t = Math.min(1, Math.max(0, x));
  return t * t * (3 - 2 * t);
};

const hourly = (values: readonly number[], minute: number): number => {
  const h = (minute / 60) % 24;
  const i = Math.floor(h);
  const a = values[i] ?? 0;
  const b = values[(i + 1) % 24] ?? a;
  return a + (b - a) * ((1 - Math.cos(Math.PI * (h - i))) / 2);
};

const noise = (minute: number, seed: number): number => 1
  + config.model.hourlyNoise * (0.6 * Math.sin(minute / 37 + seed * 1.7) + 0.4 * Math.sin(minute / 11.3 + seed * 4.1));

const span = (minute: number, from: number, to: number): number => {
  const r = config.model.jobRampMinutes;
  return smooth((minute - from) / r) * (1 - smooth((minute - to) / r));
};

// ----------------------------------------
// Day options: which day is being replayed
// ----------------------------------------
export interface DayOptions {
  levers: Levers;
}

export const actualDay: DayOptions = { levers: actualLevers };

const manualShed = (o: DayOptions): boolean => o.levers.manualShed;

const jobRate = (minute: number, o: DayOptions): number => batchJobs
  .map((job) => job.rate * span(minute, job.start, job.end)
    * (job.pausedFrom === undefined || job.pausedTo === undefined || !manualShed(o) ? 1 : 1 - span(minute, job.pausedFrom, job.pausedTo)))
  .reduce((a, b) => a + b, 0);

export const baseRate = (client: ClientId, minute: number, o: DayOptions = actualDay): number => hourly(clients[client].hourly, minute)
  * noise(minute, clientIds.indexOf(client)) + (client === 'batch' ? jobRate(minute, o) : 0);

const hitRate = (minute: number, o: DayOptions): number => {
  const { at, hitRateFloor, warmupMinutes, rolloutMinutes } = scenario.deploy;
  const healthy = storeMix.catalog.redis ?? 1;
  const floor = o.levers.prewarm ? scenario.prewarmHitFloor : hitRateFloor;
  const since = minute - at;
  return since < 0 ? healthy : healthy - (healthy - floor) * smooth(since / rolloutMinutes) * Math.exp(-since / warmupMinutes);
};

const shedShare = (client: ClientId, minute: number, o: DayOptions, primaryWaitMs: number): number => {
  const { from, to, share, baseline } = scenario.shed;
  const manual = client === scenario.shed.client && manualShed(o) ? baseline + (share - baseline) * span(minute, from, to) : 0;
  const { fromWaitMs, fullWaitMs, maxShare } = scenario.adaptive;
  const adaptive = o.levers.adaptive ? maxShare * smooth((primaryWaitMs - fromWaitMs) / (fullWaitMs - fromWaitMs)) : 0;
  return 1 - (1 - manual) * (1 - adaptive);
};

// ----------------------------------------
// One minute of traffic
// ----------------------------------------
const step = (minute: number, prev: Frame, o: DayOptions): Frame => {
  const f = emptyFrame(minute);
  const add = (source: NodeId, target: NodeId, c: number, value: number): void => {
    const li = linkIndex.get(`${source}>${target}`);
    if (li !== undefined) f.flow[li * clientCount + c] = (f.flow[li * clientCount + c] ?? 0) + value;
  };
  const failRate = (c: number): number => {
    const total = (prev.base[c] ?? 0) + (prev.retry[c] ?? 0);
    const failed = ['serverErr', 'timeout'].flatMap((o) => incoming.get(o as NodeId) ?? [])
      .reduce((a, l) => a + (prev.flow[l.index * clientCount + c] ?? 0), 0);
    return total > 0 ? Math.min(0.95, failed / total) : 0;
  };
  const hit = hitRate(minute, o);
  const primaryWait = prev.waitMs[nodeIndex.get('primary') ?? 0] ?? 0;
  const storeShare = (s: ServiceId, t: StoreId): number => {
    if (s !== 'catalog') return storeMix[s][t] ?? 0;
    return t === 'redis' ? hit : 1 - hit;
  };

  clientIds.forEach((id, c) => {
    const client = clients[id];
    const base = baseRate(id, minute, o);
    const p = failRate(c);
    const wanted = base * Array.from({ length: client.retries }, (_, i) => p ** (i + 1)).reduce((a, b) => a + b, 0);
    const retry = o.levers.retryBudget ? Math.min(wanted, base * scenario.retryBudgetShare) : wanted;
    f.base[c] = base;
    f.retry[c] = retry;
    const total = base + retry;
    add(id, client.entry, c, total);
    const edgeHit = base * client.edgeHit;
    const blocked = base * client.wafBlock;
    if (client.entry === 'cdn') {
      add('cdn', 'edgeHit', c, edgeHit);
      add('cdn', 'waf', c, blocked);
    }
    const passedBase = base - edgeHit - blocked;
    add(client.entry, 'gateway', c, passedBase + retry);
    const shedShareNow = shedShare(id, minute, o, primaryWait);
    add('gateway', 'shed', c, (passedBase + retry) * shedShareNow);
    // A retry repeats the call that failed, so it returns along this client's primary-bound paths.
    const primaryWeights = serviceIds.map((s) => serviceMix[id][s] * storeShare(s, 'primary'));
    const primaryTotal = primaryWeights.reduce((a, b) => a + b, 0);
    serviceIds.forEach((s, si) => {
      const fresh = passedBase * (1 - shedShareNow) * serviceMix[id][s];
      const retried = primaryTotal > 0 ? retry * (1 - shedShareNow) * ((primaryWeights[si] ?? 0) / primaryTotal) : 0;
      add('gateway', s, c, fresh + retried);
      storeIds.forEach((t) => add(s, t, c, fresh * storeShare(s, t) + (t === 'primary' ? retried : 0)));
    });
  });

  storeIds.forEach((s) => {
    const ni = nodeIndex.get(s) ?? 0;
    const node = nodeById.get(s);
    const arriving = (incoming.get(s) ?? []).flatMap((l) => clientIds.map((_, c) => f.flow[l.index * clientCount + c] ?? 0));
    const arrival = arriving.reduce((a, b) => a + b, 0);
    const capacity = node?.capacity ?? Infinity;
    const prevQueue = prev.queue[ni] ?? 0;
    const errors = storeErrors[s];
    if (node?.queue === 'messages') {
      f.queue[ni] = Math.max(0, prevQueue + (arrival - capacity) * 60);
      f.waitMs[ni] = 0;
    } else {
      const maxQueue = capacity * (scenario.poolTimeoutMs / 1000);
      const effective = capacity * (1 - scenario.thrashing * (prevQueue / maxQueue));
      const queue = Math.min(maxQueue, Math.max(0, prevQueue + (arrival - effective) * 60));
      f.queue[ni] = queue;
      f.waitMs[ni] = Math.min(scenario.poolTimeoutMs, (queue / effective) * 1000);
    }
    const saturation = node?.queue === 'requests' && Number.isFinite(capacity)
      ? (f.queue[ni] ?? 0) / (capacity * (scenario.poolTimeoutMs / 1000)) : 0;
    const effective = capacity * (1 - scenario.thrashing * saturation);
    const failed = arrival > 0 ? saturation ** 2 * Math.max(0, (arrival - effective) / arrival) + saturation ** 3 * 0.2 : 0;
    const shares: Record<OutcomeId, number> = {
      serverErr: errors.serverErr + failed * 0.55,
      timeout: failed * 0.45,
      clientErr: errors.clientErr * (1 - failed),
      ok: 0,
    };
    shares.ok = Math.max(0, 1 - shares.serverErr - shares.timeout - shares.clientErr);
    clientIds.forEach((_, c) => {
      const into = (incoming.get(s) ?? []).reduce((a, l) => a + (f.flow[l.index * clientCount + c] ?? 0), 0);
      outcomeIds.forEach((o) => add(s, o, c, into * shares[o]));
    });
  });

  links.forEach((l) => {
    const total = clientIds.reduce((a, _, c) => a + (f.flow[l.index * clientCount + c] ?? 0), 0);
    f.linkTotal[l.index] = total;
    const ti = nodeIndex.get(l.target) ?? 0;
    f.throughput[ti] = (f.throughput[ti] ?? 0) + total;
  });
  clientIds.forEach((id, c) => {
    const ni = nodeIndex.get(id) ?? 0;
    f.throughput[ni] = (f.base[c] ?? 0) + (f.retry[c] ?? 0);
  });
  f.rps = clientIds.reduce((a, _, c) => a + (f.base[c] ?? 0) + (f.retry[c] ?? 0), 0);
  const failures = (f.throughput[nodeIndex.get('serverErr') ?? 0] ?? 0) + (f.throughput[nodeIndex.get('timeout') ?? 0] ?? 0);
  f.errorRate = f.rps > 0 ? failures / f.rps : 0;
  f.burn = f.errorRate / (1 - scenario.slo);
  f.hitRate = hit;
  return f;
};

// ----------------------------------------
// The day, and reading it at any moment
// ----------------------------------------
export const simulateDay = (o: DayOptions = actualDay): Frame[] => {
  const warm = Array.from({ length: 120 }, (_, i) => i - 120)
    .reduce((prev, m) => step(m + minutesInDay, prev, o), emptyFrame(-121));
  return Array.from({ length: minutesInDay + 1 }, (_, m) => m)
    .reduce<Frame[]>((acc, m) => {
      acc.push(step(m, acc[acc.length - 1] ?? warm, o));
      return acc;
    }, []);
};

// ----------------------------------------
// Day-level outcomes, for comparing days
// ----------------------------------------
export interface DayOutcome {
  breachMinutes: number;
  firstBreach: number | null;
  lastBreach: number | null;
  peakErrorRate: number;
  availability: number;
}

export const dayOutcome = (day: readonly Frame[]): DayOutcome => {
  const breach = day.filter((f) => f.burn > 1);
  const total = day.reduce((a, f) => a + f.rps, 0);
  const failed = day.reduce((a, f) => a + f.rps * f.errorRate, 0);
  return {
    breachMinutes: breach.length,
    firstBreach: breach[0]?.minute ?? null,
    lastBreach: breach[breach.length - 1]?.minute ?? null,
    peakErrorRate: Math.max(...day.map((f) => f.errorRate)),
    availability: total > 0 ? 1 - failed / total : 1,
  };
};

const lerpArray = (a: Float64Array, b: Float64Array, t: number): Float64Array => a.map((v, i) => v + ((b[i] ?? v) - v) * t);

export const frameAt = (day: readonly Frame[], minute: number): Frame => {
  const m = Math.min(minutesInDay, Math.max(0, minute));
  const i = Math.min(minutesInDay - 1, Math.floor(m));
  const a = day[i];
  const b = day[i + 1] ?? a;
  if (!a || !b) return emptyFrame(m);
  const t = m - i;
  const lerp = (x: number, y: number): number => x + (y - x) * t;
  return {
    minute: m,
    flow: lerpArray(a.flow, b.flow, t),
    linkTotal: lerpArray(a.linkTotal, b.linkTotal, t),
    throughput: lerpArray(a.throughput, b.throughput, t),
    queue: lerpArray(a.queue, b.queue, t),
    waitMs: lerpArray(a.waitMs, b.waitMs, t),
    base: lerpArray(a.base, b.base, t),
    retry: lerpArray(a.retry, b.retry, t),
    rps: lerp(a.rps, b.rps),
    errorRate: lerp(a.errorRate, b.errorRate),
    burn: lerp(a.burn, b.burn),
    hitRate: lerp(a.hitRate, b.hitRate),
  };
};

// ----------------------------------------
// Readings derived from a frame
// ----------------------------------------
export const linkLatency = (frame: Frame, link: FlowLink, client?: ClientId): { p50: number; p99: number } => {
  const target = nodeById.get(link.target);
  const wait = frame.waitMs[nodeIndex.get(link.target) ?? 0] ?? 0;
  const network = client && link.source === client ? clients[client].network : { p50: 0, p99: 0 };
  return {
    p50: (target?.latency.p50 ?? 0) + wait + network.p50,
    p99: (target?.latency.p99 ?? 0) + wait * 1.6 + network.p99,
  };
};

export const clientMix = (frame: Frame, link: FlowLink): number[] => {
  const total = frame.linkTotal[link.index] ?? 0;
  return clientIds.map((_, c) => (total > 0 ? (frame.flow[link.index * clientCount + c] ?? 0) / total : 0));
};

export const nodeMix = (frame: Frame, id: NodeId): number[] => {
  const list = nodeById.get(id)?.kind === 'client' ? outgoing.get(id) ?? [] : incoming.get(id) ?? [];
  const sums = clientIds.map((_, c) => list.reduce((a, l) => a + (frame.flow[l.index * clientCount + c] ?? 0), 0));
  const total = sums.reduce((a, b) => a + b, 0);
  return sums.map((v) => (total > 0 ? v / total : 0));
};

export const activeEvent = (minute: number) => [...events].reverse().find((e) => minute >= e.start && minute < e.end);

export const columnTotals = (frame: Frame): number[] => Array.from({ length: config.layout.columns }, (_, col) => nodes
  .reduce((a, n, i) => a + (n.column === col ? frame.throughput[i] ?? 0 : 0), 0));
