// Le petit cerveau mémoire (cahier des charges § 6, 08/11/2026 ; placé dans la carte Mémoire ou en bas à gauche du centre
// à la demande d’ID Studio IA, au lieu de flotter à gauche du noyau). C'est la vue « anneaux »
// de la page Mémoire (BrainGraph en mode compact), avec les vraies données de /api/memories/graph : une visualisation du
// système de mémoire existant, pas un second moteur. Ses animations viennent des vrais événements (même tri que la page
// Mémoire) et ne sont jamais enregistrées comme souvenirs. Un clic ouvre la page Mémoire.
import { useCallback, useEffect, useRef, useState } from 'react';
import BrainGraph, { type BrainHandle } from '../BrainGraph';
import { api } from '../../lib/api';
import { useAurora } from '../../lib/store';
import { useEventStream } from '../../lib/stream';
import type { MemoryGraphData, MemoryItem } from '../../lib/types';
import { isBrainEvent } from '../brain/model';
import { errorText, useDebounced } from '../brain/hooks';
import { useReducedMotion } from '../globe/hooks';

/** Moins de nœuds que la page Mémoire (1 000) : la vignette fait 200 pixels. */
const LIMITE = 400;
const AUCUN_SOUVENIR = new Map<number, MemoryItem>();
const rien = () => undefined;

/** Où il se pose (choix d’ID Studio IA, 08/11/2026) : `carte`, dans la carte Mémoire de la colonne de gauche (grand écran) ;
 *  `centre`, en bas à gauche du centre, à côté de la barre d'outils (écran étroit, où la carte est loin sous le centre). */
export default function CerveauFlottant({ place }: { place: 'carte' | 'centre' }) {
  const { online, navigate } = useAurora();
  const cerveau = useRef<BrainHandle>(null);
  const [graphe, setGraphe] = useState<MemoryGraphData | null>(null);
  const [erreur, setErreur] = useState('');
  const reduit = useReducedMotion();

  const lire = useCallback(() => {
    api.memoryGraph(LIMITE).then(g => { setGraphe(g); setErreur(''); }).catch((e: unknown) => setErreur(errorText(e)));
  }, []);
  useEffect(() => { if (online) lire(); }, [online, lire]);
  const relire = useDebounced(lire, 1200);
  useEventStream(e => {
    if (e.subject_type === 'conversation') { relire(); return; }
    if (!isBrainEvent(e)) return;
    relire();
    cerveau.current?.play(e, { live: true });
  });
  const ouvrir = useCallback(() => navigate('memory'), [navigate]);

  return (
    <div className={`labo-cerveau labo-cerveau-${place}`}>
      {graphe ? (
        <BrainGraph ref={cerveau} compact layout="rings" rotate data={graphe} memories={AUCUN_SOUVENIR} selectedId={null}
          onSelect={rien} highlight={null} cutoffMs={null} statusOverride={null} reducedMotion={reduit} onActivate={ouvrir} />
      ) : (
        <button type="button" className="labo-cerveau-attente" onClick={ouvrir} title="Ouvrir la Mémoire">
          {erreur ? `Mémoire indisponible : ${erreur}` : online ? 'Chargement de la mémoire…' : 'Mémoire hors ligne'}
        </button>
      )}
    </div>
  );
}
