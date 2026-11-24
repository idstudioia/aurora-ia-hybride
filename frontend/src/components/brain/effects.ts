// Transient brain animations. Each one is created from a real event (live SSE or replay);
// nothing here runs on a timer of its own.
import { CORE_COLOR, DANGER_COLOR, LEVEL_BY_ID, LINK_COLOR, ROUTINE_COLOR, TOOL_COLOR, memoryKey, runKey, runStatusColor, type BrainEvent } from './model';
import { CORE_ID, hubId, type BrainSim } from './sim';
import { routineNodeId, toolNodeId } from './rings';

/** Pulse target of the whole ROUTINES ring (rings layout; render.ts reads it, the lobes ignore it). */
export const ROUTINES_RING = 'ring:routines';

interface Base { start: number; dur: number; color: string }
export type Effect =
  | (Base & { type: 'particle'; from: string; to: string; fx: number; fy: number; tx: number; ty: number; bend: number })
  | (Base & { type: 'ripple'; at: string; px: number; py: number; radius: number })
  | (Base & { type: 'flash'; node: string })
  | (Base & { type: 'halo'; node: string })
  | (Base & { type: 'link'; a: string; b: string })
  | (Base & { type: 'pulse'; node: string });

export interface PlanOptions {
  now: number;
  speed: number;          // replay speed (1 for live)
  reduced: boolean;       // prefers-reduced-motion: no particles, instant states
  live: boolean;          // live events may change node state (fade-out, status)
}

export interface Plan {
  effects: Effect[];
  /** Run status implied by the event (live mode only; replay derives statuses itself). */
  status: { key: string; status: string } | null;
}

/** Translate one classified event into animations; mutates `sim` only for appear / fade timing. */
export function planEffects(ev: BrainEvent, sim: BrainSim, opts: PlanOptions): Plan {
  const { now, reduced } = opts;
  const k = 1 / Math.sqrt(Math.max(1, opts.speed));
  const D = (ms: number) => Math.max(90, ms * k);
  const effects: Effect[] = [];
  let status: Plan['status'] = null;

  // Positions are re-resolved every frame; these are fallbacks for nodes not (yet) in the graph.
  const pos = (ref: string, fallback: string = CORE_ID) => {
    const n = sim.resolve(ref) ?? sim.resolve(fallback) ?? sim.core;
    return { x: n.x, y: n.y };
  };
  const particle = (from: string, to: string, start: number, dur: number, color: string, bend = 0.18, fallback?: string) => {
    if (reduced) return;
    const f = pos(from, fallback); const t = pos(to, fallback);
    effects.push({ type: 'particle', from, to, fx: f.x, fy: f.y, tx: t.x, ty: t.y, start, dur, color, bend });
  };
  const ripple = (at: string, start: number, dur: number, color: string, radius = 34, fallback?: string) => {
    const p = pos(at, fallback);
    effects.push({ type: 'ripple', at, px: p.x, py: p.y, start, dur: reduced ? 1200 : dur, color, radius });
  };
  const flash = (node: string, start: number, dur: number, color: string) => effects.push({ type: 'flash', node, start, dur: reduced ? 1200 : dur, color });
  const halo = (node: string, start: number, dur: number, color: string) => effects.push({ type: 'halo', node, start, dur: reduced ? 1400 : dur, color });
  const pulse = (node: string, start: number, dur: number, color: string) => effects.push({ type: 'pulse', node, start, dur: reduced ? 900 : dur, color });

  /** Node `key` pops in when the particle reaches it (or right away with reduced motion). */
  const appearAt = (key: string, at: number) => {
    const t = reduced ? now : at;
    const n = sim.byKey.get(key);
    if (n) {
      if (!n.dyingAt) { n.hiddenUntil = t; n.bornAt = reduced ? 0 : t; }
    } else if (opts.live) {
      sim.pendingAppear.set(key, t);
    }
  };

  switch (ev.action) {
    case 'mem.saved': {
      for (const id of ev.memoryIds) {
        const key = memoryKey(id);
        const node = sim.byKey.get(key);
        const level = node?.level ?? 'persistent';
        const color = LEVEL_BY_ID[level].color;
        const hub = hubId(level);
        const t1 = D(650); const t2 = D(430);
        pulse(CORE_ID, now, D(700), CORE_COLOR);
        particle(CORE_ID, hub, now, t1, color);
        pulse(hub, now + (reduced ? 0 : t1), D(800), color);
        particle(hub, key, now + t1, t2, color, 0.1, hub);
        appearAt(key, now + t1 + t2);
        ripple(key, now + (reduced ? 0 : t1 + t2), D(900), color, 40, hub);
      }
      break;
    }
    case 'mem.recalled': {
      pulse(hubId('semantic'), now, D(900), LEVEL_BY_ID.semantic.color);
      ev.memoryIds.slice(0, 16).forEach((id, i) => {
        const key = memoryKey(id);
        if (!sim.byKey.get(key)) return;
        const start = now + (reduced ? 0 : i * D(90));
        flash(key, start, D(1100), '#ffffff');
        particle(key, CORE_ID, start, D(720), LEVEL_BY_ID.semantic.color, -0.2);
        if (i === 0) pulse(CORE_ID, start + (reduced ? 0 : D(720)), D(800), CORE_COLOR);
      });
      break;
    }
    case 'mem.linked': {
      if (ev.link) {
        const a = memoryKey(ev.link[0]); const b = memoryKey(ev.link[1]);
        if (sim.byKey.get(a) && sim.byKey.get(b)) {
          effects.push({ type: 'link', a, b, start: now, dur: reduced ? 1200 : D(900), color: LINK_COLOR });
          flash(a, now, D(700), LINK_COLOR);
          flash(b, now + (reduced ? 0 : D(900)), D(700), LINK_COLOR);
        } else {
          for (const key of [a, b]) if (sim.byKey.get(key)) flash(key, now, D(800), LINK_COLOR);
        }
      }
      break;
    }
    case 'mem.updated': {
      for (const id of ev.memoryIds) {
        const key = memoryKey(id);
        if (sim.byKey.get(key)) halo(key, now, D(1600), '#ffe7a8');
      }
      break;
    }
    case 'mem.deleted': {
      for (const id of ev.memoryIds) {
        const key = memoryKey(id);
        const node = sim.byKey.get(key);
        if (node && opts.live && !node.dyingAt) {
          ripple(key, now, D(800), DANGER_COLOR, 30);
          node.dyingAt = now; node.dyingByEvent = true;
        } else {
          ripple(hubId(node?.level ?? 'persistent'), now, D(800), DANGER_COLOR, 30);
        }
      }
      break;
    }
    case 'run.created': {
      const color = LEVEL_BY_ID.actions.color;
      const hub = hubId('actions');
      const t1 = D(600);
      pulse(CORE_ID, now, D(700), CORE_COLOR);
      particle(CORE_ID, hub, now, t1, color);
      pulse(hub, now + (reduced ? 0 : t1), D(800), color);
      if (ev.runId !== null) {
        const key = runKey(ev.runId);
        particle(hub, key, now + t1, D(380), color, 0.1, hub);
        appearAt(key, now + t1 + D(380));
        if (opts.live) status = { key, status: 'pending' };
      }
      break;
    }
    case 'run.decided': {
      pulse(CORE_ID, now, D(800), CORE_COLOR);
      pulse(hubId('actions'), now, D(800), LEVEL_BY_ID.actions.color);
      // A routine is launched (route.decided, intent routine.run): the routines ring lights up,
      // and the routine itself when the phrase named it (slots.routine_id).
      if (ev.intent === 'routine.run') {
        pulse(ROUTINES_RING, now, D(1200), ROUTINE_COLOR);
        const ref = ev.routineId !== null ? routineNodeId(ev.routineId) : null;
        if (ref && sim.resolve(ref)) {
          particle(CORE_ID, ref, now, D(650), ROUTINE_COLOR, 0.2);
          pulse(ref, now + (reduced ? 0 : D(650)), D(900), ROUTINE_COLOR);
          ripple(ref, now + (reduced ? 0 : D(650)), D(900), ROUTINE_COLOR, 26);
        }
      }
      break;
    }
    case 'tool.used': {
      // tool.result: a real call of this tool by a task step.
      const color = ev.ok === false ? DANGER_COLOR : TOOL_COLOR;
      const ref = ev.tool ? toolNodeId(ev.tool) : null;
      const run = ev.runId !== null && sim.byKey.get(runKey(ev.runId)) ? runKey(ev.runId) : null;
      if (ref && sim.resolve(ref)) {
        // Rings: from the task's point (or the actions hub) out to the tool's hexagon.
        const t1 = D(560);
        particle(run ?? hubId('actions'), ref, now, t1, TOOL_COLOR, 0.2);
        pulse(ref, now + (reduced ? 0 : t1), D(900), color);
        ripple(ref, now + (reduced ? 0 : t1), D(800), color, 26);
      } else {
        // Lobes (no tools ring): the actions hub and the task show the result.
        pulse(hubId('actions'), now, D(600), LEVEL_BY_ID.actions.color);
        if (run) flash(run, now, D(600), color);
      }
      break;
    }
    case 'run.step': {
      const hub = hubId('actions');
      pulse(hub, now, D(700), LEVEL_BY_ID.actions.color);
      if (ev.runId !== null) {
        const key = runKey(ev.runId);
        if (sim.byKey.get(key)) {
          particle(hub, key, now, D(420), runStatusColor('running'), 0.12);
          flash(key, now + (reduced ? 0 : D(420)), D(600), runStatusColor('running'));
        }
        if (opts.live) status = { key, status: 'running' };
      }
      break;
    }
    case 'run.stepdone':
    case 'run.steperror': {
      const color = ev.action === 'run.steperror' ? DANGER_COLOR : runStatusColor('done');
      pulse(hubId('actions'), now, D(500), color);
      if (ev.runId !== null && sim.byKey.get(runKey(ev.runId))) flash(runKey(ev.runId), now, D(700), color);
      break;
    }
    case 'run.final': {
      const color = runStatusColor(ev.status ?? 'done');
      pulse(hubId('actions'), now, D(900), color);
      if (ev.runId !== null) {
        const key = runKey(ev.runId);
        if (sim.byKey.get(key)) ripple(key, now, D(900), color, 30);
        if (opts.live && ev.status) status = { key, status: ev.status };
      }
      break;
    }
  }
  return { effects, status };
}

/** True while an effect still has something to draw. */
export const effectAlive = (e: Effect, now: number) => now < e.start + e.dur;
