// Intended use: the dots. Spawns requests at each client,
// moves them at each hop's latency, queues them at saturated stores, loops failures
// back as retries, records each request's trace, and draws them to the canvas.

import { config } from './config.js';
import { clientIds, clients, type NodeId } from './data.js';
import {
  clientCount, linkIndexOf, nodeById, nodeIndex, outgoing, type FlowLink, type Frame,
} from './model.js';
import {
  polylinePoint, ribbonPoint, type Point, type Scene,
} from './layout.js';

// ----------------------------------------
// State
// ----------------------------------------
export interface Span {
  kind: 'hop' | 'queue' | 'retry' | 'end';
  node: NodeId;
  ms: number;
  attempt: number;
}

interface Dot {
  id: number;
  client: number;
  attempt: number;
  phase: 'link' | 'queue' | 'retry';
  link: number;
  node: NodeId;
  t: number;
  duration: number;
  across: number;
  depth: number;
  release: number;
  trace: Span[];
}

export interface FollowedRequest {
  client: number;
  trace: Span[];
  done: boolean;
}

export interface Engine {
  dots: Dot[];
  spawnDebt: number[];
  now: number;
  nextId: number;
  followed: number | null;
  finished: FollowedRequest | null;
}

export interface Look {
  colors: string[];
  radius: number;
  alpha: number;
  dimAlpha: number;
  followAlpha: number;
  ring: string;
  ringWidth: number;
}

export const createEngine = (): Engine => ({
  dots: [], spawnDebt: clientIds.map(() => Math.random()), now: 0, nextId: 1, followed: null, finished: null,
});

export const clearDots = (engine: Engine): void => {
  engine.dots.splice(0, engine.dots.length);
};

// ----------------------------------------
// Time: latency in milliseconds to seconds on screen
// ----------------------------------------
const { travelBaseSeconds, travelSecondsPerDecade } = config.particles;
export const travelSeconds = (ms: number): number => travelBaseSeconds + travelSecondsPerDecade * Math.log10(1 + ms);
const queueSeconds = (ms: number): number => travelSecondsPerDecade * Math.log10(1 + ms / 25);

const gaussian = (): number => {
  const u = Math.max(1e-6, Math.random());
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * Math.random());
};

const sampleLatency = (p50: number, p99: number): number => {
  if (p50 <= 0) return 0;
  const sigma = Math.log(Math.max(p99, p50 * 1.01) / p50) / 2.326;
  return p50 * Math.exp(sigma * Math.min(3, Math.max(-2.5, gaussian())));
};

const hopMs = (link: FlowLink, client: number): number => {
  const target = nodeById.get(link.target);
  const id = clientIds[client];
  const network = id && link.source === id ? clients[id].network : { p50: 0, p99: 0 };
  return sampleLatency((target?.latency.p50 ?? 0) + network.p50, (target?.latency.p99 ?? 0) + network.p99);
};

// ----------------------------------------
// Routing: next hop drawn from this client's share of the current flows.
// A retry repeats a failed call, so it skips the edge cache and heads for the primary.
// ----------------------------------------
const flowOf = (frame: Frame, source: NodeId, target: NodeId, client: number): number => {
  const li = linkIndexOf(source, target);
  return li === undefined ? 0 : frame.flow[li * clientCount + client] ?? 0;
};

const retryWeight = (l: FlowLink, client: number, frame: Frame): number => {
  const target = nodeById.get(l.target);
  if (target?.kind === 'outcome' && l.target !== 'shed') return 0;
  if (l.source === 'gateway' && target?.kind === 'service') return flowOf(frame, l.target, 'primary', client);
  if (nodeById.get(l.source)?.kind === 'service') return l.target === 'primary' ? 1 : 0;
  return frame.flow[l.index * clientCount + client] ?? 0;
};

const nextLink = (from: NodeId, dot: Dot, frame: Frame): FlowLink | null => {
  const options = outgoing.get(from) ?? [];
  const plain = (l: FlowLink): number => frame.flow[l.index * clientCount + dot.client] ?? 0;
  const retrying = dot.attempt > 1 && nodeById.get(from)?.kind !== 'store';
  const weighted = options.map((l) => (retrying ? retryWeight(l, dot.client, frame) : plain(l)));
  const weights = weighted.some((w) => w > 0) ? weighted : options.map(plain);
  const total = weights.reduce((a, b) => a + b, 0);
  if (total <= 0) return null;
  const pick = Math.random() * total;
  const found = weights.reduce<{ left: number; at: number }>((acc, w, i) => (acc.at >= 0 ? acc
    : { left: acc.left - w, at: acc.left - w <= 0 ? i : -1 }), { left: pick, at: -1 });
  return options[found.at] ?? null;
};

const launch = (dot: Dot, link: FlowLink): Dot => {
  const ms = hopMs(link, dot.client);
  return {
    ...dot,
    phase: 'link',
    link: link.index,
    node: link.target,
    t: 0,
    duration: travelSeconds(ms),
    across: Math.random(),
    trace: [...dot.trace, { kind: 'hop', node: link.target, ms, attempt: dot.attempt }],
  };
};

const finish = (engine: Engine, dot: Dot): void => {
  if (dot.id !== engine.followed) return;
  engine.finished = {
    client: dot.client, trace: [...dot.trace, { kind: 'end', node: dot.node, ms: 0, attempt: dot.attempt }], done: true,
  };
};

const arrive = (engine: Engine, dot: Dot, frame: Frame, now: number): Dot | null => {
  const node = nodeById.get(dot.node);
  const wait = frame.waitMs[nodeIndex.get(dot.node) ?? 0] ?? 0;
  if (dot.phase === 'link' && node?.queue === 'requests' && wait > 20) {
    const ms = wait * (0.6 + Math.random() * 0.8);
    return {
      ...dot,
      phase: 'queue',
      depth: Math.random(),
      release: now + queueSeconds(ms),
      trace: [...dot.trace, { kind: 'queue', node: dot.node, ms, attempt: dot.attempt }],
    };
  }
  const next = nextLink(dot.node, dot, frame);
  if (next) return launch(dot, next);
  const id = clientIds[dot.client];
  if (node?.failure && id && dot.attempt <= clients[id].retries) {
    return {
      ...dot,
      phase: 'retry',
      t: 0,
      duration: config.particles.retryTravelSeconds + clients[id].backoffMs / 1000,
      trace: [...dot.trace, { kind: 'end', node: dot.node, ms: 0, attempt: dot.attempt },
        { kind: 'retry', node: id, ms: clients[id].backoffMs, attempt: dot.attempt + 1 }],
    };
  }
  finish(engine, dot);
  return null;
};

// ----------------------------------------
// Step
// ----------------------------------------
export const stepEngine = (engine: Engine, frame: Frame, dt: number): void => {
  const now = engine.now + dt;
  const room = config.particles.maxDots - engine.dots.length;
  const born = clientIds.flatMap((id, c) => {
    const debt = (engine.spawnDebt[c] ?? 0) + ((frame.base[c] ?? 0) / config.particles.requestsPerDot) * dt;
    const count = Math.floor(debt);
    engine.spawnDebt[c] = debt - count;
    const first = outgoing.get(id)?.[0];
    if (!first || room <= 0) return [];
    return Array.from({ length: Math.min(count, room) }, () => {
      engine.nextId += 1;
      return launch({
        id: engine.nextId, client: c, attempt: 1, phase: 'link', link: first.index, node: id, t: 0, duration: 1, across: 0, depth: 0, release: 0, trace: [],
      }, first);
    });
  });

  const moved = engine.dots.flatMap((dot): Dot[] => {
    if (dot.phase === 'queue') {
      if (now < dot.release) return [dot];
      const next = nextLink(dot.node, dot, frame);
      if (next) return [launch(dot, next)];
      finish(engine, dot);
      return [];
    }
    const t = dot.t + dt / dot.duration;
    if (t < 1) return [{ ...dot, t }];
    if (dot.phase === 'retry') {
      const id = clientIds[dot.client];
      const first = id ? outgoing.get(id)?.[0] : undefined;
      return first ? [launch({ ...dot, attempt: dot.attempt + 1 }, first)] : [];
    }
    const next = arrive(engine, { ...dot, t: 1 }, frame, now);
    return next ? [next] : [];
  });

  engine.dots = [...moved, ...born];
  engine.now = now;
};

// Run the engine ahead so a seek lands on a full pipeline instead of an empty one.
export const warmEngine = (engine: Engine, frame: Frame, seconds: number): void => {
  const step = config.particles.warmStepSeconds;
  Array.from({ length: Math.ceil(seconds / step) }).forEach(() => stepEngine(engine, frame, step));
};

// ----------------------------------------
// Following one request
// ----------------------------------------
export const follow = (engine: Engine, id: number | null): void => {
  engine.followed = id;
  engine.finished = null;
};

export const followed = (engine: Engine): FollowedRequest | null => {
  const dot = engine.followed === null ? undefined : engine.dots.find((d) => d.id === engine.followed);
  if (dot) return { client: dot.client, trace: dot.trace, done: false };
  return engine.finished;
};

// A first-attempt request just past the gateway, from one client or any; its earlier hops are already in its trace.
export const freshRequest = (engine: Engine, client: number | null): number | null => {
  const fresh = engine.dots.filter((d) => d.attempt === 1 && d.phase === 'link' && nodeById.get(d.node)?.kind === 'service'
    && d.node !== 'gateway' && (client === null || d.client === client));
  return fresh[Math.floor(Math.random() * fresh.length)]?.id ?? null;
};

// ----------------------------------------
// Draw
// ----------------------------------------
const dotPoint = (dot: Dot, scene: Scene): Point => {
  if (dot.phase === 'retry') {
    const r = scene.retries[dot.client];
    if (!r) return { x: 0, y: 0 };
    const p = polylinePoint(r.points, dot.t);
    const q = polylinePoint(r.points, Math.min(1, dot.t + 0.004));
    const len = Math.hypot(q.x - p.x, q.y - p.y) || 1;
    const spread = (dot.across - 0.5) * r.width * 0.8;
    return { x: p.x - ((q.y - p.y) / len) * spread, y: p.y + ((q.x - p.x) / len) * spread };
  }
  if (dot.phase === 'queue') {
    const box = scene.nodes.find((n) => n.id === dot.node);
    if (!box) return { x: 0, y: 0 };
    const inset = config.particles.queueSpacing;
    const depth = Math.max(inset, box.pileU) * dot.depth;
    return scene.toScreen(box.u0 - inset / 2 - depth, box.v0 + inset / 2 + dot.across * Math.max(0, box.v1 - box.v0 - inset));
  }
  const r = scene.ribbons[dot.link];
  if (!r) return { x: 0, y: 0 };
  const before = r.lanes.slice(0, dot.client).reduce((a, b) => a + b, 0);
  const lane = r.lanes[dot.client] ?? 0;
  return ribbonPoint(r, dot.t, before + dot.across * lane, scene);
};

export const dotNear = (engine: Engine, scene: Scene, x: number, y: number, reach: number): number | null => engine.dots
  .map((d) => {
    const p = dotPoint(d, scene);
    return { id: d.id, gap: Math.hypot(p.x - x, p.y - y) };
  })
  .filter((d) => d.gap <= reach)
  .sort((a, b) => a.gap - b.gap)[0]?.id ?? null;

export const followedPoint = (engine: Engine, scene: Scene): Point | null => {
  const dot = engine.followed === null ? undefined : engine.dots.find((d) => d.id === engine.followed);
  return dot ? dotPoint(dot, scene) : null;
};

export const drawDots = (
  ctx: CanvasRenderingContext2D,
  engine: Engine,
  scene: Scene,
  look: Look,
  traced: number | null,
): void => {
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  const following = engine.followed !== null;
  clientIds.forEach((_, c) => {
    ctx.beginPath();
    engine.dots.filter((d) => d.client === c).forEach((d) => {
      const p = dotPoint(d, scene);
      ctx.moveTo(p.x + look.radius, p.y);
      ctx.arc(p.x, p.y, look.radius, 0, Math.PI * 2);
    });
    const base = traced === null || traced === c ? look.alpha : look.dimAlpha;
    ctx.globalAlpha = following ? Math.min(base, look.followAlpha) : base;
    ctx.fillStyle = look.colors[c] ?? '';
    ctx.fill();
  });
  const dot = engine.followed === null ? undefined : engine.dots.find((d) => d.id === engine.followed);
  if (dot) {
    const p = dotPoint(dot, scene);
    ctx.globalAlpha = 1;
    ctx.beginPath();
    ctx.arc(p.x, p.y, look.radius * 2.2, 0, Math.PI * 2);
    ctx.fillStyle = look.colors[dot.client] ?? '';
    ctx.fill();
    ctx.beginPath();
    ctx.arc(p.x, p.y, look.radius * 5, 0, Math.PI * 2);
    ctx.strokeStyle = look.ring;
    ctx.lineWidth = look.ringWidth;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
};
