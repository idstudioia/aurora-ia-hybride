// Globe hologramme de la page Monde 3D, porté d'Athéna (client/src/components/HologrammeTerre.tsx, 04/10 → 12/10/2026).
// La Terre en points sur la Blue Marble, l'image NASA du jour, les avions (modèles 3D, position prolongée par la
// vitesse et le cap), le trafic mondial, les satellites, le vent, la pluie, les cartes colorées, les catastrophes,
// l'actualité par pays, la carte routière et le guide de voyage ; AURORA commente à voix haute ce qu'on choisit,
// avec des phrases construites à partir des données (textes.ts).
// Le direct est éteint par défaut, comme chez Athéna : rien ne part sur Internet tant qu'on ne l'a pas demandé.
// En Mode Privé, aucune source en ligne n'est appelée (le service répond private_mode) et le navigateur ne charge
// aucune image externe : seule la Terre enregistrée sur ce PC est montrée.
// Nouveau dans AURORA : `commande` (le lieu et la couche demandés à AURORA) et `recherche` (l'animation de recherche).
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { CloudLightning, Compass, Map as IconeCarte, Newspaper, Plane, Radio, Volume2, VolumeX } from 'lucide-react';
import type { GlobeAirData, GlobeAircraftData, GlobeArticle, GlobeArticleMode, GlobeCommand, GlobeGuideData, GlobeWeatherData, GlobeWindData } from '../../lib/types';
import { api } from '../../lib/api';
import { useAurora } from '../../lib/store';
import { isSpeaking, speak, speechGeneration, stopSpeaking, useSpeaking } from '../../lib/voice';
import { getPrefs } from '../chat/prefs';
import { PAGER_LEVELS } from '../globe/data';
import { angularDistanceDeg } from '../globe/geo';
import { useElementSize, useReducedMotion, useToolResult, type ToolState } from '../globe/hooks';
import {
  etatDe, interroger, normActualites, normAir, normAvions, normEvenements, normMeteo, normMonde, normResume, normSatellites, normVent, type Etat,
} from './donnees';
import {
  ECHELLES, IMAGE_LOCALE, MODES_CARTE, THEMES_ACTU, capitaleProche, chargerLocal, ecrireChoix, ecrireOui, lireChoix, lireOui, sansParentheses, trouverLieu,
  type Capitale, type Choix, type ModeCarte, type PaysGuide, type PointActu, type SiteGuide, type ThemeChoisi,
} from './outils';
import Panneau, { type DonneesPanneau, type LectureArticle } from './Panneau';
import { creerScene, type Pilote, type SceneGlobe } from './scene';
import { T, direction, ilYaParle, nombre } from './textes';
import './hologram.css';

type Point = { lat: number; lon: number };
type Focus = Point & { nom?: string };

/** L'id de la dernière commande appliquée : retenu hors du composant, pour ne pas la rejouer à chaque visite de la page. */
let derniereCommande: number | null = null;

/** Les couches qui passent par Internet : les demander allume le direct. */
const COUCHES_EN_LIGNE = ['meteo', 'evenements', 'actualites', 'avions', 'satellites'];
/** La section du panneau à montrer pour chaque couche demandée. */
const SECTION_DE: Record<string, string> = { meteo: 'meteo', evenements: 'meteo', actualites: 'actu', avions: 'volants', satellites: 'satellites', guide: 'guide', routes: 'routes' };

/** L'état affiché d'une couche relue par useToolResult (recalculé seulement quand la réponse change). */
function useEtat<D>(s: ToolState, active: boolean, normaliser: (d: unknown) => D | null): Etat<D> | null {
  const { result, error } = s;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => etatDe(s, active, normaliser), [result, error, active, normaliser]);
}

function Icone({ actif, titre, bloque = false, aide, onClick, children }: { actif: boolean; titre: string; bloque?: boolean; aide?: string; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} className={`${actif ? 'actif' : ''}${bloque ? ' bloque' : ''}`}
      title={bloque ? `${titre} — ${T.prive}` : aide ?? titre} aria-label={titre} aria-pressed={actif}>{children}</button>
  );
}

export default function HologramGlobe({ commande = null, recherche = false, direct: directVoulu, onDirect }: {
  commande?: GlobeCommand | null; recherche?: boolean;
  /** 24/11/2026 : la page peut tenir « le direct » elle-même (son en-tête porte l'interrupteur) ; sinon le globe le tient. */
  direct?: boolean; onDirect?: (on: boolean) => void;
}) {
  const { settings, health } = useAurora();
  const canvasRef = useRef<HTMLCanvasElement>(null), sceneRef = useRef<HTMLDivElement>(null), etiquettesRef = useRef<HTMLDivElement>(null);
  const iconesRef = useRef<HTMLDivElement>(null), panneauRef = useRef<HTMLElement>(null);
  const pilote = useRef<Pilote | null>(null);
  const [pret, setPret] = useState(false); // la scène three.js est construite (chargement asynchrone)
  const [erreur, setErreur] = useState<'webgl' | 'three' | null>(null);
  const taille = useElementSize(sceneRef);
  const reduit = useReducedMotion();

  // Préférences retenues dans le navigateur (aurora.globe.*).
  const [directLocal, setDirectLocal] = useState(() => lireOui('aurora.globe.direct'));
  const direct = directVoulu ?? directLocal;
  const setDirect = (on: boolean) => { setDirectLocal(on); onDirect?.(on); };
  const [meteoOn, setMeteoOn] = useState(() => lireOui('aurora.globe.meteo'));
  const [volantsOn, setVolantsOn] = useState(() => lireOui('aurora.globe.volants', true));
  const [routesOn, setRoutesOn] = useState(() => lireOui('aurora.globe.routes'));
  const [guideOn, setGuideOn] = useState(() => lireOui('aurora.globe.guide'));
  const [actuOn, setActuOn] = useState(() => lireOui('aurora.globe.actu'));
  const [commenter, setCommenter] = useState(() => lireOui('aurora.globe.voix', true));
  const [themeActu, setThemeActu] = useState<ThemeChoisi>(() => lireChoix('aurora.globe.actu.theme', THEMES_ACTU, 'tout'));
  const [modeCarte, setModeCarte] = useState<ModeCarte>(() => lireChoix('aurora.globe.carte', MODES_CARTE, 'aucune'));

  const [choix, setChoix] = useState<Choix | null>(null);
  const [zone, setZone] = useState<Point | null>(null); // point cliqué ; aucun au départ
  const [focus, setFocus] = useState<Focus | null>(null); // lieu demandé à AURORA
  const [message, setMessage] = useState<string | null>(null);
  const [paysActu, setPaysActu] = useState<string | null>(null);
  const [siteChoisi, setSiteChoisi] = useState<number | null>(null);
  const [satDemande, setSatDemande] = useState<number | null>(null);
  const [nasaDate, setNasaDate] = useState<string | null>(null);
  const [nasaSatellite, setNasaSatellite] = useState<string | null>(null);   // 25/11/2026 : celui qui a pris l'image du jour
  const [avions, setAvions] = useState<Etat<GlobeAircraftData> | null>(null);
  const [meteo, setMeteo] = useState<Etat<GlobeWeatherData> | null>(null);
  const [capitales, setCapitales] = useState<Capitale[]>([]);
  const [guide, setGuide] = useState<{ sites: SiteGuide[]; pays: PaysGuide[] } | null>(null);
  const [resumeSite, setResumeSite] = useState<Etat<GlobeGuideData> | null>(null);
  const [resumePays, setResumePays] = useState<Etat<GlobeGuideData> | null>(null);
  const [sonde, setSonde] = useState<{ x: number; y: number; texte: string } | null>(null); // valeur sous la souris
  const [priveVu, setPriveVu] = useState(false);

  // ── Le mode d'AURORA. Inconnu (service pas encore lu) : pas d'image externe dans le navigateur.
  const mode = settings?.mode ?? health?.capabilities?.mode ?? null;
  useEffect(() => { setPriveVu(false); }, [mode]);
  const prive = mode === 'prive' || priveVu;
  const enLigne = direct && !prive;
  const photosPermises = mode !== null && !prive;

  // ── Les couches relues à intervalle (le service garde ses réponses autant de temps).
  const satsS = useToolResult(api.globeSatellites, 'satellites', { enabled: pret && enLigne && volantsOn, refreshMs: 30_000 });
  const mondeS = useToolResult(api.globeTraffic, 'monde', { enabled: pret && enLigne && volantsOn, refreshMs: 5 * 60_000 });
  const ventS = useToolResult(api.globeWind, 'vent', { enabled: pret && enLigne && meteoOn, refreshMs: 30 * 60_000 });
  const evS = useToolResult(api.globeEvents, 'evenements', { enabled: pret && enLigne && meteoOn, refreshMs: 10 * 60_000 });
  const airS = useToolResult(api.globeAir, 'air', { enabled: pret && enLigne && meteoOn && modeCarte === 'air', refreshMs: 60 * 60_000 });
  const actuS = useToolResult(api.globeNews, 'actualites', { enabled: pret && enLigne && actuOn, refreshMs: 15 * 60_000 });
  const sats = useEtat(satsS, enLigne && volantsOn, normSatellites);
  const monde = useEtat(mondeS, enLigne && volantsOn, normMonde);
  const vent = useEtat(ventS, enLigne && meteoOn, normVent);
  const evenements = useEtat(evS, enLigne && meteoOn, normEvenements);
  const air = useEtat(airS, enLigne && meteoOn && modeCarte === 'air', normAir);
  const actu = useEtat(actuS, enLigne && actuOn, normActualites);

  // Une réponse dit « Mode Privé » alors que le mode n'était pas encore connu : on s'arrête là.
  const priveDansReponses = [sats, monde, vent, evenements, air, actu, avions, meteo, resumeSite, resumePays].some(e => e && !e.ok && e.prive);
  useEffect(() => { if (priveDansReponses) setPriveVu(true); }, [priveDansReponses]);

  // ── La voix : une phrase par choix, construite avec les valeurs mesurées. Jamais par-dessus AURORA.
  // La lecture en cours est celle du globe tant que son numéro (speechGeneration) n'a pas changé : si AURORA a coupé
  // la phrase du globe pour lire sa réponse, le globe le sait sans attendre onEnd (qui n'arrive pas dans ce cas).
  const maLecture = useRef<number | null>(null);
  const commenterRef = useRef(commenter);
  useEffect(() => { commenterRef.current = commenter; }, [commenter]);
  const globeParle = () => maLecture.current !== null && isSpeaking() && speechGeneration() === maLecture.current;
  const dire = useCallback((texte: string | null) => {
    if (!commenterRef.current || !texte) return;
    if (isSpeaking() && !globeParle()) return; // AURORA lit sa réponse : le globe ne la coupe pas
    maLecture.current = speak(texte, { voiceName: getPrefs().voiceName }) ? speechGeneration() : null;
  }, []);
  const taire = useCallback(() => { if (globeParle()) stopSpeaking(); maLecture.current = null; }, []);
  useEffect(() => () => taire(), [taire]);

  // ── « Lis-le-moi » et « Explique-le » sous un article (11/11/2026, ID Studio IA : « regarde Athéna la version payante, on a
  // un système de lecture des articles plus abouti »). Le service lit le texte sur la page du journal, ou le fait
  // expliquer par le modèle local. Un article à la fois : le dernier clic l'emporte. Le clic est une demande : la
  // voix lit même si le commentaire du globe est coupé ; « Arrêter », un autre choix ou une question la coupent.
  const [lecture, setLecture] = useState<LectureArticle | null>(null);
  const demandeArticle = useRef(0);
  const voixActive = useSpeaking();
  const lireArticle = useCallback((a: GlobeArticle, mode: GlobeArticleMode) => {
    const n = ++demandeArticle.current;
    taire();
    setLecture({ lien: a.lien, mode, etat: 'attente' });
    api.globeArticle(a.lien, mode).then(r => {
      if (n !== demandeArticle.current) return;
      const d = r.data;
      if (!r.ok || !d?.texte) {
        setLecture({ lien: a.lien, mode, etat: 'refus', message: r.summary || r.error || T.actu.echec });
        return;
      }
      setLecture({ lien: a.lien, mode, etat: 'pret', texte: d.texte, partiel: !!d.partiel, modele: d.modele });
      const aDire = mode === 'lire' ? `${d.titre ?? a.titre}. ${d.texte}` : d.texte;
      maLecture.current = speak(aDire, { voiceName: getPrefs().voiceName }) ? speechGeneration() : null;
    }).catch(() => {
      if (n === demandeArticle.current) setLecture({ lien: a.lien, mode, etat: 'refus', message: T.actu.echec });
    });
  }, [taire]);
  const arreterArticle = useCallback(() => {
    demandeArticle.current++;
    taire();
    setLecture(l => (l?.etat === 'attente' ? null : l));
  }, [taire]);
  // Autre pays, autre thème, couche ou direct coupés, page quittée : l'article n'est plus à l'écran, son bouton
  // « Arrêter » non plus. La voix se tait avec lui, et une réponse encore attendue ne sera pas lue.
  useEffect(() => () => { demandeArticle.current++; taire(); setLecture(null); }, [paysActu, themeActu, actuOn, direct, taire]);
  // « Arrêter » ne se montre que tant que c'est bien cet article que la voix lit.
  const articleParle = lecture?.etat === 'pret' && voixActive && globeParle();

  // ── La scène three.js : construite une fois, pilotée ensuite par `pilote`. three.js arrive dans un morceau à part.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let annule = false, sg: SceneGlobe | null = null;
    import('three').then(THREE => {
      if (annule) return;
      sg = creerScene(THREE, canvas, () => etiquettesRef.current);
      if (!sg) { setErreur('webgl'); return; }
      pilote.current = sg.pilote; setPret(true);
    }).catch(() => { if (!annule) setErreur('three'); });
    return () => { annule = true; pilote.current = null; sg?.liberer(); setPret(false); };
  }, []);

  useEffect(() => {
    if (!pret) return;
    const ic = iconesRef.current;
    pilote.current?.redimensionner(taille.width, taille.height, { l: ic ? ic.offsetLeft + ic.offsetWidth + 6 : 0, h: ic ? ic.offsetTop + ic.offsetHeight + 6 : 0 });
  }, [pret, taille.width, taille.height]);
  useEffect(() => { if (pret) pilote.current?.mouvementReduit(reduit); }, [pret, reduit]);
  useEffect(() => { if (pret) pilote.current?.recherche(recherche); }, [pret, recherche]);
  useEffect(() => { if (pret) pilote.current?.couches({ meteo: meteoOn, volants: volantsOn, routes: routesOn }); }, [pret, meteoOn, volantsOn, routesOn]);
  // La carte détaillée passe par Internet : direct activé seulement, comme l'image NASA du jour.
  useEffect(() => { if (pret) pilote.current?.detail(enLigne); }, [pret, enLigne]);
  useEffect(() => { if (pret) pilote.current?.surligner(choix); }, [pret, choix]);
  useEffect(() => { if (pret) pilote.current?.photos(photosPermises); }, [pret, photosPermises]);

  // ── Molette : écouteur natif, pour pouvoir empêcher la page de défiler pendant le zoom.
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const roue = (e: WheelEvent) => { e.preventDefault(); pilote.current?.zoom(e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY); };
    c.addEventListener('wheel', roue, { passive: false });
    return () => c.removeEventListener('wheel', roue);
  }, []);

  // ── Échap : désélectionner, et faire taire le globe (pas la réponse d'AURORA).
  useEffect(() => {
    const touche = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      setChoix(null); setPaysActu(null); setSiteChoisi(null); taire();
    };
    window.addEventListener('keydown', touche);
    return () => window.removeEventListener('keydown', touche);
  }, [taire]);

  // ── Satellites : le service les calcule en local ; il ne va sur Internet que pour les orbites (CelesTrak).
  useEffect(() => {
    if (!pret) return;
    pilote.current?.satellites(sats?.ok ? sats.data.liste : []);
    // Le satellite choisi suit sa nouvelle position (sinon la surbrillance resterait sur l'ancienne).
    if (sats?.ok) setChoix(c => c?.genre === 'sat' ? { genre: 'sat', s: sats.data.liste.find(x => x.norad === c.s.norad) ?? c.s } : c);
  }, [pret, sats]);
  useEffect(() => { if (pret) pilote.current?.monde(monde?.ok ? monde.data : null); }, [pret, monde]);
  useEffect(() => { if (pret) pilote.current?.vent(vent?.ok ? vent.data : null); }, [pret, vent]);
  useEffect(() => { if (pret) pilote.current?.evenements(evenements?.ok ? evenements.data.evenements : []); }, [pret, evenements]);
  // ── La carte colorée suit le mode choisi et les grilles reçues.
  useEffect(() => {
    if (!pret) return;
    const g = modeCarte === 'air' ? (air?.ok ? air.data : null) : (vent?.ok ? vent.data : null);
    const valeurs = !g ? undefined : modeCarte === 'air' ? (g as GlobeAirData).indice
      : modeCarte === 'temperature' ? (g as GlobeWindData).temperature : modeCarte === 'humidite' ? (g as GlobeWindData).humidite : undefined;
    pilote.current?.carte(meteoOn && g && valeurs ? { mode: modeCarte, pas: g.pas, lat0: g.lat0, lon0: g.lon0, nLat: g.nLat, nLon: g.nLon, valeurs } : null);
  }, [pret, meteoOn, modeCarte, vent, air]);

  // ── Image NASA du jour : direct seulement. Lue avec le jeton, puis posée par une URL d'objet (rendue après lecture).
  useEffect(() => {
    if (!pret) return;
    if (!enLigne) { setNasaDate(null); setChoix(null); setZone(null); pilote.current?.texture(IMAGE_LOCALE); return; }
    // 25/11/2026, ID Studio IA : « désactive comme avant les nuages si la météo est désactivée ». L'image du jour (et
    // ses nuages) ne se pose qu'avec la couche météo ; sinon, la Blue Marble du PC, sans nuages.
    if (!meteoOn) { setNasaDate(null); pilote.current?.texture(IMAGE_LOCALE); return; }
    const ctrl = new AbortController();
    api.globeImage('nasa', undefined, ctrl.signal).then(rep => {
      if (ctrl.signal.aborted) return;
      if (!rep.ok) { if (rep.private_mode) setPriveVu(true); return; } // le panneau dit « Blue Marble (sur ce PC) »
      const p = pilote.current;
      if (!p) return;
      p.texture(URL.createObjectURL(rep.blob), true); setNasaDate(rep.date); setNasaSatellite(rep.satellite ?? null);
    }).catch(() => { /* reste sur l'image du PC, et le panneau le dit */ });
    return () => ctrl.abort();
  }, [pret, enLigne, meteoOn]);

  // ── La zone cliquée (ou le lieu demandé) : son trafic détaillé, relu toutes les 30 s, et sa météo.
  useEffect(() => {
    if (!pret || !enLigne || !zone) { setAvions(null); setMeteo(null); pilote.current?.avions(null, [], 0); return; }
    let fini = false;
    setAvions(null); setMeteo(null);
    const lireAvions = () => {
      if (!volantsOn) { setAvions(null); pilote.current?.avions(null, [], 0); return; }
      void interroger(() => api.globeAircraft(zone.lat, zone.lon), normAvions).then(e => {
        if (fini) return;
        setAvions(e);
        pilote.current?.avions(e.ok ? e.data.lieu : null, e.ok ? e.data.liste : [], e.ok ? e.data.demiCote ?? 0 : 0);
      });
    };
    void interroger(() => api.globeWeather(zone.lat, zone.lon), normMeteo).then(e => { if (!fini) setMeteo(e); });
    lireAvions();
    const a = window.setInterval(() => { if (document.visibilityState === 'visible') lireAvions(); }, 30_000);
    return () => { fini = true; window.clearInterval(a); };
  }, [pret, enLigne, zone, volantsOn]);

  useEffect(() => {
    let fini = false;
    chargerLocal<{ capitales: Capitale[] }>('/terre/capitales.json')
      .then(d => { if (!fini && Array.isArray(d?.capitales)) setCapitales(d.capitales); }).catch(() => { /* pas de capitales */ });
    return () => { fini = true; };
  }, []);

  // ── Guide de voyage : les lieux et les pays sont sur le PC (chargés à la première activation).
  useEffect(() => {
    if (!guideOn || guide) return;
    let fini = false;
    Promise.all([chargerLocal<{ sites: SiteGuide[] }>('/terre/guide-sites.json'), chargerLocal<{ pays: PaysGuide[] }>('/terre/guide-pays.json')])
      .then(([s, p]) => { if (!fini && Array.isArray(s?.sites) && Array.isArray(p?.pays)) setGuide({ sites: s.sites, pays: p.pays }); })
      .catch(() => { /* fichiers absents : le panneau garde son aide */ });
    return () => { fini = true; };
  }, [guideOn, guide]);
  useEffect(() => { if (pret) pilote.current?.guide(guideOn && guide ? guide.sites : null, i => { setChoix(null); setSiteChoisi(i); }); }, [pret, guideOn, guide]);
  const lieuGuide = guideOn && guide && siteChoisi != null ? guide.sites[siteChoisi] ?? null : null;
  /** Le pays affiché : celui du lieu choisi, sinon celui de la zone cliquée (sa capitale est la plus proche). */
  const paysGuide = useMemo(() => {
    if (!guideOn || !guide) return null;
    if (lieuGuide?.p) return guide.pays.find(x => x.n === lieuGuide.p) ?? null;
    if (!zone) return null;
    let m: PaysGuide | null = null, dm = Infinity;
    for (const x of guide.pays) if (x.la != null && x.lo != null) { const d = angularDistanceDeg(zone, { lat: x.la, lon: x.lo }); if (d < dm) { dm = d; m = x; } }
    return m;
  }, [guideOn, guide, lieuGuide, zone]);
  // Les résumés : Wikipédia pour le lieu, Wikivoyage pour le pays (le service les garde 24 h). Pas en Mode Privé.
  useEffect(() => {
    setResumeSite(null);
    const titre = lieuGuide?.wp;
    if (!titre) return;
    if (prive) { setResumeSite({ ok: false, prive: true, raison: T.prive }); return; }
    let fini = false;
    void interroger(() => api.globeGuide('wikipedia', titre), normResume).then(e => { if (!fini) setResumeSite(e); });
    return () => { fini = true; };
  }, [lieuGuide?.wp, prive]);
  useEffect(() => {
    setResumePays(null);
    const titre = paysGuide?.wv;
    if (!titre) return;
    if (prive) { setResumePays({ ok: false, prive: true, raison: T.prive }); return; }
    let fini = false;
    void interroger(() => api.globeGuide('wikivoyage', titre), normResume).then(e => { if (!fini) setResumePays(e); });
    return () => { fini = true; };
  }, [paysGuide?.wv, prive]);

  // ── L'actualité : points violets par pays, pour le thème choisi.
  const articlesTheme = useMemo<GlobeArticle[]>(
    () => !actu?.ok ? [] : themeActu === 'tout' ? actu.data.articles : actu.data.articles.filter(a => a.theme === themeActu), [actu, themeActu]);
  const pointsTheme = useMemo<PointActu[]>(() => {
    const m = new Map<string, PointActu>();
    for (const a of articlesTheme) {
      if (!a.pays || a.lat == null || a.lon == null || !Number.isFinite(a.lat) || !Number.isFinite(a.lon)) continue;
      const p = m.get(a.pays) ?? { pays: a.pays, lat: a.lat, lon: a.lon, n: 0 };
      p.n++; m.set(a.pays, p);
    }
    return [...m.values()].sort((a, b) => b.n - a.n);
  }, [articlesTheme]);
  const articlesPays = useMemo(() => paysActu ? articlesTheme.filter(a => a.pays === paysActu) : [], [paysActu, articlesTheme]);
  useEffect(() => { if (pret) pilote.current?.actualites(enLigne && actuOn && actu?.ok ? pointsTheme : null); }, [pret, pointsTheme, enLigne, actuOn, actu]);

  // ── Ce qu'AURORA dit (une fois par élément choisi : Athéna, 06/10, « elle répète les phrases »).
  const cleChoix = !choix ? null : choix.genre === 'avion' ? `avion:${choix.a.id}` : choix.genre === 'sat' ? `sat:${choix.s.norad}` : `ev:${choix.e.lat},${choix.e.lon},${choix.e.date}`;
  const choixDit = useRef<string | null>(null);
  useEffect(() => {
    if (!choix || !cleChoix) { choixDit.current = null; return; }
    if (choixDit.current === cleChoix) return;
    choixDit.current = cleChoix;
    if (choix.genre === 'avion') {
      const a = choix.a;
      dire(T.dire.avion(a.indicatif || a.id, a.type, a.altitude != null ? nombre(a.altitude) : null, a.vitesse != null ? nombre(a.vitesse) : null, direction(a.cap)));
    } else if (choix.genre === 'sat') {
      dire(T.dire.satellite(sansParentheses(choix.s.nom), choix.s.role, nombre(choix.s.altitude)));
    } else {
      const e = choix.e, pc = capitaleProche(capitales, e), info = (k: string) => e.infos?.find(x => x.cle === k)?.valeur;
      const mag = parseFloat(info('magnitude') ?? ''), alerte = info('alerte');
      dire(T.dire.evenement(T.typeUn[e.type] ?? e.type, Number.isFinite(mag) ? nombre(mag, 1) : null, alerte ? PAGER_LEVELS[alerte.toLowerCase()] ?? alerte : null,
        pc ? pc.nom : null, pc ? nombre(pc.km) : null, ilYaParle(e.date), info('profondeur') ?? null));
    }
  }, [choix, cleChoix, capitales, dire]);
  // Le détail s'affiche en haut du panneau : y remonter quand on choisit autre chose.
  useEffect(() => { if (cleChoix && panneauRef.current) panneauRef.current.scrollTop = 0; }, [cleChoix]);
  useEffect(() => { if (lieuGuide) dire(T.dire.guide(lieuGuide.n, lieuGuide.p ?? null, lieuGuide.d ?? null)); }, [lieuGuide, dire]);
  // La zone : une fois par zone, quand le trafic est arrivé (et la météo si elle est là).
  const zoneDite = useRef('');
  useEffect(() => {
    if (!zone || !avions?.ok) return;
    const cle = `${zone.lat},${zone.lon}`;
    if (zoneDite.current === cle) return;
    zoneDite.current = cle;
    const pc = capitaleProche(capitales, zone);
    dire(T.dire.zone(nombre(avions.data.total), pc ? pc.nom : null, pc ? nombre(pc.km) : null,
      meteo?.ok ? nombre(meteo.data.temperature) : null, meteo?.ok ? meteo.data.ciel : null));
  }, [zone, avions, meteo, capitales, dire]);
  // Le pays choisi dans l'actualité : le nombre d'articles et le premier titre, tels que le média l'écrit.
  const actuDite = useRef('');
  useEffect(() => {
    if (!paysActu) { actuDite.current = ''; return; }
    if (!articlesPays.length) return;
    const cle = `${paysActu}:${actu?.ok ? actu.data.mesureA : ''}`;
    if (actuDite.current === cle) return;
    actuDite.current = cle;
    dire(T.dire.actu(paysActu, articlesPays.length, articlesPays[0].titre));
  }, [paysActu, articlesPays, actu, dire]);
  // L'ouverture, une fois par visite : en Mode Privé tout de suite, sinon quand le trafic et les satellites ont répondu.
  const ouvertureDite = useRef(false);
  useEffect(() => {
    if (!pret || ouvertureDite.current) return;
    if (prive) { ouvertureDite.current = true; dire(T.dire.ouverturePrive); return; }
    if (!direct) { ouvertureDite.current = true; dire(T.dire.ouverture(null, null, false)); return; }
    if (volantsOn && (!monde || !sats)) return;
    ouvertureDite.current = true;
    dire(T.dire.ouverture(volantsOn && monde?.ok ? nombre(monde.data.total) : null, volantsOn && sats?.ok ? sats.data.liste.length : null, true));
  }, [pret, prive, direct, volantsOn, monde, sats, dire]);

  // ── Les commandes d'AURORA : à chaque nouvel id, ouvrir la couche et sa section, puis aller au lieu en douceur.
  const sectionVisee = useRef<string | null>(null);
  useEffect(() => {
    const p = pilote.current;
    if (!pret || !p || !commande || commande.id === derniereCommande) return;
    derniereCommande = commande.id;
    const c = commande, couche = c.couche;
    setMessage(null);
    if (couche === 'meteo' || couche === 'evenements') { setMeteoOn(true); ecrireOui('aurora.globe.meteo', true); }
    if (couche === 'actualites') { setActuOn(true); ecrireOui('aurora.globe.actu', true); }
    if (couche === 'avions' || couche === 'satellites') { setVolantsOn(true); ecrireOui('aurora.globe.volants', true); }
    if (couche === 'guide') { setGuideOn(true); ecrireOui('aurora.globe.guide', true); }
    if (couche === 'routes') { setRoutesOn(true); ecrireOui('aurora.globe.routes', true); }
    if (couche && COUCHES_EN_LIGNE.includes(couche)) { setDirect(true); ecrireOui('aurora.globe.direct', true); }
    if (c.theme && (THEMES_ACTU as string[]).includes(c.theme)) { setThemeActu(c.theme as ThemeChoisi); ecrireChoix('aurora.globe.actu.theme', c.theme); setPaysActu(null); }
    if (c.pays && (couche === 'actualites' || c.theme)) setPaysActu(c.pays);
    if (c.satellite != null && Number.isFinite(c.satellite)) setSatDemande(c.satellite);
    const allerVers = (f: Focus, distance?: number) => {
      p.allerA(f.lat, f.lon, distance); p.repere(f);
      setFocus(f); setZone({ lat: f.lat, lon: f.lon }); setChoix(null); setSiteChoisi(null);
    };
    if (c.focus && Number.isFinite(c.focus.lat) && Number.isFinite(c.focus.lon)) {
      allerVers({ lat: c.focus.lat, lon: c.focus.lon, nom: c.focus.nom }, couche === 'avions' ? 1.9 : undefined);
    } else if (c.pays || c.lieu) {
      // Un pays ou un lieu nommé, sans coordonnées : sa position vient des listes du PC (trouverLieu), sans réseau.
      const nom = (c.pays || c.lieu)!;
      void trouverLieu(nom).then(f => {
        if (pilote.current !== p) return;
        if (f) allerVers(f, couche === 'avions' ? 1.9 : undefined);
        else if (c.lieu) setMessage(T.lieuAbsent(c.lieu));
      });
    }
    const auLieu = !!(c.focus || c.lieu);
    sectionVisee.current = couche === 'avions' && auLieu ? 'zone' : couche ? SECTION_DE[couche] ?? null : auLieu ? 'zone' : null;
  }, [pret, commande]);
  // Le satellite demandé, dès que la liste est là.
  useEffect(() => {
    if (satDemande == null) return;
    if (prive) { setSatDemande(null); return; }
    if (!sats) return;
    if (sats.ok) {
      const s = sats.data.liste.find(x => x.norad === satDemande);
      if (s) { setChoix({ genre: 'sat', s }); pilote.current?.allerA(s.lat, s.lon, 2.6); }
      else setMessage(T.satelliteAbsent(satDemande));
    }
    setSatDemande(null);
  }, [satDemande, sats, prive]);
  // La section visée défile dans le panneau (le panneau seulement, pas la page).
  useEffect(() => {
    const s = sectionVisee.current, panneau = panneauRef.current;
    if (!s || !panneau) return;
    const el = panneau.querySelector<HTMLElement>(`[data-section="${s}"]`) ?? panneau.querySelector<HTMLElement>('[data-section="prive"], [data-section="direct"]');
    if (!el) return;
    sectionVisee.current = null;
    const haut = el.getBoundingClientRect().top - panneau.getBoundingClientRect().top + panneau.scrollTop - 8;
    panneau.scrollTo({ top: Math.max(0, haut), behavior: reduit ? 'auto' : 'smooth' });
  });

  // ── Glisser, pincer (deux doigts), cliquer.
  const pointeurs = useRef(new Map<number, { x: number; y: number }>());
  const glisse = useRef<{ x: number; y: number; bouge: number } | null>(null);
  const pince = useRef<number | null>(null);
  const ecartDoigts = () => { const [a, b] = [...pointeurs.current.values()]; return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0; };
  const surAppui = (e: PointerEvent<HTMLCanvasElement>) => {
    if (!pret) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    pointeurs.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointeurs.current.size === 2) { pince.current = ecartDoigts(); glisse.current = null; }
    else if (pointeurs.current.size === 1) glisse.current = { x: e.clientX, y: e.clientY, bouge: 0 };
  };
  const surMouvement = (e: PointerEvent<HTMLCanvasElement>) => {
    const p = pilote.current;
    if (!p) return;
    const pt = pointeurs.current.get(e.pointerId);
    if (pt) { pt.x = e.clientX; pt.y = e.clientY; }
    if (pince.current != null && pointeurs.current.size >= 2) { const d = ecartDoigts(); p.zoom((pince.current - d) * 4); pince.current = d; return; }
    const g = glisse.current;
    if (!g) {
      if (meteoOn && modeCarte !== 'aucune' && e.pointerType === 'mouse') {
        const b = e.currentTarget.getBoundingClientRect(), x = e.clientX - b.left, y = e.clientY - b.top, v = p.valeurEn(x, y), unite = ECHELLES[modeCarte].unite;
        setSonde(v && Number.isFinite(v.valeur) ? { x, y, texte: `${modeCarte === 'air' ? `${T.indiceAir} ` : ''}${Math.round(v.valeur)}${unite ? ` ${unite}` : ''}` } : null);
      }
      return;
    }
    const dx = e.clientX - g.x, dy = e.clientY - g.y;
    g.bouge += Math.abs(dx) + Math.abs(dy); g.x = e.clientX; g.y = e.clientY;
    p.tourner(dx, dy);
  };
  const surRelache = (e: PointerEvent<HTMLCanvasElement>) => {
    pointeurs.current.delete(e.pointerId);
    if (pince.current != null) { if (pointeurs.current.size < 2) pince.current = null; glisse.current = null; return; }
    const g = glisse.current, p = pilote.current;
    glisse.current = null;
    if (!p || !g || g.bouge > 6 || e.type === 'pointercancel') return;
    const b = e.currentTarget.getBoundingClientRect(), x = e.clientX - b.left, y = e.clientY - b.top;
    const c = p.choisir(x, y);
    if (c) { setChoix(c); return; }
    const pa = p.choisirActu(x, y);
    if (pa) { setChoix(null); setPaysActu(pa); return; }
    const pt = p.pointSurGlobe(x, y);
    if (pt) { setChoix(null); setZone(pt); setSiteChoisi(null); setPaysActu(null); setFocus(null); p.repere(null); }
  };

  // ── Les 7 icônes. Les couches en ligne allument le direct (Athéna).
  const basculerDirect = (on: boolean) => { setDirect(on); ecrireOui('aurora.globe.direct', on); };
  const basculerMeteo = () => { const on = !meteoOn || !direct; setMeteoOn(on); ecrireOui('aurora.globe.meteo', on); if (on && !direct) basculerDirect(true); };
  const basculerVolants = () => { const on = !volantsOn || !direct; setVolantsOn(on); ecrireOui('aurora.globe.volants', on); if (on && !direct) basculerDirect(true); };
  // La carte routière et le guide n'utilisent pas Internet (sauf les photos et les résumés) : ils marchent direct coupé.
  const basculerRoutes = () => { const on = !routesOn; setRoutesOn(on); ecrireOui('aurora.globe.routes', on); };
  const basculerActu = () => { const on = !actuOn || !direct; setActuOn(on); ecrireOui('aurora.globe.actu', on); if (on && !direct) basculerDirect(true); if (!on) setPaysActu(null); };
  const basculerGuide = () => { const on = !guideOn; setGuideOn(on); ecrireOui('aurora.globe.guide', on); if (!on) setSiteChoisi(null); };
  const basculerVoix = () => { const on = !commenter; setCommenter(on); ecrireOui('aurora.globe.voix', on); if (!on) taire(); };
  const choisirCarte = (m: ModeCarte) => { setModeCarte(m); ecrireChoix('aurora.globe.carte', m); };
  const choisirTheme = (th: ThemeChoisi) => { setThemeActu(th); setPaysActu(null); ecrireChoix('aurora.globe.actu.theme', th); };
  const voirMonde = () => { setZone(null); setChoix(null); setFocus(null); setMessage(null); pilote.current?.voirMonde(); };

  const donnees: DonneesPanneau = { monde, sats, vent, air, evenements, actu, avions, meteo, resumeSite, resumePays };

  return (
    <div className="holo">
      <div ref={sceneRef} className="holo-scene">
        <canvas ref={canvasRef} className="holo-canvas" aria-hidden="true" title={T.aide}
          onPointerDown={surAppui} onPointerMove={surMouvement} onPointerUp={surRelache} onPointerCancel={surRelache} onPointerLeave={() => setSonde(null)} />
        <div ref={etiquettesRef} className="holo-etiquettes" />
        <div ref={iconesRef} className="holo-icones" role="toolbar" aria-label={T.barreIcones}>
          <Icone actif={meteoOn && direct} bloque={prive} titre={T.iconeMeteo} onClick={basculerMeteo}><CloudLightning size={18} /></Icone>
          <Icone actif={volantsOn && direct} bloque={prive} titre={T.iconeVolants} onClick={basculerVolants}><Plane size={18} /></Icone>
          <Icone actif={direct} bloque={prive} titre={T.iconeDirect} aide={direct ? T.desactiver : T.activer} onClick={() => basculerDirect(!direct)}><Radio size={18} /></Icone>
          <Icone actif={routesOn} titre={T.iconeRoutes} onClick={basculerRoutes}><IconeCarte size={18} /></Icone>
          <Icone actif={actuOn && direct} bloque={prive} titre={T.actu.icone} onClick={basculerActu}><Newspaper size={18} /></Icone>
          <Icone actif={guideOn} titre={T.guide.icone} onClick={basculerGuide}><Compass size={18} /></Icone>
          <Icone actif={commenter} titre={T.iconeVoix} onClick={basculerVoix}>{commenter ? <Volume2 size={18} /> : <VolumeX size={18} />}</Icone>
        </div>
        <div className="holo-annonce" aria-live="polite">{recherche && <span className="holo-recherche">{T.recherche}</span>}</div>
        {sonde && <div className="holo-sonde" style={{ left: Math.min(sonde.x + 14, Math.max(0, taille.width - 110)), top: sonde.y + 10 }}>{sonde.texte}</div>}
        {erreur && <div className="holo-erreur" role="alert">{erreur === 'webgl' ? T.sansWebgl : T.sansThree}</div>}
      </div>
      <Panneau
        panneauRef={panneauRef}
        d={donnees}
        etat={{ prive, direct, photos: photosPermises, nasaDate, nasaSatellite, message }}
        couches={{ meteo: meteoOn, volants: volantsOn, routes: routesOn, guide: guideOn, actu: actuOn }}
        choix={choix}
        capitales={capitales}
        zone={zone}
        focus={focus}
        modeCarte={modeCarte}
        actu={{ theme: themeActu, articles: articlesTheme, points: pointsTheme, pays: paysActu, articlesPays,
          lecture: lecture ? { ...lecture, parle: articleParle } : null }}
        guide={guide}
        lieuGuide={lieuGuide}
        paysGuide={paysGuide}
        actions={{ setChoix, choisirCarte, choisirTheme, setPaysActu, setSiteChoisi, voirMonde, basculerDirect, lireArticle, arreterArticle }}
      />
    </div>
  );
}
