// Accueil : noyau holographique au centre depuis le 08/11/2026, le globe gardé au choix et en secours (CentreAccueil ;
// les tuiles de modules autour du globe ont été retirées, 492b793), colonne Système / Météo / Actualités et
// colonne Activité récente / Mémoire. Chaque valeur vient de l'API ; sinon « indisponible ».
// Les liens globe ↔ module s'allument uniquement sur un événement réel reçu par SSE.
import { useEffect, useMemo, useRef, useState, type ComponentType, type ReactNode } from 'react';
import {
  Activity, Atom, Bot, CalendarCheck, ChevronDown, ChevronLeft, ChevronRight, CloudSun, Cpu, Database, Download, Droplets, ExternalLink, GitBranch, Globe2,
  ListChecks, Newspaper, Orbit, Search, Settings, SquarePlay, Thermometer, Wind, type LucideProps,
} from 'lucide-react';
import Globe, { type GlobeHud, type GlobeRing } from '../components/Globe';
import Laboratoire, { useEtatNoyau } from '../components/labo/Laboratoire';
import CerveauFlottant from '../components/labo/CerveauFlottant';
import type { ChoixForme } from '../components/labo/formes';
import { sceneAccueil } from '../components/labo/scene';
import { ecrireChoix, lireChoix, webglDisponible } from '../components/hologram/outils';
import { Panel, RingGauge, Spinner, StatusDot, Unavailable } from '../components/ui';
import { useAurora } from '../lib/store';
import { api } from '../lib/api';
import { useVoiceActivity, voiceLevel } from '../lib/voice';
import { useEventStream, useStreamStatus } from '../lib/stream';
import { STAGE_LABELS, fmtTime, pageForTool, relativeTime } from '../lib/format';
import type { ActivityEvent, MemoryItem, PageId } from '../lib/types';
import { useElementSize, useNow, useReducedMotion, useToolResult } from '../components/globe/hooks';
import { firstSource, readCurrentWeather, readNews, readNewsFeeds, retrievedAt } from '../components/globe/data';
import SkyIcon from '../components/globe/SkyIcon';
import { useStudio } from '../components/studio/store';
import { DownloadsBody, GitHubBody, RoutineList, SourceLine, YouTubeBody, useOpenSettings, useRoutines } from '../components/studio/parts';
import './home.css';

// ------------------------------------------------------------------ modules

interface ModuleDef { page: PageId; title: string; subtitle: string; Icon: ComponentType<LucideProps> }

/** Clockwise from the top-left; placed at -112.5°, -67.5°, … around the globe. */
const MODULES: ModuleDef[] = [
  { page: 'chat', title: 'Chat IA', subtitle: 'Discuter et créer', Icon: Bot },
  { page: 'search', title: 'Recherche Web', subtitle: 'Explorer et analyser', Icon: Search },
  { page: 'computer', title: 'Mon ordinateur', subtitle: 'Surveiller et optimiser', Icon: Cpu },
  { page: 'world', title: 'Monde 3D', subtitle: 'Explorer la planète', Icon: Globe2 },
  { page: 'simulations', title: 'Simulations 3D', subtitle: 'Calculer et modéliser', Icon: Atom },
  { page: 'memory', title: 'Mémoire', subtitle: 'Se souvenir', Icon: Database },
  { page: 'tasks', title: 'Tâches', subtitle: 'Organiser et suivre', Icon: ListChecks },
  { page: 'settings', title: 'Paramètres', subtitle: 'Personnaliser', Icon: Settings },
];

const HOME_RINGS: GlobeRing[] = [
  { radius: 1.38, flatten: 0.22, tilt: -14, dots: 2, speed: 0.22 },
  { radius: 1.2, flatten: 0.36, tilt: 26, dots: 1, speed: -0.3, dashed: true, color: '138,92,255', width: 0.9 },
];

/** Which module a live event concerns (lights its link). Null = no module. */
function pageForEvent(e: ActivityEvent): PageId | null {
  const tool = typeof e.payload?.tool === 'string' ? e.payload.tool : '';
  if (e.subject_type === 'memory' || e.stage === 'memory' || e.kind.startsWith('memory.') || e.kind === 'saved' || e.kind === 'deleted') return 'memory';
  if (e.subject_type === 'permission' || e.stage === 'permission') return 'tasks';
  if (tool) return pageForTool(tool);   // shared with the tab AURORA opens by itself (lib/format.ts)
  if (/^(run\.(done|error|cancelled|interrupted|resumed)|plan\.|step\.|task\.)/.test(e.kind) || ['pending', 'running', 'done', 'error'].includes(e.kind)) return 'tasks';
  if (['input', 'decision', 'local_ai', 'cloud_ai', 'response'].includes(e.stage)) return 'chat';
  if (e.kind.startsWith('system.')) return 'settings';
  return null;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [query]);
  return match;
}

/** `showModules` off (default since 28/10, owner's request): the sidebar already lists every module, the
 *  centre shows the globe alone. The module tiles are kept behind the option, not deleted (CLAUDE.md). */
function ModuleOrbit({ showModules = false }: { showModules?: boolean }) {
  const { navigate } = useAurora();
  // Blue rings around the globe light up while AURORA speaks or listens, pulsing with the real voice level.
  // Luminous spheres orbit it while AURORA thinks (fast) or speaks (slow), from the noyau's real state (08/11/2026).
  const voice = useVoiceActivity();
  const { etat } = useEtatNoyau(null);
  // 11/11/2026 : « memoire » et « action » sont des moments du même travail (un pas de mémoire, un outil) : le globe
  // de secours continue de réfléchir pendant ce temps, comme avant.
  const spheres: GlobeHud['spheres'] = etat === 'analyse' || etat === 'recherche' || etat === 'memoire' || etat === 'action' ? 'reflexion'
    : etat === 'parole' ? 'parole' : null;
  const hud = useMemo<GlobeHud>(() => ({ active: voice !== 'idle', level: voiceLevel, spheres }), [voice, spheres]);
  const ref = useRef<HTMLDivElement>(null);
  const { width, height } = useElementSize(ref);
  const narrow = useMediaQuery('(max-width: 1099px)');
  const orbit = !narrow && width >= 640;
  const [lit, setLit] = useState<Partial<Record<PageId, number>>>({});
  const timers = useRef(new Map<PageId, number>());

  useEventStream(event => {
    const page = pageForEvent(event);
    if (!page) return;
    setLit(l => ({ ...l, [page]: (l[page] ?? 0) + 1 }));
    const prev = timers.current.get(page);
    if (prev) window.clearTimeout(prev);
    timers.current.set(page, window.setTimeout(() => {
      timers.current.delete(page);
      setLit(l => { const next = { ...l }; delete next[page]; return next; });
    }, 1800));
  });
  useEffect(() => () => { timers.current.forEach(t => window.clearTimeout(t)); }, []);

  const geo = useMemo(() => {
    if (!orbit || !width || !height) return null;
    const cx = width / 2;
    const cy = height / 2;
    const cardW = clamp(width * 0.25, 190, 224);
    const cardH = 66;
    const globeH = Math.min(height * 0.7, width * 0.42);
    const globeW = Math.min(globeH * 1.32, width * 0.54);
    // Globe radius as fitted by the engine (rings 1.38 wide, halo 1.2 high).
    const R = Math.min(globeW / 2 / 1.42, globeH / 2 / 1.2) * 0.98;
    const rx = width / 2 - cardW / 2 - 6;
    const ry = height / 2 - cardH / 2 - 6;
    const cards = MODULES.map((m, i) => {
      const a = (-112.5 + i * 45) * (Math.PI / 180);
      const x = cx + rx * Math.cos(a);
      const y = cy + ry * Math.sin(a);
      const dir = Math.atan2(y - cy, x - cx);
      const sx = cx + R * 1.06 * Math.cos(dir);
      const sy = cy + R * 1.06 * Math.sin(dir);
      const dx = sx - x;
      const dy = sy - y;
      const t = Math.min(cardW / 2 / Math.max(Math.abs(dx), 1e-6), cardH / 2 / Math.max(Math.abs(dy), 1e-6));
      return { m, x, y, sx, sy, ex: x + dx * t, ey: y + dy * t };
    });
    return { cx, cy, cardW, cardH, globeW, globeH, cards };
  }, [orbit, width, height]);

  if (!showModules) {
    return (
      <div className="home-orbit home-orbit-solo">
        {/* decoration only: the Monde 3D page is opened from the sidebar */}
        <div className="home-globe" data-spheres={spheres ?? 'repos'}>
          <Globe detail="110m" autoRotate rotateSpeed={5} rings={HOME_RINGS} hud={hud} initialCenter={{ lat: 18, lon: 8 }}
            ariaLabel="Globe terrestre en rotation (décor)" />
        </div>
      </div>
    );
  }

  const globe = (
    <div className="home-globe" data-spheres={spheres ?? 'repos'}
      style={geo ? { width: geo.globeW, height: geo.globeH, left: geo.cx - geo.globeW / 2, top: geo.cy - geo.globeH / 2 } : undefined}>
      <Globe detail="110m" autoRotate rotateSpeed={5} rings={HOME_RINGS} hud={hud} initialCenter={{ lat: 18, lon: 8 }}
        ariaLabel="Globe terrestre en rotation (décor)" />
    </div>
  );

  return (
    <div ref={ref} className={`home-orbit ${orbit ? 'home-orbit-ring' : 'home-orbit-grid'}`}>
      {geo && (
        <svg className="home-links" width={width} height={height} aria-hidden="true">
          <defs>
            <linearGradient id="home-link-grad" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#22c8ff" stopOpacity=".05" />
              <stop offset="100%" stopColor="#22c8ff" stopOpacity=".55" />
            </linearGradient>
          </defs>
          {geo.cards.map(c => {
            const on = lit[c.m.page] !== undefined;
            return (
              <g key={c.m.page} className={`home-link ${on ? 'home-link-lit' : ''}`}>
                <line x1={c.sx} y1={c.sy} x2={c.ex} y2={c.ey} />
                <circle cx={c.sx} cy={c.sy} r={2.4} />
                <circle cx={c.ex} cy={c.ey} r={2} />
              </g>
            );
          })}
        </svg>
      )}
      {(!orbit || geo) && globe}
      <div className="home-cards">
        {(geo?.cards ?? MODULES.map(m => ({ m, x: 0, y: 0 }))).map(({ m, x, y }) => (
          <button key={m.page} type="button" className={`home-card ${lit[m.page] !== undefined ? 'home-card-lit' : ''}`}
            onClick={() => navigate(m.page)} aria-label={`${m.title} — ${m.subtitle}`}
            style={geo ? { left: x - geo.cardW / 2, top: y - geo.cardH / 2, width: geo.cardW, height: geo.cardH } : undefined}>
            <span className="home-card-icon"><m.Icon size={22} aria-hidden="true" /></span>
            <span className="home-card-text"><strong>{m.title}</strong><small>{m.subtitle}</small></span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ centre : noyau holographique, ou le globe

type VueCentre = 'noyau' | 'globe';
const VUES_CENTRE: VueCentre[] = ['noyau', 'globe'];
const CLE_VUE_CENTRE = 'aurora.home.vue';

/** Le noyau holographique (cahier des charges du 08/11/2026, reviews/cahier-accueil-holographique-v1.md) a pris la
 *  place du globe. Le globe n'est pas supprimé (CLAUDE.md) : il reste au choix, et en secours sans WebGL ou si le
 *  moteur 3D tombe (jusqu'au prochain clic sur « Noyau »). */
/** La dernière commande de scène vue par l'accueil (hors du composant : l'accueil peut s'ouvrir après elle). */
let derniereSceneVue: number | null = null;

/** La forme du noyau, choisie à la main sur le bouton « Noyau » (11/11/2026, ID Studio IA : « ajoute aussi au noyau la sélection
 *  manuelle », « sur l'icône du noyau, ça se déplie et on sélectionne »). « Automatique » : la forme suit le travail
 *  d'AURORA. Les trois formes sont dessinées par labo/formes.ts ; ici, seulement leurs noms et le choix gardé. */
const CLE_FORME_NOYAU = 'aurora.home.forme';
const FORMES_NOYAU: { id: ChoixForme; nom: string; detail: string }[] = [
  { id: 'auto', nom: 'Automatique', detail: 'La forme suit le travail d’AURORA : Cosmique quand elle cherche, Organique quand elle consulte sa mémoire, Énergie pure quand elle lance un outil ; le noyau le reste du temps.' },
  { id: 'noyau', nom: 'Noyau', detail: 'Toujours le noyau, sans autre forme.' },
  { id: 'cosmique', nom: 'Cosmique', detail: 'La sphère aux galaxies, tant que vous la gardez.' },
  { id: 'organique', nom: 'Organique', detail: 'Le lotus, tant que vous le gardez.' },
  { id: 'energie', nom: 'Énergie pure', detail: 'Les rubans de lumière, tant que vous les gardez.' },
];
const IDS_FORMES_NOYAU = FORMES_NOYAU.map(f => f.id);

function CentreAccueil({ cerveau }: { cerveau: boolean }) {
  const { sceneCommand } = useAurora();
  const [webgl] = useState(webglDisponible);
  const [forme, setForme] = useState<ChoixForme>(() => lireChoix(CLE_FORME_NOYAU, IDS_FORMES_NOYAU, 'auto'));
  const [menuFormes, setMenuFormes] = useState(false);
  const boiteVue = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuFormes) return;
    const ailleurs = (e: PointerEvent) => { if (!boiteVue.current?.contains(e.target as Node)) setMenuFormes(false); };
    // Échap ne ferme que ce menu : pris en premier, comme le menu du moteur de « Photo vers 3D ».
    const touche = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setMenuFormes(false); } };
    document.addEventListener('pointerdown', ailleurs);
    document.addEventListener('keydown', touche, true);
    return () => { document.removeEventListener('pointerdown', ailleurs); document.removeEventListener('keydown', touche, true); };
  }, [menuFormes]);
  const choisirForme = (f: ChoixForme) => { setForme(f); ecrireChoix(CLE_FORME_NOYAU, f); setMenuFormes(false); };
  const formeChoisie = FORMES_NOYAU.find(f => f.id === forme) ?? FORMES_NOYAU[0];
  // Une scène restée ouverte revient au retour sur l'accueil, même si le globe est le choix enregistré.
  const [vue, setVue] = useState<VueCentre>(() => (sceneAccueil.id ? 'noyau' : lireChoix(CLE_VUE_CENTRE, VUES_CENTRE, 'noyau')));
  const [secours, setSecours] = useState('');
  const affichee: VueCentre = vue === 'noyau' && webgl && !secours ? 'noyau' : 'globe';
  const choisir = (v: VueCentre) => {
    if (v === 'globe') sceneAccueil.id = null;   // choisir le globe, c'est quitter la scène
    setVue(v); setSecours(''); ecrireChoix(CLE_VUE_CENTRE, v);
  };
  // Une scène 3D demandée en mots s'affiche même si le globe était choisi (sans changer ce choix enregistré) ; sans
  // WebGL, la note le dit.
  const [sceneSansWebgl, setSceneSansWebgl] = useState(false);
  useEffect(() => {
    if (!sceneCommand || sceneCommand.id === derniereSceneVue) return;
    derniereSceneVue = sceneCommand.id;
    if (sceneCommand.action === 'noyau') return;
    if (!webgl) { setSceneSansWebgl(true); return; }
    setVue('noyau'); setSecours('');
  }, [sceneCommand, webgl]);
  const note = sceneSansWebgl ? 'La scène 3D demande WebGL, indisponible sur cet ordinateur : la réponse d’AURORA la décrit en texte.'
    : vue !== 'noyau' ? ''
    : !webgl ? 'WebGL est indisponible sur cet ordinateur : le globe remplace le noyau.'
    : secours ? `Globe affiché en secours : ${secours}` : '';
  return (
    <div className="home-centre">
      <div className="home-vue" ref={boiteVue}>
        <div className="home-vue-choix" role="radiogroup" aria-label="Centre de l'accueil">
          {/* Déjà sur le noyau, un clic déplie le choix de sa forme ; depuis le globe, il ramène d'abord le noyau. */}
          <button type="button" role="radio" aria-checked={affichee === 'noyau'} disabled={!webgl}
            aria-haspopup="menu" aria-expanded={affichee === 'noyau' && menuFormes}
            title={affichee === 'noyau' ? `Forme du noyau : ${formeChoisie.nom}. Cliquez pour choisir.` : 'Afficher le noyau'}
            className={`home-vue-btn ${affichee === 'noyau' ? 'home-vue-on' : ''}`}
            onClick={() => { if (affichee === 'noyau') setMenuFormes(o => !o); else choisir('noyau'); }}>
            <Orbit size={13} aria-hidden="true" />Noyau{forme !== 'auto' && forme !== 'noyau' ? ` · ${formeChoisie.nom}` : ''}
            <ChevronDown size={12} className="home-vue-pli" aria-hidden="true" />
          </button>
          <button type="button" role="radio" aria-checked={affichee === 'globe'}
            className={`home-vue-btn ${affichee === 'globe' ? 'home-vue-on' : ''}`} onClick={() => { setMenuFormes(false); choisir('globe'); }}>
            <Globe2 size={13} aria-hidden="true" />Globe
          </button>
        </div>
        {menuFormes && affichee === 'noyau' && (
          <div className="home-vue-menu" role="menu" aria-label="Forme du noyau">
            {FORMES_NOYAU.map(f => (
              <button key={f.id} type="button" role="menuitemradio" aria-checked={f.id === forme} className={f.id === forme ? 'on' : undefined}
                onClick={() => choisirForme(f.id)}>
                <strong>{f.nom}</strong><small>{f.detail}</small>
              </button>
            ))}
            <p>Une forme gardée à la main ne dit rien du travail en cours : l’état réel reste écrit sous le noyau (« Au repos », « Recherche »…).</p>
          </div>
        )}
      </div>
      {affichee === 'noyau' ? <Laboratoire onSecours={setSecours} cerveau={cerveau} forme={forme} /> : <ModuleOrbit />}
      {note && <p className="home-vue-note" role="status">{note}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ system

function gaugeColor(v: number | null | undefined, base: 'cyan' | 'violet' | 'green') {
  if (v === null || v === undefined) return base;
  if (v >= 95) return 'red';
  if (v >= 85) return 'amber';
  return base;
}

const gb = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toFixed(v >= 100 ? 0 : 1).replace('.', ','));

function SystemPanel() {
  const { system, health, online, navigate } = useAurora();
  const live = useStreamStatus();
  const caps = health?.capabilities;
  const issues: { text: string; level: 'error' | 'warn' }[] = [];
  if (!online) {
    issues.push({ text: 'Noyau local hors ligne', level: 'error' });
  } else {
    if (health && health.status && health.status !== 'ok') issues.push({ text: `État du service : ${health.status}`, level: 'warn' });
    if (!system) issues.push({ text: 'Mesures système indisponibles', level: 'warn' });
    if (caps && !caps.ollama.up) issues.push({ text: 'IA locale (Ollama) indisponible', level: 'error' });
    if (caps && caps.internet === false) issues.push({ text: 'Internet indisponible', level: 'warn' });
    if (caps && !caps.cloud.configured) issues.push({ text: 'Claude non configuré', level: 'warn' });
    if (!live) issues.push({ text: 'Flux temps réel déconnecté', level: 'warn' });
  }
  const allGood = online && !!caps && issues.length === 0;
  return (
    <Panel title="Système" icon={<Cpu size={18} />} onOpen={() => navigate('computer')} className="home-panel">
      <div className="home-gauges">
        <RingGauge value={system?.cpu_percent} label="CPU" color={gaugeColor(system?.cpu_percent, 'cyan')} size={84} />
        <RingGauge value={system?.ram_percent} label="RAM" color={gaugeColor(system?.ram_percent, 'violet')} size={84} />
        <RingGauge value={system?.disk_percent} label="Stockage" color={gaugeColor(system?.disk_percent, 'green')} size={84} />
      </div>
      {system && (
        <div className="home-sys-details">
          <span>RAM {gb(system.ram_used_gb)} / {gb(system.ram_total_gb)} Go</span>
          <span>Disque {gb(system.disk_used_gb)} / {gb(system.disk_total_gb)} Go</span>
        </div>
      )}
      <div className="home-status" role="status">
        {allGood ? (
          <span className="home-status-row"><StatusDot ok label="opérationnel" /> Tout est opérationnel</span>
        ) : !caps && online ? (
          <span className="home-status-row"><Spinner size={12} /> Vérification des capacités…</span>
        ) : (
          issues.map(i => (
            <span key={i.text} className="home-status-row"><StatusDot ok={i.level !== 'error'} warn={i.level === 'warn'} label={i.level === 'error' ? 'erreur' : 'attention'} /> {i.text}</span>
          ))
        )}
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------------ weather

const CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO'];
const cardinal = (deg: number) => CARDINALS[Math.round((((deg % 360) + 360) % 360) / 45) % 8];

function WeatherPanel() {
  const { settings, online, navigate } = useAurora();
  const city = settings?.default_city ?? '';
  const ws = useToolResult(() => api.weather(city || undefined), `weather:${city}`, { enabled: online, refreshMs: 10 * 60_000 });
  const w = readCurrentWeather(ws.result);
  const source = firstSource(ws.result);
  const updated = retrievedAt(ws.result) ?? ws.fetchedAt;
  const place = w ? [w.city, w.country].filter(Boolean).join(', ') : city;

  let body;
  if (!online && !ws.result) body = <Unavailable what="Données météo" reason="Noyau local hors ligne." />;
  else if (ws.loading && !ws.result) body = <div className="home-loading"><Spinner /> Récupération de la météo…</div>;
  else if (ws.error) body = <Unavailable what="Données météo" reason={ws.error} />;
  else if (!w) body = <Unavailable what="Données météo" reason="Réponse de la source illisible." />;
  else {
    body = (
      <>
        <div className="home-weather-main">
          <SkyIcon kind={w.kind} night={w.isDay === false} size={46} />
          <div>
            <div className="home-weather-temp">{w.temperature === null ? '—' : `${Math.round(w.temperature)}°C`}</div>
            <div className="home-weather-label">{w.label || '—'}</div>
          </div>
        </div>
        <div className="home-weather-facts">
          <span title="Vent"><Wind size={14} aria-hidden="true" /> {w.windKmh === null ? '—' : `${Math.round(w.windKmh)} km/h${w.windDir !== null ? ` ${cardinal(w.windDir)}` : ''}`}</span>
          <span title="Humidité relative"><Droplets size={14} aria-hidden="true" /> {w.humidity === null ? '—' : `${Math.round(w.humidity)} %`}</span>
          {w.apparent !== null && <span title="Température ressentie"><Thermometer size={14} aria-hidden="true" /> ressenti {Math.round(w.apparent)}°</span>}
          {w.todayMin !== null && w.todayMax !== null && <span title="Minimum et maximum du jour">min {Math.round(w.todayMin)}° · max {Math.round(w.todayMax)}°</span>}
        </div>
      </>
    );
  }
  return (
    <Panel title="Météo" subtitle={place || undefined} icon={<CloudSun size={18} />} onOpen={() => navigate('world')} className="home-panel"
      actions={ws.loading && ws.result ? <Spinner size={12} /> : undefined}>
      {body}
      {w && !ws.error && (
        <p className="home-source">
          Source : {source?.url ? <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title || 'Open-Meteo'}</a> : (source?.title || ws.result?.provenance || 'Open-Meteo')}
          {w.observedAt ? <> · relevé de {fmtTime(w.observedAt).slice(0, 5)}</> : updated ? <> · récupéré à {fmtTime(updated).slice(0, 5)}</> : null}
        </p>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ news

function NewsPanel() {
  const { online, navigate } = useAurora();
  const ns = useToolResult(() => api.news(), 'news', { enabled: online, refreshMs: 20 * 60_000 });
  const items = useMemo(() => readNews(ns.result).slice(0, 8), [ns.result]);
  const [idx, setIdx] = useState(0);
  const [paused, setPaused] = useState(false);
  const [broken, setBroken] = useState<Set<string>>(() => new Set());
  const reduced = useReducedMotion();
  useNow(60_000);

  useEffect(() => { setIdx(i => (i < items.length ? i : 0)); }, [items.length]);
  useEffect(() => {
    if (paused || reduced || items.length < 2) return;
    const t = window.setInterval(() => setIdx(i => (i + 1) % items.length), 9000);
    return () => window.clearInterval(t);
  }, [paused, reduced, items.length]);

  const item = items[idx];
  const updated = retrievedAt(ns.result) ?? ns.fetchedAt;
  const go = (d: number) => setIdx(i => (i + d + items.length) % items.length);

  let body;
  if (!online && !ns.result) body = <Unavailable what="Actualités" reason="Noyau local hors ligne." />;
  else if (ns.loading && !ns.result) body = <div className="home-loading"><Spinner /> Récupération des actualités…</div>;
  else if (ns.error) body = <Unavailable what="Actualités" reason={ns.error} />;
  else if (!item) body = <div className="home-empty">Aucun article dans les flux pour le moment.</div>;
  else {
    const img = item.image && !broken.has(item.image) ? item.image : null;
    body = (
      <div className="home-news" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}
        onFocus={() => setPaused(true)} onBlur={() => setPaused(false)}>
        <article className="home-news-item" aria-live={paused || reduced ? 'polite' : 'off'} aria-roledescription="article" aria-label={`Article ${idx + 1} sur ${items.length}`}>
          {img && (
            <img className="home-news-img" src={img} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer"
              onError={() => setBroken(s => new Set(s).add(img))} />
          )}
          <div className="home-news-copy">
            <h3>
              {item.url ? (
                <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}<ExternalLink size={12} aria-label="(lien externe)" /></a>
              ) : item.title}
            </h3>
            {item.summary && <p>{item.summary}</p>}
            <small>{[item.source, item.published ? relativeTime(item.published) : ''].filter(Boolean).join(' · ')}</small>
          </div>
        </article>
        {items.length > 1 && (
          <div className="home-news-nav">
            <button type="button" className="icon-btn" onClick={() => go(-1)} aria-label="Article précédent"><ChevronLeft size={16} /></button>
            <div className="home-dots" role="group" aria-label="Choisir un article">
              {items.map((it, i) => (
                <button key={it.id} type="button" className={`home-dot ${i === idx ? 'home-dot-on' : ''}`} onClick={() => setIdx(i)}
                  aria-label={`Article ${i + 1}`} aria-current={i === idx ? 'true' : undefined} />
              ))}
            </div>
            <button type="button" className="icon-btn" onClick={() => go(1)} aria-label="Article suivant"><ChevronRight size={16} /></button>
          </div>
        )}
      </div>
    );
  }
  const feeds = readNewsFeeds(ns.result);
  const feedCount = feeds.ok.length || ns.result?.sources?.length || 0;
  return (
    <Panel title="Actualités" subtitle="Flux scientifiques" icon={<Newspaper size={18} />} onOpen={() => navigate('search')} className="home-panel">
      {body}
      {item && !ns.error && (
        <p className="home-source">
          Source : {item.source || firstSource(ns.result)?.title || 'flux RSS'}
          {feedCount > 1 && <> · {feedCount} flux consultés</>}
          {updated && <> · récupéré à {fmtTime(updated).slice(0, 5)}</>}
          {feeds.failed.length > 0 && <><br />Flux indisponible{feeds.failed.length > 1 ? 's' : ''} : {feeds.failed.join(', ')}</>}
        </p>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ activity & memory

const KIND_LABELS: Record<string, string> = {
  'run.created': 'Demande reçue', 'route.decided': 'Décision', 'plan.created': 'Plan créé', 'step.started': 'Étape démarrée',
  'tool.result': "Résultat d'outil", 'model.result': 'Réponse du modèle', 'step.done': 'Étape terminée', 'step.error': 'Étape en erreur',
  'step.skipped': 'Étape ignorée', 'permission.requested': 'Autorisation demandée', 'permission.granted': 'Autorisation accordée',
  'permission.denied': 'Autorisation refusée', 'permission.revoked': 'Autorisation révoquée', 'verify.passed': 'Vérification réussie', 'verify.failed': 'Vérification échouée',
  'memory.recalled': 'Souvenirs rappelés', 'memory.saved': 'Souvenir enregistré', 'memory.updated': 'Souvenir modifié',
  'memory.deleted': 'Souvenir supprimé', 'memory.linked': 'Souvenirs liés', 'response.ready': 'Réponse prête', 'agent.todo': 'Tour de l’agent',
  'run.done': 'Demande terminée', 'run.error': 'Demande en erreur', 'run.cancelled': 'Demande annulée',
  'run.interrupted': 'Demande interrompue', 'run.resumed': 'Demande reprise', 'system.imported': 'Import ASTRA',
  'routine.scheduled': 'Routine partie seule', 'reminder.due': 'Rappel',
  'system.started': 'Démarrage', 'task.pending': 'Tâche créée', 'task.running': 'Tâche en cours', 'task.done': 'Tâche terminée',
  'task.error': 'Tâche en erreur', pending: 'Créée', running: 'En cours', done: 'Terminée', error: 'Erreur', saved: 'Enregistrée', deleted: 'Supprimée',
};

const STAGE_VARS: Record<string, string> = {
  input: 'var(--st-input)', decision: 'var(--st-decision)', tool: 'var(--st-tool)', local_ai: 'var(--st-local)', cloud_ai: 'var(--st-cloud)',
  permission: 'var(--st-permission)', verify: 'var(--st-verify)', response: 'var(--st-response)', memory: 'var(--st-memory)',
};

function eventColor(e: ActivityEvent) {
  if (e.level === 'error') return 'var(--red)';
  if (e.level === 'warn') return 'var(--amber)';
  return STAGE_VARS[e.stage] ?? 'var(--cyan)';
}

function ActivityPanel() {
  const { online, navigate } = useAurora();
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);
  const [error, setError] = useState('');
  useNow(30_000);

  useEffect(() => {
    if (!online) return;
    let alive = true;
    api.events({ limit: 5 })
      .then(list => { if (alive) { setEvents(list.slice(0, 5)); setError(''); } })
      .catch((err: unknown) => { if (alive) setError(err instanceof Error ? err.message : 'Service local indisponible'); });
    return () => { alive = false; };
  }, [online]);

  useEventStream(e => setEvents(list => [e, ...(list ?? []).filter(x => x.id !== e.id)].slice(0, 5)));

  let body;
  if (events === null && (error || !online)) body = <Unavailable what="Événements" reason={error || 'Noyau local hors ligne.'} />;
  else if (events === null) body = <div className="home-loading"><Spinner /> Chargement…</div>;
  else if (!events.length) body = <div className="home-empty">Aucune activité enregistrée pour le moment.</div>;
  else body = (
    <ul className="home-activity">
      {events.map(e => (
        <li key={e.id}>
          <button type="button" onClick={() => navigate('tasks')} title="Voir dans Tâches">
            <span className="home-activity-dot" style={{ background: eventColor(e), boxShadow: `0 0 8px ${eventColor(e)}` }} />
            <span className="home-activity-copy">
              <strong>{e.label || KIND_LABELS[e.kind] || e.kind}</strong>
              <small>{[KIND_LABELS[e.kind] ?? e.kind, e.stage ? STAGE_LABELS[e.stage] : ''].filter(Boolean).join(' · ')}</small>
            </span>
            <time dateTime={e.created_at}>{relativeTime(e.created_at)}</time>
          </button>
        </li>
      ))}
    </ul>
  );
  return <Panel title="Activité récente" icon={<Activity size={18} />} onOpen={() => navigate('tasks')} className="home-panel">{body}</Panel>;
}

/** `cerveau` : sur grand écran, le cerveau mémoire du centre se pose ici, au-dessus des chiffres (ID Studio IA, 08/11/2026). */
function MemoryPanel({ cerveau }: { cerveau: boolean }) {
  const { online, navigate } = useAurora();
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [error, setError] = useState('');
  const [rev, setRev] = useState(0);
  const debounce = useRef(0);

  useEffect(() => {
    if (!online) return;
    let alive = true;
    api.memories()
      .then(list => { if (alive) { setItems(list); setError(''); } })
      .catch((err: unknown) => { if (alive) setError(err instanceof Error ? err.message : 'Service local indisponible'); });
    return () => { alive = false; };
  }, [online, rev]);

  useEventStream(e => {
    if (!/^memory\.(saved|updated|deleted)$|^(saved|deleted)$/.test(e.kind)) return;
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => setRev(r => r + 1), 1200);
  });
  useEffect(() => () => window.clearTimeout(debounce.current), []);

  let body;
  if (items === null && (error || !online)) body = <Unavailable what="Compteurs mémoire" reason={error || 'Noyau local hors ligne.'} />;
  else if (items === null) body = <div className="home-loading"><Spinner /> Chargement…</div>;
  else {
    const total = items.length;
    const capped = total >= 150; // list endpoint default limit (CONTRACT §4)
    const stats = [
      { label: 'Souvenirs', value: `${total}${capped ? '+' : ''}` },
      { label: 'Épinglés', value: String(items.filter(m => m.pinned).length) },
      { label: 'Catégories', value: String(new Set(items.map(m => m.category)).size) },
      // « verified » (checked) and « user_confirmed » (stated by the user) are distinct (compose._verif).
      { label: 'Vérifiés / confirmés', value: String(items.filter(m => m.verification === 'verified' || m.verification === 'user_confirmed').length) },
    ];
    body = (
      <>
        <div className="home-stats">
          {stats.map(s => <div key={s.label} className="home-stat"><strong>{s.value}</strong><span>{s.label}</span></div>)}
        </div>
        {capped && <p className="home-source">La liste est limitée à 150 souvenirs : le total réel peut être supérieur.</p>}
      </>
    );
  }
  return (
    <Panel title="Mémoire" icon={<Database size={18} />} onOpen={() => navigate('memory')} className="home-panel">
      {cerveau && <CerveauFlottant place="carte" />}
      {body}
    </Panel>
  );
}

// ------------------------------------------------------------------ studio card (under Mémoire)

type CardView = 'routines' | 'youtube' | 'github' | 'downloads';
const CARD_VIEWS: { id: CardView; title: string; Icon: ComponentType<LucideProps> }[] = [
  { id: 'routines', title: 'Routines', Icon: CalendarCheck },
  { id: 'youtube', title: 'YouTube', Icon: SquarePlay },
  { id: 'github', title: 'GitHub', Icon: GitBranch },
  { id: 'downloads', title: 'Téléchargements', Icon: Download },
];
const CARD_KEY = 'aurora.home.studio-view';
const readCardView = (): CardView => {
  try { const v = localStorage.getItem(CARD_KEY); return CARD_VIEWS.find(c => c.id === v)?.id ?? 'routines'; } catch { return 'routines'; }
};

/** Routines, YouTube, GitHub, Téléchargements in one card, changed with ‹ › like the news carousel. The studio
 *  views share the Studio page's copy of /api/studio (components/studio/store): no second request. */
function StudioCard() {
  const { online, navigate } = useAurora();
  const [view, setView] = useState<CardView>(readCardView);
  useEffect(() => { try { localStorage.setItem(CARD_KEY, view); } catch { /* storage blocked: the choice lasts until reload */ } }, [view]);
  const studio = useStudio(online && view !== 'routines');
  const routines = useRoutines(online && view === 'routines');
  const openSettings = useOpenSettings();
  const idx = Math.max(0, CARD_VIEWS.findIndex(c => c.id === view));
  const cur = CARD_VIEWS[idx];
  const go = (d: number) => setView(CARD_VIEWS[(idx + d + CARD_VIEWS.length) % CARD_VIEWS.length].id);

  let body: ReactNode;
  if (view === 'routines') {
    const items = routines.items;
    body = routines.error ? <Unavailable what="Routines" reason={routines.error} />
      : items === null ? (online ? <div className="home-loading"><Spinner /> Chargement…</div> : <Unavailable what="Routines" reason="Noyau local hors ligne." />)
      : items.length === 0 ? (
        <div className="home-empty">
          <span>Aucune routine. Créez-en une dans <button type="button" className="home-inline-link" onClick={() => openSettings('.set-routines')}>Paramètres › Routines</button>.</span>
        </div>
      ) : <RoutineList items={items} showSteps={items.length <= 2} max={3} onLaunched={routines.reload} />;
  } else if (!studio.data) {
    body = !online ? <Unavailable what="Données du Studio" reason="Noyau local hors ligne." />
      : studio.error ? <Unavailable what="Données du Studio" reason={studio.error} />
      : <div className="home-loading"><Spinner /> Relevé en cours…</div>;
  } else {
    const d = studio.data;
    const src = view === 'youtube' ? d.youtube : view === 'github' ? d.github : d.downloads;
    body = (
      <>
        {view === 'youtube' ? <YouTubeBody src={d.youtube} compact /> : view === 'github' ? <GitHubBody src={d.github} compact /> : <DownloadsBody src={d.downloads} compact />}
        <SourceLine src={src} />
      </>
    );
  }
  return (
    <Panel title={cur.title} icon={<cur.Icon size={18} />} className="home-panel home-studio" actions={
      <div className="home-studio-nav">
        <button type="button" className="icon-btn" onClick={() => go(-1)} aria-label="Vue précédente"><ChevronLeft size={16} /></button>
        <div className="home-dots" role="group" aria-label="Choisir une vue">
          {CARD_VIEWS.map(c => (
            <button key={c.id} type="button" className={`home-dot ${c.id === view ? 'home-dot-on' : ''}`} onClick={() => setView(c.id)}
              aria-label={c.title} aria-current={c.id === view ? 'true' : undefined} />
          ))}
        </div>
        <button type="button" className="icon-btn" onClick={() => go(1)} aria-label="Vue suivante"><ChevronRight size={16} /></button>
      </div>
    }>
      <div className="home-studio-body" aria-live="polite">{body}</div>
      <button type="button" className="home-studio-open" onClick={() => navigate('studio')}>Ouvrir dans Studio <ChevronRight size={13} aria-hidden="true" /></button>
    </Panel>
  );
}

// ------------------------------------------------------------------ page

export default function HomePage() {
  // Le cerveau mémoire va dans la carte Mémoire quand la colonne de gauche est à côté du centre (même seuil que
  // home.css) ; sinon la carte passe sous le centre et il se pose en bas à gauche du centre. Un seul à la fois.
  const cerveauEnCarte = useMediaQuery('(min-width: 1280px)');
  return (
    <div className="home">
      <aside className="home-left" aria-label="Activité, mémoire et studio">
        <ActivityPanel />
        <MemoryPanel cerveau={cerveauEnCarte} />
        <StudioCard />
      </aside>
      <section className="home-center" aria-label="Modules">
        <p className="home-greeting">Bonjour ! Comment puis-je vous aider aujourd'hui ?</p>
        <CentreAccueil cerveau={!cerveauEnCarte} />
      </section>
      <aside className="home-right" aria-label="Système, météo et actualités">
        <SystemPanel />
        <WeatherPanel />
        <NewsPanel />
      </aside>
    </div>
  );
}
