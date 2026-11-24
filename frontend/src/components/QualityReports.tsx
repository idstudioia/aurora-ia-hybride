// Paramètres › Qualité (H3): the answers reported with « Signaler » and their export for the routing bench.
// The export is a download in the format of backend/tests/data/routing_bench.jsonl: nothing is written in
// the repository; adding the lines to the corpus stays a deliberate step.
import { useCallback, useEffect, useState } from 'react';
import { Download, Flag, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { INTENT_LABELS, fmtDateTime } from '../lib/format';
import type { RunReport } from '../lib/types';
import { Badge, EmptyState, Spinner, Unavailable } from './ui';
import { loadReports, setRunReport } from './chat/reports';

const intent = (name: string | null | undefined) => (name ? INTENT_LABELS[name] ?? name : '—');

/** Save a blob as a file (also used by the page viewer, P2). */
export function download(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function QualityReports({ online }: { online: boolean }) {
  const [items, setItems] = useState<RunReport[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);

  const load = useCallback(() => {
    api.reports().then(list => { setItems(list); setError(''); void loadReports(true); })
      .catch(err => { setItems(null); setError(err instanceof Error ? err.message : 'Service indisponible'); });
  }, []);
  useEffect(() => { load(); }, [load]);

  const exportable = items?.filter(r => r.export === 'ok').length ?? 0;

  const exportBench = async () => {
    setBusy(true); setNote(null);
    try {
      const blob = await api.exportReports();
      download(blob, `signalements-banc-${new Date().toISOString().slice(0, 10)}.jsonl`);
      setNote({ kind: 'ok', text: `${exportable} phrase(s) exportée(s). À relire avant de les ajouter à backend/tests/data/routing_bench.jsonl.` });
    } catch (err) {
      setNote({ kind: 'err', text: err instanceof Error ? err.message : 'Export impossible' });
    } finally { setBusy(false); }
  };

  const remove = async (runId: number) => {
    setConfirm(null);
    try { await api.deleteRunReport(runId); setRunReport(runId, null); load(); }
    catch (err) { setNote({ kind: 'err', text: err instanceof Error ? err.message : 'Retrait impossible' }); }
  };

  return (
    <div className="set-reports">
      <div className="set-reports-head">
        <h4 className="set-h4"><Flag size={13} /> Réponses signalées{items ? ` (${items.length})` : ''}</h4>
        <div className="set-reports-actions">
          <button className="icon-btn" onClick={load} disabled={!online} title="Actualiser la liste" aria-label="Actualiser la liste des signalements"><RefreshCw size={14} /></button>
          <button className="btn btn-sm" onClick={() => void exportBench()} disabled={busy || !online || exportable === 0}
            title={exportable === 0 ? 'Aucune phrase exportable : précisez le type de demande attendu en signalant.' : 'Télécharger un fichier au format du banc de routage'}>
            {busy ? <Spinner size={12} /> : <Download size={14} />} Exporter pour le banc
          </button>
        </div>
      </div>
      <p className="set-small">« Signaler » sous une réponse l’ajoute ici. Avec le type de demande attendu, la phrase devient un cas du banc d’essai : {exportable} exportable(s), rejouée(s) à chaque « Lancer le banc d’essai », à part du score. L’export est un simple téléchargement ; rien n’est écrit dans le projet.</p>
      {note && <span className={`set-save set-save-${note.kind}`} role={note.kind === 'err' ? 'alert' : 'status'}>{note.text}</span>}
      {error && <Unavailable what="Signalements" reason={error} />}
      {!error && items === null && <EmptyState><Spinner /> Chargement des signalements…</EmptyState>}
      {items && items.length === 0 && <p className="set-small muted">Aucune réponse signalée pour l’instant.</p>}
      {items && items.length > 0 && (
        <ul className="set-reports-list">
          {items.map(r => (
            <li key={r.id}>
              <div className="set-reports-top">
                <q>{r.text}</q>
                {r.masked && <Badge tone="violet" title="Un secret a été masqué avant l’enregistrement.">masqué</Badge>}
                {r.sensitive && !r.masked && <Badge tone="amber" title="Message marqué sensible.">sensible</Badge>}
                <Badge tone={r.export === 'ok' ? 'green' : 'default'} title={r.export_label}>{r.export === 'ok' ? 'pour le banc' : 'non exporté'}</Badge>
              </div>
              <div className="set-reports-meta">
                <span>{r.reason_label}</span>
                <span>compris : <span className="mono">{intent(r.got_intent)}</span></span>
                <span>attendu : <span className="mono">{intent(r.expected_intent)}</span></span>
                <span>{fmtDateTime(r.updated_at)}</span>
                {r.export !== 'ok' && r.export_label && <span className="muted">· {r.export_label}</span>}
              </div>
              {r.comment && <p className="set-reports-comment">{r.comment}</p>}
              {confirm === r.run_id ? (
                <div className="set-confirm" role="alertdialog" aria-label="Confirmer le retrait">
                  <span>Retirer ce signalement ?</span>
                  <button className="btn btn-sm btn-danger" onClick={() => void remove(r.run_id)}>Oui, retirer</button>
                  <button className="btn btn-sm" onClick={() => setConfirm(null)}>Non</button>
                </div>
              ) : (
                <button className="btn btn-sm btn-ghost set-reports-remove" onClick={() => setConfirm(r.run_id)} disabled={!online}><Trash2 size={13} /> Retirer</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
