// AURORA shell: brand, top command field, sidebar navigation, page area, bottom voice/command bar.
// Pages are lazy-loaded so heavy modules (globe, simulations, flow replay) load only when opened.
import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import { Bell, Bot, Clapperboard, Cloud, Cpu, Database, FolderKanban, Globe2, Home, ListChecks, Search, Settings as SettingsIcon, Atom, User, Wifi, WifiOff } from 'lucide-react';
import { AuroraProvider, useAurora } from './lib/store';
import { useStreamStatus } from './lib/stream';
import { MODE_LABELS } from './lib/format';
import { useReducedMotion } from './lib/motion';
import type { PageId } from './lib/types';
import { Spinner, StatusDot } from './components/ui';
import CommandBar from './components/CommandBar';
import ChatDrawer from './components/ChatDrawer';
import { useSend } from './components/chat/useSend';
import { useChatPrefs } from './components/chat/prefs';
import { setLocalWhisper, setNeuralVoice, setOnlineVoicesAllowed, setRecognitionServiceMode, setWhisperPreferred } from './lib/voice';
import { api } from './lib/api';
import { useOpenArtefact } from './components/artefacts/store';

// P2: the page viewer is loaded on first opening, outside the shell chunk.
const ArtefactViewer = lazy(() => import('./components/artefacts/ArtefactViewer'));
// D2 (16/11/2026): the band « Routine … partie toute seule », above every page (components/RoutineSignal.tsx).
const RoutineSignal = lazy(() => import('./components/RoutineSignal'));
// C3 (16/11/2026): the band of a reminder, a timer or an alarm whose time has come (components/Reminders.tsx).
const ReminderSignal = lazy(() => import('./components/Reminders'));

const HomePage = lazy(() => import('./pages/HomePage'));
const StudioPage = lazy(() => import('./pages/StudioPage'));
const ChatPage = lazy(() => import('./pages/ChatPage'));
const SearchPage = lazy(() => import('./pages/SearchPage'));
const ComputerPage = lazy(() => import('./pages/ComputerPage'));
const WorldPage = lazy(() => import('./pages/WorldPage'));
const SimulationsPage = lazy(() => import('./pages/SimulationsPage'));
const ProjectsPage = lazy(() => import('./pages/ProjectsPage'));
const MemoryPage = lazy(() => import('./pages/MemoryPage'));
const TasksPage = lazy(() => import('./pages/TasksPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));

const NAV: { id: PageId; label: string; icon: ReactNode }[] = [
  { id: 'home', label: 'Accueil', icon: <Home size={24} /> },
  { id: 'studio', label: 'Studio', icon: <Clapperboard size={24} /> },
  { id: 'chat', label: 'Chat IA', icon: <Bot size={24} /> },
  { id: 'search', label: 'Recherche', icon: <Search size={24} /> },
  { id: 'computer', label: 'Ordinateur', icon: <Cpu size={24} /> },
  { id: 'world', label: 'Monde 3D', icon: <Globe2 size={24} /> },
  { id: 'simulations', label: 'Simulations', icon: <Atom size={24} /> },
  { id: 'projets', label: 'Projets', icon: <FolderKanban size={24} /> },
  { id: 'memory', label: 'Mémoire', icon: <Database size={24} /> },
  { id: 'tasks', label: 'Tâches', icon: <ListChecks size={24} /> },
];

export function LogoRing({ size = 46 }: { size?: number }) {
  const reduced = useReducedMotion(); // SMIL animations ignore the CSS reduced-motion override
  return (
    <svg className="logo-ring" width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
      <defs>
        <radialGradient id="lr-core" cx="50%" cy="50%" r="50%"><stop offset="0%" stopColor="#bff3ff" /><stop offset="45%" stopColor="#22c8ff" stopOpacity=".55" /><stop offset="100%" stopColor="#2f7bff" stopOpacity="0" /></radialGradient>
        <linearGradient id="lr-arc" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor="#7fe8ff" /><stop offset="100%" stopColor="#2f7bff" /></linearGradient>
      </defs>
      <circle cx="24" cy="24" r="21" fill="none" stroke="url(#lr-arc)" strokeWidth="2.2" style={{ filter: 'drop-shadow(0 0 5px #22c8ff)' }} />
      <circle cx="24" cy="24" r="15" fill="none" stroke="#22c8ff" strokeOpacity=".5" strokeWidth="1" strokeDasharray="3 4">
        {!reduced && <animateTransform attributeName="transform" type="rotate" from="0 24 24" to="360 24 24" dur="18s" repeatCount="indefinite" />}
      </circle>
      <ellipse cx="24" cy="24" rx="21" ry="7" fill="none" stroke="#9fe9ff" strokeOpacity=".55" strokeWidth="1.2" transform="rotate(-28 24 24)" />
      <circle cx="24" cy="24" r="9" fill="url(#lr-core)" />
    </svg>
  );
}

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const t = window.setInterval(() => setNow(new Date()), 1000); return () => window.clearInterval(t); }, []);
  const date = now.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  return <div className="clock"><strong>{now.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}</strong><span>{date.charAt(0).toUpperCase() + date.slice(1)}</span></div>;
}

function TopAsk() {
  const { busy } = useAurora();
  // Same send path as the composer: engine preference (« Local : Ollama uniquement ») and pending attachments.
  const send = useSend();
  const [text, setText] = useState('');
  return (
    <form className="top-ask" onSubmit={ev => { ev.preventDefault(); const t = text; setText(''); void send(t, { source: 'text', openDrawer: 'unless-spoken' }); }}>
      <Search size={18} />
      <input value={text} onChange={ev => setText(ev.target.value)} placeholder="Posez une question, donnez une instruction…" aria-label="Demander à AURORA" maxLength={12000} />
      {busy && <Spinner />}
    </form>
  );
}

/** P2 « Pages »: one viewer for the whole app, opened by openArtefact() (components/artefacts/store.ts). */
function ArtefactLayer() {
  const open = useOpenArtefact();
  return open ? <Suspense fallback={null}><ArtefactViewer key={open.seq} open={open} /></Suspense> : null;
}

function Shell() {
  const { page, navigate, health, online, pendingPermissions, setChatOpen, lastError } = useAurora();
  const live = useStreamStatus();
  // Online voices only if the viewer allowed them and never in Private mode (CLAUDE.md, local by default).
  const voicePrefs = useChatPrefs();
  const voiceMode = health?.capabilities.mode;
  useEffect(() => { setOnlineVoicesAllowed(voicePrefs.voiceOnline && !!voiceMode && voiceMode !== 'prive'); },
    [voicePrefs.voiceOnline, voiceMode]);
  // Same rule for the microphone: no online recognition in Private mode (lib/voice.ts recognitionRefusal).
  useEffect(() => { setRecognitionServiceMode(voiceMode); }, [voiceMode]);
  // Local neural voice (Piper, generated by the service on this PC): the default reading voice when installed.
  useEffect(() => {
    if (!online) return;
    api.speechStatus().then(s => setNeuralVoice(s.available, s.voice)).catch(() => setNeuralVoice(false));
    // Local recognition (voix D3): Athéna's Whisper, found by the service on this PC.
    api.recognitionStatus().then(s => setLocalWhisper(s.available)).catch(() => setLocalWhisper(false));
  }, [online]);
  useEffect(() => { setWhisperPreferred(voicePrefs.whisper); }, [voicePrefs.whisper]);
  // 13/11/2026 : « Projets » ne paraît dans le menu que si le service connaît /api/projets (un service pas encore relancé ne l'a pas).
  const [projetsConnus, setProjetsConnus] = useState(false);
  useEffect(() => {
    if (!online) return;
    api.projets().then(() => setProjetsConnus(true)).catch(() => setProjetsConnus(false));
  }, [online]);
  const caps = health?.capabilities;
  // internet null = not probed (private mode makes no connection test without permission)
  const net = !caps ? 'Connexion Internet inconnue' : caps.internet === null ? `Internet non vérifié${caps.mode === 'prive' ? ' (mode privé)' : ''}`
    : caps.internet ? 'Internet disponible' : 'Internet indisponible';
  const pages: Record<PageId, ReactNode> = {
    home: <HomePage />, studio: <StudioPage />, chat: <ChatPage />, search: <SearchPage />, computer: <ComputerPage />, world: <WorldPage />,
    simulations: <SimulationsPage />, projets: <ProjectsPage />, memory: <MemoryPage />, tasks: <TasksPage />, settings: <SettingsPage />,
  };
  return (
    <div className="shell">
      <div className="brand"><LogoRing /></div>
      <header className="topbar">
        <div className="logo"><div className="logo-text"><strong>AURORA</strong><small>Votre assistant IA</small></div></div>
        <TopAsk />
        <div className="top-right">
          <div className="status-chips" aria-label="État des moteurs">
            <span className="badge" title={online ? 'Noyau local connecté' : 'Noyau local hors ligne'}><StatusDot ok={online} /> Noyau</span>
            <span className="badge" title={caps?.ollama.up ? `Ollama · ${caps.ollama.chat_model}` : 'Ollama indisponible'}><StatusDot ok={!!caps?.ollama.up} /> Local</span>
            <span className="badge" title={caps?.cloud.configured ? 'Claude configuré' : 'Claude non configuré : enregistrez la clé dans Paramètres (coffre Windows)'}><Cloud size={12} /> {caps?.cloud.configured ? 'Claude' : 'Cloud off'}</span>
            {caps && <span className="badge badge-cyan" title="Mode d'intelligence">{MODE_LABELS[caps.mode] ?? caps.mode}</span>}
          </div>
          <Clock />
          <span className="round-btn" title={net} aria-label={net}>
            {caps?.internet ? <Wifi size={18} /> : <WifiOff size={18} />}
          </span>
          <button className="round-btn" title={`${pendingPermissions.length} autorisation(s) en attente${live ? '' : ' · flux temps réel déconnecté'}`} onClick={() => { if (pendingPermissions.length) setChatOpen(true); else navigate('tasks'); }}>
            <Bell size={18} />{pendingPermissions.length > 0 && <span className="count">{pendingPermissions.length}</span>}
          </button>
          <button className="round-btn" title="Paramètres" onClick={() => navigate('settings')}><SettingsIcon size={18} /></button>
          <span className="round-btn" aria-hidden="true"><User size={18} /></span>
        </div>
      </header>
      <nav className="sidebar" aria-label="Navigation principale">
        {NAV.filter(item => item.id !== 'projets' || projetsConnus).map(item => (
          <button key={item.id} className={`nav-item ${page === item.id ? 'active' : ''}`} onClick={() => navigate(item.id)} aria-current={page === item.id ? 'page' : undefined}>
            {item.icon}<span>{item.label}</span>
          </button>
        ))}
        <div className="nav-spacer" />
        <button className={`nav-item ${page === 'settings' ? 'active' : ''}`} onClick={() => navigate('settings')}><SettingsIcon size={24} /><span>Paramètres</span></button>
      </nav>
      <main className="main">
        {!online && <div className="error-banner" role="alert">Noyau local AURORA hors ligne — {lastError || 'démarrez le serveur Python (voir README)'}.</div>}
        {online && <Suspense fallback={null}><ReminderSignal /></Suspense>}
        {online && <Suspense fallback={null}><RoutineSignal /></Suspense>}
        <Suspense fallback={<div className="empty-state"><Spinner size={22} /></div>}>{pages[page]}</Suspense>
      </main>
      <div className="bottom-bar"><CommandBar /></div>
      <ChatDrawer />
      <ArtefactLayer />
    </div>
  );
}

export default function App() {
  return (
    <AuroraProvider>
      <div className="app-bg" aria-hidden="true" />
      <Shell />
    </AuroraProvider>
  );
}
