// Carte des outils de la page Tâches (11/11/2026) : le plan des outils qu'AURORA a vraiment, « dans le même style que
// la mémoire ». C'est le canvas de la mémoire (BrainGraph, layout="tools"), pas un second moteur de dessin. Tout ce
// qui est montré vient de GET /api/tools/map : ce que le service ne sait pas est dit, jamais inventé.
import { useMemo, useRef } from 'react';
import { RefreshCw } from 'lucide-react';
import BrainGraph, { type BrainHandle } from '../BrainGraph';
import { Spinner, Unavailable } from '../ui';
import { MODE_LABELS, fmtDateTime, fmtTime } from '../../lib/format';
import { useReducedMotion } from '../../lib/motion';
import { useEventStream } from '../../lib/stream';
import type { MemoryItem } from '../../lib/types';
import { useDebounced } from '../brain/hooks';
import { toolNodeId } from '../brain/rings';
import { TOOL_STATES, TOOL_STATE_LABELS, internetText, stateCounts, toolMapGraph } from '../brain/toolMap';
import type { ToolMapState } from './useToolMap';
import './toolmap.css';

const NO_MEMORIES = new Map<number, MemoryItem>();
const nothing = () => undefined;

/** L'hexagone d'un état, le même que sur le canvas : plein avec un point (disponible), en pointillés avec une barre
 *  (désactivé) ou une croix (indisponible). Toujours accompagné du mot : c'est un signe, pas le seul message. */
export function ToolGlyph({ state, size = 16 }: { state: string; size?: number }) {
  const known = state === 'available' || state === 'disabled' || state === 'absent';
  return (
    <svg className="toolmap-glyph" data-state={known ? state : 'unknown'} viewBox="0 0 20 20" width={size} height={size} aria-hidden="true">
      <polygon points="10,1.5 17.4,5.75 17.4,14.25 10,18.5 2.6,14.25 2.6,5.75" />
      {state === 'available' && <circle cx="10" cy="10" r="2.3" />}
      {state === 'disabled' && <path d="M6.2 10h7.6" />}
      {state === 'absent' && <path d="M7 7l6 6M13 7l-6 6" />}
    </svg>
  );
}

export default function ToolMapView({ state, selected, onSelect }: {
  state: ToolMapState;
  /** Nom de l'outil dont la fiche est ouverte. */
  selected: string | null;
  onSelect: (name: string | null) => void;
}) {
  const { map, error, loading, reload } = state;
  const brain = useRef<BrainHandle>(null);
  const reduced = useReducedMotion();
  const graph = useMemo(() => (map ? toolMapGraph(map) : null), [map]);
  const counts = useMemo(() => (map ? stateCounts(map) : null), [map]);

  // Un outil qui sert vraiment (événement réel tool.result) s'allume sur la carte, puis les comptes sont relus.
  const reloadSoon = useDebounced(reload, 900);
  useEventStream(e => {
    if (e.kind !== 'tool.result') return;
    brain.current?.play(e, { live: true });
    reloadSoon();
  });

  const absent = map?.absent_modules ?? [];
  const usage = map?.usage ?? null;

  return (
    <div className="toolmap-root">
      {absent.length > 0 && (
        <div className="toolmap-alert" role="alert">
          <strong>
            {absent.length > 1
              ? `${absent.length} modules d'outils n'ont pas pu être chargés : leurs outils ne sont pas sur la carte.`
              : "Un module d'outils n'a pas pu être chargé : ses outils ne sont pas sur la carte."}
          </strong>
          <ul>{absent.map(m => <li key={m.module}>{m.reason || m.module}</li>)}</ul>
        </div>
      )}
      {map && error && (
        <p className="toolmap-stale" role="status">
          La dernière lecture a échoué ({error}). La carte montre l'état lu {map.checked_at ? `à ${fmtTime(map.checked_at)}` : 'plus tôt'}.
        </p>
      )}

      <div className="toolmap-stage">
        {graph ? (
          <BrainGraph ref={brain} layout="tools" data={graph} memories={NO_MEMORIES}
            selectedId={selected ? toolNodeId(selected) : null} onSelect={nothing} onSelectTool={onSelect}
            highlight={null} cutoffMs={null} statusOverride={null} reducedMotion={reduced} />
        ) : (
          <div className="toolmap-center">
            {error ? (
              <>
                <Unavailable what="Carte des outils" reason={error} plural={false} />
                <button type="button" className="btn btn-sm" onClick={reload} disabled={loading}>
                  {loading ? <Spinner size={12} /> : <RefreshCw size={14} />} Réessayer
                </button>
              </>
            ) : <><Spinner /> Lecture des outils…</>}
          </div>
        )}
        {map && map.tools.length === 0 && (
          <div className="toolmap-center toolmap-center--soft">Le service n'annonce aucun outil.</div>
        )}
        {counts && (
          <ul className="toolmap-legend" aria-label="Légende : état des outils">
            {TOOL_STATES.map(s => (
              <li key={s}><ToolGlyph state={s} /><span>{TOOL_STATE_LABELS[s]}</span><strong>{counts[s]}</strong></li>
            ))}
          </ul>
        )}
      </div>

      {map && (
        <>
          <div className="toolmap-foot">
            <p className="toolmap-status">
              État lu {map.checked_at ? <>à <strong>{fmtTime(map.checked_at)}</strong></> : 'à une heure inconnue'}
              {' · '}Mode <strong>{MODE_LABELS[map.mode] ?? map.mode}</strong>
              {' · '}Internet : <strong>{internetText(map.internet)}</strong>
            </p>
            <button type="button" className="btn btn-sm" onClick={reload} disabled={loading} title="Relire l'état réel des outils">
              {loading ? <Spinner size={12} /> : <RefreshCw size={14} />} Actualiser
            </button>
          </div>
          {usage ? (
            <>
              {/* La note du service, telle quelle : elle dit ce que le nombre compte et ce qu'il ne compte pas. */}
              <p className="toolmap-note">{usage.note}</p>
              <p className="toolmap-note">
                Plus un outil a été utilisé dans une demande, plus son hexagone est gros.{' '}
                {usage.since ? `Compté depuis le ${fmtDateTime(usage.since)}.` : 'Le journal est vide : rien n’est encore compté.'}{' '}
                Survolez un hexagone pour lire son nom ; zoomez (boutons, ou Ctrl + molette) pour en lire plusieurs.
              </p>
            </>
          ) : (
            <p className="toolmap-note">
              Compteur d'utilisations indisponible : la taille des hexagones ne dit rien de leur usage.{' '}
              Survolez un hexagone pour lire son nom ; zoomez (boutons, ou Ctrl + molette) pour en lire plusieurs.
            </p>
          )}
        </>
      )}
    </div>
  );
}
