// P2 « Faire une page »: asks the service to build a page from a finished run (or from the latest bench
// result), then opens the viewer on the version just made. The figures come from the tools, not from the AI.
import { useState } from 'react';
import { FileText } from 'lucide-react';
import { api } from '../../lib/api';
import { Spinner } from '../ui';
import { artefactsChanged, openArtefact } from './store';
import './artefacts.css';

export default function MakePageButton({ source, label = 'Faire une page', className = 'chat-report-btn', iconSize = 11 }: {
  source: { runId: number } | 'bench';
  label?: string;
  /** Default: the small pill of « Signaler »; « btn btn-sm » among other actions. */
  className?: string;
  iconSize?: number;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const make = async () => {
    if (busy) return;   // not disabled while busy: the focus stays here and comes back after the viewer
    setBusy(true);
    setError('');
    try {
      const made = source === 'bench' ? await api.artefactFromBench() : await api.artefactFromRun(source.runId);
      artefactsChanged();
      openArtefact(made.artefact.id, made.version);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Page impossible à faire');
    } finally {
      setBusy(false);
    }
  };

  const title = source === 'bench'
    ? 'Range sur ce PC une page du dernier résultat du banc : chiffres et graphiques calculés par AURORA.'
    : 'Range sur ce PC une page de cette réponse : chiffres et graphiques calculés à partir des outils de la demande.';

  return (
    <span className="art-make">
      <button type="button" className={className} onClick={() => void make()} aria-busy={busy} title={title}>
        {busy ? <Spinner size={iconSize} /> : <FileText size={iconSize} />} {label}
      </button>
      {error && <span className="art-make-err" role="alert">{error}</span>}
    </span>
  );
}
