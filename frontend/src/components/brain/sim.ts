// Home-made force simulation for the brain view (no dependency): repulsion + collision,
// link springs, attraction towards the anchor of the node's level/category, velocity damping.
// The core and the four level hubs are fixed; everything else settles around them.
import type { GraphNode, GraphRoutine, GraphTool, MemoryGraphData } from '../../lib/types';
import { LEVELS, hash01, nodeKey, nodeLevel, nodeNum, type LevelId } from './model';

export interface SimNode {
  id: string;
  /** 'tool' / 'routine': rings layout only (RingSim, rings.ts), never in the lobes graph. */
  type: GraphNode['type'] | 'tool' | 'routine';
  key: string | null;       // "memory#12", "run#8"… for event lookup
  num: number | null;
  label: string;
  category: string;
  kind: string;
  level: LevelId | null;
  status: string;
  weight: number;
  createdMs: number | null;
  group: string;
  x: number; y: number; vx: number; vy: number;
  r: number;
  fixed: boolean;
  ax: number; ay: number;   // anchor (level hub / category cluster)
  bornAt: number;           // appear animation start (0 = steady)
  hiddenUntil: number;      // waits for an incoming particle before appearing
  dyingAt: number;          // fade-out start (0 = alive)
  dyingByEvent: boolean;
  inData: boolean;          // still present in the last API snapshot
  hidden: boolean;          // hidden by the replay time cursor
  raw: GraphNode | null;
  tool?: GraphTool;         // rings layout: the registry tool of a 'tool' node
  routine?: GraphRoutine;   // rings layout: the saved routine of a 'routine' node
}

export interface SimLink {
  id: string;
  s: SimNode;
  t: SimNode;
  relation: string;
  weight: number;
  structural: boolean;      // hub ↔ core or node ↔ its hub
}

export interface CategoryLabel { level: LevelId; name: string; x: number; y: number; count: number }

export const CORE_ID = 'core';
export const hubId = (level: LevelId) => `level:${level}`;

// Hubs placed like four lobes around the core (world units).
const HUB_POS: Record<LevelId, [number, number]> = {
  immediate: [-270, -150], persistent: [270, -150], semantic: [-270, 170], actions: [270, 170],
};

const APPEAR_MS = 520;
const DIE_MS = 750;
const REVIVE_AFTER_MS = 4000;

export class BrainSim {
  nodes: SimNode[] = [];
  links: SimLink[] = [];
  byId = new Map<string, SimNode>();
  byKey = new Map<string, SimNode>();
  categories: CategoryLabel[] = [];
  /** Keys whose node should appear only when a particle reaches it. */
  pendingAppear = new Map<string, number>();
  alpha = 1;
  private alias = new Map<string, string>();

  constructor() {
    this.addStructural(CORE_ID, 'core', null, 'AURORA', 0, 0, 30);
    for (const l of LEVELS) this.addStructural(hubId(l.id), 'level', l.id, l.short, HUB_POS[l.id][0], HUB_POS[l.id][1], 16);
  }

  private addStructural(id: string, type: GraphNode['type'], level: LevelId | null, label: string, x: number, y: number, r: number) {
    const n: SimNode = {
      id, type, key: null, num: null, label, category: '', kind: '', level, status: '', weight: 1, createdMs: null,
      group: type, x, y, vx: 0, vy: 0, r, fixed: true, ax: x, ay: y,
      bornAt: 0, hiddenUntil: 0, dyingAt: 0, dyingByEvent: false, inData: true, hidden: false, raw: null,
    };
    this.nodes.push(n);
    this.byId.set(id, n);
  }

  get core(): SimNode { return this.byId.get(CORE_ID)!; }
  hub(level: LevelId | null | undefined): SimNode { return this.byId.get(hubId(level ?? 'persistent'))!; }

  /** Resolve a node by internal id, data id or event key ("memory#12"). */
  resolve(ref: string): SimNode | undefined {
    return this.byId.get(ref) ?? this.byId.get(this.alias.get(ref) ?? '') ?? this.byKey.get(ref);
  }

  /** Data nodes (not core/hubs), alive or dying. */
  dataNodes(): SimNode[] { return this.nodes.filter(n => !n.fixed); }

  /** Merge fresh API data, keeping positions of known nodes. Returns the ids of new nodes. */
  sync(data: MemoryGraphData, now: number, reduced: boolean): string[] {
    const added: string[] = [];
    const seen = new Set<string>();
    this.alias.clear();

    for (const raw of data.nodes) {
      if (raw.type === 'core') { this.alias.set(raw.id, CORE_ID); continue; }
      if (raw.type === 'level') {
        const lvl = nodeLevel(raw);
        if (lvl) this.alias.set(raw.id, hubId(lvl));
        continue;
      }
      seen.add(raw.id);
      const level = nodeLevel(raw);
      const n0 = nodeNum(raw.id);
      const createdMs = raw.created_at ? Date.parse(raw.created_at) : NaN;
      let n = this.byId.get(raw.id);
      if (!n) {
        const hub = this.hub(level);
        const a = hash01(raw.id) * Math.PI * 2;
        const d = 24 + hash01(raw.id, 7) * 30;
        n = {
          id: raw.id, type: raw.type, key: null, num: n0, label: raw.label, category: '', kind: '', level, status: '',
          weight: 0.5, createdMs: null, group: '', x: hub.x + Math.cos(a) * d, y: hub.y + Math.sin(a) * d, vx: 0, vy: 0,
          r: 5, fixed: false, ax: hub.x, ay: hub.y, bornAt: reduced ? 0 : now, hiddenUntil: 0, dyingAt: 0, dyingByEvent: false,
          inData: true, hidden: false, raw,
        };
        this.nodes.push(n);
        this.byId.set(raw.id, n);
        added.push(raw.id);
      }
      n.raw = raw;
      n.inData = true;
      n.type = raw.type;
      n.num = n0;
      n.key = n0 !== null ? nodeKey(raw.type, n0) : null;
      n.label = raw.label || '(sans titre)';
      n.category = raw.category || (raw.type === 'memory' ? 'general' : '');
      n.kind = raw.kind ?? '';
      n.level = level;
      n.status = raw.status ?? '';
      n.weight = typeof raw.weight === 'number' && Number.isFinite(raw.weight) ? Math.max(0, Math.min(1, raw.weight)) : 0.5;
      n.createdMs = Number.isFinite(createdMs) ? createdMs : null;
      n.group = raw.type === 'memory' ? `cat:${n.category}` : raw.type;
      n.r = raw.type === 'memory' ? 4.5 + 4 * n.weight : raw.type === 'run' ? 5.5 : 5;
      if (n.dyingAt && (!n.dyingByEvent || now - n.dyingAt > REVIVE_AFTER_MS)) { n.dyingAt = 0; n.dyingByEvent = false; }
      if (n.key) {
        const pending = this.pendingAppear.get(n.key);
        if (pending !== undefined) { n.hiddenUntil = pending; n.bornAt = pending; this.pendingAppear.delete(n.key); }
      }
    }

    for (const n of this.nodes) {
      if (n.fixed || seen.has(n.id)) continue;
      n.inData = false;
      if (!n.dyingAt) { n.dyingAt = now; n.dyingByEvent = false; }
    }

    this.byKey.clear();
    for (const n of this.nodes) if (n.key && !n.fixed) this.byKey.set(n.key, n);

    this.rebuildLinks(data);
    this.layoutAnchors();
    if (added.length || this.alpha < 0.3) this.reheat(added.length > 20 ? 1 : 0.5);
    return added;
  }

  private rebuildLinks(data: MemoryGraphData) {
    const links: SimLink[] = [];
    const seen = new Set<string>();
    const push = (s: SimNode, t: SimNode, relation: string, weight: number, structural: boolean) => {
      if (s === t) return;
      const id = s.id < t.id ? `${s.id}|${t.id}|${relation}` : `${t.id}|${s.id}|${relation}`;
      const pair = s.id < t.id ? `${s.id}|${t.id}` : `${t.id}|${s.id}`;
      if (seen.has(id) || (structural && seen.has(pair))) return;
      seen.add(id); seen.add(pair);
      links.push({ id, s, t, relation, weight, structural });
    };
    for (const l of LEVELS) push(this.hub(l.id), this.core, 'level', 1, true);
    for (const raw of data.links) {
      const s = this.resolve(raw.source);
      const t = this.resolve(raw.target);
      if (!s || !t) continue;
      const structural = s.fixed || t.fixed;
      push(s, t, raw.relation || 'related', typeof raw.weight === 'number' ? raw.weight : 0.5, structural);
    }
    // Every data node hangs from its level hub (a membership line, not a causal link).
    for (const n of this.nodes) if (!n.fixed) push(n, this.hub(n.level), 'member', 0.3, true);
    this.links = links;
  }

  /** Category clusters fan out from each hub, away from the core (RingSim replaces it). */
  protected layoutAnchors() {
    this.categories = [];
    for (const l of LEVELS) {
      const hub = this.hub(l.id);
      const members = this.nodes.filter(n => !n.fixed && n.level === l.id && !n.hidden);
      const groups = new Map<string, SimNode[]>();
      for (const n of members) {
        const g = groups.get(n.group);
        if (g) g.push(n); else groups.set(n.group, [n]);
      }
      const ordered = [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
      const base = Math.atan2(hub.y, hub.x);
      const count = ordered.length;
      // At most a half-turn: the side facing the core stays free for the trunk and the hub label.
      const spread = count <= 1 ? 0 : Math.min(Math.PI, 0.55 * (count - 1));
      ordered.forEach(([group, list], i) => {
        const angle = count <= 1 ? base : base - spread / 2 + (spread * i) / (count - 1);
        const dist = (group === 'conversation' ? 70 : group === 'run' ? 88 : 98) + 7 * Math.sqrt(list.length);
        const ax = hub.x + Math.cos(angle) * dist;
        const ay = hub.y + Math.sin(angle) * dist;
        for (const n of list) { n.ax = ax; n.ay = ay; }
        if (group.startsWith('cat:')) {
          const ld = dist + 22 + Math.sqrt(list.length) * 7;
          this.categories.push({ level: l.id, name: group.slice(4), x: hub.x + Math.cos(angle) * ld, y: hub.y + Math.sin(angle) * ld, count: list.length });
        }
      });
    }
  }

  /** Replay time cursor: hide nodes created after `ms` (null shows everything). */
  setCutoff(ms: number | null, now: number, reduced: boolean) {
    let changed = false;
    for (const n of this.nodes) {
      if (n.fixed) continue;
      const hidden = ms !== null && n.createdMs !== null && n.createdMs > ms;
      if (hidden === n.hidden) continue;
      changed = true;
      n.hidden = hidden;
      if (!hidden) {
        // Re-emerge from the level hub.
        const hub = this.hub(n.level);
        const a = hash01(n.id, 3) * Math.PI * 2;
        n.x = hub.x + Math.cos(a) * 18; n.y = hub.y + Math.sin(a) * 18; n.vx = 0; n.vy = 0;
        n.bornAt = reduced ? 0 : Math.max(now, n.bornAt);
      }
    }
    if (changed) { this.layoutAnchors(); this.reheat(0.6); }
  }

  reheat(a = 0.5) { this.alpha = Math.max(this.alpha, a); }

  get active(): boolean { return this.alpha > 0.004; }

  /** One integration step. Returns false once the layout is settled (`_now`: used by RingSim). */
  tick(_now?: number): boolean {
    if (!this.active) return false;
    const alpha = this.alpha;
    const nodes = this.nodes.filter(n => !n.hidden);
    const len = nodes.length;

    for (const n of nodes) {
      if (n.fixed) continue;
      n.vx += (n.ax - n.x) * 0.045 * alpha;
      n.vy += (n.ay - n.y) * 0.045 * alpha;
    }

    // Pairwise repulsion with a cut-off radius, plus a hard collision term.
    const cutoff2 = 190 * 190;
    for (let i = 0; i < len; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < len; j++) {
        const b = nodes[j];
        if (a.fixed && b.fixed) continue;
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 > cutoff2) continue;
        if (d2 < 0.01) { dx = (hash01(a.id + b.id) - 0.5) * 0.5; dy = (hash01(b.id + a.id) - 0.5) * 0.5; d2 = dx * dx + dy * dy + 0.01; }
        const d = Math.sqrt(d2);
        const charge = (a.fixed || b.fixed ? 1500 : 520) * alpha / d2;
        let fx = dx / d * charge;
        let fy = dy / d * charge;
        // Keep a clear ring around the core and the hubs (their labels sit there).
        const minD = a.r + b.r + (a.type === 'core' || b.type === 'core' ? 46 : a.fixed || b.fixed ? 34 : 5);
        if (d < minD) {
          const push = (minD - d) / d * 0.5;
          fx += dx * push; fy += dy * push;
        }
        if (!a.fixed) { a.vx -= b.fixed ? fx * 2 : fx; a.vy -= b.fixed ? fy * 2 : fy; }
        if (!b.fixed) { b.vx += a.fixed ? fx * 2 : fx; b.vy += a.fixed ? fy * 2 : fy; }
      }
    }

    // Springs on real memory links (structural links are handled by the anchor force).
    for (const l of this.links) {
      if (l.structural || l.s.hidden || l.t.hidden) continue;
      const dx = l.t.x - l.s.x;
      const dy = l.t.y - l.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      // Links across levels pull gently so each lobe keeps its shape.
      const sameLevel = l.s.level === l.t.level;
      const rest = sameLevel ? 64 : 160;
      const k = (0.03 + 0.06 * l.weight) * (sameLevel ? 1 : 0.25) * alpha;
      const f = (d - rest) / d * k;
      if (!l.s.fixed) { l.s.vx += dx * f; l.s.vy += dy * f; }
      if (!l.t.fixed) { l.t.vx -= dx * f; l.t.vy -= dy * f; }
    }

    for (const n of nodes) {
      if (n.fixed) continue;
      n.vx *= 0.62; n.vy *= 0.62;
      const sp = Math.hypot(n.vx, n.vy);
      if (sp > 30) { n.vx *= 30 / sp; n.vy *= 30 / sp; }
      n.x += n.vx; n.y += n.vy;
    }
    this.alpha *= 0.975;
    return true;
  }

  /** Run the layout to rest synchronously (reduced motion, first paint). */
  settle(maxTicks = 400) {
    for (let i = 0; i < maxTicks && this.tick(); i++) { /* converge */ }
  }

  /** Drop nodes whose fade-out has finished. */
  prune(now: number, reduced: boolean) {
    const before = this.nodes.length;
    this.nodes = this.nodes.filter(n => {
      if (!n.dyingAt) return true;
      const finished = reduced || now - n.dyingAt > DIE_MS;
      if (!finished) return true;
      if (n.dyingByEvent && n.inData && now - n.dyingAt < REVIVE_AFTER_MS) return true; // invisible until the refresh drops it
      this.byId.delete(n.id);
      if (n.key && this.byKey.get(n.key) === n) this.byKey.delete(n.key);
      return false;
    });
    if (this.nodes.length !== before) {
      this.links = this.links.filter(l => this.byId.has(l.s.id) && this.byId.has(l.t.id));
      this.layoutAnchors();
    }
  }

  /** Visual state of a node at `now`: scale and opacity of the appear / fade animations. */
  presence(n: SimNode, now: number): { scale: number; alpha: number } | null {
    if (n.hidden) return null;
    if (n.hiddenUntil && now < n.hiddenUntil) return null;
    let scale = 1;
    let alpha = 1;
    if (n.bornAt && now >= n.bornAt) {
      const t = Math.min(1, (now - n.bornAt) / APPEAR_MS);
      if (t >= 1) n.bornAt = 0;
      else { scale = easeOutBack(t); alpha = Math.min(1, t * 1.6); }
    } else if (n.bornAt && now < n.bornAt) return null;
    if (n.dyingAt) {
      const t = Math.min(1, (now - n.dyingAt) / DIE_MS);
      if (t >= 1) return null;
      alpha *= 1 - t; scale *= 1 - 0.5 * t;
    }
    return { scale, alpha };
  }

  /** Bounding box of the visible layout (for fit-to-view). */
  bounds(): { minX: number; minY: number; maxX: number; maxY: number } {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of this.nodes) {
      if (n.hidden) continue;
      minX = Math.min(minX, n.x - n.r - 30); maxX = Math.max(maxX, n.x + n.r + 30);
      minY = Math.min(minY, n.y - n.r - 30); maxY = Math.max(maxY, n.y + n.r + 30);
    }
    for (const c of this.categories) {
      minX = Math.min(minX, c.x - 50); maxX = Math.max(maxX, c.x + 50);
      minY = Math.min(minY, c.y - 14); maxY = Math.max(maxY, c.y + 14);
    }
    return { minX, minY, maxX, maxY };
  }

  /** Nearest visible node under a world point. */
  hit(x: number, y: number, pad: number, now: number): SimNode | null {
    let best: SimNode | null = null;
    let bestD = Infinity;
    for (const n of this.nodes) {
      if (n.dyingAt || !this.presence(n, now)) continue;
      const d = Math.hypot(n.x - x, n.y - y);
      if (d <= n.r + pad && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }
}

export function easeOutBack(t: number): number {
  const c1 = 1.70158;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
}
