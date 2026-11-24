// Side panel: detail of the node selected in the brain, with memory actions
// (confirm, pin, delete with confirmation, link to another memory).
import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ArrowRight, CheckCircle2, Link2, ListChecks, Pin, PinOff, Trash2 } from 'lucide-react';
import { api } from '../../lib/api';
import { INTENT_LABELS, fmtDateTime, relativeTime } from '../../lib/format';
import type { GraphNode, MemoryGraphData, MemoryItem } from '../../lib/types';
import { Badge, EmptyState, Spinner, Unavailable } from '../ui';
import {
  CONFIDENTIALITY_LABELS, KIND_LABELS, LEVEL_BY_ID, LEVELS, NODE_TYPE_LABELS, RELATION_LABELS, RUN_STATUS_LABELS,
  VERIFICATION_LABELS, nodeLevel, runStatusColor, sourceLabel, type LevelId,
} from './model';
import { errorText } from './hooks';

export interface Selection {
  id: string;
  type: GraphNode['type'];
  node: GraphNode | null;
  memory: MemoryItem | null;
  level: LevelId | null;
}

interface Props {
  selection: Selection | null;
  graph: MemoryGraphData | null;
  memories: MemoryItem[];
  memoriesError: string;
  /** Elements per level in the graph received. */
  levelCounts: Record<LevelId, number>;
  /** True totals in the database (service `totals`); null for an older service. */
  levelTotals?: Record<LevelId, number> | null;
  onSelect: (id: string) => void;
  onChanged: () => void;
  onDeleted: (memoryId: number) => void;
  onOpenTasks: () => void;
}

const RELATIONS = Object.keys(RELATION_LABELS);

const verificationTone = (v: string) => (v === 'user_confirmed' ? 'green' : v === 'verified' ? 'cyan' : v === 'contradicted' ? 'red' : 'default');
const isStructural = (n: GraphNode | undefined) => !n || n.type === 'core' || n.type === 'level';

export default function MemoryDetail(props: Props) {
  const { selection } = props;
  if (!selection) {
    return (
      <EmptyState>
        Sélectionnez un nœud du cerveau pour afficher son détail.<br />
        Survolez un nœud pour un aperçu, ou utilisez la recherche.
      </EmptyState>
    );
  }
  if (selection.type === 'core') return <CoreDetail counts={props.levelTotals ?? props.levelCounts} />;
  if (selection.type === 'level' && selection.level) {
    return <LevelDetail level={selection.level} graph={props.graph} count={props.levelCounts[selection.level]} total={props.levelTotals?.[selection.level] ?? null} onSelect={props.onSelect} />;
  }
  if (selection.type === 'memory') return <MemoryCard {...props} selection={selection} />;
  return <OtherDetail selection={selection} onOpenTasks={props.onOpenTasks} />;
}

function CoreDetail({ counts }: { counts: Record<LevelId, number> }) {
  return (
    <div className="mem-detail">
      <h3 className="mem-detail-title">Cœur AURORA</h3>
      <p className="mem-detail-text">Le cœur relie les quatre niveaux de mémoire. Le graphe animé est une visualisation de cette mémoire, et non son système de stockage.</p>
      <ul className="mem-level-list">
        {LEVELS.map(l => (
          <li key={l.id}><span className="mem-dot" style={{ background: l.color }} /> <strong>{l.label}</strong><span className="mono">{counts[l.id]}</span></li>
        ))}
      </ul>
    </div>
  );
}

function LevelDetail({ level, graph, count, total, onSelect }: { level: LevelId; graph: MemoryGraphData | null; count: number; total: number | null; onSelect: (id: string) => void }) {
  const meta = LEVEL_BY_ID[level];
  const items = useMemo(() => (graph?.nodes ?? [])
    .filter(n => !isStructural(n) && nodeLevel(n) === level)
    .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
    .slice(0, 14), [graph, level]);
  // True counts per category (service `groups`, rings view): what the graph does not show is said.
  const groups = useMemo(() => (graph?.groups ?? []).filter(g => g.level === level).sort((a, b) => b.count - a.count), [graph, level]);
  return (
    <div className="mem-detail">
      <div className="eyebrow" style={{ color: meta.color }}>Niveau de mémoire</div>
      <h3 className="mem-detail-title">{meta.label}</h3>
      <p className="mem-detail-text">{meta.description}</p>
      <p className="muted mem-small">
        {total !== null && <>{total} élément{total > 1 ? 's' : ''} en base · </>}
        {count} élément{count > 1 ? 's' : ''} visible{count > 1 ? 's' : ''} dans le graphe.
      </p>
      {groups.length > 0 && (
        <ul className="mem-level-list">
          {groups.map(g => (
            <li key={g.category}>
              <span className="mem-dot" style={{ background: meta.color }} /> <strong>{level === 'actions' ? INTENT_LABELS[g.category] ?? g.category : g.category}</strong>
              <span className="mono">{g.count}{g.count > g.shown ? ` (${g.count - g.shown} non affiché${g.count - g.shown > 1 ? 's' : ''})` : ''}</span>
            </li>
          ))}
        </ul>
      )}
      {items.length > 0 && (
        <ul className="mem-link-list">
          {items.map(n => (
            <li key={n.id}><button type="button" className="mem-link-btn" onClick={() => onSelect(n.id)}>
              <span className="mem-dot" style={{ background: n.type === 'run' ? runStatusColor(n.status) : meta.color }} />
              <span className="mem-link-label">{n.label}</span>
              <small>{NODE_TYPE_LABELS[n.type] ?? n.type}</small>
            </button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function OtherDetail({ selection, onOpenTasks }: { selection: Selection; onOpenTasks: () => void }) {
  const n = selection.node;
  const isRun = selection.type === 'run';
  return (
    <div className="mem-detail">
      <div className="eyebrow">{NODE_TYPE_LABELS[selection.type] ?? selection.type}</div>
      <h3 className="mem-detail-title">{n?.label || '(sans titre)'}</h3>
      <dl className="mem-dl">
        {selection.level && <><dt>Niveau</dt><dd>{LEVEL_BY_ID[selection.level].label}</dd></>}
        {isRun && <><dt>Statut</dt><dd><span className="mem-dot" style={{ background: runStatusColor(n?.status) }} /> {RUN_STATUS_LABELS[n?.status ?? ''] ?? n?.status ?? '—'}</dd></>}
        <dt>Date</dt><dd>{fmtDateTime(n?.created_at)}</dd>
      </dl>
      {isRun && <button type="button" className="btn btn-sm" onClick={onOpenTasks}><ListChecks size={14} /> Voir dans Tâches</button>}
    </div>
  );
}

function MemoryCard({ selection, graph, memories, memoriesError, onSelect, onChanged, onDeleted }: Props & { selection: Selection }) {
  const m = selection.memory;
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [linkTarget, setLinkTarget] = useState('');
  const [relation, setRelation] = useState('related');
  const [notice, setNotice] = useState('');

  useEffect(() => { setConfirmDelete(false); setError(''); setNotice(''); setLinkTarget(''); }, [selection.id]);

  const links = useMemo(() => {
    if (!graph || !selection.node) return [];
    const byId = new Map(graph.nodes.map(n => [n.id, n]));
    const id = selection.node.id;
    return graph.links
      .filter(l => l.source === id || l.target === id)
      .map(l => ({ link: l, outgoing: l.source === id, other: byId.get(l.source === id ? l.target : l.source) }))
      .filter(x => !isStructural(x.other));
  }, [graph, selection.node]);

  const targets = useMemo(() => memories.filter(x => x.id !== m?.id).sort((a, b) => a.title.localeCompare(b.title, 'fr')), [memories, m?.id]);

  if (!m) {
    return (
      <div className="mem-detail">
        <div className="eyebrow">Souvenir</div>
        <h3 className="mem-detail-title">{selection.node?.label ?? '(sans titre)'}</h3>
        <Unavailable what="Détails du souvenir" reason={memoriesError || 'Ce souvenir ne figure pas dans la liste renvoyée par le service.'} />
      </div>
    );
  }

  const act = async (name: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(name); setError(''); setNotice('');
    try {
      await fn();
      if (done) setNotice(done);
      onChanged();
      return true;
    } catch (err) {
      setError(errorText(err, 'Action impossible'));
      return false;
    } finally {
      setBusy('');
    }
  };

  const level = selection.level ?? 'persistent';
  const pinned = !!m.pinned;
  const refIsUrl = /^https?:\/\//i.test(m.source_ref);

  return (
    <div className="mem-detail">
      <div className="eyebrow" style={{ color: LEVEL_BY_ID[level].color }}>{LEVEL_BY_ID[level].label}</div>
      <h3 className="mem-detail-title">{m.title}</h3>
      <div className="mem-badges">
        <Badge tone="violet">{KIND_LABELS[m.kind] ?? m.kind}</Badge>
        <Badge>{m.category}</Badge>
        <Badge tone={m.confidentiality === 'sensitive' ? 'red' : m.confidentiality === 'public' ? 'cyan' : 'default'}>{CONFIDENTIALITY_LABELS[m.confidentiality] ?? m.confidentiality}</Badge>
        <Badge tone={verificationTone(m.verification)}>{VERIFICATION_LABELS[m.verification] ?? m.verification}</Badge>
        {pinned && <Badge tone="amber"><Pin size={11} /> Épinglé</Badge>}
      </div>
      {m.detail ? <p className="mem-detail-text mem-prewrap">{m.detail}</p> : <p className="muted mem-small">Aucun détail.</p>}
      <dl className="mem-dl">
        <dt>Type</dt><dd>{KIND_LABELS[m.kind] ?? m.kind}</dd>
        <dt>Catégorie</dt><dd>{m.category}</dd>
        <dt>Source</dt><dd>{sourceLabel(m.source)}{m.source_ref && <> · {refIsUrl ? <a href={m.source_ref} target="_blank" rel="noopener noreferrer">{m.source_ref}</a> : <span className="mono">{m.source_ref}</span>}</>}</dd>
        <dt>Créé</dt><dd>{fmtDateTime(m.created_at)}</dd>
        <dt>Modifié</dt><dd>{fmtDateTime(m.updated_at)}</dd>
        <dt>Confidentialité</dt><dd>{CONFIDENTIALITY_LABELS[m.confidentiality] ?? m.confidentiality}</dd>
        <dt>Vérification</dt><dd>{VERIFICATION_LABELS[m.verification] ?? m.verification}</dd>
        <dt>Rappels</dt><dd>{m.recall_count}{m.last_recalled_at ? ` · dernier ${relativeTime(m.last_recalled_at)}` : ''}</dd>
      </dl>

      <div className="mem-actions">
        <button type="button" className="btn btn-sm" disabled={!!busy || m.verification === 'user_confirmed'}
          onClick={() => void act('confirm', () => api.updateMemory(m.id, { verification: 'user_confirmed' }), 'Souvenir confirmé.')}>
          {busy === 'confirm' ? <Spinner size={13} /> : <CheckCircle2 size={14} />} {m.verification === 'user_confirmed' ? 'Confirmé' : 'Confirmer'}
        </button>
        <button type="button" className="btn btn-sm" disabled={!!busy}
          onClick={() => void act('pin', () => api.updateMemory(m.id, { pinned: pinned ? 0 : 1 }), pinned ? 'Souvenir désépinglé.' : 'Souvenir épinglé.')}>
          {busy === 'pin' ? <Spinner size={13} /> : pinned ? <PinOff size={14} /> : <Pin size={14} />} {pinned ? 'Désépingler' : 'Épingler'}
        </button>
        {!confirmDelete && (
          <button type="button" className="btn btn-sm btn-danger" disabled={!!busy} onClick={() => setConfirmDelete(true)}><Trash2 size={14} /> Supprimer</button>
        )}
      </div>
      {confirmDelete && (
        <div className="mem-confirm" role="alertdialog" aria-label="Confirmer la suppression">
          <p>Supprimer définitivement « {m.title} » de la mémoire ?</p>
          <div className="mem-actions">
            <button type="button" className="btn btn-sm" onClick={() => setConfirmDelete(false)} disabled={busy === 'delete'}>Annuler</button>
            <button type="button" className="btn btn-sm btn-danger" disabled={busy === 'delete'}
              onClick={async () => { if (await act('delete', () => api.deleteMemory(m.id))) onDeleted(m.id); }}>
              {busy === 'delete' ? <Spinner size={13} /> : <Trash2 size={14} />} Supprimer
            </button>
          </div>
        </div>
      )}

      <h4 className="mem-subtitle">Liens</h4>
      {links.length === 0 ? <p className="muted mem-small">Aucun lien avec un autre souvenir.</p> : (
        <ul className="mem-link-list">
          {links.map(({ link, outgoing, other }) => (
            <li key={`${link.source}-${link.target}-${link.relation}`}>
              <button type="button" className="mem-link-btn" onClick={() => other && onSelect(other.id)}>
                {outgoing ? <ArrowRight size={13} /> : <ArrowLeft size={13} />}
                <small>{RELATION_LABELS[link.relation] ?? link.relation}</small>
                <span className="mem-link-label">{other?.label ?? '—'}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <form className="mem-link-form" onSubmit={ev => {
        ev.preventDefault();
        const target = Number(linkTarget);
        if (!target) return;
        void act('link', () => api.linkMemories(m.id, target, relation), 'Lien créé.').then(ok => { if (ok) setLinkTarget(''); });
      }}>
        <label className="field">Lier à une autre mémoire
          <select className="select" value={linkTarget} onChange={ev => setLinkTarget(ev.target.value)} disabled={!targets.length}>
            <option value="">{targets.length ? 'Choisir un souvenir…' : 'Aucun autre souvenir'}</option>
            {targets.map(t => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select>
        </label>
        <div className="mem-row">
          <select className="select" value={relation} onChange={ev => setRelation(ev.target.value)} aria-label="Type de relation">
            {RELATIONS.map(r => <option key={r} value={r}>{RELATION_LABELS[r]}</option>)}
          </select>
          <button type="submit" className="btn btn-sm" disabled={!linkTarget || !!busy}>{busy === 'link' ? <Spinner size={13} /> : <Link2 size={14} />} Lier</button>
        </div>
      </form>

      {error && <p className="mem-error" role="alert">{error}</p>}
      {notice && <p className="mem-notice" role="status">{notice}</p>}
    </div>
  );
}
