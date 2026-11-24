// Globe hologramme : constantes, petits calculs et types partagés, portés d'Athéna (HologrammeTerre.tsx).
// Les calculs sphériques communs viennent de ../globe/geo.ts (RAD, angularDistanceDeg), déjà utilisés par l'ancien globe.
import type { GlobeAircraft, GlobeEvent, GlobeNewsTheme, GlobeSatellite } from '../../lib/types';
import { RAD, angularDistanceDeg } from '../globe/geo';
import { fold } from '../brain/Highlight';

export const IMAGE_LOCALE = '/terre/blue-marble-2048.jpg';
/** Les avions volent à 10 km sur un rayon de 6 371 : on exagère l'altitude pour les détacher du sol. */
export const EXAGERATION_AVIONS = 12;
/** Les plus proches de la zone (le service les trie). 40 : ID Studio IA, « moins d'avions, juste l'animation ». */
export const MAX_AVIONS_AFFICHES = 40;
/** ISS et Tiangong prennent le modèle de station ; les autres, celui du satellite. */
export const STATIONS = [25544, 48274];
/** Distance de la caméra (rayons terrestres depuis le centre) sous laquelle la carte détaillée sert. */
export const ZOOM_DETAIL = 2.3;

/** Un lieu du patrimoine mondial (guide-sites.json, Wikidata) : n nom, d description, img fichier Commons, p pays, wp / wv titres Wikipédia / Wikivoyage, l notoriété. */
export interface SiteGuide { q: string; n: string; d?: string; la: number; lo: number; img?: string; p?: string; wp?: string; wv?: string; l: number }
/** Un pays (guide-pays.json, Wikidata). */
export interface PaysGuide { n: string; cap?: string; la?: number; lo?: number; pop: number; mon: string[]; lang: string[]; wv?: string }
export interface Capitale { nom: string; pays: string; lat: number; lon: number; rang: number }
export interface PointActu { pays: string; lat: number; lon: number; n: number }
export interface Lieu { nom: string; lat: number; lon: number }

export type ThemeChoisi = 'tout' | GlobeNewsTheme;
/** Les thèmes proposés : ceux dont un flux répond et vit (Athéna, actualitesService). */
export const THEMES_ACTU: ThemeChoisi[] = ['tout', 'actualite', 'economie', 'sport', 'culture', 'science', 'technologie', 'voyage'];

export type ModeCarte = 'aucune' | 'temperature' | 'humidite' | 'air';
export const MODES_CARTE: ModeCarte[] = ['aucune', 'temperature', 'humidite', 'air'];
export interface CarteValeurs { mode: ModeCarte; pas: number; lat0: number; lon0: number; nLat: number; nLon: number; valeurs: number[] }
/** Ce qu'on a choisi d'un clic sur le globe. */
export type Choix = { genre: 'avion'; a: GlobeAircraft } | { genre: 'sat'; s: GlobeSatellite } | { genre: 'evenement'; e: GlobeEvent };

/** La vignette d'une photo de Commons, à la largeur voulue (Commons la calcule et la garde). */
export const photoCommons = (fichier: string, largeur: number) =>
  `https://commons.wikimedia.org/wiki/Special:FilePath/${encodeURIComponent(fichier)}?width=${largeur}`;
/** Les mêmes vignettes, à une adresse qu'une texture WebGL peut lire (thumb.wikimedia.org répond avec l'en-tête CORS) :
 *  l'adresse de photoCommons passe par une redirection sans cet en-tête, que le navigateur refuse pour une texture
 *  (essayé le 08/11/2026). Une seule demande à l'API de Commons pour toutes les photos. Fichier → adresse ; null en échec. */
export async function vignettesCommons(fichiers: string[], largeur: number, signal?: AbortSignal): Promise<Map<string, string> | null> {
  const titres = fichiers.map(f => `File:${f}`).join('|');
  const url = 'https://commons.wikimedia.org/w/api.php?action=query&format=json&formatversion=2&origin=*&prop=imageinfo'
    + `&iiprop=url&iiurlwidth=${largeur}&titles=${encodeURIComponent(titres)}`;
  try {
    const r = await fetch(url, { signal, referrerPolicy: 'no-referrer' });
    if (!r.ok) return null;
    const j = await r.json() as { query?: { normalized?: { from: string; to: string }[]; pages?: { title?: string; imageinfo?: { thumburl?: string; url?: string }[] }[] } };
    // Commons récrit les titres (« _ » en espace, majuscule) : on retrouve le nom envoyé.
    const envoye = new Map((j.query?.normalized ?? []).map(n => [n.to, n.from]));
    const vues = new Map<string, string>();
    for (const p of j.query?.pages ?? []) {
      const info = p.imageinfo?.[0], brut = info?.thumburl ?? info?.url;
      if (!p.title || typeof brut !== 'string') continue;
      let adresse: URL;
      try { adresse = new URL(brut); } catch { continue; }
      if (adresse.protocol !== 'https:' || !adresse.hostname.endsWith('.wikimedia.org')) continue;
      adresse.search = '';   // les marqueurs de suivi (utm_*) ajoutés par l'API
      vues.set((envoye.get(p.title) ?? p.title).replace(/^File:/, ''), adresse.href);
    }
    return vues;
  } catch {
    return null;
  }
}
/** Combien de photos du guide à la fois selon le zoom : aucune de loin (la Terre entière), 10 de près. */
export const PHOTOS_GUIDE = (distance: number) => distance > 2.7 ? 0 : distance > 2.1 ? 4 : distance > 1.6 ? 7 : 10;

/**
 * Échelles de couleur des cartes (ID Studio IA, 04/10 : « les zones thermiques et l'humidité, un super
 * simulateur »). Qualité de l'air : les couleurs officielles de l'indice européen (AEE).
 */
export const ECHELLES: Record<Exclude<ModeCarte, 'aucune'>, { unite: string; arrets: [number, string][] }> = {
  temperature: { unite: '°C', arrets: [[-40, '#7e3cff'], [-20, '#2f6bff'], [0, '#33d1ff'], [10, '#4be37a'], [20, '#f5e04b'], [30, '#ff8a2a'], [40, '#e8243a']] },
  humidite: { unite: '%', arrets: [[0, '#c58a3a'], [30, '#e8d27a'], [60, '#5fc9b0'], [80, '#3c8dff'], [100, '#1d3fcf']] },
  air: { unite: '', arrets: [[0, '#50f0e6'], [20, '#50ccaa'], [40, '#f0e641'], [60, '#ff5050'], [80, '#960032'], [100, '#7d2181']] },
};

export const couleurEchelle = (arrets: [number, string][], x: number): [number, number, number] => {
  const hex = (h: string) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
  if (x <= arrets[0][0]) return hex(arrets[0][1]);
  for (let i = 1; i < arrets.length; i++) if (x <= arrets[i][0]) {
    const [a, ca] = arrets[i - 1], [b, cb] = arrets[i], f = (x - a) / (b - a), p = hex(ca), q = hex(cb);
    return [0, 1, 2].map(k => Math.round(p[k] + (q[k] - p[k]) * f)) as [number, number, number];
  }
  return hex(arrets[arrets.length - 1][1]);
};

/** Valeur mesurée interpolée entre les 4 nœuds voisins de la grille ; au-delà de ±80°, le bord. */
export const valeurGrille = (g: { pas: number; lat0: number; lon0: number; nLat: number; nLon: number }, a: number[], lat: number, lon: number) => {
  const fi = Math.max(0, Math.min(g.nLat - 1.001, (lat - g.lat0) / g.pas)), fj = (((lon - g.lon0) % 360) + 360) % 360 / g.pas;
  const i0 = Math.floor(fi), ti = fi - i0, j0 = Math.floor(fj) % g.nLon, j1 = (j0 + 1) % g.nLon, tj = fj - Math.floor(fj), k = (i: number, j: number) => i * g.nLon + j;
  return (a[k(i0, j0)] * (1 - tj) + a[k(i0, j1)] * tj) * (1 - ti) + (a[k(i0 + 1, j0)] * (1 - tj) + a[k(i0 + 1, j1)] * tj) * ti;
};

/** Même convention que les points des continents : lon = atan2(-z, x). */
export const versXYZ = (lat: number, lon: number, r: number): [number, number, number] => {
  const a = lat * RAD, o = lon * RAD;
  return [r * Math.cos(a) * Math.cos(o), r * Math.sin(a), -r * Math.cos(a) * Math.sin(o)];
};

export const sansParentheses = (nom: string) => nom.replace(/\s*\(.*\)$/, '');

/** La capitale la plus proche d'un point, et sa distance en km (rayon moyen 6 371 km). */
export function capitaleProche(capitales: Capitale[], pt: { lat: number; lon: number } | null) {
  if (!pt || !capitales.length) return null;
  let m = capitales[0], dm = Infinity;
  for (const c of capitales) { const d = angularDistanceDeg(pt, c); if (d < dm) { m = c; dm = d; } }
  return { ...m, km: Math.round(dm * RAD * 6371) };
}

// ── Préférences retenues dans le navigateur (clés aurora.globe.*). Stockage bloqué : le choix dure jusqu'au rechargement.
export function lireOui(cle: string, defaut = false): boolean {
  try { const v = localStorage.getItem(cle); return v === null ? defaut : v === '1'; } catch { return defaut; }
}
export function ecrireOui(cle: string, v: boolean): void {
  try { localStorage.setItem(cle, v ? '1' : '0'); } catch { /* stockage indisponible */ }
}
export function lireChoix<C extends string>(cle: string, permis: readonly C[], defaut: C): C {
  try { const v = localStorage.getItem(cle); return v !== null && (permis as readonly string[]).includes(v) ? v as C : defaut; } catch { return defaut; }
}
export function ecrireChoix(cle: string, v: string): void {
  try { localStorage.setItem(cle, v); } catch { /* stockage indisponible */ }
}

const fichiers = new Map<string, Promise<unknown>>();
/** Un fichier JSON de /terre (servi par l'interface, sans jeton), lu une fois ; un échec pourra être retenté. */
export function chargerLocal<D>(chemin: string): Promise<D> {
  let p = fichiers.get(chemin);
  if (!p) {
    p = fetch(chemin).then(r => { if (!r.ok) throw new Error(`${chemin} : ${r.status}`); return r.json(); });
    p.catch(() => fichiers.delete(chemin));
    fichiers.set(chemin, p);
  }
  return p as Promise<D>;
}

/** Un nom sans accents ni casse (fold), tirets et apostrophes en espaces : « Nouvelle-Zélande » → « nouvelle zelande »,
 *  comme le lieu que le service lit dans la demande (core/normalize.py). */
const cleDeNom = (nom: string) => fold(nom).replace(/[^a-z0-9]+/g, ' ').trim();

/** Le lieu nommé dans une demande à AURORA (« montre-moi Lyon sur le globe »), cherché sur ce PC seulement, sans
 *  réseau : un pays ou sa capitale (guide-pays.json, en français), puis les 7 342 villes et les capitales (Natural Earth,
 *  noms anglais, les plus peuplées d'abord), enfin les lieux du patrimoine mondial (guide-sites.json, en français, le
 *  plus connu d'abord : « Pétra », 08/11/2026). null : le lieu n'est dans aucune de ces listes. */
export async function trouverLieu(nom: string): Promise<Lieu | null> {
  const cle = cleDeNom(nom);
  if (!cle) return null;
  const pays = await chargerLocal<{ pays: PaysGuide[] }>('/terre/guide-pays.json').catch(() => null);
  for (const champ of ['n', 'cap'] as const) {
    const x = pays?.pays?.find(y => typeof y?.[champ] === 'string' && cleDeNom(y[champ]!) === cle);
    if (x && x.la != null && x.lo != null) return { nom: champ === 'n' ? x.n : x.cap!, lat: x.la, lon: x.lo };
  }
  const villes = await chargerLocal<{ villes: { n: string; la: number; lo: number }[] }>('/terre/villes.json').catch(() => null);
  const v = villes?.villes?.find(y => typeof y?.n === 'string' && cleDeNom(y.n) === cle);
  if (v && Number.isFinite(v.la) && Number.isFinite(v.lo)) return { nom: v.n, lat: v.la, lon: v.lo };
  const caps = await chargerLocal<{ capitales: Capitale[] }>('/terre/capitales.json').catch(() => null);
  const c = caps?.capitales?.find(y => typeof y?.nom === 'string' && cleDeNom(y.nom) === cle);
  if (c) return { nom: c.nom, lat: c.lat, lon: c.lon };
  const sites = await chargerLocal<{ sites: SiteGuide[] }>('/terre/guide-sites.json').catch(() => null);
  let s: SiteGuide | null = null;
  for (const y of sites?.sites ?? []) {
    if (typeof y?.n === 'string' && Number.isFinite(y.la) && Number.isFinite(y.lo) && cleDeNom(y.n) === cle
        && (!s || (y.l ?? 0) > (s.l ?? 0))) s = y;
  }
  return s ? { nom: s.n, lat: s.la, lon: s.lo } : null;
}

/** three.js 0.163 demande WebGL 2. Le contexte d'essai est rendu tout de suite. */
export function webglDisponible(): boolean {
  try {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) return false;
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return true;
  } catch {
    return false;
  }
}
