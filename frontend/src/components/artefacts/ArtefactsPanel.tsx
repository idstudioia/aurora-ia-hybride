// P2 « Pages » in Tâches: the pages stored on this PC, the most recent first; a click opens the viewer.
// Read again after each creation or deletion (store.ts counter). Same rows as the run list (tasks.css).
import { useEffect, useState } from 'react';
import { FileText } from 'lucide-react';
import { api } from '../../lib/api';
import { ARTEFACT_KIND_LABELS, relativeTime } from '../../lib/format';
import type { ArtefactSummary } from '../../lib/types';
import { EmptyState, Panel, Spinner, Unavailable } from '../ui';
import { openArtefact, useArtefactChanges } from './store';

export default function ArtefactsPanel() {
  const changes = useArtefactChanges();
  const [items, setItems] = useState<ArtefactSummary[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    api.artefacts()
      .then(list => { if (alive) { setItems(list); setError(''); } })
      .catch(err => { if (alive) setError(err instanceof Error ? err.message : 'Service local indisponible'); });
    return () => { alive = false; };
  }, [changes]);

  const count = items?.length ?? 0;
  return (
    <Panel icon={<FileText size={18} />} title="Pages"
      subtitle={error ? 'indisponibles' : items ? `${count} page${count > 1 ? 's' : ''}` : 'chargement…'}>
      {error ? <Unavailable what="Pages" reason={error} />
        : items === null ? <EmptyState><Spinner /> Chargement…</EmptyState>
          : items.length === 0 ? <EmptyState>Aucune page pour le moment. Sous une réponse, « Faire une page » en crée une.</EmptyState>
            : (
              <ul className="tasks-runs">
                {items.map(a => (
                  <li key={a.id}>
                    <button type="button" className="tasks-run" onClick={() => openArtefact(a.id)}>
                      <FileText size={12} className="tasks-page-icon" aria-hidden="true" />
                      <span className="tasks-run-copy">
                        <strong title={a.title}>{a.title || '—'}</strong>
                        <small>
                          {ARTEFACT_KIND_LABELS[a.kind] ?? a.kind} · {a.versions} version{a.versions > 1 ? 's' : ''}
                          {a.sensitive ? ' · sensible' : ''}
                        </small>
                      </span>
                      <time dateTime={a.updated_at}>{relativeTime(a.updated_at)}</time>
                    </button>
                  </li>
                ))}
              </ul>
            )}
    </Panel>
  );
}
