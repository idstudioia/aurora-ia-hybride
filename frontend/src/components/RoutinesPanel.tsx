// Paramètres › Routines (D1, backend tasks/routines.py): a named list of ordinary requests, launched by a phrase.
// « Lancer » goes through the normal request (« lance la routine … »): the steps are then sent one after the other
// as if typed, with the same permission cards. « Reprendre mes dernières demandes » pre-fills the steps with what
// was just done by hand (image « Skill backbone »: done twice by hand, then a routine).
import { useCallback, useEffect, useState } from 'react';
import { ListChecks, Play, Plus, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import { autoText, fmtDateTime, scheduleText, slotText, WEEK_DAYS } from '../lib/format';
import type { Routine } from '../lib/types';
import { EmptyState, Panel, Spinner, Unavailable } from './ui';
import { routinesChanged } from './studio/parts';

const lines = (text: string) => text.split('\n').map(l => l.trim()).filter(Boolean);

const DAY_LETTERS = ['L', 'M', 'M', 'J', 'V', 'S', 'D'];
const EVERY_DAY = [0, 1, 2, 3, 4, 5, 6];

/** D2 (16/11/2026, ID Studio IA : « continue avec la configuration de la routine ») — the schedule of one routine: a switch,
 *  an hour, the seven days. The service launches the routine at that hour through the same requests as « Lancer »
 *  (main._routine_loop); this panel only sets the schedule and shows what the service says of the last launch. */
function ScheduleEditor({ routine, online, onSaved, onError, onOpen }: {
  routine: Routine; online: boolean; onSaved: () => void; onError: (text: string) => void;
  /** Opens the conversation where the last launch by the schedule wrote its answers. */
  onOpen?: (conversationId: number) => void;
}) {
  const set = routine.schedule ?? null;
  // D2, second part: in place of the hour, an event this service knows (« au démarrage d’AURORA »). '' = an hour.
  const events = Object.entries(routine.events ?? {});
  const [event, setEvent] = useState(set?.event ?? '');
  // Without a schedule, the hour offered first is the one AURORA proposes (the usual hour of its launches by hand).
  const [time, setTime] = useState(set?.time ?? routine.proposal?.time ?? '08:00');
  const [days, setDays] = useState<number[]>(set?.days ?? EVERY_DAY);
  const [busy, setBusy] = useState(false);
  // A service started before D2 does not send the field: say when the option arrives, show no dead control.
  if (routine.schedule === undefined) return <p className="set-routine-auto">Horaire : cette option arrive au prochain redémarrage d’AURORA.</p>;
  const wanted = event ? { event, days } : { time, days };
  const ready = days.length > 0 && (!!event || !!time);
  const changed = !!set && (event !== (set.event ?? '') || (!event && time !== set.time) || days.join() !== set.days.join());
  const save = async (schedule: { time?: string; event?: string; days: number[] } | null) => {
    setBusy(true);
    try {
      await api.updateRoutine(routine.id, { name: routine.name, triggers: routine.triggers, steps: routine.steps, schedule });
      onSaved();
    } catch (err) { onError(err instanceof Error ? err.message : 'Horaire non enregistré'); }
    finally { setBusy(false); }
  };
  const toggle = (day: number) => setDays(d => (d.includes(day) ? d.filter(x => x !== day) : [...d, day].sort((a, b) => a - b)));
  return (
    <>
      <div className="set-routine-hour">
        <button type="button" role="switch" aria-checked={!!set} aria-label={`Lancer la routine ${routine.name} toute seule`} className={`set-switch ${set ? 'on' : ''}`}
          disabled={!online || busy || (!set && !ready)} onClick={() => void save(set ? null : wanted)}><span /></button>
        <span>Lancer seule{events.length ? '' : ' à'}</span>
        {events.length > 0 && (
          <select className="select" aria-label="Moment du lancement" value={event} onChange={e => setEvent(e.target.value)} disabled={busy}>
            <option value="">à une heure</option>
            {events.map(([id, words]) => <option key={id} value={id}>{words}</option>)}
          </select>
        )}
        {!event && <input type="time" className="input" aria-label="Heure du lancement" value={time} onChange={e => setTime(e.target.value)} disabled={busy} />}
        <span className="set-routine-days" role="group" aria-label="Jours du lancement">
          {DAY_LETTERS.map((letter, day) => (
            <button key={day} type="button" className={days.includes(day) ? 'on' : ''} aria-pressed={days.includes(day)} title={WEEK_DAYS[day]} disabled={busy} onClick={() => toggle(day)}>{letter}</button>
          ))}
        </span>
        {changed && <button className="btn btn-sm btn-primary" onClick={() => void save(wanted)} disabled={!online || busy || !ready}>Enregistrer l’horaire</button>}
      </div>
      <p className="set-routine-auto">
        {!set ? (routine.proposal
          ? <><strong>Réussie {routine.proposal.count} fois de suite à la main{routine.proposal.time ? `, vers ${routine.proposal.time}` : ''}.</strong> Pour qu’elle parte toute seule, choisissez le moment ci-dessus puis activez « Lancer seule ». Sinon, elle ne part que sur votre demande.</>
          : 'Pas d’horaire : la routine ne part que sur votre demande.')
          : set.event ? <>Part toute seule <strong>{scheduleText(set, routine.events)}</strong> : moins d’une minute après le démarrage, et une fois par jour au plus — un second démarrage le même jour ne la relance pas.</>
            : <>Part toute seule <strong>{scheduleText(set)}</strong>{routine.next ? <> · prochain lancement : {slotText(routine.next)}</> : null}. Si AURORA n’est pas démarrée à cette heure, la routine part à son démarrage, le jour même.</>}
      </p>
      {routine.auto && (
        <p className="set-routine-auto">
          Dernier lancement tout seul : {autoText(routine.auto)}
          {onOpen && routine.auto.conversation_id !== null && routine.auto.conversation_id !== undefined && (
            <> <button type="button" className="btn btn-sm btn-ghost" onClick={() => onOpen(routine.auto!.conversation_id!)} disabled={!online}>Voir les réponses</button></>
          )}
        </p>
      )}
    </>
  );
}

export default function RoutinesPanel() {
  const { ask, online, chat, navigate, openConversation } = useAurora();
  const [items, setItems] = useState<Routine[] | null>(null);
  const [error, setError] = useState('');
  const [note, setNote] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [name, setName] = useState('');
  const [triggers, setTriggers] = useState('');
  const [steps, setSteps] = useState('');
  const [confirm, setConfirm] = useState<number | null>(null);

  const load = useCallback(() => {
    api.routines().then(list => { setItems(list); setError(''); })
      .catch(err => { setItems(null); setError(err instanceof Error ? err.message : 'Service indisponible'); });
  }, []);
  useEffect(() => { load(); }, [load]);
  // After a routine is saved or deleted here: this list, and the other ones on the screen (the band, the Studio cards).
  const reloadAll = useCallback(() => { load(); routinesChanged(); }, [load]);

  const recent = () => {
    const asked = chat.filter(e => e.role === 'user' && e.source !== 'routine').map(e => e.text.trim()).filter(Boolean);
    setSteps(asked.slice(-5).join('\n'));
  };

  const create = async () => {
    setNote(null);
    try {
      const saved = await api.createRoutine({ name, triggers: lines(triggers), steps: lines(steps) });
      setName(''); setTriggers(''); setSteps('');
      setNote({ kind: 'ok', text: `Routine « ${saved.name} » enregistrée : dites « lance la routine ${saved.name} »${saved.triggers.length ? ` ou « ${saved.triggers[0]} »` : ''}.` });
      reloadAll();
    } catch (err) { setNote({ kind: 'err', text: err instanceof Error ? err.message : 'Enregistrement impossible' }); }
  };

  const remove = async (id: number) => {
    setConfirm(null);
    try { await api.deleteRoutine(id); reloadAll(); }
    catch (err) { setNote({ kind: 'err', text: err instanceof Error ? err.message : 'Suppression impossible' }); }
  };

  const launch = (r: Routine) => { navigate('chat'); void ask(`Lance la routine ${r.name}`, { source: 'panel' }); };

  // D2: the answers of a launch by the schedule are in a conversation of their own — opened like any past one.
  const see = async (conversationId: number) => {
    try { await openConversation(conversationId); navigate('chat'); }
    catch (err) { setNote({ kind: 'err', text: err instanceof Error ? err.message : 'Conversation introuvable' }); }
  };

  return (
    <Panel className="set-routines" title="Routines" icon={<ListChecks size={18} />}
      subtitle="Une suite de demandes enregistrée une fois, lancée par une phrase. Chaque étape passe comme si vous l’aviez tapée : mêmes outils, mêmes demandes d’accord.">
      {note && <span className={`set-save set-save-${note.kind}`} role={note.kind === 'err' ? 'alert' : 'status'}>{note.text}</span>}
      {error && <Unavailable what="Routines" reason={error} />}
      {!error && items === null && <EmptyState><Spinner /> Chargement des routines…</EmptyState>}
      {items && items.length === 0 && <p className="set-small muted">Aucune routine pour l’instant.</p>}
      {items && items.length > 0 && (
        <ul className="set-reports-list">
          {items.map(r => (
            <li key={r.id}>
              <div className="set-reports-top"><strong>{r.name}</strong>{r.triggers.map(t => <q key={t}>{t}</q>)}</div>
              <ol className="set-routine-steps">{r.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
              <div className="set-reports-meta">
                <span>{r.runs} lancement(s)</span>
                {r.last_run_at && <span>dernier : {fmtDateTime(r.last_run_at)}</span>}
              </div>
              <div className="set-routine-actions">
                <button className="btn btn-sm btn-primary" onClick={() => launch(r)} disabled={!online}><Play size={13} /> Lancer</button>
                {confirm === r.id ? (
                  <span className="set-confirm" role="alertdialog" aria-label="Confirmer la suppression">
                    <span>Supprimer cette routine ?</span>
                    <button className="btn btn-sm btn-danger" onClick={() => void remove(r.id)}>Oui, supprimer</button>
                    <button className="btn btn-sm" onClick={() => setConfirm(null)}>Non</button>
                  </span>
                ) : <button className="btn btn-sm btn-ghost" onClick={() => setConfirm(r.id)} disabled={!online}><Trash2 size={13} /> Supprimer</button>}
              </div>
              <ScheduleEditor routine={r} online={online} onSaved={reloadAll} onError={text => setNote({ kind: 'err', text })} onOpen={id => void see(id)} />
            </li>
          ))}
        </ul>
      )}
      <div className="set-routine-form">
        <h4 className="set-h4"><Plus size={13} /> Nouvelle routine</h4>
        <label>Nom<input className="input" value={name} onChange={e => setName(e.target.value)} maxLength={60} placeholder="Matin" /></label>
        <label>Phrases de lancement (une par ligne, facultatif)<textarea className="input" rows={2} value={triggers} onChange={e => setTriggers(e.target.value)} placeholder="Bonjour AURORA, on commence" /></label>
        <label>Étapes (une demande par ligne, 10 au plus)<textarea className="input" rows={4} value={steps} onChange={e => setSteps(e.target.value)} placeholder={'Quelle heure est-il ?\nMétéo à Paris\nQuoi de neuf aujourd’hui ?'} /></label>
        <div className="set-routine-actions">
          <button className="btn btn-sm" onClick={recent} disabled={!chat.some(e => e.role === 'user')} title="Pré-remplit les étapes avec vos 5 dernières demandes de la conversation">Reprendre mes dernières demandes</button>
          <button className="btn btn-sm btn-primary" onClick={() => void create()} disabled={!online || !name.trim() || !lines(steps).length}>Enregistrer la routine</button>
        </div>
        <p className="set-small">Une étape ne peut pas lancer une autre routine. Ce qui supprime, envoie ou dépense demande toujours votre accord, étape par étape — aussi quand la routine part toute seule à son heure : l’étape attend alors votre accord, et rien ne part vers le cloud sans vous.</p>
      </div>
    </Panel>
  );
}
