// Live progress of a run, rebuilt only from the real SSE events of that run.
import { useEffect, useState } from 'react';
import { CircleStop } from 'lucide-react';
import type { ActivityEvent, AgentTurnEvent } from '../../lib/types';
import { ENGINE_LABELS, INTENT_LABELS, STAGE_LABELS, fmtMs, fmtUsd } from '../../lib/format';
import { Spinner } from '../ui';
import { plainText } from './text';
import { METHOD_LABELS } from './labels';
import { partialText } from './live';
import { AgentLive, asAgentTurn } from './AgentPlan';

type StepState = 'pending' | 'running' | 'done' | 'error' | 'skipped';
interface LiveStep { id: string; idx: number; title: string; stage: string; tool: string; state: StepState; detail: string; ms: number | null }

export interface LiveSummary {
  decision: Record<string, any> | null;
  steps: LiveStep[];
  permission: Record<string, any> | null;
  verify: { ok: boolean; checks: { name: string; ok: boolean; detail: string }[]; blocked: boolean } | null;
  memories: number;
  /** P1: the last `agent.todo` of the run (current turn, list, chosen action). */
  agent: AgentTurnEvent | null;
  last: ActivityEvent | null;
  terminal: ActivityEvent | null;
}

export function summarize(events: ActivityEvent[]): LiveSummary {
  const steps = new Map<string, LiveStep>();
  let decision: Record<string, any> | null = null;
  let permission: Record<string, any> | null = null;
  let verify: LiveSummary['verify'] = null;
  let memories = 0;
  let agent: AgentTurnEvent | null = null;
  let terminal: ActivityEvent | null = null;
  const step = (p: Record<string, any>, ev: ActivityEvent): LiveStep => {
    const id = String(p.step_id ?? p.id ?? `${ev.stage}-${p.idx ?? steps.size}`);
    let s = steps.get(id);
    if (!s) {
      s = { id, idx: Number(p.idx ?? steps.size), title: String(p.title ?? ev.label ?? ''), stage: String(p.stage ?? ev.stage ?? ''), tool: String(p.tool ?? ''), state: 'pending', detail: '', ms: null };
      steps.set(id, s);
    }
    return s;
  };
  for (const ev of events) {
    const p = ev.payload ?? {};
    switch (ev.kind) {
      case 'route.decided': decision = p; break;
      case 'plan.created':
        for (const raw of Array.isArray(p.steps) ? p.steps : []) {
          const s = step({ ...raw, step_id: raw.id ?? raw.step_id }, ev);
          s.title = String(raw.title ?? s.title);
          s.stage = String(raw.stage ?? s.stage);
          s.tool = String(raw.tool ?? s.tool);
        }
        break;
      case 'step.started': { const s = step(p, ev); s.state = 'running'; if (ev.stage) s.stage = ev.stage; if (p.title) s.title = String(p.title); break; }
      case 'tool.result': {
        const s = step(p, ev);
        s.detail = plainText(p.summary ?? '') || (p.ok === false ? 'Outil indisponible' : '');
        if (typeof p.ms === 'number') s.ms = p.ms;
        if (p.ok === false) s.state = 'error';
        break;
      }
      case 'model.result': {
        const s = step(p, ev);
        const tokens = (Number(p.input_tokens) || 0) + (Number(p.output_tokens) || 0);
        s.detail = [p.model, tokens ? `${tokens} jetons` : '', Number(p.cost_usd) > 0 ? fmtUsd(Number(p.cost_usd)) : ''].filter(Boolean).join(' · ');
        if (typeof p.ms === 'number') s.ms = p.ms;
        break;
      }
      case 'step.done': { const s = step(p, ev); if (s.state !== 'error') s.state = 'done'; if (ev.duration_ms !== null && s.ms === null) s.ms = ev.duration_ms; break; }
      case 'step.error': { const s = step(p, ev); s.state = 'error'; if (p.error) s.detail = plainText(p.error); break; }
      case 'step.skipped': { const s = step(p, ev); s.state = 'skipped'; break; }
      case 'permission.requested': permission = p; break;
      case 'permission.granted': case 'permission.denied': permission = null; break;
      case 'verify.passed': case 'verify.failed':
        verify = { ok: ev.kind === 'verify.passed', checks: Array.isArray(p.checks) ? p.checks : [],
          blocked: Array.isArray(p.blocked) && p.blocked.length > 0 }; break;
      case 'memory.recalled': memories = Array.isArray(p.memory_ids) ? p.memory_ids.length : memories; break;
      case 'agent.todo': agent = asAgentTurn(p) ?? agent; break;
      default:
        if (ev.kind === 'run.done' || ev.kind === 'run.error' || ev.kind === 'run.cancelled' || ev.kind === 'run.interrupted') terminal = ev;
    }
  }
  return {
    decision, permission, verify, memories, agent, terminal,
    steps: [...steps.values()].sort((a, b) => a.idx - b.idx),
    last: events.length ? events[events.length - 1] : null,
  };
}

function useElapsed(startMs: number, active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(t);
  }, [active]);
  return Math.max(0, now - startMs);
}

const STATE_LABELS: Record<StepState, string> = { pending: 'en attente', running: 'en cours', done: 'terminé', error: 'erreur', skipped: 'ignoré' };

export default function LiveProgress({ events, startedAt, connected, onCancel, cancelling = false, compact = false, waitingLabel }: {
  events: ActivityEvent[];
  startedAt: number;
  connected: boolean;
  onCancel?: () => void;
  cancelling?: boolean;
  compact?: boolean;
  waitingLabel?: string;
}) {
  const sum = summarize(events);
  const draft = partialText(events[0]?.run_id);
  const active = !sum.terminal;
  const elapsed = useElapsed(startedAt, active);
  const d = sum.decision;
  return (
    <div className={`chat-live ${compact ? 'chat-live-compact' : ''}`} aria-live="polite">
      <div className="chat-live-head">
        {active ? <Spinner size={14} /> : <span className="chat-live-dot" />}
        <span className="chat-live-label">
          {sum.last ? plainText(sum.last.label) : (waitingLabel ?? (connected ? 'Demande envoyée, en attente du premier événement…' : 'Flux temps réel déconnecté — la réponse s’affichera à la fin.'))}
        </span>
        <span className="chat-live-time mono">{fmtMs(elapsed)}</span>
        {onCancel && active && (
          <button type="button" className="btn btn-sm btn-ghost chat-live-cancel" onClick={onCancel} disabled={cancelling} title="Annuler ce traitement">
            <CircleStop size={14} /> {cancelling ? 'Annulation…' : 'Interrompre'}
          </button>
        )}
      </div>
      {d && (
        <div className="chat-live-decision">
          <span className="badge badge-cyan">{INTENT_LABELS[d.intent] ?? d.intent}</span>
          {typeof d.confidence === 'number' && <span className="badge">{METHOD_LABELS[d.method] ?? d.method} · {Math.round(d.confidence * 100)} %</span>}
          {d.engine && <span className="badge badge-violet">{ENGINE_LABELS[d.engine] ?? d.engine}{d.model ? ` · ${d.model}` : ''}</span>}
          {!compact && d.reason && <span className="chat-live-reason">{plainText(d.reason)}</span>}
        </div>
      )}
      {sum.agent && <AgentLive turn={sum.agent} />}
      {sum.steps.length > 0 && (
        <ol className="chat-live-steps">
          {sum.steps.map(s => (
            <li key={s.id} className={`chat-live-step chat-step-${s.state}`}>
              <span className={`chat-stage-dot chat-stage-${s.stage || 'system'}`} aria-hidden="true" />
              <span className="chat-live-step-title">{s.title || STAGE_LABELS[s.stage] || s.tool || 'Étape'}</span>
              <span className="chat-live-step-state">{STATE_LABELS[s.state]}{s.ms !== null ? ` · ${fmtMs(s.ms)}` : ''}</span>
              {s.detail && !compact && <span className="chat-live-step-detail">{s.detail}</span>}
            </li>
          ))}
        </ol>
      )}
      {sum.memories > 0 && <div className="chat-live-note">{sum.memories} souvenir(s) rappelé(s)</div>}
      {sum.permission && <div className="chat-live-note chat-live-warn">Autorisation demandée : {plainText(sum.permission.title ?? '')}</div>}
      {draft && !sum.verify && (   /* B4: the answer while the model writes it, never presented as verified */
        <div className="chat-live-draft" aria-live="polite">
          <span className="chat-live-draft-label">Réponse en cours de rédaction · pas encore vérifiée</span>
          <p>{draft}</p>
        </div>
      )}
      {sum.verify && <div className={`chat-live-note ${sum.verify.ok ? 'chat-live-ok' : 'chat-live-warn'}`}>{sum.verify.ok ? '✓ Vérification réussie' : sum.verify.blocked ? '⚠ Réponse de l’IA retenue par la vérification' : '⚠ Vérification : points à revoir'}</div>}
    </div>
  );
}
