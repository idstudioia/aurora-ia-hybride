// AURORA "visual brain": a canvas view of the memory graph (core, four level hubs, memories,
// conversations, runs). Every animation comes from a real event passed to `play()`; the graph
// is a visualisation of the memory, not its storage.
import { useCallback, useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent, type PointerEvent, type Ref } from 'react';
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import type { ActivityEvent, MemoryGraphData, MemoryItem } from '../lib/types';
import { fmtDateTime, relativeTime } from '../lib/format';
import { BrainSim, type SimNode } from './brain/sim';
import { RingSim } from './brain/rings';
import { effectAlive, planEffects, type Effect } from './brain/effects';
import { drawScene, type Camera, type MemoryMeta } from './brain/render';
import {
  KIND_LABELS, LEVEL_BY_ID, LEVELS, NODE_TYPE_LABELS, RUN_STATUS_LABELS, classifyEvent, type LevelId,
} from './brain/model';
import { USES_LABEL, graphUsesText, toolStateLabel } from './brain/toolMap';
import './brain/brain.css';

export interface BrainHandle {
  /** Animate one real event (live SSE or replay). */
  play: (event: ActivityEvent, opts?: { speed?: number; live?: boolean }) => void;
  fit: () => void;
  focus: (nodeId: string) => void;
  clearEffects: () => void;
}

export interface BrainGraphProps {
  data: MemoryGraphData | null;
  memories: Map<number, MemoryItem>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  highlight: Map<string, number> | null;
  cutoffMs: number | null;
  statusOverride: Map<string, string> | null;
  reducedMotion: boolean;
  /** Width (CSS px) covered by an overlay on the left; fit-to-view keeps the graph clear of it. */
  insetLeft?: number;
  /** 'rings' (computed rings, brain/rings.ts) or 'lobes' (force simulation, the original view).
   *  Fixed for the life of the component: the page remounts it (key) to switch.
   *  'tools' (page Tâches, 11/11/2026): the tool map — the tools of `data.tools` alone around the core, one sector
   *  per large family of `data.tool_groups`; no memory disk, no routines ring. */
  layout?: 'rings' | 'lobes' | 'tools';
  /** Tool map: a click on a tool gives its name, a click elsewhere (or Échap) gives null. A prop of its own: the
   *  memory page does not pass it, and its click (`onSelect`, tools ignored) is unchanged. */
  onSelectTool?: (name: string | null) => void;
  /** Rings: slow rotation switch (always off with reduced motion). */
  rotate?: boolean;
  /** Vignette de l'accueil (08/11/2026) : ni zoom, ni déplacement, ni infobulle, ni étiquettes ; un clic (ou Entrée)
   *  appelle onActivate. Fixé pour la vie du composant. */
  compact?: boolean;
  onActivate?: () => void;
  ref?: Ref<BrainHandle>;
}

const MIN_K = 0.25;
const MAX_K = 4;
const MAX_EFFECTS = 500;

export default function BrainGraph(props: BrainGraphProps) {
  const { data, cutoffMs, reducedMotion, ref } = props;
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const simRef = useRef<BrainSim | null>(null);
  if (!simRef.current) simRef.current = props.layout === 'rings' ? new RingSim() : props.layout === 'tools' ? new RingSim(true) : new BrainSim();
  const effects = useRef<Effect[]>([]);
  const liveStatus = useRef(new Map<string, string>());
  const cam = useRef<Camera>({ k: 1, x: 0, y: 0 });
  const camTarget = useRef<Camera | null>(null);
  const userMoved = useRef(false);
  const fitOnData = useRef(true);
  const size = useRef({ w: 0, h: 0, dpr: 1 });
  const quality = useRef(1);                 // lowered if frames stay slow (adaptive resolution)
  const applySize = useRef<() => void>(() => undefined);
  const dirty = useRef(true);
  const hoverId = useRef<string | null>(null);
  const drag = useRef<{ x0: number; y0: number; cx: number; cy: number; moved: boolean; id: number } | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const [hover, setHover] = useState<SimNode | null>(null);

  const setCamera = useCallback((next: Camera, animate: boolean) => {
    if (animate && !propsRef.current.reducedMotion) camTarget.current = next;
    else { cam.current = next; camTarget.current = null; }
    dirty.current = true;
  }, []);

  const fitView = useCallback((animate: boolean) => {
    const { w, h } = size.current;
    if (!w || !h) return;
    const inset = Math.min(propsRef.current.insetLeft ?? 0, w * 0.3);
    const b = simRef.current!.bounds();
    const bw = Math.max(200, b.maxX - b.minX);
    const bh = Math.max(200, b.maxY - b.minY);
    // La vignette est bien plus petite que le graphe : elle descend sous le zoom minimal de la page Mémoire.
    const compact = !!propsRef.current.compact;
    const k = Math.max(compact ? 0.02 : MIN_K, Math.min(1.6, Math.min((w - inset - (compact ? 4 : 60)) / bw, (h - (compact ? 4 : 40)) / bh)));
    const cx = (b.minX + b.maxX) / 2;
    const cy = (b.minY + b.maxY) / 2;
    setCamera({ k, x: -cx * k + inset / 2, y: -cy * k }, animate);
  }, [setCamera]);

  const fitRef = useRef(fitView);
  fitRef.current = fitView;

  const focusNode = useCallback((id: string) => {
    const n = simRef.current!.resolve(id);
    if (!n) return;
    const k = Math.max(cam.current.k, 1.3);
    userMoved.current = true;
    setCamera({ k, x: -n.x * k, y: -n.y * k }, true);
  }, [setCamera]);

  useImperativeHandle(ref, () => ({
    play(event, opts) {
      const be = classifyEvent(event);
      if (!be) return;
      const p = propsRef.current;
      const sim = simRef.current!;
      const plan = planEffects(be, sim, { now: performance.now(), speed: opts?.speed ?? 1, reduced: p.reducedMotion, live: opts?.live ?? true });
      effects.current.push(...plan.effects);
      if (effects.current.length > MAX_EFFECTS) effects.current.splice(0, effects.current.length - MAX_EFFECTS);
      if (plan.status) liveStatus.current.set(plan.status.key, plan.status.status);
      dirty.current = true;
    },
    fit: () => { userMoved.current = false; fitView(true); },
    focus: focusNode,
    clearEffects: () => { effects.current = []; dirty.current = true; },
  }), [fitView, focusNode]);

  // Merge fresh API data into the simulation.
  useEffect(() => {
    if (!data) return;
    const sim = simRef.current!;
    const now = performance.now();
    const first = fitOnData.current;
    sim.sync(data, now, reducedMotion || first);
    liveStatus.current.clear();
    sim.setCutoff(propsRef.current.cutoffMs, now, reducedMotion);
    if (reducedMotion) sim.settle();
    else if (first) sim.settle(160);
    // Keep the whole brain in view while the user has not zoomed or panned.
    if (first || !userMoved.current) fitView(!first && !reducedMotion);
    fitOnData.current = false;
    dirty.current = true;
  }, [data, reducedMotion, fitView]);

  // Replay time cursor.
  useEffect(() => {
    const sim = simRef.current!;
    sim.setCutoff(cutoffMs, performance.now(), reducedMotion);
    if (reducedMotion) sim.settle();
    dirty.current = true;
  }, [cutoffMs, reducedMotion]);

  useEffect(() => { dirty.current = true; }, [props.selectedId, props.highlight, props.statusOverride, props.memories]);

  // The legend overlay opened or closed: keep the graph clear of it unless the user moved the view.
  useEffect(() => { if (!userMoved.current) fitView(true); }, [props.insetLeft, fitView]);

  // Canvas size (devicePixelRatio aware).
  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const apply = () => {
      const rect = wrap.getBoundingClientRect();
      const base = Math.min(3, window.devicePixelRatio || 1);
      const dpr = Math.max(Math.min(1, base), base * quality.current);
      const w = Math.max(1, Math.round(rect.width));
      const h = Math.max(1, Math.round(rect.height));
      size.current = { w, h, dpr };
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      if (!userMoved.current) fitView(false);
      dirty.current = true;
    };
    apply();
    applySize.current = apply;
    const ro = new ResizeObserver(apply);
    ro.observe(wrap);
    const mq = window.matchMedia?.(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    mq?.addEventListener('change', apply);
    return () => { ro.disconnect(); mq?.removeEventListener('change', apply); };
  }, [fitView]);

  // Render loop.
  useEffect(() => {
    let raf = 0;
    const statusOf = (n: SimNode) => {
      if (n.type !== 'run' || !n.key) return n.status;
      return propsRef.current.statusOverride?.get(n.key) ?? liveStatus.current.get(n.key) ?? n.status;
    };
    const metaOf = (n: SimNode): MemoryMeta | null => {
      if (n.num === null) return null;
      // Graph nodes carry pinned / verification (S6), even beyond the /api/memories list limit.
      if (typeof n.raw?.pinned === 'boolean' && typeof n.raw.verification === 'string') {
        return { pinned: n.raw.pinned, verification: n.raw.verification };
      }
      const m = propsRef.current.memories.get(n.num);
      return m ? { pinned: !!m.pinned, verification: m.verification } : null;
    };
    let last = performance.now();
    let ema = 16.7;
    let slow = 0;
    let frame = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const canvas = canvasRef.current;
      if (!canvas) return;
      const p = propsRef.current;
      const sim = simRef.current!;
      const now = performance.now();
      // Adaptive resolution: if frames stay slow for ~2 s, render fewer device pixels.
      const dt = now - last;
      last = now;
      if (dt < 100 && !p.reducedMotion) {
        ema = ema * 0.95 + dt * 0.05;
        slow = ema > 26 ? slow + 1 : 0;
        if (slow > 120 && size.current.dpr > 1 && quality.current > 0.5) {
          quality.current *= 0.75;
          slow = 0;
          applySize.current();
        }
      }
      if (sim instanceof RingSim) {
        // The rings stop turning while a node is hovered, so it stays under the pointer.
        sim.rotating = !!p.rotate && !p.reducedMotion && !hoverId.current;
        sim.instant = p.reducedMotion;
      }
      const moving = sim.tick(now);
      // While the layout settles, the camera follows it (unless the user took control).
      if (moving && !userMoved.current && ++frame % 30 === 0) fitRef.current(true);
      sim.prune(now, p.reducedMotion);
      if (effects.current.length) effects.current = effects.current.filter(e => effectAlive(e, now));
      let camMoving = false;
      const target = camTarget.current;
      if (target) {
        const c = cam.current;
        const f = 0.18;
        const next = { k: c.k + (target.k - c.k) * f, x: c.x + (target.x - c.x) * f, y: c.y + (target.y - c.y) * f };
        if (Math.abs(next.k - target.k) < 0.001 && Math.abs(next.x - target.x) < 0.5 && Math.abs(next.y - target.y) < 0.5) {
          cam.current = target; camTarget.current = null;
        } else cam.current = next;
        camMoving = true;
      }
      // With reduced motion nothing breathes: redraw only when something changed.
      if (p.reducedMotion && !moving && !camMoving && !dirty.current && effects.current.length === 0) return;
      dirty.current = false;
      const { w, h, dpr } = size.current;
      const ctx = canvas.getContext('2d');
      if (!ctx || !w || !h) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const counts: Record<LevelId, number> = { immediate: 0, persistent: 0, semantic: 0, actions: 0 };
      for (const n of sim.nodes) if (!n.fixed && !n.hidden && !n.dyingAt && n.level) counts[n.level]++;
      drawScene(ctx, {
        sim, effects: effects.current, now, w, h, cam: cam.current, selectedId: p.selectedId, hoverId: hoverId.current,
        highlight: p.highlight, statusOf, metaOf, counts, reduced: p.reducedMotion,
        rings: sim instanceof RingSim ? sim : null, compact: p.compact,
      });
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Wheel zoom around the cursor (non-passive listener to keep the page from scrolling).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || propsRef.current.compact) return;   // la vignette laisse la molette faire défiler la page
    const onWheel = (ev: WheelEvent) => {
      // Carte des outils (page Tâches) : elle est au milieu d'une page qui défile. Vu à l'essai du 11/11/2026 : la
      // molette y zoomait la carte et la page ne descendait plus. La molette seule fait donc défiler la page ; elle
      // zoome avec Ctrl enfoncé (les boutons et les touches + et − zooment toujours). La page Mémoire ne change pas.
      if (propsRef.current.layout === 'tools' && !ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      zoomAt(ev.clientX - rect.left, ev.clientY - rect.top, Math.exp(-ev.deltaY * 0.0015));
    };
    canvas.addEventListener('wheel', onWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', onWheel);
  }, []);

  function zoomAt(sx: number, sy: number, factor: number) {
    const { w, h } = size.current;
    const c = camTarget.current ?? cam.current;
    const k = Math.max(MIN_K, Math.min(MAX_K, c.k * factor));
    const wx = (sx - w / 2 - c.x) / c.k;
    const wy = (sy - h / 2 - c.y) / c.k;
    userMoved.current = true;
    camTarget.current = null;
    cam.current = { k, x: sx - w / 2 - wx * k, y: sy - h / 2 - wy * k };
    dirty.current = true;
  }

  const toWorld = (sx: number, sy: number) => {
    const { w, h } = size.current;
    const c = cam.current;
    return { x: (sx - w / 2 - c.x) / c.k, y: (sy - h / 2 - c.y) / c.k };
  };

  const pick = (ev: PointerEvent<HTMLCanvasElement>) => {
    const rect = ev.currentTarget.getBoundingClientRect();
    const sx = ev.clientX - rect.left; const sy = ev.clientY - rect.top;
    const wpt = toWorld(sx, sy);
    return { sx, sy, node: simRef.current!.hit(wpt.x, wpt.y, 6 / cam.current.k + 3, performance.now()) };
  };

  const onPointerDown = (ev: PointerEvent<HTMLCanvasElement>) => {
    if (ev.button !== 0) return;
    ev.currentTarget.setPointerCapture(ev.pointerId);
    drag.current = { x0: ev.clientX, y0: ev.clientY, cx: cam.current.x, cy: cam.current.y, moved: false, id: ev.pointerId };
  };

  const onPointerMove = (ev: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (d) {
      const dx = ev.clientX - d.x0; const dy = ev.clientY - d.y0;
      if (!d.moved && Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
      if (d.moved) {
        camTarget.current = null;
        cam.current = { ...cam.current, x: d.cx + dx, y: d.cy + dy };
        userMoved.current = true;
        dirty.current = true;
        if (hoverId.current) { hoverId.current = null; setHover(null); }
        return;
      }
    }
    const { sx, sy, node } = pick(ev);
    const id = node?.id ?? null;
    if (id !== hoverId.current) {
      hoverId.current = id;
      setHover(node);
      dirty.current = true;
    }
    ev.currentTarget.style.cursor = node ? 'pointer' : d ? 'grabbing' : 'grab';
    const tip = tipRef.current;
    if (tip && node) {
      const { w } = size.current;
      const left = sx + 16 + 260 > w ? sx - 16 - 260 : sx + 16;
      tip.style.transform = `translate(${Math.max(4, left)}px, ${Math.max(4, sy + 12)}px)`;
    }
  };

  const onPointerUp = (ev: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    drag.current = null;
    if (ev.currentTarget.hasPointerCapture(ev.pointerId)) ev.currentTarget.releasePointerCapture(ev.pointerId);
    if (!d || d.moved) return;
    const { node } = pick(ev);
    // Tool map (page Tâches): a click opens the tool's card, through its own prop.
    const onSelectTool = propsRef.current.onSelectTool;
    if (onSelectTool) { onSelectTool(node?.type === 'tool' && node.tool ? node.tool.name : null); return; }
    // Tools and routines are not memory nodes: their hover card says all there is (no detail panel).
    if (node && (node.type === 'tool' || node.type === 'routine')) return;
    propsRef.current.onSelect(node ? node.id : null);
  };

  const onPointerLeave = () => {
    if (hoverId.current) { hoverId.current = null; setHover(null); dirty.current = true; }
  };

  const onKeyDown = (ev: KeyboardEvent<HTMLCanvasElement>) => {
    const { w, h } = size.current;
    const step = 60;
    const pan = (dx: number, dy: number) => { userMoved.current = true; camTarget.current = null; cam.current = { ...cam.current, x: cam.current.x + dx, y: cam.current.y + dy }; dirty.current = true; };
    switch (ev.key) {
      case '+': case '=': zoomAt(w / 2, h / 2, 1.2); break;
      case '-': zoomAt(w / 2, h / 2, 1 / 1.2); break;
      case '0': userMoved.current = false; fitView(true); break;
      case 'ArrowLeft': pan(step, 0); break;
      case 'ArrowRight': pan(-step, 0); break;
      case 'ArrowUp': pan(0, step); break;
      case 'ArrowDown': pan(0, -step); break;
      case 'Escape': propsRef.current.onSelect(null); propsRef.current.onSelectTool?.(null); break;
      default: return;
    }
    ev.preventDefault();
  };

  const levelSummary = LEVELS.map(l => l.label).join(', ');
  const shape = props.layout === 'rings'
    ? `disque mémoire en quatre secteurs (${levelSummary}), anneau des routines, anneau des outils`
    : `cœur central et quatre niveaux (${levelSummary})`;
  const toolMap = props.layout === 'tools';
  const toolCount = data?.tools?.length ?? 0;
  const toolGroupCount = data?.tool_groups?.length ?? 0;

  if (props.compact) {
    const activer = props.onActivate;
    return (
      <div className="brain-wrap brain-compact" ref={wrapRef}>
        <canvas
          ref={canvasRef}
          className="brain-canvas"
          tabIndex={0}
          role="button"
          aria-label={`Cerveau mémoire d'AURORA (${shape}), dessiné avec les vraies données. Ouvrir la page Mémoire.`}
          title="Ouvrir la Mémoire"
          onClick={activer}
          onKeyDown={ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activer?.(); } }}
        />
      </div>
    );
  }

  return (
    <div className="brain-wrap" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        className="brain-canvas"
        tabIndex={0}
        role="img"
        aria-label={toolMap
          ? `Carte des outils d'AURORA : ${toolCount} outil${toolCount > 1 ? 's' : ''} autour du cœur, en ${toolGroupCount} grande${toolGroupCount > 1 ? 's' : ''} famille${toolGroupCount > 1 ? 's' : ''}. La liste à côté donne les mêmes outils au clavier. Ctrl + molette ou + / − pour zoomer, glisser ou flèches pour déplacer, 0 pour recentrer, Échap pour fermer la fiche.`
          : `Cerveau visuel AURORA : ${shape}. Molette ou + / − pour zoomer, glisser ou flèches pour déplacer, 0 pour recentrer, Échap pour désélectionner.`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => { drag.current = null; }}
        onPointerLeave={onPointerLeave}
        onKeyDown={onKeyDown}
      />
      <div className="brain-tooltip" ref={tipRef} hidden={!hover} role="tooltip">
        {hover && <NodeTooltip node={hover} memories={props.memories} statusOverride={props.statusOverride} liveStatus={liveStatus.current} toolMap={toolMap} />}
      </div>
      <div className="brain-zoom" role="group" aria-label="Zoom du graphe">
        <button type="button" className="icon-btn" title="Zoom avant" aria-label="Zoom avant" onClick={() => zoomAt(size.current.w / 2, size.current.h / 2, 1.25)}><ZoomIn size={16} /></button>
        <button type="button" className="icon-btn" title="Zoom arrière" aria-label="Zoom arrière" onClick={() => zoomAt(size.current.w / 2, size.current.h / 2, 0.8)}><ZoomOut size={16} /></button>
        <button type="button" className="icon-btn" title="Recentrer" aria-label="Recentrer" onClick={() => { userMoved.current = false; fitView(true); }}><Maximize2 size={16} /></button>
      </div>
    </div>
  );
}

function NodeTooltip({ node, memories, statusOverride, liveStatus, toolMap }: {
  node: SimNode; memories: Map<number, MemoryItem>; statusOverride: Map<string, string> | null; liveStatus: Map<string, string>;
  toolMap: boolean;
}) {
  if (node.type === 'core') {
    if (toolMap) return <><strong>Cœur AURORA</strong><span>Ses outils sont rangés autour, par grande famille.</span></>;
    return <><strong>Cœur AURORA</strong><span>Point de convergence des quatre niveaux de mémoire.</span></>;
  }
  // Tool map (page Tâches): the state in a word, with its reason, and the real count under its exact label.
  if (toolMap && node.type === 'tool' && node.tool) {
    const tool = node.tool;
    const reason = tool.reason ?? '';
    return (
      <>
        <strong>{node.label}</strong>
        <span className="brain-tip-meta">{NODE_TYPE_LABELS.tool}{tool.title && tool.title !== tool.name ? ` · ${tool.name}` : ''}</span>
        <span className="brain-tip-detail">{toolStateLabel(tool.state)}{reason ? ` — ${reason.length > 150 ? `${reason.slice(0, 149)}…` : reason}` : ''}</span>
        <span className="brain-tip-detail">{USES_LABEL} : {graphUsesText(tool)}</span>
        {tool.last_used_at && <span className="brain-tip-date">Dernière fois : {relativeTime(tool.last_used_at)} · {fmtDateTime(tool.last_used_at)}</span>}
        <span className="brain-tip-date">Cliquez pour ouvrir sa fiche.</span>
      </>
    );
  }
  if (node.type === 'level' && node.level) {
    const l = LEVEL_BY_ID[node.level];
    return <><strong style={{ color: l.color }}>{l.label}</strong><span>{l.description}</span></>;
  }
  // Rings: a registry tool or a saved routine, with its real use (service counts).
  if (node.type === 'tool' || node.type === 'routine') {
    const tool = node.tool;
    const routine = node.routine;
    const uses = tool ? tool.uses : routine?.runs ?? 0;
    const last = tool ? tool.last_used_at : routine?.last_run_at ?? null;
    return (
      <>
        <strong>{node.label}</strong>
        <span className="brain-tip-meta">
          {NODE_TYPE_LABELS[node.type]}
          {tool && tool.title && tool.title !== tool.name ? ` · ${tool.name}` : ''}
          {routine ? ` · ${routine.steps} étape${routine.steps > 1 ? 's' : ''}` : ''}
        </span>
        <span className="brain-tip-detail">{uses === 0 ? (tool ? 'Jamais utilisé' : 'Jamais lancée') : `${uses} ${tool ? 'utilisation' : 'lancement'}${uses > 1 ? 's' : ''}`}</span>
        {last && <span className="brain-tip-date">Dernière : {relativeTime(last)} · {fmtDateTime(last)}</span>}
      </>
    );
  }
  const memory = node.type === 'memory' && node.num !== null ? memories.get(node.num) : undefined;
  const status = node.type === 'run' && node.key ? statusOverride?.get(node.key) ?? liveStatus.get(node.key) ?? node.status : '';
  return (
    <>
      <strong>{node.label}</strong>
      <span className="brain-tip-meta">
        {NODE_TYPE_LABELS[node.type] ?? node.type}
        {node.level ? ` · ${LEVEL_BY_ID[node.level].short}` : ''}
        {node.type === 'memory' && node.category ? ` · ${node.category}` : ''}
        {node.type === 'memory' && node.kind ? ` · ${KIND_LABELS[node.kind] ?? node.kind}` : ''}
        {status ? ` · ${RUN_STATUS_LABELS[status] ?? status}` : ''}
      </span>
      {memory?.detail && <span className="brain-tip-detail">{memory.detail.length > 140 ? `${memory.detail.slice(0, 139)}…` : memory.detail}</span>}
      {node.raw?.created_at && <span className="brain-tip-date">{fmtDateTime(node.raw.created_at)}</span>}
    </>
  );
}
