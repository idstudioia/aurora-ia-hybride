// « Signaler » under an answer (H3): reason, optional expected request type (INTENT_LABELS), optional
// comment. One report per run on the service: sending again corrects it, « Annuler le signalement »
// removes it. The sentence never leaves the PC; the service masks it when it holds a secret.
import { useId, useState, type FormEvent } from 'react';
import { Flag } from 'lucide-react';
import { api } from '../../lib/api';
import { INTENT_LABELS } from '../../lib/format';
import type { ReportReason } from '../../lib/types';
import { Spinner } from '../ui';
import { REPORT_REASON_LABELS } from './labels';
import { setRunReport, useRunReport } from './reports';

const REASONS = Object.entries(REPORT_REASON_LABELS) as [ReportReason, string][];

export default function ReportButton({ runId, gotIntent }: { runId: number; gotIntent?: string }) {
  const report = useRunReport(runId);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<ReportReason | ''>('');
  const [expected, setExpected] = useState('');
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const uid = useId();

  const openForm = () => {
    setReason(report?.reason ?? '');
    setExpected(report?.expected_intent ?? '');
    setComment(report?.comment ?? '');
    setError('');
    setOpen(true);
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try { await action(); setOpen(false); }
    catch (err) { setError(err instanceof Error ? err.message : 'Signalement impossible'); }
    finally { setBusy(false); }
  };

  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    if (!reason || busy) return;
    void run(async () => {
      setRunReport(runId, await api.saveRunReport(runId, { reason, expected_intent: expected || null, comment: comment.trim() }));
    });
  };

  const cancel = () => void run(async () => { await api.deleteRunReport(runId); setRunReport(runId, null); });

  const doneTitle = report
    ? `Signalé : ${report.reason_label}${report.expected_intent ? ` · attendu : ${INTENT_LABELS[report.expected_intent] ?? report.expected_intent}` : ''}. Cliquez pour corriger ou annuler.`
    : 'Signaler une mauvaise réponse : elle devient une phrase de test pour le banc d’essai.';

  return (
    <div className="chat-report">
      <button type="button" className={`chat-report-btn ${report ? 'chat-report-done' : ''}`} onClick={() => (open ? setOpen(false) : openForm())}
        aria-expanded={open} aria-controls={`${uid}-form`} title={doneTitle}>
        <Flag size={11} fill={report ? 'currentColor' : 'none'} /> {report ? 'Signalé' : 'Signaler'}
      </button>
      {open && (
        <form id={`${uid}-form`} className="chat-report-form" onSubmit={submit} aria-label="Signaler cette réponse"
          onKeyDown={ev => { if (ev.key === 'Escape') setOpen(false); }}>
          <fieldset>
            <legend>Qu’est-ce qui ne va pas ?</legend>
            {REASONS.map(([id, label]) => (
              <label key={id} className={`chat-report-reason ${reason === id ? 'active' : ''}`}>
                <input type="radio" name={`${uid}-reason`} value={id} checked={reason === id} onChange={() => setReason(id)} />
                {label}
              </label>
            ))}
          </fieldset>
          <label className="chat-report-field">Type de demande attendu (facultatif)
            <select className="select" value={expected} onChange={ev => setExpected(ev.target.value)}>
              <option value="">— je ne sais pas —</option>
              {Object.entries(INTENT_LABELS).map(([id, label]) => (
                <option key={id} value={id}>{label}{id === gotIntent ? ' (compris ainsi)' : ''}</option>
              ))}
            </select>
          </label>
          <label className="chat-report-field">Commentaire (facultatif)
            <textarea className="textarea" rows={2} maxLength={1000} value={comment} onChange={ev => setComment(ev.target.value)} />
          </label>
          <p className="chat-report-note">
            {gotIntent ? <>Compris comme : <strong>{INTENT_LABELS[gotIntent] ?? gotIntent}</strong>. </> : null}
            Le signalement reste sur ce PC ; la phrase est masquée si elle contient un secret.
          </p>
          <div className="chat-report-actions">
            <button type="submit" className="btn btn-sm btn-primary" disabled={!reason || busy}>{busy ? <Spinner size={12} /> : null}{report ? 'Corriger' : 'Envoyer'}</button>
            {report && <button type="button" className="btn btn-sm btn-danger" onClick={cancel} disabled={busy}>Annuler le signalement</button>}
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setOpen(false)} disabled={busy}>Fermer</button>
            {error && <span className="chat-report-err" role="alert">{error}</span>}
          </div>
        </form>
      )}
    </div>
  );
}
