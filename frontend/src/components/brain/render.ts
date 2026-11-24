// Canvas 2D renderer for the brain view. Glows use pre-rendered sprites (no shadowBlur) so a
// few hundred nodes stay at 60 fps. Drawing is in screen space so strokes and text stay crisp.
import { CORE_COLOR, DANGER_COLOR, LEVELS, LEVEL_BY_ID, LINK_COLOR, ROUTINE_COLOR, TOOL_ABSENT_COLOR, TOOL_COLOR, TOOL_DISABLED_COLOR, hash01, runStatusColor, type LevelId } from './model';
import { ROUTINES_RING, type Effect } from './effects';
import { hubId, type BrainSim, type SimLink, type SimNode } from './sim';
import type { RingToolSector, RingView } from './rings';
import { INTENT_LABELS } from '../../lib/format';
import { fmtNum } from '../chat/text';

export interface Camera { k: number; x: number; y: number }

export interface MemoryMeta { pinned: boolean; verification: string }

export interface SceneState {
  sim: BrainSim;
  effects: Effect[];
  now: number;
  w: number;
  h: number;
  cam: Camera;
  selectedId: string | null;
  hoverId: string | null;
  highlight: Map<string, number> | null;
  statusOf: (n: SimNode) => string;
  metaOf: (n: SimNode) => MemoryMeta | null;
  counts: Record<LevelId, number>;
  reduced: boolean;
  /** Rings layout (RingSim); null or absent: the lobes, drawn exactly as before. */
  rings?: RingView | null;
  /** Vignette de l'accueil (08/11/2026) : les étiquettes, illisibles à cette taille, ne sont pas dessinées ; « AURORA » reste. */
  compact?: boolean;
}

// ---------------------------------------------------------------- sprites

// Glow sprites: small canvas elements (measured ~30× faster to draw than ImageBitmaps from an
// OffscreenCanvas). A hidden page can hibernate them, after which each drawImage costs tens
// of ms (measured), so they are rebuilt whenever the page becomes visible again.
const sprites = new Map<string, HTMLCanvasElement>();
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sprites.clear(); });
}

function glow(color: string): HTMLCanvasElement {
  let c = sprites.get(color);
  if (c) return c;
  c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d')!;
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, hexA(color, 1));
  grad.addColorStop(0.18, hexA(color, 0.55));
  grad.addColorStop(0.45, hexA(color, 0.16));
  grad.addColorStop(1, hexA(color, 0));
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  sprites.set(color, c);
  return c;
}

function hexA(hex: string, a: number): string {
  const h = hex.replace('#', '');
  const v = h.length === 3 ? h.split('').map(x => x + x).join('') : h.slice(0, 6);
  const n = parseInt(v, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// Brain glyph (24×24 box, left hemisphere; the right one is mirrored).
let glyph: { outline: Path2D; gyri: Path2D } | null = null;
function brainGlyph() {
  if (!glyph) {
    glyph = {
      outline: new Path2D('M11.6 4.6C10.3 3.3 7.9 3.5 7.1 5.4C5.1 5.3 3.7 7.2 4.4 9C2.8 9.9 2.6 12.4 3.9 13.5C3.2 15.5 4.5 17.5 6.5 17.6C7 19.4 9.3 20.3 10.8 19.2C11.2 19.6 11.6 19.8 11.6 19.8'),
      gyri: new Path2D('M7.1 5.4C7.6 6.8 8.9 7.6 10.3 7.3M4.4 9C5.6 9.9 7.2 9.9 8.3 9.1M3.9 13.5C5.2 13.1 6.6 13.6 7.3 14.7M6.5 17.6C7.1 16.4 8.6 15.9 9.9 16.3M8.4 11.7C9.5 11.1 10.6 11.4 11.4 12.1'),
    };
  }
  return glyph;
}

// ---------------------------------------------------------------- helpers

export function nodeColor(n: SimNode, status: string): string {
  if (n.type === 'run') return runStatusColor(status);
  if (n.type === 'conversation') return '#8fe3ff';
  if (n.type === 'core') return CORE_COLOR;
  return LEVEL_BY_ID[n.level ?? 'persistent'].color;
}

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
const ease = (t: number) => 1 - Math.pow(1 - t, 3);

function ctrl(ax: number, ay: number, bx: number, by: number, bend: number) {
  return { x: (ax + bx) / 2 - (by - ay) * bend, y: (ay + by) / 2 + (bx - ax) * bend };
}

function curvePoint(ax: number, ay: number, bx: number, by: number, bend: number, t: number) {
  const { x: cx, y: cy } = ctrl(ax, ay, bx, by, bend);
  const u = 1 - t;
  return { x: u * u * ax + 2 * u * t * cx + t * t * bx, y: u * u * ay + 2 * u * t * cy + t * t * by };
}

function truncate(s: string, max: number) { return s.length > max ? `${s.slice(0, max - 1)}…` : s; }

type Rect = [number, number, number, number];

/** Draw a centred label unless it would overlap one already placed (forced labels always draw). */
function label(ctx: CanvasRenderingContext2D, placed: Rect[], text: string, x: number, y: number, color: string, size = 11, weight = 500, force = true): void {
  ctx.font = `${weight} ${size}px "Segoe UI Variable", "Segoe UI", Inter, sans-serif`;
  const half = ctx.measureText(text).width / 2 + 3;
  const r: Rect = [x - half, y - size * 0.7, x + half, y + size * 0.7];
  if (!force && placed.some(p => r[0] < p[2] && r[2] > p[0] && r[1] < p[3] && r[3] > p[1])) return;
  placed.push(r);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineWidth = 3;
  ctx.strokeStyle = 'rgba(2, 8, 20, 0.85)';
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

// ---------------------------------------------------------------- scene

export function drawScene(ctx: CanvasRenderingContext2D, s: SceneState): void {
  const { sim, now, w, h, cam, reduced } = s;
  const k = cam.k;
  const ox = w / 2 + cam.x; const oy = h / 2 + cam.y;
  const SX = (x: number) => ox + x * k;
  const SY = (y: number) => oy + y * k;
  const kk = Math.sqrt(k);
  const hl = s.highlight && s.highlight.size ? s.highlight : null;
  const rings = s.rings ?? null;
  /** Tool map of the page Tâches (rings.ts, toolMapLayout): the tools alone, one sector per large family. Null on the
   *  memory rings and the lobes, which are drawn exactly as before. */
  const toolMap = rings?.geo.toolSectors ?? null;

  ctx.clearRect(0, 0, w, h);
  const placed: Rect[] = [];

  // Per-frame effect intensities by node.
  const flash = new Map<string, { v: number; color: string }>();
  const halo = new Map<string, { v: number; color: string }>();
  const pulse = new Map<string, { v: number; color: string }>();
  for (const e of s.effects) {
    if (now < e.start) continue;
    const t = clamp01((now - e.start) / e.dur);
    if (e.type === 'flash' || e.type === 'halo' || e.type === 'pulse') {
      const n = sim.resolve(e.node);
      if (!n) continue;
      const v = reduced ? 1 : e.type === 'halo' ? Math.sin(Math.PI * t) : 1 - t;
      const map = e.type === 'flash' ? flash : e.type === 'halo' ? halo : pulse;
      const prev = map.get(n.id);
      if (!prev || prev.v < v) map.set(n.id, { v, color: e.color });
    }
  }

  const breath = reduced ? 0.5 : 0.5 + 0.5 * Math.sin(now / 900);
  const core = sim.core;

  // Lobe glows behind each hub (rings: sector bands and the two outer rings), and the core aura.
  ctx.globalCompositeOperation = 'lighter';
  if (rings && toolMap) drawToolMapBackdrop(ctx, rings, toolMap, pulse, ox, oy, k, kk);
  else if (rings) drawRingBackdrop(ctx, s, rings, pulse, ox, oy, k, kk);
  else for (const l of LEVELS) {
    const hub = sim.hub(l.id);
    const p = pulse.get(hub.id)?.v ?? 0;
    const size = (470 + 60 * p) * k;
    ctx.globalAlpha = 0.11 + 0.14 * p;
    ctx.drawImage(glow(l.color), SX(hub.x) - size / 2, SY(hub.y) - size / 2, size, size);
  }
  {
    const p = pulse.get(core.id)?.v ?? 0;
    const size = (330 + 40 * breath + 90 * p) * k;
    ctx.globalAlpha = 0.2 + 0.08 * breath + 0.25 * p;
    ctx.drawImage(glow(CORE_COLOR), SX(core.x) - size / 2, SY(core.y) - size / 2, size, size);
  }
  ctx.globalAlpha = 1;

  // Links.
  const present = new Map<string, { scale: number; alpha: number }>();
  for (const n of sim.nodes) {
    const p = sim.presence(n, now);
    if (p) present.set(n.id, p);
  }
  if (rings) for (const n of rings.extras) {
    const p = sim.presence(n, now);
    if (p) present.set(n.id, p);
  }
  const sel = s.selectedId;
  const memberBatches = new Map<string, SimLink[]>();
  const dataLinks: SimLink[] = [];
  for (const l of sim.links) {
    if (l.relation === 'level' || !present.has(l.s.id) || !present.has(l.t.id)) continue;
    if (!l.structural) { dataLinks.push(l); continue; }
    if (rings) continue;   // rings: membership is the sector itself, no line to the hub
    const end = l.s.fixed ? l.s : l.t;
    const color = end.type === 'core' ? CORE_COLOR : LEVEL_BY_ID[end.level ?? 'persistent'].color;
    const batch = memberBatches.get(color);
    if (batch) batch.push(l); else memberBatches.set(color, [l]);
  }

  // Hub ↔ core trunks (none on the tool map: the memory levels are not drawn there).
  for (const l of toolMap ? [] : LEVELS) {
    const hub = sim.hub(l.id);
    const p = Math.max(pulse.get(hub.id)?.v ?? 0, (pulse.get(core.id)?.v ?? 0) * 0.6);
    const grad = ctx.createLinearGradient(SX(hub.x), SY(hub.y), SX(core.x), SY(core.y));
    grad.addColorStop(0, hexA(l.color, 0.55 + 0.4 * p));
    grad.addColorStop(1, hexA(CORE_COLOR, 0.35 + 0.4 * p));
    ctx.strokeStyle = grad;
    ctx.lineWidth = (2 + 2.5 * p) * kk;
    ctx.beginPath();
    const c = ctrl(hub.x, hub.y, core.x, core.y, 0.1);
    ctx.moveTo(SX(hub.x), SY(hub.y));
    ctx.quadraticCurveTo(SX(c.x), SY(c.y), SX(core.x), SY(core.y));
    ctx.stroke();
  }

  // Membership lines (node → hub), batched by colour.
  ctx.lineWidth = Math.max(0.6, 0.9 * kk);
  for (const [color, list] of memberBatches) {
    ctx.strokeStyle = hexA(color, hl ? 0.07 : 0.16);
    ctx.beginPath();
    for (const l of list) {
      const a = l.s.fixed ? l.t : l.s;
      const b = l.s.fixed ? l.s : l.t;
      const c = ctrl(a.x, a.y, b.x, b.y, (hash01(a.id) - 0.5) * 0.3);
      ctx.moveTo(SX(a.x), SY(a.y));
      ctx.quadraticCurveTo(SX(c.x), SY(c.y), SX(b.x), SY(b.y));
    }
    ctx.stroke();
  }

  // Real memory links.
  for (const l of dataLinks) {
    const touchesSel = (sel !== null && (l.s.id === sel || l.t.id === sel))
      || (rings !== null && s.hoverId !== null && (l.s.id === s.hoverId || l.t.id === s.hoverId));
    const matched = hl && (hl.has(l.s.id) || hl.has(l.t.id));
    const alpha = Math.min(present.get(l.s.id)!.alpha, present.get(l.t.id)!.alpha) * (touchesSel ? 0.95 : hl && !matched ? 0.12 : (0.28 + 0.4 * l.weight) * (rings ? 0.5 : 1));
    const color = l.relation === 'contradicts' ? DANGER_COLOR : LINK_COLOR;
    ctx.strokeStyle = hexA(color, alpha);
    ctx.lineWidth = (0.9 + 1.5 * l.weight + (touchesSel ? 1.2 : 0)) * kk;
    if (l.relation === 'contradicts') ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(SX(l.s.x), SY(l.s.y));
    if (rings) {
      // Chords bend towards the core, more as the two ends are far apart.
      const pull = 1 - 0.5 * Math.min(1, Math.hypot(l.t.x - l.s.x, l.t.y - l.s.y) / 300);
      ctx.quadraticCurveTo(SX(((l.s.x + l.t.x) / 2) * pull), SY(((l.s.y + l.t.y) / 2) * pull), SX(l.t.x), SY(l.t.y));
    } else ctx.lineTo(SX(l.t.x), SY(l.t.y));
    ctx.stroke();
    if (l.relation === 'contradicts') ctx.setLineDash([]);
  }

  // Links being drawn (memory.linked).
  for (const e of s.effects) {
    if (e.type !== 'link' || now < e.start) continue;
    const a = sim.resolve(e.a); const b = sim.resolve(e.b);
    if (!a || !b) continue;
    const t = reduced ? 1 : ease(clamp01((now - e.start) / (e.dur * 0.7)));
    const fade = reduced ? 1 : 1 - clamp01((now - e.start - e.dur * 0.7) / (e.dur * 0.3));
    const x = a.x + (b.x - a.x) * t; const y = a.y + (b.y - a.y) * t;
    ctx.strokeStyle = hexA(e.color, 0.95 * fade + 0.05);
    ctx.lineWidth = 2.6 * kk;
    ctx.beginPath(); ctx.moveTo(SX(a.x), SY(a.y)); ctx.lineTo(SX(x), SY(y)); ctx.stroke();
    if (!reduced) {
      const size = 34 * kk;
      ctx.globalAlpha = fade;
      ctx.drawImage(glow('#ffffff'), SX(x) - size / 2, SY(y) - size / 2, size, size);
      ctx.globalAlpha = 1;
    }
  }

  // Data nodes.
  if (rings) drawRingNodes(ctx, s, rings, present, flash, halo, pulse, SX, SY, k, kk);
  else for (const n of sim.nodes) {
    if (n.fixed) continue;
    const p = present.get(n.id);
    if (!p) continue;
    const status = s.statusOf(n);
    const color = nodeColor(n, status);
    const matched = hl ? hl.get(n.id) : undefined;
    const dim = hl && matched === undefined ? 0.22 : 1;
    const f = flash.get(n.id); const hv = halo.get(n.id);
    const r = n.r * k * p.scale * (1 + (f ? 0.5 * f.v : 0));
    const x = SX(n.x); const y = SY(n.y);
    if (x < -40 || y < -40 || x > w + 40 || y > h + 40) continue;
    const a = p.alpha * dim;
    ctx.globalAlpha = a * (0.55 + (f ? 0.45 * f.v : 0) + (matched !== undefined ? 0.3 : 0));
    const gs = r * (5 + (f ? 4 * f.v : 0));
    ctx.drawImage(glow(f && f.v > 0.3 ? f.color : color), x - gs / 2, y - gs / 2, gs, gs);
    if (hv) {
      const hs = r * (7 + 3 * hv.v);
      ctx.globalAlpha = a * hv.v * 0.9;
      ctx.drawImage(glow(hv.color), x - hs / 2, y - hs / 2, hs, hs);
    }
    ctx.globalAlpha = a;
    ctx.fillStyle = hexA(color, 0.9);
    ctx.beginPath(); ctx.arc(x, y, Math.max(1.2, r), 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,0.85)';
    ctx.beginPath(); ctx.arc(x - r * 0.25, y - r * 0.25, Math.max(0.6, r * 0.32), 0, Math.PI * 2); ctx.fill();
    const meta = n.type === 'memory' ? s.metaOf(n) : null;
    if (meta && (meta.verification === 'user_confirmed' || meta.verification === 'verified' || meta.verification === 'contradicted')) {
      ctx.strokeStyle = meta.verification === 'contradicted' ? DANGER_COLOR : '#2ee6a8';
      ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.arc(x, y, r + 2.5, 0, Math.PI * 2); ctx.stroke();
    }
    if (meta?.pinned) {
      ctx.fillStyle = '#ffd36b';
      ctx.beginPath(); ctx.arc(x + r * 0.9, y - r * 0.9, Math.max(1.6, 2.2 * kk), 0, Math.PI * 2); ctx.fill();
    }
    if (matched !== undefined) {
      ctx.strokeStyle = hexA('#ffffff', 0.5 + 0.5 * matched);
      ctx.lineWidth = 1.5 + matched;
      ctx.beginPath(); ctx.arc(x, y, r + 5, 0, Math.PI * 2); ctx.stroke();
    }
    if (n.id === sel || n.id === s.hoverId) {
      ctx.strokeStyle = n.id === sel ? '#ffffff' : hexA('#ffffff', 0.6);
      ctx.lineWidth = n.id === sel ? 2 : 1.2;
      ctx.beginPath(); ctx.arc(x, y, r + (n.id === sel ? 7 : 5), 0, Math.PI * 2); ctx.stroke();
      if (n.id === sel) {
        ctx.strokeStyle = hexA(color, 0.6);
        ctx.beginPath(); ctx.arc(x, y, r + 11, 0, Math.PI * 2); ctx.stroke();
      }
    }
  }
  ctx.globalAlpha = 1;

  // Hubs.
  if (toolMap) { /* tool map: no memory level, so no hub */ }
  else if (rings) drawRingHubs(ctx, sim, pulse, SX, SY, k, reduced);
  else for (const l of LEVELS) {
    const hub = sim.hub(l.id);
    const x = SX(hub.x); const y = SY(hub.y);
    const p = pulse.get(hub.id);
    const pv = p?.v ?? 0;
    const r = hub.r * k * (1 + 0.25 * pv);
    ctx.globalAlpha = 0.8 + 0.2 * pv;
    const gs = r * (6 + 4 * pv);
    ctx.drawImage(glow(p && pv > 0.2 ? p.color : l.color), x - gs / 2, y - gs / 2, gs, gs);
    ctx.globalAlpha = 1;
    ctx.fillStyle = 'rgba(4, 14, 32, 0.92)';
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = l.color; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = hexA(l.color, 0.45); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(x, y, r + 5 * kk, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = l.color;
    ctx.beginPath(); ctx.arc(x, y, r * 0.38, 0, Math.PI * 2); ctx.fill();
    if (pv > 0 && !reduced) {
      ctx.strokeStyle = hexA(p!.color, pv * 0.9); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, r + (1 - pv) * 30 * k, 0, Math.PI * 2); ctx.stroke();
    }
    // Labels on the side facing the core, where no cluster grows.
    const dir = hub.y > core.y ? -1 : 1;
    label(ctx, placed, l.short.toUpperCase(), x, y + dir * (r + 16), l.color, 11.5, 650);
    label(ctx, placed, `${s.counts[l.id]} élément${s.counts[l.id] > 1 ? 's' : ''}`, x, y + dir * (r + 30), '#9fb9da', 10.5, 500);
  }

  // Core: luminous nucleus with the brain glyph.
  {
    const x = SX(core.x); const y = SY(core.y);
    const pv = pulse.get(core.id)?.v ?? 0;
    const r = core.r * k * (1 + 0.04 * breath + 0.12 * pv);
    const grad = ctx.createRadialGradient(x, y, r * 0.1, x, y, r);
    grad.addColorStop(0, 'rgba(210, 248, 255, 0.95)');
    grad.addColorStop(0.45, hexA('#22c8ff', 0.55 + 0.2 * pv));
    grad.addColorStop(1, hexA('#2f7bff', 0.12));
    ctx.fillStyle = grad;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = hexA('#9fe9ff', 0.8); ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
    // Orbiting dashed ring (static with reduced motion).
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(reduced ? 0 : now / 6000);
    ctx.setLineDash([4 * kk, 6 * kk]);
    ctx.strokeStyle = hexA('#22c8ff', 0.55); ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(0, 0, r * 1.38, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([]);
    ctx.rotate(-0.5);
    ctx.strokeStyle = hexA('#9fe9ff', 0.35);
    ctx.beginPath(); ctx.ellipse(0, 0, r * 1.75, r * 0.6, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
    // Glyph.
    const g = brainGlyph();
    const sc = (r * 1.25) / 24;
    ctx.save();
    ctx.translate(x - 12 * sc, y - 12 * sc);
    ctx.scale(sc, sc);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    for (const side of toolMap ? [] : [1, -1]) {   // the brain is the memory's sign: the tool map keeps the bare nucleus
      ctx.save();
      if (side < 0) { ctx.translate(24, 0); ctx.scale(-1, 1); }
      ctx.strokeStyle = 'rgba(4, 30, 60, 0.9)';
      ctx.lineWidth = 1.9;
      ctx.stroke(g.outline); ctx.stroke(g.gyri);
      ctx.restore();
    }
    ctx.restore();
    label(ctx, placed, 'AURORA', x, y + r * 1.38 + 16, '#bff3ff', 12, 700);
  }

  // Particles (memory flowing between core, hubs and nodes).
  if (!reduced) {
    for (const e of s.effects) {
      if (e.type !== 'particle' || now < e.start) continue;
      const t = clamp01((now - e.start) / e.dur);
      const a = sim.resolve(e.from); const b = sim.resolve(e.to);
      const ax = a?.x ?? e.fx; const ay = a?.y ?? e.fy; const bx = b?.x ?? e.tx; const by = b?.y ?? e.ty;
      for (let i = 6; i >= 0; i--) {
        const tt = ease(Math.max(0, t - i * 0.035));
        const pt = curvePoint(ax, ay, bx, by, e.bend, tt);
        const size = (i === 0 ? 30 : 16 - i * 1.6) * kk;
        ctx.globalAlpha = i === 0 ? 1 : 0.5 - i * 0.06;
        ctx.drawImage(glow(i === 0 ? '#ffffff' : e.color), SX(pt.x) - size / 2, SY(pt.y) - size / 2, size, size);
      }
      ctx.globalAlpha = 1;
    }
  }

  // Ripples (a node appears, a run ends, a memory is removed).
  for (const e of s.effects) {
    if (e.type !== 'ripple' || now < e.start) continue;
    const t = clamp01((now - e.start) / e.dur);
    const n = sim.resolve(e.at);
    const x = SX(n?.x ?? e.px); const y = SY(n?.y ?? e.py);
    ctx.strokeStyle = hexA(e.color, reduced ? 0.9 : (1 - t) * 0.9);
    ctx.lineWidth = reduced ? 2 : 2.4 * (1 - t) + 0.6;
    ctx.beginPath(); ctx.arc(x, y, (reduced ? 0.5 : 0.15 + 0.85 * ease(t)) * e.radius * k + 4, 0, Math.PI * 2); ctx.stroke();
    if (!reduced) {
      ctx.strokeStyle = hexA(e.color, (1 - t) * 0.4);
      ctx.beginPath(); ctx.arc(x, y, ease(t) * e.radius * 0.55 * k + 2, 0, Math.PI * 2); ctx.stroke();
    }
  }
  ctx.globalCompositeOperation = 'source-over';
  if (s.compact) return;

  // Labels: relevant nodes first (always shown), then categories, then other titles when
  // zoomed in — a label that would overlap one already placed is skipped.
  const showAll = k > (toolMap ? 1.3 : rings ? 2.2 : 1.35);
  const focused: SimNode[] = [];
  const matches: SimNode[] = [];
  const others: SimNode[] = [];
  for (const n of rings ? [...sim.nodes, ...rings.extras] : sim.nodes) {
    if (n.fixed) continue;
    const p = present.get(n.id);
    if (!p || p.alpha < 0.5) continue;
    if (n.id === sel || n.id === s.hoverId) focused.push(n);
    else if (hl?.has(n.id)) matches.push(n);
    else if (showAll && !hl) others.push(n);
  }
  matches.sort((a, b) => (hl!.get(b.id) ?? 0) - (hl!.get(a.id) ?? 0));
  const titleAt = (n: SimNode, strong: boolean, force: boolean) => {
    const x = SX(n.x); const y = SY(n.y) - n.r * k - 10;
    if (x < 0 || y < 0 || x > w || y > h) return;
    label(ctx, placed, truncate(n.label, strong ? 40 : 24), x, y, strong ? '#ffffff' : '#c8dcf5', strong ? 12 : 10.5, strong ? 600 : 500, force);
  };
  // Tool map: the family names are what is read there, so they are placed first and a tool's title gives way to them
  // (the hover card and the tool's own card say its name anyway).
  if (rings && toolMap) drawToolMapLabels(ctx, placed, rings, toolMap, SX, SY, k);
  for (const n of focused) titleAt(n, true, !toolMap);
  for (const n of matches) titleAt(n, true, false);  // best search scores first
  if (rings && !toolMap) drawRingLabels(ctx, placed, rings, SX, SY, k);
  if (k > 0.55) {
    for (const c of sim.categories) {
      label(ctx, placed, truncate(c.name.toUpperCase(), 22), SX(c.x), SY(c.y), hexA(LEVEL_BY_ID[c.level].color, 0.75), 10, 600, false);
    }
  }
  for (const n of others) titleAt(n, false, false);
}

// ---------------------------------------------------------------- rings layout (29/10/2026)
// Same look as the lobes: glow sprites, 'lighter' compositing, luminous strokes on navy.

type Fx = Map<string, { v: number; color: string }>;
type Presence = Map<string, { scale: number; alpha: number }>;
const TAU = Math.PI * 2;
const FONT = '"Segoe UI Variable", "Segoe UI", Inter, sans-serif';

/** Pointy-top hexagon path (tools). */
function hexPath(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    const a = -Math.PI / 2 + (i * Math.PI) / 3;
    if (i) ctx.lineTo(x + Math.cos(a) * r, y + Math.sin(a) * r);
    else ctx.moveTo(x + Math.cos(a) * r, y + Math.sin(a) * r);
  }
  ctx.closePath();
}

/** A full ring: wide soft stroke under a thin bright one, dashes turning with the ring, a wave on a pulse. */
function ringCircle(ctx: CanvasRenderingContext2D, ox: number, oy: number, R: number, color: string, pv: number, rot: number, kk: number, reduced: boolean) {
  ctx.strokeStyle = hexA(color, 0.07 + 0.22 * pv);
  ctx.lineWidth = (8 + 8 * pv) * kk;
  ctx.beginPath(); ctx.arc(ox, oy, R, 0, TAU); ctx.stroke();
  ctx.strokeStyle = hexA(color, 0.4 + 0.45 * pv);
  ctx.lineWidth = (1.1 + 1.4 * pv) * kk;
  ctx.stroke();
  ctx.setLineDash([2 * kk, 12 * kk]);
  ctx.strokeStyle = hexA(color, 0.55);
  ctx.lineWidth = 2.2 * kk;
  ctx.beginPath(); ctx.arc(ox, oy, R, rot, rot + TAU); ctx.stroke();
  ctx.setLineDash([]);
  if (pv > 0 && !reduced) {
    ctx.strokeStyle = hexA(color, pv * 0.8);
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(ox, oy, R + (1 - pv) * 26 * kk, 0, TAU); ctx.stroke();
  }
}

/** Memory disk sectors (one luminous band per level) and the routines / tools rings. */
function drawRingBackdrop(ctx: CanvasRenderingContext2D, s: SceneState, rv: RingView, pulse: Fx, ox: number, oy: number, k: number, kk: number) {
  const { geo, rot } = rv;
  const r0 = geo.r0 * k;
  const r1 = geo.r1 * k;
  for (const sec of geo.sectors) {
    const color = LEVEL_BY_ID[sec.level].color;
    const pv = pulse.get(hubId(sec.level))?.v ?? 0;
    const a0 = sec.a0 + rot.memory;
    const a1 = sec.a1 + rot.memory;
    const mid = (a0 + a1) / 2;
    const rm = (r0 + r1) / 2;
    const gsz = (r1 - r0) * 2.6 + 80 * k;
    ctx.globalAlpha = 0.09 + 0.16 * pv;
    ctx.drawImage(glow(color), ox + Math.cos(mid) * rm - gsz / 2, oy + Math.sin(mid) * rm - gsz / 2, gsz, gsz);
    ctx.globalAlpha = 1;
    ctx.fillStyle = hexA(color, 0.035 + 0.08 * pv);
    ctx.beginPath(); ctx.arc(ox, oy, r1, a0, a1); ctx.arc(ox, oy, r0, a1, a0, true); ctx.closePath(); ctx.fill();
    // Inner rim: the level's luminous arc, where its hub sits.
    ctx.strokeStyle = hexA(color, 0.2 + 0.5 * pv);
    ctx.lineWidth = (5 + 5 * pv) * kk;
    ctx.beginPath(); ctx.arc(ox, oy, (geo.r0 - 10) * k, a0, a1); ctx.stroke();
    ctx.strokeStyle = hexA(color, 0.75 + 0.25 * pv);
    ctx.lineWidth = (1.4 + 1.6 * pv) * kk;
    ctx.stroke();
    ctx.strokeStyle = hexA(color, 0.14);
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(ox, oy, r1, a0, a1); ctx.stroke();
  }
  // Routines ring: lit by route.decided (routine.run). Tools ring: lit by its tools' pulses.
  let rp = 0;
  for (const e of s.effects) {
    if (e.type !== 'pulse' || e.node !== ROUTINES_RING || s.now < e.start) continue;
    rp = Math.max(rp, s.reduced ? 1 : 1 - clamp01((s.now - e.start) / e.dur));
  }
  let tp = 0;
  for (const n of rv.extras) if (n.type === 'tool') tp = Math.max(tp, (pulse.get(n.id)?.v ?? 0) * 0.5);
  ringCircle(ctx, ox, oy, geo.routineR * k, ROUTINE_COLOR, rp, rot.routines, kk, s.reduced);
  ringCircle(ctx, ox, oy, geo.toolR * k, TOOL_COLOR, tp, rot.tools, kk, s.reduced);
}

/**
 * Points of the disk, routines and tools. Up to ~1 300 points: steady dots are batched in one
 * path per colour (one fill instead of hundreds); glows stay one sprite each, softer than the
 * lobes because neighbours add up.
 */
function drawRingNodes(
  ctx: CanvasRenderingContext2D, s: SceneState, rv: RingView, present: Presence, flash: Fx, halo: Fx, pulse: Fx,
  SX: (x: number) => number, SY: (y: number) => number, k: number, kk: number,
) {
  const { sim, w, h } = s;
  const hl = s.highlight && s.highlight.size ? s.highlight : null;
  const sel = s.selectedId;
  const fills = new Map<string, Path2D>();
  const shine = new Path2D();
  const confirmed = new Path2D();
  const contradicted = new Path2D();
  const pins = new Path2D();
  const marks: { x: number; y: number; r: number; id: string; color: string; matched: number | undefined }[] = [];
  const off = (x: number, y: number) => x < -30 || y < -30 || x > w + 30 || y > h + 30;

  for (const n of sim.nodes) {
    if (n.fixed) continue;
    const p = present.get(n.id);
    if (!p) continue;
    const x = SX(n.x); const y = SY(n.y);
    if (off(x, y)) continue;
    // Rings: a finished task takes its sector's colour (green is the semantic level's); errors and runs in
    // progress keep their status colour, so they still stand out.
    const status = s.statusOf(n);
    const color = n.type === 'run' && status === 'done' ? LEVEL_BY_ID.actions.color : nodeColor(n, status);
    const matched = hl ? hl.get(n.id) : undefined;
    const a = p.alpha * (hl && matched === undefined ? 0.22 : 1);
    const f = flash.get(n.id); const hv = halo.get(n.id);
    const r = Math.max(1.2, n.r * k * p.scale * (1 + (f ? 0.6 * f.v : 0)));
    ctx.globalAlpha = a * (0.3 + (f ? 0.6 * f.v : 0) + (matched !== undefined ? 0.35 : 0));
    const gs = r * (3.6 + (f ? 5 * f.v : 0));
    ctx.drawImage(glow(f && f.v > 0.3 ? f.color : color), x - gs / 2, y - gs / 2, gs, gs);
    if (hv) {
      const hs = r * (7 + 3 * hv.v);
      ctx.globalAlpha = a * hv.v * 0.9;
      ctx.drawImage(glow(hv.color), x - hs / 2, y - hs / 2, hs, hs);
    }
    if (a >= 0.999) {
      let fp = fills.get(color);
      if (!fp) { fp = new Path2D(); fills.set(color, fp); }
      fp.moveTo(x + r, y); fp.arc(x, y, r, 0, TAU);
      if (r > 2) { const sr = r * 0.32; shine.moveTo(x - r * 0.25 + sr, y - r * 0.25); shine.arc(x - r * 0.25, y - r * 0.25, sr, 0, TAU); }
    } else {
      ctx.globalAlpha = a;
      ctx.fillStyle = hexA(color, 0.9);
      ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
    }
    const meta = n.type === 'memory' && r > 1.8 ? s.metaOf(n) : null;
    if (meta) {
      const path = meta.verification === 'contradicted' ? contradicted
        : meta.verification === 'user_confirmed' || meta.verification === 'verified' ? confirmed : null;
      if (path) { path.moveTo(x + r + 2, y); path.arc(x, y, r + 2, 0, TAU); }
      if (meta.pinned) { const pr = Math.max(1.3, 1.8 * kk); pins.moveTo(x + r * 0.9 + pr, y - r * 0.9); pins.arc(x + r * 0.9, y - r * 0.9, pr, 0, TAU); }
    }
    if (matched !== undefined || n.id === sel || n.id === s.hoverId) marks.push({ x, y, r, id: n.id, color, matched });
  }
  ctx.globalAlpha = 1;
  for (const [color, path] of fills) { ctx.fillStyle = hexA(color, 0.9); ctx.fill(path); }
  ctx.fillStyle = 'rgba(255,255,255,0.8)'; ctx.fill(shine);
  ctx.lineWidth = 1;
  ctx.strokeStyle = '#2ee6a8'; ctx.stroke(confirmed);
  ctx.strokeStyle = DANGER_COLOR; ctx.stroke(contradicted);
  ctx.fillStyle = '#ffd36b'; ctx.fill(pins);

  // Routines (orange) and tools (blue hexagons): brighter and bigger with their real use.
  for (const n of rv.extras) {
    const p = present.get(n.id);
    if (!p) continue;
    const x = SX(n.x); const y = SY(n.y);
    if (off(x, y)) continue;
    const isTool = n.type === 'tool';
    // Tool map (page Tâches) only: the tool's real state. Undefined on the memory rings, drawn exactly as before.
    const state = isTool ? n.tool?.state : undefined;
    const down = state === 'disabled' || state === 'absent';
    const usage = isTool ? Math.min(1, Math.log10(1 + (n.tool?.uses ?? 0)) / 3) : Math.min(1, Math.log10(1 + (n.routine?.runs ?? 0)) / 2);
    const pu = pulse.get(n.id); const pv = pu?.v ?? 0;
    const color = pu && pv > 0.2 ? pu.color : !isTool ? ROUTINE_COLOR : state === 'disabled' ? TOOL_DISABLED_COLOR : state === 'absent' ? TOOL_ABSENT_COLOR : TOOL_COLOR;
    // On the map the state sets the look (use only sets the size); on the memory rings a tool never used is paler.
    const a = p.alpha * (hl ? 0.35 : 1) * (state ? (down ? 0.85 : 1) : usage > 0 ? 1 : 0.6);
    const r = Math.max(2, n.r * k * p.scale * (1 + 0.35 * pv));
    ctx.globalAlpha = a * (0.5 + 0.5 * pv) * (down ? 0.3 : 1);
    const gs = r * (4.2 + 4 * pv);
    ctx.drawImage(glow(color), x - gs / 2, y - gs / 2, gs, gs);
    ctx.globalAlpha = a;
    if (isTool) hexPath(ctx, x, y, r);
    else { ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); }
    ctx.fillStyle = hexA(color, down ? 0.06 + 0.3 * pv : 0.18 + 0.3 * usage + 0.3 * pv);
    ctx.fill();
    ctx.strokeStyle = hexA(color, 0.95);
    ctx.lineWidth = Math.max(1, 1.3 * kk);
    if (down) ctx.setLineDash([3 * kk, 2.5 * kk]);   // dashed outline: not available now
    ctx.stroke();
    if (down) {
      // A bar (désactivé) or a cross (indisponible) instead of the bright dot: the state never rests on colour alone.
      ctx.setLineDash([]);
      toolSign(ctx, state, x, y, r, kk);
    } else {
      ctx.fillStyle = 'rgba(255,255,255,0.75)';
      ctx.beginPath(); ctx.arc(x, y, Math.max(0.8, r * 0.24), 0, TAU); ctx.fill();
    }
    if (pv > 0 && !s.reduced) {
      ctx.strokeStyle = hexA(color, pv * 0.9);
      ctx.lineWidth = 1.6;
      if (isTool) hexPath(ctx, x, y, r + (1 - pv) * 18 * kk);
      else { ctx.beginPath(); ctx.arc(x, y, r + (1 - pv) * 18 * kk, 0, TAU); }
      ctx.stroke();
    }
    // The tool whose card is open keeps its ring (tool map: a click selects a tool; never on the memory rings).
    if (n.id === s.hoverId || (state !== undefined && n.id === sel)) marks.push({ x, y, r, id: n.id, color, matched: undefined });
  }
  ctx.globalAlpha = 1;

  // Search matches, selection and hover rings on top.
  for (const m of marks) {
    if (m.matched !== undefined) {
      ctx.strokeStyle = hexA('#ffffff', 0.5 + 0.5 * m.matched);
      ctx.lineWidth = 1.2 + m.matched;
      ctx.beginPath(); ctx.arc(m.x, m.y, m.r + 4, 0, TAU); ctx.stroke();
    }
    if (m.id === sel || m.id === s.hoverId) {
      ctx.strokeStyle = m.id === sel ? '#ffffff' : hexA('#ffffff', 0.6);
      ctx.lineWidth = m.id === sel ? 2 : 1.2;
      ctx.beginPath(); ctx.arc(m.x, m.y, m.r + (m.id === sel ? 6 : 4), 0, TAU); ctx.stroke();
      if (m.id === sel) {
        ctx.strokeStyle = hexA(m.color, 0.6);
        ctx.beginPath(); ctx.arc(m.x, m.y, m.r + 10, 0, TAU); ctx.stroke();
      }
    }
  }
}

/** Level hubs on the inner rim of their sector (particles core → hub → point go through them). */
function drawRingHubs(ctx: CanvasRenderingContext2D, sim: BrainSim, pulse: Fx, SX: (x: number) => number, SY: (y: number) => number, k: number, reduced: boolean) {
  for (const l of LEVELS) {
    const hub = sim.hub(l.id);
    const x = SX(hub.x); const y = SY(hub.y);
    const p = pulse.get(hub.id);
    const pv = p?.v ?? 0;
    const r = Math.max(3, hub.r * k * (1 + 0.35 * pv));
    ctx.globalAlpha = 0.85 + 0.15 * pv;
    const gs = r * (6 + 5 * pv);
    ctx.drawImage(glow(p && pv > 0.2 ? p.color : l.color), x - gs / 2, y - gs / 2, gs, gs);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = l.color; ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.stroke();
    ctx.fillStyle = l.color;
    ctx.beginPath(); ctx.arc(x, y, r * 0.45, 0, TAU); ctx.fill();
    if (pv > 0 && !reduced) {
      ctx.strokeStyle = hexA(p!.color, pv * 0.9); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, r + (1 - pv) * 22 * k, 0, TAU); ctx.stroke();
    }
  }
}

const charWidths = new Map<string, number>();   // "font|char" → width: arc labels are drawn every frame

/** Text along an arc, centred on angle `mid` (screen space); reads left to right on both halves. */
function arcText(ctx: CanvasRenderingContext2D, placed: Rect[], text: string, cx: number, cy: number, r: number, mid: number, color: string, size: number, weight: number, maxSpan: number): boolean {
  if (r <= 4) return false;
  const font = `${weight} ${size}px ${FONT}`;
  ctx.font = font;
  const chars = [...text];
  const widths = chars.map(ch => {
    const key = `${font}|${ch}`;
    let cw = charWidths.get(key);
    if (cw === undefined) { cw = ctx.measureText(ch).width + 1.2; charWidths.set(key, cw); }
    return cw;
  });
  const span = widths.reduce((a, b) => a + b, 0) / r;
  if (span > maxSpan) return false;
  const bottom = Math.sin(mid) > 0.0001;
  let a = bottom ? mid + span / 2 : mid - span / 2;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineWidth = 3;
  ctx.strokeStyle = 'rgba(2, 8, 20, 0.85)';
  ctx.fillStyle = color;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  chars.forEach((ch, i) => {
    const half = widths[i] / 2 / r;
    const ca = bottom ? a - half : a + half;
    const x = cx + Math.cos(ca) * r; const y = cy + Math.sin(ca) * r;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(bottom ? ca - Math.PI / 2 : ca + Math.PI / 2);
    ctx.strokeText(ch, 0, 0);
    ctx.fillText(ch, 0, 0);
    ctx.restore();
    minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    a = bottom ? a - 2 * half : a + 2 * half;
  });
  placed.push([minX - size * 0.6, minY - size * 0.7, maxX + size * 0.6, maxY + size * 0.7]);
  return true;
}

/** Straight label block pushed outward from an anchor (direction ux, uy), skipped if it would overlap. */
function radialBlock(ctx: CanvasRenderingContext2D, placed: Rect[], lines: { text: string; color: string; size: number; weight: number }[], px: number, py: number, ux: number, uy: number): boolean {
  let bw = 0;
  for (const l of lines) { ctx.font = `${l.weight} ${l.size}px ${FONT}`; bw = Math.max(bw, ctx.measureText(l.text).width + 6); }
  const hs = lines.map(l => l.size * 1.3);
  const bh = hs.reduce((a, b) => a + b, 0);
  const e = (Math.abs(ux) * bw) / 2 + (Math.abs(uy) * bh) / 2;
  const cx = px + ux * e; const cy = py + uy * e;
  const rect: Rect = [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2];
  if (placed.some(p => rect[0] < p[2] && rect[2] > p[0] && rect[1] < p[3] && rect[3] > p[1])) return false;
  placed.push(rect);
  let y = cy - bh / 2;
  lines.forEach((l, i) => {
    y += hs[i] / 2;
    label(ctx, [], l.text, cx, y, l.color, l.size, l.weight, true);
    y += hs[i] / 2;
  });
  return true;
}

const categoryName = (level: LevelId, category: string) =>
  category === 'conversations' ? 'Conversations' : category === 'autre' ? 'Autre'
    : level === 'actions' ? INTENT_LABELS[category] ?? category : category;

/** Ring names at the top, level names along the inner rim, category labels with their true counts. */
function drawRingLabels(ctx: CanvasRenderingContext2D, placed: Rect[], rv: RingView, SX: (x: number) => number, SY: (y: number) => number, k: number) {
  const { geo, rot, totals } = rv;
  const ox = SX(0); const oy = SY(0);
  const top = -Math.PI / 2;
  const memoryTotal = LEVELS.reduce((t, l) => t + (totals[l.id] ?? 0), 0);
  const routines = rv.extras.filter(n => n.type === 'routine' && !n.dyingAt).length;
  const tools = rv.extras.filter(n => n.type === 'tool' && !n.dyingAt).length;
  arcText(ctx, placed, `OUTILS · ${rv.toolsKnown ? fmtNum(tools, 0) : 'indisponible'}`, ox, oy, (geo.toolR + 24) * k, top, hexA(TOOL_COLOR, 0.95), 10.5, 700, Math.PI);
  arcText(ctx, placed, `ROUTINES · ${rv.routinesKnown ? fmtNum(routines, 0) : 'indisponible'}`, ox, oy, ((geo.routineR + geo.toolR) / 2) * k, top, hexA(ROUTINE_COLOR, 0.95), 10.5, 700, Math.PI);
  arcText(ctx, placed, `MÉMOIRE · ${fmtNum(memoryTotal, 0)}`, ox, oy, (geo.r1 + 23) * k, top, '#bff3ff', 10.5, 700, Math.PI);

  // Level names (with their true total when it fits) along the inner rim of each sector.
  for (const sec of geo.sectors) {
    const l = LEVEL_BY_ID[sec.level];
    const mid = (sec.a0 + sec.a1) / 2 + rot.memory;
    const span = (sec.a1 - sec.a0) * 0.95;
    const rr = (geo.r0 - 24) * k;
    const color = hexA(l.color, 0.95);
    if (!arcText(ctx, placed, `${l.short.toUpperCase()} · ${fmtNum(totals[sec.level] ?? 0, 0)}`, ox, oy, rr, mid, color, 9.5, 650, span)) {
      arcText(ctx, placed, l.short.toUpperCase(), ox, oy, rr, mid, color, 9.5, 650, span);
    }
  }

  // Categories, the largest first; « +N non affichés » when the service holds more than it sent.
  if (k <= 0.4) return;
  const cats = [...geo.categories].sort((a, b) => b.count - a.count || a.category.localeCompare(b.category, 'fr'));
  for (const c of cats) {
    const mid = (c.a0 + c.a1) / 2 + rot.memory;
    const ux = Math.cos(mid); const uy = Math.sin(mid);
    const rr = (Math.max(c.outer, geo.r0 + 6) + 6) * k;
    const color = hexA(LEVEL_BY_ID[c.level].color, 0.85);
    const lines = [{ text: `${truncate(categoryName(c.level, c.category).toUpperCase(), 22)} · ${fmtNum(c.count, 0)}`, color, size: 10, weight: 600 }];
    const hidden = c.count - c.shown;
    if (hidden > 0) lines.push({ text: `+${fmtNum(hidden, 0)} non affiché${hidden > 1 ? 's' : ''}`, color: '#9fb9da', size: 9.5, weight: 500 });
    radialBlock(ctx, placed, lines, ox + ux * rr, oy + uy * rr, ux, uy);
  }
}

// ---------------------------------------------------------------- tool map (page Tâches, 11/11/2026)
// The same look as the memory disk (bands, glows, hexagons), for the tools alone. Called only when the geometry
// carries `toolSectors` (rings.ts, toolMapLayout).

/** The sign of a tool that cannot serve now, in place of the bright dot: a bar (désactivé), a cross (indisponible). */
function toolSign(ctx: CanvasRenderingContext2D, state: string | undefined, x: number, y: number, r: number, kk: number) {
  const d = r * 0.36;
  ctx.strokeStyle = 'rgba(255,255,255,0.9)';
  ctx.lineWidth = Math.max(1.4, 1.7 * kk);
  ctx.lineCap = 'round';
  ctx.beginPath();
  if (state === 'absent') {
    ctx.moveTo(x - d, y - d); ctx.lineTo(x + d, y + d);
    ctx.moveTo(x + d, y - d); ctx.lineTo(x - d, y + d);
  } else {
    ctx.moveTo(x - d * 1.2, y); ctx.lineTo(x + d * 1.2, y);
  }
  ctx.stroke();
  ctx.lineCap = 'butt';
}

/** One luminous band per large family, lit while one of its tools is being used; a family without tool is only outlined. */
function drawToolMapBackdrop(ctx: CanvasRenderingContext2D, rv: RingView, sectors: RingToolSector[], pulse: Fx, ox: number, oy: number, k: number, kk: number) {
  const { geo, rot } = rv;
  const r0 = geo.r0 * k;
  const r1 = geo.r1 * k;
  const lit = new Map<string, number>();
  for (const n of rv.extras) {
    const v = pulse.get(n.id)?.v ?? 0;
    if (n.type !== 'tool' || v <= 0) continue;
    const group = n.tool?.group ?? '';
    lit.set(group, Math.max(lit.get(group) ?? 0, v * 0.5));
  }
  for (const sec of sectors) {
    const pv = lit.get(sec.id) ?? 0;
    const a0 = sec.a0 + rot.tools;
    const a1 = sec.a1 + rot.tools;
    const mid = (a0 + a1) / 2;
    const rm = (r0 + r1) / 2;
    const gsz = (r1 - r0) * 2.4 + 70 * k;
    ctx.globalAlpha = (sec.count ? 0.09 : 0.04) + 0.16 * pv;
    ctx.drawImage(glow(TOOL_COLOR), ox + Math.cos(mid) * rm - gsz / 2, oy + Math.sin(mid) * rm - gsz / 2, gsz, gsz);
    ctx.globalAlpha = 1;
    ctx.fillStyle = hexA(TOOL_COLOR, (sec.count ? 0.05 : 0.018) + 0.08 * pv);
    ctx.beginPath(); ctx.arc(ox, oy, r1, a0, a1); ctx.arc(ox, oy, r0, a1, a0, true); ctx.closePath(); ctx.fill();
    // Inner rim: the family's luminous arc, facing the core.
    ctx.strokeStyle = hexA(TOOL_COLOR, 0.2 + 0.5 * pv);
    ctx.lineWidth = (5 + 5 * pv) * kk;
    ctx.beginPath(); ctx.arc(ox, oy, (geo.r0 - 8) * k, a0, a1); ctx.stroke();
    ctx.strokeStyle = hexA(TOOL_COLOR, 0.75 + 0.25 * pv);
    ctx.lineWidth = (1.4 + 1.6 * pv) * kk;
    ctx.stroke();
    ctx.strokeStyle = hexA(TOOL_COLOR, sec.count ? 0.3 : 0.22);
    ctx.lineWidth = 1;
    if (!sec.count) ctx.setLineDash([3 * kk, 4 * kk]);
    ctx.beginPath(); ctx.arc(ox, oy, r1, a0, a1); ctx.stroke();
    if (!sec.count) ctx.setLineDash([]);
  }
}

/** The name of each large family outside its sector, and what it holds. Straight text: readable all around the disk. */
function drawToolMapLabels(ctx: CanvasRenderingContext2D, placed: Rect[], rv: RingView, sectors: RingToolSector[], SX: (x: number) => number, SY: (y: number) => number, k: number) {
  const { geo, rot } = rv;
  const ox = SX(0); const oy = SY(0);
  for (const sec of sectors) {
    const mid = (sec.a0 + sec.a1) / 2 + rot.tools;
    const ux = Math.cos(mid); const uy = Math.sin(mid);
    const rr = (geo.r1 + 8) * k;
    const title = { text: truncate(sec.title.toUpperCase(), 28), color: '#cfe0ff', size: 11, weight: 700 };
    const holds = sec.count ? `${fmtNum(sec.count, 0)} outil${sec.count > 1 ? 's' : ''}`
      : sec.builtins ? `${fmtNum(sec.builtins, 0)} capacité${sec.builtins > 1 ? 's' : ''} intégrée${sec.builtins > 1 ? 's' : ''}` : 'aucun outil';
    const px = ox + ux * rr; const py = oy + uy * rr;
    // The count is dropped before the name when two labels would overlap (small view).
    if (!radialBlock(ctx, placed, [title, { text: holds, color: '#9fb9da', size: 10, weight: 500 }], px, py, ux, uy)) {
      radialBlock(ctx, placed, [title], px, py, ux, uy);
    }
  }
}
