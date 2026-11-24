// Intended use: geometry for one frame. Places columns, nodes, ribbons, queue piles,
// capacity brackets and retry loops in flow coordinates (u along the flow, v across),
// then maps them to the screen for landscape or portrait.

import { config } from './config.js';
import { clientIds, nodes, type NodeId } from './data.js';
import {
  clientCount, columnTotals, incoming, links, nodeIndex, outgoing, type FlowLink, type Frame,
} from './model.js';

// ----------------------------------------
// Shapes
// ----------------------------------------
export interface Point {
  x: number;
  y: number;
}

export interface NodeBox {
  id: NodeId;
  u0: number;
  u1: number;
  v0: number;
  v1: number;
  visible: boolean;
  pileU: number;
  capacityV: number;
  labelV: number;
}

export interface RibbonGeom {
  link: FlowLink;
  us: number;
  ut: number;
  vs: number;
  vt: number;
  width: number;
  lanes: number[];
}

export interface RetryGeom {
  client: number;
  width: number;
  points: Point[];
}

export interface Scene {
  portrait: boolean;
  compact: boolean;
  k: number;
  nodes: NodeBox[];
  ribbons: RibbonGeom[];
  retries: RetryGeom[];
  toScreen: (u: number, v: number) => Point;
}

export interface Bounds {
  width: number;
  height: number;
  maxColumn: number;
  maxRetry: number;
}

// ----------------------------------------
// Day-wide scale, so widths compare across hours
// ----------------------------------------
export const dayBounds = (day: readonly Frame[]): Omit<Bounds, 'width' | 'height'> => ({
  maxColumn: Math.max(...day.flatMap((f) => columnTotals(f))),
  maxRetry: Math.max(...day.map((f) => f.retry.reduce((a, b) => a + b, 0))),
});

const nodeValue = (frame: Frame, id: NodeId): number => frame.throughput[nodeIndex.get(id) ?? 0] ?? 0;
const visibleAt = (value: number): number => Math.min(1, value / 20);

// ----------------------------------------
// Labels pushed apart along each column
// ----------------------------------------
const spreadLabels = (wanted: number[], span: number, lo: number, hi: number): number[] => {
  const down = wanted.reduce<number[]>((acc, w) => [...acc, Math.max(w, (acc[acc.length - 1] ?? -Infinity) + span)], []);
  const overflow = Math.max(0, (down[down.length - 1] ?? 0) - hi);
  return down.map((v) => Math.max(lo, v - overflow));
};

// ----------------------------------------
// Scene for one frame
// ----------------------------------------
export const buildScene = (frame: Frame, bounds: Bounds): Scene => {
  const { nodeThickness, nodeGap, laneGap, columns, labelSpan, pileReferenceSeconds, pileShare } = config.layout;
  // The flow runs along the figure's longer side, and always top to bottom on phones.
  const portrait = bounds.width < config.layout.portraitBelow || bounds.height > bounds.width * config.layout.portraitAspect;
  const U = portrait ? bounds.height : bounds.width;
  const V = portrait ? bounds.width : bounds.height;
  const toScreen = (u: number, v: number): Point => (portrait ? { x: v, y: u } : { x: u, y: v });
  const maxStack = Math.max(...Array.from({ length: columns }, (_, c) => nodes.filter((n) => n.column === c).length));
  const usable = V - laneGap * 3 - nodeGap * (maxStack - 1);
  const k = usable / (bounds.maxColumn + bounds.maxRetry);
  const lane = bounds.maxRetry * k + laneGap * 2;
  const uStart = lane;
  const uEnd = U - laneGap - nodeThickness;
  const vTop = laneGap;
  const vBottom = V - lane;
  // Gaps are weighted: the last one holds both the store labels and the outcome labels.
  const gaps = config.layout.columnGaps;
  const gapTotal = gaps.reduce((a, b) => a + b, 0);
  const colU = (c: number): number => uStart + ((uEnd - uStart) * gaps.slice(0, c).reduce((a, b) => a + b, 0)) / gapTotal;
  const pileMax = ((uEnd - uStart) / (columns - 1)) * pileShare;

  const boxes = Array.from({ length: columns }, (_, c) => nodes.filter((n) => n.column === c)).flatMap((column) => {
    const stacked = column.reduce<{ v: number; out: NodeBox[] }>((acc, n) => {
      const value = nodeValue(frame, n.id);
      const height = value * k;
      const u0 = colU(n.column);
      const queue = frame.queue[nodeIndex.get(n.id) ?? 0] ?? 0;
      const delaySeconds = n.capacity ? queue / n.capacity : 0;
      const reference = n.queue === 'messages' ? pileReferenceSeconds.messages : pileReferenceSeconds.requests;
      const box: NodeBox = {
        id: n.id,
        u0,
        u1: u0 + nodeThickness,
        v0: acc.v,
        v1: acc.v + height,
        visible: value >= 1,
        pileU: Math.min(1, delaySeconds / reference) * pileMax,
        capacityV: n.capacity && value / n.capacity > config.layout.capacityShownAbove ? n.capacity * k : 0,
        labelV: acc.v + height / 2,
      };
      return { v: acc.v + height + nodeGap * visibleAt(value), out: [...acc.out, box] };
    }, { v: vTop, out: [] });
    if (portrait) return stacked.out;
    const visible = stacked.out.filter((b) => b.visible);
    const spread = spreadLabels(visible.map((b) => b.labelV), labelSpan, vTop, vBottom - labelSpan / 2);
    return stacked.out.map((b) => {
      const i = visible.indexOf(b);
      return i < 0 ? b : { ...b, labelV: spread[i] ?? b.labelV };
    });
  });
  const boxOf = new Map(boxes.map((b) => [b.id, b]));

  // Ribbons: outgoing stacked by target order at the source, incoming by source order at the target.
  const offsets = (list: readonly FlowLink[]): Map<number, number> => list
    .reduce<{ at: number; map: Map<number, number> }>((acc, l) => {
      acc.map.set(l.index, acc.at);
      return { at: acc.at + (frame.linkTotal[l.index] ?? 0) * k, map: acc.map };
    }, { at: 0, map: new Map() }).map;
  const order = (id: NodeId): number => nodeIndex.get(id) ?? 0;
  const sourceOffsets = new Map(nodes.map((n) => [n.id, offsets([...(outgoing.get(n.id) ?? [])].sort((a, b) => order(a.target) - order(b.target)))]));
  const targetOffsets = new Map(nodes.map((n) => [n.id, offsets([...(incoming.get(n.id) ?? [])].sort((a, b) => order(a.source) - order(b.source)))]));

  const ribbons = links.map((link): RibbonGeom => {
    const s = boxOf.get(link.source);
    const t = boxOf.get(link.target);
    const width = (frame.linkTotal[link.index] ?? 0) * k;
    const lanes = clientIds.map((_, c) => (frame.flow[link.index * clientCount + c] ?? 0) * k);
    return {
      link,
      us: s?.u1 ?? 0,
      ut: t?.u0 ?? 0,
      vs: (s?.v0 ?? 0) + (sourceOffsets.get(link.source)?.get(link.index) ?? 0),
      vt: (t?.v0 ?? 0) + (targetOffsets.get(link.target)?.get(link.index) ?? 0),
      width,
      lanes,
    };
  });

  // Retry loops: down from beneath the failure outcomes, along the far edge, back up to each client.
  // Web runs outermost (rightmost going down, lowest across, leftmost going up), so no two loops cross.
  const failTop = boxOf.get('serverErr');
  const failBottom = boxOf.get('timeout');
  const widths = clientIds.map((_, c) => (frame.retry[c] ?? 0) * k);
  const failEdge = Math.max(failTop?.v1 ?? 0, failBottom?.v1 ?? 0);
  const failU = Math.max(failTop?.u1 ?? 0, failBottom?.u1 ?? 0);
  const retries = clientIds.map((id, c): RetryGeom => {
    const w = widths[c] ?? 0;
    const inner = widths.slice(c + 1).reduce((a, b) => a + b, 0);
    const before = widths.slice(0, c).reduce((a, b) => a + b, 0);
    const client = boxOf.get(id);
    const uDown = failU - before - w / 2;
    const vLane = vBottom + laneGap + inner + w / 2;
    const uLeft = (client?.u0 ?? 0) - laneGap - inner - w / 2;
    const vEnd = (client?.v1 ?? 0) - w / 2;
    return {
      client: c,
      width: w,
      points: [
        toScreen(uDown, failEdge),
        toScreen(uDown, vLane),
        toScreen(uLeft, vLane),
        toScreen(uLeft, vEnd),
        toScreen(client?.u0 ?? 0, vEnd),
      ],
    };
  });

  const compact = portrait || colU(columns - 1) - colU(columns - 2) < config.layout.shortLabelsBelow;
  return {
    portrait, compact, k, nodes: boxes, ribbons, retries, toScreen,
  };
};

// ----------------------------------------
// Paths and positions
// ----------------------------------------
const ease = (t: number): number => t * t * (3 - 2 * t);

export const ribbonPoint = (r: RibbonGeom, t: number, across: number, scene: Scene): Point => {
  const u = (1 - t) ** 3 * r.us + 3 * (1 - t) * t * ((r.us + r.ut) / 2) + t ** 3 * r.ut;
  const v = r.vs + (r.vt - r.vs) * ease(t) + across;
  return scene.toScreen(u, v);
};

export const ribbonPath = (r: RibbonGeom, scene: Scene): string => {
  const um = (r.us + r.ut) / 2;
  const p = (u: number, v: number): string => {
    const s = scene.toScreen(u, v);
    return `${s.x.toFixed(1)},${s.y.toFixed(1)}`;
  };
  return `M${p(r.us, r.vs)}C${p(um, r.vs)} ${p(um, r.vt)} ${p(r.ut, r.vt)}`
    + `L${p(r.ut, r.vt + r.width)}C${p(um, r.vt + r.width)} ${p(um, r.vs + r.width)} ${p(r.us, r.vs + r.width)}Z`;
};

export const retryPath = (r: RetryGeom): string => {
  const radius = 10 + r.width / 2;
  const [first, ...rest] = r.points;
  if (!first) return '';
  const segments = rest.map((pt, i) => {
    const prev = r.points[i] ?? pt;
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
  const target = Math.min(1, Math.max(0, t)) * total;
  const found = lengths.reduce<{ left: number; point: Point | null }>((acc, len, i) => {
    if (acc.point) return acc;
    if (acc.left > len && i < lengths.length - 1) return { left: acc.left - len, point: null };
    const a = points[i] ?? { x: 0, y: 0 };
    const b = points[i + 1] ?? a;
    const f = len > 0 ? Math.min(1, acc.left / len) : 0;
    return { left: 0, point: { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f } };
  }, { left: target, point: null });
  return found.point ?? points[0] ?? { x: 0, y: 0 };
};

export const nodeRect = (b: NodeBox, scene: Scene): { x: number; y: number; w: number; h: number } => {
  const a = scene.toScreen(b.u0, b.v0);
  const z = scene.toScreen(b.u1, b.v1);
  return { x: a.x, y: a.y, w: z.x - a.x, h: z.y - a.y };
};
