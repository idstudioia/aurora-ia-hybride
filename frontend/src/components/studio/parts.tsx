// Studio: pieces shared by the Studio page and the home card. Every figure comes from GET /api/studio
// (one real tool per source); an absent value is « — » and a source that is not connected says why.
import { useCallback, useEffect, useState, type ComponentType, type ReactNode } from 'react';
import {
  Download, ExternalLink, GitBranch, Globe, MessagesSquare, Play, Rocket, Settings, SquarePlay, type LucideProps,
} from 'lucide-react';
import { api } from '../../lib/api';
import { useAurora } from '../../lib/store';
import { fmtPct, fmtTime, relativeTime, scheduleText } from '../../lib/format';
import { prefersReducedMotion } from '../../lib/motion';
import type { Routine, StudioData, StudioSource, StudioState } from '../../lib/types';
import { Badge, Sparkline, Spinner } from '../ui';
import { useSend } from '../chat/useSend';
import { fmtNum, safeUrl } from '../chat/text';
import '../../pages/studio.css';

// ------------------------------------------------------------------ sources and formatting

export type SourceKey = 'site' | 'youtube' | 'discord' | 'github' | 'kickstarter' | 'downloads';

/** Brand icons are absent from lucide-react 1.x: neutral icons instead. */
export const SOURCES: { key: SourceKey; label: string; Icon: ComponentType<LucideProps> }[] = [
  { key: 'site', label: 'Site', Icon: Globe },
  { key: 'youtube', label: 'YouTube', Icon: SquarePlay },
  { key: 'discord', label: 'Discord', Icon: MessagesSquare },
  { key: 'github', label: 'GitHub', Icon: GitBranch },
  { key: 'kickstarter', label: 'Kickstarter', Icon: Rocket },
  { key: 'downloads', label: 'Téléchargements', Icon: Download },
];

/** Whole count in French notation; null → « — ». */
export const whole = (v: number | null | undefined) => fmtNum(v, 0);
/** « 0 vidéo », « 1 vidéo », « 2 vidéos »; null → « — vidéos ». */
export const count = (v: number | null | undefined, one: string, many: string) =>
  `${whole(v)} ${typeof v === 'number' && Math.abs(v) < 2 ? one : many}`;
/** « 29 oct. » — a day « AAAA-MM-JJ » is read as a local date (not UTC midnight), an ISO instant as is. */
export const dayLabel = (iso: string) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
};

const STATE_INFO: Record<StudioState, { label: string; tone: 'green' | 'default' | 'violet' | 'amber' | 'red'; title: string }> = {
  ok: { label: 'connecté', tone: 'green', title: 'Connecté' },
  not_connected: { label: 'non connecté', tone: 'default', title: 'Non connecté' },
  private: { label: 'mode Privé', tone: 'violet', title: 'Mode Privé : source non relevée' },
  offline: { label: 'hors ligne', tone: 'amber', title: 'Hors ligne' },
  error: { label: 'erreur', tone: 'red', title: 'Relevé impossible' },
};
export const stateInfo = (s: StudioState | undefined) => STATE_INFO[s ?? 'error'] ?? STATE_INFO.error;

export function StateBadge({ state }: { state: StudioState }) {
  const info = stateInfo(state);
  return <Badge tone={info.tone}>{info.label}</Badge>;
}

/** Opens Paramètres and scrolls to one of its sections (the page is loaded on demand). */
export function useOpenSettings(): (selector: string) => void {
  const { navigate } = useAurora();
  return useCallback((selector: string) => {
    navigate('settings');
    let tries = 0;
    const seek = () => {
      const el = document.querySelector(selector);
      if (el) {
        el.scrollIntoView({ block: 'start', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
        // 16/11/2026: the sections above fill in as their data arrives and push this one down, out of the screen —
        // aimed again, without animation, while the page settles.
        for (const later of [600, 1200, 2000]) window.setTimeout(() => { if (el.isConnected) el.scrollIntoView({ block: 'start', behavior: 'auto' }); }, later);
        return;
      }
      if (++tries < 40) window.setTimeout(seek, 100);
    };
    window.setTimeout(seek, 50);
  }, [navigate]);
}

export function StudioSettingsButton({ label = 'Paramètres › Studio' }: { label?: string }) {
  const open = useOpenSettings();
  return <button type="button" className="btn btn-sm" onClick={() => open('#set-studio')}><Settings size={13} aria-hidden="true" /> {label}</button>;
}

const reasonsOf = (src: StudioSource<unknown>) => [src.summary, src.error].filter((t, i, all): t is string => !!t && all.indexOf(t) === i);
const noteTone = (s: StudioState) => (s === 'error' || s === 'offline' ? 'studio-note-warn' : s === 'private' ? 'studio-note-private' : '');

/** Why a source shows no figure (its own French reason) and, when it is not connected, how to plug it. */
export function SourceNote({ src, connect }: { src: StudioSource<unknown>; connect?: ReactNode }) {
  return (
    <div className={`studio-note ${noteTone(src.state)}`}>
      <strong>{stateInfo(src.state).title}</strong>
      {reasonsOf(src).map(r => <span key={r}>{r}</span>)}
      {connect && src.state === 'not_connected' && <div className="studio-note-actions">{connect}</div>}
    </div>
  );
}

/** « Source : … · relevé à 14:42 ». */
export function SourceLine({ src }: { src: StudioSource<unknown> }) {
  const list = src.sources ?? [];
  const first = list.find(s => safeUrl(s.url));
  const href = safeUrl(first?.url);
  // provenance is a code (« github », « youtube-rss »): the readable title first, like the home weather card
  const label = first?.title || src.provenance || 'source non précisée';
  const more = list.length - (first ? 1 : 0);
  return (
    <p className="studio-source">
      Source : {href ? <a href={href} target="_blank" rel="noopener noreferrer">{label}</a> : label}
      {more > 0 && <> · {more} autre{more > 1 ? 's' : ''}</>}
      {src.fetched_at && <> · relevé à {fmtTime(src.fetched_at, false)}</>}
    </p>
  );
}

/** Opens the real site in a new tab. `text` → small button, else an icon button. */
export function ExtLink({ href, label, text }: { href: string | null | undefined; label: string; text?: string }) {
  const url = safeUrl(href);
  if (!url) return null;
  return (
    <a className={text ? 'btn btn-sm studio-ext' : 'icon-btn studio-ext'} href={url} target="_blank" rel="noopener noreferrer"
      title={`${label} (nouvel onglet)`} aria-label={`${label} (nouvel onglet)`}>
      <ExternalLink size={text ? 13 : 16} aria-hidden="true" />{text}
    </a>
  );
}

/** Plain external text link (http(s) only), else the text alone. */
function TextLink({ href, children }: { href: string | null | undefined; children: ReactNode }) {
  const url = safeUrl(href);
  return url ? <a href={url} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
}

export function Stats({ items }: { items: { label: string; value: ReactNode; hint?: string }[] }) {
  return (
    <div className="studio-stats">
      {items.map(s => <div key={s.label} className="studio-stat"><strong>{s.value}</strong><span>{s.label}</span>{s.hint && <small>{s.hint}</small>}</div>)}
    </div>
  );
}

function Bar({ label, value, max, text, title }: { label: string; value: number; max: number; text: string; title?: string }) {
  const pct = max > 0 && value > 0 ? Math.max(2, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="studio-bar" title={title}>
      <span className="studio-bar-label">{label}</span>
      <span className="studio-bar-track" aria-hidden="true"><span className="studio-bar-fill" style={{ width: `${pct}%` }} /></span>
      <span className="studio-bar-value">{text}</span>
    </div>
  );
}

type HistoryField = 'downloads' | 'discord_members' | 'youtube_views' | 'github_stars' | 'youtube_subscribers';

/** AURORA's own daily readings: drawn from two of them on. */
export function HistoryCurve({ history, field, label }: { history: StudioData['history'] | undefined; field: HistoryField; label: string }) {
  const pts = (history ?? []).flatMap(h => (typeof h[field] === 'number' ? [{ date: h.date, v: h[field] as number }] : []));
  if (pts.length < 2) return null;
  const last = pts[pts.length - 1];
  return (
    <div className="studio-history" role="img" aria-label={`${label} : ${pts.length} relevés depuis le ${dayLabel(pts[0].date)}, dernier ${whole(last.v)}`}>
      <span>{label} · {pts.length} relevés depuis le {dayLabel(pts[0].date)}</span>
      <Sparkline values={pts.map(p => p.v)} width={240} height={34} />
    </div>
  );
}

function Thumb({ src, small }: { src: string | null; small?: boolean }) {
  const [broken, setBroken] = useState(false);
  const url = safeUrl(src);
  const cls = `studio-thumb ${small ? 'studio-thumb-sm' : ''}`;
  if (!url || broken) return <span className={`${cls} studio-thumb-none`} aria-hidden="true"><SquarePlay size={16} /></span>;
  return <img className={cls} src={url} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setBroken(true)} />;
}

// ------------------------------------------------------------------ views (page panels and home card)

export function SiteBody({ src }: { src: StudioData['site'] }) {
  const d = src.data;
  if (!d) return <SourceNote src={src} connect={<StudioSettingsButton />} />;
  const series = d.series.filter(p => typeof p.visitors === 'number');
  return (
    <>
      {src.state !== 'ok' && <SourceNote src={src} connect={<StudioSettingsButton />} />}
      <Stats items={[{ label: 'Visiteurs', value: whole(d.visitors) }, { label: 'Pages vues', value: whole(d.pageviews) }]} />
      {series.length >= 2 ? (
        <div className="studio-curve" role="img" aria-label={`Visiteurs par jour du ${dayLabel(series[0].date)} au ${dayLabel(series[series.length - 1].date)}`}>
          <Sparkline values={series.map(p => p.visitors as number)} width={300} height={70} />
          <div className="studio-curve-axis"><span>{dayLabel(series[0].date)}</span><span>{dayLabel(series[series.length - 1].date)}</span></div>
        </div>
      ) : <p className="studio-muted">Courbe : moins de deux jours relevés.</p>}
    </>
  );
}

export function YouTubeBody({ src, history, compact = false }: { src: StudioData['youtube']; history?: StudioData['history']; compact?: boolean }) {
  const d = src.data;
  if (!d) return <SourceNote src={src} connect={<StudioSettingsButton />} />;
  const videos = d.videos.slice(0, compact ? 3 : 6);
  const api = d.stats === 'api';
  const subscribers = api
    ? { label: 'Abonnés', value: d.subscribers_hidden ? '—' : whole(d.subscribers), hint: d.subscribers_hidden ? 'masqués par la chaîne' : 'API YouTube' }
    : { label: 'Abonnés', value: '—', hint: 'clé API absente' };
  return (
    <>
      {src.state !== 'ok' && <SourceNote src={src} />}
      <p className="studio-line"><TextLink href={d.channel_url}>{d.channel || 'Chaîne YouTube'}</TextLink></p>
      <Stats items={[
        { label: 'Vidéos', value: whole(api && d.channel_videos != null ? d.channel_videos : d.video_count), hint: api ? 'toute la chaîne' : 'flux public' },
        { label: 'Vues', value: whole(api && d.channel_views != null ? d.channel_views : d.total_views), hint: api ? 'toute la chaîne' : 'flux public' },
        subscribers,
      ]} />
      {videos.length === 0 ? <p className="studio-muted">Aucune vidéo dans le flux public.</p> : (
        <ul className={`studio-videos ${compact ? 'studio-videos-compact' : ''}`}>
          {videos.map(v => (
            <li key={v.id}>
              <Thumb src={v.thumbnail} small={compact} />
              <div>
                <TextLink href={v.url}>{v.title || 'Vidéo sans titre'}</TextLink>
                <small>
                  {count(v.views, 'vue', 'vues')}
                  {typeof v.likes === 'number' ? ` · ${count(v.likes, '« j’aime »', '« j’aime »')}` : ''}
                  {typeof v.comments === 'number' ? ` · ${count(v.comments, 'commentaire', 'commentaires')}` : ''}
                  {v.published ? ` · ${relativeTime(v.published)}` : ''}
                </small>
              </div>
            </li>
          ))}
        </ul>
      )}
      {d.stats_error && <p className="studio-muted">Statistiques détaillées indisponibles : {d.stats_error}</p>}
      <p className="studio-muted">
        {api ? 'Abonnés et totaux de la chaîne relus par l’API YouTube (clé de Paramètres › Studio) ; la liste montre les 15 dernières vidéos.'
          : 'Vues additionnées sur les vidéos du flux public (15 dernières au plus). Les abonnés et les commentaires demandent une clé API YouTube (Paramètres › Studio).'}
      </p>
      {!compact && <HistoryCurve history={history} field={api ? 'youtube_subscribers' : 'youtube_views'} label={api ? 'Abonnés' : 'Vues'} />}
    </>
  );
}

/** The latest comment threads of the channel (YouTube Manager's reading, in AURORA): those without an answer
 *  first. The text is the commenter's, shown as plain text: data, never an instruction. Answering stays on YouTube. */
export function CommentsBody({ src, compact = false }: { src: StudioData['comments'] | undefined; compact?: boolean }) {
  if (!src) return <p className="studio-muted">Commentaires : le service n’a pas encore cette source (redémarrage nécessaire).</p>;
  const d = src.data;
  if (!d) return <SourceNote src={src} connect={<StudioSettingsButton />} />;
  const shown = [...d.threads].sort((a, b) => Number(b.unanswered) - Number(a.unanswered)).slice(0, compact ? 3 : 8);
  return (
    <>
      <Stats items={[
        { label: 'Récents', value: whole(d.total), hint: '50 derniers fils au plus' },
        { label: 'Sans réponse', value: whole(d.unanswered) },
        ...(d.uncertain ? [{ label: 'Incertains', value: whole(d.uncertain), hint: 'réponses non toutes renvoyées' }] : []),
        ...(d.own ? [{ label: 'Les vôtres', value: whole(d.own), hint: 'écartés de la liste' }] : []),
      ]} />
      {shown.length === 0 ? (
        <p className="studio-muted">
          Aucun commentaire récent d’un autre compte que le vôtre{d.own ? ` : les ${whole(d.own)} fil(s) récent(s) sont les vôtres` : ''}.
        </p>
      ) : (
        <ul className="studio-comments">
          {shown.map(t => (
            <li key={t.id} className={t.unanswered ? 'studio-comment-todo' : ''}>
              <div className="studio-comment-head">
                <strong>{t.author || 'Anonyme'}</strong>
                {t.unanswered && <Badge tone="amber">sans réponse</Badge>}
                {t.published && <small>{relativeTime(t.published)}</small>}
              </div>
              <p className="studio-comment-text">{t.text}</p>
              <small className="studio-comment-video">
                <TextLink href={t.url}>{t.video_title || 'Voir sur YouTube'}</TextLink>
                {t.replies > 0 ? ` · ${count(t.replies, 'réponse', 'réponses')}` : ''}
              </small>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export function DiscordBody({ src, history }: { src: StudioData['discord']; history?: StudioData['history'] }) {
  const d = src.data;
  if (!d) return <SourceNote src={src} connect={<StudioSettingsButton />} />;
  const icon = safeUrl(d.icon_url);
  return (
    <>
      {src.state !== 'ok' && <SourceNote src={src} />}
      <div className="studio-line studio-discord">
        {icon && <img src={icon} alt="" width={28} height={28} loading="lazy" referrerPolicy="no-referrer" />}
        <strong>{d.name || 'Serveur Discord'}</strong>
      </div>
      <Stats items={[{ label: 'Membres', value: whole(d.members) }, { label: 'En ligne', value: whole(d.online) }]} />
      {safeUrl(d.invite_url) && (
        <p className="studio-invite"><span className="mono">{d.invite_url}</span><ExtLink href={d.invite_url} label="Ouvrir le lien d’invitation Discord" text="Invitation" /></p>
      )}
      <HistoryCurve history={history} field="discord_members" label="Membres" />
    </>
  );
}

export function GitHubBody({ src, history, compact = false }: { src: StudioData['github']; history?: StudioData['history']; compact?: boolean }) {
  const d = src.data;
  if (!d) return <SourceNote src={src} connect={<StudioSettingsButton />} />;
  const t = d.totals;
  const activity = d.activity.slice(0, compact ? 5 : 7);
  return (
    <>
      {src.state !== 'ok' && <SourceNote src={src} />}
      <Stats items={[
        { label: 'Dépôts', value: whole(t.repos) },
        { label: 'Étoiles', value: whole(t.stars) },
        ...(compact ? [] : [{ label: 'Forks', value: whole(t.forks) }]),
        { label: 'Abonnés', value: whole(t.followers) },
      ]} />
      {!compact && d.accounts.length > 0 && (
        <div className="studio-chips">
          {d.accounts.map(a => <span key={a.login} className="studio-chip"><TextLink href={a.url}>{a.login}</TextLink></span>)}
        </div>
      )}
      <h4 className="studio-h4">Activité publique récente</h4>
      {activity.length === 0 ? <p className="studio-muted">Aucune activité publique récente.</p> : (
        <ul className="studio-activity">
          {activity.map((a, i) => (
            <li key={`${a.created_at}-${i}`}>
              <span className="studio-activity-copy">
                <TextLink href={a.url}>{a.label || a.type}</TextLink>
                <small>{a.repo}</small>
              </span>
              <time dateTime={a.created_at}>{relativeTime(a.created_at)}</time>
            </li>
          ))}
        </ul>
      )}
      {!compact && <HistoryCurve history={history} field="github_stars" label="Étoiles" />}
    </>
  );
}

type DownloadsData = NonNullable<StudioData['downloads']['data']>;
type Release = DownloadsData['repos'][number]['releases'][number] & { repo: string };

/** Every release of every repository, the most recent first. */
const releasesOf = (d: DownloadsData): Release[] => d.repos
  .flatMap(r => r.releases.map(x => ({ ...x, repo: r.full_name })))
  .sort((a, b) => (b.published_at ?? '').localeCompare(a.published_at ?? ''));
const repoName = (full: string) => full.split('/').pop() || full;

export function DownloadsBody({ src, history, compact = false }: { src: StudioData['downloads']; history?: StudioData['history']; compact?: boolean }) {
  const d = src.data;
  if (!d) return <SourceNote src={src} connect={<StudioSettingsButton />} />;
  const releases = releasesOf(d);
  const latest = releases[0];
  const several = d.repos.length > 1;
  if (compact) {
    return (
      <>
        {src.state !== 'ok' && <SourceNote src={src} />}
        <Stats items={[{ label: 'Téléchargements', value: whole(d.total), hint: 'toutes versions' }]} />
        {latest ? (
          <p className="studio-line">Dernière version : <strong>{latest.tag || latest.name}</strong>{several ? ` (${repoName(latest.repo)})` : ''}
            {' · '}{count(latest.downloads, 'téléchargement', 'téléchargements')}{latest.published_at ? ` · ${relativeTime(latest.published_at)}` : ''}</p>
        ) : <p className="studio-muted">Aucune version publiée.</p>}
      </>
    );
  }
  const shown = releases.slice(0, 8);
  const maxRel = Math.max(0, ...shown.map(r => r.downloads));
  // Outside the contract but sent by the service: repositories it could not read (French sentences).
  const problems = (d as { problems?: unknown }).problems;
  const unread = Array.isArray(problems) ? problems.filter((p): p is string => typeof p === 'string' && !!p) : [];
  return (
    <>
      {src.state !== 'ok' && <SourceNote src={src} />}
      {unread.length > 0 && <p className="studio-muted">Non relevé : {unread.join(' ')}</p>}
      <Stats items={[{ label: 'Téléchargements', value: whole(d.total), hint: 'toutes versions' }, { label: 'Versions', value: whole(releases.length) }]} />
      {/* 26/11/2026, ID Studio IA : « ajoute dans Aurora les téléchargements fait pour sa version de GitHub ». With
          several repositories, each one's own total; one without a release says why it counts nothing. */}
      {several && (
        <>
          <h4 className="studio-h4">Par dépôt</h4>
          <div className="studio-bars">
            {d.repos.map(r => (
              <Bar key={r.full_name} label={repoName(r.full_name)} value={r.total} max={Math.max(1, d.total)}
                text={r.releases.length ? whole(r.total) : 'aucune version'}
                title={r.releases.length ? `${r.full_name} — ${count(r.releases.length, 'version', 'versions')}`
                  : `${r.full_name} — aucune version publiée : GitHub ne compte que les fichiers joints à une version, pas le ZIP du bouton « Code ».`} />
            ))}
          </div>
        </>
      )}
      <h4 className="studio-h4">Par version</h4>
      {shown.length === 0 ? <p className="studio-muted">Aucune version publiée.</p> : (
        <div className="studio-bars">
          {shown.map(r => (
            <Bar key={`${r.repo}-${r.tag}`} label={several ? `${r.tag} · ${repoName(r.repo)}` : (r.tag || r.name)} value={r.downloads} max={maxRel}
              text={whole(r.downloads)} title={`${r.name || r.tag} — ${r.repo}${r.published_at ? ` — publiée ${relativeTime(r.published_at)}` : ''}`} />
          ))}
          {releases.length > shown.length && <p className="studio-muted">+ {releases.length - shown.length} version(s) plus ancienne(s).</p>}
        </div>
      )}
      <h4 className="studio-h4">Par système <small>d’après les noms des fichiers</small></h4>
      {d.by_os.every(o => !o.downloads) ? <p className="studio-muted">Aucun téléchargement attribué à un système.</p> : (
        <div className="studio-bars">
          {/* share: fraction 0–1 computed by the service */}
          {d.by_os.map(o => <Bar key={o.os} label={o.os} value={o.share} max={1} text={`${fmtPct(o.share * 100)} · ${whole(o.downloads)}`} />)}
        </div>
      )}
      <HistoryCurve history={history} field="downloads" label="Téléchargements" />
    </>
  );
}

export function KickstarterBody({ src, link }: { src: StudioData['kickstarter']; link: string | null }) {
  if (src.state === 'ok') {
    return <p className="studio-line">Campagne liée{safeUrl(link) ? '' : ' (adresse absente)'}. Aucun chiffre n’est encore relevé pour Kickstarter.</p>;
  }
  return (
    <div className={`studio-note ${noteTone(src.state)}`}>
      {/* the service says how to link a campaign (or that a linked one is not measured yet) */}
      <strong>{src.state !== 'not_connected' ? stateInfo(src.state).title : link ? 'Campagne liée' : 'Aucune campagne liée'}</strong>
      {reasonsOf(src).map(r => <span key={r}>{r}</span>)}
      <div className="studio-note-actions"><StudioSettingsButton /></div>
    </div>
  );
}

// ------------------------------------------------------------------ routines

/** AURORA's routines (D1). A schedule (D2, set in Paramètres › Routines) is shown in words; there is no per-step
 *  « done » state: none is shown. */
export function useRoutines(enabled: boolean): { items: Routine[] | null; error: string; reload: () => void } {
  const [items, setItems] = useState<Routine[] | null>(null);
  const [error, setError] = useState('');
  const reload = useCallback(() => {
    api.routines().then(list => { setItems(list); setError(''); })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Service local indisponible'));
  }, []);
  useEffect(() => { if (enabled) reload(); }, [enabled, reload]);
  // 16/11/2026: read again when a routine is saved or deleted elsewhere on the screen (routinesChanged) — the band
  // that proposes a schedule must go as soon as the schedule is set in Paramètres.
  useEffect(() => {
    if (!enabled) return;
    window.addEventListener(ROUTINES_CHANGED, reload);
    return () => window.removeEventListener(ROUTINES_CHANGED, reload);
  }, [enabled, reload]);
  return { items, error, reload };
}

const ROUTINES_CHANGED = 'aurora:routines-changed';
/** Says to every list of routines on the screen (useRoutines: the band, the Studio cards) that one was saved or deleted. */
export const routinesChanged = (): void => { window.dispatchEvent(new Event(ROUTINES_CHANGED)); };

const launchedText = (n: number) => (n > 0 ? `lancée ${whole(n)} fois` : 'jamais lancée');

/** « Lancer » sends « Lance la routine <nom> » exactly as if typed (same path as the Analyse card's actions). */
export function RoutineList({ items, showSteps = false, max = 6, onLaunched }: {
  items: Routine[]; showSteps?: boolean; max?: number; onLaunched?: () => void;
}) {
  const { online } = useAurora();
  const send = useSend();
  const [busy, setBusy] = useState<number | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const start = async (r: Routine) => {
    setBusy(r.id); setNote(null);
    const run = await send(`Lance la routine ${r.name}`, { source: 'text', openDrawer: true, withAttachments: false });
    setBusy(null);
    setNote(run ? { ok: true, text: `Routine « ${r.name} » envoyée.` } : { ok: false, text: 'Envoi impossible : la phrase n’est pas partie.' });
    onLaunched?.();
  };
  const shown = items.slice(0, max);
  return (
    <>
      <ul className="studio-routines">
        {shown.map(r => (
          <li key={r.id}>
            <div className="studio-routine-top">
              <strong>{r.name}</strong>
              <button type="button" className="btn btn-sm btn-primary" onClick={() => void start(r)} disabled={!online || busy !== null}
                aria-label={`Lancer la routine ${r.name}`}>
                {busy === r.id ? <Spinner size={12} /> : <Play size={12} aria-hidden="true" />} Lancer
              </button>
            </div>
            <small>{count(r.steps.length, 'étape', 'étapes')} · {launchedText(r.runs)}{r.last_run_at ? ` · dernier lancement ${relativeTime(r.last_run_at)}` : ''}{r.schedule ? ` · seule ${scheduleText(r.schedule, r.events)}` : ''}</small>
            {showSteps && r.steps.length > 0 && (
              <ol className="studio-routine-steps">
                {r.steps.slice(0, 3).map((s, i) => <li key={i}>{s}</li>)}
                {r.steps.length > 3 && <li className="studio-more">+ {r.steps.length - 3} autre(s)</li>}
              </ol>
            )}
          </li>
        ))}
      </ul>
      {items.length > shown.length && <p className="studio-muted">+ {items.length - shown.length} autre(s) routine(s) dans Paramètres › Routines.</p>}
      {note && <p className={`studio-launch ${note.ok ? '' : 'studio-launch-err'}`} role={note.ok ? 'status' : 'alert'}>{note.text}</p>}
    </>
  );
}
