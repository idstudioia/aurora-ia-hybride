// « Contexte » frame at the top of the Chat page's Suggestions column, like Claude Code's panel: how much of
// the model's working memory the last AI call of the active conversation used, and for what. The total is
// measured by the provider; the split by part is estimated from each part's length (said in the footer).
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Bot, ChevronDown, Circle, CircleDashed, Database, ListChecks, ScrollText, User, Wrench, type LucideIcon } from 'lucide-react';
import { useAurora } from '../../lib/store';
import { fmtTokens } from '../../lib/format';
import { useReducedMotion } from '../../lib/motion';
import {
  PROVIDER_LABELS, ROW_ORDER, WINDOW_SOURCE_LABELS, contextView, fmtMeasuredAt, fmtShare, fmtUsed, latestContext, type RowKey,
} from './contextUsage';

/** Each part with AURORA's own icon (the sidebar's where one fits: Mémoire, Chat IA, Tâches; the top bar's user),
 *  coloured like its segment of the bar. */
const ROW_ICONS: Record<RowKey, LucideIcon> = {
  system: ScrollText, tools: Wrench, memory: Database, conversation: Bot, results: ListChecks, question: User,
  other: CircleDashed, free: Circle,
};

const OPEN_KEY = 'aurora.chat.context.open.v1';

function readOpen(): boolean {
  try { return window.localStorage.getItem(OPEN_KEY) !== '0'; } catch { return true; }
}

/** The values move smoothly from the previous measure to the new one (0.6 s, ease-out); at once when motion is
 *  reduced. A value that disappears goes to 0. A few lines of requestAnimationFrame: no animation library is
 *  loaded with the Chat page for this. */
function useTweened(targets: Record<string, number>, reduced: boolean): Record<string, number> {
  const [shown, setShown] = useState(targets);
  const values = useRef<Record<string, number>>({ ...targets });
  const sig = JSON.stringify(targets);
  useEffect(() => {
    const goal = JSON.parse(sig) as Record<string, number>;
    const from = { ...values.current };
    Object.keys(from).forEach(k => { if (!(k in goal)) goal[k] = 0; });
    Object.keys(goal).forEach(k => { if (!(k in from)) from[k] = 0; });
    if (reduced || Object.keys(goal).every(k => from[k] === goal[k])) {
      values.current = goal;
      setShown(goal);
      return;
    }
    const start = performance.now();
    let frame = requestAnimationFrame(function step() {
      const t = Math.min(1, Math.max(0, (performance.now() - start) / 600));
      const eased = 1 - Math.pow(1 - t, 3);
      const v: Record<string, number> = {};
      Object.keys(goal).forEach(k => { v[k] = from[k] + (goal[k] - from[k]) * eased; });
      values.current = v;
      setShown(v);
      if (t < 1) frame = requestAnimationFrame(step);
    });
    // No frame is drawn in a hidden or minimised window: the true values are shown anyway, never a stale « 0 % ».
    const settle = window.setTimeout(() => { cancelAnimationFrame(frame); values.current = goal; setShown(goal); }, 700);
    return () => { cancelAnimationFrame(frame); window.clearTimeout(settle); };
  }, [sig, reduced]);
  return shown;
}

export default function ContextCard() {
  const { chat } = useAurora();
  const reduced = useReducedMotion();
  const bodyId = useId();
  const [open, setOpen] = useState(readOpen);
  const known = useMemo(() => latestContext(chat), [chat]);
  const view = useMemo(() => (known ? contextView(known.usage) : null), [known]);

  // Animated values: the share used (0 with no window or no context) and the width of each bar segment.
  const targets = useMemo(() => {
    const t: Record<string, number> = { used: view?.used ?? 0 };
    if (view) ROW_ORDER.forEach(k => { const w = view.segments[k]; if (w) t[k] = w; });
    return t;
  }, [view]);
  const tw = useTweened(targets, reduced);

  const flipOpen = () => {
    setOpen(o => {
      try { window.localStorage.setItem(OPEN_KEY, o ? '0' : '1'); } catch { /* stockage indisponible */ }
      return !o;
    });
  };

  const usage = known?.usage;
  const when = usage ? fmtMeasuredAt(usage.measured_at, new Date()) : '';

  return (
    <section className="chat-ctx" aria-label="Contexte de la dernière demande à l'IA">
      <button type="button" className="chat-page-list-head chat-ctx-head" onClick={flipOpen} aria-expanded={open} aria-controls={bodyId}
        title={open ? 'Replier le cadre Contexte' : 'Déplier le cadre Contexte'}>
        <strong>Contexte</strong>
        <ChevronDown size={14} className={`chat-ctx-chevron ${open ? '' : 'chat-ctx-closed'}`} aria-hidden />
        {!open && view && <small className="chat-ctx-mini">{view.used !== null ? fmtUsed(view.used) : fmtTokens(view.total)}</small>}
      </button>
      {open && (
        <div id={bodyId} className="chat-ctx-body">
          {!usage || !view ? (
            <p className="chat-ctx-note">Aucune réponse de l'IA dans cette conversation pour l'instant.</p>
          ) : (
            <>
              <div className="chat-ctx-summary">
                <strong>{view.used !== null ? `${fmtUsed(tw.used)} utilisé` : `${fmtTokens(view.total)} jetons`}</strong>
                {view.window !== null && (
                  <span title={usage.window_source ? WINDOW_SOURCE_LABELS[usage.window_source] : undefined}>
                    {fmtTokens(view.total)} / {fmtTokens(view.window)}
                  </span>
                )}
              </div>
              <div className="chat-ctx-bar" role="img"
                aria-label={view.used !== null ? `${fmtUsed(view.used)} de la mémoire de travail utilisé` : 'Répartition du total mesuré'}>
                {ROW_ORDER.map(k => ((tw[k] ?? 0) > 0
                  ? <span key={k} className={`chat-ctx-seg chat-ctx-${k}`} style={{ width: `${tw[k] * 100}%` }} />
                  : null))}
              </div>
              {view.window === null && <p className="chat-ctx-note">Taille de la mémoire de travail inconnue (modèle déchargé ou non communiquée).</p>}
              {known.newerWithoutAi && <p className="chat-ctx-note">Aucune IA n'a répondu à la dernière demande : chiffres de la dernière réponse d'une IA.</p>}
              <ul className="chat-ctx-rows">
                {view.rows.map(r => {
                  const Icon = ROW_ICONS[r.key];
                  return (
                  <li key={r.key} title={r.chars !== null ? `${r.chars.toLocaleString('fr-FR')} caractères ; jetons estimés au prorata de la longueur` : undefined}>
                    {/* « Place libre » is near-black in the bar: its icon takes the muted text colour to stay visible */}
                    <Icon size={13} className="chat-ctx-ico" style={{ color: r.key === 'free' ? 'var(--muted)' : `var(--ctx-${r.key})` }} aria-hidden />
                    <span className="chat-ctx-label">{r.label}</span>
                    <span className="chat-ctx-num">{fmtTokens(r.tokens)}</span>
                    <span className="chat-ctx-num">{fmtShare(r.share)}</span>
                  </li>
                  );
                })}
              </ul>
              <p className="chat-ctx-foot">
                {when ? `Dernière mise à jour ${when}.` : 'Heure de la mesure inconnue.'} Total mesuré par {usage.model || 'le modèle'} ({PROVIDER_LABELS[usage.provider]}) ;
                répartition estimée d'après la longueur de chaque partie.
              </p>
            </>
          )}
        </div>
      )}
    </section>
  );
}
