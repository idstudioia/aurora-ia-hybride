// Adapté de Request Flow Replay — Copyright (c) 2026 Cliff Pyles — licence MIT, voir third_party/request-flow-replay/LICENSE.txt
// AURORA — Copyright (C) 2026 ID Studio IA — licence AGPL version 3. Ce fichier appelle GSAP, qui n'est pas libre :
// une permission additionnelle (article 7 de la licence) l'autorise, voir EXCEPTION-GSAP.md à la racine du projet.
// The task-flow figure: station columns, ribbons whose width is the number of real passages,
// one dot per real run, and a transport (live / replay, play, scrub, speed, window).
// Every mark comes from persisted events; the replay re-reads them and never calls an
// execution endpoint.
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent } from 'react';
import { gsap } from 'gsap';
import { Pause, Play, Radio, RotateCcw } from 'lucide-react';
import { STAGE_LABELS, fmtMs } from '../../lib/format';
import { useAurora } from '../../lib/store';
import { Spinner, Unavailable } from '../ui';
import { WINDOW_LABELS, flowConfig, type FlowMode, type WindowId } from './config';
import {
  STATIONS, activityWindows, buildGraph, countUpTo, eventTimes, inFlightAt, inWindow, linkKey, quantile,
  startOfToday, stationById, statusTone, visitIndexAt, visitStarts, type FlowGraph, type Journey, type StationId,
} from './model';
import { buildScene, loopPath, ribbonPath, type Scene } from './layout';
import { clearDots, createEngine, dotNear, drawDots, follow, hasDot, spawn, stepEngine, type Look } from './particles';
import type { RunData } from './useRunJourneys';

export interface ReplayRequest { at: number; seq: number }

interface Props {
  data: RunData;
  selectedRunId: number | null;
  onSelectRun: (runId: number | null) => void;
  replayRequest: ReplayRequest | null;
}

interface Bounds { from: number; to: number }

// ----------------------------------------
// Formatting
// ----------------------------------------
const DAY = 86_400_000;
const two = (n: number) => String(n).padStart(2, '0');
const clockText = (t: number, span: number, precise = false): string => {
  if (!Number.isFinite(t)) return '—';
  const d = new Date(t);
  // Paused replay: one run's events are milliseconds apart, so steps show milliseconds.
  const ms = precise ? `.${String(d.getMilliseconds()).padStart(3, '0')}` : '';
  const hms = `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}${ms}`;
  return span > DAY * 1.05 ? `${d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' })} ${hms}` : hms;
};
const axisText = (t: number, span: number): string => {
  const d = new Date(t);
  return span > DAY * 1.05 ? d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' }) : `${two(d.getHours())}:${two(d.getMinutes())}`;
};
const passages = (n: number) => `${n} passage${n > 1 ? 's' : ''}`;
const safeId = (s: string) => s.replace(/[^a-zA-Z0-9_-]/g, '');

const computeBounds = (windowId: WindowId, journeys: Map<number, Journey>, now: number): Bounds => {
  if (windowId === 'hour') return { from: now - 3_600_000, to: now };
  if (windowId === 'today') return { from: startOfToday(now), to: now };
  let min = now - 3_600_000;
  journeys.forEach(j => { if (Number.isFinite(j.createdAt) && j.createdAt < min) min = j.createdAt; });
  return { from: min - 1000, to: now };
};

const readToken = (name: string, fallback: string): string => {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
};

export default function FlowReplay({ data, selectedRunId, onSelectRun, replayRequest }: Props) {
  const uid = safeId(useId());
  const { journeys, version } = data;

  const [mode, setMode] = useState<FlowMode>('live');
  const [windowId, setWindowId] = useState<WindowId>('today');
  const [playing, setPlayingState] = useState(true);
  const [speedIdx, setSpeedIdx] = useState(0);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [reduced, setReduced] = useState(false);
  const [progress, setProgress] = useState(0);
  const [liveTick, setLiveTick] = useState(() => Date.now());
  const [replayBounds, setReplayBounds] = useState<Bounds | null>(null);

  const { chatOpen } = useAurora();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const figureRef = useRef<HTMLElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rangeRef = useRef<HTMLInputElement | null>(null);
  const axisRef = useRef<HTMLDivElement | null>(null);
  const nowLabelRef = useRef<HTMLSpanElement | null>(null);
  const hoverLabelRef = useRef<HTMLSpanElement | null>(null);
  const clockRef = useRef<HTMLElement | null>(null);
  const engine = useRef(createEngine());
  const spawned = useRef(new Set<number>());
  const liveStart = useRef(Date.now());
  const progressRef = useRef(0);
  const dragging = useRef(false);
  const lastClock = useRef('');
  const look = useRef<Look | null>(null);
  // Mutable playback state read by the GSAP ticker (and tweened by GSAP for jumps).
  const st = useRef({ mode: 'live' as FlowMode, playing: true, cursor: Date.now(), last: Date.now(), from: 0, to: 0, stepClock: 0, speed: 1, pulse: 0, reduced: false });

  // ----------------------------------------
  // Window, graph and scene
  // ----------------------------------------
  const bounds = useMemo<Bounds>(
    () => (mode === 'replay' && replayBounds ? replayBounds : computeBounds(windowId, journeys, Date.now())),
    // version / liveTick: the live window slides and grows with new runs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mode, replayBounds, windowId, journeys, version, liveTick],
  );
  const span = Math.max(1, bounds.to - bounds.from);

  const graph = useMemo(() => buildGraph(journeys.values(), bounds.from, bounds.to), [journeys, version, bounds]); // eslint-disable-line react-hooks/exhaustive-deps
  const starts = useMemo(() => visitStarts(journeys.values(), bounds.from, bounds.to), [journeys, version, bounds]); // eslint-disable-line react-hooks/exhaustive-deps
  const steps = useMemo(() => eventTimes(journeys.values(), bounds.from, bounds.to), [journeys, version, bounds]); // eslint-disable-line react-hooks/exhaustive-deps
  const runStarts = useMemo(() => [...journeys.values()].filter(j => inWindow(j, bounds.from, bounds.to) && j.visits.length > 0)
    .map(j => ({ at: j.createdAt, runId: j.runId })).sort((a, b) => a.at - b.at), [journeys, version, bounds]); // eslint-disable-line react-hooks/exhaustive-deps
  const slow = useMemo(() => activityWindows(journeys.values(), bounds.from, bounds.to, flowConfig.playback.activityPadMs, Date.now()), [journeys, version, bounds]); // eslint-disable-line react-hooks/exhaustive-deps

  const scene = useMemo<Scene | null>(() => (size.w > 0 && size.h > 0 ? buildScene(graph, size.w, size.h) : null), [graph, size]);

  // Replay progress: passages whose visit started by the cursor, on the window's fixed scale.
  const threshold = progress > 0 ? (starts[progress - 1] ?? bounds.from - 1) : bounds.from - 1;
  const progressGraph = useMemo<FlowGraph | null>(
    () => (mode === 'replay' ? buildGraph(journeys.values(), bounds.from, bounds.to, threshold) : null),
    [mode, journeys, version, bounds, threshold], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const selected = selectedRunId !== null ? journeys.get(selectedRunId) : undefined;
  const selectedLinks = useMemo(() => {
    const keys = new Set<string>();
    const stations = new Set<StationId>();
    if (selected && inWindow(selected, bounds.from, bounds.to)) {
      selected.visits.forEach((v, i) => {
        stations.add(v.stage);
        const prev = selected.visits[i - 1];
        if (prev) keys.add(linkKey(prev.stage, v.stage));
      });
    }
    return { keys, stations };
  }, [selected, bounds, version]); // eslint-disable-line react-hooks/exhaustive-deps

  // Refs the ticker reads.
  const sceneRef = useRef<Scene | null>(null);
  const stepsRef = useRef<number[]>([]);
  const startsRef = useRef<number[]>([]);
  const runStartsRef = useRef<{ at: number; runId: number }[]>([]);
  const slowRef = useRef(slow);
  useLayoutEffect(() => {
    sceneRef.current = scene;
    stepsRef.current = steps;
    startsRef.current = starts;
    runStartsRef.current = runStarts;
    slowRef.current = slow;
    st.current.from = bounds.from;
    st.current.to = bounds.to;
  });

  const lookup = useCallback((runId: number) => journeys.get(runId), [journeys]);

  // ----------------------------------------
  // Playback control
  // ----------------------------------------
  const setPlaying = useCallback((p: boolean) => {
    st.current.playing = p;
    setPlayingState(p);
  }, []);

  /** Dots for runs in flight at real time t, placed at the station they were at. */
  const refill = useCallback((t: number) => {
    clearDots(engine.current);
    if (st.current.reduced) return;
    journeys.forEach(j => {
      if (!inWindow(j, st.current.from, st.current.to) || !inFlightAt(j, t)) return;
      const idx = visitIndexAt(j, t);
      if (idx >= 0) spawn(engine.current, j.runId, idx);
    });
  }, [journeys]);

  const seek = useCallback((t: number, settle = true) => {
    const s = st.current;
    gsap.killTweensOf(s);
    s.cursor = Math.min(s.to, Math.max(s.from, t));
    s.last = s.cursor;
    if (settle) refill(s.cursor); else clearDots(engine.current);
    progressRef.current = countUpTo(startsRef.current, s.cursor);
    setProgress(progressRef.current);
  }, [refill]);

  /** GSAP scrub tween to t, then a full pipeline at t (original jumpTo). */
  const jumpTo = useCallback((t: number) => {
    const s = st.current;
    clearDots(engine.current);
    gsap.killTweensOf(s);
    const target = Math.min(s.to, Math.max(s.from, t));
    if (s.reduced) {
      seek(target);
    } else {
      gsap.to(s, {
        cursor: target, duration: flowConfig.playback.scrubSeconds, ease: flowConfig.playback.scrubEase,
        onComplete: () => { s.last = s.cursor; refill(s.cursor); },
      });
    }
  }, [refill, seek]);

  const enterReplay = useCallback((at?: number, win: WindowId = windowId) => {
    const b = computeBounds(win, journeys, Date.now());
    const s = st.current;
    s.mode = 'replay';
    s.from = b.from;
    s.to = b.to;
    setReplayBounds(b);
    setMode('replay');
    const t = at === undefined ? b.from : Math.min(b.to, Math.max(b.from, at));
    seek(t);
    setPlaying(true);
  }, [journeys, seek, setPlaying, windowId]);

  const enterLive = useCallback(() => {
    const s = st.current;
    gsap.killTweensOf(s);
    s.mode = 'live';
    setMode('live');
    setReplayBounds(null);
    clearDots(engine.current);
    spawned.current = new Set();
    const now = Date.now();
    liveStart.current = now;
    s.cursor = now;
    s.last = now;
    // Runs already in progress start where they are.
    journeys.forEach(j => {
      if (!inFlightAt(j, now)) return;
      const idx = visitIndexAt(j, now);
      if (idx >= 0 && !s.reduced) spawn(engine.current, j.runId, idx);
      spawned.current.add(j.runId);
    });
    setPlaying(true);
  }, [journeys, setPlaying]);

  const changeWindow = useCallback((w: WindowId) => {
    setWindowId(w);
    if (st.current.mode === 'replay') enterReplay(undefined, w);
  }, [enterReplay]);

  const togglePlay = useCallback(() => {
    const s = st.current;
    if (s.mode === 'replay' && !s.playing && s.cursor >= s.to - 1) seek(s.from);
    setPlaying(!s.playing);
  }, [seek, setPlaying]);

  const stepBy = useCallback((dir: 1 | -1, byRun: boolean) => {
    const s = st.current;
    if (s.mode === 'live') {
      // Stepping leaves the live view for a replay paused at the latest event.
      const b = computeBounds(windowId, journeys, Date.now());
      s.mode = 'replay';
      s.from = b.from;
      s.to = b.to;
      setReplayBounds(b);
      setMode('replay');
      s.cursor = b.to;
    }
    setPlaying(false);
    const list = byRun ? runStartsRef.current.map(r => r.at) : stepsRef.current;
    const cur = s.cursor;
    const target = dir > 0 ? list.find(t => t > cur + 0.5) : [...list].reverse().find(t => t < cur - 0.5);
    seek(target ?? cur);
  }, [journeys, seek, setPlaying, windowId]);

  // Live: the window slides.
  useEffect(() => {
    const t = window.setInterval(() => setLiveTick(Date.now()), flowConfig.live.slideEveryMs);
    return () => window.clearInterval(t);
  }, []);

  // Live: a new dot leaves Entrée when run.created arrives (journeys are rebuilt from SSE).
  useEffect(() => {
    if (mode !== 'live') return;
    const s = st.current;
    journeys.forEach(j => {
      if (spawned.current.has(j.runId) || j.visits.length === 0) return;
      spawned.current.add(j.runId);
      if (!inFlightAt(j, Date.now()) && j.endAt !== null && Date.now() - j.endAt > 10_000) return;
      // Born live: leaves Entrée. Known before the live view opened: starts where it is.
      const idx = j.createdAt >= liveStart.current - 1000 ? 0 : Math.max(0, visitIndexAt(j, Date.now()));
      if (!s.reduced && !hasDot(engine.current, j.runId)) spawn(engine.current, j.runId, idx);
    });
  }, [mode, journeys, version]);

  // First load in live mode: already-known runs are not "new".
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current || data.loading) return;
    seeded.current = true;
    if (st.current.mode === 'live') enterLive();
  }, [data.loading, enterLive]);

  useEffect(() => { follow(engine.current, selectedRunId); }, [selectedRunId]);
  useEffect(() => { st.current.speed = flowConfig.playback.speeds[speedIdx] ?? 1; }, [speedIdx]);

  // Replay requests from the page (history row, run list, inspector).
  const lastRequest = useRef(0);
  useEffect(() => {
    if (!replayRequest || replayRequest.seq === lastRequest.current) return;
    lastRequest.current = replayRequest.seq;
    const at = replayRequest.at - flowConfig.playback.leadMs;
    const win: WindowId = computeBounds(windowId, journeys, Date.now()).from <= at ? windowId : 'all';
    if (win !== windowId) setWindowId(win);
    if (st.current.mode !== 'replay') enterReplay(at, win);
    else {
      const b = computeBounds(win, journeys, Date.now());
      st.current.from = b.from;
      st.current.to = b.to;
      setReplayBounds(b);
      jumpTo(at);
      setPlaying(true);
    }
  }, [replayRequest, enterReplay, jumpTo, journeys, setPlaying, windowId]);

  // ----------------------------------------
  // Reduced motion (original: gsap.matchMedia)
  // ----------------------------------------
  useEffect(() => {
    const mm = gsap.matchMedia();
    mm.add({ reduce: '(prefers-reduced-motion: reduce)' }, ctx => {
      const r = Boolean(ctx.conditions?.reduce);
      st.current.reduced = r;
      setReduced(r);
      if (r) clearDots(engine.current);
    });
    return () => mm.revert();
  }, []);

  // ----------------------------------------
  // Size
  // ----------------------------------------
  useEffect(() => {
    const fig = figureRef.current;
    if (!fig) return;
    const resize = () => {
      const r = fig.getBoundingClientRect();
      const w = Math.round(r.width);
      const h = Math.round(r.height);
      const canvas = canvasRef.current;
      if (canvas) {
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;
        canvas.getContext('2d')?.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      setSize(prev => (prev.w === w && prev.h === h ? prev : { w, h }));
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(fig);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    look.current = {
      colors: {
        cyan: readToken('--cyan', '#22c8ff'), green: readToken('--green', '#2ee6a8'), red: readToken('--red', '#ff6b7a'),
        amber: readToken('--amber', '#ffb547'), grey: readToken('--muted-2', '#56769c'),
      },
      ring: readToken('--text-strong', '#f3f8ff'),
      radius: flowConfig.particles.radius,
      alpha: 0.95,
      followAlpha: 0.35,
    };
  }, []);

  // ----------------------------------------
  // Loop (GSAP ticker)
  // ----------------------------------------
  const placeTime = useCallback((el: HTMLElement | null, fraction: number) => {
    const axis = axisRef.current;
    if (!el || !axis) return;
    const width = axis.clientWidth;
    const w = el.offsetWidth;
    const x = Math.min(width - w, Math.max(0, fraction * width - w / 2));
    el.style.transform = `translateX(${x.toFixed(1)}px)`;
  }, []);

  const playbackRate = useCallback((cursor: number): number => {
    // Real milliseconds of the window per second of playback (original playbackSpeed).
    const s = st.current;
    const base = Math.max(1, s.to - s.from) / flowConfig.playback.windowSeconds;
    const slowRate = flowConfig.playback.activityRate * 1000;
    if (slowRate >= base) return base * s.speed;
    const ramp = base * flowConfig.playback.rampSeconds;
    const rate = slowRef.current.reduce((speed, w) => {
      const inside = Math.min(1, Math.max(0, Math.min(cursor - w.from, w.to - cursor) / ramp + 1));
      return Math.min(speed, base + (slowRate - base) * inside);
    }, base);
    return rate * s.speed;
  }, []);

  useEffect(() => {
    const tick = (_time: number, deltaMs: number) => {
      const s = st.current;
      const dt = Math.min(0.1, deltaMs / 1000);
      const now = Date.now();
      s.pulse += dt;
      if (s.mode === 'replay') {
        const tweening = gsap.isTweening(s);
        if (s.playing && !tweening && !dragging.current) {
          if (s.reduced) {
            s.stepClock += dt;
            if (s.stepClock >= flowConfig.playback.reducedStepSeconds) {
              s.stepClock = 0;
              const next = stepsRef.current.find(t => t > s.cursor + 0.5);
              s.cursor = next ?? s.to;
            }
          } else {
            s.cursor += playbackRate(s.cursor) * dt;
          }
          if (s.cursor >= s.to) {
            s.cursor = s.to;
            setPlaying(false);
          }
        }
        // Dots leave Entrée as the cursor passes each run's real start.
        if (!tweening && !s.reduced && s.cursor > s.last) {
          const list = runStartsRef.current;
          for (let i = countUpTo(list.map(r => r.at), s.last); i < list.length; i += 1) {
            const r = list[i];
            if (!r || r.at > s.cursor) break;
            spawn(engine.current, r.runId, 0);
          }
        }
        if (!tweening) s.last = s.cursor;
        const count = countUpTo(startsRef.current, s.cursor);
        if (count !== progressRef.current) { progressRef.current = count; setProgress(count); }
        const range = rangeRef.current;
        const frac = (s.cursor - s.from) / Math.max(1, s.to - s.from);
        if (range && !dragging.current) range.value = String(Math.round((s.cursor - s.from) / 100) / 10);
        placeTime(nowLabelRef.current, frac);
      } else {
        s.cursor = now;
        placeTime(nowLabelRef.current, 1);
      }
      const text = clockText(s.cursor, s.to - s.from, s.mode === 'replay' && !s.playing);
      if (text !== lastClock.current) {
        lastClock.current = text;
        if (clockRef.current) clockRef.current.textContent = text;
        if (nowLabelRef.current) nowLabelRef.current.textContent = s.mode === 'live' ? 'Direct' : text;
        rangeRef.current?.setAttribute('aria-valuetext', text);
      }
      const scn = sceneRef.current;
      const ctx = canvasRef.current?.getContext('2d');
      const lk = look.current;
      if (!scn || !ctx || !lk) return;
      if (s.reduced) {
        ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height); ctx.restore();
        return;
      }
      if (s.playing) stepEngine(engine.current, lookup, now, dt);
      drawDots(ctx, engine.current, scn, lookup, lk, s.pulse);
    };
    gsap.ticker.add(tick);
    return () => gsap.ticker.remove(tick);
  }, [lookup, placeTime, playbackRate, setPlaying]);

  // ----------------------------------------
  // Scrubber
  // ----------------------------------------
  useEffect(() => {
    const range = rangeRef.current;
    if (!range) return;
    const onInput = () => {
      const s = st.current;
      if (s.mode !== 'replay') return;
      seek(s.from + Number(range.value) * 1000, false);
    };
    const onChange = () => {
      dragging.current = false;
      const s = st.current;
      if (s.mode === 'replay') refill(s.cursor);
    };
    const onDown = () => { dragging.current = true; };
    const onUp = () => { dragging.current = false; };
    range.addEventListener('input', onInput);
    range.addEventListener('change', onChange);
    range.addEventListener('pointerdown', onDown);
    range.addEventListener('pointerup', onUp);
    return () => {
      range.removeEventListener('input', onInput);
      range.removeEventListener('change', onChange);
      range.removeEventListener('pointerdown', onDown);
      range.removeEventListener('pointerup', onUp);
    };
  }, [refill, seek]);

  const onAxisMove = (e: ReactMouseEvent<HTMLDivElement>) => {
    const el = hoverLabelRef.current;
    const axis = axisRef.current;
    if (!el || !axis || mode !== 'replay') return;
    const r = axis.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    el.hidden = false;
    el.textContent = clockText(bounds.from + frac * span, span);
    placeTime(el, frac);
  };
  const onAxisLeave = () => { if (hoverLabelRef.current) hoverLabelRef.current.hidden = true; };

  // ----------------------------------------
  // Keyboard: Space play/pause, ←/→ event steps (Shift: run steps), Escape stops following
  // ----------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.altKey || e.metaKey || e.defaultPrevented) return;
      // Figure cachée (page Tâches, vue « Carte des outils », 11/11/2026) : ses touches ne s'appliquent pas.
      if (!rootRef.current?.getClientRects().length) return;
      const target = e.target instanceof HTMLElement ? e.target : null;
      const isRange = target === rangeRef.current;
      const typing = !!target && !isRange && (target.isContentEditable || !!target.closest('input, textarea, select, [contenteditable="true"]'));
      if (typing) return;
      // Only when nothing else has the focus, or the focus is inside this figure: arrows and
      // Space keep their own meaning on tabs, lists and filters elsewhere on the page.
      const inFlow = !target || target === document.body || !!rootRef.current?.contains(target);
      if (e.key === 'Escape') {
        // The open chat drawer closes first (its own Échap); following stops on the next press.
        if (chatOpen || target?.closest('.chat-drawer')) return;
        if (selectedRunId !== null) onSelectRun(null);
        return;
      }
      if (!inFlow) return;
      if (e.key === ' ' || e.code === 'Space') {
        if (target?.closest('button, a, summary, [role="button"]')) return;
        e.preventDefault();
        togglePlay();
      } else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        stepBy(e.key === 'ArrowRight' ? 1 : -1, e.shiftKey);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onSelectRun, selectedRunId, stepBy, togglePlay, chatOpen]);

  // ----------------------------------------
  // Follow a run: click a dot
  // ----------------------------------------
  const onFigureClick = (e: ReactMouseEvent<HTMLElement>) => {
    const fig = figureRef.current;
    const scn = sceneRef.current;
    if (!fig || !scn || reduced) return;
    const r = fig.getBoundingClientRect();
    const id = dotNear(engine.current, scn, lookup, e.clientX - r.left, e.clientY - r.top, flowConfig.particles.followReach);
    if (id !== null) onSelectRun(id);
  };

  // ----------------------------------------
  // Render
  // ----------------------------------------
  const replay = mode === 'replay';
  const progressOf = (key: string) => progressGraph?.links.get(key)?.count ?? 0;
  const stationProgress = (id: StationId) => progressGraph?.stations.get(id)?.count ?? 0;
  const inFlightGraph = replay ? progressGraph : graph;
  const inFlightTotal = STATIONS.reduce((a, s) => a + (inFlightGraph?.stations.get(s.id)?.inFlight ?? 0), 0);
  const focusOn = selectedLinks.keys.size > 0;
  const linkState = (key: string) => (!focusOn ? '' : selectedLinks.keys.has(key) ? 'focus' : 'dim');

  // Nothing loaded (service unreachable): no count is known, so none is shown — not a zero.
  const unknown = !!data.error && graph.runs === 0;
  const stationValue = (id: StationId): string => {
    if (unknown) return '—';
    const stat = graph.stations.get(id);
    if (!stat) return '';
    const sorted = [...stat.durations].sort((a, b) => a - b);
    const p50 = quantile(sorted, 0.5);
    const count = replay ? `${stationProgress(id)}/${stat.count}` : String(stat.count);
    const live = inFlightGraph?.stations.get(id)?.inFlight ?? 0;
    return [count, p50 !== null ? `p50 ${fmtMs(p50)}` : '', live ? `${live} en cours` : ''].filter(Boolean).join(' · ');
  };
  const stationAria = (id: StationId): string => {
    const stat = graph.stations.get(id);
    const sorted = [...(stat?.durations ?? [])].sort((a, b) => a - b);
    const p50 = quantile(sorted, 0.5);
    const p95 = quantile(sorted, 0.95);
    return `${STAGE_LABELS[id] ?? id} : ${passages(stat?.count ?? 0)} dans la fenêtre${p50 !== null ? `, durée médiane ${fmtMs(p50)}, p95 ${fmtMs(p95)}` : ''}${stat?.inFlight ? `, ${stat.inFlight} en cours` : ''}.`;
  };

  const spark = useMemo(() => {
    const buckets = 160;
    const all = new Array<number>(buckets).fill(0);
    const errs = new Array<number>(buckets).fill(0);
    const ticks: { x: number; tone: string; runId: number }[] = [];
    journeys.forEach(j => {
      if (!inWindow(j, bounds.from, bounds.to)) return;
      const f = (j.createdAt - bounds.from) / span;
      const b = Math.min(buckets - 1, Math.max(0, Math.floor(f * buckets)));
      all[b] = (all[b] ?? 0) + 1;
      if (j.status === 'error') errs[b] = (errs[b] ?? 0) + 1;
      ticks.push({ x: f * 1000, tone: statusTone(j.status), runId: j.runId });
    });
    const max = Math.max(1, ...all);
    const area = (vals: number[]) => `M0,100${vals.map((v, i) => `L${((i + 0.5) / buckets) * 1000},${(100 - (v / max) * 88).toFixed(1)}`).join('')}L1000,100Z`;
    return { volume: area(all), errors: area(errs), ticks };
  }, [journeys, version, bounds, span]); // eslint-disable-line react-hooks/exhaustive-deps

  const axis = useMemo(() => Array.from({ length: 6 }, (_, i) => ({ frac: i / 5, text: axisText(bounds.from + (span * i) / 5, span) })), [bounds, span]);

  const totalRuns = graph.runs;
  const emptyWindow = !data.loading && !data.error && totalRuns === 0;
  const missing = graph.runsWithoutEvents;
  const summary = unknown ? 'Requêtes indisponibles.' : totalRuns === 0
    ? 'Aucune requête dans la fenêtre.'
    : `${totalRuns} requête${totalRuns > 1 ? 's' : ''} dans la fenêtre « ${WINDOW_LABELS[windowId]} », ${inFlightTotal} en cours. ${STATIONS.map(s => `${STAGE_LABELS[s.id]} ${graph.stations.get(s.id)?.count ?? 0}`).join(', ')}.`;

  const onRangeKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') e.preventDefault();
  };

  return (
    <div className="flow-root" data-mode={mode} ref={rootRef}>
      <div className="flow-head">
        <div className="flow-readout" aria-live="off">
          <span className="flow-mode-badge" data-mode={mode}>
            {replay ? <><RotateCcw size={12} /> Relecture</> : <><Radio size={12} /> En direct</>}
          </span>
          <time className="flow-clock" ref={el => { clockRef.current = el; }}>—</time>
          <span className="flow-readout-stat"><strong>{unknown ? '—' : inFlightTotal}</strong> en cours</span>
          <span className="flow-readout-stat"><strong>{unknown ? '—' : totalRuns}</strong> requête{totalRuns > 1 ? 's' : ''}</span>
          {missing > 0 && <span className="flow-readout-stat" data-muted="true" title="Leurs événements n'ont pas pu être chargés">{missing} sans événements</span>}
        </div>
        <div className="flow-switches">
          <div className="flow-seg" role="group" aria-label="Fenêtre affichée">
            {(Object.keys(WINDOW_LABELS) as WindowId[]).map(w => (
              <button key={w} type="button" aria-pressed={windowId === w} onClick={() => changeWindow(w)}>{WINDOW_LABELS[w]}</button>
            ))}
          </div>
          <div className="flow-seg" role="group" aria-label="Mode">
            <button type="button" aria-pressed={!replay} onClick={enterLive}>Direct</button>
            <button type="button" aria-pressed={replay} onClick={() => enterReplay()}>Relecture</button>
          </div>
        </div>
      </div>

      <div className="flow-scroll">
        <figure className="flow-figure" ref={el => { figureRef.current = el; }} onClick={onFigureClick} style={{ minWidth: flowConfig.layout.minWidth }}>
          {scene && (
            <svg className="flow-layer" width={scene.width} height={scene.height} viewBox={`0 0 ${scene.width} ${scene.height}`} aria-hidden="true">
              <defs>
                {[...scene.ribbons.map(r => ({ key: r.link.key, a: r.link.source, b: r.link.target, x1: r.us, x2: r.ut })),
                  ...scene.loops.map(l => ({ key: l.link.key, a: l.link.source, b: l.link.target, x1: l.points[0]?.x ?? 0, x2: l.points[l.points.length - 1]?.x ?? 1 }))]
                  .map(g => (
                    <linearGradient key={g.key} id={`${uid}-${safeId(g.key)}`} gradientUnits="userSpaceOnUse" x1={g.x1} x2={g.x2 === g.x1 ? g.x1 + 1 : g.x2} y1={0} y2={0}>
                      <stop offset="0%" style={{ stopColor: `var(${stationById.get(g.a)?.colorVar ?? '--cyan'})` }} />
                      <stop offset="100%" style={{ stopColor: `var(${stationById.get(g.b)?.colorVar ?? '--cyan'})` }} />
                    </linearGradient>
                  ))}
              </defs>
              <g className="flow-ribbons">
                {scene.ribbons.map(r => (
                  <path key={r.link.key} className="flow-ribbon" data-layer={replay ? 'base' : 'live'} data-state={linkState(r.link.key)}
                    d={r.width > 0.05 ? ribbonPath(r, scene) : ''} fill={`url(#${uid}-${safeId(r.link.key)})`}>
                    <title>{`${STAGE_LABELS[r.link.source]} → ${STAGE_LABELS[r.link.target]} : ${passages(r.link.count)}`}</title>
                  </path>
                ))}
                {replay && scene.ribbons.map(r => {
                  const w = progressOf(r.link.key) * scene.k;
                  return w > 0.05 ? <path key={`p-${r.link.key}`} className="flow-ribbon" data-layer="progress" data-state={linkState(r.link.key)} d={ribbonPath(r, scene, w)} fill={`url(#${uid}-${safeId(r.link.key)})`} /> : null;
                })}
                {scene.loops.map(l => (
                  <path key={l.link.key} className="flow-loop" data-layer={replay ? 'base' : 'live'} data-state={linkState(l.link.key)}
                    d={loopPath(l.points, l.width)} stroke={`url(#${uid}-${safeId(l.link.key)})`} strokeWidth={Math.max(1.5, l.width)}>
                    <title>{`${STAGE_LABELS[l.link.source]} → ${STAGE_LABELS[l.link.target]} (retour) : ${passages(l.link.count)}`}</title>
                  </path>
                ))}
                {replay && scene.loops.map(l => {
                  const w = progressOf(l.link.key) * scene.k;
                  return w > 0.05 ? <path key={`p-${l.link.key}`} className="flow-loop" data-layer="progress" data-state={linkState(l.link.key)} d={loopPath(l.points, l.width)} stroke={`url(#${uid}-${safeId(l.link.key)})`} strokeWidth={Math.max(1.5, w)} /> : null;
                })}
              </g>
            </svg>
          )}
          <canvas className="flow-dots" ref={el => { canvasRef.current = el; }} aria-hidden="true" />
          {scene && (
            <svg className="flow-layer flow-layer--over" width={scene.width} height={scene.height} viewBox={`0 0 ${scene.width} ${scene.height}`} role="group" aria-label="Stations du traitement. Chaque station annonce ses passages réels.">
              {scene.nodes.map(n => {
                const def = stationById.get(n.id);
                const passed = stationProgress(n.id);
                const ph = passed > 0 ? Math.max(2, passed * scene.k) : 0;
                const x = n.u1 + flowConfig.layout.labelGap;
                const active = selectedLinks.stations.has(n.id);
                return (
                  <g key={n.id} className="flow-station" data-state={focusOn ? (active ? 'focus' : 'dim') : ''} data-empty={n.count === 0} tabIndex={0} role="img" aria-label={stationAria(n.id)}>
                    <title>{stationAria(n.id)}</title>
                    <rect className="flow-station-bar" data-layer={replay ? 'base' : 'live'} x={n.u0} y={n.v0} width={n.u1 - n.u0} height={n.v1 - n.v0} rx={2} style={{ fill: `var(${def?.colorVar ?? '--cyan'})`, color: `var(${def?.colorVar ?? '--cyan'})` }} />
                    {replay && ph > 0 && <rect className="flow-station-bar" data-layer="progress" x={n.u0} y={n.v0} width={n.u1 - n.u0} height={Math.min(ph, n.v1 - n.v0)} rx={2} style={{ fill: `var(${def?.colorVar ?? '--cyan'})`, color: `var(${def?.colorVar ?? '--cyan'})` }} />}
                    <text className="flow-station-name" x={x} y={n.labelV - 3}>{STAGE_LABELS[n.id] ?? n.id}</text>
                    <text className="flow-station-value" x={x} y={n.labelV + 11}>{stationValue(n.id)}</text>
                  </g>
                );
              })}
            </svg>
          )}
          <figcaption className="flow-sr">{summary}</figcaption>
          {data.loading && <div className="flow-overlay"><Spinner size={22} /><span>Chargement des requêtes…</span></div>}
          {!data.loading && data.error && <div className="flow-overlay"><Unavailable what="Requêtes" reason={data.error} /></div>}
          {emptyWindow && (
            <div className="flow-overlay flow-overlay--soft">
              <strong>Aucune requête dans « {WINDOW_LABELS[windowId]} »</strong>
              <span>Posez une question à AURORA ou lancez le diagnostic du PC : son parcours s'animera ici, station par station.</span>
            </div>
          )}
        </figure>
      </div>

      <dl className="flow-key" aria-label="Légende">
        <div><dt><svg viewBox="0 0 28 12" aria-hidden="true"><rect className="flow-key-band" x="0" y="2" width="28" height="8" rx="2" /></svg>Largeur</dt><dd>passages réels, échelle fixe pour la fenêtre</dd></div>
        <div><dt><svg viewBox="0 0 28 12" aria-hidden="true"><circle className="flow-key-dot" cx="5" cy="6" r="2.4" /><circle className="flow-key-dot" cx="14" cy="6" r="2.4" /><circle className="flow-key-dot" cx="23" cy="6" r="2.4" /></svg>Allure</dt><dd>durée réelle de l'étape, échelle log</dd></div>
        <div><dt><svg viewBox="0 0 28 12" aria-hidden="true"><path className="flow-key-loop" d="M2 3h18a5 5 0 0 1 0 6h-18" /></svg>Boucle</dt><dd>retour à une station (autorisation, reprise)</dd></div>
        <div className="flow-key-status"><dt>Couleur</dt><dd>
          <span><i data-tone="cyan" />en cours</span><span><i data-tone="green" />terminé</span><span><i data-tone="red" />erreur</span>
          <span><i data-tone="amber" />autorisation</span><span><i data-tone="grey" />annulé</span>
        </dd></div>
      </dl>

      <div className="flow-transport">
        <button type="button" className="flow-play" onClick={togglePlay} aria-pressed={playing} aria-label={playing ? 'Pause' : 'Lecture'} title="Espace">
          {playing ? <Pause size={18} /> : <Play size={18} />}
        </button>
        <div className="flow-timeline" ref={el => { axisRef.current = el; }} onMouseMove={onAxisMove} onMouseLeave={onAxisLeave}>
          <svg className="flow-spark" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
            <path className="flow-spark-volume" d={spark.volume} />
            <path className="flow-spark-errors" d={spark.errors} />
            {spark.ticks.map(t => <line key={t.runId} className="flow-spark-tick" data-tone={t.tone} x1={t.x} x2={t.x} y1={0} y2={100} />)}
          </svg>
          <input ref={el => { rangeRef.current = el; }} className="flow-range" type="range" min={0} max={Math.round(span / 100) / 10} step="any" defaultValue={0}
            disabled={!replay} aria-label="Position de la relecture" onKeyDown={onRangeKey} />
          <ol className="flow-axis" aria-hidden="true">
            {axis.map((a, i) => <li key={i} style={{ left: `${a.frac * 100}%` }} data-edge={i === 0 ? 'start' : i === axis.length - 1 ? 'end' : 'mid'}>{a.text}</li>)}
          </ol>
          <span className="flow-time flow-time--now" ref={el => { nowLabelRef.current = el; }} aria-hidden="true" />
          <span className="flow-time flow-time--hover" ref={el => { hoverLabelRef.current = el; }} aria-hidden="true" hidden />
        </div>
        <div className="flow-seg flow-seg--speed" role="group" aria-label="Vitesse de lecture">
          {flowConfig.playback.speeds.map((sp, i) => (
            <button key={sp} type="button" aria-pressed={speedIdx === i} onClick={() => setSpeedIdx(i)} disabled={!replay}>×{sp}</button>
          ))}
        </div>
      </div>
      <p className="flow-hint">
        Espace : lecture/pause · ← → : événement précédent/suivant · Maj + ← → : requête précédente/suivante · cliquez un point pour suivre sa requête
        {reduced && ' · Mouvement réduit : la lecture avance par étapes, sans particules.'}
        {replay && ' · La relecture rejoue les événements enregistrés, elle n\'exécute rien.'}
      </p>
    </div>
  );
}

