// Adapté de Request Flow Replay — Copyright (c) 2026 Cliff Pyles — licence MIT, voir third_party/request-flow-replay/LICENSE.txt
// Geometry for one graph: columns, station bars, forward ribbons and loops, in flow
// coordinates (u along the flow, v across), mapped to the screen. Forward hops are Bézier
// ribbons; a hop that goes back (or stays in its column) becomes a loop under the figure,
// the way the original drew retries — a forward-only sankey cannot draw a cycle.

import { flowConfig } from './config';
import { STATIONS, stationById, type FlowGraph, type LinkStat, type StationId } from './model';

export interface Point { x: number; y: number }

export interface StationBox {
  id: StationId;
  column: number;
  u0: number;
  u1: number;
  v0: number;
  v1: number;
  labelV: number;
  count: number;
}

export interface RibbonGeom {
  link: LinkStat;
  us: number;
  ut: number;
  vs: number;
  vt: number;
  width: number;
}

export interface LoopGeom {
  link: LinkStat;
  width: number;
  points: Point[];
}

export type LinkGeom = { kind: 'ribbon'; geom: RibbonGeom } | { kind: 'loop'; geom: LoopGeom };

export interface Scene {
  width: number;
  height: number;
  /** Pixels per passage: one fixed scale for the whole window. */
  k: number;
  nodes: StationBox[];
  nodeById: Map<StationId, StationBox>;
  ribbons: RibbonGeom[];
  loops: LoopGeom[];
  linkByKey: Map<string, LinkGeom>;
  toScreen: (u: number, v: number) => Point;
}

const order = (id: StationId): number => stationById.get(id)?.order ?? 0;
const columnOf = (id: StationId): number => stationById.get(id)?.column ?? 0;
const isForward = (l: LinkStat): boolean => columnOf(l.source) < columnOf(l.target);
const isSameColumn = (l: LinkStat): boolean => columnOf(l.source) === columnOf(l.target);

// ----------------------------------------
// Labels pushed apart along each column (original spreadLabels)
// ----------------------------------------
export const spreadLabels = (wanted: number[], span: number, lo: number, hi: number): number[] => {
  const down = wanted.reduce<number[]>((acc, w) => [...acc, Math.max(w, (acc[acc.length - 1] ?? -Infinity) + span)], []);
  const overflow = Math.max(0, (down[down.length - 1] ?? 0) - hi);
  return down.map(v => Math.max(lo, v - overflow));
};

// ----------------------------------------
// Scene for one graph
// ----------------------------------------
export function buildScene(graph: FlowGraph, width: number, height: number): Scene {
  const { nodeThickness, nodeGap, minNode, laneGap, loopGap, labelSpan, labelRoom, padLeft, padTop, maxPassagePx, columns, columnGaps } = flowConfig.layout;
  const toScreen = (u: number, v: number): Point => ({ x: u, y: v });
  const U = width;
  const V = height;
  const links = [...graph.links.values()];
  const forward = links.filter(isForward);
  // Backward hops loop under the figure; hops inside one column (Outils → IA locale,
  // Claude ⇄ Autorisation) take a short bracket on the column's left, beside the waiting pile.
  const loops = links.filter(l => !isForward(l) && !isSameColumn(l));
  const brackets = links.filter(isSameColumn);

  const countOf = (id: StationId): number => graph.stations.get(id)?.count ?? 0;
  const columnStations = Array.from({ length: columns }, (_, c) => STATIONS.filter(s => s.column === c));
  const maxStack = Math.max(...columnStations.map(c => c.length));
  const maxColumn = Math.max(1, ...columnStations.map(c => c.reduce((a, s) => a + countOf(s.id), 0)));
  const loopTotal = loops.reduce((a, l) => a + l.count, 0);

  // One scale for the window, shared by station bars, ribbons and loops.
  const loopFixed = loops.length ? laneGap * 2 + loopGap * loops.length : laneGap;
  const usable = V - padTop - loopFixed - nodeGap * (maxStack - 1) - minNode * maxStack;
  const k = Math.max(0.5, Math.min(maxPassagePx, usable / (maxColumn + loopTotal)));
  const loopBand = loops.length ? loopTotal * k + loopGap * loops.length + laneGap * 2 : laneGap;
  const vTop = padTop;
  const vBottom = V - loopBand;

  const uStart = padLeft;
  const uEnd = U - labelRoom - nodeThickness;
  const gapTotal = columnGaps.reduce((a, b) => a + b, 0);
  const colU = (c: number): number => uStart + ((uEnd - uStart) * columnGaps.slice(0, c).reduce((a, b) => a + b, 0)) / gapTotal;

  const nodes: StationBox[] = columnStations.flatMap(stations => {
    const heights = stations.map(s => Math.max(minNode, countOf(s.id) * k));
    const total = heights.reduce((a, b) => a + b, 0) + nodeGap * (stations.length - 1);
    // Columns are centred across, so the flow fans out from Entrée and back in to Réponse.
    let v = vTop + Math.max(0, (vBottom - vTop - total) / 2);
    const boxes = stations.map((s, i) => {
      const h = heights[i] ?? minNode;
      const u0 = colU(s.column);
      const box: StationBox = { id: s.id, column: s.column, u0, u1: u0 + nodeThickness, v0: v, v1: v + h, labelV: v + h / 2, count: countOf(s.id) };
      v += h + nodeGap;
      return box;
    });
    const spread = spreadLabels(boxes.map(b => b.labelV), labelSpan, vTop + labelSpan / 2 - 6, V - labelSpan / 2);
    return boxes.map((b, i) => ({ ...b, labelV: spread[i] ?? b.labelV }));
  });
  const nodeById = new Map(nodes.map(n => [n.id, n]));

  // Forward ribbons: outgoing stacked by target order at the source, incoming by source order
  // at the target. Loops stack after them on both ends.
  const outOffset = new Map<string, number>();
  const inOffset = new Map<string, number>();
  STATIONS.forEach(s => {
    const outs = links.filter(l => l.source === s.id && !isSameColumn(l)).sort((a, b) => Number(!isForward(a)) - Number(!isForward(b)) || order(a.target) - order(b.target));
    outs.reduce((at, l) => { outOffset.set(l.key, at); return at + l.count * k; }, 0);
    const ins = links.filter(l => l.target === s.id && !isSameColumn(l)).sort((a, b) => Number(!isForward(a)) - Number(!isForward(b)) || order(a.source) - order(b.source));
    ins.reduce((at, l) => { inOffset.set(l.key, at); return at + l.count * k; }, 0);
  });

  const ribbons = forward.map((link): RibbonGeom => {
    const s = nodeById.get(link.source);
    const t = nodeById.get(link.target);
    return {
      link,
      us: s?.u1 ?? 0,
      ut: t?.u0 ?? 0,
      vs: (s?.v0 ?? 0) + (outOffset.get(link.key) ?? 0),
      vt: (t?.v0 ?? 0) + (inOffset.get(link.key) ?? 0),
      width: link.count * k,
    };
  });

  // Loops: out to the right of the source, down to a lane under the figure, back left of the
  // target and up into it. Wider-spanning loops take outer lanes so short ones stay inside.
  const span = (l: LinkStat): number => Math.abs(columnOf(l.source) - columnOf(l.target));
  const loopOrder = [...loops].sort((a, b) => span(a) - span(b) || order(a.source) - order(b.source));
  let laneAt = vBottom + laneGap;
  const loopGeoms = loopOrder.map((link, i): LoopGeom => {
    const s = nodeById.get(link.source);
    const t = nodeById.get(link.target);
    const w = link.count * k;
    const vLane = laneAt + w / 2;
    laneAt += w + loopGap;
    const vs = (s?.v0 ?? 0) + (outOffset.get(link.key) ?? 0) + w / 2;
    const vt = (t?.v0 ?? 0) + (inOffset.get(link.key) ?? 0) + w / 2;
    const dOut = 10 + i * (loopGap + 2) + w / 2;
    const dIn = 10 + i * (loopGap + 2) + w / 2;
    const us = s?.u1 ?? 0;
    const ut = t?.u0 ?? 0;
    return {
      link,
      width: w,
      points: [
        toScreen(us, vs), toScreen(us + dOut, vs), toScreen(us + dOut, vLane),
        toScreen(ut - dIn, vLane), toScreen(ut - dIn, vt), toScreen(ut, vt),
      ],
    };
  });

  const pile = flowConfig.particles.pileWidth;
  const bracketGeoms = brackets.map((link, i): LoopGeom => {
    const s = nodeById.get(link.source);
    const t = nodeById.get(link.target);
    const w = link.count * k;
    const d = pile + 8 + i * 5 + w / 2;
    const mid = (b: StationBox | undefined): number => (b ? (b.v0 + b.v1) / 2 : 0);
    const u = Math.min(s?.u0 ?? 0, t?.u0 ?? 0) - d;
    return {
      link,
      width: w,
      points: [toScreen(s?.u0 ?? 0, mid(s)), toScreen(u, mid(s)), toScreen(u, mid(t)), toScreen(t?.u0 ?? 0, mid(t))],
    };
  });
  loopGeoms.push(...bracketGeoms);

  const linkByKey = new Map<string, LinkGeom>([
    ...ribbons.map((r): [string, LinkGeom] => [r.link.key, { kind: 'ribbon', geom: r }]),
    ...loopGeoms.map((l): [string, LinkGeom] => [l.link.key, { kind: 'loop', geom: l }]),
  ]);

  return { width, height, k, nodes, nodeById, ribbons, loops: loopGeoms, linkByKey, toScreen };
}

// ----------------------------------------
// Paths and positions (original ribbonPoint / ribbonPath / retryPath / polylinePoint)
// ----------------------------------------
const ease = (t: number): number => t * t * (3 - 2 * t);

export const ribbonPoint = (r: RibbonGeom, t: number, across: number, scene: Scene): Point => {
  const u = (1 - t) ** 3 * r.us + 3 * (1 - t) * t * ((r.us + r.ut) / 2) + t ** 3 * r.ut;
  const v = r.vs + (r.vt - r.vs) * ease(t) + across;
  return scene.toScreen(u, v);
};

export const ribbonPath = (r: RibbonGeom, scene: Scene, width = r.width): string => {
  const um = (r.us + r.ut) / 2;
  const p = (u: number, v: number): string => {
    const s = scene.toScreen(u, v);
    return `${s.x.toFixed(1)},${s.y.toFixed(1)}`;
  };
  return `M${p(r.us, r.vs)}C${p(um, r.vs)} ${p(um, r.vt)} ${p(r.ut, r.vt)}`
    + `L${p(r.ut, r.vt + width)}C${p(um, r.vt + width)} ${p(um, r.vs + width)} ${p(r.us, r.vs + width)}Z`;
};

export const loopPath = (points: readonly Point[], width: number): string => {
  const radius = 8 + width / 2;
  const [first, ...rest] = points;
  if (!first) return '';
  const segments = rest.map((pt, i) => {
    const prev = points[i] ?? pt;
    const next = rest[i + 1];
    if (!next) return `L${pt.x.toFixed(1)},${pt.y.toFixed(1)}`;
    const inLen = Math.hypot(pt.x - prev.x, pt.y - prev.y);
    const outLen = Math.hypot(next.x - pt.x, next.y - pt.y);
    const rIn = Math.min(radius, inLen / 2);
    const rOut = Math.min(radius, outLen / 2);
    const a = { x: pt.x - ((pt.x - prev.x) / (inLen || 1)) * rIn, y: pt.y - ((pt.y - prev.y) / (inLen || 1)) * rIn };
    const b = { x: pt.x + ((next.x - pt.x) / (outLen || 1)) * rOut, y: pt.y + ((next.y - pt.y) / (outLen || 1)) * rOut };
    return `L${a.x.toFixed(1)},${a.y.toFixed(1)}Q${pt.x.toFixed(1)},${pt.y.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}`;
  });
  return `M${first.x.toFixed(1)},${first.y.toFixed(1)}${segments.join('')}`;
};

export const polylinePoint = (points: readonly Point[], t: number): Point => {
  const lengths = points.slice(1).map((p, i) => Math.hypot(p.x - (points[i]?.x ?? 0), p.y - (points[i]?.y ?? 0)));
  const total = lengths.reduce((a, b) => a + b, 0);
  let left = Math.min(1, Math.max(0, t)) * total;
  for (let i = 0; i < lengths.length; i += 1) {
    const len = lengths[i] ?? 0;
    if (left <= len || i === lengths.length - 1) {
      const a = points[i] ?? { x: 0, y: 0 };
      const b = points[i + 1] ?? a;
      const f = len > 0 ? Math.min(1, left / len) : 0;
      return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f };
    }
    left -= len;
  }
  return points[0] ?? { x: 0, y: 0 };
};

export const nodeRect = (b: StationBox, scene: Scene): { x: number; y: number; w: number; h: number } => {
  const a = scene.toScreen(b.u0, b.v0);
  const z = scene.toScreen(b.u1, b.v1);
  return { x: a.x, y: a.y, w: z.x - a.x, h: z.y - a.y };
};
