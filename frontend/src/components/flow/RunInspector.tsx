// Adapté de Request Flow Replay — Copyright (c) 2026 Cliff Pyles — licence MIT, voir third_party/request-flow-replay/LICENSE.txt
// "Suivre une requête": the original's waterfall of one request, here for one real AURORA run —
// every stage on a real time axis, the decision, checks, pending permissions, the answer and
// its sources. External text is rendered as text only.
import { useEffect, useMemo, useState } from 'react';
import { Ban, Check, ExternalLink, FolderInput, Play, Redo2, RotateCcw, ShieldAlert, X } from 'lucide-react';
import { api } from '../../lib/api';
import { useAurora } from '../../lib/store';
import { ENGINE_LABELS, INTENT_LABELS, STAGE_LABELS, fmtDateTime, fmtMs, fmtPct, fmtUsd } from '../../lib/format';
import type { Permission, RunDetail, Source, Step } from '../../lib/types';
import { Badge, EmptyState, Spinner } from '../ui';
import PermissionCard from '../chat/PermissionCard';
import { useSend } from '../chat/useSend';
import MakePageButton from '../artefacts/MakePageButton';
import { flowConfig } from './config';
import { RUN_STATUS_LABELS, isTerminal, stationById, statusTone, toMs, type Journey, type Segment, type SegmentStatus } from './model';

interface Props {
  runId: number;
  journey: Journey | undefined;
  detail: RunDetail | undefined;
  onClose: () => void;
  onReplay: (at: number) => void;
  onChanged: () => void;
}

const SEGMENT_LABELS: Record<Exclude<SegmentStatus, ''>, string> = {
  running: 'en cours', done: 'terminé', error: 'erreur', skipped: 'ignoré', waiting: 'en attente', granted: 'autorisé',
  denied: 'refusé', passed: 'vérifié', failed: 'échec', ready: 'prête', cancelled: 'annulé', interrupted: 'interrompu',
};
const segmentTone = (s: SegmentStatus): 'green' | 'red' | 'amber' | 'cyan' | 'default' => {
  if (s === 'done' || s === 'passed' || s === 'granted' || s === 'ready') return 'green';
  if (s === 'error' || s === 'failed' || s === 'denied') return 'red';
  if (s === 'waiting') return 'amber';
  if (s === 'running') return 'cyan';
  return 'default';
};
const METHOD_LABELS: Record<string, string> = {
  explicit: 'Explicite (bouton)', rule: 'Règle', semantic: 'Similarité sémantique', llm: 'Classifieur local', default: 'Par défaut',
  clarification: 'Précision demandée au tour précédent',
};
const tone = (t: ReturnType<typeof statusTone>) => (t === 'grey' ? 'default' : t);

const toText = (v: unknown, max = 400): string => {
  let s: string;
  if (v === null || v === undefined) s = '—';
  else if (typeof v === 'string') s = v;
  else if (typeof v === 'number' || typeof v === 'boolean') s = String(v);
  else if (Array.isArray(v)) s = v.map(x => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ');
  else s = JSON.stringify(v);
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
};
const safeUrl = (url: string): string | null => (/^https?:\/\//i.test(url) ? url : null);

const segmentLabel = (s: Segment): string => {
  const stage = STAGE_LABELS[s.stage] ?? s.stage;
  const what = s.model || s.tool;
  return what ? `${stage} · ${what}` : stage;
};

export default function RunInspector({ runId, journey, detail, onClose, onReplay, onChanged }: Props) {
  const { pendingPermissions } = useAurora();
  const send = useSend();
  const [busy, setBusy] = useState<string>('');
  const [actionError, setActionError] = useState('');
  const [actionNote, setActionNote] = useState('');

  const task = detail?.task;
  const status = journey?.status ?? (task?.status as Journey['status'] | undefined) ?? 'pending';
  const createdAt = journey?.createdAt ?? toMs(task?.created_at);
  const events = journey?.events ?? detail?.events ?? [];

  const decision = useMemo(() => {
    const e = [...events].reverse().find(ev => ev.kind === 'route.decided');
    if (e) return e.payload ?? {};
    // Events gone (history cleared): decision_json nests {decision:{intent, method…}, engine:{engine, model, reason}}.
    const raw = (task as (RunDetail['task'] & { decision_json?: string }) | undefined)?.decision_json;
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw) as Record<string, any>;
      if (!parsed || typeof parsed !== 'object') return null;
      const base = parsed.decision && typeof parsed.decision === 'object' ? parsed.decision : parsed;
      const eng = parsed.engine && typeof parsed.engine === 'object' ? parsed.engine : null;
      return eng ? { ...base, engine: eng.engine, model: eng.model, reason: eng.reason } : base;
    } catch { return null; }
  }, [events, task]);

  const checks = useMemo(() => events.filter(e => e.kind === 'verify.passed' || e.kind === 'verify.failed')
    .flatMap(e => (Array.isArray(e.payload?.checks) ? e.payload.checks as { name?: unknown; ok?: unknown; detail?: unknown }[] : [])
      .map((c, i) => ({ key: `${e.id}-${i}`, name: toText(c.name, 120), ok: c.ok === true, detail: toText(c.detail, 300) }))), [events]);

  const sources = useMemo(() => {
    const seen = new Set<string>();
    const out: Source[] = [];
    events.filter(e => e.kind === 'tool.result').forEach(e => {
      (Array.isArray(e.payload?.sources) ? e.payload.sources as Partial<Source>[] : []).forEach(src => {
        const key = `${src.url ?? ''}|${src.title ?? ''}`;
        if (seen.has(key) || (!src.url && !src.title)) return;
        seen.add(key);
        out.push({ title: toText(src.title ?? src.url, 160), url: String(src.url ?? ''), kind: String(src.kind ?? ''), retrieved_at: String(src.retrieved_at ?? '') });
      });
    });
    return out;
  }, [events]);

  const permissions = useMemo(() => {
    const byId = new Map<number, Permission>();
    (detail?.permissions ?? []).forEach(p => byId.set(p.id, p));
    pendingPermissions.filter(p => p.task_id === runId).forEach(p => byId.set(p.id, { ...byId.get(p.id), ...p }));
    return [...byId.values()].sort((a, b) => a.id - b.id);
  }, [detail, pendingPermissions, runId]);

  // While the run is in progress, its open stage counts up with the wall clock.
  const running = !isTerminal(status) && status !== 'paused';
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [running]);

  // Waterfall on the run's real time axis (original renderTrace).
  const waterfall = useMemo(() => {
    const lastVisit = journey?.visits[journey.visits.length - 1];
    const openSeg = lastVisit?.open ? lastVisit.segments[lastVisit.segments.length - 1] : undefined;
    const segs = (journey?.segments ?? []).map(s => ({ s, ms: s === openSeg ? Math.max(s.ms, now - s.start) : s.ms, open: s === openSeg }));
    const end = journey?.endAt ?? (openSeg ? now : segs.length ? Math.max(...segs.map(x => x.s.start + x.ms)) : createdAt);
    const total = Math.max(0, end - createdAt);
    const scale = Math.max(total, ...segs.map(x => x.s.start - createdAt + x.ms), flowConfig.trace.minScaleMs);
    const seenSteps = new Set(segs.map(x => x.s.stepId).filter((x): x is number => x !== null));
    const planned: Step[] = (detail?.steps ?? []).filter(s => !seenSteps.has(s.id));
    return { segs, total, scale, planned };
  }, [journey, detail, createdAt, now]);

  const tokens = (journey?.segments ?? []).reduce((acc, s) => ({ i: acc.i + s.inputTokens, o: acc.o + s.outputTokens }), { i: 0, o: 0 });
  // The task row carries the run total; model.result events carry each call's cost.
  const cost = Math.max(task?.cost_usd ?? 0, (journey?.segments ?? []).reduce((a, s) => a + s.costUsd, 0));
  const canCancel = status === 'running' || status === 'pending' || status === 'waiting_permission' || status === 'paused';
  // The server only resumes interrupted / paused runs (done, error, cancelled are never re-executed):
  // those can be sent again as a new request instead.
  const canResume = status === 'interrupted' || status === 'paused';
  const canResend = (status === 'error' || status === 'cancelled') && !!task?.input_text?.trim();
  // Files moved by this run can be put back (POST /api/files/restore/{run_id}), as the answer says.
  const quarantined = (detail?.steps ?? []).some(s => s.tool === 'files.quarantine' && s.status === 'done');

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    setActionError('');
    setActionNote('');
    try {
      await fn();
      onChanged();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Action impossible');
    } finally {
      setBusy('');
    }
  };
  const restore = async () => {
    const r = await api.restoreFiles(runId);
    if (!r.ok) throw new Error(r.error || r.summary || 'Restauration impossible');
    setActionNote(r.summary || 'Fichiers restaurés.');
  };
  const resend = async () => {
    const r = await send(task?.input_text ?? '', { source: 'panel', withAttachments: false, openDrawer: true });
    if (!r) throw new Error('Nouvelle demande impossible : service local indisponible.');
    setActionNote(`Nouvelle demande n° ${r.task_id} envoyée.`);
  };

  const answer = task?.result_text ?? '';
  const title = journey?.title || task?.input_text || task?.title || `Requête n° ${runId}`;
  const st = statusTone(status);

  return (
    <section className="flow-trace" style={{ ['--trace-color' as string]: `var(--${st === 'grey' ? 'muted' : st})` }} aria-labelledby={`flow-trace-${runId}`}>
      <header className="flow-trace-head">
        <div>
          <p className="flow-trace-kicker">Suivre une requête · n° {runId}</p>
          <h3 className="flow-trace-title" id={`flow-trace-${runId}`}>{title}</h3>
          <div className="flow-trace-meta">
            <Badge tone={tone(st)}>{RUN_STATUS_LABELS[status] ?? status}</Badge>
            {journey?.intent && <Badge tone="violet">{INTENT_LABELS[journey.intent] ?? journey.intent}</Badge>}
            {journey?.engine && <Badge>{ENGINE_LABELS[journey.engine] ?? journey.engine}</Badge>}
          </div>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Arrêter de suivre (Échap)"><X size={18} /></button>
      </header>

      <dl className="flow-trace-facts">
        <div><dt>Reçue</dt><dd>{Number.isFinite(createdAt) ? fmtDateTime(new Date(createdAt).toISOString()) : '—'}</dd></div>
        <div><dt>Durée</dt><dd>{journey?.durationMs !== null && journey?.durationMs !== undefined ? fmtMs(journey.durationMs) : isTerminal(status) ? '—' : 'en cours'}</dd></div>
        <div><dt>Coût cloud</dt><dd>{fmtUsd(cost)}</dd></div>
        <div><dt>Jetons</dt><dd>{tokens.i + tokens.o > 0 ? `${tokens.i} → ${tokens.o}` : '—'}</dd></div>
      </dl>

      <div className="flow-trace-actions">
        {canCancel && <button type="button" className="btn btn-sm btn-danger" disabled={!!busy} onClick={() => void act('cancel', () => api.cancelRun(runId))}>{busy === 'cancel' ? <Spinner size={12} /> : <Ban size={14} />} Annuler</button>}
        {canResume && <button type="button" className="btn btn-sm" disabled={!!busy} onClick={() => void act('resume', () => api.resumeRun(runId))}>{busy === 'resume' ? <Spinner size={12} /> : <Play size={14} />} Reprendre</button>}
        {canResend && <button type="button" className="btn btn-sm" disabled={!!busy} onClick={() => void act('resend', resend)} title="Renvoie le même texte comme nouvelle demande : cette requête-ci n’est pas réexécutée">{busy === 'resend' ? <Spinner size={12} /> : <Redo2 size={14} />} Relancer</button>}
        {quarantined && <button type="button" className="btn btn-sm" disabled={!!busy} onClick={() => void act('restore', restore)} title="Remet les fichiers déplacés en quarantaine par cette requête à leur emplacement d’origine">{busy === 'restore' ? <Spinner size={12} /> : <FolderInput size={14} />} Restaurer les fichiers</button>}
        {Number.isFinite(createdAt) && <button type="button" className="btn btn-sm btn-ghost" onClick={() => onReplay(createdAt)} title="Relit les événements enregistrés, sans rien exécuter"><RotateCcw size={14} /> Rejouer le parcours</button>}
        {status === 'done' && <MakePageButton source={{ runId }} className="btn btn-sm" iconSize={14} />}
      </div>
      {actionError && <p className="flow-trace-error" role="alert">{actionError}</p>}
      {actionNote && <p className="flow-trace-note" role="status">{actionNote}</p>}

      {permissions.some(p => p.status === 'pending') && (
        <div className="flow-trace-section">
          <h4><ShieldAlert size={14} /> Autorisation en attente</h4>
          {/* Same card as in the chat: full preview (scrollable), honest failure message. */}
          {permissions.filter(p => p.status === 'pending').map(p => <PermissionCard key={p.id} permission={p} compact onResult={onChanged} />)}
        </div>
      )}

      <div className="flow-trace-section">
        <h4>Décision</h4>
        {decision ? (
          <dl className="flow-trace-decision">
            <div><dt>Intention</dt><dd>{INTENT_LABELS[String(decision.intent)] ?? toText(decision.intent, 80)}</dd></div>
            <div><dt>Méthode</dt><dd>{METHOD_LABELS[String(decision.method)] ?? toText(decision.method, 40)}</dd></div>
            <div><dt>Confiance</dt><dd>{typeof decision.confidence === 'number' ? fmtPct(decision.confidence * 100) : '—'}</dd></div>
            <div><dt>Moteur</dt><dd>{ENGINE_LABELS[String(decision.engine)] ?? toText(decision.engine, 40)}{decision.model ? ` · ${toText(decision.model, 60)}` : ''}</dd></div>
            {decision.reason && <div data-wide="true"><dt>Raison</dt><dd>{toText(decision.reason, 300)}</dd></div>}
            {Array.isArray(decision.secondary_intents) && decision.secondary_intents.length > 0 && (
              <div data-wide="true"><dt>Intentions secondaires</dt><dd>{decision.secondary_intents.map((s: unknown) => INTENT_LABELS[String(s)] ?? String(s)).join(', ')}</dd></div>
            )}
          </dl>
        ) : <p className="muted flow-trace-note">Aucune décision enregistrée pour cette requête.</p>}
      </div>

      <div className="flow-trace-section">
        <h4>Cascade des étapes</h4>
        {waterfall.segs.length === 0 && waterfall.planned.length === 0 ? (
          <EmptyState>{journey?.hasEvents === false || !journey ? 'Événements de cette requête indisponibles.' : 'Aucune étape enregistrée.'}</EmptyState>
        ) : (
          <ol className="flow-spans">
            {waterfall.segs.map(({ s, ms, open }) => {
              const left = ((s.start - createdAt) / waterfall.scale) * 100;
              const width = (ms / waterfall.scale) * 100;
              const color = stationById.get(s.stage)?.colorVar ?? '--cyan';
              return (
                <li key={`${s.key}-${s.start}`} className="flow-span" data-status={s.status} data-open={open}>
                  <span className="flow-span-label" title={s.title}>{segmentLabel(s)}</span>
                  <span className="flow-span-track"><span className="flow-span-bar" style={{ left: `${Math.min(100, Math.max(0, left))}%`, width: `${Math.min(100 - Math.max(0, left), Math.max(0, width))}%`, background: `var(${color})` }} /></span>
                  <span className="flow-span-ms" title={open ? 'En cours : temps écoulé' : s.measured ? 'Durée mesurée' : 'Temps écoulé entre événements'}>{fmtMs(ms)}</span>
                  <span className="flow-span-extra">
                    {s.status && <Badge tone={segmentTone(s.status)}>{SEGMENT_LABELS[s.status as Exclude<SegmentStatus, ''>] ?? s.status}</Badge>}
                    {(s.inputTokens > 0 || s.outputTokens > 0) && <small>{s.inputTokens} → {s.outputTokens} jetons</small>}
                    {s.costUsd > 0 && <small>{fmtUsd(s.costUsd)}</small>}
                  </span>
                </li>
              );
            })}
            {waterfall.planned.map(step => (
              <li key={`plan-${step.id}`} className="flow-span" data-status="planned">
                <span className="flow-span-label" title={step.title}>{STAGE_LABELS[step.stage] ?? step.stage}{step.tool ? ` · ${step.tool}` : ''}</span>
                <span className="flow-span-track flow-span-track--planned" />
                <span className="flow-span-ms">—</span>
                <span className="flow-span-extra"><Badge>{step.status === 'pending' ? 'prévue' : step.status}</Badge></span>
              </li>
            ))}
          </ol>
        )}
        {waterfall.segs.length > 0 && (
          <p className="flow-trace-total">{fmtMs(waterfall.total)} {isTerminal(status) ? 'de bout en bout' : 'jusqu\'ici · en cours'}</p>
        )}
      </div>

      {checks.length > 0 && (
        <div className="flow-trace-section">
          <h4>Vérifications</h4>
          <ul className="flow-checks">
            {checks.map(c => (
              <li key={c.key} data-ok={c.ok}>
                {c.ok ? <Check size={13} /> : <X size={13} />}
                <span><strong>{c.name}</strong>{c.detail && c.detail !== '—' ? ` — ${c.detail}` : ''}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {permissions.some(p => p.status !== 'pending') && (
        <div className="flow-trace-section">
          <h4>Autorisations décidées</h4>
          <ul className="flow-checks">
            {permissions.filter(p => p.status !== 'pending').map(p => (
              <li key={p.id} data-ok={p.status === 'granted'}>
                {p.status === 'granted' ? <Check size={13} /> : <X size={13} />}
                <span><strong>{p.title}</strong> — {p.status === 'granted' ? 'autorisée' : p.status === 'denied' ? 'refusée' : 'expirée'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flow-trace-section">
        <h4>Réponse</h4>
        {answer ? (
          <p className="flow-trace-answer">{answer.length > 900 ? `${answer.slice(0, 899)}…` : answer}</p>
        ) : !detail ? <p className="muted flow-trace-note">Détail de la requête en cours de chargement ou indisponible.</p>
          : <p className="muted flow-trace-note">{isTerminal(status) ? 'Aucune réponse enregistrée.' : 'Réponse pas encore prête.'}</p>}
        {sources.length > 0 && (
          <ul className="flow-sources">
            {sources.map(s => {
              const url = safeUrl(s.url);
              return (
                <li key={`${s.url}|${s.title}`}>
                  {url ? <a href={url} target="_blank" rel="noopener noreferrer">{s.title} <ExternalLink size={11} /></a> : <span>{s.title}</span>}
                  {s.kind && <small> · {s.kind}</small>}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
