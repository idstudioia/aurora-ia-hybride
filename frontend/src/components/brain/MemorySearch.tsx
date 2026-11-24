// Side panel: hybrid memory search (FTS5 + local vectors) with scores, match reasons and
// excerpts; results are highlighted in the brain.
import { useState } from 'react';
import { Search, X } from 'lucide-react';
import type { MemoryHit } from '../../lib/types';
import { fixedFr } from '../../lib/format';
import { Badge, EmptyState, Spinner, Unavailable } from '../ui';
import Highlight from './Highlight';
import { KIND_LABELS, MATCHED_BY_LABELS } from './model';

interface Props {
  query: string;
  hits: MemoryHit[] | null;
  searching: boolean;
  error: string;
  inGraph: (memoryId: number) => boolean;
  onSearch: (q: string) => void;
  onClear: () => void;
  onPick: (hit: MemoryHit) => void;
}

export default function MemorySearch({ query, hits, searching, error, inGraph, onSearch, onClear, onPick }: Props) {
  const [text, setText] = useState(query);
  const best = hits?.reduce((m, h) => Math.max(m, h.score), 0) || 1;
  return (
    <div className="mem-search">
      <form className="mem-search-form" onSubmit={ev => { ev.preventDefault(); const q = text.trim(); if (q) onSearch(q); }} role="search">
        <Search size={15} aria-hidden="true" />
        <input className="mem-search-input" value={text} onChange={ev => setText(ev.target.value)} placeholder="Retrouver un souvenir, même reformulé…" aria-label="Rechercher dans la mémoire" maxLength={500} />
        {(text || hits) && <button type="button" className="icon-btn" aria-label="Effacer la recherche" onClick={() => { setText(''); onClear(); }}><X size={15} /></button>}
        <button type="submit" className="btn btn-sm btn-primary" disabled={!text.trim() || searching}>{searching ? <Spinner size={13} /> : 'Chercher'}</button>
      </form>
      {error ? <Unavailable what="Résultats" reason={error} />
        : hits === null ? <p className="muted mem-small">La recherche combine les mots-clés (FTS5) et, si les vecteurs locaux sont prêts, le sens de la question. Les résultats s'illuminent dans le cerveau.</p>
        : hits.length === 0 ? <EmptyState>Aucun souvenir ne correspond à « {query} ».</EmptyState>
        : (
          <ol className="mem-hits">
            {hits.map(hit => {
              const present = inGraph(hit.memory.id);
              return (
                <li key={hit.memory.id}>
                  <button type="button" className="mem-hit" onClick={() => onPick(hit)}>
                    <span className="mem-hit-head">
                      <strong><Highlight text={hit.memory.title} query={query} /></strong>
                      <span className="mem-score mono" title="Score de pertinence renvoyé par le service">{fixedFr(hit.score, 3)}</span>
                    </span>
                    <span className="mem-score-bar" aria-hidden="true"><span style={{ width: `${Math.max(4, Math.min(100, (hit.score / best) * 100))}%` }} /></span>
                    {hit.snippet && <span className="mem-hit-snippet"><Highlight text={hit.snippet} query={query} /></span>}
                    <span className="mem-badges">
                      {hit.matched_by.map(m => <Badge key={m} tone={m.startsWith('sem') || m === 'vector' || m === 'embedding' ? 'green' : 'cyan'}>{MATCHED_BY_LABELS[m] ?? m}</Badge>)}
                      <Badge>{hit.memory.category}</Badge>
                      <Badge tone="violet">{KIND_LABELS[hit.memory.kind] ?? hit.memory.kind}</Badge>
                      {!present && <Badge tone="amber" title="Le graphe affiche un nombre limité de nœuds">Hors du graphe</Badge>}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        )}
    </div>
  );
}
