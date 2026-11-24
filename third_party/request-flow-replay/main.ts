// Intended use: entry point. Simulates the days, builds the figure from templates,
// runs the replay loop, and wires readouts, callouts, inspector, tracing, following,
// what-if levers, walkthrough, transport, keyboard, reduced motion and the ledger.

import { gsap } from 'gsap';
import { config } from './config.js';
import {
  actualLevers, clientIds, clients, events, leverIds, levers, nodes, tourIntro, tourStops,
  type DayFacts, type Levers, type NodeId, type PlatformEvent, type PlatformNode,
} from './data.js';
import {
  activeEvent, actualDay, clientMix, dayOutcome, frameAt, linkLatency, links, minutesInDay, nodeById, nodeIndex, nodeMix,
  simulateDay, type DayOutcome, type FlowLink, type Frame,
} from './model.js';
import {
  buildScene, dayBounds, nodeRect, retryPath, ribbonPath, type Bounds, type NodeBox, type Scene,
} from './layout.js';
import {
  clearDots, createEngine, dotNear, drawDots, follow, followed, freshRequest, stepEngine, warmEngine, type Look, type Span,
} from './particles.js';

// ----------------------------------------
// DOM helpers
// ----------------------------------------
const slot = <T extends Element>(name: string): T => {
  const el = document.querySelector<T>(`[data-slot="${name}"]`);
  if (!el) throw new Error(`index.html: missing [data-slot="${name}"]`);
  return el;
};

const clone = <T extends Element>(template: string, selector: string): T => {
  const tpl = document.querySelector<HTMLTemplateElement>(`#${template}`);
  const fragment = tpl?.content.cloneNode(true);
  const el = fragment instanceof DocumentFragment ? fragment.querySelector<T>(selector) : null;
  if (!el) throw new Error(`index.html: template #${template} has no ${selector}`);
  return el;
};

const part = <T extends Element>(root: Element, selector: string): T => {
  const el = root.querySelector<T>(selector);
  if (!el) throw new Error(`template part ${selector} missing`);
  return el;
};

const setAttrs = (el: Element, attrs: Record<string, string | number>): void => {
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v)));
};

// ----------------------------------------
// Tokens read from tokens.css
// ----------------------------------------
const css = getComputedStyle(document.documentElement);
const token = (name: string): string => css.getPropertyValue(name).trim();
const tokenNumber = (name: string): number => Number.parseFloat(token(name));
const clientColors = clientIds.map((id) => token(`--client-${id}`));
const look: Look = {
  colors: clientColors,
  radius: tokenNumber('--dot-radius'),
  alpha: tokenNumber('--dot-alpha'),
  dimAlpha: tokenNumber('--dot-dim-alpha'),
  followAlpha: tokenNumber('--dot-follow-alpha'),
  ring: token('--ink'),
  ringWidth: tokenNumber('--ring-width'),
};
const motion = {
  callout: tokenNumber('--dur-callout'),
  scrub: tokenNumber('--dur-scrub'),
  easeCallout: token('--ease-callout'),
  easeScrub: token('--ease-scrub'),
};

// ----------------------------------------
// Formatting, in SRE units
// ----------------------------------------
const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });
const rate = (v: number): string => integer.format(v);
const ms = (v: number): string => {
  if (v >= 1000) return `${(v / 1000).toFixed(1)} s`;
  return v < 1 ? `${v.toFixed(1)} ms` : `${integer.format(v)} ms`;
};
const percent = (v: number): string => (v * 100 < 1 ? (v * 100).toFixed(2) : (v * 100).toFixed(1));
const clock = (minute: number): string => {
  const m = Math.round(minute);
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
};
const kindLabel: Record<PlatformNode['kind'], string> = {
  client: 'Client', ingress: 'Ingress', service: 'Service', store: 'Data store', outcome: 'Response',
};
const nodeName = (id: NodeId): string => nodeById.get(id)?.label ?? id;

// ----------------------------------------
// The days: the actual one, and the one being replayed
// ----------------------------------------
const actual = simulateDay(actualDay);
const actualOutcome = dayOutcome(actual);
const withoutShedOutcome = dayOutcome(simulateDay({ levers: { ...actualLevers, manualShed: false } }));
let day = actual;
let range: Omit<Bounds, 'width' | 'height'> = dayBounds(actual);
const engine = createEngine();

const facts: DayFacts = {
  breachMinutes: String(actualOutcome.breachMinutes),
  firstBreach: clock(actualOutcome.firstBreach ?? 0),
  lastBreach: clock(actualOutcome.lastBreach ?? 0),
  withoutShedLastBreach: clock(withoutShedOutcome.lastBreach ?? 0),
  requestsPerDot: String(config.particles.requestsPerDot),
};
const eventBody = (e: PlatformEvent): string => (typeof e.body === 'string' ? e.body : e.body(facts));
const eventById = (id: string): PlatformEvent | undefined => (id === tourIntro.id ? tourIntro : events.find((e) => e.id === id));
const reduced = { matches: false };
gsap.matchMedia().add({ reduce: '(prefers-reduced-motion: reduce)' }, (context: { conditions?: Record<string, boolean> }) => {
  reduced.matches = Boolean(context.conditions?.reduce);
});

const state = {
  minute: config.playback.startMinute as number,
  playing: false,
  traced: null as number | null,
  focus: null as { kind: 'node'; id: NodeId } | { kind: 'link'; index: number } | null,
  pointer: null as { x: number; y: number } | null,
  event: null as PlatformEvent | null,
  width: 0,
  height: 0,
  stepClock: 0,
  calloutSide: 'right',
  calloutKey: '',
  levers: { ...actualLevers } as Levers,
  scene: null as Scene | null,
  traceKey: '',
  tour: null as number | null,
  tourWaiting: false,
};

const counterfactual = (): boolean => leverIds.some((id) => state.levers[id] !== actualLevers[id]);

// ----------------------------------------
// Figure: ribbons, trace lanes, retry loops, nodes
// ----------------------------------------
const figure = slot<HTMLElement>('flow');
const svg = slot<SVGSVGElement>('svg');
const overlay = slot<SVGSVGElement>('overlay');
const leader = slot<SVGLineElement>('leader');
const canvas = slot<HTMLCanvasElement>('canvas');
const ctx = canvas.getContext('2d');

const ribbonEls = links.map((link) => {
  const el = clone<SVGPathElement>('tpl-ribbon', 'path');
  const target = nodeById.get(link.target);
  setAttrs(el, { 'data-link': link.index, 'data-failure': String(Boolean(target?.failure)) });
  slot('ribbons').append(el);
  return el;
});

const traceEls = links.map(() => {
  const el = clone<SVGPathElement>('tpl-ribbon', 'path');
  slot('trace').append(el);
  return el;
});

const retryEls = clientIds.map((_, c) => {
  const el = clone<SVGPathElement>('tpl-retry', 'path');
  el.style.stroke = clientColors[c] ?? '';
  slot('retries').append(el);
  return el;
});

const nodeEls = new Map(nodes.map((n) => {
  const g = clone<SVGGElement>('tpl-node', 'g');
  setAttrs(g, {
    'data-node': n.id,
    'data-failure': String(Boolean(n.failure)),
    'data-queue': n.queue ?? '',
    'aria-label': n.label,
  });
  part(g, '.node__name').textContent = n.label;
  slot('nodes').append(g);
  return [n.id, g] as const;
}));

// ----------------------------------------
// Clients: legend entries that double as highlight toggles
// ----------------------------------------
const clientEls = clientIds.map((id, c) => {
  const button = clone<HTMLButtonElement>('tpl-legend-client', '.legend__client');
  const note = clients[id].retryNote;
  button.style.setProperty('--chip-color', clientColors[c] ?? '');
  part(button, '.legend__name').textContent = nodeById.get(id)?.short ?? id;
  button.title = `${nodeName(id)}: ${note}`;
  button.setAttribute('aria-label', `${nodeName(id)}. Retry policy: ${note}. Highlight its path.`);
  button.addEventListener('click', () => {
    state.traced = state.traced === c ? null : c;
    svg.style.setProperty('--trace-color', clientColors[c] ?? '');
  });
  slot('legend-clients').append(button);
  return button;
});

// ----------------------------------------
// Timeline: sparkline, markers, range
// ----------------------------------------
const rangeEl = slot<HTMLInputElement>('range');
const maxRps = Math.max(...actual.map((f) => f.rps));
const sparkY = (v: number): number => 100 - (v / maxRps) * 96;
const sparkPoints = (values: readonly number[]): string => values.map((v, m) => `L${m},${sparkY(v).toFixed(1)}`).join('');
const area = (values: readonly number[]): string => `M0,100${sparkPoints(values)}L${values.length - 1},100Z`;

const renderTimeline = (): void => {
  slot('spark-volume').setAttribute('d', area(day.map((f) => f.rps)));
  slot('spark-errors').setAttribute('d', area(day.map((f) => f.rps * f.errorRate)));
  slot('spark-actual').setAttribute('d', counterfactual() ? `M0,100${sparkPoints(actual.map((f) => f.rps * f.errorRate))}` : '');
};

// Every event gets a tick at its exact time. On wide timelines its label sits in the row
// above, spread apart where events crowd and tied to the tick by a leader. Narrow
// timelines keep one diamond per stretch of the day (events flagged as markers).
const markerEls = events.map((e) => {
  const button = clone<HTMLButtonElement>('tpl-marker', 'button');
  const tick = clone<SVGGElement>('tpl-marker-tick', 'g');
  button.dataset.tone = e.failure ? 'failure' : 'note';
  button.dataset.representative = String(Boolean(e.marker));
  tick.dataset.tone = button.dataset.tone;
  button.title = `${clock(e.start)} ${e.title}`;
  part(button, '.marker__label').textContent = e.label;
  part(button, '.sr-only').textContent = `Jump to ${clock(e.start)}, ${e.title}`;
  button.addEventListener('click', () => {
    endTour();
    jumpTo(Math.max(0, e.start - config.tour.leadMinutes));
  });
  button.addEventListener('pointerenter', () => { tick.dataset.hover = 'true'; });
  button.addEventListener('pointerleave', () => { tick.dataset.hover = 'false'; });
  slot('markers').append(button);
  slot('annotations').append(tick);
  return { button, tick, event: e };
});

// Places labels without overlap: colliding labels merge into a group centered on the
// mean of their events, so a crowded stretch fans out both ways. Returns null when
// even that cannot fit the width.
const spreadAlong = (wanted: readonly number[], widths: readonly number[], gap: number, width: number): number[] | null => {
  interface Group { first: number; count: number; sum: number; size: number; left: number }
  const place = (g: Omit<Group, 'left'>): Group => ({ ...g, left: Math.min(width - g.size, Math.max(0, g.sum / g.count - g.size / 2)) });
  const groups = wanted.reduce<Group[]>((stack, x, i) => {
    const merge = (acc: Group[], g: Group): Group[] => {
      const prev = acc[acc.length - 1];
      if (!prev || prev.left + prev.size + gap <= g.left) return [...acc, g];
      return merge(acc.slice(0, -1), place({
        first: prev.first, count: prev.count + g.count, sum: prev.sum + g.sum, size: prev.size + gap + g.size,
      }));
    };
    return merge(stack, place({ first: i, count: 1, sum: x, size: widths[i] ?? 0 }));
  }, []);
  if (groups.some((g) => g.size > width)) return null;
  return groups.flatMap((g) => Array.from({ length: g.count }, (_, k) => g.first + k).reduce<{ at: number; centers: number[] }>((acc, i) => {
    const w = widths[i] ?? 0;
    return { at: acc.at + w + gap, centers: [...acc.centers, acc.at + w / 2] };
  }, { at: g.left, centers: [] }).centers);
};

// ----------------------------------------
// Time axis: HH:MM ticks, the playhead's time, and the time under the pointer
// ----------------------------------------
config.timeline.axisHours.forEach((hour, i, all) => {
  const li = clone<HTMLLIElement>('tpl-axis', 'li');
  li.textContent = clock(hour * 60);
  li.style.left = `${(hour / 24) * 100}%`;
  li.dataset.edge = i === 0 ? 'start' : (i === all.length - 1 ? 'end' : 'mid');
  li.dataset.minor = String(hour % config.timeline.majorEveryHours !== 0);
  slot('axis').append(li);
});

const placeTime = (el: HTMLElement, minute: number): void => {
  const width = slot<HTMLElement>('axis').clientWidth;
  const w = el.offsetWidth;
  const x = Math.min(width - w, Math.max(0, (minute / minutesInDay) * width - w / 2));
  el.style.transform = `translateX(${x.toFixed(1)}px)`;
};

const hoverTime = slot<HTMLElement>('hover-time');
const axisLabels = [...slot('axis').querySelectorAll<HTMLElement>('.timeline__hour')];

// Axis labels step aside while the playhead's or the pointer's time covers them.
const clearAxis = (): void => {
  const covers = [slot<HTMLElement>('now'), hoverTime].filter((el) => !el.hidden).map((el) => el.getBoundingClientRect());
  axisLabels.forEach((li) => {
    const r = li.getBoundingClientRect();
    li.dataset.covered = String(covers.some((c) => c.left < r.right + config.timeline.labelGap / 2 && c.right > r.left - config.timeline.labelGap / 2));
  });
};
rangeEl.addEventListener('pointermove', (e) => {
  const r = rangeEl.getBoundingClientRect();
  const minute = Math.min(minutesInDay, Math.max(0, ((e.clientX - r.left) / r.width) * minutesInDay));
  hoverTime.hidden = false;
  hoverTime.textContent = clock(minute);
  placeTime(hoverTime, minute);
  clearAxis();
});
rangeEl.addEventListener('pointerleave', () => {
  hoverTime.hidden = true;
  clearAxis();
});

const layoutMarkers = (): void => {
  const timeline = slot<HTMLElement>('markers');
  const width = timeline.clientWidth;
  const row = timeline.clientHeight;
  const labelled = !docked.matches;
  const size = tokenNumber('--marker-size');
  const tickX = (e: PlatformEvent): number => (e.start / minutesInDay) * width;
  markerEls.forEach(({ button }) => { button.hidden = false; });
  // Measure every label as a label first, then decide whether they all fit.
  slot<HTMLElement>('markers').dataset.labels = String(labelled);
  const all = markerEls.map(({ button }) => button.offsetWidth);
  const spread = labelled ? spreadAlong(markerEls.map(({ event }) => tickX(event)), all, config.timeline.labelGap, width) : null;
  // Labels only when every one fits without overlap; otherwise one diamond per stretch.
  const labels = spread !== null;
  slot<HTMLElement>('markers').dataset.labels = String(labels);
  const shown = labels ? markerEls : markerEls.filter(({ event }) => event.marker);
  const widths = shown.map(({ button }) => button.offsetWidth);
  const centers = spread ?? shown.map(({ event }) => tickX(event));
  slot('annotations').setAttribute('viewBox', `0 0 ${width} ${row}`);
  markerEls.forEach(({ button, tick, event }) => {
    const i = shown.findIndex((s) => s.event === event);
    button.hidden = i < 0;
    tick.setAttribute('display', labels ? 'inline' : 'none');
    if (i < 0) return;
    const x = tickX(event);
    const center = centers[i] ?? x;
    button.style.transform = `translateX(${(center - (widths[i] ?? 0) / 2).toFixed(1)}px)`;
    const top = button.offsetHeight;
    const bottom = row - size * 1.3;
    const mid = (top + bottom) / 2;
    part(tick, '.marker-tick__leader').setAttribute('d', `M${center.toFixed(1)},${top} C${center.toFixed(1)},${mid.toFixed(1)} ${x.toFixed(1)},${mid.toFixed(1)} ${x.toFixed(1)},${bottom.toFixed(1)}`);
    setAttrs(part(tick, '.marker-tick__diamond'), {
      x: x - size / 2, y: row - size * 1.1, width: size, height: size, transform: `rotate(45 ${x} ${row - size * 0.6})`,
    });
  });
};

// ----------------------------------------
// Readouts
// ----------------------------------------
const hopLabel = (link: FlowLink): string => `${nodeById.get(link.source)?.short ?? link.source} → ${nodeById.get(link.target)?.short ?? link.target}`;

const slowestHop = (frame: Frame): { link: FlowLink; p50: number } | null => links
  .filter((l) => nodeById.get(l.target)?.kind !== 'outcome' && nodeById.get(l.source)?.kind !== 'client' && (frame.linkTotal[l.index] ?? 0) > 1)
  .map((link) => ({ link, p50: linkLatency(frame, link).p50 }))
  .reduce<{ link: FlowLink; p50: number } | null>((best, x) => (!best || x.p50 > best.p50 ? x : best), null);

const renderReadout = (frame: Frame): void => {
  slot('clock').textContent = clock(state.minute);
  slot('clock').setAttribute('datetime', `2026-10-22T${clock(Math.min(state.minute, minutesInDay - 1))}`);
  slot('rps').textContent = rate(frame.rps);
  slot('errors').textContent = percent(frame.errorRate);
  slot('burn').textContent = frame.burn < 10 ? frame.burn.toFixed(1) : integer.format(frame.burn);
  const breach = frame.burn > 1 ? 'breach' : 'ok';
  document.querySelectorAll('[data-stat="errors"], [data-stat="burn"]').forEach((el) => el.setAttribute('data-state', breach));
  const slow = slowestHop(frame);
  slot('slowest').textContent = slow ? `${hopLabel(slow.link)} · ${ms(slow.p50)}` : '–';
  clientEls.forEach((button, c) => {
    button.setAttribute('aria-pressed', String(state.traced === c));
    button.dataset.dimmed = String(state.traced !== null && state.traced !== c);
  });
  rangeEl.value = String(Math.round(state.minute));
  slot('now').textContent = clock(state.minute);
  placeTime(slot<HTMLElement>('now'), state.minute);
  clearAxis();
  rangeEl.setAttribute('aria-valuetext', `${clock(state.minute)}, ${rate(frame.rps)} requests per second, ${percent(frame.errorRate)}% errors`);
  markerEls.forEach(({ button, tick, event }) => {
    button.dataset.active = String(state.event?.id === event.id);
    tick.dataset.active = button.dataset.active;
  });
};

// ----------------------------------------
// Node labels and figure geometry
// ----------------------------------------
const nodeValueText = (n: PlatformNode, frame: Frame, brief = false): { text: string; alert: boolean; backlog: boolean } => {
  const i = nodeIndex.get(n.id) ?? 0;
  const throughput = frame.throughput[i] ?? 0;
  const queue = frame.queue[i] ?? 0;
  if (n.queue === 'messages' && queue > 1) return { text: `lag ${compact.format(queue)} msgs`, alert: false, backlog: true };
  if (n.queue === 'requests' && queue > 1) {
    return { text: brief ? `${rate(queue)} queued` : `queue ${rate(queue)} · ${ms(frame.waitMs[i] ?? 0)}`, alert: true, backlog: true };
  }
  const latency = reduced.matches && n.kind !== 'client' ? ` · ${ms(n.latency.p50)}` : '';
  return { text: `${rate(throughput)}${brief ? '' : ' req/s'}${latency}`, alert: Boolean(n.failure) && frame.burn > 1, backlog: false };
};

const renderNode = (box: NodeBox, scene: Scene, frame: Frame): void => {
  const g = nodeEls.get(box.id);
  const n = nodeById.get(box.id);
  if (!g || !n) return;
  g.dataset.state = !box.visible ? 'hidden' : (state.focus?.kind === 'node' && state.focus.id === box.id ? 'focus' : '');
  g.setAttribute('tabindex', box.visible ? '0' : '-1');
  g.setAttribute('aria-hidden', String(!box.visible));
  if (!box.visible) return;
  const r = nodeRect(box, scene);
  setAttrs(part(g, '.node__bar'), { x: r.x, y: r.y, width: Math.max(1, r.w), height: Math.max(1, r.h) });
  const cap = scene.toScreen(box.u0 - 3, box.v0);
  const capEnd = scene.toScreen(box.u1 + 3, box.v0 + box.capacityV);
  setAttrs(part(g, '.node__capacity'), {
    x: cap.x, y: cap.y, width: Math.max(0, capEnd.x - cap.x), height: Math.max(0, capEnd.y - cap.y), opacity: box.capacityV > 0 ? 1 : 0,
  });
  const pile = scene.toScreen(box.u0 - box.pileU, box.v0);
  const pileEnd = scene.toScreen(box.u0, box.v1);
  setAttrs(part(g, '.node__pile'), {
    x: pile.x, y: pile.y, width: Math.max(0, pileEnd.x - pile.x), height: Math.max(0, pileEnd.y - pile.y),
  });
  const last = n.column === config.layout.columns - 1;
  const name = part<SVGTextElement>(g, '.node__name');
  const value = part<SVGTextElement>(g, '.node__value');
  const reading = nodeValueText(n, frame, scene.compact);
  value.textContent = reading.text;
  g.dataset.alert = String(reading.alert);
  name.textContent = scene.compact ? n.short : n.label;
  if (scene.portrait) {
    const twoLines = reading.alert || reading.backlog;
    const u = box.u0 - (twoLines ? 20 : 7);
    if (!twoLines) value.textContent = '';
    setAttrs(name, { y: u, 'text-anchor': 'start' });
    setAttrs(value, { y: u + 12, 'text-anchor': 'start' });
  } else {
    const x = last ? box.u0 - 8 : box.u1 + 8;
    const anchor = last ? 'end' : 'start';
    setAttrs(name, { x, y: box.labelV - 2, 'text-anchor': anchor });
    setAttrs(value, { x, y: box.labelV + 11, 'text-anchor': anchor });
  }
  g.setAttribute('aria-label', `${n.label}, ${nodeValueText(n, frame).text}`);
};

// Portrait rows are narrow: each label starts at its node, nudged right only past its neighbor.
const spreadPortraitLabels = (scene: Scene): void => {
  Array.from({ length: config.layout.columns }, (_, col) => scene.nodes
    .filter((b) => b.visible && nodeById.get(b.id)?.column === col))
    .forEach((row) => row.reduce((edge, b) => {
      const g = nodeEls.get(b.id);
      if (!g) return edge;
      const name = part<SVGTextElement>(g, '.node__name');
      const value = part<SVGTextElement>(g, '.node__value');
      const x = Math.max(b.v0, edge);
      name.setAttribute('x', String(x));
      value.setAttribute('x', String(x));
      return x + Math.max(name.getComputedTextLength(), value.getComputedTextLength()) + config.layout.labelGap;
    }, 0));
};

const linkTouches = (link: FlowLink): boolean => {
  if (!state.focus) return false;
  if (state.focus.kind === 'link') return state.focus.index === link.index;
  return link.source === state.focus.id || link.target === state.focus.id;
};

const renderFigure = (scene: Scene, frame: Frame): void => {
  scene.ribbons.forEach((r) => {
    const el = ribbonEls[r.link.index];
    if (!el) return;
    el.setAttribute('d', r.width > 0.05 ? ribbonPath(r, scene) : '');
    el.dataset.state = !state.focus ? '' : (linkTouches(r.link) ? 'focus' : 'dim');
    const trace = traceEls[r.link.index];
    if (!trace) return;
    const lane = state.traced === null ? 0 : r.lanes[state.traced] ?? 0;
    const before = state.traced === null ? 0 : r.lanes.slice(0, state.traced).reduce((a, b) => a + b, 0);
    trace.setAttribute('d', lane > 0.05 ? ribbonPath({ ...r, vs: r.vs + before, vt: r.vt + before, width: lane }, scene) : '');
  });
  scene.retries.forEach((r) => {
    const el = retryEls[r.client];
    if (!el) return;
    el.setAttribute('d', r.width > 0.3 ? retryPath(r) : '');
    el.style.strokeWidth = String(r.width);
    el.style.strokeOpacity = state.traced === null || state.traced === r.client ? '' : String(look.dimAlpha);
  });
  scene.nodes.forEach((b) => renderNode(b, scene, frame));
  if (scene.portrait) spreadPortraitLabels(scene);
};

// ----------------------------------------
// Callout: the active event, beside its node
// ----------------------------------------
const callout = slot<HTMLElement>('callout');
const announcer = slot<HTMLElement>('announcer');

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

const overlap = (a: Rect, b: Rect): number => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x))
  * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));

const clampPoint = (p: { x: number; y: number }, r: Rect): { x: number; y: number } => ({
  x: Math.min(r.x + r.w, Math.max(r.x, p.x)),
  y: Math.min(r.y + r.h, Math.max(r.y, p.y)),
});

const docked = window.matchMedia('(max-width: 899px)');

// A node's drawn extent: its bar, labels and pile (not empty parts at the origin).
const nodeExtent = (g: SVGGElement): Rect => {
  const parts = ['.node__bar', '.node__name', '.node__value', '.node__pile']
    .map((sel) => g.querySelector<SVGGraphicsElement>(sel))
    .flatMap((el) => {
      if (!el) return [];
      const b = el.getBBox();
      const t = el.transform.baseVal.consolidate()?.matrix;
      return b.width + b.height > 0 ? [{ x: b.x + (t?.e ?? 0), y: b.y + (t?.f ?? 0), w: b.width, h: b.height }] : [];
    });
  const x = Math.min(...parts.map((p) => p.x));
  const y = Math.min(...parts.map((p) => p.y));
  return parts.length === 0 ? { x: 0, y: 0, w: 0, h: 0 } : {
    x, y, w: Math.max(...parts.map((p) => p.x + p.w)) - x, h: Math.max(...parts.map((p) => p.y + p.h)) - y,
  };
};

// Obstacles every floating card avoids: visible nodes with their labels, and retry lanes.
const figureObstacles = (scene: Scene): Rect[] => [
  ...[...nodeEls.values()].filter((g) => g.dataset.state !== 'hidden').map(nodeExtent),
  ...scene.retries.filter((r) => r.width > 0.3).flatMap((r) => r.points.slice(1, -1).map((p, i): Rect => {
    const q = r.points[i + 2] ?? p;
    return {
      x: Math.min(p.x, q.x) - r.width / 2, y: Math.min(p.y, q.y) - r.width / 2, w: Math.abs(q.x - p.x) + r.width, h: Math.abs(q.y - p.y) + r.width,
    };
  })),
];

// The least-covered spot for a w×h card, near an anchor when given; keeps its last spot unless clearly beaten.
const bestSpot = (w: number, h: number, near: Rect | null, obstacles: readonly Rect[], last: string): { key: string; rect: Rect } => {
  const gap = config.layout.laneGap;
  const fit = (x: number, y: number): Rect => ({
    x: Math.min(state.width - w, Math.max(0, x)), y: Math.min(state.height - h, Math.max(0, y)), w, h,
  });
  const { columns, rows } = config.layout.calloutGrid;
  const sides: Record<string, Rect> = near ? {
    right: fit(near.x + near.w + gap, near.y + near.h / 2 - h / 2),
    left: fit(near.x - gap - w, near.y + near.h / 2 - h / 2),
    below: fit(near.x + near.w / 2 - w / 2, near.y + near.h + gap),
  } : {};
  const grid = Object.fromEntries(Array.from({ length: columns * rows }, (_, i): [string, Rect] => [
    `grid${i}`,
    fit(((i % columns) / (columns - 1)) * (state.width - w), (Math.floor(i / columns) / (rows - 1)) * (state.height - h)),
  ]));
  const candidates: Record<string, Rect> = { ...sides, ...grid };
  const clear = config.layout.calloutClearance;
  const distance = (r: Rect): number => (near ? Math.hypot(r.x + w / 2 - (near.x + near.w / 2), r.y + h / 2 - (near.y + near.h / 2)) : 0);
  const score = (r: Rect): number => obstacles
    .reduce((a, o) => a + overlap({ x: r.x - clear, y: r.y - clear, w: r.w + 2 * clear, h: r.h + 2 * clear }, o), 0)
    + distance(r) * config.layout.calloutDistanceWeight;
  const best = Object.entries(candidates).sort((a, b) => score(a[1]) - score(b[1]))[0];
  const kept = candidates[last];
  const key = kept && best && score(kept) <= score(best[1]) * 1.3 + 1 ? last : best?.[0] ?? 'grid0';
  return { key, rect: candidates[key] ?? fit(0, 0) };
};

const toConsole = (r: Rect): string => `translate(${Math.round(r.x + figure.offsetLeft)}px, ${Math.round(r.y + figure.offsetTop)}px)`;

const placeCallout = (scene: Scene, obstacles: readonly Rect[]): void => {
  const event = state.event;
  nodeEls.forEach((g, id) => {
    g.dataset.anchor = String(docked.matches && event?.anchor === id);
    g.dataset.tone = event?.failure ? 'failure' : 'note';
  });
  const box = event ? scene.nodes.find((b) => b.id === event.anchor) : null;
  if (!event || !box || docked.matches) {
    leader.setAttribute('visibility', 'hidden');
    callout.style.transform = '';
    return;
  }
  const g = nodeEls.get(box.id);
  const node: Rect = g ? nodeExtent(g) : { x: 0, y: 0, w: 0, h: 0 };
  const w = callout.offsetWidth;
  const h = callout.offsetHeight;
  const spot = bestSpot(w, h, node, obstacles, state.calloutSide);
  state.calloutSide = spot.key;
  const r = spot.rect;
  callout.style.transform = toConsole(r);
  const from = clampPoint({ x: r.x + w / 2, y: r.y + h / 2 }, node);
  const to = clampPoint({ x: node.x + node.w / 2, y: node.y + node.h / 2 }, r);
  setAttrs(leader, {
    x1: from.x, y1: from.y, x2: to.x, y2: to.y, visibility: 'visible', 'data-tone': event.failure ? 'failure' : 'note',
  });
};

const showEvent = (event: PlatformEvent | null): void => {
  const next = events.find((e) => e.start > state.minute);
  const waiting = state.tour !== null && state.tourWaiting;
  const key = `${event ? event.id : `idle:${next?.id ?? 'end'}`}:${waiting ? 'tour' : ''}:${counterfactual()}`;
  if (key === state.calloutKey) return;
  const wasEvent = state.event !== null;
  const changed = event?.id !== state.event?.id;
  state.calloutKey = key;
  state.event = event;
  callout.dataset.state = event ? 'event' : 'idle';
  callout.dataset.tone = event?.failure ? 'failure' : (event ? 'note' : 'idle');
  callout.dataset.touring = String(waiting);
  slot('tour-start').setAttribute('aria-pressed', String(state.tour !== null));
  callout.dataset.counterfactual = String(counterfactual() && event !== null && event.id !== tourIntro.id);
  slot('callout-time').textContent = event ? clock(event.start) : 'Within SLO';
  slot('callout-title').textContent = event ? event.title : 'No queues, no retry loops';
  slot('callout-body').textContent = event ? eventBody(event)
    : `Next annotated moment: ${next ? `${clock(next.start)}, ${next.title.toLowerCase()}` : 'none; the day ends at 24:00'}.`;
  slot('tour-next').textContent = state.tour !== null && state.tour >= tourStops.length - 1 ? 'Finish' : 'Continue';
  if (event && changed) announcer.textContent = `${clock(event.start)}. ${event.title}. ${eventBody(event)}`;
  if (waiting && document.activeElement !== slot('tour-next')) slot<HTMLButtonElement>('tour-next').focus({ preventScroll: true });
  if ((event || wasEvent) && changed) {
    const shown = event !== null || docked.matches;
    gsap.fromTo(callout, { autoAlpha: 0 }, { autoAlpha: shown ? 1 : 0, duration: reduced.matches || !shown ? 0 : motion.callout, ease: motion.easeCallout });
  }
};

// ----------------------------------------
// Following one request: its trace as a waterfall
// ----------------------------------------
const tracePanel = slot<HTMLElement>('trace-panel');
const followButtons = [slot<HTMLButtonElement>('follow-start'), slot<HTMLButtonElement>('follow-start-rail')];
const consoleEl = slot<HTMLElement>('console');

const spanLabel = (span: Span): string => {
  const node = nodeById.get(span.node);
  if (span.kind === 'queue') return `wait · ${node?.short ?? span.node}`;
  if (span.kind === 'retry') return `retry ${span.attempt}`;
  if (span.kind === 'end') return `${node?.short ?? span.node}, retried`;
  if (node?.kind === 'outcome') return `→ ${node.short}`;
  return node?.kind === 'ingress' ? `${node.short} (network)` : node?.short ?? span.node;
};

const setFollowing = (id: number | null): void => {
  follow(engine, id);
  state.traceKey = '';
  const on = id !== null;
  tracePanel.hidden = !on;
  consoleEl.dataset.following = String(on);
  followButtons.forEach((b) => b.setAttribute('aria-pressed', String(on)));
};

const renderTrace = (): void => {
  const request = followed(engine);
  if (tracePanel.hidden) return;
  if (!request) {
    setFollowing(null);
    return;
  }
  const key = `${request.trace.length}:${request.done}`;
  if (key !== state.traceKey) {
    state.traceKey = key;
    const id = clientIds[request.client];
    const attempts = Math.max(...request.trace.map((sp) => sp.attempt));
    tracePanel.style.setProperty('--trace-color', clientColors[request.client] ?? '');
    slot('trace-title').textContent = `${id ? nodeName(id) : ''} · ${attempts === 1 ? 'first attempt' : `attempt ${attempts}`}`;
    const total = request.trace.reduce((a, sp) => a + sp.ms, 0);
    const scale = Math.max(total, config.trace.minScaleMs);
    // An end marker is only worth a row when a retry follows it; the total line names the final outcome.
    const shown = request.trace.filter((sp, i) => sp.kind !== 'end' || request.trace[i + 1]?.kind === 'retry');
    const rows = shown.reduce<{ at: number; rows: HTMLElement[] }>((acc, sp) => {
      const row = clone<HTMLElement>('tpl-span', '.span');
      row.dataset.kind = sp.kind;
      row.dataset.failure = String(Boolean(nodeById.get(sp.node)?.failure));
      row.dataset.slow = String(sp.kind === 'queue' && sp.ms > config.trace.slowWaitMs);
      part(row, '.span__label').textContent = spanLabel(sp);
      const bar = part<HTMLElement>(row, '.span__bar');
      bar.style.left = `${(acc.at / scale) * 100}%`;
      bar.style.width = `${(sp.ms / scale) * 100}%`;
      part(row, '.span__ms').textContent = sp.kind === 'end' ? '' : ms(sp.ms);
      return { at: acc.at + sp.ms, rows: [...acc.rows, row] };
    }, { at: 0, rows: [] }).rows;
    slot('trace-rows').replaceChildren(...rows);
    const last = request.trace[request.trace.length - 1];
    slot('trace-total').textContent = request.done
      ? `${ms(total)} end to end · ${last ? nodeName(last.node) : ''}`
      : `${ms(total)} so far · in flight`;
  }
};

// The waterfall lives at the top of the rail, or above the figure where the rail is a sheet.
const homeTrace = (): void => {
  const home = docked.matches ? slot('trace-dock') : slot('trace-home');
  if (tracePanel.parentElement !== home) home.append(tracePanel);
};
docked.addEventListener('change', homeTrace);

const toggleFollow = (): void => {
  if (engine.followed !== null) {
    setFollowing(null);
    return;
  }
  const id = freshRequest(engine, state.traced);
  if (id !== null) setFollowing(id);
  if (docked.matches) closeRail();
  if (!state.playing) setPlaying(true);
};
followButtons.forEach((b) => b.addEventListener('click', toggleFollow));
slot('trace-close').addEventListener('click', () => setFollowing(null));

// ----------------------------------------
// Inspector: detail on hover, tap or focus
// ----------------------------------------
const inspector = slot<HTMLElement>('inspector');

const fillInspector = (kicker: string, title: string, facts: [string, string, boolean?][], mix: number[]): void => {
  slot('inspector-kicker').textContent = kicker;
  slot('inspector-title').textContent = title;
  slot('inspector-facts').replaceChildren(...facts.map(([label, value, alert]) => {
    const fact = clone<HTMLElement>('tpl-fact', '.fact');
    part(fact, 'dt').textContent = label;
    part(fact, 'dd').textContent = value;
    fact.dataset.alert = String(Boolean(alert));
    return fact;
  }));
  slot('inspector-mix').replaceChildren(...clientIds.map((id, c) => {
    const row = clone<HTMLElement>('tpl-mix', '.mix');
    row.style.setProperty('--chip-color', clientColors[c] ?? '');
    part(row, '.mix__name').textContent = nodeById.get(id)?.short ?? id;
    part<HTMLElement>(row, '.mix__fill').style.width = `${Math.round((mix[c] ?? 0) * 100)}%`;
    part(row, '.mix__share').textContent = `${Math.round((mix[c] ?? 0) * 100)}%`;
    return row;
  }));
};

const nodeFacts = (n: PlatformNode, frame: Frame): [string, string, boolean?][] => {
  const i = nodeIndex.get(n.id) ?? 0;
  const throughput = frame.throughput[i] ?? 0;
  const queue = frame.queue[i] ?? 0;
  const wait = frame.waitMs[i] ?? 0;
  const c = clientIds.indexOf(n.id as (typeof clientIds)[number]);
  if (n.kind === 'client' && c >= 0) {
    return [
      ['Sent', `${rate(frame.base[c] ?? 0)} req/s`],
      ['Retried', `${rate(frame.retry[c] ?? 0)} req/s`, (frame.retry[c] ?? 0) > 1],
      ['Policy', clients[n.id as (typeof clientIds)[number]].retryNote],
    ];
  }
  const capacity: [string, string, boolean?][] = n.capacity ? [
    ['Capacity', `${rate(n.capacity)} ${n.queue === 'messages' ? 'msg/s' : 'req/s'}`],
    ['Utilization', `${Math.round((throughput / n.capacity) * 100)}%`, throughput > n.capacity],
  ] : [];
  const backlog: [string, string, boolean?][] = n.queue === 'messages'
    ? [['Consumer lag', `${compact.format(queue)} msgs`, queue > 1]]
    : n.queue === 'requests' ? [['Queue', `${rate(queue)} req`, queue > 1], ['Pool wait', ms(wait), wait > 1]] : [];
  const time: [string, string, boolean?][] = n.kind === 'outcome' && !n.failure ? [] : [['p50 · p99', `${ms(n.latency.p50 + wait)} · ${ms(n.latency.p99 + wait * 1.6)}`]];
  return [['Throughput', `${rate(throughput)} req/s`], ...capacity, ...backlog, ...time];
};

const renderInspector = (scene: Scene, frame: Frame): void => {
  const focus = state.focus;
  if (!focus) {
    inspector.hidden = true;
    return;
  }
  if (focus.kind === 'node') {
    const n = nodeById.get(focus.id);
    if (!n) return;
    fillInspector(kindLabel[n.kind], n.label, nodeFacts(n, frame), nodeMix(frame, n.id));
  } else {
    const link = links[focus.index];
    if (!link) return;
    const latency = linkLatency(frame, link);
    const sourceTotal = frame.throughput[nodeIndex.get(link.source) ?? 0] ?? 0;
    fillInspector('Hop', `${nodeName(link.source)} → ${nodeName(link.target)}`, [
      ['Flow', `${rate(frame.linkTotal[link.index] ?? 0)} req/s`],
      ['Share of source', `${sourceTotal > 0 ? Math.round(((frame.linkTotal[link.index] ?? 0) / sourceTotal) * 100) : 0}%`],
      ['p50 · p99', `${ms(latency.p50)} · ${ms(latency.p99)}`, latency.p50 > 1000],
    ], clientMix(frame, link));
  }
  inspector.hidden = false;
  const anchor = state.pointer ?? (() => {
    const box = focus.kind === 'node' ? scene.nodes.find((b) => b.id === focus.id) : null;
    const r = box ? nodeRect(box, scene) : { x: 0, y: 0, w: 0, h: 0 };
    return { x: r.x + r.w, y: r.y + r.h / 2 };
  })();
  const w = inspector.offsetWidth;
  const h = inspector.offsetHeight;
  const gap = config.layout.laneGap;
  const x = anchor.x + gap + w > state.width ? anchor.x - gap - w : anchor.x + gap;
  const y = Math.min(state.height - h, Math.max(0, anchor.y - h / 2));
  inspector.style.transform = `translate(${Math.round(Math.max(0, x))}px, ${Math.round(y)}px)`;
};

const pointerIn = (e: PointerEvent): { x: number; y: number } => {
  const r = figure.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
};

figure.addEventListener('pointermove', (e) => {
  const target = e.target instanceof Element ? e.target : null;
  const node = target?.closest<SVGGElement>('.node');
  const ribbon = target?.closest<SVGPathElement>('.ribbon[data-link]');
  if (node?.dataset.node) state.focus = { kind: 'node', id: node.dataset.node as NodeId };
  else if (ribbon?.dataset.link) state.focus = { kind: 'link', index: Number(ribbon.dataset.link) };
  else if (e.pointerType === 'mouse') state.focus = null;
  state.pointer = state.focus ? pointerIn(e) : null;
});

figure.addEventListener('click', (e) => {
  const target = e.target instanceof Element ? e.target : null;
  if (target?.closest('.node') || !state.scene || reduced.matches) return;
  const p = pointerIn(e);
  const id = dotNear(engine, state.scene, p.x, p.y, config.particles.followReach);
  if (id !== null) setFollowing(id);
});

figure.addEventListener('pointerleave', (e) => {
  if (e.pointerType !== 'mouse') return;
  state.focus = null;
  state.pointer = null;
});

overlay.addEventListener('focusin', (e) => {
  const node = e.target instanceof Element ? e.target.closest<SVGGElement>('.node') : null;
  if (node?.dataset.node) {
    state.focus = { kind: 'node', id: node.dataset.node as NodeId };
    state.pointer = null;
  }
});

overlay.addEventListener('focusout', () => {
  state.focus = null;
});

// ----------------------------------------
// Transport
// ----------------------------------------
const playButton = slot<HTMLButtonElement>('play');

const setPlaying = (playing: boolean): void => {
  if (playing && state.minute >= minutesInDay) state.minute = 0;
  state.playing = playing;
  playButton.setAttribute('aria-pressed', String(playing));
  slot('play-label').textContent = playing ? 'Pause replay' : 'Play replay';
};

const refill = (): void => {
  clearDots(engine);
  if (engine.followed !== null && !engine.finished) setFollowing(null);
  if (!reduced.matches) warmEngine(engine, frameAt(day, state.minute), config.particles.warmSeconds);
};

const seek = (minute: number, settle = true): void => {
  gsap.killTweensOf(state);
  state.minute = Math.min(minutesInDay, Math.max(0, minute));
  if (settle) refill();
  else clearDots(engine);
};

const jumpTo = (minute: number): void => {
  clearDots(engine);
  gsap.killTweensOf(state);
  if (reduced.matches) {
    seek(minute);
  } else {
    gsap.to(state, { minute, duration: motion.scrub, ease: motion.easeScrub, onComplete: refill });
  }
  setPlaying(true);
};

const playbackSpeed = (minute: number): number => {
  const { minutesPerSecond, slowWindows, rampMinutes } = config.playback;
  return slowWindows.reduce<number>((speed, w) => {
    const inside = Math.min(1, Math.max(0, Math.min(minute - w.from, w.to - minute) / rampMinutes + 1));
    return speed + (w.minutesPerSecond - speed) * inside;
  }, minutesPerSecond);
};

playButton.addEventListener('click', () => setPlaying(!state.playing));
rangeEl.addEventListener('input', () => {
  endTour();
  seek(Number(rangeEl.value), false);
});
rangeEl.addEventListener('change', () => refill());

const eventStarts = events.map((e) => e.start);
document.addEventListener('keydown', (e) => {
  const target = e.target instanceof HTMLElement ? e.target : null;
  const typing = target?.matches('input, button, summary, [role="button"]') ?? false;
  const step = e.shiftKey ? config.keyboard.bigStepMinutes : config.keyboard.stepMinutes;
  if (e.key === 'Escape') {
    state.focus = null;
    if (target?.closest('.node')) target.blur();
    if (railOpen()) {
      closeRail();
    } else if (!tracePanel.hidden) {
      setFollowing(null);
    }
    return;
  }
  if (e.key === ' ' && !typing) {
    e.preventDefault();
    setPlaying(!state.playing);
  } else if (e.key === 'ArrowRight' && target !== rangeEl) {
    seek(state.minute + step);
  } else if (e.key === 'ArrowLeft' && target !== rangeEl) {
    seek(state.minute - step);
  } else if (e.key === ']') {
    const next = eventStarts.find((s) => s > state.minute + 0.5);
    if (next !== undefined) jumpTo(next);
  } else if (e.key === '[') {
    const prev = [...eventStarts].reverse().find((s) => s < state.minute - 0.5);
    if (prev !== undefined) jumpTo(prev);
  }
});

// ----------------------------------------
// Ledger: the same day as an hourly table
// ----------------------------------------
const ledger = slot<HTMLDialogElement>('ledger');
const mean = (xs: readonly number[]): number => xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);
const orDash = (v: number, format: (x: number) => string): string => (v < 1 ? '–' : format(v));
const primaryIndex = nodeIndex.get('primary') ?? 0;
const kafkaIndex = nodeIndex.get('kafka') ?? 0;

const renderLedger = (): void => {
  const hours = Array.from({ length: 24 }, (_, h) => day.slice(h * 60, h * 60 + 60));
  slot('ledger-rows').replaceChildren(...hours.map((frames, h) => {
    const row = clone<HTMLTableRowElement>('tpl-row', 'tr');
    const rps = mean(frames.map((f) => f.rps));
    const errors = mean(frames.map((f) => f.rps * f.errorRate)) / Math.max(1, rps);
    const cells = [
      `${String(h).padStart(2, '0')}:00`,
      rate(rps),
      `${percent(errors)}%`,
      `${(errors / 0.001).toFixed(1)}×`,
      orDash(Math.max(...frames.map((f) => f.waitMs[primaryIndex] ?? 0)), ms),
      orDash(Math.max(...frames.map((f) => f.queue[kafkaIndex] ?? 0)), (v) => compact.format(v)),
      `${rate(mean(frames.map((f) => f.retry.reduce((a, b) => a + b, 0))))} req/s`,
    ];
    Array.from(row.children).forEach((cell, i) => { cell.textContent = cells[i] ?? ''; });
    row.dataset.alert = String(errors > 0.001);
    return row;
  }));
  const peak = day.reduce((a, f) => (f.rps > a.rps ? f : a));
  const outcome = dayOutcome(day);
  const worst = day.reduce((a, f) => (f.errorRate > a.errorRate ? f : a));
  const lag = day.reduce((a, f) => ((f.queue[kafkaIndex] ?? 0) > (a.queue[kafkaIndex] ?? 0) ? f : a));
  const lead = counterfactual() ? 'In this replay, errors' : 'Errors';
  const incident = outcome.firstBreach !== null && outcome.lastBreach !== null
    ? ` ${lead} broke the 99.9% SLO from ${clock(outcome.firstBreach)} to ${clock(outcome.lastBreach)}, peaking at ${percent(worst.errorRate)}% at ${clock(worst.minute)}.`
    : ` ${lead} never broke the 99.9% SLO.`;
  const cause = counterfactual() ? '' : ' A catalog deploy emptied Redis, the Postgres primary saturated, and client retries kept it saturated until on-call shed load.';
  const summary = `Traffic peaked at ${rate(peak.rps)} req/s at ${clock(peak.minute)}.${incident}${cause} Kafka consumer lag peaked at ${compact.format(lag.queue[kafkaIndex] ?? 0)} messages at ${clock(lag.minute)} during the nightly ledger export, with no errors. Availability for the day: ${(100 * outcome.availability).toFixed(2)}%.`;
  slot('ledger-summary').textContent = summary;
  slot('summary').textContent = `Animated sankey replay of one day of production traffic. ${summary} The hourly ledger has the same data as a table.`;
};

slot('open-ledger').addEventListener('click', () => ledger.showModal());
slot('open-ledger-rail').addEventListener('click', () => {
  closeRail();
  ledger.showModal();
});
slot('close-ledger').addEventListener('click', () => ledger.close());
ledger.addEventListener('click', (e) => { if (e.target === ledger) ledger.close(); });

// ----------------------------------------
// Rail: a column on wide screens, a bottom sheet on narrow ones
// ----------------------------------------
const rail = slot<HTMLElement>('rail');
const railButton = slot<HTMLButtonElement>('rail-open');
const scrim = slot<HTMLElement>('rail-scrim');

const railOpen = (): boolean => docked.matches && rail.dataset.open === 'true';

const closeRail = (): void => {
  if (!railOpen()) return;
  rail.dataset.open = 'false';
  scrim.hidden = true;
  railButton.setAttribute('aria-expanded', 'false');
  railButton.focus({ preventScroll: true });
};

// Opens the sheet (narrow) or brings a section into view (wide), and moves focus to it.
const openRail = (sectionId?: string): void => {
  const section = sectionId ? document.getElementById(sectionId) : null;
  if (docked.matches) {
    rail.dataset.open = 'true';
    scrim.hidden = false;
    railButton.setAttribute('aria-expanded', 'true');
  }
  const target = section?.querySelector<HTMLElement>('h3') ?? slot<HTMLElement>('rail-close');
  target.setAttribute('tabindex', '-1');
  section?.scrollIntoView({ block: 'start', behavior: reduced.matches ? 'auto' : 'smooth' });
  target.focus({ preventScroll: Boolean(section) });
};

railButton.addEventListener('click', () => (railOpen() ? closeRail() : openRail()));
slot('rail-close').addEventListener('click', closeRail);
scrim.addEventListener('click', closeRail);
docked.addEventListener('change', () => {
  rail.dataset.open = 'false';
  scrim.hidden = true;
});

// ----------------------------------------
// What if: replay the day with different levers
// ----------------------------------------
const changedList = new Intl.ListFormat('en', { type: 'conjunction' });

const outcomeLine = (o: DayOutcome): string => (o.breachMinutes === 0
  ? 'never above SLO'
  : `${o.breachMinutes} min above SLO, ${clock(o.firstBreach ?? 0)}–${clock(o.lastBreach ?? 0)}`);

const renderWhatif = (): void => {
  const outcome = dayOutcome(day);
  const result = slot<HTMLElement>('whatif-result');
  result.textContent = counterfactual()
    ? `This replay: ${outcomeLine(outcome)}, ${(outcome.availability * 100).toFixed(2)}% available. Actual day: ${outcomeLine(actualOutcome)}, ${(actualOutcome.availability * 100).toFixed(2)}%.`
    : `The actual day: ${outcomeLine(actualOutcome)}, ${(actualOutcome.availability * 100).toFixed(2)}% available.`;
  result.dataset.state = outcome.breachMinutes > actualOutcome.breachMinutes ? 'worse' : 'better';
  slot<HTMLButtonElement>('whatif-reset').disabled = !counterfactual();
  const changed = leverIds.filter((id) => state.levers[id] !== actualLevers[id]).map((id) => levers[id].changed);
  const comparison = `${outcomeLine(outcome)}, against ${actualOutcome.breachMinutes} min on the actual day`;
  slot<HTMLElement>('mode-chip').hidden = !counterfactual();
  slot('mode-summary').textContent = changed.length === 1 ? changed[0] ?? '' : `${changed.length} changes`;
  slot('mode-open').setAttribute('aria-label', `What-if replay: ${changedList.format(changed)}. ${comparison}. Open the what-if controls.`);
  slot<HTMLElement>('mode-chip').title = `${changedList.format(changed)}: ${comparison}`;
  railButton.dataset.mode = counterfactual() ? 'whatif' : 'actual';
  slot('controls-label').textContent = counterfactual() ? 'What-if' : 'Controls';
  leverInputs.forEach((input, id) => { input.checked = state.levers[id]; });
};

const applyDay = (): void => {
  day = counterfactual() ? simulateDay({ levers: state.levers }) : actual;
  const bounds = dayBounds(day);
  const actualBounds = dayBounds(actual);
  range = { maxColumn: Math.max(bounds.maxColumn, actualBounds.maxColumn), maxRetry: Math.max(bounds.maxRetry, actualBounds.maxRetry) };
  state.calloutKey = '';
  renderTimeline();
  renderLedger();
  renderWhatif();
  refill();
};

const leverInputs = new Map(leverIds.map((id) => {
  const label = clone<HTMLLabelElement>('tpl-lever', 'label');
  const input = part<HTMLInputElement>(label, 'input');
  part(label, '.lever__label').textContent = levers[id].label;
  part(label, '.lever__detail').textContent = levers[id].detail;
  input.addEventListener('change', () => {
    state.levers = { ...state.levers, [id]: input.checked };
    applyDay();
  });
  slot('levers').append(label);
  return [id, input] as const;
}));


const backToActual = (): void => {
  state.levers = { ...actualLevers };
  applyDay();
};
slot('whatif-reset').addEventListener('click', backToActual);
slot('mode-reset').addEventListener('click', backToActual);
slot('mode-open').addEventListener('click', () => openRail('rail-whatif'));

// ----------------------------------------
// Walkthrough: stop at each moment of the incident; Continue plays to the next
// ----------------------------------------
const stopAt = (i: number): PlatformEvent | undefined => {
  const id = tourStops[i];
  return id === undefined ? undefined : eventById(id);
};

const endTour = (): void => {
  state.tour = null;
  state.tourWaiting = false;
  state.calloutKey = '';
};

const startTour = (): void => {
  const first = stopAt(0);
  if (!first) return;
  if (counterfactual()) {
    state.levers = { ...actualLevers };
    applyDay();
  }
  setFollowing(null);
  seek(first.start);
  setPlaying(false);
  state.tour = 0;
  state.tourWaiting = true;
  state.calloutKey = '';
};

const continueTour = (): void => {
  if (state.tour === null) return;
  const next = stopAt(state.tour + 1);
  if (!next) {
    endTour();
    setPlaying(true);
    return;
  }
  state.tourWaiting = false;
  state.calloutKey = '';
  if (next.start - state.minute > config.tour.skipAheadMinutes) seek(next.start - config.tour.leadMinutes);
  setPlaying(true);
};

// Called each tick while playing: pause on reaching the next stop.
const holdAtStop = (): void => {
  if (state.tour === null || state.tourWaiting) return;
  const next = stopAt(state.tour + 1);
  if (!next || state.minute < next.start) return;
  state.minute = next.start;
  state.tour += 1;
  state.tourWaiting = true;
  state.calloutKey = '';
  setPlaying(false);
};

slot('tour-start').addEventListener('click', startTour);
slot('tour-start-ticker').addEventListener('click', startTour);
slot('tour-start-rail').addEventListener('click', () => {
  closeRail();
  startTour();
});
slot('tour-next').addEventListener('click', continueTour);
slot('tour-end').addEventListener('click', () => {
  endTour();
  setPlaying(true);
});

const tourEvent = (): PlatformEvent | null => (state.tour !== null && state.tourWaiting ? stopAt(state.tour) ?? null : null);

// ----------------------------------------
// Size
// ----------------------------------------
const resize = (): void => {
  const r = figure.getBoundingClientRect();
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  state.width = r.width;
  state.height = r.height;
  svg.setAttribute('viewBox', `0 0 ${r.width} ${r.height}`);
  overlay.setAttribute('viewBox', `0 0 ${r.width} ${r.height}`);
  canvas.width = Math.round(r.width * dpr);
  canvas.height = Math.round(r.height * dpr);
  ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
  layoutMarkers();
  // Dots are placed from the scene every frame, so a resize keeps them (and any followed request).
  if (engine.dots.length === 0) refill();
};
new ResizeObserver(resize).observe(figure);

// ----------------------------------------
// Loop
// ----------------------------------------
const tick = (_time: number, deltaMs: number): void => {
  const dt = Math.min(0.1, deltaMs / 1000);
  if (state.playing && !gsap.isTweening(state)) {
    if (reduced.matches) {
      state.stepClock += dt;
      if (state.stepClock >= config.playback.reducedStepSeconds) {
        state.stepClock = 0;
        state.minute += config.playback.reducedStepMinutes;
      }
    } else {
      state.minute += playbackSpeed(state.minute) * dt;
    }
    if (state.minute >= minutesInDay) {
      state.minute = minutesInDay;
      setPlaying(false);
    }
    holdAtStop();
  }
  if (state.width === 0) return;
  const frame = frameAt(day, state.minute);
  const scene = buildScene(frame, { width: state.width, height: state.height, ...range });
  state.scene = scene;
  renderFigure(scene, frame);
  renderReadout(frame);
  showEvent(tourEvent() ?? activeEvent(state.minute) ?? null);
  placeCallout(scene, figureObstacles(scene));
  renderTrace();
  renderInspector(scene, frame);
  if (!ctx) return;
  if (reduced.matches) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }
  if (state.playing) stepEngine(engine, frame, dt);
  drawDots(ctx, engine, scene, look, state.traced);
};

// ----------------------------------------
// Start
// ----------------------------------------
homeTrace();
document.fonts.ready.then(layoutMarkers).catch(() => undefined);
renderTimeline();
renderLedger();
renderWhatif();
resize();
setPlaying(true);

// First visit opens on the walkthrough; afterwards the replay just plays.
const firstVisit = ((): boolean => {
  try {
    const seen = window.localStorage.getItem(config.tour.storageKey) !== null;
    window.localStorage.setItem(config.tour.storageKey, '1');
    return !seen;
  } catch {
    return false;
  }
})();
if (firstVisit) startTour();
gsap.ticker.add(tick);
