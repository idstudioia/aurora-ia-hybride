// Intended use: the platform being replayed. Topology, the clients' daily traffic,
// routing shares, capacities, latencies, and the causes of the 14:20 incident. The
// model derives every flow from these; nothing here is a precomputed result.

// ----------------------------------------
// Identifiers
// ----------------------------------------
export const clientIds = ['web', 'mobile', 'partner', 'batch'] as const;
export type ClientId = (typeof clientIds)[number];

export type ServiceId = 'auth' | 'catalog' | 'search' | 'checkout';
export type StoreId = 'redis' | 'primary' | 'replica' | 'kafka';
export type OutcomeId = 'ok' | 'clientErr' | 'serverErr' | 'timeout';
export type NodeId =
  | ClientId
  | 'cdn' | 'lb'
  | 'gateway' | 'edgeHit' | 'waf'
  | ServiceId | 'shed'
  | StoreId
  | OutcomeId;

export type NodeKind = 'client' | 'ingress' | 'service' | 'store' | 'outcome';

export interface Latency {
  p50: number;
  p99: number;
}

export interface PlatformNode {
  id: NodeId;
  label: string;
  short: string;
  column: number;
  kind: NodeKind;
  latency: Latency;
  failure?: boolean;
  capacity?: number;
  queue?: 'requests' | 'messages';
}

// ----------------------------------------
// Nodes, column by column, in drawing order along each column
// ----------------------------------------
export const nodes: readonly PlatformNode[] = [
  { id: 'web', label: 'Web', short: 'Web', column: 0, kind: 'client', latency: { p50: 0, p99: 0 } },
  { id: 'mobile', label: 'Mobile app', short: 'Mobile', column: 0, kind: 'client', latency: { p50: 0, p99: 0 } },
  { id: 'partner', label: 'Partner API', short: 'Partner', column: 0, kind: 'client', latency: { p50: 0, p99: 0 } },
  { id: 'batch', label: 'Batch jobs', short: 'Batch', column: 0, kind: 'client', latency: { p50: 0, p99: 0 } },

  { id: 'cdn', label: 'CDN · WAF', short: 'CDN', column: 1, kind: 'ingress', latency: { p50: 4, p99: 22 } },
  { id: 'lb', label: 'Internal LB', short: 'Int. LB', column: 1, kind: 'ingress', latency: { p50: 1, p99: 3 } },

  { id: 'edgeHit', label: 'Edge cache hit', short: 'Edge hit', column: 2, kind: 'outcome', latency: { p50: 1, p99: 6 } },
  { id: 'waf', label: 'Blocked at WAF', short: 'WAF 403', column: 2, kind: 'outcome', latency: { p50: 1, p99: 4 } },
  { id: 'gateway', label: 'API gateway', short: 'Gateway', column: 2, kind: 'service', latency: { p50: 2, p99: 9 } },

  { id: 'auth', label: 'auth', short: 'auth', column: 3, kind: 'service', latency: { p50: 3, p99: 14 } },
  { id: 'catalog', label: 'catalog', short: 'catalog', column: 3, kind: 'service', latency: { p50: 6, p99: 40 } },
  { id: 'search', label: 'search', short: 'search', column: 3, kind: 'service', latency: { p50: 22, p99: 140 } },
  { id: 'checkout', label: 'checkout', short: 'checkout', column: 3, kind: 'service', latency: { p50: 14, p99: 90 } },
  { id: 'shed', label: 'Shed · 429', short: '429', column: 3, kind: 'outcome', latency: { p50: 1, p99: 3 } },

  { id: 'redis', label: 'Redis', short: 'Redis', column: 4, kind: 'store', latency: { p50: 0.8, p99: 4 }, capacity: 40000, queue: 'requests' },
  {
    id: 'primary', label: 'Postgres primary', short: 'PG primary', column: 4, kind: 'store', latency: { p50: 7, p99: 38 }, capacity: 1500, queue: 'requests',
  },
  {
    id: 'replica', label: 'Postgres replica', short: 'PG replica', column: 4, kind: 'store', latency: { p50: 5, p99: 30 }, capacity: 2600, queue: 'requests',
  },
  { id: 'kafka', label: 'Kafka · orders', short: 'Kafka', column: 4, kind: 'store', latency: { p50: 3, p99: 18 }, capacity: 380, queue: 'messages' },

  { id: 'ok', label: '2xx', short: '2xx', column: 5, kind: 'outcome', latency: { p50: 1, p99: 4 } },
  { id: 'clientErr', label: '4xx', short: '4xx', column: 5, kind: 'outcome', latency: { p50: 1, p99: 4 } },
  { id: 'serverErr', label: '5xx', short: '5xx', column: 5, kind: 'outcome', latency: { p50: 1, p99: 4 }, failure: true },
  { id: 'timeout', label: 'Client timeout', short: 'Timeout', column: 5, kind: 'outcome', latency: { p50: 3000, p99: 3000 }, failure: true },
];

// ----------------------------------------
// Clients: traffic at each hour (req/s at hh:00), how they enter, how they retry
// ----------------------------------------
export interface Client {
  id: ClientId;
  hourly: readonly number[];
  entry: 'cdn' | 'lb';
  network: Latency;
  edgeHit: number;
  wafBlock: number;
  retries: number;
  backoffMs: number;
  retryNote: string;
}

export const clients: Record<ClientId, Client> = {
  web: {
    id: 'web',
    hourly: [620, 480, 390, 340, 330, 380, 520, 820, 1180, 1420, 1510, 1560, 1640, 1590, 1540, 1500, 1490, 1560, 1710, 1880, 1960, 1820, 1430, 940],
    entry: 'cdn',
    network: { p50: 28, p99: 120 },
    edgeHit: 0.34,
    wafBlock: 0.012,
    retries: 1,
    backoffMs: 1000,
    retryNote: 'one retry after 1 s',
  },
  mobile: {
    id: 'mobile',
    hourly: [900, 640, 470, 390, 380, 450, 700, 1100, 1420, 1500, 1460, 1520, 1700, 1620, 1560, 1540, 1600, 1780, 2050, 2340, 2460, 2290, 1860, 1320],
    entry: 'cdn',
    network: { p50: 46, p99: 310 },
    edgeHit: 0.22,
    wafBlock: 0.006,
    retries: 2,
    backoffMs: 200,
    retryNote: 'two retries, 200 ms apart, no jitter',
  },
  partner: {
    id: 'partner',
    hourly: [140, 120, 110, 110, 120, 160, 260, 410, 560, 620, 640, 630, 600, 640, 650, 630, 590, 520, 400, 320, 260, 220, 190, 160],
    entry: 'cdn',
    network: { p50: 22, p99: 80 },
    edgeHit: 0.05,
    wafBlock: 0.003,
    retries: 0,
    backoffMs: 0,
    retryNote: 'no retries; honors Retry-After',
  },
  batch: {
    id: 'batch',
    hourly: Array.from({ length: 24 }, () => 60),
    entry: 'lb',
    network: { p50: 2, p99: 6 },
    edgeHit: 0,
    wafBlock: 0,
    retries: 3,
    backoffMs: 0,
    retryNote: 'three retries, no backoff',
  },
};

// ----------------------------------------
// Routing: which services each client calls, which stores each service reads
// ----------------------------------------
export const serviceMix: Record<ClientId, Record<ServiceId, number>> = {
  web: { auth: 0.12, catalog: 0.48, search: 0.26, checkout: 0.14 },
  mobile: { auth: 0.18, catalog: 0.44, search: 0.22, checkout: 0.16 },
  partner: { auth: 0.06, catalog: 0.58, search: 0.04, checkout: 0.32 },
  batch: { auth: 0, catalog: 0.22, search: 0, checkout: 0.78 },
};

// Catalog's Redis share is the cache hit rate, set over time by the scenario.
export const storeMix: Record<ServiceId, Partial<Record<StoreId, number>>> = {
  auth: { redis: 0.92, primary: 0.08 },
  catalog: { redis: 0.88, primary: 0.12 },
  search: { replica: 0.9, redis: 0.1 },
  checkout: { primary: 0.62, kafka: 0.38 },
};

// Share of each store's responses that are 4xx and 5xx on a healthy day.
export const storeErrors: Record<StoreId, { clientErr: number; serverErr: number }> = {
  redis: { clientErr: 0.03, serverErr: 0.0002 },
  primary: { clientErr: 0.015, serverErr: 0.0006 },
  replica: { clientErr: 0.01, serverErr: 0.0004 },
  kafka: { clientErr: 0, serverErr: 0.0001 },
};

// ----------------------------------------
// Scenario: scheduled jobs and the incident's causes, in minutes after midnight
// ----------------------------------------
export interface BatchJob {
  name: string;
  start: number;
  end: number;
  rate: number;
  pausedFrom?: number;
  pausedTo?: number;
}

export const batchJobs: readonly BatchJob[] = [
  { name: 'Nightly ledger export', start: 60, end: 280, rate: 1400 },
  { name: 'Order reconciliation', start: 840, end: 980, rate: 380, pausedFrom: 881, pausedTo: 930 },
];

export const scenario = {
  deploy: { at: 860, version: 'catalog v412', hitRateFloor: 0.03, warmupMinutes: 9, rolloutMinutes: 3 },
  shed: { from: 881, to: 910, client: 'partner' as ClientId, share: 0.6, baseline: 0 },
  poolTimeoutMs: 2500,
  thrashing: 0.35,
  slo: 0.999,
  prewarmHitFloor: 0.8,
  retryBudgetShare: 0.1,
  adaptive: { fromWaitMs: 500, fullWaitMs: 2000, maxShare: 0.5 },
};

// ----------------------------------------
// What-if levers: changes to the day the model can replay
// ----------------------------------------
export const leverIds = ['prewarm', 'retryBudget', 'adaptive', 'manualShed'] as const;
export type LeverId = (typeof leverIds)[number];
export type Levers = Record<LeverId, boolean>;

export interface Lever {
  id: LeverId;
  label: string;
  detail: string;
  actual: boolean;
  changed: string;
}

export const levers: Record<LeverId, Lever> = {
  prewarm: {
    id: 'prewarm',
    label: 'Pre-warm the new key prefix',
    detail: 'Fill v412 keys before switching reads. The hit rate dips to 80%, not 3%.',
    actual: false,
    changed: 'Pre-warmed cache',
  },
  retryBudget: {
    id: 'retryBudget',
    label: 'Retry budget',
    detail: 'Clients cap retries at 10% of their requests, like gRPC and Envoy budgets.',
    actual: false,
    changed: 'Retry budget',
  },
  adaptive: {
    id: 'adaptive',
    label: 'Adaptive concurrency at the gateway',
    detail: 'The gateway returns 429s once pool wait on the primary passes 500 ms.',
    actual: false,
    changed: 'Adaptive concurrency',
  },
  manualShed: {
    id: 'manualShed',
    label: 'On-call sheds load at 14:41',
    detail: 'Pause reconciliation and shed 60% of partner traffic, as on the day.',
    actual: true,
    changed: 'No load shed at 14:41',
  },
};

export const actualLevers: Levers = {
  prewarm: levers.prewarm.actual,
  retryBudget: levers.retryBudget.actual,
  adaptive: levers.adaptive.actual,
  manualShed: levers.manualShed.actual,
};

// ----------------------------------------
// Events: what the replay annotates, and where
// ----------------------------------------
// Figures the model computes, so event copy can't drift from the simulation.
export interface DayFacts {
  breachMinutes: string;
  firstBreach: string;
  lastBreach: string;
  withoutShedLastBreach: string;
  requestsPerDot: string;
}

export interface PlatformEvent {
  id: string;
  start: number;
  end: number;
  anchor: NodeId;
  title: string;
  label: string;
  body: string | ((facts: DayFacts) => string);
  marker?: boolean;
  failure?: boolean;
}

export const events: readonly PlatformEvent[] = [
  {
    id: 'ledger',
    marker: true,
    start: 60,
    end: 360,
    anchor: 'kafka',
    title: 'Consumer lag',
    label: 'Consumer lag',
    body: 'The nightly ledger export publishes faster than order consumers drain. Lag builds for hours, but producers still get 202s, so nobody is paged.',
  },
  {
    id: 'reconcile',
    start: 840,
    end: 860,
    anchor: 'primary',
    title: 'Reconciliation starts',
    label: 'Reconciliation',
    body: 'A batch job adds steady writes to the primary. It still has headroom.',
  },
  {
    id: 'stampede',
    failure: true,
    marker: true,
    start: 860,
    end: 863,
    anchor: 'redis',
    title: 'Cache stampede',
    label: 'Stampede',
    body: 'catalog v412 ships with a new key prefix. Every catalog read misses Redis and falls through to the primary.',
  },
  {
    id: 'pool',
    failure: true,
    start: 863,
    end: 866,
    anchor: 'primary',
    title: 'Pool exhaustion',
    label: 'Pool exhausted',
    body: 'Every connection is busy. Requests wait past the 2.5 s pool timeout and come back as 503s and client timeouts.',
  },
  {
    id: 'retries',
    failure: true,
    start: 866,
    end: 881,
    anchor: 'mobile',
    title: 'Retry amplification',
    label: 'Retry storm',
    body: 'Mobile retries twice without jitter and batch jobs retry with no backoff. Each retry repeats a call that failed, so it goes straight back to the primary. The cache is warming, but failures keep returning as new load.',
  },
  {
    id: 'shed',
    start: 881,
    end: 887,
    anchor: 'gateway',
    title: 'Load shed',
    label: 'Load shed',
    body: 'On-call pauses reconciliation and sheds 60% of partner traffic with 429s. The retry loop breaks and the queue drains.',
  },
  {
    id: 'recovered',
    start: 887,
    end: 930,
    anchor: 'primary',
    title: 'Recovered',
    label: 'Recovered',
    body: (f) => `Errors stayed above SLO for ${f.breachMinutes} minutes, ${f.firstBreach} to ${f.lastBreach}. Without the shed, retries would have held them there until ${f.withoutShedLastBreach}.`,
  },
  {
    id: 'resume',
    start: 930,
    end: 960,
    anchor: 'primary',
    title: 'Same job, warm cache',
    label: 'Warm cache',
    body: 'Reconciliation resumes at the same rate. With Redis warm, the primary runs at half its capacity and nothing queues.',
  },
  {
    id: 'peak',
    marker: true,
    start: 1190,
    end: 1250,
    anchor: 'gateway',
    title: 'Evening peak',
    label: 'Evening peak',
    body: 'The busiest hour of the day, and every hop stays healthy. Load alone was never the problem.',
  },
];

// ----------------------------------------
// Walkthrough: the incident told in stops; Continue plays to the next one
// ----------------------------------------
export const tourIntro: PlatformEvent = {
  id: 'intro',
  start: 835,
  end: 840,
  anchor: 'gateway',
  title: 'A day of production traffic',
  label: 'Start',
  body: (f) => `Each dot is ${f.requestsPerDot} requests. Ribbon width is req/s, a dot's pace is the latency of its hop, and its color is the client that sent it. At 14:20, catalog v412 deploys.`,
};

export const tourStops: readonly string[] = ['intro', 'stampede', 'pool', 'retries', 'shed', 'recovered', 'peak'];

