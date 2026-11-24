// Tâches: the "organisation des tâches" flow (live + replay of real runs), the run to follow,
// the run list, the starter's manual tasks and the real event history.
// 11/11/2026 : le panneau principal a deux vues, « Flux » et « Carte des outils » (components/toolmap) ; pas de
// nouvel onglet. La carte prend la place du flux, la fiche d'un outil celle de « Suivre une requête ».
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Activity, CheckCircle2, Clock3, Cloud, Hexagon, ListChecks, Play, Plus, Stethoscope, Workflow } from 'lucide-react';
import { api } from '../lib/api';
import { readPref, writePref } from '../lib/prefs';
import { useAurora } from '../lib/store';
import { useEventStream } from '../lib/stream';
import { ENGINE_LABELS, INTENT_LABELS, fmtMs, fmtPct, fmtUsd, relativeTime } from '../lib/format';
import type { ActivityEvent, TaskItem, TaskStatus, Usage } from '../lib/types';
import { Badge, EmptyState, Panel, Spinner, Unavailable } from '../components/ui';
import TaskTimeline from '../components/TaskTimeline';
import FlowReplay, { type ReplayRequest } from '../components/flow/FlowReplay';
import RunInspector from '../components/flow/RunInspector';
import { useRunJourneys } from '../components/flow/useRunJourneys';
import ArtefactsPanel from '../components/artefacts/ArtefactsPanel';
import { RemindersPanel } from '../components/Reminders';
import ToolMapView from '../components/toolmap/ToolMapView';
import ToolMapPanel from '../components/toolmap/ToolMapPanel';
import { useToolMap } from '../components/toolmap/useToolMap';
import { askOptions } from '../components/chat/prefs';
import { RUN_STATUS_LABELS, dayStats, statusTone, type RunStatus } from '../components/flow/model';
import './tasks.css';

type StatusFilter = 'all' | 'active' | 'waiting_permission' | 'done' | 'error' | 'stopped';
const STATUS_FILTERS: Record<StatusFilter, string> = {
  all: 'Tous les statuts', active: 'En cours', waiting_permission: "En attente d'autorisation",
  done: 'Terminés', error: 'Erreurs', stopped: 'Annulés / interrompus',
};
const matchesStatus = (s: string, f: StatusFilter): boolean => {
  switch (f) {
    case 'all': return true;
    case 'active': return s === 'running' || s === 'pending';
    case 'stopped': return s === 'cancelled' || s === 'interrupted' || s === 'paused';
    default: return s === f;
  }
};
const MANUAL_LABELS: Record<string, string> = { pending: 'À faire', running: 'En cours', done: 'Terminée', error: 'Erreur', cancelled: 'Annulée', paused: 'En pause', interrupted: 'Interrompue', waiting_permission: 'En attente' };
const toneOf = (s: string) => {
  const t = statusTone(s as RunStatus);
  return t === 'grey' ? 'default' : t;
};

/** Ce que montre le panneau principal : le flux des requêtes, ou la carte des outils. Le choix est gardé. */
type MainView = 'flow' | 'tools';
const MAIN_VIEW_KEY = 'aurora.tasks.view';
const MAIN_VIEWS = ['flow', 'tools'] as const;

export default function TasksPage() {
  const { ask, busy, health } = useAurora();
  const data = useRunJourneys();
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [replayRequest, setReplayRequest] = useState<ReplayRequest | null>(null);
  const seq = useRef(0);

  // ---------- main view: flow, or tool map ----------
  const [mainView, setMainViewState] = useState<MainView>(() => readPref(MAIN_VIEW_KEY, MAIN_VIEWS, 'flow'));
  const setMainView = useCallback((v: MainView) => { setMainViewState(v); writePref(MAIN_VIEW_KEY, v); }, []);
  const toolsView = mainView === 'tools';
  // Read only when the map is shown, and read again when the real state the app already measures changes
  // (mode, Internet, terminal, mouse and keyboard: /api/health, every 15 s).
  const caps = health?.capabilities;
  const toolMap = useToolMap(toolsView, caps ? `${caps.mode}|${caps.internet}|${caps.shell}|${caps.pc_control}` : '');
  const [selectedTool, setSelectedTool] = useState<string | null>(null);

  // ---------- usage (cloud cost of the day) ----------
  const [usage, setUsage] = useState<Usage | null>(null);
  const [usageError, setUsageError] = useState(false);
  const loadUsage = useCallback(() => {
    api.usage().then(u => { setUsage(u); setUsageError(false); }).catch(() => { setUsage(null); setUsageError(true); });
  }, []);

  // ---------- event history ----------
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [eventsError, setEventsError] = useState('');
  const [activeEventId, setActiveEventId] = useState<number | null>(null);

  // ---------- manual tasks (ASTRA starter) ----------
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [tasksError, setTasksError] = useState('');
  const [newTask, setNewTask] = useState('');
  const [taskBusy, setTaskBusy] = useState<number | 'new' | null>(null);
  const loadTasks = useCallback(() => {
    api.tasks()
      .then(list => { setTasks(list.filter(t => (t.kind ?? 'manual') === 'manual')); setTasksError(''); })
      .catch(err => setTasksError(err instanceof Error ? err.message : 'Service local indisponible'));
  }, []);

  useEffect(() => {
    loadUsage();
    loadTasks();
    api.events({ limit: 120 })
      .then(list => { setEvents(list); setEventsError(''); })
      .catch(err => setEventsError(err instanceof Error ? err.message : 'Service local indisponible'));
  }, [loadTasks, loadUsage]);

  const timers = useRef<{ usage: number | null; tasks: number | null }>({ usage: null, tasks: null });
  useEffect(() => () => {
    if (timers.current.usage !== null) window.clearTimeout(timers.current.usage);
    if (timers.current.tasks !== null) window.clearTimeout(timers.current.tasks);
  }, []);
  useEventStream(event => {
    setEvents(prev => (prev.some(e => e.id === event.id) ? prev : [event, ...prev].slice(0, 120)));
    if (event.kind.startsWith('run.') || event.kind === 'model.result') {
      if (timers.current.usage !== null) window.clearTimeout(timers.current.usage);
      timers.current.usage = window.setTimeout(() => { timers.current.usage = null; loadUsage(); }, 800);
    }
    if (event.subject_type === 'task' && event.run_id === null) {
      if (timers.current.tasks !== null) window.clearTimeout(timers.current.tasks);
      timers.current.tasks = window.setTimeout(() => { timers.current.tasks = null; loadTasks(); }, 400);
    }
  });

  // ---------- stats on real runs ----------
  const stats = useMemo(() => dayStats(data.summaries), [data.summaries]);

  // ---------- following ----------
  const { details, refreshRun } = data;
  const select = useCallback((runId: number | null) => {
    setSelectedRunId(runId);
    if (runId !== null && !details.has(runId)) refreshRun(runId);
    // A run chosen in a list while the tool map is shown: back to the flow, where it is followed.
    if (runId !== null) setMainView('flow');
  }, [details, refreshRun, setMainView]);
  const replayAt = useCallback((at: number) => {
    seq.current += 1;
    setReplayRequest({ at, seq: seq.current });
  }, []);

  const [diagError, setDiagError] = useState('');
  const runDiagnostic = async () => {
    setDiagError('');
    // The diagnosis ends with an AI step: the viewer's engine preference applies to it.
    const run = await ask('Diagnostic du PC', { source: 'panel', options: askOptions({ intent: 'system.diagnose' }) });
    if (run) {
      select(run.task_id);
      data.refresh();
    } else {
      setDiagError('Diagnostic impossible : service local indisponible.');
    }
  };

  // ---------- run list ----------
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [intentFilter, setIntentFilter] = useState('');
  const intents = useMemo(() => [...new Set(data.summaries.map(r => r.intent).filter(Boolean))].sort(), [data.summaries]);
  const runs = useMemo(() => data.summaries
    .map(r => ({ r, status: data.journeys.get(r.id)?.status ?? r.status }))
    .filter(({ r, status }) => matchesStatus(status, statusFilter) && (!intentFilter || r.intent === intentFilter))
    .slice(0, 80),
  [data.summaries, data.journeys, data.version, statusFilter, intentFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------- manual task actions ----------
  const addTask = async (e: FormEvent) => {
    e.preventDefault();
    const title = newTask.trim();
    if (!title) return;
    setTaskBusy('new');
    try {
      await api.addTask(title);
      setNewTask('');
      loadTasks();
    } catch (err) {
      setTasksError(err instanceof Error ? err.message : 'Création impossible');
    } finally {
      setTaskBusy(null);
    }
  };
  const setStatus = async (id: number, status: TaskStatus) => {
    setTaskBusy(id);
    try {
      await api.setTaskStatus(id, status);
      loadTasks();
    } catch (err) {
      setTasksError(err instanceof Error ? err.message : 'Mise à jour impossible');
    } finally {
      setTaskBusy(null);
    }
  };

  const pickEvent = (id: number) => {
    setActiveEventId(id);
    const ev = events.find(e => e.id === id);
    if (ev?.run_id) select(ev.run_id);
  };

  const selectedJourney = selectedRunId !== null ? data.journeys.get(selectedRunId) : undefined;
  const selectedDetail = selectedRunId !== null ? data.details.get(selectedRunId) : undefined;

  return (
    <div className="tasks-page">
      <div className="page-title">
        <div>
          <div className="eyebrow">AURORA · organisation des tâches</div>
          <h1>Tâches</h1>
          <p>Chaque point est une vraie requête qui passe de station en station, dans l'ordre de ses événements enregistrés.</p>
        </div>
        <div className="tasks-title-actions">
          {diagError && <span className="tasks-inline-error" role="alert">{diagError}</span>}
          <button type="button" className="btn btn-primary" onClick={() => void runDiagnostic()} disabled={busy} title="Passe par le moteur de décision (intention system.diagnose)">
            {busy ? <Spinner size={14} /> : <Stethoscope size={16} />} Diagnostic du PC
          </button>
        </div>
      </div>

      <section className="tasks-stats" aria-label="Statistiques du jour, calculées sur les requêtes réelles">
        <div className="tasks-stat">
          <span className="tasks-stat-label"><Activity size={14} /> Requêtes du jour</span>
          <strong>{data.error ? '—' : stats.runsToday}</strong>
          <small>{data.error ? 'indisponible' : stats.inProgress ? `${stats.inProgress} en cours` : 'aucune en cours'}</small>
        </div>
        <div className="tasks-stat">
          <span className="tasks-stat-label"><CheckCircle2 size={14} /> Taux de réussite</span>
          <strong>{data.error || stats.successRate === null ? '—' : fmtPct(stats.successRate * 100)}</strong>
          <small>{data.error ? 'indisponible' : stats.finished ? `${stats.succeeded} / ${stats.finished} terminées` : 'aucune requête terminée'}</small>
        </div>
        <div className="tasks-stat">
          <span className="tasks-stat-label"><Clock3 size={14} /> Durée p50</span>
          <strong>{data.error ? '—' : fmtMs(stats.p50)}</strong>
          <small>{data.error ? 'indisponible' : 'médiane du jour'}</small>
        </div>
        <div className="tasks-stat">
          <span className="tasks-stat-label"><Clock3 size={14} /> Durée p95</span>
          <strong>{data.error ? '—' : fmtMs(stats.p95)}</strong>
          <small>{data.error ? 'indisponible' : stats.finished ? `sur ${stats.finished} requête${stats.finished > 1 ? 's' : ''}` : '—'}</small>
        </div>
        <div className="tasks-stat">
          <span className="tasks-stat-label"><Cloud size={14} /> Coût cloud du jour</span>
          <strong>{usage ? fmtUsd(usage.today_usd) : '—'}</strong>
          <small>{usage ? `budget ${fmtUsd(usage.budget_usd)} · reste ${fmtUsd(usage.remaining_usd)}` : usageError ? 'indisponible' : 'chargement…'}</small>
        </div>
      </section>

      <div className="tasks-main" data-following={selectedRunId !== null}>
        <Panel className="tasks-flow-panel" icon={toolsView ? <Hexagon size={18} /> : <Workflow size={18} />} title="Organisation des tâches"
          subtitle={toolsView
            ? 'Les outils qu’AURORA peut utiliser, rangés par grande famille autour d’elle'
            : 'Entrée → Décision → Outils · IA locale · Claude · Autorisation → Vérification → Réponse → Mémoire'}
          actions={(
            <div className="flow-seg" role="group" aria-label="Vue du panneau">
              <button type="button" aria-pressed={!toolsView} onClick={() => setMainView('flow')}>Flux</button>
              <button type="button" aria-pressed={toolsView} onClick={() => setMainView('tools')}>Carte des outils</button>
            </div>
          )}>
          {/* The flow stays mounted while the map is shown: its mode, window and replay position are kept. */}
          <div hidden={toolsView}>
            <FlowReplay data={data} selectedRunId={selectedRunId} onSelectRun={select} replayRequest={replayRequest} />
          </div>
          {toolsView && <ToolMapView state={toolMap} selected={selectedTool} onSelect={setSelectedTool} />}
        </Panel>
        <aside className="panel tasks-follow-panel" aria-label={toolsView ? 'Outils d’AURORA' : 'Suivre une requête'}>
          {toolsView ? (
            <ToolMapPanel state={toolMap} selected={selectedTool} onSelect={setSelectedTool} />
          ) : selectedRunId !== null ? (
            <RunInspector key={selectedRunId} runId={selectedRunId} journey={selectedJourney} detail={selectedDetail}
              onClose={() => setSelectedRunId(null)} onReplay={replayAt}
              onChanged={() => { data.refreshRun(selectedRunId); data.refresh(); }} />
          ) : (
            <div className="tasks-follow-empty">
              <h3>Suivre une requête</h3>
              <p>Cliquez sur un point du flux ou sur une requête de la liste : sa cascade d'étapes, sa décision, ses vérifications et ses autorisations s'affichent ici.</p>
            </div>
          )}
        </aside>
      </div>

      <div className="tasks-lower">
        <Panel icon={<ListChecks size={18} />} title="Requêtes" subtitle={data.error ? 'indisponibles' : `${data.summaries.length} plus récentes`}
          actions={(
            <div className="tasks-filters">
              <select className="select" value={statusFilter} onChange={e => setStatusFilter(e.target.value as StatusFilter)} aria-label="Filtrer par statut">
                {(Object.keys(STATUS_FILTERS) as StatusFilter[]).map(f => <option key={f} value={f}>{STATUS_FILTERS[f]}</option>)}
              </select>
              <select className="select" value={intentFilter} onChange={e => setIntentFilter(e.target.value)} aria-label="Filtrer par intention">
                <option value="">Toutes les intentions</option>
                {intents.map(i => <option key={i} value={i}>{INTENT_LABELS[i] ?? i}</option>)}
              </select>
            </div>
          )}>
          {data.error ? <Unavailable what="Requêtes" reason={data.error} />
            : data.loading ? <EmptyState><Spinner /> Chargement…</EmptyState>
              : runs.length === 0 ? <EmptyState>{data.summaries.length ? 'Aucune requête ne correspond à ces filtres.' : 'Aucune requête pour le moment. Posez une question ou lancez le diagnostic du PC.'}</EmptyState>
                : (
                  <ul className="tasks-runs">
                    {runs.map(({ r, status }) => (
                      <li key={r.id}>
                        <button type="button" className="tasks-run" aria-pressed={selectedRunId === r.id} onClick={() => select(r.id)}>
                          <span className="tasks-run-dot" data-tone={statusTone(status as RunStatus)} aria-hidden="true" />
                          <span className="tasks-run-copy">
                            <strong title={r.input_text || r.title}>{r.input_text || r.title}</strong>
                            <small>
                              {RUN_STATUS_LABELS[status as RunStatus] ?? status}
                              {r.intent ? ` · ${INTENT_LABELS[r.intent] ?? r.intent}` : ''}
                              {r.engine ? ` · ${ENGINE_LABELS[r.engine] ?? r.engine}` : ''}
                              {r.duration_ms !== null ? ` · ${fmtMs(r.duration_ms)}` : ''}
                              {r.cost_usd > 0 ? ` · ${fmtUsd(r.cost_usd)}` : ''}
                            </small>
                          </span>
                          <time dateTime={r.created_at}>{relativeTime(r.created_at)}</time>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
        </Panel>

        <RemindersPanel />

        <Panel icon={<Plus size={18} />} title="Tâches manuelles" subtitle="Créer, démarrer, terminer">
          <form className="tasks-new" onSubmit={e => void addTask(e)}>
            <input className="input" value={newTask} onChange={e => setNewTask(e.target.value)} placeholder="Nouvelle tâche…" aria-label="Titre de la nouvelle tâche" maxLength={200} />
            <button type="submit" className="btn" disabled={!newTask.trim() || taskBusy === 'new'}>{taskBusy === 'new' ? <Spinner size={12} /> : <Plus size={14} />} Ajouter</button>
          </form>
          {tasksError && <p className="tasks-inline-error" role="alert">{tasksError}</p>}
          {!tasksError && tasks.length === 0 ? <EmptyState>Aucune tâche manuelle.</EmptyState> : (
            <ul className="tasks-manual">
              {tasks.slice(0, 40).map(t => (
                <li key={t.id}>
                  <span className="tasks-manual-title" title={t.title}>{t.title}</span>
                  <Badge tone={toneOf(t.status)}>{MANUAL_LABELS[t.status] ?? t.status}</Badge>
                  {t.status === 'pending' && (
                    <button type="button" className="btn btn-sm" disabled={taskBusy === t.id} onClick={() => void setStatus(t.id, 'running')}><Play size={12} /> Démarrer</button>
                  )}
                  {t.status === 'running' && (
                    <button type="button" className="btn btn-sm" disabled={taskBusy === t.id} onClick={() => void setStatus(t.id, 'done')}><CheckCircle2 size={12} /> Terminer</button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel icon={<Clock3 size={18} />} title="Historique" subtitle="Événements réels enregistrés">
          {eventsError ? <Unavailable what="Événements" reason={eventsError} />
            : <TaskTimeline events={events} activeId={activeEventId} onPick={pickEvent} />}
        </Panel>

        <ArtefactsPanel />
      </div>
    </div>
  );
}
