// Studio : tableau de bord de l'activité de créateur (maquette « AI Studio »), adapté à AURORA.
// Onglets en haut, globe au centre avec les six sources en orbite, panneaux autour. Chaque chiffre vient
// de GET /api/studio (un vrai outil par source) ; une source non connectée le dit et reste éteinte.
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ComponentType, type ReactNode } from 'react';
import {
  CalendarCheck, Download, GitBranch, Globe as GlobeIcon, House, Lock, MessageSquareText, MessagesSquare, RefreshCw, Rocket, Settings,
  SquarePlay, type LucideProps,
} from 'lucide-react';
import Globe, { type GlobeHud, type GlobeRing } from '../components/Globe';
import { Panel, Spinner, Unavailable } from '../components/ui';
import { useAurora } from '../lib/store';
import { fmtTime } from '../lib/format';
import { useElementSize, useReducedMotion } from '../components/globe/hooks';
import type { StudioData } from '../lib/types';
import { useStudio } from '../components/studio/store';
import {
  CommentsBody, DiscordBody, DownloadsBody, ExtLink, GitHubBody, KickstarterBody, RoutineList, SOURCES, SiteBody, SourceLine, StateBadge, YouTubeBody,
  count, stateInfo, useOpenSettings, useRoutines, type SourceKey,
} from '../components/studio/parts';

type TabId = 'home' | 'site' | 'youtube' | 'discord' | 'github' | 'kickstarter' | 'routine' | 'downloads';

/** Same order as the mock-up's top bar; lucide icons like the sidebar (brand icons are absent from lucide 1.x). */
const TABS: { id: TabId; label: string; Icon: ComponentType<LucideProps> }[] = [
  { id: 'home', label: 'Accueil', Icon: House },
  { id: 'site', label: 'Site', Icon: GlobeIcon },
  { id: 'youtube', label: 'YouTube', Icon: SquarePlay },
  { id: 'discord', label: 'Discord', Icon: MessagesSquare },
  { id: 'github', label: 'GitHub', Icon: GitBranch },
  { id: 'kickstarter', label: 'Kickstarter', Icon: Rocket },
  { id: 'routine', label: 'Routine', Icon: CalendarCheck },
  { id: 'downloads', label: 'Téléchargements', Icon: Download },
];

/** Same rings as the home globe (HomePage HOME_RINGS). */
const ORBIT_RINGS: GlobeRing[] = [
  { radius: 1.38, flatten: 0.22, tilt: -14, dots: 2, speed: 0.22 },
  { radius: 1.2, flatten: 0.36, tilt: 26, dots: 1, speed: -0.3, dashed: true, color: '138,92,255', width: 0.9 },
];
const NODE_W = 168;
const NODE_H = 52;

/** The short figure shown on each orbiting source (or why there is none). */
function nodeFigures(d: StudioData): Record<SourceKey, string> {
  const why = (s: { state: StudioData['site']['state'] }) => stateInfo(s.state).label;
  return {
    site: d.site.data ? count(d.site.data.visitors, 'visiteur', 'visiteurs') : why(d.site),
    youtube: d.youtube.data ? count(d.youtube.data.video_count, 'vidéo', 'vidéos') : why(d.youtube),
    discord: d.discord.data ? count(d.discord.data.members, 'membre', 'membres') : why(d.discord),
    github: d.github.data ? count(d.github.data.totals.stars, 'étoile', 'étoiles') : why(d.github),
    kickstarter: d.kickstarter.state === 'ok' ? 'campagne liée' : d.kickstarter.state === 'not_connected' ? 'aucune campagne' : why(d.kickstarter),
    downloads: d.downloads.data ? count(d.downloads.data.total, 'téléchargement', 'téléchargements') : why(d.downloads),
  };
}

// ------------------------------------------------------------------ centre: globe + sources in orbit

/** Reuses the home globe and its rings (components/Globe): the rings light up and turn while a reading
 *  is in flight. The six sources orbit on an ellipse (CSS only); a source is lit once its data arrived,
 *  grey when not connected. Static under « animations réduites ». */
function StudioOrbit({ data, loading, onPick }: { data: StudioData | null; loading: boolean; onPick: (id: TabId) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const { width, height } = useElementSize(ref);
  const wide = width >= 480;   // the centre is ≈ 555 px wide at 1280 px
  const hud = useMemo<GlobeHud>(() => ({ active: loading }), [loading]);
  const rx = Math.max(0, width / 2 - NODE_W / 2 - 4);
  const ry = Math.max(0, height / 2 - NODE_H / 2 - 4);
  const figures = data ? nodeFigures(data) : null;
  const lit = data ? SOURCES.filter(s => data[s.key].state === 'ok').length : 0;
  return (
    <div ref={ref} className={`studio-orbit ${wide ? 'studio-orbit-wide' : 'studio-orbit-list'}`}
      style={{ '--rx': `${rx}px`, '--ry': `${ry}px` } as CSSProperties}>
      {wide && (
        <svg className="studio-orbit-track" width={width} height={height} aria-hidden="true">
          <ellipse cx={width / 2} cy={height / 2} rx={rx} ry={ry} />
        </svg>
      )}
      <div className="studio-orbit-globe">
        <Globe detail="110m" autoRotate rotateSpeed={5} rings={ORBIT_RINGS} hud={hud} initialCenter={{ lat: 30, lon: -20 }}
          ariaLabel="Globe terrestre en rotation (décor)" />
      </div>
      <div className="studio-orbit-ring" role="group" aria-label="Sources du Studio">
        {SOURCES.map((s, i) => {
          const src = data?.[s.key];
          const look = !src ? 'wait' : src.state === 'ok' ? 'on' : src.state === 'error' || src.state === 'offline' ? 'err' : 'off';
          const figure = figures?.[s.key] ?? (loading ? 'relevé en cours…' : '—');
          return (
            <div key={s.key} className={`studio-sat studio-sat-${look}`} style={{ '--slot': `${-90 + i * 60}deg` } as CSSProperties}>
              <span className="studio-beam" aria-hidden="true" />
              <button type="button" className="studio-node" onClick={() => onPick(s.key)}
                aria-label={`${s.label} : ${src ? stateInfo(src.state).label : 'en attente'}, ${figure}. Aller au panneau.`}>
                {/* replayed once each time fresh data arrives */}
                {look === 'on' && src?.fetched_at && <span key={src.fetched_at} className="studio-node-pulse" aria-hidden="true" />}
                <span className="studio-node-icon"><s.Icon size={18} aria-hidden="true" /></span>
                <span className="studio-node-text"><strong>{s.label}</strong><small>{figure}</small></span>
              </button>
            </div>
          );
        })}
      </div>
      <p className="studio-orbit-caption" role="status">
        {data ? `${lit} source${lit > 1 ? 's' : ''} sur ${SOURCES.length} reliée${lit > 1 ? 's' : ''} · relevé à ${fmtTime(data.generated_at, false)}`
          : loading ? 'Relevé en cours…' : 'Aucun relevé'}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ page

/** Placeholder of a source panel before the first reading (or when the whole request failed). */
function Waiting({ loading, error, online }: { loading: boolean; error: string; online: boolean }) {
  if (!online) return <Unavailable what="Données" reason="Noyau local hors ligne." />;
  if (error) return <Unavailable what="Données" reason={error} />;
  return <div className="studio-wait">{loading ? <><Spinner /> Relevé en cours…</> : 'Aucun relevé pour le moment.'}</div>;
}

/** GitHub repository page → its releases page; anything else unchanged. */
const releasesUrl = (url: string | undefined) => (url && /^https:\/\/github\.com\/[^/]+\/[^/]+\/?$/i.test(url) ? `${url.replace(/\/$/, '')}/releases` : url);

export default function StudioPage() {
  const { online, navigate } = useAurora();
  const studio = useStudio(online);
  const routines = useRoutines(online);
  const openSettings = useOpenSettings();
  const reduced = useReducedMotion();
  const [active, setActive] = useState<TabId>('home');
  const [flash, setFlash] = useState<TabId | null>(null);
  const flashTimer = useRef(0);
  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  const { data, loading, error } = studio;
  const links = data?.links;

  /** Scroll to a panel, highlight it a moment and move the focus there (keyboard users). */
  const go = (id: TabId) => {
    setActive(id);
    const el = document.getElementById(`studio-${id}`);
    if (!el) return;
    el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: id === 'home' ? 'center' : 'start' });
    el.focus({ preventScroll: true });
    setFlash(id);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlash(null), 1600);
  };
  const refresh = () => { studio.refresh(); routines.reload(); };

  const slot = (id: TabId | 'comments', area: string, children: ReactNode) => (
    <div id={`studio-${id}`} tabIndex={-1} className={`studio-slot studio-a-${area} ${flash === id ? 'studio-flash' : ''}`}>{children}</div>
  );
  const waiting = <Waiting loading={loading} error={error} online={online} />;
  const badge = (key: SourceKey) => (data ? <StateBadge state={data[key].state} /> : null);

  return (
    <div className="studio">
      <nav className="studio-tabs" aria-label="Sections du Studio">
        <div className="studio-tab-list">
          {TABS.map(t => {
            const src = t.id !== 'home' && t.id !== 'routine' ? data?.[t.id] : undefined;
            return (
              <button key={t.id} type="button" className={`studio-tab ${active === t.id ? 'active' : ''}`} onClick={() => go(t.id)}
                aria-current={active === t.id ? 'true' : undefined} title={src ? `${t.label} : ${stateInfo(src.state).label}` : t.label}>
                <span className="studio-tab-icon">
                  <t.Icon size={20} aria-hidden="true" />
                  {src && <span className={`studio-tab-dot ${src.state === 'ok' ? 'on' : ''}`} aria-hidden="true" />}
                </span>
                <span>{t.label}</span>
              </button>
            );
          })}
        </div>
        <div className="studio-tabs-end">
          <span className="studio-stamp">
            {data ? <>Relevé à {fmtTime(data.generated_at, false)}</> : loading ? 'Relevé en cours…' : 'Aucun relevé'}
          </span>
          <button type="button" className="btn btn-sm" onClick={refresh} disabled={!online || loading}
            title="Relire toutes les sources maintenant (vide le cache du service)">
            <RefreshCw size={14} className={loading ? 'studio-spin' : ''} aria-hidden="true" /> Actualiser
          </button>
        </div>
      </nav>

      {data?.mode === 'prive' && (
        <div className="studio-banner" role="status">
          <Lock size={16} aria-hidden="true" />
          <span>Mode Privé : AURORA ne se connecte à aucun service en ligne, les sources restent éteintes. Le mode Équilibré ou Avancé permet de les relever.</span>
          <button type="button" className="btn btn-sm" onClick={() => navigate('settings')}><Settings size={13} aria-hidden="true" /> Paramètres</button>
        </div>
      )}
      {error && data && (
        <div className="error-banner" role="alert">Nouveau relevé impossible ({error}) : affichage du relevé de {fmtTime(data.generated_at, false)}.</div>
      )}

      <div className="studio-grid">
        {slot('home', 'center', <StudioOrbit data={data} loading={loading} onPick={go} />)}

        {slot('site', 'site', (
          <Panel className="studio-panel" title="Analyse du site" icon={<GlobeIcon size={18} />}
            subtitle={data?.site.data ? `${data.site.data.site} · ${data.site.data.days} derniers jours` : 'Netlify Analytics'}
            actions={<>{badge('site')}<ExtLink href={links?.site} label="Ouvrir le site" /></>}>
            {data ? <><SiteBody src={data.site} /><SourceLine src={data.site} /></> : waiting}
          </Panel>
        ))}

        {slot('comments', 'com', (
          <Panel className="studio-panel" title="Commentaires" subtitle="YouTube" icon={<MessageSquareText size={18} />}
            actions={<><StateBadge state={data?.comments?.state ?? 'not_connected'} /><ExtLink href={links?.youtube_studio} label="Ouvrir YouTube Studio" /></>}>
            {data ? <><CommentsBody src={data.comments} />
              <p className="studio-muted">Pour répondre, ouvrez YouTube Studio : AURORA lit seulement, elle ne poste rien.</p>
              {data.comments && <SourceLine src={data.comments} />}</> : waiting}
          </Panel>
        ))}

        {slot('downloads', 'dl', (
          <Panel className="studio-panel" title="Téléchargements" subtitle="Versions publiées sur GitHub" icon={<Download size={18} />}
            actions={<>{badge('downloads')}<ExtLink href={releasesUrl(data?.downloads.data?.repos[0]?.url ?? links?.github[0])} label="Ouvrir les versions sur GitHub" /></>}>
            {data ? <><DownloadsBody src={data.downloads} history={data.history} /><SourceLine src={data.downloads} /></> : waiting}
          </Panel>
        ))}

        {slot('routine', 'rout', (
          <Panel className="studio-panel" title="Routine du jour" subtitle="Routines d’AURORA, lancées à la demande ou à leur heure" icon={<CalendarCheck size={18} />}
            actions={<button type="button" className="btn btn-sm btn-ghost" onClick={() => openSettings('.set-routines')}>Gérer</button>}>
            {routines.error ? <Unavailable what="Routines" reason={routines.error} />
              : routines.items === null ? <div className="studio-wait">{online ? <><Spinner /> Chargement des routines…</> : 'Noyau local hors ligne.'}</div>
              : routines.items.length === 0 ? (
                <div className="studio-note">
                  <strong>Aucune routine</strong>
                  <span>Une routine enchaîne des demandes enregistrées une fois : elle se crée dans Paramètres › Routines.</span>
                  <div className="studio-note-actions"><button type="button" className="btn btn-sm" onClick={() => openSettings('.set-routines')}>Paramètres › Routines</button></div>
                </div>
              ) : <RoutineList items={routines.items} onLaunched={routines.reload} />}
            <p className="studio-source">Source : routines enregistrées dans AURORA. L’horaire d’une routine se règle dans Paramètres › Routines ; pas d’étape cochée.</p>
          </Panel>
        ))}

        {slot('youtube', 'yt', (
          <Panel className="studio-panel" title="YouTube" subtitle="Chaîne publique" icon={<SquarePlay size={18} />}
            actions={<>{badge('youtube')}<ExtLink href={links?.youtube} label="Ouvrir la chaîne YouTube" /></>}>
            {data ? <><YouTubeBody src={data.youtube} history={data.history} />
              <div className="studio-actions"><ExtLink href={links?.youtube_studio} label="Ouvrir YouTube Studio" text="YouTube Studio" /></div>
              <SourceLine src={data.youtube} /></> : waiting}
          </Panel>
        ))}

        {slot('discord', 'dc', (
          <Panel className="studio-panel" title="Discord" subtitle="Serveur public" icon={<MessagesSquare size={18} />}
            actions={<>{badge('discord')}<ExtLink href={links?.discord} label="Ouvrir Discord" /></>}>
            {data ? <><DiscordBody src={data.discord} history={data.history} /><SourceLine src={data.discord} /></> : waiting}
          </Panel>
        ))}

        {slot('github', 'gh', (
          <Panel className="studio-panel" title="GitHub" subtitle="Activité publique" icon={<GitBranch size={18} />}
            actions={<>{badge('github')}<ExtLink href={links?.github[0]} label="Ouvrir GitHub" /></>}>
            {data ? <><GitHubBody src={data.github} history={data.history} /><SourceLine src={data.github} /></> : waiting}
          </Panel>
        ))}

        {slot('kickstarter', 'ks', (
          <Panel className="studio-panel" title="Kickstarter" subtitle="Campagne" icon={<Rocket size={18} />}
            actions={<>{badge('kickstarter')}<ExtLink href={links?.kickstarter ?? 'https://www.kickstarter.com/'} label="Ouvrir Kickstarter" /></>}>
            {data ? <><KickstarterBody src={data.kickstarter} link={links?.kickstarter ?? null} /><SourceLine src={data.kickstarter} /></> : waiting}
          </Panel>
        ))}
      </div>
    </div>
  );
}
