// Permission request card: shows exactly what will be done or sent, then lets the user grant or
// deny. Only this user action (POST /api/permissions/{id}) can grant a permission.
import { useState } from 'react';
import { Check, Cloud, FolderLock, MessageSquareText, MousePointerClick, ShieldAlert, Skull, Terminal, X } from 'lucide-react';
import { useAurora } from '../../lib/store';
import { useStreamStatus } from '../../lib/stream';
import type { Permission, RunResult } from '../../lib/types';
import { fmtUsd } from '../../lib/format';
import { Badge } from '../ui';
import LiveProgress from './LiveProgress';
import { lastEventId, runEvents, useLiveFeed } from './live';
import { ACTION_LABELS, REDACTION_LABELS, RISK_LABELS, TIER_LABELS } from './labels';
import { arr, fmtBytes, fmtNum, isObj, num, plainText, str } from './text';

const PREVIEW_KEYS: Record<string, string> = {
  tool: 'Outil', title: 'Étape', query: 'Requête', topic: 'Sujet', city: 'Ville', url: 'Adresse', folder: 'Dossier',
  content: 'Contenu', category: 'Catégorie', older_than_days: 'Plus ancien que (jours)', limit: 'Limite',
};

/** Generic preview: top-level scalars plus the step parameters (one level). */
function KeyValues({ data, skip = [] }: { data: Record<string, unknown>; skip?: string[] }) {
  const params = isObj(data.params) ? data.params : {};
  const merged: Record<string, unknown> = { ...data, ...params };
  const entries = Object.entries(merged).filter(([k, v]) => !skip.includes(k) && k !== 'params' && v !== null && v !== undefined && v !== '' && (typeof v !== 'object' || Array.isArray(v)));
  if (!entries.length) return null;
  return (
    <dl className="chat-kv">
      {entries.slice(0, 12).map(([k, v]) => (
        <div key={k}><dt>{PREVIEW_KEYS[k] ?? k.replace(/_/g, ' ')}</dt><dd>{Array.isArray(v) ? v.filter(x => typeof x !== 'object').map(String).join(', ') || `${v.length} élément(s)` : typeof v === 'boolean' ? (v ? 'oui' : 'non') : plainText(v)}</dd></div>
      ))}
    </dl>
  );
}

function cloudText(p: Record<string, unknown>): string {
  const direct = str(p, 'text', 'redacted_text', 'sent_text', 'prompt', 'content', 'message');
  if (direct) return direct;
  const msgs = arr(p, 'messages');
  return msgs.map(m => (isObj(m) ? `${str(m, 'role') || 'message'} : ${str(m, 'content')}` : String(m))).join('\n\n');
}

function CloudPreview({ p }: { p: Record<string, unknown> }) {
  const text = cloudText(p);
  const system = str(p, 'system', 'system_prompt');
  const findings = arr(p, 'redactions', 'findings', 'masked', 'redacted').filter(isObj);
  const cost = num(p, 'estimated_cost_usd', 'cost_estimate_usd', 'estimate_usd', 'cost_usd');
  const budget = num(p, 'budget_remaining_usd', 'remaining_usd', 'budget_remaining');
  const tier = str(p, 'tier', 'cloud_tier');
  const chars = num(p, 'chars', 'text_chars') ?? (text ? text.length : null);
  return (
    <div className="chat-perm-preview">
      <div className="chat-perm-facts">
        <div><span>Modèle</span><strong>{str(p, 'model') || 'non précisé'}</strong></div>
        <div><span>Palier</span><strong>{TIER_LABELS[tier] ?? (tier || '—')}</strong></div>
        <div><span>Coût estimé</span><strong>{cost === null ? 'non estimé' : fmtUsd(cost)}</strong></div>
        <div><span>Budget restant</span><strong>{budget === null ? 'inconnu' : fmtUsd(budget)}</strong></div>
        {num(p, 'max_cost_usd') !== null && <div><span>Coût maximal</span><strong>{fmtUsd(num(p, 'max_cost_usd'))}</strong></div>}
        {num(p, 'request_cap_usd') !== null && <div><span>Plafond par requête</span><strong>{fmtUsd(num(p, 'request_cap_usd'))}</strong></div>}
        {str(p, 'effort') && <div><span>Effort</span><strong>{str(p, 'effort')}</strong></div>}
        {str(p, 'sensitivity') && <div><span>Sensibilité</span><strong>{str(p, 'sensitivity') === 'sensitive' ? 'sensible' : 'normale'}</strong></div>}
      </div>
      <div className="chat-perm-label">Texte exact qui sera envoyé{chars !== null ? ` (${fmtNum(chars, 0)} caractères)` : ''}</div>
      {text ? <pre className="chat-perm-text">{text}</pre> : <div className="chat-perm-missing">Aperçu du texte absent : refusez si vous n’êtes pas certain du contenu.</div>}
      {system && (
        <details className="chat-perm-details"><summary>Consignes système envoyées</summary><pre className="chat-perm-text">{system}</pre></details>
      )}
      <div className="chat-perm-label">Données masquées avant envoi</div>
      {findings.length ? (
        <ul className="chat-perm-redactions">
          {findings.map((f, i) => { const t = str(f, 'type', 'kind'); return <li key={i}>{plainText(str(f, 'label')) || REDACTION_LABELS[t] || t} × {num(f, 'count', 'n') ?? 1}</li>; })}
        </ul>
      ) : <div className="chat-perm-none">Aucune donnée personnelle détectée.</div>}
    </div>
  );
}

function FilesPreview({ p }: { p: Record<string, unknown> }) {
  const files = arr(p, 'files', 'items', 'paths', 'candidates');
  const sizeOf = (f: unknown) => (isObj(f) ? num(f, 'size_bytes', 'size', 'bytes') : null);
  const computed = files.reduce<number>((acc, f) => acc + (sizeOf(f) ?? 0), 0);
  const total = num(p, 'total_bytes', 'total_size_bytes', 'size_bytes') ?? (computed || null);
  const count = num(p, 'count', 'file_count') ?? files.length;
  // The preview may list fewer files than `count` (the server sends the first ones only).
  const shown = Math.min(files.length, 40);
  const dest = str(p, 'destination', 'quarantine_dir', 'target_dir', 'target');
  return (
    <div className="chat-perm-preview">
      <div className="chat-perm-facts">
        <div><span>Fichiers</span><strong>{fmtNum(count, 0)}</strong></div>
        <div><span>Taille totale</span><strong>{fmtBytes(total)}</strong></div>
      </div>
      {files.length > 0 && (
        <ul className="chat-perm-files">
          {files.slice(0, 40).map((f, i) => (
            <li key={i}><span className="mono">{isObj(f) ? str(f, 'path', 'name') : String(f)}</span>{sizeOf(f) !== null && <small>{fmtBytes(sizeOf(f))}</small>}</li>
          ))}
          {count > shown && <li className="muted">… et {fmtNum(count - shown, 0)} autre(s){count > files.length ? ' (non détaillés dans l’aperçu)' : ''}</li>}
        </ul>
      )}
      {str(p, 'tool') === 'files.restore' ? (   /* B4: the opposite move, out of the quarantine */
        <div className="chat-perm-reversible">{plainText(str(p, 'restore_hint'))}</div>
      ) : p.reversible !== false && (
        <div className="chat-perm-reversible">
          Réversible : les fichiers sont déplacés{dest ? <> vers <span className="mono">{dest}</span></> : ' vers la quarantaine AURORA'}, pas supprimés, et peuvent être restaurés.
          {str(p, 'restore_hint') && <> {plainText(str(p, 'restore_hint'))}</>}
        </div>
      )}
    </div>
  );
}

function ProcessPreview({ p }: { p: Record<string, unknown> }) {
  const list = arr(p, 'processes', 'targets');
  const procs = (list.length ? list : num(p, 'pid') !== null ? [p] : []).filter(isObj);
  const refused = arr(p, 'refused').filter(isObj);
  return (
    <div className="chat-perm-preview">
      {str(p, 'summary') && <div className="chat-perm-reason">{plainText(str(p, 'summary'))}</div>}
      {procs.length === 0 && <div className="chat-perm-missing">Aucun processus ciblé dans l’aperçu : refusez en cas de doute.</div>}
      {refused.length > 0 && (
        <ul className="chat-perm-redactions">
          {refused.map((r, i) => <li key={i}>Protégé, non arrêté : {str(r, 'name') || `PID ${num(r, 'pid') ?? '?'}`}{str(r, 'reason') ? ` — ${plainText(str(r, 'reason'))}` : ''}</li>)}
        </ul>
      )}
      <ul className="chat-perm-files">
        {procs.map((pr, i) => (
          <li key={i}>
            <span><strong>{str(pr, 'name', 'process') || 'Processus'}</strong>{num(pr, 'pid') !== null && <span className="mono"> · PID {num(pr, 'pid')}</span>}</span>
            <small>
              {[str(pr, 'username', 'user'), num(pr, 'memory_mb') !== null ? `${fmtNum(num(pr, 'memory_mb'), 0)} Mo` : '', num(pr, 'cpu_percent') !== null ? `CPU ${fmtNum(num(pr, 'cpu_percent'))} %` : ''].filter(Boolean).join(' · ')}
            </small>
          </li>
        ))}
      </ul>
      <div className="chat-perm-missing">{plainText(str(p, 'warning')) || 'Le processus sera arrêté : le travail non enregistré dans ce programme peut être perdu.'}</div>
    </div>
  );
}

const RISK_WORD: Record<string, string> = {
  lecture: 'Lecture seule', modification: 'Modifie des fichiers', suppression: 'Supprime des fichiers',
  réseau: 'Accède au réseau',
};

/** B2 (04/11/2026): the exact command, the folder, what the CODE says it does, its risk and how to undo. */
function ShellPreview({ p }: { p: Record<string, unknown> }) {
  const command = str(p, 'command');
  const risk = str(p, 'risk');
  const outside = p.outside_allowed === true;
  const origin = str(p, 'origin');
  return (
    <div className="chat-perm-preview">
      <div className="chat-perm-facts">
        <div><span>Dossier</span><strong className="mono">{str(p, 'cwd') || '—'}{outside ? ' ⚠' : ''}</strong></div>
        <div><span>Risque</span><strong>{str(p, 'risk_label') || RISK_WORD[risk] || risk || '—'}</strong></div>
        {str(p, 'does') && <div><span>Ce que ça fait</span><strong>{plainText(str(p, 'does'))}</strong></div>}
        {origin && origin !== 'user' && <div><span>Origine</span><strong>proposée par un contenu externe</strong></div>}
      </div>
      <div className="chat-perm-label">Commande exacte qui sera exécutée</div>
      {command ? <pre className="chat-perm-text">{command}</pre>
        : <div className="chat-perm-missing">Commande absente de l’aperçu : refusez en cas de doute.</div>}
      {outside && <div className="chat-perm-missing">Ce dossier n’est pas dans vos dossiers autorisés.</div>}
      {str(p, 'undo') && <div className="chat-perm-reversible">{plainText(str(p, 'undo'))}</div>}
      <div className="chat-perm-missing">La sortie de la commande sera traitée comme une donnée, jamais comme une consigne.</div>
    </div>
  );
}

/** 9/11/2026 (tools/pc_control.py): the one gesture, the window Windows reports at that point, the exact text or keys.
 *  The real gesture is checked against this card (its fingerprint) and refused if anything changed. */
function PcPreview({ p }: { p: Record<string, unknown> }) {
  const win = isObj(p.window) ? p.window : {};
  const x = num(p, 'x'); const y = num(p, 'y');
  const text = str(p, 'text');
  const keys = str(p, 'keys');
  const origin = str(p, 'origin');
  return (
    <div className="chat-perm-preview">
      <div className="chat-perm-facts">
        <div><span>Fenêtre visée</span><strong>{str(win, 'title') || 'sans titre'}{str(win, 'exe') ? <span className="mono"> ({str(win, 'exe')})</span> : null}</strong></div>
        {x !== null && y !== null && <div><span>Position</span><strong className="mono">{fmtNum(x, 0)}, {fmtNum(y, 0)} px</strong></div>}
        {keys && <div><span>Touches</span><strong className="mono">{keys}</strong></div>}
        <div><span>Risque</span><strong>{str(p, 'risk_label') || str(p, 'risk') || '—'}</strong></div>
        {origin && origin !== 'user' && <div><span>Origine</span><strong>proposé par l’agent, pas par vous</strong></div>}
      </div>
      {str(p, 'does') ? <div className="chat-perm-reason">{plainText(str(p, 'does'))}</div>
        : <div className="chat-perm-missing">Geste absent de l’aperçu : refusez en cas de doute.</div>}
      {text && <>
        <div className="chat-perm-label">Texte exact qui sera tapé ({fmtNum(text.length, 0)} caractères)</div>
        <pre className="chat-perm-text">{text}</pre>
      </>}
      {str(p, 'undo') && <div className="chat-perm-reversible">{plainText(str(p, 'undo'))}</div>}
      <div className="chat-perm-missing">Ne touchez pas la souris pendant le geste. Si la fenêtre a changé entre-temps, rien n’est fait.</div>
    </div>
  );
}

/** 9/11/2026 (tools/youtube_comments.py): the exact comment, the video read in the channel's public feed, the channel.
 *  The real post is checked against this card (its fingerprint) and refused if anything changed. */
function YoutubePreview({ p }: { p: Record<string, unknown> }) {
  const text = str(p, 'text');
  const url = str(p, 'video_url');
  const published = str(p, 'video_published');
  const origin = str(p, 'origin');
  const drafted = str(p, 'drafted_by') === 'local_model';
  return (
    <div className="chat-perm-preview">
      <div className="chat-perm-facts">
        <div><span>Vidéo</span><strong>{str(p, 'video_title') || 'inconnue'}{published ? ` (${published.slice(0, 10)})` : ''}</strong></div>
        <div><span>Chaîne</span><strong>{str(p, 'channel_title') || '—'}</strong></div>
        <div><span>Texte</span><strong>{drafted ? `rédigé par l’IA locale (${str(p, 'model') || 'Ollama'})` : 'vos mots'}</strong></div>
        <div><span>Risque</span><strong>{str(p, 'risk_label') || str(p, 'risk') || '—'}</strong></div>
        {origin && origin !== 'user' && <div><span>Origine</span><strong>proposé par l’agent, pas par vous</strong></div>}
      </div>
      {p.same_channel === false && <div className="chat-perm-missing">Ce n’est pas la chaîne indiquée dans Paramètres › Studio : le commentaire part sur la chaîne reliée par la connexion Google.</div>}
      {text ? <>
        <div className="chat-perm-label">Texte exact qui sera publié ({fmtNum(text.length, 0)} caractères), visible par tous, en votre nom</div>
        <pre className="chat-perm-text">{text}</pre>
      </> : <div className="chat-perm-missing">Texte absent de l’aperçu : refusez.</div>}
      {/* Not in a .chat-perm-label (capitals): a video id is case-sensitive, the address must read as it is. */}
      {url && <div className="chat-perm-reason">Lien de la vidéo : <a className="mono" href={url} target="_blank" rel="noopener noreferrer">{url}</a></div>}
      {str(p, 'undo') && <div className="chat-perm-reversible">{plainText(str(p, 'undo'))}</div>}
      {str(p, 'consent_scope') === 'reglage' && <div className="chat-perm-reason">Accordé par le réglage « Publier sans redemander » (Paramètres › Studio).</div>}
    </div>
  );
}

function expiresIn(iso: string): string {
  const ms = Date.parse(iso) - Date.now();
  if (!Number.isFinite(ms)) return '';
  if (ms <= 0) return 'expirée';
  const min = Math.round(ms / 60000);
  return min < 1 ? 'expire dans moins d’une minute' : `expire dans ${min} min`;
}

const ICONS: Record<string, typeof Cloud> = { 'cloud.send': Cloud, 'files.quarantine': FolderLock, 'process.terminate': Skull, 'shell.run': Terminal, 'pc.control': MousePointerClick, 'youtube.comment': MessageSquareText };

export default function PermissionCard({ permission, onResult, compact = false }: {
  permission: Permission;
  onResult?: (run: RunResult) => void;
  compact?: boolean;
}) {
  const { decidePermission, lastError, health } = useAurora();
  const connected = useStreamStatus();
  const [deciding, setDeciding] = useState<null | { decision: 'grant' | 'deny'; since: number; at: number }>(null);
  useLiveFeed(deciding !== null);
  const [failed, setFailed] = useState(false);
  const p = isObj(permission.preview) ? permission.preview : {};
  const risk = RISK_LABELS[permission.risk] ?? { label: permission.risk, tone: 'amber' as const };
  const Icon = ICONS[permission.action] ?? ShieldAlert;
  const decided = permission.status !== 'pending';

  // CLAUDE.md: in Équilibré and Avancé a cloud consent may cover the whole conversation — never for sensitive data.
  // 04/11/2026, ID Studio IA: « si je donne autorisation pas besoin de la redemander » — it is the first button.
  const mode = health?.capabilities.mode;
  const conversationWide = permission.action === 'cloud.send' && (mode === 'equilibre' || mode === 'avance')
    && str(p, 'sensitivity') !== 'sensitive';

  const decide = async (decision: 'grant' | 'deny', scope: 'once' | 'conversation' = 'once') => {
    setFailed(false);
    setDeciding({ decision, since: lastEventId(permission.task_id), at: Date.now() });
    const run = await decidePermission(permission.id, decision, scope);
    setDeciding(null);
    if (run) onResult?.(run);
    else setFailed(true);
  };

  const liveEvents = deciding && permission.task_id !== null ? runEvents(permission.task_id).filter(e => e.id > deciding.since) : [];

  return (
    <div className={`chat-perm chat-perm-${permission.risk} ${compact ? 'chat-perm-compact' : ''}`} role="group" aria-label={`Autorisation : ${permission.title}`}>
      <div className="chat-perm-head">
        <span className="chat-perm-icon"><Icon size={18} /></span>
        <div className="chat-perm-titles">
          <strong>{plainText(permission.title) || ACTION_LABELS[permission.action] || permission.action}</strong>
          <span>{str(p, 'tool') === 'files.restore' ? 'Restauration de fichiers' : ACTION_LABELS[permission.action] ?? permission.action}{permission.expires_at && !decided ? ` · ${expiresIn(permission.expires_at)}` : ''}</span>
        </div>
        <Badge tone={risk.tone}>{risk.label}</Badge>
      </div>
      {permission.action === 'cloud.send' ? <CloudPreview p={p} />
        : permission.action === 'files.quarantine' ? <FilesPreview p={p} />
        : permission.action === 'process.terminate' ? <ProcessPreview p={p} />
        : permission.action === 'shell.run' ? <ShellPreview p={p} />
        : permission.action === 'pc.control' ? <PcPreview p={p} />
        : permission.action === 'youtube.comment' ? <YoutubePreview p={p} />
        : <div className="chat-perm-preview"><KeyValues data={p} /></div>}
      {permission.reason && <div className="chat-perm-reason">{plainText(permission.reason)}</div>}
      {decided ? (
        <div className="chat-perm-status">Décision enregistrée : {permission.status === 'granted' ? 'autorisé' : permission.status === 'denied' ? 'refusé' : 'expiré'}</div>
      ) : (
        <div className="chat-perm-actions">
          {conversationWide && (
            <button type="button" className="btn btn-primary" disabled={!!deciding} onClick={() => void decide('grant', 'conversation')}
              title="Les prochains envois de cette conversation partiront sans vous redemander ; chaque texte envoyé reste affiché. Révocable à tout moment.">
              <Check size={16} /> {deciding?.decision === 'grant' ? 'Autorisation…' : 'Autoriser sans redemander'}
            </button>
          )}
          <button type="button" className={conversationWide ? 'btn' : 'btn btn-primary'} disabled={!!deciding} onClick={() => void decide('grant')}>
            <Check size={16} /> {conversationWide ? 'Seulement cette fois' : deciding?.decision === 'grant' ? 'Autorisation…' : 'Autoriser'}
          </button>
          <button type="button" className="btn btn-danger" disabled={!!deciding} onClick={() => void decide('deny')}>
            <X size={16} /> {deciding?.decision === 'deny' ? 'Refus…' : 'Refuser'}
          </button>
        </div>
      )}
      {deciding && (
        <LiveProgress events={liveEvents} startedAt={deciding.at} connected={connected} compact
          waitingLabel={deciding.decision === 'grant' ? 'Autorisation envoyée, reprise du traitement…' : 'Refus envoyé…'} />
      )}
      {failed && <div className="chat-perm-error" role="alert">{lastError || 'Décision impossible (service indisponible ou demande expirée).'}</div>}
    </div>
  );
}
