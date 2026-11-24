// 3D view of a numerically integrated trajectory (km, body-centred) around a shaded planet.
// The path is the server's solve_ivp output; nothing here is simulated, only drawn.
import { useCallback, useEffect, useRef, type PointerEvent } from 'react';
import type { Vec3 } from './sim/kepler';
import { fitCanvas, starField, useElementSize, useReducedMotion } from './sim/hooks';

const RAD = Math.PI / 180;
const STARS = starField(110, 23);

const BODY_LOOK: Record<string, { base: string; light: string; dark: string; atmo?: string; bands?: boolean }> = {
  earth: { base: '#2f78d8', light: '#9fe0ff', dark: '#03101f', atmo: 'rgba(90,180,255,0.35)' },
  mars: { base: '#c9583a', light: '#ffb08a', dark: '#1a0703', atmo: 'rgba(255,140,100,0.15)' },
  moon: { base: '#9a9ea6', light: '#f0f1f3', dark: '#0d0f12' },
  venus: { base: '#d9b26a', light: '#fff0c4', dark: '#1d1405', atmo: 'rgba(255,220,150,0.3)' },
  jupiter: { base: '#c99d72', light: '#fde6c8', dark: '#160d05', bands: true },
};

export default function OrbitView({ points, radiusKm, bodyKey, label }: {
  points: Vec3[];
  radiusKm: number;
  bodyKey: string;
  label: string;
}) {
  const reduced = useReducedMotion();
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { w, h } = useElementSize(wrapRef);
  const az = useRef(-35);
  const elev = useRef(24);
  const progress = useRef(1);
  const drag = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => { progress.current = reduced ? 1 : 0; }, [points, reduced]);

  const draw = useCallback(() => {
    const ctx = fitCanvas(canvasRef.current, w, h);
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    for (const s of STARS) {
      ctx.globalAlpha = s.a * 0.8; ctx.fillStyle = '#cfe6ff';
      ctx.beginPath(); ctx.arc(s.x * w, s.y * h, s.r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (!points.length) return;

    const look = BODY_LOOK[bodyKey] ?? BODY_LOOK.moon;
    const maxR = Math.max(radiusKm, ...points.map(p => Math.hypot(p[0], p[1], p[2])));
    const unit = (Math.min(w, h) * 0.43) / maxR;
    const camDist = maxR * 4;
    const ca = Math.cos(az.current * RAD), sa = Math.sin(az.current * RAD);
    const ce = Math.cos(elev.current * RAD), se = Math.sin(elev.current * RAD);
    const cx = w / 2, cy = h / 2;
    const proj = (p: Vec3) => {
      const x1 = p[0] * ca - p[1] * sa, y1 = p[0] * sa + p[1] * ca;
      const up = y1 * se + p[2] * ce, d = y1 * ce - p[2] * se;
      const s = camDist / Math.max(camDist * 0.2, camDist + d);
      return { x: cx + x1 * unit * s, y: cy - up * unit * s, d };
    };
    const q = points.map(proj);
    const R = radiusKm * unit;

    const strokeSegments = (behind: boolean) => {
      ctx.lineWidth = behind ? 1.4 : 2;
      ctx.strokeStyle = behind ? 'rgba(34,200,255,0.28)' : '#22c8ff';
      ctx.shadowColor = behind ? 'transparent' : 'rgba(34,200,255,0.7)';
      ctx.shadowBlur = behind ? 0 : 6;
      ctx.beginPath();
      let open = false;
      for (let k = 1; k < q.length; k++) {
        const isBehind = (q[k - 1].d + q[k].d) / 2 > 0;
        if (isBehind !== behind) { open = false; continue; }
        if (!open) { ctx.moveTo(q[k - 1].x, q[k - 1].y); open = true; }
        ctx.lineTo(q[k].x, q[k].y);
      }
      ctx.stroke();
      ctx.shadowBlur = 0;
    };

    // probe marker along the path
    const idx = Math.min(q.length - 1, Math.floor(progress.current * (q.length - 1)));
    const probe = q[idx];
    const drawProbe = () => {
      const g = ctx.createRadialGradient(probe.x, probe.y, 0, probe.x, probe.y, 10);
      g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.35, 'rgba(46,230,168,0.9)'); g.addColorStop(1, 'rgba(46,230,168,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(probe.x, probe.y, 10, 0, Math.PI * 2); ctx.fill();
    };

    strokeSegments(true);
    if (probe.d > 0) drawProbe();

    // planet: atmosphere, lit sphere (light from upper left), optional bands
    if (look.atmo) {
      const a = ctx.createRadialGradient(cx, cy, R * 0.9, cx, cy, R * 1.35);
      a.addColorStop(0, look.atmo); a.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = a; ctx.beginPath(); ctx.arc(cx, cy, R * 1.35, 0, Math.PI * 2); ctx.fill();
    }
    const body = ctx.createRadialGradient(cx - R * 0.42, cy - R * 0.42, R * 0.05, cx, cy, R * 1.02);
    body.addColorStop(0, look.light); body.addColorStop(0.42, look.base); body.addColorStop(1, look.dark);
    ctx.fillStyle = body; ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();
    if (look.bands) {
      ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.clip();
      ctx.globalAlpha = 0.18; ctx.fillStyle = '#5a3418';
      for (const [off, th] of [[-0.55, 0.1], [-0.25, 0.14], [0.08, 0.08], [0.32, 0.12], [0.6, 0.07]]) ctx.fillRect(cx - R, cy + off * R, 2 * R, th * R);
      ctx.restore(); ctx.globalAlpha = 1;
    }
    // terminator shadow
    const shade = ctx.createRadialGradient(cx - R * 0.35, cy - R * 0.35, R * 0.6, cx, cy, R * 1.05);
    shade.addColorStop(0, 'rgba(0,0,0,0)'); shade.addColorStop(1, 'rgba(0,0,0,0.55)');
    ctx.fillStyle = shade; ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();

    strokeSegments(false);
    if (probe.d <= 0) drawProbe();

    // periapsis / apoapsis from the integrated points themselves
    let iMin = 0, iMax = 0, rMin = Infinity, rMax = -Infinity;
    points.forEach((p, k) => { const r = Math.hypot(p[0], p[1], p[2]); if (r < rMin) { rMin = r; iMin = k; } if (r > rMax) { rMax = r; iMax = k; } });
    const mark = (k: number, text: string, color: string) => {
      const m = q[k];
      ctx.fillStyle = color; ctx.beginPath(); ctx.arc(m.x, m.y, 4, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = '#030914'; ctx.lineWidth = 2; ctx.stroke();
      ctx.font = '600 11px Inter, "Segoe UI", sans-serif'; ctx.fillStyle = '#dcebff';
      ctx.fillText(text, m.x + 8, m.y - 6);
    };
    if (rMax - rMin > maxR * 0.01) { mark(iMin, 'Périapside', '#ffb547'); mark(iMax, 'Apoapside', '#8a5cff'); }
  }, [points, radiusKm, bodyKey, w, h]);

  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (!reduced) {
        if (!drag.current) az.current += dt * 6;
        progress.current = (progress.current + dt / 12) % 1;
      }
      draw();
      raf = requestAnimationFrame(tick);
    };
    if (reduced) { draw(); return; }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [draw, reduced]);

  const onDown = (ev: PointerEvent<HTMLCanvasElement>) => { drag.current = { x: ev.clientX, y: ev.clientY }; ev.currentTarget.setPointerCapture(ev.pointerId); };
  const onMove = (ev: PointerEvent<HTMLCanvasElement>) => {
    if (!drag.current) return;
    az.current += (ev.clientX - drag.current.x) * 0.4;
    elev.current = Math.min(89, Math.max(-89, elev.current + (ev.clientY - drag.current.y) * 0.3));
    drag.current = { x: ev.clientX, y: ev.clientY };
    if (reduced) draw();
  };

  return (
    <div className="sim-orbit-view" ref={wrapRef}>
      <canvas ref={canvasRef} className="sim-canvas" role="img" aria-label={label}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} />
    </div>
  );
}
