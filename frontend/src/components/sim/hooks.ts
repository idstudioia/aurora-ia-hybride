// Small hooks shared by the canvas views (solar system, orbit) and the charts.
import { useEffect, useState, type RefObject } from 'react';

/** Live size of an element (CSS pixels), via ResizeObserver. */
export function useElementSize(ref: RefObject<HTMLElement | null>): { w: number; h: number } {
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      const r = el.getBoundingClientRect();
      setSize(s => (Math.abs(s.w - r.width) < 0.5 && Math.abs(s.h - r.height) < 0.5 ? s : { w: r.width, h: r.height }));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

/** True when the user asked the OS to reduce motion (shared implementation in lib/motion). */
export { useReducedMotion } from '../../lib/motion';

/** Deterministic pseudo-random star field (decor only), coordinates in [0, 1]. */
export function starField(count: number, seed = 7): { x: number; y: number; r: number; a: number }[] {
  let s = seed >>> 0;
  const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  return Array.from({ length: count }, () => ({ x: rnd(), y: rnd(), r: rnd() * 1.1 + 0.25, a: rnd() * 0.55 + 0.15 }));
}

/** Prepare a canvas for a given CSS size and device pixel ratio; returns the 2D context. */
export function fitCanvas(canvas: HTMLCanvasElement | null, w: number, h: number): CanvasRenderingContext2D | null {
  if (!canvas || w <= 0 || h <= 0) return null;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const W = Math.round(w * dpr), H = Math.round(h * dpr);
  if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
  const ctx = canvas.getContext('2d');
  ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}
