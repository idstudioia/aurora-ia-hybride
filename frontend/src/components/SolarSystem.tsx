// Pseudo-3D solar system on a canvas. Positions are propagated in the browser from the server's
// orbital elements (Kepler's equation), so the animation stays physically consistent at any speed.
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { CalendarClock, Pause, Play, Rewind, FastForward, ZoomIn, ZoomOut, Move3d } from 'lucide-react';
import { bodyElements, bodyPosition, type SolarModel } from './sim/solarData';
import { msFromJd, norm, orbitPath, periodDays, type Vec3 } from './sim/kepler';
import { fitCanvas, starField, useElementSize, useReducedMotion } from './sim/hooks';

const SPEEDS = [1, 10, 100, 1_000, 10_000, 100_000];
const RAD = Math.PI / 180;
const STARS = starField(170, 11);
const REF_RINGS = [1, 5, 10, 20, 30];

interface View { az: number; elev: number; zoom: number; mode: 'sqrt' | 'linear' }
interface Hit { key: string; x: number; y: number; r: number }

/** Fixed decimals with a French decimal comma. */
const frNum = (v: number, d: number) => v.toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtSpeed = (s: number) => `×${s.toLocaleString('fr-FR')}`;
function perRealSecond(speed: number): string {
  if (speed < 60) return `${speed} s`;
  if (speed < 3600) return `${(speed / 60).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} min`;
  if (speed < 86400) return `${(speed / 3600).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} h`;
  return `${(speed / 86400).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} j`;
}
const fmtSimDate = (jd: number) => {
  const d = new Date(msFromJd(jd));
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' })} · ${d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;
};
const isoDay = (jd: number) => {
  const d = new Date(msFromJd(jd));
  return Number.isNaN(d.getTime()) ? '' : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
const fmtPeriod = (days: number) => days < 700 ? `${days.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} j` : `${(days / 365.25).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} ans`;

export default function SolarSystem({ model, onJumpDate, onNow, loading, validYears = null }: {
  model: SolarModel;
  onJumpDate: (isoDate: string) => void;
  onNow: () => void;
  loading: boolean;
  /** Validity of the server's elements (years), when the server states it. */
  validYears?: [number, number] | null;
}) {
  const reduced = useReducedMotion();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { w, h } = useElementSize(canvasRef);

  const jdRef = useRef(model.refJd);
  const [displayJd, setDisplayJd] = useState(model.refJd);
  const [playing, setPlaying] = useState(!reduced);
  const [speed, setSpeed] = useState(10_000);
  const [reverse, setReverse] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<View>({ az: -28, elev: 30, zoom: 1, mode: 'sqrt' });
  const viewRef = useRef(view); viewRef.current = view;
  const selRef = useRef(selected); selRef.current = selected;
  const hitsRef = useRef<Hit[]>([]);
  const barRef = useRef<HTMLDivElement>(null);
  const dirtyRef = useRef(true);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);

  // New server snapshot → restart from its reference date.
  useEffect(() => { jdRef.current = model.refJd; setDisplayJd(model.refJd); dirtyRef.current = true; }, [model]);
  useEffect(() => { dirtyRef.current = true; }, [view, selected, w, h]);
  useEffect(() => { if (reduced) setPlaying(false); }, [reduced]);

  const draw = useCallback(() => {
    const ctx = fitCanvas(canvasRef.current, w, h);
    if (!ctx) return;
    const v = viewRef.current;
    const jd = jdRef.current;
    ctx.clearRect(0, 0, w, h);

    // decor: faint stars + vignette
    for (const s of STARS) {
      ctx.globalAlpha = s.a;
      ctx.fillStyle = '#cfe6ff';
      ctx.beginPath(); ctx.arc(s.x * w, s.y * h, s.r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;

    const comp = (r: number) => (v.mode === 'sqrt' ? Math.sqrt(r) : r);
    const maxR = Math.max(...model.bodies.map(b => b.el.a * (1 + b.el.e)), 1);
    const extent = comp(maxR);
    // keep the drawing above the play bar when it overlays the canvas
    const bar = barRef.current;
    const reserve = bar && getComputedStyle(bar).position === 'absolute' ? bar.offsetHeight + 18 : 0;
    const usableH = Math.max(120, h - reserve);
    const cx = w / 2, cy = usableH / 2 + 6;
    const unit = (Math.min(w, usableH * 1.35) * 0.45 / extent) * v.zoom;
    const camDist = extent * 2.8;
    const ca = Math.cos(v.az * RAD), sa = Math.sin(v.az * RAD), ce = Math.cos(v.elev * RAD), se = Math.sin(v.elev * RAD);
    const proj = (p: Vec3) => {
      const r = norm(p);
      const k = r > 0 ? comp(r) / r : 0;
      const x = p[0] * k, y = p[1] * k, z = p[2] * k;
      const x1 = x * ca - y * sa, y1 = x * sa + y * ca;
      const up = y1 * se + z * ce, d = y1 * ce - z * se;
      const s = camDist / Math.max(camDist * 0.2, camDist + d);
      return { x: cx + x1 * unit * s, y: cy - up * unit * s, d, s };
    };
    const polyline = (pts: Vec3[]) => {
      ctx.beginPath();
      pts.forEach((p, i) => { const q = proj(p); if (i) ctx.lineTo(q.x, q.y); else ctx.moveTo(q.x, q.y); });
    };

    // reference distance rings in the ecliptic plane (labelled in real AU)
    ctx.font = '500 10px Inter, "Segoe UI", sans-serif';
    for (const R of REF_RINGS) {
      if (R > maxR * 1.08) continue;
      const ring: Vec3[] = Array.from({ length: 97 }, (_, k) => [R * Math.cos((k / 96) * 2 * Math.PI), R * Math.sin((k / 96) * 2 * Math.PI), 0]);
      polyline(ring);
      ctx.strokeStyle = 'rgba(120,170,255,0.09)'; ctx.lineWidth = 1; ctx.stroke();
      const lab = proj([-R * sa, -R * ca, 0]);
      ctx.fillStyle = 'rgba(127,156,192,0.75)';
      ctx.fillText(`${R} UA`, lab.x + 4, lab.y + 12);
    }

    // orbits (osculating ellipse at the simulated date)
    for (const b of model.bodies) {
      const sel = selRef.current === b.key;
      polyline(orbitPath(bodyElements(b, jd), 160));
      ctx.strokeStyle = b.style.color;
      ctx.globalAlpha = sel ? 0.85 : 0.3;
      ctx.lineWidth = sel ? 1.8 : 1;
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // Sun
    const sun = proj([0, 0, 0]);
    const glowR = Math.min(90, 34 * Math.sqrt(v.zoom)) * sun.s;
    const g = ctx.createRadialGradient(sun.x, sun.y, 0, sun.x, sun.y, glowR);
    g.addColorStop(0, 'rgba(255,248,220,1)'); g.addColorStop(0.18, 'rgba(255,214,120,0.95)');
    g.addColorStop(0.45, 'rgba(255,170,60,0.35)'); g.addColorStop(1, 'rgba(255,140,40,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(sun.x, sun.y, glowR, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#fff6da'; ctx.beginPath(); ctx.arc(sun.x, sun.y, Math.max(4, glowR * 0.16), 0, Math.PI * 2); ctx.fill();

    // planets, far to near
    const placed = model.bodies.map(b => {
      const pos = bodyPosition(b, jd);
      return { b, pos, p: proj(pos), r: norm(pos) };
    }).sort((a, z) => z.p.d - a.p.d);
    const hits: Hit[] = [];
    const sized: { key: string; name: string; x: number; y: number; size: number; r: number; a: number }[] = [];
    for (const { b, p, r } of placed) {
      const size = b.style.size * Math.max(0.65, Math.min(1.6, p.s)) * (0.85 + 0.15 * Math.sqrt(v.zoom));
      const halo = ctx.createRadialGradient(p.x, p.y, size * 0.6, p.x, p.y, size * 3.2);
      halo.addColorStop(0, `${b.style.color}55`); halo.addColorStop(1, `${b.style.color}00`);
      ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(p.x, p.y, size * 3.2, 0, Math.PI * 2); ctx.fill();
      // lit hemisphere faces the Sun
      let dx = sun.x - p.x, dy = sun.y - p.y; const dl = Math.hypot(dx, dy) || 1; dx /= dl; dy /= dl;
      const body = ctx.createRadialGradient(p.x + dx * size * 0.45, p.y + dy * size * 0.45, size * 0.1, p.x, p.y, size * 1.05);
      body.addColorStop(0, '#ffffff'); body.addColorStop(0.3, b.style.color); body.addColorStop(1, '#060b16');
      ctx.fillStyle = body; ctx.beginPath(); ctx.arc(p.x, p.y, size, 0, Math.PI * 2); ctx.fill();
      if (b.style.ring) {
        ctx.strokeStyle = 'rgba(240,217,160,0.7)'; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.ellipse(p.x, p.y, size * 2.1, Math.max(1.5, size * 2.1 * Math.abs(Math.sin(v.elev * RAD)) * 0.55), 0, 0, Math.PI * 2); ctx.stroke();
      }
      if (selRef.current === b.key) {
        ctx.strokeStyle = '#22c8ff'; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(p.x, p.y, size + 5, 0, Math.PI * 2); ctx.stroke();
      }
      sized.push({ key: b.key, name: b.name, x: p.x, y: p.y, size, r, a: b.el.a });
      hits.push({ key: b.key, x: p.x, y: p.y, r: Math.max(12, size + 6) });
    }

    // labels: greedy placement without overlap (selected planet first, then from the Sun outwards);
    // when no slot is free the distance line is dropped, then the label (the side list keeps every value)
    const boxes: { x: number; y: number; w: number; h: number }[] = [{ x: sun.x - 10, y: sun.y - 10, w: 20, h: 20 }];
    const free = (bx: { x: number; y: number; w: number; h: number }) =>
      bx.x >= 2 && bx.x + bx.w <= w - 2 && bx.y >= 2 && !boxes.some(o => bx.x < o.x + o.w && bx.x + bx.w > o.x && bx.y < o.y + o.h && bx.y + bx.h > o.y);
    const nameFont = '600 11.5px Inter, "Segoe UI", sans-serif', valFont = '500 10px Inter, "Segoe UI", sans-serif';
    ctx.textBaseline = 'alphabetic';
    const order = [...sized].sort((a, z) => Number(z.key === selRef.current) - Number(a.key === selRef.current) || a.a - z.a);
    for (const it of order) {
      const val = `${frNum(it.r, 2)} UA`;
      ctx.font = nameFont; const nw = ctx.measureText(it.name).width;
      ctx.font = valFont; const vw = ctx.measureText(val).width;
      let done = false;
      for (const twoLines of [true, false]) {
        const bw = twoLines ? Math.max(nw, vw) : nw, bh = twoLines ? 25 : 13;
        const gap = it.size + 6;
        const slots: [number, number][] = [[gap, -bh / 2], [-gap - bw, -bh / 2], [gap, -bh - 4], [gap, 4], [-gap - bw, -bh - 4], [-gap - bw, 4], [-bw / 2, -gap - bh], [-bw / 2, gap]];
        for (const [ox, oy] of slots) {
          const bx = { x: it.x + ox, y: it.y + oy, w: bw, h: bh };
          if (!free(bx)) continue;
          boxes.push(bx);
          ctx.font = nameFont; ctx.fillStyle = '#dcebff'; ctx.fillText(it.name, bx.x, bx.y + 10);
          if (twoLines) { ctx.font = valFont; ctx.fillStyle = '#7f9cc0'; ctx.fillText(val, bx.x, bx.y + 22); }
          done = true; break;
        }
        if (done) break;
      }
    }
    hitsRef.current = hits;
  }, [model, w, h]);

  // animation loop: advance simulated time when playing, redraw when something changed
  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    let lastUi = 0;
    const tick = (now: number) => {
      const dt = Math.min(0.25, (now - last) / 1000);
      last = now;
      if (playing) {
        jdRef.current += ((reverse ? -1 : 1) * speed * dt) / 86400;
        dirtyRef.current = true;
        if (now - lastUi > 200) { lastUi = now; setDisplayJd(jdRef.current); }
      }
      if (dirtyRef.current) { dirtyRef.current = false; draw(); }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [draw, playing, speed, reverse]);

  // wheel zoom (non-passive so the page does not scroll)
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      setView(vw => ({ ...vw, zoom: Math.min(14, Math.max(0.4, vw.zoom * Math.exp(-ev.deltaY * 0.0012))) }));
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  const onPointerDown = (ev: PointerEvent<HTMLCanvasElement>) => {
    drag.current = { x: ev.clientX, y: ev.clientY, moved: false };
    ev.currentTarget.setPointerCapture(ev.pointerId);
  };
  const onPointerMove = (ev: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = ev.clientX - d.x, dy = ev.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) < 3) return;
    d.moved = true; d.x = ev.clientX; d.y = ev.clientY;
    setView(vw => ({ ...vw, az: vw.az + dx * 0.4, elev: Math.min(90, Math.max(3, vw.elev + dy * 0.3)) }));
  };
  const onPointerUp = (ev: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved) {
      const rect = ev.currentTarget.getBoundingClientRect();
      const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
      const hit = hitsRef.current.slice().reverse().find(hh => Math.hypot(hh.x - x, hh.y - y) <= hh.r);
      setSelected(hit ? (hit.key === selRef.current ? null : hit.key) : null);
    }
  };
  const onKeyDown = (ev: KeyboardEvent<HTMLCanvasElement>) => {
    const step: Record<string, Partial<View>> = {
      ArrowLeft: { az: view.az - 6 }, ArrowRight: { az: view.az + 6 },
      ArrowUp: { elev: Math.min(90, view.elev + 4) }, ArrowDown: { elev: Math.max(3, view.elev - 4) },
      '+': { zoom: Math.min(14, view.zoom * 1.2) }, '-': { zoom: Math.max(0.4, view.zoom / 1.2) },
    };
    const s = step[ev.key];
    if (s) { ev.preventDefault(); setView(vw => ({ ...vw, ...s })); }
  };

  const distances = useMemo(() => model.bodies.map(b => ({ b, r: norm(bodyPosition(b, displayJd)) })), [model, displayJd]);
  const sel = model.bodies.find(b => b.key === selected) ?? null;
  const selEl = sel ? bodyElements(sel, displayJd) : null;
  const daysFromRef = displayJd - model.refJd;
  const simYear = new Date(msFromJd(displayJd)).getUTCFullYear();
  const outOfRange = validYears !== null && (simYear < validYears[0] || simYear > validYears[1]);

  return (
    <div className="sim-solar">
      <div className="sim-stage">
        <canvas ref={canvasRef} className="sim-canvas" tabIndex={0}
          role="img" aria-label={`Système solaire au ${fmtSimDate(displayJd)} — ${model.bodies.length} planètes. Glisser pour incliner la vue, molette pour zoomer.`}
          onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={() => { drag.current = null; }}
          onKeyDown={onKeyDown} />
        <div className="sim-stage-hint"><Move3d size={14} /> Glisser : inclinaison / rotation · molette : zoom · clic : détails</div>
        <div className="sim-zoom">
          <button className="icon-btn" aria-label="Zoom avant" onClick={() => setView(vw => ({ ...vw, zoom: Math.min(14, vw.zoom * 1.3) }))}><ZoomIn size={16} /></button>
          <button className="icon-btn" aria-label="Zoom arrière" onClick={() => setView(vw => ({ ...vw, zoom: Math.max(0.4, vw.zoom / 1.3) }))}><ZoomOut size={16} /></button>
        </div>

        <div className="sim-playbar" ref={barRef} role="group" aria-label="Lecture de la simulation">
          <div className="sim-date">
            <span className="eyebrow">Date simulée</span>
            <strong aria-live="off">{fmtSimDate(displayJd)}</strong>
            <small>{Math.abs(daysFromRef) < 0.01 ? 'date de référence du calcul serveur' : `${daysFromRef > 0 ? '+' : '−'}${Math.abs(daysFromRef).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} j depuis la référence`}</small>
            {outOfRange && validYears && <small className="sim-out">Hors de la validité des éléments ({validYears[0]}–{validYears[1]}) : positions approximatives</small>}
          </div>
          <div className="sim-transport">
            <button className={`icon-btn ${reverse ? 'sim-on' : ''}`} onClick={() => setReverse(r => !r)} aria-pressed={reverse} title={reverse ? 'Sens : vers le passé' : 'Sens : vers le futur'} aria-label="Inverser le sens du temps">
              {reverse ? <Rewind size={17} /> : <FastForward size={17} />}
            </button>
            <button className="sim-play" onClick={() => setPlaying(p => !p)} aria-label={playing ? 'Pause' : 'Lecture'}>
              {playing ? <Pause size={20} /> : <Play size={20} />}
            </button>
            <button className="icon-btn" onClick={onNow} disabled={loading} title="Revenir à maintenant (nouveau calcul serveur)" aria-label="Revenir à maintenant"><CalendarClock size={17} /></button>
          </div>
          <div className="sim-speeds" role="radiogroup" aria-label="Vitesse de simulation">
            {SPEEDS.map(s => (
              <button key={s} role="radio" aria-checked={speed === s} className={speed === s ? 'active' : ''} onClick={() => setSpeed(s)}>{fmtSpeed(s)}</button>
            ))}
          </div>
          <div className="sim-speed-note">1 s réelle = {perRealSecond(speed)}</div>
          <label className="sim-jump">
            <span>Aller au</span>
            <input type="date" className="input" value={isoDay(displayJd)} disabled={loading}
              min={validYears ? `${validYears[0]}-01-01` : undefined} max={validYears ? `${validYears[1]}-12-31` : undefined}
              onChange={ev => { if (ev.target.value) { setPlaying(false); onJumpDate(`${ev.target.value}T12:00:00Z`); } }} />
          </label>
        </div>
      </div>

      <aside className="sim-aside">
        <div className="sim-scale" role="radiogroup" aria-label="Échelle des distances">
          <button role="radio" aria-checked={view.mode === 'sqrt'} className={view.mode === 'sqrt' ? 'active' : ''} onClick={() => setView(vw => ({ ...vw, mode: 'sqrt' }))}>Distances compressées (√r)</button>
          <button role="radio" aria-checked={view.mode === 'linear'} className={view.mode === 'linear' ? 'active' : ''} onClick={() => setView(vw => ({ ...vw, mode: 'linear' }))}>Distances réelles</button>
        </div>
        <p className="sim-note">{view.mode === 'sqrt'
          ? 'Directions et mouvements exacts ; distance au Soleil affichée en racine carrée pour la lisibilité. Tailles non à l’échelle.'
          : 'Distances à l’échelle (zoomez pour les planètes internes). Tailles non à l’échelle.'}</p>
        <ul className="sim-bodies">
          {distances.map(({ b, r }) => (
            <li key={b.key}>
              <button className={selected === b.key ? 'active' : ''} onClick={() => setSelected(s => (s === b.key ? null : b.key))}>
                <span className="sim-dot" style={{ background: b.style.color, boxShadow: `0 0 8px ${b.style.color}` }} />
                <span className="sim-body-name">{b.name}</span>
                <span className="sim-body-val">{frNum(r, 3)} UA</span>
              </button>
            </li>
          ))}
        </ul>
        {sel && selEl && (
          <dl className="sim-elements">
            <div><dt>Demi-grand axe</dt><dd>{frNum(selEl.a, 4)} UA</dd></div>
            <div><dt>Excentricité</dt><dd>{frNum(selEl.e, 4)}</dd></div>
            <div><dt>Inclinaison</dt><dd>{frNum(selEl.i, 3)}°</dd></div>
            <div><dt>Période (3ᵉ loi de Kepler)</dt><dd>{fmtPeriod(periodDays(selEl.a))}</dd></div>
            <div><dt>Périhélie / aphélie</dt><dd>{frNum(selEl.a * (1 - selEl.e), 3)} / {frNum(selEl.a * (1 + selEl.e), 3)} UA</dd></div>
          </dl>
        )}
      </aside>
    </div>
  );
}

