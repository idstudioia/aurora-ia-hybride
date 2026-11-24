// Rings layout of the brain view (29/10/2026): Rubric-style organisation, AURORA look.
// Centre: the core. Around it, a MEMORY disk of points in concentric arcs (the four levels of
// LEVELS as angular sectors, one sub-sector per category), then a ROUTINES ring and, outermost,
// a TOOLS ring of hexagons. Positions are computed by a pure function (deterministic, no force
// simulation), so 1 000 memories + 250 tasks stay at 60 fps: the slow rotation only changes
// three angles per frame. RingSim reuses BrainSim's node life cycle (appear, fade, replay
// cursor, event keys), so effects.ts and render.ts work unchanged on it.
import type { GraphGroup, GraphNode, GraphRoutine, GraphTool, GraphToolGroup, MemoryGraphData } from '../../lib/types';
import { LEVELS, isLevelId, nodeLevel, type LevelId } from './model';
import { BrainSim, hubId, type SimNode } from './sim';

export type RingId = 'memory' | 'routines' | 'tools';
export const RING_IDS: RingId[] = ['memory', 'routines', 'tools'];

/** One point of the memory disk: a memory, a conversation or a task. */
export interface RingItem { id: string; level: LevelId; category: string; weight: number; order: number }
export interface RingInput {
  items: RingItem[]; groups: GraphGroup[]; tools: GraphTool[]; routines: GraphRoutine[];
  /** Tool map of the page Tâches (11/11/2026): when present, the layout is the tools alone, one sector per large
   *  family (toolMapLayout). Absent: the memory rings, computed exactly as before. */
  toolGroups?: GraphToolGroup[];
}

/** Polar position in the frame of its ring (the ring's rotation is added when placing the node). */
export interface RingSlot { ring: RingId; r: number; a: number; size: number }
export interface RingSector { level: LevelId; a0: number; a1: number; shown: number; hubR: number; hubA: number }
/** A category sub-sector: `count` is the true number in the database, `shown` the points drawn. */
export interface RingCategory { level: LevelId; category: string; a0: number; a1: number; count: number; shown: number; outer: number }
/** Tool map: the sector of a large family. `count`: its tools drawn; `builtins`: its capabilities without tool. */
export interface RingToolSector { id: string; title: string; a0: number; a1: number; count: number; builtins: number }
export interface RingGeometry {
  r0: number;             // inner radius of the memory disk
  r1: number;             // outer radius of the memory disk
  spacing: number;        // distance between two neighbouring points
  routineR: number;
  toolR: number;
  outer: number;          // outer edge of the drawing (tools included)
  sectors: RingSector[];
  categories: RingCategory[];
  /** Data points, the four level hubs ("level:<id>"), routines ("routine:<id>") and tools ("tool:<name>"). */
  slots: Map<string, RingSlot>;
  /** Tool map only: one sector per large family, in the order given. Absent on the memory rings. */
  toolSectors?: RingToolSector[];
}

export const RING_R0 = 110;           // clear room around the core (its halo, its label and the level names)
const HUB_INSET = 10;                 // level hubs sit on the inner rim of their sector
export const RING_HUB_R = 7;
const LEVEL_GAP = 0.07;               // rad between two level sectors
const CAT_GAP = 0.01;                 // rad between two categories (points already keep half a step from the edge)
const MIN_LEVEL_SHARE = 0.08;         // each level keeps at least 8 % of the turn, even when empty
const MIN_CAT_ANGLE = 0.035;          // readable minimum of a category (rad): one point per row, its label on zoom
const MIN_DISK = 48;                  // minimum thickness of the disk
const ROUTINE_GAP = 46;               // disk edge → routines ring (room for the category labels)
const TOOL_GAP = 44;                  // routines ring → tools ring
const RING_ITEM_GAP = 5;
const START = -Math.PI / 2;           // sectors start at the top and run clockwise
const TAU = Math.PI * 2;

export const toolNodeId = (name: string) => `tool:${name}`;
export const routineNodeId = (id: number) => `routine:${id}`;

/** Point spacing: denser when there are many points, so 1 300 points still fit a screen. */
export function ringSpacing(n: number): number {
  return n <= 150 ? 13 : n <= 400 ? 11.5 : n <= 800 ? 10.5 : 9.5;
}

/** Category key of a data node, exactly as the service counts it (store.graph): conversations / category / 'autre'. */
export function ringCategory(type: string, category: string | undefined): string {
  return type === 'conversation' ? 'conversations' : category || 'autre';
}

/** Tool hexagon size: logarithmic in the real number of uses (0 → 5, 9 → 7.7, 99 → 10.3, 999+ → 13). */
export const toolSize = (uses: number) => 5 + 8 * Math.min(1, Math.log10(1 + Math.max(0, uses || 0)) / 3);
/** Routine size: logarithmic in its real runs (0 → 4.5, 99+ → 9). */
export const routineSize = (runs: number) => 4.5 + 4.5 * Math.min(1, Math.log10(1 + Math.max(0, runs || 0)) / 2);
/** Point size of the disk, from the node's weight (0..1). */
const pointSize = (s: number, weight: number) => s * (0.19 + 0.2 * Math.max(0, Math.min(1, weight)));

/**
 * Split `total` in proportion to `weights`, no part below its minimum (water-filling: the parts
 * too small are raised to their minimum, the others stay exactly proportional to each other).
 */
function share(total: number, weights: number[], mins: number[]): number[] {
  const sumMin = mins.reduce((a, b) => a + b, 0);
  if (sumMin >= total) return mins.map(m => (sumMin > 0 ? (m / sumMin) * total : total / mins.length));
  const atMin = new Set<number>();
  for (;;) {
    let rest = total;
    let restW = 0;
    let free = 0;
    weights.forEach((w, i) => { if (atMin.has(i)) rest -= mins[i]; else { restW += w; free++; } });
    const part = (i: number) => (restW > 0 ? (rest * weights[i]) / restW : rest / free);
    const low = weights.findIndex((_, i) => !atMin.has(i) && part(i) < mins[i]);
    if (low < 0) return weights.map((_, i) => (atMin.has(i) ? mins[i] : part(i)));
    // Raise every part below its minimum in one pass (each pass fixes at least one: ≤ n passes).
    weights.forEach((_, i) => { if (!atMin.has(i) && part(i) < mins[i]) atMin.add(i); });
  }
}

const byOrder = (a: RingItem, b: RingItem) => a.order - b.order || a.id.localeCompare(b.id, 'en', { numeric: true });

/** Fill a sub-sector row by row, from the inner rim outward; returns the outer radius reached. */
function pack(items: RingItem[], a0: number, a1: number, s: number, slots: Map<string, RingSlot>): number {
  const span = Math.max(0, a1 - a0);
  let outer = RING_R0;
  let i = 0;
  for (let row = 0; i < items.length; row++) {
    const r = RING_R0 + s * (row + 0.5);
    const cap = Math.max(1, Math.floor((span * r) / s));
    const m = Math.min(cap, items.length - i);
    const step = span / cap;
    const first = a0 + step * ((cap - m) / 2 + 0.5);   // a partial last row stays centred
    for (let j = 0; j < m; j++) {
      const it = items[i + j];
      slots.set(it.id, { ring: 'memory', r, a: first + j * step, size: pointSize(s, it.weight) });
    }
    i += m;
    outer = r + s / 2;
  }
  return outer;
}

/** Items around a full ring, each taking an angle proportional to its size; sizes shrink if the ring is full. */
function placeRing(list: { id: string; size: number }[], r: number, ring: RingId, slots: Map<string, RingSlot>): number {
  if (!list.length) return 0;
  const need = list.reduce((t, x) => t + 2 * x.size + RING_ITEM_GAP, 0);
  const room = TAU * r * 0.92;
  const scale = need > room ? room / need : 1;
  let cum = 0;
  let max = 0;
  for (const x of list) {
    const w = 2 * x.size + RING_ITEM_GAP;
    slots.set(x.id, { ring, r, a: START + (TAU * (cum + w / 2)) / need, size: x.size * scale });
    cum += w;
    max = Math.max(max, x.size * scale);
  }
  return max;
}

// ---------------------------------------------------------------- tool map (page Tâches, 11/11/2026)
// The tools alone around the core, one sector per large family: the look of the memory disk, hexagons instead of
// points. Reached only through RingInput.toolGroups; nothing above or below changes for the memory rings.

const MAP_R0 = 96;                    // clear room around the core (its halo and its name)
const MAP_SIZE_MIN = 10;              // a tool never used
const MAP_SIZE_MAX = 17;              // 999 uses and more
const MAP_CELL = 2 * MAP_SIZE_MAX + 6;   // one cell holds the largest hexagon: two hexagons never touch
const MAP_GROUP_GAP = 0.09;           // rad between two families
const MAP_EMPTY_ANGLE = 0.3;          // a family without tool (built-in capabilities) keeps a visible sector
const MAP_LABEL_X = 150;              // room kept beside the disk for the family names (fit-to-view)
const MAP_LABEL_Y = 36;               // and above / below it
/** Sector of the tools whose family is in no group given: shown apart, never dropped. */
export const OTHER_TOOLS: GraphToolGroup = { id: '(autres)', title: 'Autres outils' };

/** Hexagon size on the tool map: the logarithmic scale of toolSize (real uses), larger since tools are the subject. */
export const toolMapSize = (uses: number) => MAP_SIZE_MIN + (MAP_SIZE_MAX - MAP_SIZE_MIN) * ((toolSize(uses) - 5) / 8);

const byText = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);   // code units: the same order on every machine

/**
 * Pure layout of the tool map. Sectors follow `groups` (the service's order), each as wide as its number of tools;
 * inside one, the tools (sorted by family, then name) fill rows of cells from the inner rim outward. The result
 * depends only on the tools and the groups, never on the order the tools arrive in.
 */
function toolMapLayout(tools: GraphTool[], groups: GraphToolGroup[]): RingGeometry {
  const slots = new Map<string, RingSlot>();
  const byId = new Map<string, { group: GraphToolGroup; tools: GraphTool[] }>();
  const list: { group: GraphToolGroup; tools: GraphTool[] }[] = [];
  for (const g of groups) {
    if (byId.has(g.id)) continue;
    const entry = { group: g, tools: [] as GraphTool[] };
    byId.set(g.id, entry);
    list.push(entry);
  }
  let other: { group: GraphToolGroup; tools: GraphTool[] } | null = null;
  const seen = new Set<string>();
  for (const t of tools) {
    if (seen.has(t.name)) continue;
    seen.add(t.name);
    let entry = t.group === undefined ? undefined : byId.get(t.group);
    if (!entry) {
      if (!other) { other = { group: OTHER_TOOLS, tools: [] }; list.push(other); }
      entry = other;
    }
    entry.tools.push(t);
  }
  // The memory hubs sit on the core (they are not drawn here): a particle « actions → tool » leaves from the centre.
  for (const l of LEVELS) slots.set(hubId(l.id), { ring: 'tools', r: 0, a: 0, size: RING_HUB_R });

  const n = list.length;
  const withTools = list.filter(e => e.tools.length > 0).length;
  const gap = n ? Math.min(MAP_GROUP_GAP, (TAU * 0.2) / n) : 0;
  const free = TAU - gap * n;
  const emptyAngle = n > withTools ? Math.min(MAP_EMPTY_ANGLE, (free * 0.4) / (n - withTools)) : 0;
  // Inner radius: every family with tools holds at least one cell on its first row (wider when families are many).
  const forTools = free - (n - withTools) * emptyAngle;
  const r0 = Math.max(MAP_R0, withTools ? (withTools * MAP_CELL * 1.02) / forTools - MAP_CELL / 2 : MAP_R0);
  const minAngle = MAP_CELL / (r0 + MAP_CELL / 2);
  const widths = n ? share(free, list.map(e => e.tools.length), list.map(e => (e.tools.length ? minAngle : emptyAngle))) : [];

  const sectors: RingToolSector[] = [];
  let outer = r0 + MAP_CELL;   // one row thick at least, even with no tool at all
  let cursor = START;
  list.forEach((e, i) => {
    const a0 = cursor + gap / 2;
    const a1 = a0 + widths[i];
    cursor = a1 + gap / 2;
    const sorted = [...e.tools].sort((a, b) => byText(a.family ?? '', b.family ?? '') || byText(a.name, b.name));
    // The rows needed (a row holds as many cells as its arc is long), then the tools spread over them in proportion
    // to what each row holds: every row is about as full as the others, instead of full rows and a thin last one.
    const caps: number[] = [];
    for (let held = 0; held < sorted.length;) {
      const cap = Math.max(1, Math.floor(((a1 - a0) * (r0 + MAP_CELL * (caps.length + 0.5))) / MAP_CELL + 1e-9));
      caps.push(cap);
      held += cap;
    }
    let left = sorted.length;
    let room = caps.reduce((t, c) => t + c, 0);
    let k = 0;
    caps.forEach((cap, row) => {
      const m = Math.max(left - (room - cap), Math.min(cap, Math.round((left * cap) / room)));
      const r = r0 + MAP_CELL * (row + 0.5);
      const step = (a1 - a0) / cap;
      const first = a0 + step * ((cap - m) / 2 + 0.5);   // a row that is not full stays centred
      for (let j = 0; j < m; j++) {
        const t = sorted[k + j];
        slots.set(toolNodeId(t.name), { ring: 'tools', r, a: first + j * step, size: toolMapSize(t.uses) });
      }
      k += m;
      left -= m;
      room -= cap;
      outer = Math.max(outer, r + MAP_CELL / 2);
    });
    sectors.push({ id: e.group.id, title: e.group.title, a0, a1, count: sorted.length, builtins: Math.max(0, e.group.builtins ?? 0) });
  });

  return {
    r0, r1: outer, spacing: MAP_CELL, routineR: outer, toolR: outer, outer: outer + 4,
    sectors: [], categories: [], slots, toolSectors: sectors,
  };
}

/** Pure layout: positions of every point, hub, routine and tool from the data alone. */
export function ringLayout(input: RingInput): RingGeometry {
  if (input.toolGroups) return toolMapLayout(input.tools, input.toolGroups);   // tool map (page Tâches)
  const s = ringSpacing(input.items.length);
  const slots = new Map<string, RingSlot>();

  // Categories of each level: every group counted by the service (even with no point sent),
  // plus the points' own categories (older service, or a point newer than the counts).
  const byLevel = new Map<LevelId, Map<string, { count: number; items: RingItem[] }>>(LEVELS.map(l => [l.id, new Map()]));
  const cat = (level: LevelId, name: string) => {
    const m = byLevel.get(level)!;
    let c = m.get(name);
    if (!c) { c = { count: 0, items: [] }; m.set(name, c); }
    return c;
  };
  for (const g of input.groups) if (isLevelId(g.level) && g.count > 0) cat(g.level, g.category || 'autre').count += g.count;
  for (const it of input.items) cat(it.level, it.category).items.push(it);

  const levels = LEVELS.map(l => {
    const cats = [...byLevel.get(l.id)!.entries()]
      .map(([name, c]) => ({ name, count: Math.max(c.count, c.items.length), items: c.items.sort(byOrder) }))
      .sort((a, b) => a.name.localeCompare(b.name, 'fr'));
    return { id: l.id, cats, shown: cats.reduce((t, c) => t + c.items.length, 0) };
  });

  // A category with points is at least one step wide on the first row (its points never crowd
  // the neighbour's); a category with nothing sent only needs room for its label.
  const catMin = (shown: number) => (shown > 0 ? Math.max(MIN_CAT_ANGLE, s / (RING_R0 + s / 2)) : MIN_CAT_ANGLE);
  const gaps = (k: number) => Math.max(0, k - 1) * CAT_GAP;

  // Angles: the same angle per point across the whole disk (categories, not levels, are the unit),
  // each category at least its minimum; a nearly empty level is raised to MIN_LEVEL_SHARE.
  const free = TAU - LEVEL_GAP * levels.length;
  const levelMinW = MIN_LEVEL_SHARE * free;
  const raised = new Set<LevelId>();
  const catWidth = new Map<string, number>();
  const keyOf = (level: LevelId, name: string) => `${level}|${name}`;
  for (let pass = 0; pass <= levels.length; pass++) {
    const open = levels.filter(l => !raised.has(l.id));
    if (!open.length) break;
    const avail = free - raised.size * levelMinW - open.reduce((t, l) => t + gaps(l.cats.length), 0);
    const list = open.flatMap(l => l.cats.map(c => ({ key: keyOf(l.id, c.name), shown: c.items.length })));
    const ws = share(Math.max(0, avail), list.map(x => x.shown), list.map(x => catMin(x.shown)));
    catWidth.clear();
    list.forEach((x, i) => catWidth.set(x.key, ws[i]));
    const low = open.filter(l => l.cats.reduce((t, c) => t + (catWidth.get(keyOf(l.id, c.name)) ?? 0), 0) + gaps(l.cats.length) < levelMinW);
    if (!low.length) break;
    for (const l of low) raised.add(l.id);
  }
  const allRaised = raised.size === levels.length;
  const widths = levels.map(l => {
    if (allRaised) return free / levels.length;   // nothing to share in proportion: four equal sectors
    if (raised.has(l.id)) return levelMinW;
    return l.cats.reduce((t, c) => t + (catWidth.get(keyOf(l.id, c.name)) ?? 0), 0) + gaps(l.cats.length);
  });
  // Categories of a raised level share its sector in proportion to their points.
  levels.forEach((l, i) => {
    if (!raised.has(l.id) && !allRaised) return;
    const ws = share(Math.max(0, widths[i] - gaps(l.cats.length)), l.cats.map(c => c.items.length), l.cats.map(c => catMin(c.items.length)));
    l.cats.forEach((c, j) => catWidth.set(keyOf(l.id, c.name), ws[j]));
  });

  const sectors: RingSector[] = [];
  const categories: RingCategory[] = [];
  let r1 = RING_R0 + MIN_DISK;
  let cursor = START;
  levels.forEach((l, i) => {
    const a0 = cursor + LEVEL_GAP / 2;
    const a1 = a0 + widths[i];
    cursor = a1 + LEVEL_GAP / 2;
    if (l.cats.length) {
      let c0 = a0;
      l.cats.forEach(c => {
        const c1 = c0 + (catWidth.get(keyOf(l.id, c.name)) ?? 0);
        const outer = pack(c.items, c0, c1, s, slots);
        r1 = Math.max(r1, outer);
        categories.push({ level: l.id, category: c.name, a0: c0, a1: c1, count: c.count, shown: c.items.length, outer });
        c0 = c1 + CAT_GAP;
      });
    }
    const hubA = (a0 + a1) / 2;
    const hubR = RING_R0 - HUB_INSET;
    sectors.push({ level: l.id, a0, a1, shown: l.shown, hubR, hubA });
    slots.set(hubId(l.id), { ring: 'memory', r: hubR, a: hubA, size: RING_HUB_R });
  });

  const routineR = r1 + ROUTINE_GAP;
  placeRing([...input.routines].sort((a, b) => a.id - b.id)
    .map(r => ({ id: routineNodeId(r.id), size: routineSize(r.runs) })), routineR, 'routines', slots);
  const toolR = routineR + TOOL_GAP;
  const maxTool = placeRing([...input.tools].sort((a, b) => a.name.localeCompare(b.name, 'en'))
    .map(t => ({ id: toolNodeId(t.name), size: toolSize(t.uses) })), toolR, 'tools', slots);

  return {
    r0: RING_R0, r1, spacing: s, routineR, toolR, outer: toolR + Math.max(8, maxTool) + 4,
    sectors, categories, slots,
  };
}

/** Counts recomputed from the nodes received, for a service older than `groups` (29/10/2026). */
export function fallbackGroups(nodes: GraphNode[]): GraphGroup[] {
  const map = new Map<string, GraphGroup>();
  for (const n of nodes) {
    if (n.type !== 'memory' && n.type !== 'conversation' && n.type !== 'run') continue;
    const level = nodeLevel(n);
    if (!level) continue;
    const category = ringCategory(n.type, n.category);
    const key = `${level}|${category}`;
    const g = map.get(key) ?? { level, category, count: 0, shown: 0 };
    g.count++; g.shown++;
    map.set(key, g);
  }
  return [...map.values()];
}

export function totalsOf(groups: GraphGroup[]): Record<LevelId, number> {
  const t: Record<LevelId, number> = { immediate: 0, persistent: 0, semantic: 0, actions: 0 };
  for (const g of groups) if (isLevelId(g.level)) t[g.level] += g.count;
  return t;
}

// ---------------------------------------------------------------- simulation

/** What render.ts needs to draw the rings (RingSim implements it). */
export interface RingView {
  geo: RingGeometry;
  rot: Record<RingId, number>;
  /** Tool and routine nodes (not part of `nodes`: the graph's data life cycle does not apply to them). */
  extras: SimNode[];
  /** False when the service sent no `tools` / `routines` (older service): the ring says so. */
  toolsKnown: boolean;
  routinesKnown: boolean;
  /** True totals per level (service `totals`, or recomputed from the nodes). */
  totals: Record<LevelId, number>;
}

// Slow turns, in rad per ms: one turn in 4 min (memory), 3 min the other way (routines), 5 min (tools).
const SPEED: Record<RingId, number> = { memory: TAU / 240_000, routines: -TAU / 180_000, tools: TAU / 300_000 };
const EASE = 0.14;                    // per 60 Hz frame, when a point moves to a new slot
const EXTRA_DIE_MS = 750;

const angleDelta = (to: number, from: number) => {
  let d = (to - from) % TAU;
  if (d > Math.PI) d -= TAU;
  if (d < -Math.PI) d += TAU;
  return d;
};

function extraNode(id: string, type: 'tool' | 'routine', label: string, now: number, reduced: boolean): SimNode {
  return {
    id, type, key: null, num: null, label, category: '', kind: '', level: null, status: '', weight: 0, createdMs: null,
    group: type, x: 0, y: 0, vx: 0, vy: 0, r: 5, fixed: false, ax: 0, ay: 0, bornAt: reduced ? 0 : now, hiddenUntil: 0,
    dyingAt: 0, dyingByEvent: false, inData: true, hidden: false, raw: null,
  };
}

/** BrainSim with computed positions: no force simulation, a polar slot per node, three rotating frames. */
export class RingSim extends BrainSim implements RingView {
  geo: RingGeometry = ringLayout({ items: [], groups: [], tools: [], routines: [] });
  rot: Record<RingId, number> = { memory: 0, routines: 0, tools: 0 };
  extras: SimNode[] = [];
  toolsKnown = false;
  routinesKnown = false;
  totals: Record<LevelId, number> = { immediate: 0, persistent: 0, semantic: 0, actions: 0 };
  /** Set by the view every frame: rotation switch on, no reduced motion, nothing hovered. */
  rotating = false;
  /** Reduced motion: points jump to their slot instead of gliding. */
  instant = false;
  /** Tool map of the page Tâches: the tools alone, in sectors by large family (data.tool_groups). Fixed at
   *  creation; false: the memory rings, exactly as before. */
  readonly toolMap: boolean;
  private extraById = new Map<string, SimNode>();
  private polar = new Map<string, { r: number; a: number }>();
  private data: MemoryGraphData | null = null;
  private last = 0;
  private settled = false;

  constructor(toolMap = false) {
    super();
    this.toolMap = toolMap;
    for (const l of LEVELS) this.hub(l.id).r = RING_HUB_R;
    this.layoutAnchors();
    this.settle();
  }

  resolve(ref: string): SimNode | undefined {
    return super.resolve(ref) ?? this.extraById.get(ref);
  }

  sync(data: MemoryGraphData, now: number, reduced: boolean): string[] {
    this.data = data;
    this.syncExtras(data, now, reduced);
    return super.sync(data, now, reduced);   // ends with layoutAnchors(): the ring geometry
  }

  private syncExtras(data: MemoryGraphData, now: number, reduced: boolean) {
    this.toolsKnown = Array.isArray(data.tools);
    this.routinesKnown = Array.isArray(data.routines);
    const seen = new Set<string>();
    const upsert = (id: string, type: 'tool' | 'routine', label: string) => {
      seen.add(id);
      let n = this.extraById.get(id);
      if (!n) {
        n = extraNode(id, type, label, now, reduced);
        this.extras.push(n);
        this.extraById.set(id, n);
      }
      n.dyingAt = 0;
      n.label = label;
      return n;
    };
    for (const t of data.tools ?? []) upsert(toolNodeId(t.name), 'tool', t.title || t.name).tool = t;
    for (const r of data.routines ?? []) upsert(routineNodeId(r.id), 'routine', r.name || `Routine ${r.id}`).routine = r;
    for (const n of this.extras) if (!seen.has(n.id) && !n.dyingAt) n.dyingAt = now;
  }

  /** Ring geometry from the nodes held (hidden and fading ones keep their slot: a replay never reshuffles). */
  protected layoutAnchors(): void {
    const data = this.data;
    const items: RingItem[] = [];
    for (const n of this.nodes) {
      if (n.fixed || !n.level) continue;
      items.push({ id: n.id, level: n.level, category: ringCategory(n.type, n.raw?.category), weight: n.weight, order: n.createdMs ?? 0 });
    }
    const groups = data?.groups ?? fallbackGroups(data?.nodes ?? []);
    const input: RingInput = { items, groups, tools: data?.tools ?? [], routines: data?.routines ?? [] };
    if (this.toolMap) input.toolGroups = data?.tool_groups ?? [];
    this.geo = ringLayout(input);
    this.totals = data?.totals ?? totalsOf(groups);
    for (const n of this.nodes) { const slot = this.geo.slots.get(n.id); if (slot) n.r = slot.size; }
    for (const n of this.extras) { const slot = this.geo.slots.get(n.id); if (slot) n.r = slot.size; }
    this.settled = false;
  }

  /** Replay cursor: hidden points keep their slot and pop in place (easeOutBack) when the cursor passes them. */
  setCutoff(ms: number | null, now: number, reduced: boolean): void {
    for (const n of this.nodes) {
      if (n.fixed) continue;
      const hidden = ms !== null && n.createdMs !== null && n.createdMs > ms;
      if (hidden === n.hidden) continue;
      n.hidden = hidden;
      if (!hidden) n.bornAt = reduced ? 0 : Math.max(now, n.bornAt);
    }
  }

  /** Rotate the rings and glide points towards their slot. Returns true while something moves. */
  tick(now = 0): boolean {
    const dt = this.last && now > this.last ? Math.min(100, now - this.last) : 0;
    if (now) this.last = now;
    const turning = this.rotating && dt > 0;
    if (turning) for (const id of RING_IDS) this.rot[id] = (this.rot[id] + SPEED[id] * dt) % TAU;
    if (this.settled && !turning) return false;
    const f = this.instant ? 1 : 1 - Math.pow(1 - EASE, Math.max(1, dt / 16.7));
    let gliding = false;
    const place = (n: SimNode) => {
      const slot = this.geo.slots.get(n.id);
      if (!slot) return;   // the core, or a tool / routine fading out: stays where it is
      let p = this.polar.get(n.id);
      if (!p) { p = { r: slot.r, a: slot.a }; this.polar.set(n.id, p); }
      else if (p.r !== slot.r || p.a !== slot.a) {
        const dr = slot.r - p.r;
        const da = angleDelta(slot.a, p.a);
        if (f >= 1 || (Math.abs(dr) < 0.05 && Math.abs(da) < 2e-4)) { p.r = slot.r; p.a = slot.a; }
        else { p.r += dr * f; p.a += da * f; gliding = true; }
      }
      const a = p.a + this.rot[slot.ring];
      n.x = Math.cos(a) * p.r; n.y = Math.sin(a) * p.r;
      n.ax = n.x; n.ay = n.y;
    };
    for (const n of this.nodes) place(n);
    for (const n of this.extras) place(n);
    this.settled = !gliding;
    return gliding || turning;
  }

  /** Put every point on its slot at once (first paint, reduced motion). */
  settle(_maxTicks?: number): void {
    const instant = this.instant;
    this.instant = true;
    this.settled = false;
    this.tick(this.last);
    this.instant = instant;
  }

  prune(now: number, reduced: boolean): void {
    super.prune(now, reduced);
    if (this.extras.some(n => n.dyingAt && (reduced || now - n.dyingAt > EXTRA_DIE_MS))) {
      this.extras = this.extras.filter(n => {
        if (!n.dyingAt || (!reduced && now - n.dyingAt <= EXTRA_DIE_MS)) return true;
        this.extraById.delete(n.id);
        return false;
      });
    }
    if (this.polar.size > this.nodes.length + this.extras.length + 32) {
      for (const id of [...this.polar.keys()]) if (!this.byId.has(id) && !this.extraById.has(id)) this.polar.delete(id);
    }
  }

  /** Rotation-invariant bounds (a circle, plus the ring labels above it): fit-to-view never jitters. */
  bounds(): { minX: number; minY: number; maxX: number; maxY: number } {
    if (this.geo.toolSectors) {
      // Tool map: the family names are written outside the disk, straight (wide on the sides, two lines above and below).
      const X = this.geo.outer + MAP_LABEL_X; const Y = this.geo.outer + MAP_LABEL_Y;
      return { minX: -X, minY: -Y, maxX: X, maxY: Y };
    }
    const R = this.geo.outer + 30;
    return { minX: -R, minY: -R - 12, maxX: R, maxY: R };
  }

  hit(x: number, y: number, pad: number, now: number): SimNode | null {
    let best = super.hit(x, y, pad, now);
    if (best && this.toolMap && best.type === 'level') best = null;   // the memory hubs are not drawn on the tool map
    let bestD = best ? Math.hypot(best.x - x, best.y - y) : Infinity;
    for (const n of this.extras) {
      if (n.dyingAt || !this.presence(n, now)) continue;
      const d = Math.hypot(n.x - x, n.y - y);
      if (d <= n.r + pad && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }
}
