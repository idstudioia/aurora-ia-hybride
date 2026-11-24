// Globe hologramme : la lecture des routes /api/globe/* du service. Elles renvoient l'enveloppe ToolResult
// (reviews/globe-contrat.md) ; une route absente, en échec ou une réponse mal formée ne casse rien :
// le panneau le dit. Aucune valeur n'est inventée pour remplacer ce qui manque.
// Les couches relues à intervalle passent par useToolResult (../globe/hooks.ts, déjà utilisé par l'ancien globe) ;
// etatDe() en tire l'état affiché. interroger() sert aux lectures ponctuelles (zone cliquée, résumés du guide).
import type {
  GlobeAirData, GlobeAircraftData, GlobeEventsData, GlobeGuideData, GlobeNewsData, GlobeSatellitesData, GlobeTrafficData,
  GlobeWeatherData, GlobeWindData, Source, ToolResult,
} from '../../lib/types';
import { isObj, type Json } from '../../lib/tolerant';
import type { ToolState } from '../globe/hooks';
import { T } from './textes';

/** null tant que la réponse n'est pas arrivée (ou couche éteinte). */
export type Etat<D> = { ok: true; data: D; sources: Source[] } | { ok: false; prive: boolean; raison: string };

const tableau = (d: Json, cle: string) => Array.isArray(d[cle]);
const fini = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** « Not Found » (FastAPI : route pas encore installée) dit en français. */
function raisonLisible(message: string): string {
  const m = message.trim();
  if (/^not found$/i.test(m) || /^erreur 404$/i.test(m)) return T.routeAbsente;
  return m || 'erreur inconnue';
}

/** L'état d'un ToolResult reçu : Mode Privé (ok=false et data.private_mode), échec, forme inattendue, ou données. */
function etatResultat<D>(r: ToolResult<unknown>, normaliser: (d: unknown) => D | null): Etat<D> {
  if (!r?.ok) {
    const prive = isObj(r?.data) && r.data.private_mode === true;
    return { ok: false, prive, raison: prive ? T.prive : raisonLisible(r?.error || r?.summary || '') };
  }
  const d = normaliser(r.data);
  return d === null ? { ok: false, prive: false, raison: T.illisible } : { ok: true, data: d, sources: Array.isArray(r.sources) ? r.sources : [] };
}

/** Une lecture ponctuelle d'une route ; ne lève jamais d'erreur. */
export async function interroger<D>(appel: () => Promise<ToolResult<unknown>>, normaliser: (d: unknown) => D | null): Promise<Etat<D>> {
  try { return etatResultat(await appel(), normaliser); }
  catch (err) { return { ok: false, prive: false, raison: raisonLisible(err instanceof Error ? err.message : String(err)) }; }
}

/** L'état d'une couche relue par useToolResult ; null si elle est éteinte ou pas encore arrivée. */
export function etatDe<D>(s: ToolState, active: boolean, normaliser: (d: unknown) => D | null): Etat<D> | null {
  if (!active) return null;
  if (s.result) return etatResultat(s.result, normaliser);
  if (s.error) return { ok: false, prive: false, raison: raisonLisible(s.error) };
  return null;
}

export const messageEtat = (e: { prive: boolean; raison: string }) => e.prive ? T.prive : T.indisponible(e.raison);

// ── Formes attendues (contrat). Les tableaux absents deviennent vides, jamais des valeurs inventées.
export const normAvions = (d: unknown): GlobeAircraftData | null =>
  isObj(d) && tableau(d, 'liste') && fini(d.total) && isObj(d.lieu) ? d as unknown as GlobeAircraftData : null;
export const normMonde = (d: unknown): GlobeTrafficData | null =>
  isObj(d) && tableau(d, 'avions') && fini(d.mesureA) ? { ...d, total: fini(d.total) ? d.total : (d.avions as unknown[]).length } as unknown as GlobeTrafficData : null;
export const normSatellites = (d: unknown): GlobeSatellitesData | null =>
  isObj(d) && tableau(d, 'liste') ? {
    liste: (d.liste as unknown[]).filter((s): s is Json => isObj(s) && fini(s.lat) && fini(s.lon) && fini(s.altitude))
      .map(s => ({ ...s, trace: Array.isArray(s.trace) ? s.trace : [] })),
  } as unknown as GlobeSatellitesData : null;
export const normMeteo = (d: unknown): GlobeWeatherData | null => isObj(d) && fini(d.temperature) ? d as unknown as GlobeWeatherData : null;
export const normEvenements = (d: unknown): GlobeEventsData | null =>
  isObj(d) && tableau(d, 'evenements') ? { evenements: d.evenements, sources: tableau(d, 'sources') ? d.sources : [] } as GlobeEventsData : null;

/** Une grille nLat × nLon dont chaque tableau demandé a la bonne longueur. */
function grille(d: unknown, ...cles: string[]): d is Json {
  if (!isObj(d) || ![d.pas, d.lat0, d.lon0, d.nLat, d.nLon].every(fini)) return false;
  const n = (d.nLat as number) * (d.nLon as number);
  return n > 0 && cles.every(k => Array.isArray(d[k]) && (d[k] as unknown[]).length === n);
}
export const normVent = (d: unknown): GlobeWindData | null => {
  if (!grille(d, 'u', 'v', 'pluie')) return null;
  const n = (d.nLat as number) * (d.nLon as number);
  const facultatif = (k: string) => Array.isArray(d[k]) && (d[k] as unknown[]).length === n ? d[k] : undefined;
  return { ...d, temperature: facultatif('temperature'), humidite: facultatif('humidite') } as unknown as GlobeWindData;
};
export const normAir = (d: unknown): GlobeAirData | null => grille(d, 'indice') ? d as unknown as GlobeAirData : null;
export const normActualites = (d: unknown): GlobeNewsData | null =>
  isObj(d) && tableau(d, 'articles') ? {
    mesureA: d.mesureA, points: tableau(d, 'points') ? d.points : [], sources: tableau(d, 'sources') ? d.sources : [],
    articles: (d.articles as unknown[]).filter(a => isObj(a) && typeof a.titre === 'string' && typeof a.lien === 'string'),
  } as GlobeNewsData : null;
export const normResume = (d: unknown): GlobeGuideData | null => isObj(d) && typeof d.texte === 'string' ? d as unknown as GlobeGuideData : null;
