// Carte des outils (page Tâches) : la lecture de GET /api/tools/map. Lue quand la carte s'ouvre, relue à la demande
// (bouton « Actualiser », outil qui vient de servir). Une lecture qui échoue est dite : jamais une carte vide.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import type { ToolMapData } from '../../lib/types';
import { errorText } from '../brain/hooks';
import { isToolMap } from '../brain/toolMap';

export interface ToolMapState {
  /** La dernière carte lue (gardée à l'écran si une relecture échoue : `error` le dit alors). */
  map: ToolMapData | null;
  /** Pourquoi la dernière lecture a échoué, en clair ; '' si elle a réussi. */
  error: string;
  loading: boolean;
  reload: () => void;
}

/** Un service d'avant la carte répond 404 (« Not Found ») : on le dit avec des mots. */
function reason(err: unknown): string {
  const text = errorText(err);
  if (/^(Not Found|Erreur 404)$/i.test(text)) return 'Ce service ne connaît pas encore la carte des outils. Redémarrez AURORA pour charger la mise à jour.';
  return text;
}

/**
 * `active` : la carte est à l'écran (rien n'est lu sinon). `watch` : une clé de l'état réel déjà mesuré par
 * l'application (mode, Internet, terminal, souris et clavier) ; quand elle change, la carte est relue.
 */
export function useToolMap(active: boolean, watch = ''): ToolMapState {
  const [map, setMap] = useState<ToolMapData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const ticket = useRef(0);

  const reload = useCallback(() => {
    const mine = ++ticket.current;
    setLoading(true);
    api.toolMap()
      .then(data => {
        if (mine !== ticket.current) return;   // une lecture plus récente a pris le relais
        if (!isToolMap(data)) throw new Error('Le service a répondu autre chose que la carte des outils.');
        setMap(data);
        setError('');
      })
      .catch((err: unknown) => { if (mine === ticket.current) setError(reason(err)); })
      .finally(() => { if (mine === ticket.current) setLoading(false); });
  }, []);

  useEffect(() => { if (active) reload(); }, [active, watch, reload]);
  useEffect(() => () => { ticket.current++; }, []);   // page quittée : une réponse tardive est ignorée

  return { map, error, loading, reload };
}
