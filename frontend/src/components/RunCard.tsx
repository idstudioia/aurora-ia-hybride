// One AURORA answer: text (safe mini-markdown), how it was produced (intent, engine, model,
// method, confidence, duration, cost), sources, recalled memories, verification, widget and,
// when needed, the permission card.
import { lazy, Suspense, useEffect, useState } from 'react';
import { BookOpen, CircleCheck, Cloud, Link as LinkIcon, Square, TriangleAlert, Volume2 } from 'lucide-react';
import { api } from '../lib/api';
import { useStreamStatus } from '../lib/stream';
import type { EngineChoice, RunDetail, RunResult, Step } from '../lib/types';
import { ENGINE_LABELS, INTENT_LABELS, fixedFr, fmtDateTime, fmtMs, fmtUsd } from '../lib/format';
import { speak, stopSpeaking, useSpeaking, voiceSupport } from '../lib/voice';
import { Badge, Spinner } from './ui';
import { Markdown } from './chat/Markdown';
import LiveProgress from './chat/LiveProgress';
import PermissionCard from './chat/PermissionCard';
import { OriginBadges } from './chat/OriginBadges';
import ReportButton from './chat/ReportButton';
import MakePageButton from './artefacts/MakePageButton';
import { AgentSummary } from './chat/AgentPlan';

// Widget cards are only drawn for runs that carry ui.widget: loaded on first use, outside the shell chunk.
const RunWidget = lazy(() => import('./chat/Widgets'));
import { isTerminal, runEvents, useLiveFeed } from './chat/live';
import { METHOD_LABELS, STATUS_LABELS, TIER_LABELS, VERIFY_MOD_LABELS } from './chat/labels';
import { getPrefs } from './chat/prefs';
import { domainOf, markdownToPlain, plainText, safeUrl } from './chat/text';

/** A run returned as 'running' finishes in the background: follow its events, then read it. */
function useBackgroundCompletion(run: RunResult): RunDetail | null {
  const running = run.status === 'running';
  useLiveFeed(running);
  const done = running && isTerminal(run.task_id);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  useEffect(() => {
    if (!done) return;
    let alive = true;
    api.run(run.task_id).then(d => { if (alive) setDetail(d); }).catch(() => undefined);
    return () => { alive = false; };
  }, [done, run.task_id]);
  return running ? detail : null;
}

const parseJson = (raw: string | undefined): Record<string, any> | null => {
  if (!raw) return null;
  try { const v = JSON.parse(raw); return v && typeof v === 'object' ? v : null; } catch { return null; }
};

/** Secondary intents the plan really handled: the compose step lists them (planner.respond params.secondary;
 *  params_json stores the step spec {params, action}). */
function handledSecondary(steps: Step[]): string[] | null {
  const spec = parseJson(steps.find(s => s.tool === 'compose')?.params_json);
  const params = spec?.params && typeof spec.params === 'object' ? spec.params : spec;
  return Array.isArray(params?.secondary) ? params.secondary.map(String) : null;
}

/**
 * Engine that really produced the answer. After a refused, expired or failed Claude step the
 * orchestrator falls back to a local_ai step, while run.engine still names the planned cloud;
 * and a finished plan may hold no AI step at all (e.g. « analyse » without a document).
 */
function effectiveEngine(engine: EngineChoice | undefined, steps: Step[], status: string): EngineChoice | undefined {
  if (!engine?.engine || !/local|cloud/.test(engine.engine)) return engine;
  const withTool = engine.engine.startsWith('tool');
  const finished = status === 'done' || status === 'error' || status === 'cancelled';
  if (finished && steps.length > 0 && !steps.some(s => s.stage === 'local_ai' || s.stage === 'cloud_ai')) {
    return { ...engine, engine: withTool ? 'tool' : 'none', cloud_tier: null, model: null, needs_consent: false,
      reason: `${engine.reason ? `${engine.reason} ` : ''}Aucune IA n’a été sollicitée pour cette réponse.` };
  }
  if (!engine.engine.includes('cloud')) return engine;
  const cloud = steps.find(s => s.stage === 'cloud_ai');
  if (!cloud || !['skipped', 'error', 'cancelled'].includes(cloud.status)) return engine;
  const local = [...steps].reverse().find(s => s.stage === 'local_ai' && s.status === 'done');
  const model = local ? parseJson(local.result_json)?.model : null;
  return {
    ...engine,
    engine: local ? (withTool ? 'tool+local' : 'local') : (withTool ? 'tool' : 'none'),
    cloud_tier: null,
    model: typeof model === 'string' && model ? model : null,
    needs_consent: false,
    reason: `${engine.reason ? `${engine.reason} ` : ''}Claude n’a pas été utilisé : ${local ? 'réponse produite par l’IA locale (repli).' : 'aucune IA n’a produit la réponse.'}`,
  };
}

export default function RunCard({ run, compact = false, onRunUpdate, startedAt }: {
  run: RunResult;
  compact?: boolean;
  onRunUpdate?: (run: RunResult) => void;
  startedAt?: number;
}) {
  const connected = useStreamStatus();
  const speakingNow = useSpeaking();
  const [readingThis, setReadingThis] = useState(false);
  const background = useBackgroundCompletion(run);
  const decision = run.decision;
  const steps = background?.steps ?? run.steps ?? [];
  const status = background ? background.task.status : run.status;
  const engine = effectiveEngine(run.engine, steps, status);
  const answer = background ? background.task.result_text : run.answer;
  const cloudy = !!engine?.engine && engine.engine.includes('cloud');
  const handled = handledSecondary(steps);
  // Recalled memories only matter when a model consumed them (not for engine 'tool' / 'none').
  const aiAnswered = steps.some(s => (s.stage === 'local_ai' || s.stage === 'cloud_ai') && s.status === 'done');
  const synth = voiceSupport().synthesis;

  useEffect(() => { if (!speakingNow) setReadingThis(false); }, [speakingNow]);

  const toggleRead = () => {
    if (readingThis && speakingNow) { stopSpeaking(); setReadingThis(false); return; }
    if (speak(markdownToPlain(answer || run.error || ''), { voiceName: getPrefs().voiceName })) setReadingThis(true);
  };

  return (
    <article className={`chat-run chat-run-${status} ${compact ? 'chat-run-compact' : ''}`}>
      <div className="chat-run-badges">
        {status === 'done' && <OriginBadges origin={run.origin} />}
        {decision?.intent && <Badge tone="cyan" title={decision.explanation ? plainText(decision.explanation) : undefined}>{INTENT_LABELS[decision.intent] ?? decision.intent}</Badge>}
        {engine?.engine && <Badge tone={cloudy ? 'violet' : 'default'} title={engine.reason ? plainText(engine.reason) : undefined}>{ENGINE_LABELS[engine.engine] ?? engine.engine}</Badge>}
        {engine?.model && <Badge>{engine.model}{engine.cloud_tier ? ` · ${TIER_LABELS[engine.cloud_tier] ?? engine.cloud_tier}` : ''}</Badge>}
        {decision && typeof decision.confidence === 'number' && (
          <Badge title={`Méthode de décision : ${METHOD_LABELS[decision.method] ?? decision.method}`}>{METHOD_LABELS[decision.method] ?? decision.method} · {Math.round(decision.confidence * 100)} %</Badge>
        )}
        {typeof run.duration_ms === 'number' && run.duration_ms > 0 && <Badge>{fmtMs(run.duration_ms)}</Badge>}
        {(cloudy || run.cost_usd > 0) && <Badge tone="amber" title="Coût du cloud pour cette réponse">{fmtUsd(run.cost_usd)}</Badge>}
        {status !== 'done' && <Badge tone={status === 'error' ? 'red' : status === 'waiting_permission' ? 'amber' : 'default'}>{STATUS_LABELS[status] ?? status}</Badge>}
        {run.agent?.corrected && <Badge tone="amber" title="Un contrôle de la réponse a échoué ; l’agent a refait une tentative avant de répondre. Le contrôle final est détaillé plus bas.">corrigée après contrôle</Badge>}
        {!compact && handled && decision?.secondary_intents?.map(si => (handled.includes(si)
          ? <Badge key={si} tone="default">+ {INTENT_LABELS[si] ?? si}</Badge>
          : <Badge key={si} tone="amber" title="Demande détectée mais non traitée : reformulez-la séparément.">Non traité : {INTENT_LABELS[si] ?? si}</Badge>))}
        {/* The service moves the parts it did not perform out of secondary_intents (decision.unhandled_intents). */}
        {!compact && decision?.unhandled_intents?.filter(si => !decision.secondary_intents?.includes(si)).map(si => (
          <Badge key={`u-${si}`} tone="amber" title="Demande détectée mais non traitée : reformulez-la séparément.">Non traité : {INTENT_LABELS[si] ?? si}</Badge>))}
        {synth && (answer || run.error) && (
          <button type="button" className="icon-btn chat-run-read" onClick={toggleRead} title={readingThis && speakingNow ? 'Arrêter la lecture' : 'Lire à voix haute'} aria-label={readingThis && speakingNow ? 'Arrêter la lecture' : 'Lire à voix haute'}>
            {readingThis && speakingNow ? <Square size={14} /> : <Volume2 size={15} />}
          </button>
        )}
      </div>

      {run.status === 'running' && !background && (
        <LiveProgress events={runEvents(run.task_id)} startedAt={startedAt ?? Date.now()} connected={connected} compact={compact}
          onCancel={() => { void api.cancelRun(run.task_id).then(r => onRunUpdate?.(r)).catch(() => undefined); }} />
      )}

      {answer ? <Markdown text={answer} className="chat-run-answer" /> : null}
      {(status === 'error' || run.error) && run.error && run.error !== answer && <div className="chat-run-error" role="alert">{plainText(run.error)}</div>}
      {status === 'cancelled' && !answer && <div className="chat-run-muted">Traitement annulé.</div>}

      {/* P1: a 'running' result is followed live (LiveProgress) and its agent field would go stale once it ends. */}
      {run.agent && run.status !== 'running' && <AgentSummary agent={run.agent} />}

      {run.ui?.widget && run.ui.data !== undefined && run.ui.data !== null && (
        <Suspense fallback={<div className="chat-run-muted"><Spinner size={12} /> Chargement de la carte…</div>}>
          <RunWidget widget={run.ui.widget} data={run.ui.data} runId={run.task_id} />
        </Suspense>
      )}
      {/* 08/11/2026: the photos of the place the question names (ui.lieux), under the answer. */}
      {run.ui?.lieux && (
        <Suspense fallback={null}>
          <RunWidget widget="lieux" data={run.ui.lieux} runId={run.task_id} />
        </Suspense>
      )}

      {run.status === 'waiting_permission' && run.permission && (
        <PermissionCard permission={run.permission} onResult={onRunUpdate} compact={compact} />
      )}

      {run.sources?.length > 0 && (
        <div className="chat-run-section">
          <div className="chat-run-label"><LinkIcon size={13} /> Sources</div>
          <ul className="chat-run-sources">
            {run.sources.map((s, i) => {
              const url = safeUrl(s.url);
              return (
                <li key={`${s.url}-${i}`}>
                  {url ? <a href={url} target="_blank" rel="noopener noreferrer nofollow">{plainText(s.title) || domainOf(url)}</a> : <span>{plainText(s.title) || '—'}</span>}
                  <small>{[domainOf(s.url) || s.kind, s.retrieved_at ? `récupéré ${fmtDateTime(s.retrieved_at)}` : ''].filter(Boolean).join(' · ')}</small>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {run.cloud_sends && run.cloud_sends.length > 0 && (
        // CLAUDE.md: what left for the cloud is always shown — open by default for conversation-wide consents.
        <details className="chat-run-section chat-run-cloud" open={run.cloud_sends.some(s => s.scope === 'conversation') && !compact}>
          <summary className="chat-run-label"><Cloud size={13} /> Envoyé à Claude{run.cloud_sends.some(s => s.scope === 'conversation') ? ' (accord de conversation)' : ''}</summary>
          {run.cloud_sends.map(s => (
            <div key={s.permission_id}>
              <small className="muted">{s.model || 'modèle non précisé'}</small>
              <pre className="chat-perm-text">{s.text}</pre>
            </div>
          ))}
        </details>
      )}

      {aiAnswered && run.memories_used?.length > 0 && (
        <div className="chat-run-section">
          <div className="chat-run-label"><BookOpen size={13} /> Souvenirs utilisés</div>
          <ul className="chat-run-memories">
            {run.memories_used.map(m => <li key={m.id}><span>{plainText(m.title)}</span><small title="Score de pertinence">{fixedFr(m.score ?? 0, 2)}</small></li>)}
          </ul>
        </div>
      )}

      {run.verification && (
        <details className={`chat-run-verify ${run.verification.ok ? 'chat-ok' : 'chat-warn'}`} open={!run.verification.ok && !compact}>
          <summary>
            {run.verification.ok ? <CircleCheck size={14} /> : <TriangleAlert size={14} />}
            {run.verification.ok ? ' Vérifié' : run.verification.blocked?.length ? ' Réponse de l’IA retenue par la vérification' : ' Vérification : points à revoir'}
            <small> · {run.verification.checks.filter(c => c.ok).length}/{run.verification.checks.length} contrôle(s)</small>
          </summary>
          <ul>
            {run.verification.checks.map((c, i) => (
              <li key={i} className={c.ok ? 'chat-ok' : 'chat-warn'}>
                {c.ok ? '✓' : '⚠'} <strong>{plainText(c.label ?? VERIFY_MOD_LABELS[c.name]?.name ?? c.name)}</strong>{c.detail ? ` — ${plainText(c.detail)}` : ''}
                {c.level === 'bloquer' && <small className="chat-verify-level" title="Ce contrôle retient le texte de l’IA quand il échoue (Paramètres › Qualité).">bloquant</small>}
              </li>
            ))}
          </ul>
          {run.withheld_answer && (
            <details className="chat-run-withheld">
              <summary>Voir le texte retenu (non vérifié)</summary>
              <p>{plainText(run.withheld_answer)}</p>
            </details>
          )}
        </details>
      )}

      {!compact && (decision?.explanation || engine?.reason) && (
        <details className="chat-run-why">
          <summary>Pourquoi cette réponse ?</summary>
          {decision?.explanation && <p><strong>Décision :</strong> {plainText(decision.explanation)}{typeof decision.duration_ms === 'number' ? ` (${fmtMs(decision.duration_ms)})` : ''}</p>}
          {engine?.reason && <p><strong>Moteur :</strong> {plainText(engine.reason)}</p>}
          {decision?.alternatives?.length > 0 && (
            <p><strong>Alternatives :</strong> {decision.alternatives.slice(0, 3).map(([name, score]) => `${INTENT_LABELS[name] ?? name} (${Math.round(score * 100)} %)`).join(', ')}</p>
          )}
        </details>
      )}

      {/* H3: a finished answer can be reported (its run must be over to be judged); P2: a done one can become a page. */}
      {status !== 'running' && status !== 'pending' && (
        <div className="chat-run-actions">
          {status === 'done' && <MakePageButton source={{ runId: run.task_id }} />}
          <ReportButton runId={run.task_id} gotIntent={decision?.intent} />
        </div>
      )}
    </article>
  );
}
