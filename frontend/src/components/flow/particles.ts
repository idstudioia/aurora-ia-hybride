// Adapté de Request Flow Replay — Copyright (c) 2026 Cliff Pyles — licence MIT, voir third_party/request-flow-replay/LICENSE.txt
// The dots. One dot per real run: it enters at Entrée and moves station to station in the
// order of the run's events. Travel time on a hop is the real duration of the stage it enters,
// on a log scale (original travelSeconds), never a function of the playback speed. A dot whose
// stage is still running waits in a pile left of the station until the next event arrives.

import { flowConfig } from './config';
import { linkKey, statusTone, visitMs, type Journey, type RunStatus, type StationId, type StatusTone } from './model';
import { polylinePoint, ribbonPoint, type Point, type Scene } from './layout';

export interface Dot {
  id: number;
  runId: number;
  /** Index of the visit the dot is at (phase 'at' / 'linger') or leaving (phase 'move'). */
  idx: number;
  phase: 'at' | 'move' | 'linger';
  t: number;
  across: number;
  depth: number;
  clock: number;
}

export interface Engine {
  dots: Dot[];
  nextId: number;
  /** Followed run (not dot): the side panel and the ring follow it. */
  followed: number | null;
}

export interface Look {
  colors: Record<StatusTone, string>;
  ring: string;
  radius: number;
  alpha: number;
  followAlpha: number;
}

export type JourneyLookup = (runId: number) => Journey | undefined;

export const createEngine = (): Engine => ({ dots: [], nextId: 1, followed: null });

export const clearDots = (engine: Engine): void => {
  engine.dots.splice(0, engine.dots.length);
};

// ----------------------------------------
// Time: real milliseconds to seconds on screen
// ----------------------------------------
const { travelBaseSeconds, travelSecondsPerDecade } = flowConfig.particles;
export const travelSeconds = (ms: number): number => travelBaseSeconds + travelSecondsPerDecade * Math.log10(1 + Math.max(0, ms));

// ----------------------------------------
// Spawning
// ----------------------------------------
export const hasDot = (engine: Engine, runId: number): boolean => engine.dots.some(d => d.runId === runId);

/** A dot for a run, at visit `idx` (0 = Entrée). */
export const spawn = (engine: Engine, runId: number, idx = 0): void => {
  if (engine.dots.length >= flowConfig.particles.maxDots || hasDot(engine, runId)) return;
  engine.nextId += 1;
  engine.dots.push({ id: engine.nextId, runId, idx: Math.max(0, idx), phase: 'at', t: 0, across: 0.15 + Math.random() * 0.7, depth: Math.random(), clock: 0 });
};

// ----------------------------------------
// Step
// ----------------------------------------
export const stepEngine = (engine: Engine, lookup: JourneyLookup, nowMs: number, dt: number): void => {
  const { lingerSeconds } = flowConfig.particles;
  engine.dots = engine.dots.flatMap((dot): Dot[] => {
    const j = lookup(dot.runId);
    if (!j || j.visits.length === 0) return [];
    if (dot.phase === 'linger') {
      const clock = dot.clock + dt;
      return clock >= lingerSeconds ? [] : [{ ...dot, clock }];
    }
    if (dot.phase === 'at') {
      const next = j.visits[dot.idx + 1];
      if (next) return [{ ...dot, phase: 'move', t: 0, clock: 0 }];
      const here = j.visits[dot.idx];
      // Last known visit: wait while it runs, linger once the run is over.
      if (here && !here.open) return [{ ...dot, phase: 'linger', clock: 0 }];
      return [{ ...dot, clock: dot.clock + dt }];
    }
    const target = j.visits[dot.idx + 1];
    if (!target) return [{ ...dot, phase: 'at', t: 0 }];
    // An open target keeps counting up, so the dot slows as its stage keeps running.
    const duration = travelSeconds(visitMs(target, nowMs));
    const t = dot.t + dt / duration;
    if (t < 1) return [{ ...dot, t }];
    return [{ ...dot, phase: 'at', idx: dot.idx + 1, t: 0, clock: 0 }];
  });
};

// ----------------------------------------
// Following one run
// ----------------------------------------
export const follow = (engine: Engine, runId: number | null): void => {
  engine.followed = runId;
};

// ----------------------------------------
// Positions and drawing
// ----------------------------------------
const statusOf = (j: Journey, dot: Dot): RunStatus => {
  const v = j.visits[dot.idx];
  if (!v) return j.status;
  return v.statusAfter;
};

const pilePoint = (scene: Scene, stage: StationId, dot: Dot): Point | null => {
  const box = scene.nodeById.get(stage);
  if (!box) return null;
  const inset = 3;
  const depth = flowConfig.particles.pileWidth * dot.depth;
  const span = Math.max(0, box.v1 - box.v0 - 2 * inset);
  return scene.toScreen(box.u0 - inset - depth, box.v0 + inset + dot.across * span);
};

export const dotPoint = (dot: Dot, scene: Scene, lookup: JourneyLookup): Point | null => {
  const j = lookup(dot.runId);
  const here = j?.visits[dot.idx];
  if (!j || !here) return null;
  if (dot.phase !== 'move') return pilePoint(scene, here.stage, dot);
  const next = j.visits[dot.idx + 1];
  if (!next) return pilePoint(scene, here.stage, dot);
  const geom = scene.linkByKey.get(linkKey(here.stage, next.stage));
  if (geom?.kind === 'ribbon') return ribbonPoint(geom.geom, dot.t, dot.across * geom.geom.width, scene);
  if (geom?.kind === 'loop') {
    const pts = geom.geom.points;
    const p = polylinePoint(pts, dot.t);
    const q = polylinePoint(pts, Math.min(1, dot.t + 0.004));
    const len = Math.hypot(q.x - p.x, q.y - p.y) || 1;
    const spread = (dot.across - 0.5) * geom.geom.width * 0.8;
    return { x: p.x - ((q.y - p.y) / len) * spread, y: p.y + ((q.x - p.x) / len) * spread };
  }
  // Link outside the drawn window (should not happen): straight line between stations.
  const a = scene.nodeById.get(here.stage);
  const b = scene.nodeById.get(next.stage);
  if (!a || !b) return null;
  return { x: a.u1 + (b.u0 - a.u1) * dot.t, y: (a.v0 + a.v1) / 2 + (((b.v0 + b.v1) / 2) - ((a.v0 + a.v1) / 2)) * dot.t };
};

/** Run whose dot is nearest to (x, y), within `reach` pixels. */
export const dotNear = (engine: Engine, scene: Scene, lookup: JourneyLookup, x: number, y: number, reach: number): number | null => {
  let best: { runId: number; gap: number } | null = null;
  engine.dots.forEach(d => {
    const p = dotPoint(d, scene, lookup);
    if (!p) return;
    const gap = Math.hypot(p.x - x, p.y - y);
    if (gap <= reach && (!best || gap < best.gap)) best = { runId: d.runId, gap };
  });
  return (best as { runId: number; gap: number } | null)?.runId ?? null;
};

export const drawDots = (ctx: CanvasRenderingContext2D, engine: Engine, scene: Scene, lookup: JourneyLookup, look: Look, pulse: number): void => {
  const canvas = ctx.canvas;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.restore();
  const following = engine.followed !== null;
  let ringAt: { p: Point; color: string } | null = null;
  engine.dots.forEach(d => {
    const j = lookup(d.runId);
    if (!j) return;
    const p = dotPoint(d, scene, lookup);
    if (!p) return;
    const color = look.colors[statusTone(statusOf(j, d))];
    const fade = d.phase === 'linger' ? Math.max(0, 1 - d.clock / flowConfig.particles.lingerSeconds) : 1;
    const isFollowed = engine.followed === d.runId;
    const base = following && !isFollowed ? look.followAlpha : look.alpha;
    // A dot waiting on a running stage breathes, so "still working" reads without text.
    const waiting = d.phase === 'at' && j.visits[d.idx]?.open;
    const r = look.radius * (isFollowed ? 1.6 : 1) * (waiting ? 1 + 0.18 * Math.sin(pulse * 4 + d.id) : 1);
    ctx.globalAlpha = base * fade;
    ctx.shadowColor = color;
    ctx.shadowBlur = isFollowed ? 14 : 8;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
    ctx.fill();
    if (isFollowed) ringAt = { p, color };
  });
  ctx.shadowBlur = 0;
  const ring = ringAt as { p: Point; color: string } | null;
  if (ring) {
    ctx.globalAlpha = 1;
    ctx.beginPath();
    ctx.arc(ring.p.x, ring.p.y, look.radius * 4, 0, Math.PI * 2);
    ctx.strokeStyle = look.ring;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
};
