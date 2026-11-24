// Memory page: the animated "visual brain" (or the simple React Flow view), the replay bar,
// and a side panel (detail + actions, search, add, journal). All data comes from the API;
// every animation is triggered by a real persisted event.
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Brain, ChevronDown, ChevronUp, CircleDot, History, Network, Plus, RotateCw, Search, Shapes, SquareStack } from 'lucide-react';
import { api } from '../lib/api';
import { readPref, writePref } from '../lib/prefs';
import { useAurora } from '../lib/store';
import { useEventStream, useStreamStatus } from '../lib/stream';
import type { ActivityEvent, MemoryGraphData, MemoryHit, MemoryItem, TaskItem } from '../lib/types';
import { Spinner, StatusDot, Unavailable } from '../components/ui';
import BrainGraph, { type BrainHandle } from '../components/BrainGraph';
import TaskTimeline from '../components/TaskTimeline';
import ReplayBar from '../components/brain/ReplayBar';
import MemoryDetail, { type Selection } from '../components/brain/MemoryDetail';
import MemorySearch from '../components/brain/MemorySearch';
import MemoryAddForm from '../components/brain/MemoryAddForm';
import { errorText, useDebounced } from '../components/brain/hooks';
import { useReducedMotion } from '../components/sim/hooks';
import { cutoffAt, loadBrainEvents, sortBrainEvents, statusesAt, useReplay } from '../components/brain/replay';
import { LEVELS, LEVEL_BY_ID, ROUTINE_COLOR, TOOL_COLOR, isBrainEvent, isLevelId, nodeLevel, nodeNum, type LevelId } from '../components/brain/model';
import './memory.css';

const MemoryGraph = lazy(() => import('../components/MemoryGraph'));

type Tab = 'detail' | 'search' | 'add' | 'journal';
type View = 'brain' | 'simple';
/** Brain view layout: computed rings (default) or the original lobes, kept as the fallback. */
type Layout = 'rings' | 'lobes';

const VIEW_KEY = 'aurora.memory.view';
const LAYOUT_KEY = 'aurora.memory.layout';
const ROTATION_KEY = 'aurora.memory.rotation';
/** Rings: the service maximum; lobes: the service default (120), where the force layout stays fluid. */
const RINGS_LIMIT = 1000;

const readView = (): View => readPref(VIEW_KEY, ['brain', 'simple'] as const, 'brain');

const zeroCounts = (): Record<LevelId, number> => ({ immediate: 0, persistent: 0, semantic: 0, actions: 0 });

export default function MemoryPage() {
  const { navigate } = useAurora();
  const reduced = useReducedMotion();
  const streamUp = useStreamStatus();
  const brain = useRef<BrainHandle>(null);

  const [view, setViewState] = useState<View>(readView);
  const [layout, setLayoutState] = useState<Layout>(() => readPref(LAYOUT_KEY, ['rings', 'lobes'] as const, 'rings'));
  const [rotation, setRotationState] = useState(() => readPref(ROTATION_KEY, ['on', 'off'] as const, 'on') === 'on');
  const [simpleMode, setSimpleMode] = useState<'memories' | 'tasks'>('memories');
  const [graph, setGraph] = useState<MemoryGraphData | null>(null);
  /** Layout the current `graph` was loaded for (rings: limit 1000; lobes: the default 120). */
  const [graphLayout, setGraphLayout] = useState<Layout | null>(null);
  const [graphError, setGraphError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [memoriesError, setMemoriesError] = useState('');
  const [runs, setRuns] = useState<TaskItem[]>([]);
  const [history, setHistory] = useState<ActivityEvent[]>([]);
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('detail');
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<MemoryHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [missedLive, setMissedLive] = useState(0);
  const [lastEvent, setLastEvent] = useState<ActivityEvent | null>(null);
  const [legendOpen, setLegendOpen] = useState(true);

  const viewRef = useRef(view);
  viewRef.current = view;
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const refreshSeq = useRef(0);

  const setView = (v: View) => {
    setViewState(v);
    writePref(VIEW_KEY, v);
  };
  const setLayout = (l: Layout) => {
    setLayoutState(l);
    writePref(LAYOUT_KEY, l);
  };
  const setRotation = (on: boolean) => {
    setRotationState(on);
    writePref(ROTATION_KEY, on ? 'on' : 'off');
  };

  // ------------------------------------------------------------ data
  const refresh = useCallback(async () => {
    const forLayout = layoutRef.current;
    const ticket = ++refreshSeq.current;
    const [g, m] = await Promise.allSettled([api.memoryGraph(forLayout === 'rings' ? RINGS_LIMIT : undefined), api.memories()]);
    if (ticket !== refreshSeq.current) return;   // a newer refresh (or a layout switch) supersedes this answer
    if (g.status === 'fulfilled') { setGraph(g.value); setGraphLayout(forLayout); setGraphError(''); }
    else setGraphError(errorText(g.reason));
    if (m.status === 'fulfilled') { setMemories(m.value); setMemoriesError(''); }
    else setMemoriesError(errorText(m.reason));
    if (viewRef.current === 'simple') {
      try { setRuns(await api.runs(50)); } catch { setRuns([]); }
    }
    setLoaded(true);
  }, []);
  const scheduleRefresh = useDebounced(() => { void refresh(); }, 450);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const list = await loadBrainEvents(500);
      setHistory(list); setHistoryError('');
      return list;
    } catch (err) {
      setHistoryError(errorText(err));
      return null;
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => { void loadHistory(); }, [loadHistory]);
  useEffect(() => { void refresh(); }, [layout, refresh]);   // on mount, and with the limit of the new layout
  useEffect(() => { if (view === 'simple') void refresh(); }, [view, refresh]);

  // ------------------------------------------------------------ replay
  const replay = useReplay((event, speed) => {
    brain.current?.play(event, { speed, live: false });
    setLastEvent(event);
  });

  const restartReplay = async () => {
    brain.current?.clearEffects();
    const list = await loadHistory();
    if (list) { setMissedLive(0); replay.start(list); }
  };

  const exitReplay = () => {
    replay.stop();
    brain.current?.clearEffects();
    setMissedLive(0);
    void refresh();
  };

  const pickEvent = (id: number) => {
    const list = replay.active ? replay.events : history;
    const idx = list.findIndex(e => e.id === id);
    if (idx < 0) return;
    if (!replay.active) setMissedLive(0);
    replay.seek(idx, replay.active ? undefined : list);
  };

  // ------------------------------------------------------------ live events
  useEventStream(event => {
    if (event.subject_type === 'conversation') { scheduleRefresh(); return; }
    if (!isBrainEvent(event)) return;
    setHistory(h => (h.some(x => x.id === event.id) ? h : sortBrainEvents([...h, event]).slice(-600)));
    scheduleRefresh();
    if (replay.active) { setMissedLive(c => c + 1); return; }
    brain.current?.play(event, { live: true });
    setLastEvent(event);
  });

  // ------------------------------------------------------------ derived
  const memoriesById = useMemo(() => new Map(memories.map(m => [m.id, m])), [memories]);

  const memNodeIds = useMemo(() => {
    const map = new Map<number, string>();
    for (const n of graph?.nodes ?? []) {
      if (n.type !== 'memory') continue;
      const num = nodeNum(n.id);
      if (num !== null) map.set(num, n.id);
    }
    return map;
  }, [graph]);

  const dataNodeCount = useMemo(() => (graph?.nodes ?? []).filter(n => n.type !== 'core' && n.type !== 'level').length, [graph]);

  /** Nodes received per level (what the graph shows). */
  const visibleCounts = useMemo(() => {
    const counts = zeroCounts();
    for (const n of graph?.nodes ?? []) {
      if (n.type === 'core' || n.type === 'level') continue;
      const l = nodeLevel(n);
      if (l) counts[l]++;
    }
    return counts;
  }, [graph]);
  /** True totals in the database (service `totals`); the nodes received for an older service. */
  const levelCounts: Record<LevelId, number> = graph?.totals ?? visibleCounts;
  const ringsShown = view === 'brain' && layout === 'rings';

  const categories = useMemo(() => [...new Set(memories.map(m => m.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'fr')), [memories]);

  const highlight = useMemo(() => {
    if (!hits?.length) return null;
    const best = Math.max(...hits.map(h => h.score), 1e-9);
    const map = new Map<string, number>();
    for (const h of hits) {
      const id = memNodeIds.get(h.memory.id);
      if (id) map.set(id, Math.max(0, Math.min(1, h.score / best)));
    }
    return map;
  }, [hits, memNodeIds]);

  const selection = useMemo<Selection | null>(() => {
    if (!selectedId) return null;
    if (selectedId === 'core') return { id: selectedId, type: 'core', node: null, memory: null, level: null };
    if (selectedId.startsWith('level:')) {
      const tail = selectedId.slice(6);
      return isLevelId(tail) ? { id: selectedId, type: 'level', node: null, memory: null, level: tail } : null;
    }
    const node = graph?.nodes.find(n => n.id === selectedId) ?? null;
    if (node && (node.type === 'core' || node.type === 'level')) {
      const level = nodeLevel(node);
      return { id: selectedId, type: node.type, node, memory: null, level };
    }
    const isMemory = node ? node.type === 'memory' : selectedId.startsWith('memory#');
    const num = nodeNum(selectedId);
    const memory = isMemory && num !== null ? memoriesById.get(num) ?? hits?.find(h => h.memory.id === num)?.memory ?? null : null;
    if (!node && !memory) return null;
    return { id: selectedId, type: node?.type ?? 'memory', node, memory, level: node ? nodeLevel(node) : 'persistent' };
  }, [selectedId, graph, memoriesById, hits]);

  const replayStatuses = useMemo(() => (replay.active ? statusesAt(replay.events, replay.index) : null), [replay.active, replay.events, replay.index]);
  const cutoffMs = replay.active ? cutoffAt(replay.events, replay.index) : null;

  const timelineEvents = useMemo(() => (replay.active ? replay.events : history).slice().reverse(), [replay.active, replay.events, history]);

  const activeEventId = replay.active ? replay.current?.id ?? null : lastEvent?.id ?? null;

  // Keep the replayed event visible in the journal.
  const journalRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (tab !== 'journal' || !replay.active) return;
    journalRef.current?.querySelector('.timeline-row.selected')?.scrollIntoView({ block: 'nearest' });
  }, [tab, replay.active, activeEventId]);

  // ------------------------------------------------------------ actions
  const runSearch = async (q: string) => {
    setQuery(q); setSearching(true); setSearchError('');
    try { setHits(await api.searchMemories(q, 12)); }
    catch (err) { setHits(null); setSearchError(errorText(err)); }
    finally { setSearching(false); }
  };

  const pickHit = (hit: MemoryHit) => {
    const nodeId = memNodeIds.get(hit.memory.id);
    setSelectedId(nodeId ?? `memory#${hit.memory.id}`);
    if (nodeId) brain.current?.focus(nodeId);
    setTab('detail');
  };

  const select = (id: string | null) => {
    setSelectedId(id);
    if (id) setTab('detail');
  };

  const selectFromPanel = (id: string) => {
    setSelectedId(id);
    brain.current?.focus(id);
  };

  const empty = loaded && !graphError && dataNodeCount === 0;

  return (
    <div className="mem-page">
      <div className="page-title mem-title">
        <div>
          <div className="eyebrow">AURORA · Mémoire</div>
          <h1>Cerveau visuel</h1>
          <p>Le graphe animé est une visualisation de cette mémoire, et non son système de stockage.</p>
        </div>
        <div className="mem-title-right">
          <span className="badge" title={streamUp ? 'Les animations suivent les événements réels en direct' : 'Flux temps réel déconnecté : reconnexion automatique'}>
            <StatusDot ok={streamUp} /> {streamUp ? 'Flux en direct' : 'Flux déconnecté'}
          </span>
          {replay.active && <span className="badge badge-violet">Relecture</span>}
          {reduced && <span className="badge" title="Préférence système « réduire les animations » active">Animations réduites</span>}
          <div className="mem-toggle" role="radiogroup" aria-label="Type de vue">
            <button type="button" role="radio" aria-checked={view === 'brain'} className={view === 'brain' ? 'on' : ''} onClick={() => setView('brain')}><Brain size={15} /> Vue cerveau</button>
            <button type="button" role="radio" aria-checked={view === 'simple'} className={view === 'simple' ? 'on' : ''} onClick={() => setView('simple')}><Network size={15} /> Vue simple</button>
          </div>
          {view === 'brain' && (
            <div className="mem-toggle" role="radiogroup" aria-label="Disposition de la vue cerveau">
              <button type="button" role="radio" aria-checked={layout === 'rings'} className={layout === 'rings' ? 'on' : ''} onClick={() => setLayout('rings')}
                title="Anneaux : disque mémoire par niveau et catégorie, routines, outils (jusqu'à 1 000 souvenirs)"><CircleDot size={15} /> Anneaux</button>
              <button type="button" role="radio" aria-checked={layout === 'lobes'} className={layout === 'lobes' ? 'on' : ''} onClick={() => setLayout('lobes')}
                title="Lobes : la disposition d'origine, quatre lobes autour du cœur (120 éléments)"><Shapes size={15} /> Lobes</button>
            </div>
          )}
          {ringsShown && (
            <button type="button" className={`mem-switch ${rotation && !reduced ? 'on' : ''}`} aria-pressed={rotation && !reduced} disabled={reduced}
              onClick={() => setRotation(!rotation)}
              title={reduced ? 'Rotation coupée : préférence système « réduire les animations »' : rotation ? 'Arrêter la rotation lente des anneaux' : 'Faire tourner lentement les anneaux'}>
              <RotateCw size={14} /> Rotation
            </button>
          )}
        </div>
      </div>

      <div className="mem-layout">
        <section className="mem-stage panel" aria-label={view === 'brain' ? 'Cerveau visuel de la mémoire' : 'Vue simple de la mémoire'}>
          <div className="mem-canvas-area">
            {view === 'brain' ? (
              graphError && !graph ? (
                <div className="mem-center"><Unavailable what="Graphe mémoire" reason={graphError} /></div>
              ) : (
                <>
                  <BrainGraph
                    key={layout}
                    ref={brain}
                    layout={layout}
                    rotate={rotation}
                    data={graphLayout === layout ? graph : null}
                    memories={memoriesById}
                    selectedId={selection?.node?.id ?? selection?.id ?? null}
                    onSelect={select}
                    highlight={highlight}
                    cutoffMs={cutoffMs}
                    statusOverride={replayStatuses}
                    reducedMotion={reduced}
                    insetLeft={legendOpen ? 300 : 0}
                  />
                  {(!loaded || (graphLayout !== layout && !graphError)) && <div className="mem-center mem-loading"><Spinner size={22} /> Chargement de la mémoire…</div>}
                  {empty && (
                    <div className="mem-center mem-empty">
                      <strong>Aucune mémoire enregistrée pour le moment.</strong>
                      <span>Ajoutez un souvenir ou demandez à AURORA de retenir quelque chose : son nœud apparaîtra ici avec l'événement réel.</span>
                      <button type="button" className="btn btn-sm" onClick={() => setTab('add')}><Plus size={14} /> Ajouter un souvenir</button>
                    </div>
                  )}
                  {graphError && graph && <div className="mem-stale" role="status">Actualisation impossible — {graphError}</div>}
                  <div className={`mem-legend ${legendOpen ? 'open' : ''}`}>
                    <button type="button" className="mem-legend-head" onClick={() => setLegendOpen(o => !o)} aria-expanded={legendOpen}>
                      Niveaux de mémoire {legendOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>
                    <ul>
                      {LEVELS.map(l => (
                        <li key={l.id}>
                          <button type="button" className="mem-legend-row" onClick={() => selectFromPanel(`level:${l.id}`)} title={l.description}>
                            <span className="mem-dot" style={{ background: l.color, boxShadow: `0 0 8px ${l.color}` }} />
                            <span className="mem-legend-text"><strong>{l.label}</strong>{legendOpen && <small>{l.description}</small>}</span>
                            <span className="mem-legend-count mono" aria-label={`${levelCounts[l.id]} éléments`}
                              title={graph ? `${levelCounts[l.id]} en base · ${visibleCounts[l.id]} dans le graphe` : undefined}>{graph ? levelCounts[l.id] : '—'}</span>
                          </button>
                        </li>
                      ))}
                      {ringsShown && ([
                        ['Routines', 'Routines enregistrées : une impulsion quand l\'une est lancée.', ROUTINE_COLOR, graph?.routines?.length],
                        ['Outils', 'Outils du registre (hexagones), plus gros selon leurs utilisations réelles.', TOOL_COLOR, graph?.tools?.length],
                      ] as const).map(([name, text, color, count]) => (
                        <li key={name}>
                          <div className="mem-legend-row" title={count === undefined ? 'Le service ne fournit pas encore cette information.' : text}>
                            <span className="mem-dot" style={{ background: color, boxShadow: `0 0 8px ${color}` }} />
                            <span className="mem-legend-text"><strong>{name}</strong>{legendOpen && <small>{count === undefined ? 'Indisponible avec ce service.' : text}</small>}</span>
                            <span className="mem-legend-count mono">{count ?? '—'}</span>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                </>
              )
            ) : (
              <div className="mem-simple">
                <div className="mem-simple-switch" role="radiogroup" aria-label="Contenu de la vue simple">
                  <button type="button" role="radio" aria-checked={simpleMode === 'memories'} className={simpleMode === 'memories' ? 'on' : ''} onClick={() => setSimpleMode('memories')}><SquareStack size={14} /> Mémoires</button>
                  <button type="button" role="radio" aria-checked={simpleMode === 'tasks'} className={simpleMode === 'tasks' ? 'on' : ''} onClick={() => { setSimpleMode('tasks'); void refresh(); }}><History size={14} /> Tâches</button>
                </div>
                {memoriesError && simpleMode === 'memories' ? <div className="mem-center"><Unavailable what="Mémoires" reason={memoriesError} /></div> : (
                  <Suspense fallback={<div className="mem-center"><Spinner size={22} /></div>}>
                    <MemoryGraph mode={simpleMode} memories={memories} tasks={runs} highlightedEvent={replay.active ? replay.current : lastEvent} />
                  </Suspense>
                )}
              </div>
            )}
          </div>
          <ReplayBar
            replay={replay}
            loading={historyLoading}
            error={historyError}
            available={history.length}
            missedLive={missedLive}
            onRestart={() => { void restartReplay(); }}
            onExit={exitReplay}
          />
        </section>

        <aside className="mem-side panel" aria-label="Panneau mémoire">
          <div className="mem-tabs" role="tablist" aria-label="Sections du panneau">
            {([
              ['detail', 'Détail', null],
              ['search', 'Recherche', <Search key="s" size={14} />],
              ['add', 'Ajouter', <Plus key="a" size={14} />],
              ['journal', 'Journal', <History key="j" size={14} />],
            ] as const).map(([id, label, icon]) => (
              <button key={id} type="button" role="tab" aria-selected={tab === id} className={`mem-tab ${tab === id ? 'on' : ''}`} onClick={() => setTab(id)}>
                {icon}{label}{id === 'search' && hits?.length ? <span className="mem-tab-count">{hits.length}</span> : null}
              </button>
            ))}
          </div>
          <div className="mem-side-body" role="tabpanel">
            {tab === 'detail' && (
              <MemoryDetail
                selection={selection}
                graph={graph}
                memories={memories}
                memoriesError={memoriesError}
                levelCounts={visibleCounts}
                levelTotals={graph?.totals ?? null}
                onSelect={selectFromPanel}
                onChanged={() => { void refresh(); }}
                onDeleted={id => { setSelectedId(null); setHits(h => h?.filter(x => x.memory.id !== id) ?? null); void refresh(); }}
                onOpenTasks={() => navigate('tasks')}
              />
            )}
            {tab === 'search' && (
              <MemorySearch
                query={query}
                hits={hits}
                searching={searching}
                error={searchError}
                inGraph={id => memNodeIds.has(id)}
                onSearch={q => { void runSearch(q); }}
                onClear={() => { setHits(null); setQuery(''); setSearchError(''); }}
                onPick={pickHit}
              />
            )}
            {tab === 'add' && <MemoryAddForm categories={categories} onSaved={() => scheduleRefresh()} />}
            {tab === 'journal' && (
              <div className="mem-timeline" ref={journalRef}>
                <p className="muted mem-small">
                  Événements réels enregistrés (mémoire et tâches). Cliquez sur une ligne pour la rejouer dans le cerveau.
                  {historyError ? ` Journal indisponible — ${historyError}` : ''}
                </p>
                <TaskTimeline events={timelineEvents} activeId={activeEventId} onPick={pickEvent} />
              </div>
            )}
          </div>
          <div className="mem-side-foot">
            {LEVELS.map(l => (
              <span key={l.id} className="mem-foot-count" title={graph ? `${l.label} : ${levelCounts[l.id]} en base · ${visibleCounts[l.id]} dans le graphe` : l.label}>
                <span className="mem-dot" style={{ background: LEVEL_BY_ID[l.id].color }} /> {l.short} <strong className="mono">{graph ? levelCounts[l.id] : '—'}</strong>
              </span>
            ))}
            {ringsShown && graph?.routines && (
              <span className="mem-foot-count" title="Routines enregistrées"><span className="mem-dot" style={{ background: ROUTINE_COLOR }} /> Routines <strong className="mono">{graph.routines.length}</strong></span>
            )}
            {ringsShown && graph?.tools && (
              <span className="mem-foot-count" title="Outils du registre"><span className="mem-dot" style={{ background: TOOL_COLOR }} /> Outils <strong className="mono">{graph.tools.length}</strong></span>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
