// Paramètres: intelligence mode, Claude (state, tiers, budget, spend), local AI, folders, quality bench, data.
// The Claude key can be typed here: it goes once to the service, which stores it in the Windows vault and never
// sends it back (only its status: configured, source). Also: answer checks (A4) and the reports bench (A1).
import './settings.css';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AppWindow, Brain, Clapperboard, Cloud, Database, FlaskConical, FolderOpen, FolderPlus, Gauge, Globe, Lock, MapPin, Monitor, MousePointerClick, Power, Scale, ShieldCheck, Terminal, Trash2, X, Zap } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import { INTENT_LABELS, fmtDateTime, relativeTime } from '../lib/format';
import { getPath, pickNum, pickStr } from '../lib/tolerant';
import type {
  AllowedApp, AnthropicKeyStatus, Mode, PcApp, PcApps, Permission, Settings, StartupState, StudioSecretName, StudioSecretStatus, StudioSettings, StudioSettingsInput, TierPrices, Usage, VerifyLevel, YoutubeConnection,
} from '../lib/types';
import { VERIFY_MOD_LABELS } from '../components/chat/labels';
import { Badge, EmptyState, Panel, Spinner, StatusDot, Unavailable } from '../components/ui';
import { parseBench, type BenchReplay, type BenchView } from '../components/sim/bench';
import QualityReports from '../components/QualityReports';
import RoutinesPanel from '../components/RoutinesPanel';
import MakePageButton from '../components/artefacts/MakePageButton';
import { markStudioStale } from '../components/studio/store';
import { takeYoutubeReturn } from '../lib/youtubeReturn';

const MODES: { id: Mode; title: string; icon: ReactNode; text: string }[] = [
  { id: 'prive', title: 'Privé', icon: <Lock size={20} />, text: 'Outils locaux + IA locale, aucun cloud.' },
  { id: 'equilibre', title: 'Équilibré', icon: <Scale size={20} />, text: 'L’IA locale répond d’abord ; Claude seulement si elle ne peut pas répondre, avec votre accord, qui peut valoir pour toute la conversation.' },
  { id: 'avance', title: 'Avancé', icon: <Zap size={20} />, text: 'Comme Équilibré, avec en plus Claude Opus sur demande ; chaque envoi au cloud vous est montré.' },
];

/** Fallback copy of Anthropic list prices (USD / million tokens) from backend providers/pricing.py, used
 *  only when capabilities.cloud.tiers[*].prices is absent. The server computes the real cost of every call. */
const PRICE_REF: Record<string, TierPrices> = {
  'claude-haiku-5-5': { input: 0.10, output: 0.50, cache_read: 0.01, cache_write: 0.125 },
  'claude-sonnet-5-5': { input: 2.00, output: 10.00, cache_read: 0.20, cache_write: 2.50 },
  'claude-opus-5-5': { input: 4.00, output: 20.00, cache_read: 0.20, cache_write: 5.00 },
  'claude-fable-5-1': { input: 10.00, output: 50.00, cache_read: 0.25, cache_write: 12.50 },
};
const TIER_LABELS: Record<string, string> = { rapide: 'Rapide (routage)', standard: 'Standard (par défaut)', expert: 'Expert (sur demande)' };
const EFFORT_LABELS: Record<string, string> = { low: 'faible', medium: 'moyen', high: 'élevé', xhigh: 'très élevé', max: 'maximal' };
const RISK: Record<string, { tone: 'green' | 'amber' | 'red'; label: string }> = { low: { tone: 'green', label: 'faible' }, medium: { tone: 'amber', label: 'moyen' }, high: { tone: 'red', label: 'élevé' } };
const METHOD_COLORS = ['#22c8ff', '#8a5cff', '#2ee6a8', '#ffb547'];
const usd = (v: number) => `${v.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 3 })} $`;
/** Spend in French notation; tiny amounts in cents so they never round to zero. */
const spend = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? '—' : v === 0 ? '0 $'
  : v < 0.01 ? `${(v * 100).toLocaleString('fr-FR', { maximumFractionDigits: 3 })} ¢` : usd(v));
/** Latency in French notation (the bench measures sub-millisecond routing). */
const ms = (v: number | null) => (v === null ? '—' : `${v.toLocaleString('fr-FR', { maximumFractionDigits: v < 10 ? 2 : 0 })} ms`);
/** Prices sent by the service (S2) when complete, else null. */
const servedPrices = (p: TierPrices | undefined): TierPrices | null =>
  p && [p.input, p.output, p.cache_read, p.cache_write].every(v => typeof v === 'number' && Number.isFinite(v)) ? p : null;
/** Embedding models cannot chat (Ollama answers « does not support chat »): heuristic used when the
 *  service does not send chat_models (S5). */
const isEmbeddingModel = (name: string, embedModel: string) => {
  const base = (s: string) => s.trim().toLowerCase().replace(/:latest$/, '');
  const n = base(name);
  return (!!embedModel && n === base(embedModel)) || n.includes('embed') || n.startsWith('bge');
};
/** Same rule as the server (config.py _CONTACT_RE): an e-mail or an http(s) URL. */
const CONTACT_RE = /^(?:[^@\s;()]+@[^@\s;()]+\.[^@\s;()]+|https?:\/\/[^\s;()]+)$/;

type SaveState = { kind: 'busy' | 'ok' | 'err'; text: string } | undefined;
/** Claude key: saved in the Windows vault by the local service, never shown again (CLAUDE.md). */
function ClaudeKeyForm({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<AnthropicKeyStatus | null>(null);
  const [key, setKey] = useState('');
  const [state, setState] = useState<SaveState>(undefined);
  useEffect(() => { api.anthropicKey().then(setStatus).catch(() => setStatus(null)); }, []);
  const run = async (action: () => Promise<AnthropicKeyStatus>, ok: string) => {
    setState({ kind: 'busy', text: 'Enregistrement…' });
    try { setStatus(await action()); setKey(''); setState({ kind: 'ok', text: ok }); onChanged(); }
    catch (err) { setState({ kind: 'err', text: err instanceof Error ? err.message : 'Échec de l’enregistrement' }); }
  };
  const where = !status ? 'État de la clé indisponible'
    : !status.configured ? 'Aucune clé enregistrée'
    : status.source === 'vault' ? 'Clé enregistrée dans le coffre Windows'
    : 'Clé lue dans la variable d’environnement ANTHROPIC_API_KEY (ancienne méthode)';
  return (
    <div className="set-howto">
      <strong><ShieldCheck size={14} /> Clé Claude — coffre Windows</strong>
      <p>{where}.</p>
      {status?.vault_available === false ? <p>Le coffre Windows n’est pas disponible sur ce système.</p> : (
        <div className="set-inline">
          <input className="input" type="password" autoComplete="off" spellCheck={false} placeholder="sk-ant-…"
            value={key} onChange={ev => setKey(ev.target.value)} aria-label="Clé API Anthropic" />
          <button className="btn btn-sm" disabled={key.trim().length < 20} onClick={() => void run(() => api.saveAnthropicKey(key.trim()), 'Clé enregistrée dans le coffre Windows')}>Enregistrer</button>
          {status?.source === 'vault' && (
            <button className="btn btn-sm btn-danger" onClick={() => void run(api.deleteAnthropicKey, 'Clé supprimée du coffre')}>Supprimer</button>
          )}
        </div>
      )}
      <SaveNote s={state} />
      <p className="set-small">La clé est envoyée une seule fois au service local (127.0.0.1, jeton de session), rangée dans le Gestionnaire d’identification de Windows, puis jamais réaffichée ni écrite dans un fichier, un journal ou Git.</p>
    </div>
  );
}

/** The Studio secrets: what each one is for and where it is made (the service checks its shape again). */
const STUDIO_SECRETS: Record<StudioSecretName, { title: string; noun: string; placeholder: string; label: string; help: string }> = {
  netlify: {
    title: 'Jeton Netlify', noun: 'Jeton', placeholder: 'nfp_…', label: 'Jeton d’accès personnel Netlify',
    help: 'Il sert seulement à lire les visites du site Netlify indiqué ci-dessus ; il se crée dans Netlify › User settings › Applications › Personal access tokens, et les visites demandent l’option Netlify Analytics activée sur le site.',
  },
  youtube: {
    title: 'Clé API YouTube', noun: 'Clé', placeholder: 'AIza…', label: 'Clé API YouTube Data v3',
    help: 'Elle sert seulement à lire les données publiques de la chaîne indiquée ci-dessus : abonnés, vues, « j’aime » et commentaires (rien n’est jamais posté avec). Elle se crée dans Google Cloud Console › API et services › Identifiants › Créer des identifiants › Clé API ; limitez-la à « YouTube Data API v3 ». Environ 3 unités du quota YouTube par relevé.',
  },
  youtube_client_id: {
    title: 'ID client OAuth (connexion Google)', noun: 'ID client', placeholder: '…-….apps.googleusercontent.com', label: 'ID client OAuth Google',
    help: 'Il identifie votre client OAuth : Google Cloud Console › API et services › Identifiants › votre « ID client OAuth 2.0 ». Il sert seulement à relier votre chaîne pour publier des commentaires (connexion ci-dessous).',
  },
  youtube_client_secret: {
    title: 'Code secret du client OAuth', noun: 'Code secret', placeholder: 'GOCSPX-…', label: 'Code secret du client OAuth Google',
    help: 'Il est sur la même page que l’ID client. Ne le collez jamais ailleurs (ni dans une conversation) : seulement ici.',
  },
  // 13/11/2026 : les deux services 3D en ligne. AURORA ne s'en sert pas encore ; les clés sont rangées pour ce jour-là.
  meshy: {
    title: 'Clé API Meshy', noun: 'Clé', placeholder: 'msy_…', label: 'Clé API Meshy',
    help: 'Elle se crée sur meshy.ai, dans la page des clés d’API. Chaque modèle demandé à Meshy coûte des crédits de votre compte. Ne la collez jamais ailleurs (ni dans une conversation) : seulement ici.',
  },
  huggingface: {
    title: 'Jeton Hugging Face', noun: 'Jeton', placeholder: 'hf_…', label: 'Jeton d’accès Hugging Face',
    help: 'Il se crée sur huggingface.co › Settings › Access Tokens ; un jeton en lecture seule suffit pour télécharger un modèle. Ne le collez jamais ailleurs (ni dans une conversation) : seulement ici.',
  },
};

/** AURORA's own Google connection, to post comments on the channel (GET/POST/DELETE /api/youtube/*). The link to
 *  Google's page is shown, not opened in a pop-up; the status is read again when this window gets the focus back.
 *  Google sends the browser back to /youtube-retour: main.tsx keeps what it gave, sent here with the session token. */
function YoutubeConnectionForm({ refreshKey }: { refreshKey: number }) {
  const [status, setStatus] = useState<YoutubeConnection | null>(null);
  const [loadErr, setLoadErr] = useState('');
  const [link, setLink] = useState<string | null>(null);
  const [state, setState] = useState<SaveState>(undefined);
  const [autoState, setAutoState] = useState<SaveState>(undefined);
  const { settings, saveSettings } = useAurora();
  const box = useRef<HTMLDivElement>(null);
  const setAuto = async (on: boolean) => {
    setAutoState({ kind: 'busy', text: 'Enregistrement…' });
    try { await saveSettings({ youtube_comment_auto: on }); setAutoState({ kind: 'ok', text: on ? 'Publication sans redemander activée' : 'Chaque commentaire attendra votre accord' }); }
    catch (err) { setAutoState({ kind: 'err', text: err instanceof Error ? err.message : 'Échec de l’enregistrement' }); }
  };
  const load = useCallback(() => {
    api.youtubeConnection().then(s => { setStatus(s); setLoadErr(''); if (s.connected) setLink(null); })
      .catch(err => setLoadErr(err instanceof Error ? err.message : 'Service indisponible'));
  }, []);
  useEffect(() => { load(); }, [load, refreshKey]);
  useEffect(() => {
    const back = takeYoutubeReturn();   // given once: the second run of StrictMode finds nothing
    if (!back) return;
    box.current?.scrollIntoView({ block: 'center' });
    setState({ kind: 'busy', text: 'Retour de Google : enregistrement…' });
    api.youtubeReturn(back).then(s => {
      setStatus(s); setLink(null);
      setState({ kind: 'ok', text: `Chaîne « ${s.channel_title ?? s.channel_id ?? '?'} » reliée` });
    }).catch(err => setState({ kind: 'err', text: err instanceof Error ? err.message : 'Connexion non faite' }));
  }, []);
  useEffect(() => {
    window.addEventListener('focus', load);
    return () => window.removeEventListener('focus', load);
  }, [load]);
  const connect = async () => {
    setState({ kind: 'busy', text: 'Préparation du lien…' });
    try { const r = await api.youtubeConnect(); setLink(r.url); setState({ kind: 'ok', text: 'Lien prêt, valable 10 minutes' }); }
    catch (err) { setState({ kind: 'err', text: err instanceof Error ? err.message : 'Connexion impossible' }); }
  };
  const disconnect = async () => {
    setState({ kind: 'busy', text: 'Déconnexion…' });
    try {
      const s = await api.youtubeDisconnect();
      setStatus(s); setLink(null);
      setState(s.note ? { kind: 'err', text: `Déconnecté ici. ${s.note}` } : { kind: 'ok', text: 'Chaîne déconnectée' });
    } catch (err) { setState({ kind: 'err', text: err instanceof Error ? err.message : 'Déconnexion impossible' }); }
  };
  const uri = status?.redirect_uri;
  return (
    <div className="set-howto" ref={box}>
      <strong><ShieldCheck size={14} /> Connexion Google — publier des commentaires</strong>
      {loadErr ? <p>État : indisponible ({loadErr}).</p> : !status ? <p><Spinner /> Chargement…</p> : (
        <>
          <p>{status.connected
            ? `Chaîne reliée : « ${status.channel_title ?? status.channel_id} »${status.connected_at ? ` (depuis le ${fmtDateTime(status.connected_at)})` : ''}.`
            : status.client ? 'Client OAuth enregistré ; aucune chaîne reliée pour l’instant.' : 'Collez d’abord l’ID client et le code secret de votre client OAuth ci-dessous.'}</p>
          {status.connected && status.same_channel === false && (
            <p>Attention : ce n’est pas la chaîne indiquée plus haut ; AURORA ne publiera que sur la chaîne reliée ici.</p>
          )}
          {status.vault_available === false ? <p>Le coffre Windows n’est pas disponible sur ce système.</p> : (
            <div className="set-inline">
              {!status.connected && (
                <button className="btn btn-sm btn-primary" disabled={!status.client || state?.kind === 'busy'} onClick={() => void connect()}>Se connecter à Google</button>
              )}
              {link && !status.connected && <a className="btn btn-sm" href={link} target="_blank" rel="noopener noreferrer">Ouvrir la page Google ›</a>}
              {status.connected && <button className="btn btn-sm btn-danger" disabled={state?.kind === 'busy'} onClick={() => void disconnect()}>Déconnecter</button>}
            </div>
          )}
          {uri && (
            <p className="set-small">Adresse de retour à déclarer une fois dans votre client OAuth (type « Application Web » › URI de redirection autorisés) : <code className="mono">{uri}</code>{' '}
              <button className="btn btn-sm" type="button" onClick={() => void navigator.clipboard?.writeText(uri)}>Copier</button></p>
          )}
        </>
      )}
      <SaveNote s={state} />
      <p className="set-small">Une fois la chaîne reliée, dites dans la conversation « mets un commentaire sous ma dernière vidéo » (AURORA le rédige avec l’IA locale) ou « mets un commentaire sous ma dernière vidéo : votre texte ». Le commentaire vous est montré (texte exact, vidéo, chaîne) et attend votre accord ; une fois publié, il est relu sur YouTube. Seulement sous la dernière vidéo pour l’instant ; le retrait par AURORA n’est pas encore fait (sur YouTube : menu ⋮ du commentaire › Supprimer).</p>
      {/* 9/11/2026, ID Studio IA: « en mode auto sans mon clic d'autorisation […] si c'est déjà autorisé c'est bon ». */}
      <Switch label="Publier sans redemander" hint="Désactivé par défaut. Activé, un commentaire que vous demandez dans la conversation est publié sans nouveau clic : l’aperçu reste enregistré avec la tâche, et la réponse montre le texte publié. Jamais en mode Privé ; l’agent ne peut pas publier."
        checked={!!settings?.youtube_comment_auto} disabled={!settings || autoState?.kind === 'busy'} onChange={v => void setAuto(v)} />
      <SaveNote s={autoState} />
      <p className="set-small">Permission demandée à Google : une seule, la seule qui permette de publier un commentaire. Google l’accorde en bloc : voir, modifier et supprimer définitivement vos vidéos, notes, commentaires et sous-titres YouTube. AURORA ne s’en sert que pour publier et retirer les commentaires que vous lui demandez. L’accès durable donné par Google est rangé dans le coffre Windows, jamais réaffiché ; « Déconnecter » le retire.</p>
      <p className="set-small">Avant de vous connecter, dans Google Cloud Console : activez « YouTube Data API v3 » pour le projet de ce client OAuth et, tant que l’écran de consentement est en mode « Test », ajoutez votre compte Google aux utilisateurs test. Google affichera alors que l’application n’est pas validée : c’est votre propre client. En mode « Test », la connexion expire au bout de 7 jours ; il suffit de se reconnecter. Aucune connexion en mode Privé.</p>
    </div>
  );
}

/** A Studio secret (Netlify token, YouTube API key): same handling as the Claude key above, never shown again. */
function StudioSecretForm({ name, onChanged }: { name: StudioSecretName; onChanged: () => void }) {
  const info = STUDIO_SECRETS[name];
  const [status, setStatus] = useState<StudioSecretStatus | null>(null);
  const [token, setToken] = useState('');
  const [state, setState] = useState<SaveState>(undefined);
  useEffect(() => { api.studioSecret(name).then(setStatus).catch(() => setStatus(null)); }, [name]);
  const run = async (action: () => Promise<StudioSecretStatus>, ok: string) => {
    setState({ kind: 'busy', text: 'Enregistrement…' });
    try { setStatus(await action()); setToken(''); setState({ kind: 'ok', text: ok }); onChanged(); }
    catch (err) { setState({ kind: 'err', text: err instanceof Error ? err.message : 'Échec de l’enregistrement' }); }
  };
  const where = !status ? `État : indisponible` : status.configured ? `${info.noun} enregistré(e) dans le coffre Windows` : `Aucun(e) ${info.noun.toLowerCase()} enregistré(e)`;
  return (
    <div className="set-howto">
      <strong><ShieldCheck size={14} /> {info.title} — coffre Windows</strong>
      <p>{where}.</p>
      {status?.vault_available === false ? <p>Le coffre Windows n’est pas disponible sur ce système.</p> : (
        <div className="set-inline">
          <input className="input" type="password" autoComplete="off" spellCheck={false} placeholder={info.placeholder} maxLength={200}
            value={token} onChange={ev => setToken(ev.target.value)} aria-label={info.label} />
          <button className="btn btn-sm" disabled={token.trim().length < 20} onClick={() => void run(() => api.saveStudioSecret(name, token.trim()), `${info.noun} enregistré(e) dans le coffre Windows`)}>Enregistrer</button>
          {status?.configured && (
            <button className="btn btn-sm btn-danger" onClick={() => void run(() => api.deleteStudioSecret(name), `${info.noun} supprimé(e) du coffre`)}>Supprimer</button>
          )}
        </div>
      )}
      <SaveNote s={state} />
      <p className="set-small">Envoyé(e) une seule fois au service local (127.0.0.1, jeton de session), rangé(e) dans le Gestionnaire d’identification de Windows, puis jamais réaffiché(e) ni écrit(e) dans un fichier, un journal ou Git. {info.help}</p>
    </div>
  );
}

/** 13/11/2026 (ID Studio IA : « place 2 clés API dans les paramètres, meshy.ai et huggingface, pour si on a besoin ») : les
 *  clés des deux services 3D en ligne, rangées par le même formulaire que les autres. Rien n'est montré tant que le
 *  service ne connaît pas ces deux noms (avant son redémarrage). AURORA ne s'en sert pas encore : c'est dit. */
function Cles3D() {
  const [connu, setConnu] = useState(false);
  useEffect(() => { api.studioSecret('meshy').then(() => setConnu(true)).catch(() => setConnu(false)); }, []);
  if (!connu) return null;
  return (
    <Panel title="Services 3D en ligne" subtitle="Meshy et Hugging Face — facultatifs, jamais utilisés sans votre accord" icon={<Cloud size={18} />}>
      <p className="set-small">AURORA ne se sert pas encore de ces deux clés. Elles sont rangées pour le jour où un modèle 3D sera demandé à l’un de ces services : ce sera toujours sur votre accord, en vous montrant d’abord ce qui part. Les modèles locaux restent utilisés en premier.</p>
      <StudioSecretForm name="meshy" onChanged={() => undefined} />
      <StudioSecretForm name="huggingface" onChanged={() => undefined} />
    </Panel>
  );
}

/** Paramètres › Studio: the public accounts read by the Studio page (PUT /api/studio/settings, checked again by the service). */
const STUDIO_FIELDS: { key: keyof StudioSettingsInput; label: string; placeholder: string; max: number; list?: boolean }[] = [
  { key: 'github_users', label: 'Comptes GitHub (séparés par des virgules, 5 au plus)', placeholder: 'compte, organisation', max: 600, list: true },
  { key: 'release_repos', label: 'Dépôts des versions (séparés par des virgules, 5 au plus)', placeholder: 'compte/dépôt, compte/autre-dépôt', max: 600, list: true },
  { key: 'youtube_channel', label: 'Chaîne YouTube', placeholder: 'UC… ou https://www.youtube.com/@…', max: 200 },
  { key: 'discord_invite', label: 'Invitation Discord', placeholder: 'https://discord.gg/…', max: 200 },
  { key: 'site_url', label: 'Adresse du site', placeholder: 'https://…', max: 300 },
  { key: 'netlify_site', label: 'Site Netlify (nom ou identifiant)', placeholder: 'mon-site', max: 253 },
  { key: 'kickstarter_url', label: 'Campagne Kickstarter', placeholder: 'https://www.kickstarter.com/projects/…', max: 300 },
];
const studioDraft = (s: StudioSettings): Record<string, string> =>
  Object.fromEntries(STUDIO_FIELDS.map(f => { const v = s[f.key]; return [f.key, Array.isArray(v) ? v.join(', ') : (v ?? '')]; }));
const studioInput = (draft: Record<string, string>) => Object.fromEntries(STUDIO_FIELDS.map(f => {
  const v = draft[f.key] ?? '';
  return [f.key, f.list ? v.split(',').map(x => x.trim()).filter(Boolean) : v.trim()];
})) as StudioSettingsInput;

function StudioSettingsPanel({ online }: { online: boolean }) {
  const [saved, setSaved] = useState<StudioSettings | null>(null);
  const [loadErr, setLoadErr] = useState('');
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [state, setState] = useState<SaveState>(undefined);
  const [ytKey, setYtKey] = useState(0);   // the Google connection reads its status again after an OAuth secret changes
  const load = useCallback(() => {
    api.studioSettings().then(s => { setSaved(s); setDraft(studioDraft(s)); setLoadErr(''); })
      .catch(err => setLoadErr(err instanceof Error ? err.message : 'Service indisponible'));
  }, []);
  useEffect(() => { if (online) load(); }, [online, load]);
  const dirty = !!saved && STUDIO_FIELDS.some(f => (draft[f.key] ?? '') !== studioDraft(saved)[f.key]);
  const save = async () => {
    setState({ kind: 'busy', text: 'Enregistrement…' });
    try {
      const s = await api.saveStudioSettings(studioInput(draft));
      setSaved(s); setDraft(studioDraft(s)); setState({ kind: 'ok', text: 'Enregistré' });
      markStudioStale();
    } catch (err) { setState({ kind: 'err', text: err instanceof Error ? err.message : 'Échec de l’enregistrement' }); }
  };
  return (
    <Panel className="set-studio" title="Studio" icon={<Clapperboard size={18} />}
      subtitle="Les comptes publics lus par la page Studio : GitHub, YouTube, Discord, site, Kickstarter"
      actions={saved ? <Badge tone={saved.netlify_token ? 'green' : 'amber'}>{saved.netlify_token ? 'Jeton Netlify enregistré' : 'Sans jeton Netlify'}</Badge> : null}>
      <p className="set-small">Ces sources sont publiques. AURORA ne les lit qu’en mode Équilibré ou Avancé : aucune connexion en mode Privé.</p>
      {loadErr ? <Unavailable what="Réglages du Studio" reason={loadErr} />
        : !saved ? <EmptyState>{online ? <><Spinner /> Chargement…</> : 'Noyau local hors ligne.'}</EmptyState> : (
          <form onSubmit={ev => { ev.preventDefault(); if (dirty) void save(); }}>
            <div className="set-two">
              {STUDIO_FIELDS.map(f => (
                <label key={f.key} className="field">{f.label}
                  <input className="input" value={draft[f.key] ?? ''} onChange={ev => setDraft(d => ({ ...d, [f.key]: ev.target.value }))}
                    placeholder={f.placeholder} maxLength={f.max} spellCheck={false} />
                </label>
              ))}
            </div>
            <div className="set-inline set-studio-save">
              <button className="btn btn-sm btn-primary" type="submit" disabled={!dirty || state?.kind === 'busy'}>Enregistrer</button>
              <SaveNote s={state} />
            </div>
          </form>
        )}
      <StudioSecretForm name="youtube" onChanged={() => { load(); markStudioStale(); }} />
      <YoutubeConnectionForm refreshKey={ytKey} />
      <StudioSecretForm name="youtube_client_id" onChanged={() => setYtKey(k => k + 1)} />
      <StudioSecretForm name="youtube_client_secret" onChanged={() => setYtKey(k => k + 1)} />
      <StudioSecretForm name="netlify" onChanged={() => { load(); markStudioStale(); }} />
    </Panel>
  );
}

/** One spending ceiling in USD, saved on its own (the backend validates the range again). */
function CeilingField({ label, field, min, max, settings, onSave }: {
  label: string; field: 'cloud_request_budget_usd' | 'cloud_daily_budget_avance_usd' | 'cloud_request_budget_avance_usd';
  min: number; max: number; settings: Settings | null; onSave: (fields: Partial<Settings>) => void;
}) {
  const [value, setValue] = useState('');
  useEffect(() => { if (settings) setValue(String(settings[field] ?? '')); }, [settings, field]);
  const n = Number(value.replace(',', '.'));
  const invalid = !Number.isFinite(n) || n < min || n > max;
  return (
    <label className="field">{label}
      <div className="set-inline">
        <input className="input" type="number" min={min} max={max} step={0.05} value={value} onChange={ev => setValue(ev.target.value)} disabled={!settings} aria-invalid={invalid} />
        <button className="btn btn-sm" disabled={!settings || invalid || n === settings?.[field]} onClick={() => onSave({ [field]: n })}>Enregistrer</button>
      </div>
    </label>
  );
}

/** 9/11/2026: the allowed apps (tools/apps.py), like the allowed folders: a list, a remove button, a form to add one.
 * The service checks each entry (program found, accepted type, no terminal, local address) and says why it refuses.
 * ID Studio IA, the same day: « une liste de mes applications sur mon PC » — the Desktop and Start menu apps (GET /api/apps/pc,
 * terminals and uninstallers already left aside), one click « Ajouter » each; typing a path stays possible. */
function AllowedApps({ apps, draft, setDraft, onSave }: {
  apps: AllowedApp[]; draft: AllowedApp; setDraft: (d: AllowedApp) => void;
  onSave: (list: AllowedApp[]) => Promise<boolean>;
}) {
  const name = draft.name.trim();
  const path = draft.path.trim().replace(/^"|"$/g, '');
  const taken = apps.some(a => a.name.toLowerCase() === name.toLowerCase());
  const [pc, setPc] = useState<PcApps | null>(null);
  const [pcState, setPcState] = useState<'idle' | 'busy' | 'err'>('idle');
  const [query, setQuery] = useState('');
  const [adding, setAdding] = useState('');
  const full = apps.length >= 30;   // MAX_APPS of tools/apps.py
  const shown = useMemo(() => {
    const fold = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    const q = fold(query.trim());
    return (pc?.apps ?? []).filter(a => !q || fold(`${a.name} ${a.detail}`).includes(q));
  }, [pc, query]);
  const inList = (a: PcApp) => apps.some(x => x.name.toLowerCase() === a.name.toLowerCase() || x.path.toLowerCase() === a.path.toLowerCase());
  const loadPc = () => {
    setPcState('busy');
    api.pcApps().then(r => { setPc(r); setPcState('idle'); }, () => setPcState('err'));
  };
  const addPc = (a: PcApp) => {
    setAdding(a.path);
    void onSave([...apps, { name: a.name, path: a.path, url: a.url ?? '' }]).finally(() => setAdding(''));
  };
  return (
    <>
      {apps.length === 0 ? <p className="set-small muted">Aucune application autorisée : AURORA n’ouvre rien.</p> : (
        <ul className="set-roots">
          {apps.map(a => (
            <li key={a.name}>
              <span title={[a.path, a.url].filter(Boolean).join(' · ')}><strong>{a.name}</strong> · <span className="mono">{a.path}</span>{a.url ? <> · <span className="mono">{a.url}</span></> : null}</span>
              <button className="icon-btn" aria-label={`Retirer ${a.name}`} onClick={() => void onSave(apps.filter(x => x.name !== a.name))}><X size={15} /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="set-pc">
        {!pc ? (
          <button className="btn btn-sm" type="button" onClick={loadPc} disabled={pcState === 'busy'}>
            {pcState === 'busy' ? <Spinner size={12} /> : <Monitor size={14} />} Choisir parmi les applications du PC
          </button>
        ) : (
          <>
            <div className="set-inline">
              <input className="input" placeholder={`Chercher parmi ${pc.apps.length} applications du PC…`} value={query} onChange={ev => setQuery(ev.target.value)} aria-label="Chercher une application du PC" />
              <button className="icon-btn" type="button" aria-label="Fermer la liste des applications du PC" onClick={() => { setPc(null); setQuery(''); }}><X size={15} /></button>
            </div>
            {shown.length === 0 ? <p className="set-small muted">Aucune application du PC ne correspond.</p> : (
              <ul className="set-pc-list">
                {shown.map(a => {
                  const has = inList(a);
                  return (
                    <li key={a.path}>
                      <span className="set-pc-name"><strong>{a.name}</strong><small>{a.detail}{a.url ? ` · ${a.url}` : ''}</small></span>
                      <Badge tone={a.source === 'Bureau' ? 'cyan' : 'default'}>{a.source}</Badge>
                      <button className="btn btn-sm" type="button" title={a.path} disabled={has || full || adding !== ''} onClick={() => addPc(a)}>
                        {has ? 'Déjà dans la liste' : adding === a.path ? <Spinner size={12} /> : 'Ajouter'}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            <p className="set-small muted">
              {full ? 'La liste est pleine (30 applications) : retirez-en une pour en ajouter une autre. ' : ''}
              Lus : le Bureau et le menu Démarrer. Laissés de côté : {Object.entries(pc.skipped).map(([k, n]) => `${n} ${k}`).join(', ') || 'aucun'}.
              Rien n’est lancé par cette liste : une application ajoutée s’ouvre ensuite quand vous la demandez par son nom.
            </p>
          </>
        )}
        {pcState === 'err' && <p className="set-small set-callout" role="alert">La liste des applications du PC n’a pas pu être lue (service injoignable ?).</p>}
      </div>
      <p className="set-small muted">Ou indiquez le programme vous-même :</p>
      <form className="set-inline set-apps" onSubmit={ev => {
        ev.preventDefault();
        if (!name || !path || taken) return;
        void onSave([...apps, { name, path, url: (draft.url ?? '').trim() }]).then(ok => { if (ok) setDraft({ name: '', path: '', url: '' }); });
      }}>
        <input className="input" placeholder="Nom : YouTube Manager" value={draft.name} onChange={ev => setDraft({ ...draft, name: ev.target.value })} aria-label="Nom de l’application" maxLength={60} />
        <input className="input mono" placeholder="C:\…\start_youtube.bat" value={draft.path} onChange={ev => setDraft({ ...draft, path: ev.target.value })} aria-label="Chemin du programme" maxLength={400} />
        <input className="input mono" placeholder="http://localhost:3000 (facultatif)" value={draft.url ?? ''} onChange={ev => setDraft({ ...draft, url: ev.target.value })} aria-label="Adresse locale de l’application (facultative)" maxLength={200} />
        <button className="btn btn-sm" type="submit" disabled={!name || !path || taken} title={taken ? 'Ce nom est déjà dans la liste.' : undefined}><AppWindow size={14} /> Ajouter</button>
      </form>
    </>
  );
}

function SaveNote({ s }: { s: SaveState }) {
  if (!s) return null;
  return <span className={`set-save set-save-${s.kind}`} role={s.kind === 'err' ? 'alert' : 'status'}>{s.kind === 'busy' ? <Spinner size={12} /> : null}{s.text}</span>;
}

function Switch({ checked, onChange, label, hint, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint: string; disabled?: boolean }) {
  return (
    <div className="set-switch-row">
      <div><strong>{label}</strong><p>{hint}</p></div>
      <button type="button" role="switch" aria-checked={checked} aria-label={label} className={`set-switch ${checked ? 'on' : ''}`} disabled={disabled} onClick={() => onChange(!checked)}><span /></button>
    </div>
  );
}

/** 16/11/2026 (E3) — Démarrer AURORA avec Windows. L'état vient du disque (service : tools/demarrage.py) : le
 *  fichier de commande qu'AURORA pose dans le dossier « Démarrage » de la session Windows, ou retire. */
function StartupPanel({ online }: { online: boolean }) {
  const [state, setState] = useState<StartupState | null>(null);
  const [error, setError] = useState('');
  const [note, setNote] = useState<SaveState>(undefined);
  const load = useCallback(() => {
    api.demarrage().then(s => { setState(s); setError(''); })
      .catch(err => { setState(null); setError(err instanceof Error ? err.message : 'Service indisponible'); });
  }, []);
  useEffect(() => { load(); }, [load]);
  const set = async (actif: boolean, fenetre: boolean) => {
    setNote({ kind: 'busy', text: 'Enregistrement…' });
    try {
      const s = await api.reglerDemarrage(actif, fenetre);
      setState(s);
      setNote({ kind: 'ok', text: s.actif ? 'AURORA démarrera avec Windows.' : 'AURORA ne démarre plus avec Windows.' });
    } catch (err) {
      setNote({ kind: 'err', text: err instanceof Error ? err.message : 'Réglage impossible' });
      load();
    }
  };
  // A service started before this option exists answers « Not Found »: say when it arrives, not an error code.
  const pasEncore = /^Not Found$|^Erreur 404$/.test(error);
  return (
    <Panel title="Démarrage" subtitle="AURORA prête dès l’ouverture de votre session Windows" icon={<Power size={18} />} actions={<SaveNote s={note} />}>
      {error && <Unavailable what="Démarrage avec Windows" reason={pasEncore ? 'Cette option arrive au prochain redémarrage d’AURORA.' : error} />}
      {!error && !state && <EmptyState><Spinner /> Lecture du dossier « Démarrage »…</EmptyState>}
      {state && !state.disponible && <Unavailable what="Démarrage avec Windows" reason={state.pourquoi} />}
      {state && state.disponible && (
        <>
          <Switch checked={state.actif} disabled={!online || state.autre} label="Démarrer AURORA avec Windows"
            hint="À l’ouverture de session, le service et l’interface démarrent sans fenêtre : l’icône du Bureau ouvre alors AURORA aussitôt."
            onChange={v => void set(v, v && state.fenetre)} />
          <Switch checked={state.actif && state.fenetre} disabled={!online || !state.actif} label="Ouvrir aussi la fenêtre d’AURORA"
            hint="AURORA s’affiche toute seule à l’ouverture de session." onChange={v => void set(true, v)} />
          {state.autre && <p className="set-small">{state.pourquoi}</p>}
          {state.actif && !state.cette_copie && <p className="set-small">Le fichier de démarrage lance une autre copie d’AURORA. Désactivez puis réactivez l’option pour qu’il lance celle-ci.</p>}
          <p className="set-small muted">{state.actif ? 'Fichier posé : ' : 'Fichier qui sera posé : '}<span className="mono">{state.fichier}</span>. Le retirer, ici ou à la main, arrête le démarrage automatique. Rien d’autre n’est modifié dans Windows.</p>
        </>
      )}
    </Panel>
  );
}

function PermissionList({ items, empty }: { items: Permission[] | null; empty: string }) {
  if (items === null) return <Unavailable what="Autorisations" />;
  if (!items.length) return <p className="set-small muted">{empty}</p>;
  return (
    <ul className="set-perms">
      {items.slice(0, 15).map(p => (
        <li key={p.id}>
          <div className="set-perm-top"><strong>{p.title || p.action}</strong><Badge tone={RISK[p.risk]?.tone ?? 'amber'}>risque {RISK[p.risk]?.label ?? p.risk}</Badge></div>
          <div className="set-perm-meta"><span className="mono">{p.action}</span><span>{fmtDateTime(p.decided_at ?? p.created_at)}</span>{p.reason && <span>· {p.reason}</span>}</div>
        </li>
      ))}
    </ul>
  );
}

/** A4: each answer check is a « mod » (backend core/verify.py MODS): « alerter » adds a note, « bloquer » withholds the AI's text. */
function VerifyMods({ levels, disabled, note, onChange }: {
  levels: Record<string, VerifyLevel> | undefined; disabled: boolean; note: SaveState; onChange: (name: string, level: VerifyLevel) => void;
}) {
  return (
    <div className="set-verify-mods">
      <div className="set-reports-head"><h4 className="set-h4"><ShieldCheck size={13} /> Contrôles des réponses</h4><SaveNote s={note} /></div>
      <p className="set-small">Après chaque réponse, AURORA la contrôle. « Alerter » affiche la réponse avec une note. « Bloquer » retient le texte de l’IA et montre à la place ce que les outils ont réellement mesuré ou trouvé ; le texte retenu reste consultable sous la réponse.</p>
      <ul className="set-mods-list">
        {Object.entries(VERIFY_MOD_LABELS).map(([name, mod]) => {
          const level = levels?.[name] ?? 'alerter';
          return (
            <li key={name}>
              <div className="set-mods-text"><strong>{mod.name}</strong><span>{mod.description}</span></div>
              <div className="set-mods-choice" role="radiogroup" aria-label={`Niveau du contrôle « ${mod.name} »`}>
                {(['alerter', 'bloquer'] as const).map(l => (
                  <button key={l} type="button" role="radio" aria-checked={level === l} disabled={disabled}
                    className={`btn btn-sm${level === l ? (l === 'bloquer' ? ' btn-danger' : ' btn-primary') : ''}`}
                    onClick={() => { if (level !== l) onChange(name, l); }}>{l === 'alerter' ? 'Alerter' : 'Bloquer'}</button>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** A1: the « Signaler » reports replayed by the bench, apart from its score: the day a fixed one fails again, it shows here. */
function ReportReplay({ replay }: { replay: BenchReplay }) {
  const wrong = replay.cases.filter(c => c.now !== c.expected);
  const fixed = replay.cases.filter(c => c.now === c.expected && c.reported && c.reported !== c.expected).length;
  return (
    <>
      <h4>Vos signalements rejoués</h4>
      {replay.error ? <p className="set-small">{replay.error}</p>
        : replay.n === 0 ? <p className="set-small muted">Aucun signalement à rejouer : signalez une réponse en précisant le type de demande attendu.</p>
        : (
          <>
            <p className="set-small">
              {replay.rules?.correct ?? 0} sur {replay.n} maintenant bien compris (règles seules){fixed > 0 ? `, dont ${fixed} corrigé(s) depuis le signalement` : ''}
              {replay.semantic ? ` · ${replay.semantic.correct} sur ${replay.n} avec la similarité` : ''}. Ces phrases ne comptent pas dans le score ci-dessus.
            </p>
            {wrong.length > 0 && (
              <ul className="set-confusions" aria-label="Signalements encore mal compris">
                {wrong.map((c, i) => (
                  <li key={c.runId ?? i}>
                    <span className="mono">{INTENT_LABELS[c.expected] ?? c.expected}</span><span className="set-arrow">→</span><span className="mono">{INTENT_LABELS[c.now] ?? c.now}</span>
                    <q>{c.text}</q>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      {replay.ignored > 0 && <p className="set-small muted">{replay.ignored} ligne(s) illisible(s) ignorée(s).</p>}
    </>
  );
}

function BenchResults({ view }: { view: BenchView }) {
  const cats = useMemo(() => {
    const keys: { key: string; label: string }[] = [];
    view.methods.forEach(m => m.categories.forEach(c => { if (!keys.some(k => k.key === c.key)) keys.push({ key: c.key, label: c.label }); }));
    return keys;
  }, [view]);
  const best = Math.max(...view.methods.map(m => m.accuracy ?? 0));
  return (
    <div className="set-bench">
      <div className="set-bench-meta">
        {view.at && <span>Dernier passage : {fmtDateTime(view.at)} ({relativeTime(view.at)})</span>}
        {view.total !== null && <span>{view.total} phrases de test</span>}
        {view.machine && <span>Machine : {view.machine}</span>}
      </div>
      <h4>Exactitude par méthode</h4>
      <div className="set-legend" aria-hidden="true">{view.methods.map((m, i) => <span key={m.key}><i style={{ background: METHOD_COLORS[i % 4] }} />{m.label}</span>)}</div>
      <ul className="set-bars">
        {view.methods.map((m, i) => (
          <li key={m.key}>
            <span className="set-bar-label">{m.label}{m.accuracy === best && view.methods.length > 1 && <Badge tone="green">meilleur</Badge>}</span>
            <span className="set-bar-track"><span className="set-bar-fill" style={{ width: `${Math.max(0, Math.min(100, m.accuracy ?? 0))}%`, background: METHOD_COLORS[i % 4] }} /></span>
            <span className="set-bar-val">{m.accuracy !== null ? `${m.accuracy.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} %` : '—'}</span>
            <span className="set-bar-lat">p50 {ms(m.p50)} · p95 {ms(m.p95)}</span>
          </li>
        ))}
      </ul>
      {cats.length > 0 && (
        <>
          <h4>Exactitude par catégorie</h4>
          <div className="set-table-wrap">
            <table className="set-table">
              <thead><tr><th>Méthode</th>{cats.map(c => <th key={c.key} className="num">{c.label}</th>)}</tr></thead>
              <tbody>
                {view.methods.map((m, i) => (
                  <tr key={m.key}>
                    <td><i className="set-swatch" style={{ background: METHOD_COLORS[i % 4] }} />{m.label}</td>
                    {cats.map(c => {
                      const v = m.categories.find(x => x.key === c.key);
                      return <td key={c.key} className="num">{v?.accuracy != null ? `${v.accuracy.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} %` : '—'}{v?.n != null && <small> ({v.n})</small>}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {view.confusions.length > 0 && (
        <>
          <h4>Principales confusions</h4>
          <ul className="set-confusions">
            {view.confusions.map((c, i) => (
              <li key={i}>
                <span className="mono">{c.expected}</span><span className="set-arrow">→</span><span className="mono">{c.predicted}</span>
                {c.count !== null && <Badge>{c.count}×</Badge>}
                {c.method && <span className="muted">{c.method}</span>}
                {c.text && <q>{c.text}</q>}
              </li>
            ))}
          </ul>
        </>
      )}
      {view.defaultSemantic !== null && <p className="set-small">Routeur sémantique {view.defaultSemantic ? 'activé' : 'désactivé'} par défaut d’après ce banc (activé seulement s’il améliore le score sans en faire perdre).</p>}
      {view.extras.length > 0 && <ul className="set-extras">{view.extras.map(x => <li key={x}>{x}</li>)}</ul>}
      {view.note && <p className="set-small"><strong>Recommandation du banc :</strong> {view.note}</p>}
      {view.replay && <ReportReplay replay={view.replay} />}
    </div>
  );
}

export default function SettingsPage() {
  const { settings, saveSettings, health, online, refreshHealth } = useAurora();
  const caps = health?.capabilities ?? null;
  const [save, setSave] = useState<Record<string, SaveState>>({});
  const [budget, setBudget] = useState('');
  const [city, setCity] = useState('');
  const [contact, setContact] = useState('');
  const [backupDir, setBackupDir] = useState('');
  const [newRoot, setNewRoot] = useState('');
  const [newApp, setNewApp] = useState<AllowedApp>({ name: '', path: '', url: '' });   // 9/11/2026, tools/apps.py
  const [usage, setUsage] = useState<Usage | null>(null);
  const [usageErr, setUsageErr] = useState('');
  const [bench, setBench] = useState<Record<string, unknown> | null>(null);
  const [benchErr, setBenchErr] = useState('');
  const [benchLoaded, setBenchLoaded] = useState(false);
  const [benchRunning, setBenchRunning] = useState(false);
  const [granted, setGranted] = useState<Permission[] | null>(null);
  const [denied, setDenied] = useState<Permission[] | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearState, setClearState] = useState<SaveState>(undefined);

  useEffect(() => {
    if (!settings) return;
    setBudget(String(settings.cloud_daily_budget_usd ?? ''));
    setCity(settings.default_city ?? '');
    setContact(settings.web_contact ?? '');
    setBackupDir(settings.backup_dir ?? '');
  }, [settings]);

  const loadUsage = useCallback(() => {
    api.usage().then(u => { setUsage(u); setUsageErr(''); }).catch(err => { setUsage(null); setUsageErr(err instanceof Error ? err.message : 'Service indisponible'); });
  }, []);
  const loadPermissions = useCallback(() => {
    api.permissions('granted').then(setGranted).catch(() => setGranted(null));
    api.permissions('denied').then(setDenied).catch(() => setDenied(null));
  }, []);
  useEffect(() => {
    loadUsage();
    loadPermissions();
    api.latestBench().then(b => { setBench(b ?? null); setBenchErr(''); }).catch(err => setBenchErr(err instanceof Error ? err.message : 'Service indisponible')).finally(() => setBenchLoaded(true));
  }, [loadUsage, loadPermissions]);

  const apply = async (section: string, fields: Partial<Settings>): Promise<boolean> => {
    setSave(s => ({ ...s, [section]: { kind: 'busy', text: 'Enregistrement…' } }));
    try {
      await saveSettings(fields);
      setSave(s => ({ ...s, [section]: { kind: 'ok', text: 'Enregistré' } }));
      if (section === 'budget') loadUsage();
      return true;
    } catch (err) {
      setSave(s => ({ ...s, [section]: { kind: 'err', text: err instanceof Error ? err.message : 'Échec de l’enregistrement' } }));
      return false;
    }
  };

  const runBench = async () => {
    setBenchRunning(true); setBenchErr('');
    try { setBench(await api.runBench()); } catch (err) { setBenchErr(err instanceof Error ? err.message : 'Banc d’essai indisponible'); }
    finally { setBenchRunning(false); }
  };

  const clearEvents = async () => {
    setClearState({ kind: 'busy', text: 'Effacement…' });
    try { await api.clearEvents(); setClearState({ kind: 'ok', text: 'Historique des événements effacé.' }); }
    catch (err) { setClearState({ kind: 'err', text: err instanceof Error ? err.message : 'Effacement impossible' }); }
    finally { setConfirmClear(false); }
  };

  const benchView = useMemo(() => (bench ? parseBench(bench) : null), [bench]);
  const mode = settings?.mode ?? caps?.mode ?? null;
  const models = caps?.ollama.models ?? [];
  // Conversation models only: chat_models from the service (S5), else installed models minus embeddings.
  const chatModels = caps?.ollama.chat_models ?? models.filter(m => !isEmbeddingModel(m, caps?.ollama.embed_model ?? ''));
  const tiers = Object.entries(caps?.cloud.tiers ?? {});
  const contactClean = contact.trim();
  const contactInvalid = contactClean !== '' && (contactClean.length > 200 || !CONTACT_RE.test(contactClean));
  const savedContact = settings?.web_contact ?? '';
  const budgetNum = Number(budget.replace(',', '.'));
  const budgetInvalid = !Number.isFinite(budgetNum) || budgetNum < 0 || budgetNum > 1000;
  const dbPath = pickStr(settings, ['db_path', 'database_path']) ?? pickStr(health, ['db_path', 'database.path']) ?? null;
  const spentPct = usage && usage.budget_usd > 0 ? Math.min(100, (usage.today_usd / usage.budget_usd) * 100) : null;
  const benchErrorRaw = benchErr || (bench && getPath(bench, 'error') ? String(getPath(bench, 'error')) : '');

  const modelOptions = (current: string | undefined) => {
    const list = [...chatModels];
    if (current && !list.includes(current)) list.unshift(current);
    return list.map(m => <option key={m} value={m}>{m}{chatModels.includes(m) ? '' : models.includes(m) ? ' (inutilisable pour la conversation)' : ' (non installé)'}</option>);
  };

  return (
    <div className="set-page">
      <div className="page-title">
        <div>
          <div className="eyebrow">Configuration</div>
          <h1>Paramètres</h1>
          <p>Choisissez comment AURORA réfléchit, ce qui peut partir vers le cloud et ce qu’elle peut toucher.</p>
        </div>
        <button className="btn btn-sm" onClick={() => { void refreshHealth(); loadUsage(); loadPermissions(); }} disabled={!online}>Actualiser l’état</button>
      </div>

      {!settings && <Unavailable what="Paramètres" reason={online ? 'Le service n’a pas renvoyé les paramètres.' : 'Noyau local hors ligne : les paramètres ne peuvent pas être lus ni modifiés.'} />}

      <Panel className="set-modes" title="Modes d’intelligence" subtitle="S’applique à toutes les demandes (texte, voix, boutons)" icon={<Brain size={18} />} actions={<SaveNote s={save.mode} />}>
        <div className="set-mode-grid" role="radiogroup" aria-label="Mode d’intelligence">
          {MODES.map(m => (
            <button key={m.id} role="radio" aria-checked={mode === m.id} className={`set-mode ${mode === m.id ? 'active' : ''}`}
              disabled={!settings || save.mode?.kind === 'busy'} onClick={() => { if (mode !== m.id) void apply('mode', { mode: m.id }); }}>
              <span className="set-mode-icon">{m.icon}</span>
              <span className="set-mode-title">{m.title}{mode === m.id && <Badge tone="cyan">actif</Badge>}</span>
              <span className="set-mode-text">{m.text}</span>
            </button>
          ))}
        </div>
      </Panel>

      <div className="set-grid">
        <Panel title="Claude (Anthropic)" subtitle="IA cloud — utilisée seulement selon le mode actif" icon={<Cloud size={18} />}
          actions={caps ? <Badge tone={caps.cloud.configured ? 'green' : 'amber'}>{caps.cloud.configured ? 'Configuré' : 'Non configuré'}</Badge> : null}>
          {!caps ? <Unavailable what="État du cloud" /> : (
            <>
              {mode === 'prive' && <p className="set-small set-callout"><Lock size={13} /> Mode Privé actif : aucun appel à Claude n’est effectué, même configuré.</p>}
              <div className="set-table-wrap">
                <table className="set-table">
                  <thead><tr><th>Palier</th><th>Modèle</th><th>Effort</th><th className="num">Entrée</th><th className="num">Sortie</th><th className="num">Cache (lecture)</th></tr></thead>
                  <tbody>
                    {tiers.length === 0 && <tr><td colSpan={6} className="muted">Aucun palier déclaré par le serveur.</td></tr>}
                    {tiers.map(([tier, info]) => {
                      const p = servedPrices(info.prices) ?? PRICE_REF[info.model];
                      return (
                        <tr key={tier}>
                          <td>{TIER_LABELS[tier] ?? tier}</td>
                          <td className="mono">{info.model}</td>
                          <td>{info.effort ? (EFFORT_LABELS[info.effort] ?? info.effort) : '—'}</td>
                          {p ? <><td className="num">{usd(p.input)}</td><td className="num">{usd(p.output)}</td><td className="num">{usd(p.cache_read)}</td></>
                            : <td colSpan={3} className="num muted">tarif non référencé</td>}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="set-small">Tarifs publics Anthropic en dollars par million de jetons (tarif de base, rappel){tiers.some(([, info]) => !servedPrices(info.prices)) ? ' ; le service n’ayant pas transmis tous les tarifs, certains viennent de la table de l’interface' : ''}. Le coût réel de chaque appel est calculé par le serveur et affiché avec la réponse ; une demande d’accord montre le modèle, le texte rédigé et le coût estimé avant tout envoi.</p>

              <ClaudeKeyForm onChanged={() => void refreshHealth()} />
              {!caps.cloud.configured && (
                <details className="set-howto">
                  <summary>Ancienne méthode, toujours acceptée en secours : variable d’environnement</summary>
                  <ol>
                    <li>Créez une clé API dans la console Anthropic.</li>
                    <li>Dans le terminal qui lance le noyau AURORA, définissez la variable d’environnement :
                      <code>$env:ANTHROPIC_API_KEY = "&lt;votre-clé&gt;"</code>
                      ou, de façon permanente : <code>setx ANTHROPIC_API_KEY "&lt;votre-clé&gt;"</code> puis ouvrez un nouveau terminal.</li>
                    <li>Redémarrez le noyau (<code>start-aurora.ps1</code>) puis cliquez sur « Actualiser l’état ».</li>
                  </ol>
                  <p>Le coffre Windows est prioritaire : une clé qui y est enregistrée remplace la variable.</p>
                </details>
              )}

              <div className="set-budget">
                <label className="field">Équilibré · par jour (USD)
                  <div className="set-inline">
                    <input className="input" type="number" min={0} step={0.1} value={budget} onChange={ev => setBudget(ev.target.value)} disabled={!settings} aria-invalid={budgetInvalid} />
                    <button className="btn btn-sm" disabled={!settings || budgetInvalid || budgetNum === settings?.cloud_daily_budget_usd} onClick={() => void apply('budget', { cloud_daily_budget_usd: budgetNum })}>Enregistrer</button>
                  </div>
                </label>
                <CeilingField label="Équilibré · par requête (USD)" field="cloud_request_budget_usd" min={0.01} max={100} settings={settings} onSave={f => void apply('budget', f)} />
                <CeilingField label="Avancé · par jour (USD)" field="cloud_daily_budget_avance_usd" min={0} max={1000} settings={settings} onSave={f => void apply('budget', f)} />
                <CeilingField label="Avancé · par requête (USD)" field="cloud_request_budget_avance_usd" min={0.01} max={100} settings={settings} onSave={f => void apply('budget', f)} />
                <SaveNote s={save.budget} />
              </div>
              <p className="muted">Plafonds de dépense du mode actif. La réponse de Claude s’arrête d’elle-même au plafond : une requête ne peut jamais le dépasser.</p>
              <h4 className="set-h4">Dépense du jour</h4>
              {usage ? (
                <div className="set-usage">
                  <div className="set-usage-top">
                    <span><strong>{spend(usage.today_usd)}</strong> sur {spend(usage.budget_usd)}</span>
                    <span className="muted">reste {spend(usage.remaining_usd)}</span>
                  </div>
                  <span className="set-bar-track" role="img" aria-label={`Dépense ${spentPct === null ? 'non calculable' : `${spentPct.toFixed(0)} % du budget`}`}>
                    <span className="set-bar-fill" style={{ width: `${spentPct ?? 0}%`, background: (spentPct ?? 0) > 85 ? '#ff6b7a' : '#22c8ff' }} />
                  </span>
                  {usage.by_model.length === 0 ? <p className="set-small muted">Aucun appel cloud aujourd’hui.</p> : (
                    <table className="set-table">
                      <thead><tr><th>Modèle</th><th className="num">Appels</th><th className="num">Jetons entrée / sortie</th><th className="num">Coût</th></tr></thead>
                      <tbody>{usage.by_model.map(m => {
                        const tin = pickNum(m, ['input_tokens']), tout = pickNum(m, ['output_tokens']);
                        return <tr key={m.model}><td className="mono">{m.model}</td><td className="num">{m.calls}</td>
                          <td className="num">{tin === null ? '—' : tin.toLocaleString('fr-FR')} / {tout === null ? '—' : tout.toLocaleString('fr-FR')}</td><td className="num">{spend(m.cost_usd)}</td></tr>;
                      })}</tbody>
                    </table>
                  )}
                </div>
              ) : <Unavailable what="Consommation cloud" reason={usageErr} />}
            </>
          )}
        </Panel>

        <Panel title="IA locale (Ollama)" subtitle="Traitements sur cette machine" icon={<Gauge size={18} />}
          actions={caps ? <span className="set-state"><StatusDot ok={caps.ollama.up} /> {caps.ollama.up ? 'Ollama actif' : 'Ollama indisponible'}</span> : null}>
          {!caps ? <Unavailable what="État de l’IA locale" /> : (
            <>
              <dl className="set-facts">
                <div><dt>Modèle de conversation (effectif)</dt><dd className="mono">{caps.ollama.chat_model || '—'}</dd></div>
                <div><dt>Embeddings</dt><dd><span className="mono">{caps.ollama.embed_model || '—'}</span> {caps.ollama.embed_ready ? <Badge tone="green">prêt</Badge> : <Badge tone="amber">non prêt</Badge>}</dd></div>
              </dl>
              <h4 className="set-h4">Modèles installés ({models.length})</h4>
              {models.length ? <div className="set-chips">{models.map(m => <span key={m} className="set-chip mono">{m}</span>)}</div>
                : <p className="set-small muted">{caps.ollama.up ? 'Aucun modèle renvoyé par Ollama.' : 'Ollama ne répond pas : liste indisponible.'}</p>}
              <div className="set-two">
                <label className="field">Modèle principal (local_model)
                  <select className="select" value={settings?.local_model ?? ''} disabled={!settings || !chatModels.length}
                    onChange={ev => void apply('local', { local_model: ev.target.value })}>{modelOptions(settings?.local_model)}</select>
                </label>
                <label className="field">Modèle rapide (local_fast_model)
                  <select className="select" value={settings?.local_fast_model ?? ''} disabled={!settings || !chatModels.length}
                    onChange={ev => void apply('local', { local_fast_model: ev.target.value })}>{modelOptions(settings?.local_fast_model)}</select>
                </label>
              </div>
              {/* 13/11/2026 : montré seulement quand le service connaît ce réglage (après son redémarrage). */}
              {settings?.local_scene_model !== undefined && (
                <label className="field">Modèle des maquettes 3D (local_scene_model)
                  <select className="select" value={settings.local_scene_model} disabled={!chatModels.length}
                    onChange={ev => void apply('local', { local_scene_model: ev.target.value })}>
                    <option value="">Le même que le modèle principal</option>
                    {modelOptions(settings.local_scene_model || undefined)}
                  </select>
                  <span className="set-small muted">Un modèle plus gros construit mieux les objets difficiles, mais chaque maquette prend plus de temps. Le chat garde le modèle principal.</span>
                </label>
              )}
              <SaveNote s={save.local} />
              <h4 className="set-h4">Routage des demandes</h4>
              <Switch label="Routeur sémantique" hint="Similarité avec des exemples (n-grammes de caractères) quand aucune règle ne s’applique." checked={!!settings?.semantic_router} disabled={!settings}
                onChange={v => void apply('router', { semantic_router: v })} />
              <Switch label="Routeur LLM local" hint="Classifieur Ollama pour les demandes ambiguës (délai court, sortie JSON contrainte)." checked={!!settings?.llm_router} disabled={!settings || !caps.ollama.up}
                onChange={v => void apply('router', { llm_router: v })} />
              <SaveNote s={save.router} />
              <h4 className="set-h4"><Terminal size={13} /> Terminal avec autorisation</h4>
              <p className="set-small">Permet à AURORA d’exécuter une commande que vous lui donnez explicitement (« exécute : git status »). Chaque commande vous est montrée avant de partir et attend votre accord ; une liste de commandes dangereuses (formatage, démarrage, base de registre, suppression récursive, arrêt de l’antivirus…) reste refusée même avec accord ; la sortie est traitée comme une donnée, jamais comme une consigne.</p>
              <Switch label="Activer le terminal avec autorisation" hint="Désactivé par défaut. Même activé, rien ne s’exécute sans votre accord, une commande à la fois." checked={!!settings?.shell_enabled} disabled={!settings}
                onChange={v => void apply('shell', { shell_enabled: v })} />
              <SaveNote s={save.shell} />
              {/* 9/11/2026, ID Studio IA: « fais-moi le contrôle directement par la souris et le clavier » (tools/pc_control.py). */}
              <h4 className="set-h4"><MousePointerClick size={13} /> Souris et clavier</h4>
              <p className="set-small">Permet à AURORA de faire un geste que vous lui demandez (« clique en 640, 400 », « tape dans Bloc-notes : bonjour », « appuie sur Entrée dans Bloc-notes »). Chaque geste vous est montré avant (la fenêtre visée, la position, le texte exact) et attend votre accord, un geste à la fois. Refusé même avec accord : les terminaux, les fenêtres de sécurité de Windows, le Gestionnaire des tâches, l’Éditeur du Registre et la fenêtre d’AURORA ; on ne tape pas non plus dans un éditeur de code. Cliquer sur un bouton désigné par son nom n’est pas encore fait.</p>
              <Switch label="Activer la souris et le clavier" hint="Désactivé par défaut. Même activé, rien ne bouge sans votre accord, un geste à la fois." checked={!!settings?.pc_control_enabled} disabled={!settings}
                onChange={v => void apply('pc', { pc_control_enabled: v })} />
              <SaveNote s={save.pc} />
              {/* 9/11/2026, ID Studio IA: « ouvrir des applications choisies dans une liste où je mets les applications autorisées » (tools/apps.py). */}
              <h4 className="set-h4"><AppWindow size={13} /> Applications autorisées</h4>
              <p className="set-small">AURORA peut ouvrir par son nom une application de cette liste (« ouvre YouTube Manager »), et aucune autre. Votre liste vaut accord : une application de la liste s’ouvre sans nouvelle demande, et chaque ouverture reste dans le journal des étapes. Programmes acceptés : .exe, .bat, .cmd, .lnk, .url ; un terminal, un interpréteur lancé seul (Python, Node), l’Éditeur du Registre ou une fenêtre de sécurité de Windows sont refusés. L’adresse locale, facultative (par exemple http://localhost:3000), sert à voir si l’application tourne déjà.</p>
              {settings && <AllowedApps apps={settings.allowed_apps ?? []} draft={newApp} setDraft={setNewApp}
                onSave={list => apply('apps', { allowed_apps: list })} />}
              <SaveNote s={save.apps} />
            </>
          )}
        </Panel>

        <Cles3D />

        <Panel title="Dossiers et lieu" subtitle="Portée des outils fichiers · ville météo par défaut · contact pour la recherche web" icon={<FolderOpen size={18} />}>
          {!settings ? <Unavailable what="Dossiers autorisés" /> : (
            <>
              <p className="set-small">Documents, Téléchargements et Bureau sont toujours analysables en lecture seule. Tout déplacement (quarantaine réversible) demande une autorisation.</p>
              {settings.allowed_roots.length === 0 ? <p className="set-small muted">Aucun dossier supplémentaire.</p> : (
                <ul className="set-roots">
                  {settings.allowed_roots.map(r => (
                    <li key={r}><span className="mono" title={r}>{r}</span>
                      <button className="icon-btn" aria-label={`Retirer ${r}`} onClick={() => void apply('roots', { allowed_roots: settings.allowed_roots.filter(x => x !== r) })}><X size={15} /></button>
                    </li>
                  ))}
                </ul>
              )}
              <form className="set-inline" onSubmit={ev => {
                ev.preventDefault();
                const v = newRoot.trim();
                if (!v || settings.allowed_roots.includes(v)) return;
                void apply('roots', { allowed_roots: [...settings.allowed_roots, v] }).then(ok => { if (ok) setNewRoot(''); });
              }}>
                <input className="input mono" placeholder="C:\Users\…\Projets" value={newRoot} onChange={ev => setNewRoot(ev.target.value)} aria-label="Nouveau dossier autorisé" maxLength={400} />
                <button className="btn btn-sm" type="submit" disabled={!newRoot.trim()}><FolderPlus size={14} /> Ajouter</button>
              </form>
              <SaveNote s={save.roots} />
              <h4 className="set-h4"><MapPin size={13} /> Ville par défaut</h4>
              <form className="set-inline" onSubmit={ev => { ev.preventDefault(); if (city.trim()) void apply('city', { default_city: city.trim() }); }}>
                <input className="input" value={city} onChange={ev => setCity(ev.target.value)} aria-label="Ville par défaut" maxLength={120} />
                <button className="btn btn-sm" type="submit" disabled={!city.trim() || city.trim() === settings.default_city}>Enregistrer</button>
              </form>
              <SaveNote s={save.city} />
              <h4 className="set-h4"><Globe size={13} /> Onglet du sujet</h4>
              <Switch label="Ouvrir seule l’onglet du sujet" hint="Quand vous parlez de météo, de séismes, d’actualités du monde ou de satellites, AURORA ouvre Monde 3D et y situe le sujet ; un diagnostic ouvre Ordinateur. Une demande explicite (« ouvre le globe ») marche toujours."
                checked={settings.auto_open_tab !== false} onChange={v => void apply('autoTab', { auto_open_tab: v })} />
              <SaveNote s={save.autoTab} />
              {/* 24/11/2026, ID Studio IA: « si on le propose […] par utilisateur ? » — OpenSky is off until the person turns it on (tools/sky.py). */}
              <h4 className="set-h4"><Globe size={13} /> Avions : OpenSky Network</h4>
              <p className="set-small">Les avions autour d’un point viennent d’adsb.lol. OpenSky Network ajoute la vue du trafic mondial sur le globe, et sert de secours quand adsb.lol ne répond pas. Ce service réserve son accès à la recherche et à l’enseignement sans but lucratif ; tout autre usage demande son accord écrit. Lisez <a href="https://opensky-network.org/about/terms-of-use" target="_blank" rel="noopener noreferrer">ses conditions</a> avant de l’allumer.</p>
              <Switch label="Allumer OpenSky Network" hint="Éteint par défaut. En l’allumant, c’est vous qui acceptez ses conditions d’utilisation." checked={!!settings.opensky_enabled} disabled={!settings}
                onChange={v => void apply('sky', { opensky_enabled: v })} />
              <SaveNote s={save.sky} />
              <h4 className="set-h4" id="recherche-web"><Globe size={13} /> Recherche web — contact pour Wikipédia</h4>
              <p className="set-small">La recherche web passe d’abord par SearXNG local (127.0.0.1:8888). Wikipédia directe sert de secours : elle refuse aujourd’hui le client d’AURORA, même avec un identifiant correct (mesuré le 28/10/2026). Un contact, adresse e-mail ou URL http(s) de 200 caractères au plus, est seulement ajouté à l’identifiant envoyé à Wikipédia : il peut aider, sans garantie.
                {!savedContact && <> <strong>Aucun contact renseigné</strong> (ni variable <span className="mono">AURORA_CONTACT</span> côté serveur, le cas échéant).</>}</p>
              <form className="set-inline" onSubmit={ev => { ev.preventDefault(); if (!contactInvalid) void apply('contact', { web_contact: contactClean }); }}>
                <input className="input" type="text" value={contact} onChange={ev => setContact(ev.target.value)} aria-label="Contact pour la recherche web (e-mail ou URL)"
                  placeholder="vous@exemple.fr ou https://…" maxLength={200} aria-invalid={contactInvalid} />
                <button className="btn btn-sm" type="submit" disabled={contactInvalid || contactClean === savedContact}>Enregistrer</button>
              </form>
              {contactInvalid && <span className="set-save set-save-err" role="alert">Adresse e-mail ou URL http(s) attendue.</span>}
              <SaveNote s={save.contact} />
            </>
          )}
        </Panel>

        <Panel title="Données" subtitle="Historique, base locale, décisions passées" icon={<Database size={18} />}>
          <h4 className="set-h4">Base de données</h4>
          <p className="set-small">{dbPath ? <><span className="mono">{dbPath}</span> (emplacement réel, en lecture seule)</> : <>Emplacement non renvoyé par le service ; par défaut <span className="mono">%USERPROFILE%\.aurora\aurora.db</span> (variable <span className="mono">AURORA_DB_PATH</span>).</>}</p>
          <h4 className="set-h4">Sauvegarde du jour</h4>
          <p className="set-small">Une copie de la base par jour, vérifiée, au démarrage puis chaque heure si elle manque. Aucune copie plus ancienne n’est effacée ni remplacée. Vide : le dossier <span className="mono">backups</span> à côté de la base. Un dossier sur un autre disque (par exemple <span className="mono">D:\Documents\sauvegardes-aurora</span>) protège aussi d’une panne du disque.</p>
          <form className="set-inline" onSubmit={ev => { ev.preventDefault(); void apply('backup', { backup_dir: backupDir.trim() }); }}>
            <input className="input" type="text" value={backupDir} onChange={ev => setBackupDir(ev.target.value)} aria-label="Dossier de la sauvegarde du jour"
              placeholder="Dossier existant, chemin complet (vide : à côté de la base)" maxLength={1000} />
            <button className="btn btn-sm" type="submit" disabled={backupDir.trim() === (settings?.backup_dir ?? '')}>Enregistrer</button>
          </form>
          <SaveNote s={save.backup} />
          <h4 className="set-h4">Historique des événements</h4>
          <p className="set-small">Efface le journal des événements (animations, relectures). Les mémoires, conversations et tâches sont conservées.</p>
          {!confirmClear ? (
            <button className="btn btn-sm btn-danger" onClick={() => { setConfirmClear(true); setClearState(undefined); }} disabled={!online}><Trash2 size={14} /> Effacer l’historique…</button>
          ) : (
            <div className="set-confirm" role="alertdialog" aria-label="Confirmer l’effacement">
              <span>Effacer définitivement tous les événements ?</span>
              <button className="btn btn-sm btn-danger" onClick={() => void clearEvents()}>Oui, effacer</button>
              <button className="btn btn-sm" onClick={() => setConfirmClear(false)}>Annuler</button>
            </div>
          )}
          <SaveNote s={clearState} />
          <div className="set-two set-perm-cols">
            <div><h4 className="set-h4">Autorisations accordées</h4><PermissionList items={granted} empty="Aucune autorisation accordée." /></div>
            <div><h4 className="set-h4">Autorisations refusées</h4><PermissionList items={denied} empty="Aucun refus enregistré." /></div>
          </div>
        </Panel>
      </div>

      <Panel className="set-quality" title="Qualité — comment vérifier qu’AURORA est réellement meilleure ?" icon={<FlaskConical size={18} />}
        subtitle="Banc d’essai du routeur : phrases différentes des exemples d’entraînement (fautes, voix, demandes ambiguës, contre-exemples)"
        actions={<button className="btn btn-sm btn-primary" onClick={() => void runBench()} disabled={benchRunning || !online}>{benchRunning ? <Spinner size={12} /> : <FlaskConical size={14} />} Lancer le banc d’essai</button>}>
        <p className="set-caveat">Résultats propres à ce banc et à cette machine : ils mesurent le choix d’intention et sa latence, pas la qualité des réponses.</p>
        {benchRunning && <EmptyState><Spinner /> Banc d’essai en cours (sans LLM)…</EmptyState>}
        {!benchRunning && benchErrorRaw && <Unavailable what="Résultats du banc" reason={benchErrorRaw} />}
        {!benchRunning && !benchErrorRaw && benchLoaded && !bench && <EmptyState>Aucun résultat enregistré. Lancez le banc d’essai pour mesurer l’exactitude du routeur.</EmptyState>}
        {!benchRunning && bench && benchView && <BenchResults view={benchView} />}
        {/* P2: the latest bench result as a page (service: POST /api/artefacts/bench) */}
        {!benchRunning && bench && (
          <div className="set-bench-page"><MakePageButton source="bench" label="Faire la page du banc" className="btn btn-sm" iconSize={14} /></div>
        )}
        {!benchRunning && bench && !benchView && !benchErrorRaw && (
          <div>
            <p className="set-small">Format de résultat non reconnu ; contenu brut :</p>
            <pre className="set-raw">{JSON.stringify(bench, null, 2).slice(0, 4000)}</pre>
          </div>
        )}
        <VerifyMods levels={settings?.verify_mods} disabled={!settings || !online} note={save.verify}
          onChange={(name, level) => void apply('verify', { verify_mods: { [name]: level } })} />
        <QualityReports online={online} />
      </Panel>

      <RoutinesPanel />

      <StartupPanel online={online} />

      {/* id: target of the « Paramètres › Studio » buttons of the Studio page */}
      <div id="set-studio" className="set-studio-anchor"><StudioSettingsPanel online={online} /></div>

      {/* CLAUDE.md: « Si une fonction n'est pas encore faite, l'interface le dit clairement. » */}
      <Panel title="Pas encore disponible" subtitle="Ce qu’AURORA ne sait pas encore faire" icon={<Lock size={18} />}>
        <ul className="set-small">
          <li>Regarder l’écran d’elle-même : AURORA ne le fait jamais. Seule une capture faite par votre clic est lue, sur ce PC. La souris et le clavier se règlent plus haut (« Souris et clavier ») : un geste à la fois, après votre accord.</li>
          <li>Rappels, alarmes et agenda : aucun outil de ce type, rien n’est programmé.</li>
          <li>Application de bureau (Tauri) : AURORA s’ouvre pour l’instant dans le navigateur.</li>
        </ul>
      </Panel>
    </div>
  );
}
