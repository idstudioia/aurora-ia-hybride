// Global app state shared by every page: navigation, health, settings, live system metrics,
// the active conversation and pending permissions. One ask() for text, voice and panel buttons.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from './api';
import { isSearchTool, pageForTool } from './format';
import { useEventStream } from './stream';
import type { AskInput, AskOptions, GlobeCommand, GlobeOrder, Health, PageId, Permission, RunResult, SceneCommand, SceneContext, SceneOrder, Settings, SystemInfo } from './types';

export interface ChatEntry {
  id: string;
  role: 'user' | 'assistant' | 'note';
  text: string;
  at: string;
  run?: RunResult;
  pending?: boolean;
  source?: AskInput['source'];
  /** Stored message reopened by openConversation (no RunResult): its meta, e.g. `context` for the Contexte frame. */
  meta_json?: string;
}

export interface AskArgs { source?: AskInput['source']; options?: AskOptions; attachments?: AskInput['attachments']; silent?: boolean }

/** What AURORA is really doing, read from the steps of its runs (SSE): a search tool, a memory step, another tool. */
type Work = 'search' | 'memory' | 'action';
const NO_WORK: Record<Work, boolean> = { search: false, memory: false, action: false };
/** A memory step or a quick tool lasts a few milliseconds: its signal is held this long so that it can be seen on the
 *  home page (11/11/2026, the shapes of the core). A search is not held: it lasts by itself. */
const WORK_HOLD_MS: Record<Work, number> = { search: 0, memory: 2500, action: 2500 };

interface AuroraState {
  page: PageId;
  navigate: (page: PageId) => void;
  health: Health | null;
  online: boolean;
  refreshHealth: () => Promise<void>;
  settings: Settings | null;
  saveSettings: (fields: Partial<Settings>) => Promise<void>;
  system: SystemInfo | null;
  conversationId: number | null;
  chat: ChatEntry[];
  busy: boolean;
  ask: (text: string, args?: AskArgs) => Promise<RunResult | null>;
  applyRunUpdate: (run: RunResult) => void;
  newConversation: () => void;
  /** Make a stored conversation the active one (its messages are loaded into `chat`). Throws on failure. */
  openConversation: (id: number) => Promise<void>;
  chatOpen: boolean;
  setChatOpen: (open: boolean) => void;
  pendingPermissions: Permission[];
  refreshPermissions: () => Promise<void>;
  decidePermission: (id: number, decision: 'grant' | 'deny', scope?: 'once' | 'conversation') => Promise<RunResult | null>;
  lastError: string;
  /** Monde 3D (8/11/2026) : the last order for the globe (place, layer), null before any. */
  globeCommand: GlobeCommand | null;
  /** Open Monde 3D and give its globe this order, as a run's ui.globe does (08/11/2026: a click on the photo of a
   *  place under an answer). */
  showOnGlobe: (order: GlobeOrder) => void;
  /** True while a real search runs (web, actualités, météo…) : the globe shows its search animation. */
  searching: boolean;
  /** True for a moment (2.5 s at least) when a real memory step ran: AURORA searched or saved in its memory. */
  remembering: boolean;
  /** True for a moment (2.5 s at least) when a real tool ran that is not a search (PC, calculs, 3D, Studio, actions). */
  acting: boolean;
  /** Laboratoire 3D de l'accueil (8/11/2026) : the last order for the scene (run.ui.scene), null before any. */
  sceneCommand: SceneCommand | null;
  /** Open the home page and give its laboratory this order, as a run's ui.scene does (10/11/2026: a 3D model file
   *  joined with « Joindre un fichier » opens as a scene). */
  showScene: (order: SceneOrder) => void;
  /** The laboratory says which scene it shows (null: the core alone) ; sent as options.scene from the home page. */
  setSceneContext: (ctx: SceneContext | null) => void;
}

const PAGES: PageId[] = ['home', 'studio', 'chat', 'search', 'computer', 'world', 'simulations', 'projets', 'memory', 'tasks', 'settings'];
const pageFromHash = (): PageId => {
  const h = window.location.hash.replace(/^#\/?/, '') as PageId;
  return PAGES.includes(h) ? h : 'home';
};

const Ctx = createContext<AuroraState | null>(null);
let seq = 0;
const uid = () => `e${Date.now().toString(36)}${(seq++).toString(36)}`;

export function AuroraProvider({ children }: { children: ReactNode }) {
  const [page, setPage] = useState<PageId>(pageFromHash);
  const [health, setHealth] = useState<Health | null>(null);
  const [online, setOnline] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [chat, setChat] = useState<ChatEntry[]>([]);
  // Requests in flight (several can overlap: composer, bottom bar, voice, suggestions).
  const [inflight, setInflight] = useState(0);
  const busy = inflight > 0;
  const [chatOpen, setChatOpen] = useState(false);
  const [pendingPermissions, setPending] = useState<Permission[]>([]);
  const [lastError, setLastError] = useState('');
  const [globeCommand, setGlobeCommand] = useState<GlobeCommand | null>(null);
  const globeSeq = useRef(0);
  const [sceneCommand, setSceneCommand] = useState<SceneCommand | null>(null);
  const sceneSeq = useRef(0);
  // A ref, not a state: the scene shown changes nothing on screen here, it only goes with the next request.
  const sceneContext = useRef<SceneContext | null>(null);
  const setSceneContext = useCallback((ctx: SceneContext | null) => { sceneContext.current = ctx; }, []);
  // Steps of real tools now running (step_id -> start and kind). A search tool: the globe animates while there is one.
  // 11/11/2026 (ID Studio IA : « Oui, fais-le » — the shapes of the core appear by themselves): the same real steps also say
  // when AURORA reads or writes its memory, and when it runs a tool that is not a search. One map for the three kinds.
  const [work, setWork] = useState(NO_WORK);
  const workSteps = useRef(new Map<number, { at: number; work: Work }>());
  const syncWork = useCallback(() => {
    const now = { ...NO_WORK };
    for (const s of workSteps.current.values()) now[s.work] = true;
    setWork(w => (w.search === now.search && w.memory === now.memory && w.action === now.action ? w : now));
  }, []);
  const searching = work.search, remembering = work.memory, acting = work.action;
  const settingsRef = useRef<Settings | null>(null);
  settingsRef.current = settings;
  const pageRef = useRef<PageId>(page);
  pageRef.current = page;
  const inflightRef = useRef(0);
  inflightRef.current = inflight;
  const conversationRef = useRef<number | null>(null);
  conversationRef.current = conversationId;
  // Bumped by newConversation / openConversation: an answer arriving later must not bring back
  // the conversation the user has just left (nor be shown in the new one).
  const generation = useRef(0);

  const navigate = useCallback((p: PageId) => {
    setPage(p);
    if (window.location.hash !== `#/${p}`) window.location.hash = `/${p}`;
  }, []);

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const refreshHealth = useCallback(async () => {
    try {
      const h = await api.health();
      setHealth(h); setOnline(true); setLastError('');
    } catch (err) {
      setOnline(false);
      setLastError(err instanceof Error ? err.message : 'Service local indisponible');
    }
  }, []);

  const refreshPermissions = useCallback(async () => {
    try { setPending(await api.permissions('pending')); } catch { /* service hors ligne */ }
  }, []);

  useEffect(() => {
    void refreshHealth();
    void refreshPermissions();
    api.settings().then(setSettings).catch(() => undefined);
    const h = window.setInterval(() => { void refreshHealth(); }, 15000);
    return () => window.clearInterval(h);
  }, [refreshHealth, refreshPermissions]);

  // Offline, the health is checked every 3 s: the « Noyau local hors ligne » banner stayed up to 15 s after the
  // service came back (B7, reviews/a5-verifications.md).
  useEffect(() => {
    if (online) return;
    const h = window.setInterval(() => { void refreshHealth(); }, 3000);
    return () => window.clearInterval(h);
  }, [online, refreshHealth]);

  useEffect(() => {
    let alive = true;
    const tick = () => api.system().then(s => { if (alive) setSystem(s); }).catch(() => { if (alive) setSystem(null); });
    tick();
    const t = window.setInterval(tick, 3000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  /** 08/11/2026: open a page AURORA chose for the subject (ui.auto, followed while auto_open_tab is on). Leaving
   *  the Chat page opens the chat drawer beside the new page, so that the answer stays in view. */
  const autoNavigate = useCallback((target: PageId) => {
    if (settingsRef.current?.auto_open_tab === false || pageRef.current === target) return;
    if (pageRef.current === 'chat' && target !== 'chat') setChatOpen(true);
    navigate(target);
  }, [navigate]);

  /** What a run asks of the interface: a page (asked in words, or chosen for the subject) and an order for the globe. */
  const followUi = useCallback((run: RunResult) => {
    const ui = run.ui;
    if (!ui) return;
    if (ui.navigate) {
      if (ui.auto) autoNavigate(ui.navigate);
      else navigate(ui.navigate);
    }
    const onGlobe = ui.navigate === 'world' && (!ui.auto || settingsRef.current?.auto_open_tab !== false);
    if (ui.globe && (onGlobe || pageRef.current === 'world')) setGlobeCommand({ ...ui.globe, id: ++globeSeq.current });
    // A scene order is always asked in words (never ui.auto): the home page opens and the laboratory acts once.
    if (ui.scene && typeof ui.scene.scene === 'string') setSceneCommand({ ...ui.scene, id: ++sceneSeq.current });
  }, [autoNavigate, navigate]);

  const showOnGlobe = useCallback((order: GlobeOrder) => {
    navigate('world');
    setGlobeCommand({ ...order, id: ++globeSeq.current });
  }, [navigate]);

  const showScene = useCallback((order: SceneOrder) => {
    navigate('home');
    setSceneCommand({ ...order, id: ++sceneSeq.current });
  }, [navigate]);

  useEventStream(event => {
    if (event.kind.startsWith('permission.')) void refreshPermissions();
    // The search animation follows the real steps of the search tools (web, actualités, météo, ciel…).
    const stepId = typeof event.payload?.step_id === 'number' ? event.payload.step_id : null;
    if (stepId === null) return;
    if (event.kind === 'step.started') {
      const tool = typeof event.payload?.tool === 'string' ? event.payload.tool : '';
      // A search tool (web, actualités, météo, ciel…), a memory step (search or save), or any other tool really run.
      const kind: Work | null = isSearchTool(tool) ? 'search' : event.stage === 'memory' ? 'memory'
        : event.stage === 'tool' && tool ? 'action' : null;
      if (!kind) return;
      workSteps.current.set(stepId, { at: Date.now(), work: kind });
      syncWork();
      // The globe is open while it searches, for a request sent from here only: a routine running in the
      // background never moves the page.
      if (kind === 'search' && pageForTool(tool) === 'world' && inflightRef.current > 0) autoNavigate('world');
    } else if (/^step\.(done|error|skipped)$/.test(event.kind)) {
      const step = workSteps.current.get(stepId);
      if (!step) return;
      // Ended: at once for a search ; a memory step or a quick tool stays shown until WORK_HOLD_MS after its start.
      const left = step.at + WORK_HOLD_MS[step.work] - Date.now();
      const end = () => { if (workSteps.current.get(stepId) === step) { workSteps.current.delete(stepId); syncWork(); } };
      if (left > 0) window.setTimeout(end, left); else end();
    }
  });

  // A lost end event never leaves the globe searching (nor a shape on the core): a step older than 90 s, or no request
  // in flight, ends it.
  const working = work.search || work.memory || work.action;
  useEffect(() => {
    if (!working) return;
    const t = window.setInterval(() => {
      const now = Date.now();
      for (const [id, s] of workSteps.current) if (now - s.at > 90_000) workSteps.current.delete(id);
      syncWork();
    }, 5000);
    return () => window.clearInterval(t);
  }, [working, syncWork]);
  useEffect(() => {
    if (inflight === 0 && workSteps.current.size > 0) {
      const t = window.setTimeout(() => { workSteps.current.clear(); syncWork(); }, 1500);
      return () => window.clearTimeout(t);
    }
  }, [inflight, syncWork]);

  const saveSettings = useCallback(async (fields: Partial<Settings>) => {
    const next = await api.updateSettings(fields);
    setSettings(next);
    void refreshHealth();
  }, [refreshHealth]);

  const applyRunUpdate = useCallback((run: RunResult) => {
    setChat(entries => {
      const idx = entries.findIndex(e => e.run?.task_id === run.task_id);
      const entry: ChatEntry = { id: idx >= 0 ? entries[idx].id : uid(), role: 'assistant', text: run.answer || run.error || '', at: new Date().toISOString(), run };
      // A run of another conversation (permission decided from the loose list or the Tasks page)
      // is stored there, not in the one shown: say where instead of mixing the two.
      if (idx < 0 && run.conversation_id !== null && run.conversation_id !== conversationRef.current) {
        return [...entries, { id: uid(), role: 'note', text: `Réponse de la demande n° ${run.task_id} enregistrée dans la conversation n° ${run.conversation_id}.`, at: entry.at }];
      }
      if (idx < 0) return [...entries, entry];
      const copy = entries.slice();
      copy[idx] = entry;
      return copy;
    });
    followUi(run);
  }, [followUi]);

  const ask = useCallback(async (text: string, args: AskArgs = {}) => {
    const clean = text.trim();
    if (!clean) return null;
    const pendingId = uid();
    if (!args.silent) {
      setChat(entries => [...entries,
        { id: uid(), role: 'user', text: clean, at: new Date().toISOString(), source: args.source },
        { id: pendingId, role: 'assistant', text: '', at: new Date().toISOString(), pending: true }]);
    }
    setInflight(n => n + 1);
    const gen = generation.current;
    try {
      // The scene shown on the home page goes with the request (« démonte-le », « cette pièce »), never from another page.
      const scene = pageRef.current === 'home' ? sceneContext.current : null;
      const options = scene ? { ...args.options, scene } : args.options;
      const run = await api.ask({ text: clean, conversation_id: conversationRef.current, source: args.source ?? 'text', options, attachments: args.attachments });
      if (generation.current !== gen) {
        // The user started or reopened another conversation meanwhile: the answer stays in its own.
        if (!args.silent && run.conversation_id) {
          const short = clean.length > 60 ? `${clean.slice(0, 59)}…` : clean;
          setChat(entries => [...entries, { id: uid(), role: 'note', text: `La réponse à « ${short} » a été enregistrée dans la conversation n° ${run.conversation_id}.`, at: new Date().toISOString() }]);
        }
        if (run.status === 'waiting_permission') void refreshPermissions();
        return run;
      }
      // The ref is set at once, not at the next render (16/11/2026): a routine's first step is sent in this very turn
      // and must go to the conversation its launch just opened — it went to a conversation of its own.
      if (run.conversation_id) { conversationRef.current = run.conversation_id; setConversationId(run.conversation_id); }
      if (!args.silent) {
        setChat(entries => entries.map(e => e.id === pendingId ? { ...e, pending: false, text: run.answer || run.error || '', run } : e));
      }
      followUi(run);
      if (run.status === 'waiting_permission') void refreshPermissions();
      // D1: a routine's steps, sent one after the other exactly as if typed (same rights, same permission cards).
      // A step never starts another routine (source 'routine'); opening another conversation stops the sequence.
      if (run.ui?.routine && args.source !== 'routine') {
        for (const step of run.ui.routine.steps) {
          if (generation.current !== gen) break;
          await askRef.current(step, { source: 'routine' });
        }
      }
      return run;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Service indisponible';
      if (!args.silent) setChat(entries => entries.map(e => e.id === pendingId ? { ...e, pending: false, role: 'note', text: `Erreur : ${message}` } : e));
      setLastError(message);
      return null;
    } finally {
      setInflight(n => Math.max(0, n - 1));
    }
  }, [followUi, refreshPermissions]);
  const askRef = useRef(ask);   // the routine loop above sends its steps through the current ask
  askRef.current = ask;

  const decidePermission = useCallback(async (id: number, decision: 'grant' | 'deny', scope: 'once' | 'conversation' = 'once') => {
    try {
      const run = await api.decidePermission(id, decision, scope);
      applyRunUpdate(run);
      await refreshPermissions();
      return run;
    } catch (err) {
      setLastError(err instanceof Error ? err.message : 'Décision impossible');
      return null;
    }
  }, [applyRunUpdate, refreshPermissions]);

  const newConversation = useCallback(() => { generation.current += 1; setConversationId(null); setChat([]); }, []);

  const openConversation = useCallback(async (id: number) => {
    const messages = await api.messages(id); // oldest first; stored answers have no RunResult
    generation.current += 1;
    setConversationId(id);
    setChat(messages.map(m => ({ id: `m${m.id}`, role: m.role, text: m.content, at: m.created_at, meta_json: m.meta_json })));
  }, []);

  const value = useMemo<AuroraState>(() => ({
    page, navigate, health, online, refreshHealth, settings, saveSettings, system, conversationId, chat, busy,
    ask, applyRunUpdate, newConversation, openConversation, chatOpen, setChatOpen, pendingPermissions, refreshPermissions,
    decidePermission, lastError, globeCommand, showOnGlobe, searching, remembering, acting, sceneCommand, showScene, setSceneContext,
  }), [page, navigate, health, online, refreshHealth, settings, saveSettings, system, conversationId, chat, busy,
    ask, applyRunUpdate, newConversation, openConversation, chatOpen, pendingPermissions, refreshPermissions, decidePermission, lastError,
    globeCommand, showOnGlobe, searching, remembering, acting, sceneCommand, showScene, setSceneContext]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAurora(): AuroraState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useAurora() doit être utilisé dans <AuroraProvider>');
  return ctx;
}
