// Canvas render loop of the globe (orthographic projection, d3-geo).
// Static layers (ocean, graticule, land, night, city lights) are cached in an offscreen canvas and
// only redrawn when the view or the Sun moves; markers, pulses and rings are drawn every frame.
// The loop runs only while something moves and the globe is visible (tab shown, element on screen,
// motion allowed); otherwise it draws on demand.
import { geoCircle, geoDistance, geoGraticule10, geoOrthographic, geoPath, type GeoPermissibleObjects } from 'd3-geo';
import type { LandDetail } from './land';
import { DEG, RAD, clamp, easeInOutCubic, norm180, phaseOf, subsolarPoint, type LatLon } from './geo';
import { drawSkyGlyph, roundRectPath, withAlpha } from './draw';
import type { GlobeHud, GlobeMarker, GlobeRenderProps, GlobeRing } from './types';

const GRATICULE = geoGraticule10();
/** Night bands, radii (degrees) around the anti-solar point: from sunset (90°) through civil,
 * nautical and astronomical twilight (72°); stacked semi-transparent fills give a soft terminator. */
const NIGHT_BANDS = [90, 87, 84, 81, 78, 75, 72];
const HALO = 1.2;
/** Horizontal reach of the HUD crosshair, relative to the globe radius. */
const HUD_SPAN = 1.38;
const TAU = Math.PI * 2;

/** Pixel ratio cap for the base layer while the globe moves (detailed land is redrawn once still). */
const MOVING_DPR = 1.25;
/** Minimum interval between ambient frames (≈30 fps). */
const AMBIENT_FRAME_MS = 31;
/** Angular speed of the orbiting spheres (rad/s): fast while thinking, slow while speaking. */
const SPHERE_SPEED = { reflexion: 1.9, parole: 0.45, repos: 0.3 } as const;
/** Spheres per ring (the first rings only), set between the ring's own dots. */
const SPHERES_PER_RING = [3, 2];

interface Projected { m: GlobeMarker; x: number; y: number; r: number }
interface FocusAnim { from: [number, number]; to: [number, number]; start: number; dur: number }
interface Layer { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D; key: string; dpr: number }

function makeLayer(): Layer | null {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  return ctx ? { canvas, ctx, key: '', dpr: 1 } : null;
}

export class GlobeEngine {
  private readonly ctx: CanvasRenderingContext2D;
  /** Full-quality base (requested land detail, device pixel ratio). */
  private readonly baseHi: Layer;
  /** Cheap base used while rotating/dragging when the requested detail is heavy (50m). */
  private readonly baseLo: Layer;
  // No adaptive resampling: land vertices are dense and the graticule / night circles are
  // generated every 2–2.5°, so chords stay below a pixel; this saves ~40 % of projection time.
  private readonly projection = geoOrthographic().clipAngle(90).precision(0);
  private lands: Partial<Record<LandDetail, GeoPermissibleObjects>> = {};
  private lastInteract = -1e9;
  private settleTimer = 0;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private rot: [number, number];
  private zoom: number;
  private raf = 0;
  private lastT = 0;
  private readonly t0 = performance.now();
  private visible = typeof document === 'undefined' || document.visibilityState === 'visible';
  private inView = true;
  private reduced = false;
  private resumeAt = 0;
  private anim: FocusAnim | null = null;
  private sun: LatLon = subsolarPoint(new Date());
  private sunComputedAt = 0;
  private nightPolys: GeoPermissibleObjects[] = [];
  private nightKey = '';
  private projected: Projected[] = [];
  private hoverId: string | null = null;
  // HUD state: glow 0 (rest) → 1 (active), smoothed voice level, rotation and ripple phases.
  private hudGlow = 0;
  private hudLevel = 0;
  private hudSpin = 0;
  private hudWave = 0;
  // Orbiting spheres (hud.spheres): visibility 0 → 1, angular speed (rad/s) and phase, all smoothed.
  private sphereVis = 0;
  private sphereSpeed = SPHERE_SPEED.repos;
  private spherePhase = 0;
  // pointer state
  private pointers = new Map<number, { x: number; y: number }>();
  private dragStart: { x: number; y: number; rot: [number, number]; moved: boolean } | null = null;
  private pinch: { dist: number; zoom: number } | null = null;
  private destroyed = false;

  constructor(private readonly canvas: HTMLCanvasElement, private readonly getProps: () => GlobeRenderProps) {
    const ctx = canvas.getContext('2d');
    const hi = makeLayer();
    const lo = makeLayer();
    if (!ctx || !hi || !lo) throw new Error('Canvas 2D indisponible');
    this.ctx = ctx;
    this.baseHi = hi;
    this.baseLo = lo;
    const p = getProps();
    const c = p.initialCenter ?? { lat: 20, lon: 10 };
    this.rot = [-c.lon, clamp(-c.lat, -85, 85)];
    this.zoom = p.initialZoom ?? 1;
  }

  // ------------------------------------------------------------ lifecycle

  destroy() {
    this.destroyed = true;
    if (this.raf) cancelAnimationFrame(this.raf);
    window.clearTimeout(this.settleTimer);
    this.raf = 0;
  }

  setLand(geo: GeoPermissibleObjects, detail: LandDetail) {
    this.lands[detail] = geo;
    this.baseHi.key = '';
    this.baseLo.key = '';
    this.invalidate();
  }

  resize(w: number, h: number, dpr: number) {
    if (w === this.w && h === this.h && dpr === this.dpr) return;
    this.w = Math.max(0, Math.round(w));
    this.h = Math.max(0, Math.round(h));
    this.dpr = dpr;
    const sizes: [HTMLCanvasElement, number][] = [[this.canvas, dpr], [this.baseHi.canvas, dpr], [this.baseLo.canvas, Math.min(dpr, MOVING_DPR)]];
    for (const [c, ratio] of sizes) {
      c.width = Math.max(1, Math.round(this.w * ratio));
      c.height = Math.max(1, Math.round(this.h * ratio));
    }
    this.baseHi.dpr = dpr;
    this.baseLo.dpr = Math.min(dpr, MOVING_DPR);
    this.baseHi.key = '';
    this.baseLo.key = '';
    this.draw(performance.now(), this.getProps()); // synchronous: avoids a blank frame after resize
    this.invalidate();
  }

  /** Marks a user interaction (wheel, pinch) and schedules the full-quality redraw once it settles. */
  private touch() {
    this.lastInteract = performance.now();
    window.clearTimeout(this.settleTimer);
    this.settleTimer = window.setTimeout(this.invalidate, 220);
  }

  setVisible(v: boolean) { this.visible = v; if (v) this.invalidate(); }
  setInView(v: boolean) { this.inView = v; if (v) this.invalidate(); }
  setReduced(v: boolean) { this.reduced = v; this.invalidate(); }

  invalidate = () => {
    if (this.destroyed || this.raf || !this.visible) return;
    this.raf = requestAnimationFrame(this.frame);
  };

  // ------------------------------------------------------------ view control

  zoomBy(factor: number) {
    const p = this.getProps();
    this.zoom = clamp(this.zoom * factor, p.minZoom ?? 0.8, p.maxZoom ?? 6);
    this.touch();
    this.invalidate();
  }

  resetView() {
    const p = this.getProps();
    const c = p.initialCenter ?? { lat: 20, lon: 10 };
    this.zoom = p.initialZoom ?? 1;
    this.focusOn(c.lat, c.lon);
  }

  focusOn(lat: number, lon: number) {
    const to: [number, number] = [-lon, clamp(-lat, -85, 85)];
    if (this.reduced) {
      this.rot = to;
      this.anim = null;
    } else {
      const from: [number, number] = [this.rot[0], this.rot[1]];
      const dLon = norm180(to[0] - from[0]);
      this.anim = { from, to: [from[0] + dLon, to[1]], start: performance.now(), dur: 900 };
    }
    this.pauseAutoRotate();
    this.invalidate();
  }

  nudge(dLon: number, dLat: number) {
    this.anim = null;
    this.rot = [norm180(this.rot[0] - dLon), clamp(this.rot[1] - dLat, -85, 85)];
    this.pauseAutoRotate();
    this.touch(); // key repeat stays on the cheap layer
    this.invalidate();
  }

  private pauseAutoRotate(ms = 4000) {
    this.resumeAt = performance.now() + ms;
  }

  // ------------------------------------------------------------ pointer input

  private local(e: PointerEvent | WheelEvent) {
    const rect = this.canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  private radius(p: GlobeRenderProps) {
    // Fit the globe, its halo and the decorative rings inside the canvas.
    let hx = HALO;
    let hy = HALO;
    for (const ring of p.rings ?? []) {
      const rx = ring.radius;
      const ry = ring.radius * ring.flatten;
      const t = ring.tilt * RAD;
      hx = Math.max(hx, Math.hypot(rx * Math.cos(t), ry * Math.sin(t)) + 0.04);
      hy = Math.max(hy, Math.hypot(rx * Math.sin(t), ry * Math.cos(t)) + 0.04);
    }
    if (p.hud) hx = Math.max(hx, HUD_SPAN); // crosshair lines reach past the halo sideways only
    return Math.max(10, Math.min(this.w / 2 / hx, this.h / 2 / hy) * 0.98);
  }

  /** Geographic position under a canvas point, or null outside the disc. */
  private invert(x: number, y: number): LatLon | null {
    const r = this.radius(this.getProps()) * this.zoom;
    if (Math.hypot(x - this.w / 2, y - this.h / 2) > r) return null;
    const ll = this.projection.invert?.([x, y]);
    if (!ll || Number.isNaN(ll[0]) || Number.isNaN(ll[1])) return null;
    return { lon: norm180(ll[0]), lat: ll[1] };
  }

  private pick(x: number, y: number): GlobeMarker | null {
    let best: Projected | null = null;
    let bestD = Infinity;
    for (const p of this.projected) {
      const d = Math.hypot(p.x - x, p.y - y);
      if (d <= Math.max(p.r + 5, 9) && d < bestD) { best = p; bestD = d; }
    }
    return best?.m ?? null;
  }

  pointerDown(e: PointerEvent) {
    const p = this.getProps();
    if (!p.interactive) return;
    const pt = this.local(e);
    this.pointers.set(e.pointerId, pt);
    try { this.canvas.setPointerCapture(e.pointerId); } catch { /* capture refusée */ }
    if (this.pointers.size === 2) {
      const [a, b] = [...this.pointers.values()];
      this.pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y) || 1, zoom: this.zoom };
      this.dragStart = null;
    } else {
      this.dragStart = { x: pt.x, y: pt.y, rot: [this.rot[0], this.rot[1]], moved: false };
    }
    this.anim = null;
  }

  pointerMove(e: PointerEvent) {
    const p = this.getProps();
    const pt = this.local(e);
    if (this.pointers.has(e.pointerId)) this.pointers.set(e.pointerId, pt);
    if (this.pinch && this.pointers.size >= 2) {
      const [a, b] = [...this.pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      this.zoom = clamp(this.pinch.zoom * (d / this.pinch.dist), p.minZoom ?? 0.8, p.maxZoom ?? 6);
      this.pauseAutoRotate();
      this.touch();
      this.invalidate();
      return;
    }
    if (this.dragStart) {
      const dx = pt.x - this.dragStart.x;
      const dy = pt.y - this.dragStart.y;
      if (!this.dragStart.moved && Math.hypot(dx, dy) > 3) {
        this.dragStart.moved = true;
        p.onUserInteract?.();
      }
      if (this.dragStart.moved) {
        const k = (DEG / (this.radius(p) * this.zoom)) * 0.9; // degrees per pixel
        this.rot = [norm180(this.dragStart.rot[0] + dx * k), clamp(this.dragStart.rot[1] - dy * k, -85, 85)];
        this.pauseAutoRotate();
        this.canvas.style.cursor = 'grabbing';
        this.invalidate();
        return;
      }
    }
    this.hover(pt.x, pt.y);
  }

  pointerUp(e: PointerEvent) {
    const p = this.getProps();
    const pt = this.local(e);
    const wasClick = this.dragStart && !this.dragStart.moved && this.pointers.size === 1;
    this.pointers.delete(e.pointerId);
    if (this.pointers.size < 2) this.pinch = null;
    if (wasClick) {
      const m = this.pick(pt.x, pt.y);
      if (m) p.onMarkerClick?.(m);
    }
    this.dragStart = null;
    this.canvas.style.cursor = '';
    this.hover(pt.x, pt.y);
    this.invalidate(); // full-quality frame once the drag ends
  }

  pointerLeave() {
    if (this.pointers.size) return;
    this.setHover(null);
    this.getProps().onHover?.(null, null);
  }

  wheel(deltaY: number) {
    this.touch();
    this.zoomBy(Math.exp(-deltaY * 0.0012));
    this.getProps().onUserInteract?.();
    this.pauseAutoRotate();
  }

  /** True while the view changes every frame (the cheap base layer is used then). */
  private moving(p: GlobeRenderProps, t: number): boolean {
    return !!this.anim || !!this.dragStart?.moved || !!this.pinch || t - this.lastInteract < 200
      || (!!p.autoRotate && !this.reduced && t >= this.resumeAt);
  }

  private hover(x: number, y: number) {
    const p = this.getProps();
    const m = p.interactive || p.onMarkerClick ? this.pick(x, y) : null;
    this.setHover(m?.id ?? null);
    this.canvas.style.cursor = m && p.onMarkerClick ? 'pointer' : p.interactive ? 'grab' : '';
    if (p.onHover) {
      const ll = this.invert(x, y);
      p.onHover(ll ? ll.lat : null, ll ? ll.lon : null);
    }
  }

  private setHover(id: string | null) {
    if (id === this.hoverId) return;
    this.hoverId = id;
    this.invalidate();
  }

  // ------------------------------------------------------------ frame loop

  private frame = (t: number) => {
    this.raf = 0;
    if (this.destroyed) return;
    const p = this.getProps();
    const motion = !this.reduced;
    const animated = motion && (
      (p.autoRotate ?? false)
      || (p.markers ?? []).some(m => m.pulse)
      || (p.rings ?? []).some(r => (r.dots ?? 0) > 0)
      || (!!p.hud && (p.hud.active || this.hudGlow > 0.01 || !!p.hud.spheres || this.sphereVis > 0.01))
    );
    const next = () => {
      if (this.visible && this.inView && (this.anim || animated)) this.raf = requestAnimationFrame(this.frame);
      else this.lastT = 0;
    };
    // Ambient motion (auto-rotation, pulses, ring dots) is capped at ~30 fps to save power;
    // user-driven motion (drag, pinch, wheel, focus animation) runs at the display rate.
    const userDriven = !!this.anim || !!this.dragStart?.moved || !!this.pinch || t - this.lastInteract < 200;
    if (animated && !userDriven && this.lastT && t - this.lastT < AMBIENT_FRAME_MS) { next(); return; }
    const dt = this.lastT ? Math.min(0.1, (t - this.lastT) / 1000) : 0;
    this.lastT = t;

    if (this.anim) {
      const k = clamp((t - this.anim.start) / this.anim.dur, 0, 1);
      const e = easeInOutCubic(k);
      this.rot = [
        norm180(this.anim.from[0] + (this.anim.to[0] - this.anim.from[0]) * e),
        this.anim.from[1] + (this.anim.to[1] - this.anim.from[1]) * e,
      ];
      if (k >= 1) this.anim = null;
    } else if (p.autoRotate && motion && !this.dragStart?.moved && !this.pinch && t >= this.resumeAt) {
      this.rot = [norm180(this.rot[0] + (p.rotateSpeed ?? 4) * dt), this.rot[1]];
    }
    if (p.hud) this.stepHud(p.hud, dt);

    this.draw(t, p);
    next();
  };

  private updateSun(p: GlobeRenderProps, now: number) {
    if (p.sunDate) {
      this.sun = subsolarPoint(p.sunDate);
    } else if (now - this.sunComputedAt > 30_000 || !this.sunComputedAt) {
      this.sun = subsolarPoint(new Date());
      this.sunComputedAt = now;
    }
    const key = `${this.sun.lat.toFixed(2)},${this.sun.lon.toFixed(2)}`;
    if (key !== this.nightKey) {
      this.nightKey = key;
      const center: [number, number] = [norm180(this.sun.lon + 180), -this.sun.lat];
      this.nightPolys = NIGHT_BANDS.map(radius => geoCircle().center(center).radius(radius).precision(2)());
    }
  }

  private draw(t: number, p: GlobeRenderProps) {
    const { ctx, w, h } = this;
    if (!w || !h) return;
    const r = this.radius(p) * this.zoom;
    const cx = w / 2;
    const cy = h / 2;
    const time = this.reduced ? 0 : (t - this.t0) / 1000;
    this.updateSun(p, t);
    this.projection.scale(r).translate([cx, cy]).rotate([this.rot[0], this.rot[1], 0]);

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const rings = p.rings ?? [];
    const spheres = !!p.hud && this.sphereVis > 0.01;
    rings.forEach((ring, i) => {
      this.drawRing(ring, cx, cy, r, time, false);
      if (spheres) this.drawSpheres(ring, i, cx, cy, r, false);
    });
    this.drawHalo(cx, cy, r);
    // While moving, a 50m globe is drawn from the 110m polygons at a capped pixel ratio
    // (≈ 6× cheaper); the detailed layer is redrawn as soon as the view is still.
    const wanted = p.detail ?? '110m';
    const lite = wanted === '50m' && this.moving(p, t) && !!this.lands['110m'];
    const layer = lite ? this.baseLo : this.baseHi;
    const landId: LandDetail | null = lite ? '110m' : this.lands[wanted] ? wanted : this.lands['110m'] ? '110m' : this.lands['50m'] ? '50m' : null;
    this.ensureBase(layer, landId, p, r, cx, cy);
    ctx.drawImage(layer.canvas, 0, 0, w, h);
    if (p.hud) this.drawHud(cx, cy, r);
    this.drawMarkers(p, r, time);
    rings.forEach((ring, i) => {
      this.drawRing(ring, cx, cy, r, time, true);
      if (spheres) this.drawSpheres(ring, i, cx, cy, r, true);
    });
    this.drawTooltip(p);
  }

  private drawHalo(cx: number, cy: number, r: number) {
    const { ctx } = this;
    const g = ctx.createRadialGradient(cx, cy, r * 0.96, cx, cy, r * HALO);
    g.addColorStop(0, 'rgba(34,200,255,0.42)');
    g.addColorStop(0.3, 'rgba(34,170,255,0.14)');
    g.addColorStop(1, 'rgba(34,120,255,0)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, r * HALO, 0, TAU);
    ctx.fill();
  }

  private stepHud(hud: GlobeHud, dt: number) {
    if (this.reduced) { // no motion: lit or not, nothing turns or pulses
      this.hudGlow = hud.active ? 1 : 0;
      this.hudLevel = 0;
      this.sphereVis = hud.spheres ? 1 : 0;
      return;
    }
    this.sphereVis += ((hud.spheres ? 1 : 0) - this.sphereVis) * (1 - Math.exp(-dt * 3));
    this.sphereSpeed += (SPHERE_SPEED[hud.spheres ?? 'repos'] - this.sphereSpeed) * (1 - Math.exp(-dt * 2.5));
    this.spherePhase = (this.spherePhase + dt * this.sphereSpeed) % TAU;
    this.hudGlow += ((hud.active ? 1 : 0) - this.hudGlow) * (1 - Math.exp(-dt * 4));
    const target = hud.active ? clamp(hud.level?.() ?? 0, 0, 1) : 0;
    this.hudLevel += (target - this.hudLevel) * (1 - Math.exp(-dt * (target > this.hudLevel ? 18 : 5)));
    this.hudSpin = (this.hudSpin + dt * (0.04 + 0.5 * this.hudGlow + 0.6 * this.hudLevel)) % TAU;
    this.hudWave = (this.hudWave + dt * (0.35 + 0.9 * this.hudLevel) * this.hudGlow) % 1;
  }

  /** Blue HUD around the globe: voice ripples, a fine and a dashed circle, three glowing arcs, a tick dial
   *  and a crosshair on each side. Faint at rest; lit, turning and pulsing with the level while active. */
  private drawHud(cx: number, cy: number, r: number) {
    const { ctx } = this;
    const g = this.hudGlow;
    const lv = this.hudLevel;
    const lit = (color: string, rest: number, on: number) => withAlpha(color, rest + (on - rest) * g);
    const circle = (k: number, a0 = 0, a1 = TAU) => { ctx.beginPath(); ctx.arc(cx, cy, r * k, a0, a1); ctx.stroke(); };
    const dot = (x: number, y: number, size: number) => { ctx.beginPath(); ctx.arc(x, y, size, 0, TAU); ctx.fill(); };
    ctx.save();
    ctx.lineCap = 'round';

    if (g > 0.01) { // ripples leaving the globe while the conversation is live
      for (let i = 0; i < 3; i++) {
        const ph = (this.hudWave + i / 3) % 1;
        ctx.strokeStyle = withAlpha('#22c8ff', g * (1 - ph) * (0.16 + 0.6 * lv));
        ctx.lineWidth = 1 + 2.2 * lv * (1 - ph);
        circle(1.03 + 0.17 * ph);
      }
    }

    ctx.lineWidth = 1;
    ctx.strokeStyle = lit('#78d2ff', 0.1, 0.4);
    circle(1.055);
    ctx.setLineDash([2, 7]);
    ctx.strokeStyle = lit('#22c8ff', 0.14, 0.6);
    circle(1.1, this.hudSpin, this.hudSpin + TAU);
    ctx.setLineDash([]);

    const arc0 = -this.hudSpin * 1.4;
    const span = 1.05;
    ctx.lineWidth = 2 + 1.5 * lv;
    ctx.strokeStyle = lit('#2faaff', 0.18, 0.9);
    if (g > 0.05) { ctx.shadowColor = 'rgba(34,200,255,0.9)'; ctx.shadowBlur = 10 * g; }
    for (let i = 0; i < 3; i++) circle(1.145, arc0 + (i * TAU) / 3, arc0 + (i * TAU) / 3 + span);
    ctx.shadowBlur = 0;
    ctx.fillStyle = lit('#bef0ff', 0.2, 0.95);
    for (let i = 0; i < 3; i++) {
      const a = arc0 + (i * TAU) / 3 + span;
      dot(cx + Math.cos(a) * r * 1.145, cy + Math.sin(a) * r * 1.145, 2 + 1.2 * g);
    }

    ctx.lineWidth = 1;
    ctx.strokeStyle = lit('#78d2ff', 0.1, 0.42);
    ctx.beginPath();
    for (let i = 0; i < 120; i++) {
      const a = (i / 120) * TAU;
      const k1 = 1.18 + (i % 10 === 0 ? 0.028 : 0.012);
      ctx.moveTo(cx + Math.cos(a) * r * 1.18, cy + Math.sin(a) * r * 1.18);
      ctx.lineTo(cx + Math.cos(a) * r * k1, cy + Math.sin(a) * r * k1);
    }
    ctx.stroke();

    ctx.strokeStyle = lit('#22c8ff', 0.12, 0.55);
    ctx.beginPath();
    for (const s of [-1, 1]) {
      ctx.moveTo(cx + s * r * 1.22, cy);
      ctx.lineTo(cx + s * r * 1.34, cy);
      for (let k = 0; k < 4; k++) {
        const x = cx + s * r * (1.24 + k * 0.03);
        ctx.moveTo(x, cy - 3);
        ctx.lineTo(x, cy + 3);
      }
    }
    ctx.stroke();
    ctx.fillStyle = lit('#78dcff', 0.25, 0.95);
    for (const s of [-1, 1]) dot(cx + s * r * 1.34, cy, 2.2 + 1.5 * lv);
    ctx.restore();
  }

  private ensureBase(layer: Layer, landId: LandDetail | null, p: GlobeRenderProps, r: number, cx: number, cy: number) {
    const lights = p.cityLights?.length ?? 0;
    const key = [this.rot[0].toFixed(2), this.rot[1].toFixed(2), r.toFixed(1), this.w, this.h, layer.dpr,
      landId ?? '-', p.showNight ? this.nightKey : '-', lights ? this.nightKey : '-', lights, p.graticule !== false].join('|');
    if (key === layer.key) return;
    layer.key = key;
    const land = landId ? this.lands[landId] ?? null : null;

    const b = layer.ctx;
    b.setTransform(layer.dpr, 0, 0, layer.dpr, 0, 0);
    b.clearRect(0, 0, this.w, this.h);
    const path = geoPath(this.projection, b);

    // ocean
    const ocean = b.createRadialGradient(cx - r * 0.35, cy - r * 0.4, r * 0.05, cx, cy, r * 1.05);
    ocean.addColorStop(0, '#0f4a8c');
    ocean.addColorStop(0.55, '#072a5c');
    ocean.addColorStop(1, '#020c22');
    b.beginPath();
    b.arc(cx, cy, r, 0, TAU);
    b.fillStyle = ocean;
    b.fill();

    if (p.graticule !== false) {
      b.beginPath();
      path(GRATICULE);
      b.strokeStyle = 'rgba(110,190,255,0.11)';
      b.lineWidth = 0.6;
      b.stroke();
    }

    if (land) {
      b.beginPath();
      path(land);
      const lg = b.createLinearGradient(cx - r, cy - r, cx + r, cy + r);
      lg.addColorStop(0, 'rgba(72,196,255,0.80)');
      lg.addColorStop(1, 'rgba(26,98,196,0.74)');
      b.fillStyle = lg;
      b.fill();
      b.lineJoin = 'bevel';
      if (layer === this.baseHi) { // coastline glow: skipped on the moving layer (wide strokes are costly)
        b.strokeStyle = 'rgba(34,200,255,0.22)';
        b.lineWidth = 2.6;
        b.stroke();
      }
      b.strokeStyle = 'rgba(175,240,255,0.85)';
      b.lineWidth = 0.7;
      b.stroke();
    }

    if (p.showNight) {
      b.fillStyle = 'rgba(1,5,18,0.12)';
      for (const poly of this.nightPolys) {
        b.beginPath();
        path(poly);
        b.fill();
      }
    }

    if (lights) this.drawCityLights(b, p, r);

    // limb brightening + soft specular highlight + rim line
    const rim = b.createRadialGradient(cx, cy, r * 0.8, cx, cy, r);
    rim.addColorStop(0, 'rgba(60,180,255,0)');
    rim.addColorStop(1, 'rgba(100,215,255,0.34)');
    b.beginPath();
    b.arc(cx, cy, r, 0, TAU);
    b.fillStyle = rim;
    b.fill();
    const sx = cx - r * 0.42;
    const sy = cy - r * 0.48;
    const spec = b.createRadialGradient(sx, sy, 0, sx, sy, r * 0.9);
    spec.addColorStop(0, 'rgba(210,244,255,0.11)');
    spec.addColorStop(1, 'rgba(210,244,255,0)');
    b.fillStyle = spec;
    b.fill();
    b.strokeStyle = 'rgba(130,225,255,0.6)';
    b.lineWidth = 1;
    b.stroke();
  }

  private drawCityLights(b: CanvasRenderingContext2D, p: GlobeRenderProps, r: number) {
    const center: [number, number] = [-this.rot[0], -this.rot[1]];
    const sun: [number, number] = [this.sun.lon, this.sun.lat];
    const k = clamp(r / 260, 0.7, 2.4);
    b.save();
    b.globalCompositeOperation = 'lighter';
    for (const c of p.cityLights ?? []) {
      const pos: [number, number] = [c.lon, c.lat];
      const sunDist = geoDistance(pos, sun) * DEG;
      if (sunDist < 92) continue; // daylight or bright twilight
      const view = geoDistance(pos, center);
      if (view > Math.PI / 2 - 0.03) continue;
      const pt = this.projection(pos);
      if (!pt) continue;
      const a = clamp((sunDist - 92) / 14, 0, 1) * (0.35 + 0.65 * Math.cos(view));
      const s = (0.9 + c.size * 0.75) * k;
      const g = b.createRadialGradient(pt[0], pt[1], 0, pt[0], pt[1], s * 3.4);
      g.addColorStop(0, `rgba(255,232,170,${(0.95 * a).toFixed(3)})`);
      g.addColorStop(0.3, `rgba(255,196,96,${(0.42 * a).toFixed(3)})`);
      g.addColorStop(1, 'rgba(255,170,60,0)');
      b.fillStyle = g;
      b.beginPath();
      b.arc(pt[0], pt[1], s * 3.4, 0, TAU);
      b.fill();
    }
    b.restore();
  }

  private drawRing(ring: GlobeRing, cx: number, cy: number, r: number, time: number, front: boolean) {
    const { ctx } = this;
    const rx = r * ring.radius;
    const ry = rx * ring.flatten;
    const tilt = ring.tilt * RAD;
    const color = ring.color ?? '34,200,255';
    const [a0, a1] = front ? [0, Math.PI] : [Math.PI, TAU];
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, tilt, a0, a1);
    ctx.strokeStyle = `rgba(${color},0.08)`;
    ctx.lineWidth = 5;
    ctx.stroke();
    if (ring.dashed) ctx.setLineDash([2, 6]);
    ctx.strokeStyle = `rgba(${color},${front ? 0.6 : 0.28})`;
    ctx.lineWidth = ring.width ?? 1.1;
    ctx.stroke();
    ctx.setLineDash([]);

    const dots = ring.dots ?? 0;
    const cos = Math.cos(tilt);
    const sin = Math.sin(tilt);
    const at = (a: number) => {
      const ex = rx * Math.cos(a);
      const ey = ry * Math.sin(a);
      return [cx + ex * cos - ey * sin, cy + ex * sin + ey * cos] as const;
    };
    const speed = ring.speed ?? 0.25;
    const trail = speed >= 0 ? -0.035 : 0.035;
    for (let i = 0; i < dots; i++) {
      const a = (((i / dots) * TAU + time * speed) % TAU + TAU) % TAU;
      if ((a < Math.PI) !== front) continue;
      for (let k = 6; k >= 1; k--) { // short fading trail behind the dot
        const ta = a + k * trail;
        if (((ta % TAU + TAU) % TAU < Math.PI) !== front) continue;
        const [tx, ty] = at(ta);
        ctx.fillStyle = `rgba(${color},${(0.32 * (1 - k / 7)).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(tx, ty, 1.6 * (1 - k / 9), 0, TAU);
        ctx.fill();
      }
      const [x, y] = at(a);
      const g = ctx.createRadialGradient(x, y, 0, x, y, 7);
      g.addColorStop(0, `rgba(${color},0.75)`);
      g.addColorStop(1, `rgba(${color},0)`);
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, 7, 0, TAU);
      ctx.fill();
      ctx.fillStyle = '#e9fbff';
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  }

  /** The orbiting spheres of hud.spheres on the first rings, between the ring's own dots: the back half before the
   *  globe (dimmer), the front half after it, like the dots. They swell with the real voice level while speaking. */
  private drawSpheres(ring: GlobeRing, index: number, cx: number, cy: number, r: number, front: boolean) {
    const count = SPHERES_PER_RING[index] ?? 0;
    if (!count) return;
    const { ctx } = this;
    const rx = r * ring.radius;
    const ry = rx * ring.flatten;
    const tilt = ring.tilt * RAD;
    const cos = Math.cos(tilt);
    const sin = Math.sin(tilt);
    const at = (a: number) => {
      const ex = rx * Math.cos(a);
      const ey = ry * Math.sin(a);
      return [cx + ex * cos - ey * sin, cy + ex * sin + ey * cos] as const;
    };
    const color = ring.color ?? '34,200,255';
    const dir = (ring.speed ?? 0.25) >= 0 ? 1 : -1;
    const vis = this.sphereVis * (front ? 1 : 0.55);
    const size = clamp(r * 0.024, 4.5, 13) * (1 + 0.7 * this.hudLevel);
    const step = -dir * 0.02 * (0.5 + this.sphereSpeed);   // the trail lengthens with the speed
    ctx.save();
    for (let i = 0; i < count; i++) {
      const a = ((((i + 0.5) / count) * TAU + dir * this.spherePhase * (index ? 1.3 : 1)) % TAU + TAU) % TAU;
      if ((a < Math.PI) !== front) continue;
      for (let k = 8; k >= 1; k--) {
        const ta = a + k * step;
        if (((ta % TAU + TAU) % TAU < Math.PI) !== front) continue;
        const [tx, ty] = at(ta);
        ctx.fillStyle = `rgba(${color},${(0.45 * vis * (1 - k / 9)).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(tx, ty, size * 0.55 * (1 - k / 10), 0, TAU);
        ctx.fill();
      }
      const [x, y] = at(a);
      const glow = ctx.createRadialGradient(x, y, 0, x, y, size * 3.2);
      glow.addColorStop(0, `rgba(${color},${(0.6 * vis).toFixed(3)})`);
      glow.addColorStop(1, `rgba(${color},0)`);
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(x, y, size * 3.2, 0, TAU);
      ctx.fill();
      const core = ctx.createRadialGradient(x - size * 0.3, y - size * 0.3, 0, x, y, size);
      core.addColorStop(0, `rgba(240,252,255,${vis.toFixed(3)})`);
      core.addColorStop(1, `rgba(${color},${(0.85 * vis).toFixed(3)})`);
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(x, y, size, 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  }

  private drawMarkers(p: GlobeRenderProps, r: number, time: number) {
    const { ctx } = this;
    this.projected = [];
    const markers = p.markers ?? [];
    if (!markers.length) return;
    const center: [number, number] = [-this.rot[0], -this.rot[1]];
    const zk = Math.sqrt(this.zoom) * clamp(r / this.zoom / 260, 0.75, 1.3);
    const labels: { m: GlobeMarker; x: number; y: number; s: number; fade: number }[] = [];
    for (const m of markers) {
      const d = geoDistance([m.lon, m.lat], center);
      if (d > Math.PI / 2 - 0.015) continue;
      const pt = this.projection([m.lon, m.lat]);
      if (!pt) continue;
      const [x, y] = pt;
      const fade = d > 1.3 ? clamp((Math.PI / 2 - d) / (Math.PI / 2 - 1.3), 0.2, 1) : 1;
      const s = (m.size ?? 4) * zk;
      const color = m.color ?? '#22c8ff';
      ctx.save();
      ctx.globalAlpha = fade;
      if (m.pulse) {
        const ph = this.reduced ? 0.45 : (time * 0.55 + phaseOf(m.id)) % 1;
        ctx.strokeStyle = color;
        ctx.globalAlpha = fade * (1 - ph) * 0.75;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(x, y, s * (1.1 + ph * 2.6), 0, TAU);
        ctx.stroke();
        ctx.globalAlpha = fade;
      }
      const glow = ctx.createRadialGradient(x, y, 0, x, y, s * 2.4);
      glow.addColorStop(0, withAlpha(color, 0.66));
      glow.addColorStop(1, withAlpha(color, 0));
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(x, y, s * 2.4, 0, TAU);
      ctx.fill();
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, y, s, 0, TAU);
      ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.85)';
      ctx.beginPath();
      ctx.arc(x, y, Math.max(0.8, s * 0.35), 0, TAU);
      ctx.fill();
      if (m.id === p.selectedId || m.id === this.hoverId) {
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(x, y, s + 4, 0, TAU);
        ctx.stroke();
        if (m.id === p.selectedId) {
          ctx.beginPath();
          for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
            ctx.moveTo(x + dx * (s + 6), y + dy * (s + 6));
            ctx.lineTo(x + dx * (s + 11), y + dy * (s + 11));
          }
          ctx.stroke();
        }
      }
      ctx.restore();
      if (m.label) labels.push({ m, x, y, s, fade });
      this.projected.push({ m, x, y, r: s });
    }
    // labels above all dots
    ctx.save();
    ctx.font = '600 11px "Segoe UI", system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    for (const { m, x, y, s, fade } of labels) {
      const label = m.label ?? '';
      const glyph = m.glyph ? 14 : 0;
      const tw = ctx.measureText(label).width;
      const bw = tw + glyph + 12;
      const bx = x + s + 5;
      const by = y - 9;
      ctx.globalAlpha = fade;
      roundRectPath(ctx, bx, by, bw, 18, 9);
      ctx.fillStyle = 'rgba(4,16,38,0.82)';
      ctx.fill();
      ctx.strokeStyle = withAlpha(m.color ?? '#22c8ff', 0.6);
      ctx.lineWidth = 1;
      ctx.stroke();
      if (m.glyph) drawSkyGlyph(ctx, m.glyph, bx + 6 + 6, y, 13, m.night);
      ctx.fillStyle = '#eaf6ff';
      ctx.fillText(label, bx + 6 + glyph, y + 0.5);
    }
    ctx.restore();
  }

  private drawTooltip(p: GlobeRenderProps) {
    const id = this.hoverId ?? p.selectedId;
    if (!id) return;
    const hit = this.projected.find(q => q.m.id === id);
    const text = hit?.m.tooltip;
    if (!hit || !text) return;
    const { ctx } = this;
    ctx.save();
    ctx.font = '500 12px "Segoe UI", system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    const tw = Math.min(ctx.measureText(text).width, 280);
    const bw = tw + 18;
    const bh = 24;
    let bx = hit.x - bw / 2;
    let by = hit.y - hit.r - 14 - bh;
    bx = clamp(bx, 4, this.w - bw - 4);
    if (by < 4) by = hit.y + hit.r + 12;
    roundRectPath(ctx, bx, by, bw, bh, 7);
    ctx.fillStyle = 'rgba(6,20,46,0.94)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(34,200,255,0.65)';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = '#f3f8ff';
    ctx.fillText(text, bx + 9, by + bh / 2 + 0.5, 280);
    ctx.restore();
  }
}
