// Small hooks shared by the home page and the world page.
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from 'react';
import type { ToolResult } from '../../lib/types';

export interface ToolState {
  loading: boolean;
  result: ToolResult | null;
  /** Message to show when the request failed or the tool answered ok=false. */
  error: string;
  /** When this page received the answer (ISO). */
  fetchedAt: string | null;
  reload: () => void;
}

/**
 * Fetches a ToolResult, refreshes it periodically and keeps the last good answer while
 * reloading. `ok=false` is an error (the tool's own message is shown), never replaced by fake data.
 */
export function useToolResult(load: () => Promise<ToolResult>, key: string, { enabled = true, refreshMs = 0 } = {}): ToolState {
  const [state, setState] = useState<Omit<ToolState, 'reload'>>({ loading: enabled, result: null, error: '', fetchedAt: null });
  const loadRef = useRef(load);
  loadRef.current = load;
  const seq = useRef(0);

  const run = useCallback(() => {
    const id = ++seq.current;
    setState(s => ({ ...s, loading: true }));
    loadRef.current()
      .then(result => {
        if (id !== seq.current) return;
        setState({
          loading: false,
          result,
          error: result.ok ? '' : (result.error || result.summary || 'La source a répondu sans données.'),
          fetchedAt: new Date().toISOString(),
        });
      })
      .catch((err: unknown) => {
        if (id !== seq.current) return;
        setState({ loading: false, result: null, error: err instanceof Error ? err.message : 'Service local indisponible', fetchedAt: null });
      });
  }, []);

  useEffect(() => {
    if (!enabled) return;
    run();
    if (!refreshMs) return;
    const t = window.setInterval(() => { if (document.visibilityState === 'visible') run(); }, refreshMs);
    return () => window.clearInterval(t);
  }, [enabled, refreshMs, key, run]);

  return { ...state, reload: run };
}

/** Content-box size of an element (ResizeObserver). */
export function useElementSize<T extends HTMLElement>(ref: RefObject<T | null>): { width: number; height: number } {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      const box = entries[0]?.contentRect;
      if (box) setSize(s => (Math.abs(s.width - box.width) < 0.5 && Math.abs(s.height - box.height) < 0.5 ? s : { width: box.width, height: box.height }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

/** Live `prefers-reduced-motion` flag (shared implementation in lib/motion). */
export { useReducedMotion } from '../../lib/motion';

/** Ce qu'un moteur 3D fait des gestes (useGestes3d). Coordonnées en pixels CSS du canevas. */
export interface Gestes3d {
  tourner(dx: number, dy: number): void;
  zoom(dy: number): void;
  /** Un appui relâché sans glisser (6 pixels au plus). */
  clic(x: number, y: number): void;
  /** La souris passe, sans bouton appuyé. */
  survol?(x: number, y: number): void;
  /** Le glisser a vraiment commencé (plus de 6 pixels). */
  glisse?(): void;
}

/** Glisser pour tourner, pincer à deux doigts ou molette pour s'approcher, cliquer : le schéma des gestes du globe de
 *  Monde 3D (hologram/HologramGlobe.tsx), mis en commun le 08/11/2026 pour le laboratoire de l'accueil. Le globe garde
 *  ses propres gestes, liés à ses couches (sonde météo, actualités par pays). `actif` faux : aucun geste n'est pris. */
export function useGestes3d(canvas: RefObject<HTMLCanvasElement | null>, actif: boolean, gestes: Gestes3d) {
  const g = useRef(gestes);
  g.current = gestes;
  const pointeurs = useRef(new Map<number, { x: number; y: number }>());
  const glisse = useRef<{ x: number; y: number; bouge: number } | null>(null);
  const pince = useRef<number | null>(null);
  const ecart = () => { const [a, b] = [...pointeurs.current.values()]; return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0; };
  const local = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    const b = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - b.left, y: e.clientY - b.top };
  };
  useEffect(() => {
    const c = canvas.current;
    if (!c || !actif) return;
    const roue = (e: WheelEvent) => { e.preventDefault(); g.current.zoom(e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY); };
    c.addEventListener('wheel', roue, { passive: false });
    return () => c.removeEventListener('wheel', roue);
  }, [canvas, actif]);

  const onPointerDown = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!actif) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    pointeurs.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointeurs.current.size === 2) { pince.current = ecart(); glisse.current = null; }
    else if (pointeurs.current.size === 1) glisse.current = { x: e.clientX, y: e.clientY, bouge: 0 };
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!actif) return;
    const pt = pointeurs.current.get(e.pointerId);
    if (pt) { pt.x = e.clientX; pt.y = e.clientY; }
    if (pince.current != null && pointeurs.current.size >= 2) { const d = ecart(); g.current.zoom((pince.current - d) * 4); pince.current = d; return; }
    const gl = glisse.current;
    if (!gl) {
      if (e.pointerType === 'mouse' && g.current.survol) { const { x, y } = local(e); g.current.survol(x, y); }
      return;
    }
    const dx = e.clientX - gl.x, dy = e.clientY - gl.y;
    gl.bouge += Math.abs(dx) + Math.abs(dy); gl.x = e.clientX; gl.y = e.clientY;
    g.current.tourner(dx, dy);
    if (gl.bouge > 6) g.current.glisse?.();
  };
  const onPointerUp = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    pointeurs.current.delete(e.pointerId);
    if (pince.current != null) { if (pointeurs.current.size < 2) pince.current = null; glisse.current = null; return; }
    const gl = glisse.current;
    glisse.current = null;
    if (!actif || !gl || gl.bouge > 6 || e.type === 'pointercancel') return;
    const { x, y } = local(e);
    g.current.clic(x, y);
  };
  return { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp };
}

/** Re-render every `ms` (for « il y a … » labels and the Sun position). */
export function useNow(ms: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}
