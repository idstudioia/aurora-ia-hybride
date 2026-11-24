// D2 (16/11/2026, ID Studio IA : « Oui, ajoute-le ») — the band that says a routine was launched by its schedule. Shown above
// every page until « Fermer ». Nothing is invented here: the text is the report the service keeps in the routine
// (`auto`, tasks/routines.run_scheduled), read again when the service says a scheduled launch started (run.created,
// source « schedule ») or ended (routine.scheduled) — and once at startup, so a routine that left while the
// interface was closed is said at its next opening. « Fermer » is remembered in this browser only.
//
// D2, last part (16/11/2026, ID Studio IA : « continue la suite ») — the same band also carries the proposal « réussi 5 fois de
// suite, alors proposé en automatique »: the service counts the launches by hand that went to their end
// (`proposal`, tasks/routines.proposal); the band only asks. « Régler l’horaire » opens Paramètres › Routines,
// « Plus tard » hides it in this browser until the next success, « Ne plus proposer » is kept by the service.
import { useCallback, useRef, useState } from 'react';
import { CalendarCheck, CalendarClock } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import { useEventStream } from '../lib/stream';
import { autoText } from '../lib/format';
import type { Routine } from '../lib/types';
import { useOpenSettings, useRoutines } from './studio/parts';

const SEEN_KEY = 'aurora.routine-signal.seen';

/** { routine id: the slot closed by « Fermer » }. The storage can be refused (private window): nothing is kept then. */
function readSeen(): Record<string, string> {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, string> : {};
  } catch { return {}; }
}

export default function RoutineSignal() {
  const { online, navigate, openConversation } = useAurora();
  const routines = useRoutines(online);
  const [seen, setSeen] = useState<Record<string, string>>(readSeen);
  const [error, setError] = useState('');
  const openSettings = useOpenSettings();
  // The requests a routine sent (source « routine ») and that are not over: when one ends, the launch by hand it
  // belongs to may be the fifth in a row that went to its end — the service is asked again.
  const steps = useRef(new Set<number>());
  useEventStream(event => {
    if (event.kind === 'routine.scheduled' || (event.kind === 'run.created' && event.payload?.source === 'schedule')) routines.reload();
    else if (event.kind === 'run.created' && event.payload?.source === 'routine' && event.run_id != null) steps.current.add(event.run_id);
    else if (event.kind.startsWith('run.') && event.run_id != null && steps.current.has(event.run_id)) {
      if (['run.done', 'run.error', 'run.cancelled', 'run.interrupted'].includes(event.kind)) steps.current.delete(event.run_id);
      routines.reload();
    }
  });
  /** Hides one band in this browser: a launch (key: the routine id, value: its slot) or a proposal (« p<id> », its count). */
  const close = useCallback((id: number | string, slot: string) => {
    setSeen(current => {
      const next = { ...current, [id]: slot };
      try { window.localStorage.setItem(SEEN_KEY, JSON.stringify(next)); } catch { /* kept for this page only */ }
      return next;
    });
  }, []);
  const see = async (id: number, slot: string, conversationId: number) => {
    try { await openConversation(conversationId); navigate('chat'); close(id, slot); }
    catch (err) { setError(err instanceof Error ? err.message : 'Conversation introuvable'); }
  };
  /** « Ne plus proposer »: kept by the service, in the routine. */
  const refuse = async (r: Routine) => {
    try { await api.updateRoutine(r.id, { name: r.name, triggers: r.triggers, steps: r.steps, propose: false }); routines.reload(); }
    catch (err) { setError(err instanceof Error ? err.message : 'Réponse non enregistrée'); }
  };
  const news = (routines.items ?? []).filter(r => r.auto && seen[r.id] !== r.auto.slot);
  const offers = (routines.items ?? []).filter(r => r.proposal && !r.schedule && seen[`p${r.id}`] !== String(r.proposal.count));
  if (!news.length && !offers.length) return null;
  return (
    <>
      {news.map(r => (
        <div key={r.id} className="info-banner" role="status">
          <CalendarCheck size={16} aria-hidden="true" />
          <span>Routine « {r.name} » partie toute seule {autoText(r.auto!)}{error ? ` ${error}` : ''}</span>
          {r.auto!.conversation_id != null && (
            <button type="button" className="btn btn-sm btn-primary" disabled={!online} onClick={() => void see(r.id, r.auto!.slot, r.auto!.conversation_id!)}>Voir les réponses</button>
          )}
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => close(r.id, r.auto!.slot)}>Fermer</button>
        </div>
      ))}
      {offers.map(r => (
        <div key={`p${r.id}`} className="info-banner" role="status">
          <CalendarClock size={16} aria-hidden="true" />
          <span>La routine « {r.name} » a réussi {r.proposal!.count} fois de suite{r.proposal!.time ? `, vers ${r.proposal!.time}` : ''}. Voulez-vous qu’elle parte toute seule ?{error ? ` ${error}` : ''}</span>
          <button type="button" className="btn btn-sm btn-primary" onClick={() => openSettings('.set-routines')}>Régler l’horaire</button>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => close(`p${r.id}`, String(r.proposal!.count))}>Plus tard</button>
          <button type="button" className="btn btn-sm btn-ghost" disabled={!online} onClick={() => void refuse(r)}>Ne plus proposer</button>
        </div>
      ))}
    </>
  );
}
