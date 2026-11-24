// Reusable Earth globe: canvas 2D + d3-geo orthographic projection over Natural Earth land
// polygons bundled with `world-atlas` (nothing is loaded from the Internet). Rings, orbiting dots
// and city lights are decoration; markers carry the caller's real data.
// Respects prefers-reduced-motion (static globe), pauses when the tab is hidden or off-screen.
import { useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent, type Ref } from 'react';
import { GlobeEngine } from './globe/engine';
import { loadLand } from './globe/land';
import type { GlobeHandle, GlobeRenderProps } from './globe/types';
import './globe/globe.css';

export type { GlobeHandle, GlobeHud, GlobeMarker, GlobeRing } from './globe/types';
export type { LandDetail } from './globe/land';

export interface GlobeProps extends GlobeRenderProps {
  className?: string;
  /** Accessible description of what the globe shows. */
  ariaLabel?: string;
  ref?: Ref<GlobeHandle>;
}

export default function Globe(props: GlobeProps) {
  const { detail = '110m', interactive = false, className = '', ariaLabel, ref } = props;
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<GlobeEngine | null>(null);
  const propsRef = useRef<GlobeRenderProps>(props);
  propsRef.current = props;
  const [landState, setLandState] = useState<'loading' | 'ok' | 'error'>('loading');

  // Engine + observers, created once.
  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    let engine: GlobeEngine;
    try { engine = new GlobeEngine(canvas, () => propsRef.current); } catch { setLandState('error'); return; }
    engineRef.current = engine;

    const dpr = () => Math.min(2, window.devicePixelRatio || 1);
    const ro = new ResizeObserver(entries => {
      const box = entries[0]?.contentRect;
      if (box) engine.resize(box.width, box.height, dpr());
    });
    ro.observe(wrap);
    const io = new IntersectionObserver(entries => engine.setInView(entries.some(e => e.isIntersecting)));
    io.observe(wrap);
    const onVisibility = () => engine.setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', onVisibility);
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onMotion = () => engine.setReduced(mq.matches);
    onMotion();
    mq.addEventListener('change', onMotion);

    const down = (e: PointerEvent) => engine.pointerDown(e);
    const move = (e: PointerEvent) => engine.pointerMove(e);
    const up = (e: PointerEvent) => engine.pointerUp(e);
    const leave = () => engine.pointerLeave();
    const wheel = (e: WheelEvent) => {
      if (!propsRef.current.interactive) return;
      e.preventDefault(); // zoom the globe, not the page
      engine.wheel(e.deltaY);
    };
    canvas.addEventListener('pointerdown', down);
    canvas.addEventListener('pointermove', move);
    canvas.addEventListener('pointerup', up);
    canvas.addEventListener('pointercancel', up);
    canvas.addEventListener('pointerleave', leave);
    canvas.addEventListener('wheel', wheel, { passive: false });

    return () => {
      ro.disconnect();
      io.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
      mq.removeEventListener('change', onMotion);
      canvas.removeEventListener('pointerdown', down);
      canvas.removeEventListener('pointermove', move);
      canvas.removeEventListener('pointerup', up);
      canvas.removeEventListener('pointercancel', up);
      canvas.removeEventListener('pointerleave', leave);
      canvas.removeEventListener('wheel', wheel);
      engine.destroy();
      engineRef.current = null;
    };
  }, []);

  // Land polygons: the light 110m set first (instant), then 50m if requested.
  useEffect(() => {
    let alive = true;
    let loaded = false;
    setLandState('loading');
    const wanted = detail === '50m' ? (['110m', '50m'] as const) : (['110m'] as const);
    let pending = wanted.length;
    for (const d of wanted) {
      loadLand(d)
        .then(geo => {
          if (!alive) return;
          loaded = true;
          engineRef.current?.setLand(geo, d);
          setLandState('ok');
        })
        .catch(() => undefined)
        .finally(() => {
          pending -= 1;
          if (alive && pending === 0 && !loaded) setLandState('error');
        });
    }
    return () => { alive = false; };
  }, [detail]);

  // Any prop change (markers, layers, selection…) → redraw on the next frame.
  useEffect(() => { engineRef.current?.invalidate(); });

  useImperativeHandle(ref, () => ({
    zoomBy: f => engineRef.current?.zoomBy(f),
    resetView: () => engineRef.current?.resetView(),
    focusOn: (lat, lon) => engineRef.current?.focusOn(lat, lon),
    nudge: (dLon, dLat) => engineRef.current?.nudge(dLon, dLat),
  }), []);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const engine = engineRef.current;
    if (!interactive || !engine) return;
    const step = e.shiftKey ? 30 : 10;
    const actions: Record<string, () => void> = {
      ArrowLeft: () => engine.nudge(-step, 0),
      ArrowRight: () => engine.nudge(step, 0),
      ArrowUp: () => engine.nudge(0, step),
      ArrowDown: () => engine.nudge(0, -step),
      '+': () => engine.zoomBy(1.25),
      '=': () => engine.zoomBy(1.25),
      '-': () => engine.zoomBy(0.8),
      '0': () => engine.resetView(),
      Home: () => engine.resetView(),
    };
    const action = actions[e.key];
    if (action) { e.preventDefault(); action(); }
  };

  const label = ariaLabel ?? 'Globe terrestre';
  return (
    <div
      ref={wrapRef}
      className={`globe ${interactive ? 'globe-interactive' : ''} ${className}`}
      role={interactive ? 'application' : 'img'}
      aria-roledescription={interactive ? 'globe interactif' : undefined}
      aria-label={interactive ? `${label}. Glisser pour tourner, molette pour zoomer ; au clavier : flèches pour tourner, + et − pour zoomer, 0 pour recentrer.` : label}
      tabIndex={interactive ? 0 : undefined}
      onKeyDown={interactive ? onKeyDown : undefined}
    >
      <canvas ref={canvasRef} className="globe-canvas" aria-hidden="true" />
      {landState === 'loading' && <span className="globe-status">Chargement de la carte…</span>}
      {landState === 'error' && <span className="globe-status globe-status-error">Carte des terres indisponible</span>}
    </div>
  );
}
