// C3 (16/11/2026, ID Studio IA : « continue la suite ») — reminders, timers and alarms on the screen. The service keeps them and
// says when their time comes (tasks/reminders.py, event « reminder.due »); nothing is counted or decided here.
//  - default export: the band above every page for the ones whose time has come, until « Fait ». Read aloud once, when
//    the answers are read aloud (the speaker of the bar) and the service has just said it. One that rang while the
//    interface was closed is shown at its next opening, not read: nobody may be there.
//  - RemindersPanel: the ones still to come, with what is left, in the page Tâches.
// A service started before C3 has no such route: nothing is shown, no dead control.
import { useCallback, useEffect, useState } from 'react';
import { AlarmClock, Check, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import { useEventStream } from '../lib/stream';
import { slotText } from '../lib/format';
import { speak } from '../lib/voice';
import type { Reminder, ReminderList } from '../lib/types';
import { getPrefs } from './chat/prefs';
import { Badge, Panel } from './ui';

const CHANGED = 'aurora:reminders-changed';
/** « 2026-11-16 18:00:00 », the PC's local time as the service writes it → milliseconds. */
const stampMs = (stamp: string) => new Date(stamp.replace(' ', 'T')).getTime();

/** What the band says, and what is read aloud: « Rappel : appeler Paul », « Minuteur de 10 minutes : terminé », « Réveil ». */
function title(r: Reminder): string {
  if (r.kind === 'timer') return `${r.text} : terminé`;
  if (r.kind === 'alarm') return r.text === 'Réveil' || r.text === 'Alarme' ? r.text : `Réveil : ${r.text}`;
  return r.text === 'Rappel' ? 'Rappel' : `Rappel : ${r.text}`;
}

/** What is left before a reminder: « 45 s », « 9 min 30 s », « 2 h 05 », « 3 j ». */
function left(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`;
  return `${Math.floor(s / 86400)} j`;
}

/** The reminders the service keeps, read again when one rings, when a request sets or cancels one (the tool
 *  « reminder.set »), and after « Fait », « Dans 10 min » or « Supprimer » anywhere on the screen. `at`: when this
 *  list was read, on this browser's clock — what is left is counted from the service's `now`, not from here. */
export function useReminders(enabled: boolean): { list: ReminderList | null; at: number } {
  const [state, setState] = useState<{ list: ReminderList | null; at: number }>({ list: null, at: 0 });
  const reload = useCallback(() => {
    api.reminders().then(list => setState({ list, at: Date.now() })).catch(() => setState({ list: null, at: 0 }));
  }, []);
  useEffect(() => { if (enabled) reload(); }, [enabled, reload]);
  useEffect(() => {
    if (!enabled) return;
    window.addEventListener(CHANGED, reload);
    return () => window.removeEventListener(CHANGED, reload);
  }, [enabled, reload]);
  useEventStream(event => {
    if (event.kind === 'reminder.due' || (event.kind === 'tool.result' && event.payload?.tool === 'reminder.set')) reload();
  });
  return state;
}

/** « Fait » / « Supprimer » and « Dans 10 min »: the person's click, sent to the service, then every list re-reads. */
async function answer(id: number, later: number | null, onError: (text: string) => void): Promise<void> {
  try {
    if (later === null) await api.removeReminder(id);
    else await api.snoozeReminder(id, later);
  } catch (err) { onError(err instanceof Error ? err.message : 'Rappel non modifié'); }
  window.dispatchEvent(new Event(CHANGED));
}

export default function ReminderSignal() {
  const { online } = useAurora();
  const { list } = useReminders(online);
  const [error, setError] = useState('');
  useEventStream(event => {
    if (event.kind !== 'reminder.due' || !getPrefs().tts) return;
    const rung = event.payload as Partial<Reminder>;
    // Read only what the service has just said: an event replayed later is shown by the band, not read again.
    if (!rung.text || !rung.fired_at || Math.abs(Date.now() - stampMs(rung.fired_at)) > 120_000) return;
    speak(`${title(rung as Reminder)}.`, { voiceName: getPrefs().voiceName });
  });
  const due = (list?.items ?? []).filter(r => r.state === 'due');
  if (!due.length) return null;
  return (
    <>
      {due.map(r => (
        <div key={r.id} className="info-banner" role="alert">
          <AlarmClock size={16} aria-hidden="true" />
          <span><strong>{title(r)}</strong> — prévu {slotText(r.due.slice(0, 16))}{error ? ` · ${error}` : ''}</span>
          <button type="button" className="btn btn-sm btn-primary" disabled={!online} onClick={() => void answer(r.id, null, setError)}><Check size={13} /> Fait</button>
          <button type="button" className="btn btn-sm btn-ghost" disabled={!online} onClick={() => void answer(r.id, 10, setError)}>Dans 10 min</button>
        </div>
      ))}
    </>
  );
}

/** Page Tâches: the reminders and timers kept by the service, the next one first, with what is left. */
export function RemindersPanel() {
  const { online } = useAurora();
  const { list, at } = useReminders(online);
  const [error, setError] = useState('');
  const [, tick] = useState(0);
  const waiting = (list?.items ?? []).some(r => r.state === 'waiting');
  useEffect(() => {
    if (!waiting) return;
    const t = window.setInterval(() => tick(n => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [waiting]);
  if (!list) return null;   // a service started before C3
  const now = stampMs(list.now) + (Date.now() - at);
  return (
    <Panel icon={<AlarmClock size={18} />} title="Rappels et minuteurs" subtitle={list.items.length ? `${list.items.length} en cours` : 'aucun en cours'}>
      {error && <p className="tasks-inline-error" role="alert">{error}</p>}
      {list.items.length === 0 ? (
        <p className="muted">Aucun rappel. Dites par exemple « rappelle-moi à 18 h d’appeler Paul » ou « minuteur de 10 minutes ».</p>
      ) : (
        <ul className="tasks-manual">
          {list.items.map(r => (
            <li key={r.id}>
              <span className="tasks-reminder-title" title={`${r.text} — ${slotText(r.due.slice(0, 16))}`}><span>{r.text}</span><small>{slotText(r.due.slice(0, 16))}</small></span>
              {r.state === 'due' ? <Badge tone="amber">a sonné</Badge> : <Badge tone="cyan">dans {left((stampMs(r.due) - now) / 1000)}</Badge>}
              <button type="button" className="btn btn-sm" disabled={!online} onClick={() => void answer(r.id, null, setError)}>
                {r.state === 'due' ? <><Check size={12} /> Fait</> : <><Trash2 size={12} /> Supprimer</>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
