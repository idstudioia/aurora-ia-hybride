// Globe hologramme : les textes, en français seulement. Repris de l'objet `globe` (fr) d'Athéna
// (client/src/lib/texteInterface.ts), passés au vouvoiement. Les phrases dites à voix haute sont
// CONSTRUITES À PARTIR DES DONNÉES, jamais par un modèle de langage : il dirait n'importe quoi
// sur une magnitude ou une altitude.
import type { GlobeEventType } from '../../lib/types';
import type { ModeCarte, ThemeChoisi } from './outils';

const LOCALE = 'fr-FR';
const pluriel = (n: number, mot: string) => `${n} ${mot}${n > 1 ? 's' : ''}`;

/** 12 345 ; d décimales au plus, virgule française. */
export const nombre = (n: number, d = 0) => n.toLocaleString(LOCALE, { maximumFractionDigits: d });
export const heure = (ms: number) => new Date(ms).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
export const jour = (ms: number) => new Date(ms).toLocaleDateString(LOCALE, { day: 'numeric', month: 'short' });
export const dateHeure = (ms: number) => new Date(ms).toLocaleString(LOCALE, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
const age = (ms: number, mots: (n: number, u: 'min' | 'h' | 'j') => string) => {
  const m = Math.max(0, (Date.now() - ms) / 60000);
  return m < 60 ? mots(Math.round(m), 'min') : m < 2880 ? mots(Math.round(m / 60), 'h') : mots(Math.round(m / 1440), 'j');
};
/** « il y a 3 h » : l'âge d'un événement, d'après la date de sa source. */
export const ilYa = (ms: number) => age(ms, (n, u) => `il y a ${n} ${u}`);
/** La même chose en toutes lettres, pour la voix : « 4 j » était lu tel quel (Athéna, 04/10). */
export const ilYaParle = (ms: number) => age(ms, (n, u) => `il y a ${pluriel(n, u === 'min' ? 'minute' : u === 'h' ? 'heure' : 'jour')}`);

const DIRECTIONS = ['le nord', 'le nord-est', "l'est", 'le sud-est', 'le sud', 'le sud-ouest', "l'ouest", 'le nord-ouest'];
/** « le nord-est » pour un cap en degrés (0 = nord). */
export const direction = (deg: number | null) => deg == null ? null : DIRECTIONS[Math.round(((deg % 360) + 360) % 360 / 45) % 8];

export const TYPES_EVENEMENTS: GlobeEventType[] = ['seisme', 'tsunami', 'volcan', 'tornade', 'cyclone'];

export const T = {
  titre: 'Le ciel au-dessus de chez vous',
  aide: 'Molette ou deux doigts : zoom · glisser : tourner · clic : détail · Échap : désélectionner',
  fermer: 'Fermer',
  chargement: 'Lecture des sources…',
  prive: "Mode Privé : aucune source en ligne n'est appelée.",
  priveTerre: 'Le globe montre la Terre enregistrée sur ce PC : la Blue Marble, les continents en points, les capitales, la carte routière et le guide (sans photos).',
  routeAbsente: "cette fonction du service n'est pas encore installée",
  illisible: 'réponse du service illisible',
  indisponible: (raison: string) => `Indisponible : ${raison}`,
  directOff: 'Données en direct désactivées : rien ne part sur Internet.',
  activer: 'Activer — passe par Internet',
  desactiver: 'Désactiver le direct',
  /** 24/11/2026 : l'interrupteur du direct en tête de la page Monde 3D, à côté de la phrase qui en parle. */
  directAllumer: 'Allumer le direct',
  directAllume: 'Direct allumé',
  directPrive: 'Direct bloqué en mode Privé',
  detail: 'Zoom fort : carte détaillée de la région (NASA Blue Marble, ~500 m par pixel, sans nuages).',
  detailOff: 'Activez le direct pour une carte détaillée au zoom fort.',
  affiches: (n: number) => `les ${n} plus proches sont affichés`,
  anime: 'Entre deux mesures (1 par minute), chaque avion avance selon sa vitesse et son cap mesurés, 2 min 30 au plus.',
  monde: (n: number, total: number) => `Trafic mondial : ${nombre(n)} vrais avions affichés sur ${nombre(total)} en vol`,
  mondeAnime: 'relu toutes les 5 min, prolongé entre deux',
  cliquerZone: 'Cliquez sur une zone du globe pour voir son trafic en détail.',
  zoneAvions: (n: number) => `${pluriel(n, 'avion')} en vol dans cette zone`,
  capitaleProche: (nom: string, pays: string, km: number) => `Capitale la plus proche : ${nom} (${pays}), ${nombre(km)} km`,
  voirMonde: '← Revenir au monde',
  meteoPoint: 'Météo du point',
  vent: 'vent',
  iconeMeteo: 'Météo et catastrophes',
  iconeVolants: 'Objets volants : avions et satellites',
  iconeDirect: 'Données en direct',
  iconeRoutes: 'Carte routière : villes et routes',
  iconeVoix: 'AURORA commente à voix haute',
  ventPluie: 'Vent et pluie',
  ventAnime: 'animation accélérée : direction et force mesurées',
  catastrophes: 'Catastrophes (points rouges)',
  sourceIndisponible: 'source indisponible',
  types: { seisme: 'Séismes (M ≥ 4,5, 7 j)', tsunami: 'Bulletins tsunami', volcan: 'Volcans en alerte', tornade: 'Tornades', cyclone: 'Cyclones' } as Record<GlobeEventType, string>,
  /** Le mot cherché dans le nom de la source de chaque type (GET /api/globe/evenements, `sources[].nom`). */
  motsSources: { seisme: 'séismes', tsunami: 'tsunamis', volcan: 'volcans', tornade: 'tornades', cyclone: 'cyclones' } as Record<GlobeEventType, string>,
  typeUn: { seisme: 'Séisme', tsunami: 'Bulletin tsunami', volcan: 'Volcan', tornade: 'Alerte tornade', cyclone: 'Cyclone' } as Record<GlobeEventType, string>,
  infos: {
    magnitude: 'Magnitude', profondeur: 'Profondeur', ressenti: 'Ressenti', intensite: 'Intensité estimée (MMI)', alerte: "Niveau d'alerte",
    statut: 'Statut', categorie: 'Catégorie du bulletin', pays: 'Pays', force: 'Force', debut: 'Début', fin: 'Fin', zone: 'Zone', expire: 'Expire',
  } as Record<string, string>,
  statuts: { reviewed: 'vérifié par un sismologue', automatic: 'calcul automatique' } as Record<string, string>,
  temoignages: (n: number) => pluriel(n, 'témoignage'),
  sourceDe: (s: string) => `Source : ${s}`,
  carte: 'Carte du monde',
  cartes: { aucune: 'Aucune', temperature: 'Chaleur', humidite: 'Humidité', air: 'Air' } as Record<ModeCarte, string>,
  sourceAir: "indice européen de la qualité de l'air, Copernicus (CAMS)",
  survoler: 'survolez le globe pour lire la valeur',
  indiceAir: 'indice',
  routesTitre: 'Carte routière — 7 342 villes',
  routesSource: 'Natural Earth (domaine public), sur ce PC. Les grands axes seulement, pas les rues. Zoomez pour voir plus de villes.',
  routesLegende: { autoroute: 'Autoroutes', nationale: 'Nationales', route: 'Routes', ferry: 'Ferries' },
  aucunAvion: 'Aucun avion en vol mesuré dans la zone.',
  satellites: 'Satellites',
  calcule: "position calculée par le service depuis l'orbite publiée (CelesTrak)",
  secoursTle: 'secours : TLE API',
  nasa: (d: string, satellite?: string | null) => `Image NASA du ${d} (VIIRS, ${satellite || 'Suomi NPP'}) ; zones non survolées : Blue Marble`,
  imageLocale: 'Image NASA Blue Marble (sur ce PC)',
  mesureA: (h: string) => `mesuré à ${h}`,
  altitude: 'altitude',
  vitesse: 'vitesse',
  cap: 'cap',
  capSur: (nom: string) => `Cap sur ${nom}`,
  demandeAurora: 'demandé à AURORA',
  satelliteAbsent: (norad: number) => `Le satellite n° ${norad} ne fait pas partie des satellites suivis.`,
  lieuAbsent: (nom: string) => `« ${nom} » n'est ni dans les pays, ni dans les villes, ni dans les lieux du patrimoine mondial enregistrés sur ce PC : le globe ne s'est pas déplacé.`,
  recherche: 'Recherche en cours…',
  sansWebgl: "Le globe hologramme ne peut pas s'afficher : WebGL est indisponible dans ce navigateur. Choisissez « Classique » en haut de la page.",
  sansThree: "Le globe hologramme n'a pas pu se charger (bibliothèque three.js). Choisissez « Classique » en haut de la page.",
  /** Page Monde 3D : le choix d'affichage (aurora.world.vue). */
  vues: { titre: 'Affichage du globe', hologramme: 'Hologramme', classique: 'Classique' },
  basculeClassique: "WebGL est indisponible dans ce navigateur : le globe hologramme ne peut pas s'afficher, voici le globe classique.",
  barreIcones: 'Couches du globe',
  sources: 'Sources et licences',
  sourcesConsultees: 'Sources consultées',
  /** Les attributions (client/public/terre/SOURCE.txt d'Athéna, reviews/globe-contrat.md règle 6). */
  attributions: [
    ['Terre', 'NASA Visible Earth, Blue Marble (domaine public), sur ce PC.'],
    ['Image du jour et carte détaillée', "NASA GIBS : VIIRS (Suomi NPP, NOAA-20 ou NOAA-21) et Blue Marble NG, imagerie NASA, domaine public. « We acknowledge the use of imagery provided by services from NASA's Global Imagery Browse Services (GIBS), part of NASA's ESDIS. »"],
    ['Avions', 'adsb.lol (licence ODbL). Trafic mondial et secours : OpenSky Network (recherche et enseignement sans but lucratif), seulement si vous l’avez allumé dans Paramètres.'],
    ['Satellites', 'orbites publiées par CelesTrak ; positions calculées par le service local (sgp4).'],
    ['Météo, vent, pluie, chaleur, humidité', 'Open-Meteo (CC BY 4.0).'],
    ["Qualité de l'air", '« Generated using Copernicus Atmosphere Monitoring Service information » (CAMS), via Open-Meteo.'],
    ['Catastrophes', 'USGS (séismes), NOAA PTWC et NTWC (tsunamis), GDACS (volcans, cyclones), NWS (tornades, États-Unis).'],
    ['Actualités', "les médias cités sous chaque article (flux RSS) ; titres et résumés tels qu'ils les écrivent."],
    ['Capitales, villes, routes', 'Natural Earth (domaine public), sur ce PC.'],
    ['Guide', 'lieux et pays : Wikidata (domaine public) ; photos : Wikimedia Commons ; textes : Wikipédia et Wikivoyage (CC BY-SA).'],
    ['Modèles 3D', 'avions, satellite et station faits par ID Studio IA avec Meshy.'],
  ] as [string, string][],

  actu: {
    icone: 'Actualité du monde : ce qui se passe dans les pays (par Internet)',
    titre: 'Actualité du monde',
    aide: "Un point violet = un pays dont on parle aujourd'hui ; plus il est gros, plus il y a d'articles. Cliquez sur un point ou sur un pays de la liste.",
    lire: "Lire l'article ↗",
    video: '▶ Voir la vidéo ↗',
    theme: 'Thème',
    themes: { tout: 'Tout', actualite: 'Actualité', economie: 'Économie', sport: 'Sport', culture: 'Culture', science: 'Science', technologie: 'Technologie', voyage: 'Voyage' } as Record<ThemeChoisi, string>,
    aucun: 'Aucun article placé sur un pays pour ce thème en ce moment.',
    derniers: 'Les derniers articles du thème',
    articles: (n: number) => pluriel(n, 'article'),
    bilan: (a: number, p: number) => `${a} articles, ${p} pays`,
    imagesPrive: "Images des articles non chargées : le mode d'AURORA n'est pas encore connu.",
    // 11/11/2026 : « Lis-le-moi » et « Explique-le », d'après Athéna payante (ID Studio IA : « un système de lecture des
    // articles plus abouti »). Le texte est lu sur la page du journal ; l'explication vient du modèle local.
    lisLeMoi: 'Lis-le-moi',
    expliqueLe: 'Explique-le',
    arreter: '■ Arrêter',
    aideLire: 'AURORA lit le texte sur la page du journal et le dit à voix haute',
    aideExpliquer: "Le modèle local explique l'article en trois phrases courtes",
    lecturePage: 'AURORA lit la page du journal…',
    explicationEnCours: "Le modèle local lit l'article et l'explique…",
    partiel: 'Article réservé aux abonnés : seul le début est lisible.',
    explication: (modele: string) => `Explication d'AURORA (modèle local${modele ? ` ${modele}` : ''}), d'après le texte de l'article :`,
    echec: "L'article n'a pas pu être lu : le service d'AURORA n'a pas répondu.",
  },

  guide: {
    icone: 'Guide de voyage : lieux à visiter et pays (photos par Internet)',
    titre: 'Guide de voyage',
    aide: 'Zoomez sur une région : les lieux du patrimoine mondial apparaissent en photos. Cliquez sur une photo pour sa fiche, ou sur une zone du globe pour le pays.',
    lieux: (n: number) => `${nombre(n)} lieux du patrimoine mondial de l'UNESCO, sur ce PC`,
    source: 'Lieux et pays : Wikidata (domaine public) · photos : Wikimedia Commons · textes : Wikipédia et Wikivoyage (CC BY-SA)',
    sansPhotos: "Photos de Wikimedia Commons non chargées (Mode Privé, ou mode d'AURORA pas encore connu).",
    pays: 'Le pays',
    capitale: 'Capitale',
    population: 'Population',
    monnaie: 'Monnaie',
    langues: 'Langues officielles',
    lireWikipedia: 'Lire sur Wikipédia',
    lireWikivoyage: 'Le guide sur Wikivoyage',
    photo: 'Photo : Wikimedia Commons (auteur et licence)',
  },

  dire: {
    ouverture: (avions: string | null, sats: number | null, direct: boolean) => !direct
      ? 'Voici la Terre. Activez le direct pour voir les avions, les satellites et la météo.'
      : `Voici la Terre en direct${avions != null ? ` : ${avions} avions en vol en ce moment` : ''}${sats ? `, et ${sats} satellites suivis` : ''}. Cliquez sur une zone ou sur un point rouge, je vous dirai ce qui s'y passe.`,
    ouverturePrive: "Voici la Terre, enregistrée sur ce PC. Mode Privé : aucune source en ligne n'est appelée.",
    avion: (v: string, ty: string, al: string | null, vi: string | null, di: string | null) =>
      `Vol ${v}${ty ? `, un ${ty}` : ''}${al ? `, à ${al} mètres d'altitude` : ''}${vi ? `, ${vi} kilomètres heure` : ''}${di ? `, cap vers ${di}` : ''}.`,
    satellite: (n: string, r: string, al: string) => `${r ? `${r}. ` : ''}${n} vole à ${al} kilomètres d'altitude.`,
    evenement: (ty: string, m: string | null, al: string | null, c: string | null, km: string | null, il: string, p: string | null) =>
      `${ty}${m ? ` de magnitude ${m}` : ''}${al ? `, alerte ${al}` : ''}${c ? `, à ${km} kilomètres de ${c}` : ''}, ${il}${p ? `, à ${p} de profondeur` : ''}.`,
    zone: (a: string, c: string | null, km: string | null, te: string | null, ci: string | null) =>
      `Dans cette zone${c ? `, à ${km} kilomètres de ${c}` : ''} : ${a === '0' ? 'aucun avion en vol mesuré' : `${a} avion${a === '1' ? '' : 's'} en vol`}${te ? `. Il fait ${te} degrés${ci ? `, ${ci}` : ''}` : ''}.`,
    actu: (p: string, n: number, titre: string) => `${p} : ${pluriel(n, 'article')}. À la une : ${titre}`,
    guide: (n: string, p: string | null, d: string | null) => `${n}${p ? `, ${p}` : ''}${d ? ` : ${d}` : ''}. Un lieu inscrit au patrimoine mondial de l'UNESCO.`,
  },
};
