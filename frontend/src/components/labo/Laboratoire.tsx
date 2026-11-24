// Le centre de l'accueil : le noyau holographique (noyau.ts), branché sur les vrais signaux de l'interface (08/11/2026).
// three.js et le moteur arrivent dans un morceau à part, chargé après l'accueil : la conversation n'attend jamais la 3D.
// Sans WebGL, ou si le moteur tombe en route, onSecours rend la place au globe (HomePage).
// Phase 3 : une scène du laboratoire (scene.ts) se pose sur le socle, à la demande d'AURORA (ui.scene → sceneCommand)
// ou des boutons ; elle garde son contexte entre deux commandes (options.scene : la scène et la pièce choisie).
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from 'react';
import { Boxes, Camera, Sparkles, X } from 'lucide-react';
import { Spinner } from '../ui';
import { api } from '../../lib/api';
import { useAurora } from '../../lib/store';
import type { RunResult, SceneDescription, SceneOrder, SceneStep } from '../../lib/types';
import { useVoiceActivity, voiceLevel } from '../../lib/voice';
import { useElementSize, useGestes3d, useReducedMotion } from '../globe/hooks';
import type { ContenuGlb } from '../hologram/glb';
import { ecrireOui, lireOui, vignettesCommons } from '../hologram/outils';
import type { ChoixForme } from './formes';
import type { EtatNoyau, PiloteNoyau, SceneNoyau } from './noyau';
import type { ModeleOrigine } from './origine';
import { DEBOUT, fichierOrigine, fichiersMaillage, maillages, modeleGarde, piecesVisees, quartsPoses, sceneAccueil, validerScene, VUE_DEPART, type PieceFichier, type VueScene } from './scene';
import CerveauFlottant from './CerveauFlottant';
import { BarreScene, EnteteScene, PanneauModeles, PanneauPiece, PanneauPieceFichier, PanneauScene } from './OutilsScene';
import './labo.css';

const ETIQUETTES: Record<EtatNoyau, string> = {
  repos: 'Au repos', ecoute: 'Écoute', parole: 'Parle', analyse: 'Analyse la demande', recherche: 'Recherche',
  memoire: 'Mémoire', action: 'Outil',
  generation: 'Construit la scène', exploration: 'Exploration', simulation: 'Simulation', erreur: 'Erreur',
};
const CLE_EFFETS = 'aurora.home.effets';
/** Le temps qu'une paire de photos du sujet reste affichée avant de laisser la place aux deux suivantes. */
const PERIODE_PHOTOS = 9000;
const DUREE_ERREUR = 6000;

/** L'id de la dernière commande appliquée, retenu hors du composant pour ne pas la rejouer à chaque visite de
 *  l'accueil. La scène ouverte l'est aussi (scene.ts : sceneAccueil), pour la retrouver en y revenant. */
let derniereCommande: number | null = null;

/** L'état du noyau, tiré des signaux réels : service joignable, dernière réponse, micro, recherche, demande en cours, voix.
 *  `enScene` : l'état de la scène affichée, à la place du repos. Le globe de l'accueil s'en sert aussi (ses sphères). */
export function useEtatNoyau(enScene: EtatNoyau | null): { etat: EtatNoyau; message: string } {
  const { busy, searching, remembering, acting, online, chat } = useAurora();
  const voix = useVoiceActivity();
  const dernier = chat[chat.length - 1];
  const texte = !dernier || dernier.pending ? ''
    : dernier.run?.status === 'error' ? (dernier.run.error || 'La demande a échoué.')
    : dernier.role === 'note' && dernier.text.startsWith('Erreur') ? dernier.text : '';
  const id = texte ? dernier!.id : '';
  // Une erreur se montre quelques secondes, puis le noyau revient au repos : elle ne bloque rien.
  const [erreur, setErreur] = useState('');
  useEffect(() => {
    if (!id) return;
    setErreur(texte);
    const t = window.setTimeout(() => setErreur(''), DUREE_ERREUR);
    return () => window.clearTimeout(t);
  }, [id, texte]);
  if (!online) return { etat: 'erreur', message: 'Service local hors ligne : le noyau attend la reconnexion.' };
  if (erreur) return { etat: 'erreur', message: erreur };
  if (voix === 'listening') return { etat: 'ecoute', message: '' };
  if (searching) return { etat: 'recherche', message: '' };
  // 11/11/2026 : un vrai pas de mémoire (recherche ou enregistrement), puis un vrai outil qui n'est pas une recherche.
  // Ces pas durent quelques millisecondes : le signal est tenu 2,5 s (lib/store.tsx) pour qu'on le voie.
  if (remembering) return { etat: 'memoire', message: '' };
  if (acting) return { etat: 'action', message: '' };
  // 11/11/2026 : la voix commence pendant que la réponse s'écrit encore ; le noyau montre alors qu'elle parle.
  if (voix === 'speaking') return { etat: 'parole', message: '' };
  if (busy) return { etat: 'analyse', message: '' };
  return { etat: enScene ?? 'repos', message: '' };
}

/** Les photos du sujet, de part et d'autre du noyau (noyau.ts) : celles de la dernière réponse terminée, deux à la fois
 *  (ui.lieux, normalize.place_photos), si le mode les permet (connu et pas Privé, comme la carte « En images » du chat).
 *  Une réponse sans lieu les retire : le sujet a changé ; `fermer` aussi, jusqu'à la réponse suivante. Les adresses que
 *  WebGL peut lire viennent de vignettesCommons ; une photo sans adresse laisse son côté vide (null). */
function usePhotosDuSujet() {
  const { chat, settings, health } = useAurora();
  const mode = settings?.mode ?? health?.capabilities?.mode ?? null;
  let run: RunResult | undefined;
  for (let i = chat.length - 1; i >= 0 && !run; i--) { const e = chat[i]; if (e.role === 'assistant' && !e.pending && e.run) run = e.run; }
  const lieux = mode !== null && mode !== 'prive' ? run?.ui?.lieux : undefined;
  const [fermee, setFermee] = useState<number | null>(null);
  const ouverte = !!lieux && !!run && run.task_id !== fermee;
  // Toutes les photos du sujet (six au plus : normalize.PHOTOS_MAX), plus seulement les deux premières (11/11/2026).
  const choisies = useMemo(() => (ouverte && Array.isArray(lieux?.photos) ? lieux.photos : [])
    .filter(p => p && typeof p.img === 'string' && p.img !== '' && Number.isFinite(p.la) && Number.isFinite(p.lo)), [ouverte, lieux]);
  const cle = choisies.map(p => p.img).join('|');
  const [vignettes, setVignettes] = useState<{ cle: string; urls: Map<string, string> } | null>(null);
  useEffect(() => {
    if (!cle) return;
    const ctrl = new AbortController();
    // 960 px de large (500 avant le 11/11/2026 : « un peu troubles ») : une taille que Commons garde toute prête.
    void vignettesCommons(cle.split('|'), 960, ctrl.signal).then(urls => { if (!ctrl.signal.aborted) setVignettes(urls?.size ? { cle, urls } : null); });
    return () => ctrl.abort();
  }, [cle]);
  // Les photos tournent (ID Studio IA, 11/11/2026 : « on peut faire tourner les images qu'on a en stock d'un sujet ») : deux à
  // la fois, à gauche et à droite du noyau, puis les deux suivantes toutes les 9 secondes quand il y en a plus de
  // deux. Le moteur fait le fondu (l'ancienne s'efface, la nouvelle se matérialise). Pas de tour en mouvement réduit.
  const reduit = useReducedMotion();
  const [tour, setTour] = useState(0);
  useEffect(() => { setTour(0); }, [cle]);
  const n = choisies.length, tourne = n > 2 && !reduit;
  useEffect(() => {
    if (!tourne) return;
    const t = window.setInterval(() => setTour(v => v + 1), PERIODE_PHOTOS);
    return () => window.clearInterval(t);
  }, [tourne, cle]);
  const rangs = n > 2 ? [(tour * 2) % n, (tour * 2 + 1) % n] : choisies.map((_, i) => i);
  const photos = cle && vignettes?.cle === cle
    ? rangs.map(i => { const p = choisies[i], url = vignettes.urls.get(p.img); return url ? { ...p, url } : null; }) : [];
  const idRun = run?.task_id ?? null;
  /** `rang` : « 3-4 / 6 » quand les photos tournent ; '' sinon. */
  const rang = tourne && photos.length ? `${rangs.map(i => i + 1).join('-')} / ${n}` : '';
  return { titre: lieux?.titre ?? '', photos, rang, fermer: () => setFermee(idRun) };
}

/** `cerveau` : le cerveau mémoire se pose en bas à gauche du centre (écran étroit) ; sur grand écran, il est dans la
 *  carte Mémoire (HomePage) et le centre ne le montre pas. */
/** `forme` : le choix fait sur le bouton « Noyau » de l'accueil (HomePage) — la forme suit le travail d'AURORA, le noyau
 *  seul, ou une forme tenue à la main. L'état écrit sous le noyau reste l'état réel, quelle que soit la forme. */
export default function Laboratoire({ onSecours, cerveau = true, forme = 'auto' }: { onSecours: (raison: string) => void; cerveau?: boolean; forme?: ChoixForme }) {
  const boite = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pilote = useRef<PiloteNoyau | null>(null);
  const secours = useRef(onSecours);
  useEffect(() => { secours.current = onSecours; }, [onSecours]);
  const [pret, setPret] = useState(false);
  const taille = useElementSize(boite);
  const reduit = useReducedMotion();
  const [effets, setEffets] = useState(() => lireOui(CLE_EFFETS, true));
  const { sceneCommand, setSceneContext, showOnGlobe } = useAurora();
  const sujet = usePhotosDuSujet();
  const photosVues = sujet.photos.filter(p => p !== null);

  // ── La scène affichée et ce qu'on y a changé.
  const [desc, setDesc] = useState<SceneDescription | null>(null);
  const descRef = useRef<SceneDescription | null>(null);
  const [chargement, setChargement] = useState(false);
  const [erreurScene, setErreurScene] = useState('');
  const [vue, setVue] = useState<VueScene>(VUE_DEPART);
  const [choisie, setChoisie] = useState<string | null>(null);
  const [infoScene, setInfoScene] = useState(false);
  // 11/11/2026 : la liste « Modèles 3D » (ouvrir ou retirer une scène gardée), à la place de la fiche d'une pièce.
  const [liste, setListe] = useState(false);
  // 12/11/2026 : la vue d'origine d'un modèle importé en GLB (origine.ts ; `vue.origine` dit si elle est demandée).
  // `contenuOrigine` : ce que son fichier contient, une fois lu. Le modèle lu reste en mémoire tant que sa scène est
  // ouverte, pour revenir à lui sans relire le fichier ; un seul à la fois, comme les formes des modèles importés.
  const [contenuOrigine, setContenuOrigine] = useState<ContenuGlb | null>(null);
  const [lectureOrigine, setLectureOrigine] = useState(false);
  const modeleOrigine = useRef<{ fichier: string; modele: ModeleOrigine } | null>(null);
  // 12/11/2026 (ID Studio IA : « la fiche au clic et la coque transparente ») : en vue d'origine, la pièce du fichier dont la
  // fiche est ouverte (le fichier la décrit lui-même) ; et si le fichier dit quelle est sa coque (pour les ordres).
  const [pieceFichier, setPieceFichier] = useState<PieceFichier | null>(null);
  const coqueOrigine = useRef(false);
  useEffect(() => { coqueOrigine.current = contenuOrigine?.coque === true; }, [contenuOrigine]);
  useEffect(() => { setPieceFichier(null); }, [desc, vue.origine]);
  const [bulle, setBulle] = useState<{ nom: string; x: number; y: number } | null>(null);
  // Une illustration animée n'est jamais présentée comme une simulation (cahier § 8).
  const enScene: EtatNoyau | null = chargement ? 'generation'
    : desc ? (vue.anime && desc.nature === 'simulation' ? 'simulation' : 'exploration') : null;
  const { etat, message } = useEtatNoyau(enScene);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let annule = false, sn: SceneNoyau | null = null;
    Promise.all([import('three'), import('./noyau')]).then(([THREE, moteur]) => {
      if (annule) return;
      sn = moteur.creerNoyau(THREE, canvas, raison => secours.current(raison));
      if (!sn) { secours.current('WebGL est indisponible sur cet ordinateur.'); return; }
      pilote.current = sn.pilote; setPret(true);
    }).catch(() => { if (!annule) secours.current('le moteur 3D n’a pas pu se charger.'); });
    return () => { annule = true; pilote.current = null; sn?.liberer(); setPret(false); };
  }, []);

  useEffect(() => { if (pret && taille.width && taille.height) pilote.current?.redimensionner(taille.width, taille.height); }, [pret, taille.width, taille.height]);
  useEffect(() => { if (pret) pilote.current?.etat(etat); }, [pret, etat]);
  useEffect(() => { if (pret) pilote.current?.forme(forme); }, [pret, forme]);
  useEffect(() => { if (pret) pilote.current?.niveau(voiceLevel); }, [pret]);
  useEffect(() => { if (pret) pilote.current?.mouvementReduit(reduit); }, [pret, reduit]);
  useEffect(() => { if (pret) pilote.current?.effets(effets); }, [pret, effets]);
  // Les photos du sujet passent au moteur quand elles changent (la clé résume la liste).
  const listePhotos = useRef(sujet.photos);
  listePhotos.current = sujet.photos;
  const clePhotos = sujet.photos.map(p => p?.url ?? '').join('|');
  useEffect(() => {
    if (pret) pilote.current?.photos(clePhotos ? listePhotos.current.map(p => (p ? { url: p.url, nom: p.nom } : null)) : null);
  }, [pret, clePhotos]);
  const basculerEffets = () => setEffets(v => { ecrireOui(CLE_EFFETS, !v); return !v; });

  /** La description (service, puis revalidée ici), construite aussitôt ; null si elle manque ou est refusée.
   *  `relire` : la scène déjà affichée vient d'être réglée (renommée : sa description seule ; redressée ou tournée :
   *  sa forme aussi, reposée d'après le nouveau réglage). */
  const charger = useCallback(async (id: string, relire: false | 'description' | 'forme' = false): Promise<SceneDescription | null> => {
    if (!relire && descRef.current?.id === id) return descRef.current;
    setChargement(true); setErreurScene('');
    try {
      const r = await api.labScene(id);
      const v = r.ok ? validerScene(r.data) : { ok: false as const, erreur: `Scène 3D indisponible : ${r.error || 'description illisible.'}` };
      if (!v.ok) { setErreurScene(v.erreur); return null; }
      // Un modèle importé (phase 7) : son fichier 3D est lu avant de construire. Un seul modèle reste en mémoire.
      const voulus = fichiersMaillage(v.scene);
      for (const [fichier, forme] of maillages) {
        if (relire === 'forme' || !voulus.includes(fichier)) { forme.dispose(); maillages.delete(fichier); quartsPoses.delete(fichier); }
      }
      if (voulus.some(f => !maillages.has(f))) await (await import('./maillage')).preparerMaillages(v.scene, api.labModel);
      descRef.current = v.scene; sceneAccueil.id = v.scene.id;
      // La scène réglée (renommée, redressée, tournée) reste dans la vue où elle était : hologramme ou vue d'origine.
      setDesc(v.scene); setVue(vu => (relire ? { ...VUE_DEPART, origine: vu.origine } : VUE_DEPART)); setChoisie(null); setInfoScene(false);
      pilote.current?.scene(v.scene);   // tout de suite : l'ordre qui suit (viser, signaler) trouve la scène construite
      return v.scene;
    } catch (e) {
      setErreurScene(`Scène 3D indisponible : ${e instanceof Error ? e.message : 'service local injoignable.'}`);
      return null;
    } finally {
      setChargement(false);
    }
  }, []);

  const fermer = useCallback(() => {
    descRef.current = null; sceneAccueil.id = null;
    setDesc(null); setVue(VUE_DEPART); setChoisie(null); setInfoScene(false); setErreurScene(''); setBulle(null);
  }, []);

  /** Une étape d'un ordre d'AURORA (reviews/accueil-holographique.md § 3) sur la scène construite ``d``. */
  const appliquerEtape = useCallback((o: SceneStep, d: SceneDescription, p: PiloteNoyau) => {
    const ids = piecesVisees(d, o.piece, o.groupe);
    const sauf = (liste: string[]) => liste.filter(x => !ids.includes(x));
    const montrerPiece = (id: string) => { setChoisie(id); setInfoScene(false); };
    switch (o.action) {
      case 'ouvrir': setVue(VUE_DEPART); setChoisie(null); setInfoScene(false); p.viser(null); break;
      case 'eclater': case 'assembler': setVue(v => ({ ...v, eclate: o.action === 'eclater' })); break;
      case 'animer': case 'arreter': setVue(v => ({ ...v, anime: o.action === 'animer' })); break;
      // L'intérieur en transparence est une vue de l'hologramme : la vue d'origine lui rend la place, sauf quand son
      // fichier dit quelle est sa coque (12/11/2026) : c'est alors elle qui devient transparente.
      case 'interieur':
        setVue(v => (v.origine && coqueOrigine.current ? { ...v, interieur: true } : { ...v, interieur: true, origine: false, animation: null }));
        break;
      case 'tout_montrer': setVue(v => ({ ...v, masquees: [], isolees: null })); break;
      case 'recentrer': p.viser(null); break;
      case 'masquer':
        setVue(v => ({ ...v, masquees: [...sauf(v.masquees), ...ids] }));
        setChoisie(c => (c && ids.includes(c) ? null : c));
        break;
      case 'isoler': setVue(v => ({ ...v, masquees: sauf(v.masquees), isolees: ids })); p.viser(ids); break;
      case 'montrer':
        setVue(v => ({ ...v, masquees: sauf(v.masquees), isolees: v.isolees ? [...sauf(v.isolees), ...ids] : null }));
        p.signaler(ids); p.viser(ids);
        if (o.piece) montrerPiece(o.piece);
        break;
      case 'expliquer':
        if (o.piece) { montrerPiece(o.piece); p.viser(ids); }
        else if (ids.length) { p.signaler(ids); p.viser(ids); }
        else { setChoisie(null); setInfoScene(true); }
        break;
      case 'agrandir': if (o.piece) { montrerPiece(o.piece); p.viser(ids, true); } break;
    }
  }, []);

  /** 10/11/2026 (« redresser et renommer ») : le modèle gardé qui est affiché (importé, ou reconstruit depuis une photo)
   *  est posé autrement, tourné d'un quart de tour ou renommé. Le service garde le réglage (il ne réécrit que la
   *  description, jamais le fichier du modèle), puis la scène est relue. Un refus est dit tel quel. */
  const enReglage = useRef(false);
  const regler = useCallback(async (quoi: 'redresser' | 'tourner' | 'renommer', titre?: string) => {
    const d = descRef.current;
    const forme = d?.pieces.map(p => p.forme).find(f => f.type === 'maillage');
    if (!d || !modeleGarde(d.origine) || !forme || forme.type !== 'maillage') return;
    const nom = (titre ?? '').trim();
    if ((quoi === 'renommer' && !nom) || enReglage.current) return;   // un réglage à la fois : un double clic n'en fait qu'un
    // « Redresser » : la position suivante parmi les six. « Tourner » : un quart de tour de plus que ce qui est
    // réellement affiché (le modèle a pu être tourné tout seul pour montrer son grand côté).
    const reglage = quoi === 'renommer' ? { titre: nom }
      : quoi === 'redresser' ? { debout: DEBOUT[(DEBOUT.indexOf(forme.debout ?? 'y') + 1) % DEBOUT.length] }
      : { quart: ((quartsPoses.get(forme.fichier) ?? forme.quart ?? 0) + 1) % 4 };
    setErreurScene('');
    enReglage.current = true;
    try {
      await api.labAdjustModel(d.id, reglage);
      await charger(d.id, quoi === 'renommer' ? 'description' : 'forme');
    } catch (e) {
      setErreurScene(e instanceof Error ? e.message : 'Modèle 3D non modifié : service local injoignable.');
    } finally {
      enReglage.current = false;
    }
  }, [charger]);

  /** Un ordre d'AURORA, appliqué une fois ; pour une phrase de plusieurs commandes (« retire le boîtier et montre les
   *  bobines »), ses étapes suivantes (ordre.suite) le sont ensuite, dans l'ordre dit. */
  const appliquer = useCallback(async (ordre: SceneOrder) => {
    const suite = Array.isArray(ordre.suite) ? ordre.suite.filter(s => s && typeof s.action === 'string') : [];
    for (const etape of [ordre, ...suite]) {
      if (etape.action === 'noyau') { fermer(); return; }
      const d = await charger(ordre.scene), p = pilote.current;
      if (!d || !p) return;
      if (etape.action === 'redresser' || etape.action === 'tourner' || etape.action === 'renommer') await regler(etape.action, etape.titre);
      else appliquerEtape(etape, d, p);
    }
  }, [charger, fermer, appliquerEtape, regler]);

  useEffect(() => {
    if (!pret || !sceneCommand || sceneCommand.id === derniereCommande) return;
    derniereCommande = sceneCommand.id;
    void appliquer(sceneCommand);
  }, [pret, sceneCommand, appliquer]);
  // De retour sur l'accueil : la scène qui était ouverte revient.
  useEffect(() => { if (pret && sceneAccueil.id && !descRef.current) void charger(sceneAccueil.id); }, [pret, charger]);

  // Le moteur suit l'état ; la scène et la pièce choisie partent avec la prochaine demande.
  useEffect(() => { if (pret) pilote.current?.scene(desc); }, [pret, desc]);
  useEffect(() => {
    const p = pilote.current;
    if (!pret || !p) return;
    p.eclater(vue.eclate); p.animer(vue.anime); p.interieur(vue.interieur); p.visibles(vue.masquees, vue.isolees);
  }, [pret, desc, vue]);
  // La vue d'origine (12/11/2026) : le fichier du modèle importé est lu à la première demande (origine.ts n'est chargé
  // qu'à ce moment), posé comme sa forme en hologramme, puis donné au moteur à la place de celle-ci. Un refus est dit
  // tel quel, et l'hologramme reste affiché.
  useEffect(() => {
    const p = pilote.current, fichier = desc ? fichierOrigine(desc) : null;
    const forme = desc?.pieces.map(x => x.forme).find(f => f.type === 'maillage');
    const garde = modeleOrigine.current;
    if (garde && garde.fichier !== fichier) {   // une autre scène, ou plus de scène : le modèle lu est rendu
      p?.origine(null); garde.modele.liberer(); modeleOrigine.current = null; setContenuOrigine(null);
    }
    if (!pret || !p || !vue.origine || !fichier || forme?.type !== 'maillage') { p?.origine(null); return; }
    let annule = false;
    const quart = quartsPoses.get(fichier) ?? forme.quart ?? 0;
    void (async () => {
      try {
        let m = modeleOrigine.current?.modele;
        if (m) m.poser(forme, quart);   // la scène a pu être redressée ou tournée depuis la lecture
        else {
          setLectureOrigine(true);
          const [{ lireOrigine }, donnees] = await Promise.all([import('./origine'), api.labModel(fichier)]);
          m = await lireOrigine(donnees, forme, quart);
          if (annule) { m.liberer(); return; }
          modeleOrigine.current = { fichier, modele: m };
        }
        p.origine(m); setContenuOrigine(m.contenu);
      } catch (e) {
        if (annule) return;
        setErreurScene(`Vue d’origine indisponible : ${e instanceof Error ? e.message : 'fichier illisible'}. L’hologramme reste affiché.`);
        setVue(v => ({ ...v, origine: false, animation: null }));
      } finally {
        if (!annule) setLectureOrigine(false);
      }
    })();
    return () => { annule = true; setLectureOrigine(false); };
  }, [pret, desc, vue.origine]);
  useEffect(() => {
    if (pret) pilote.current?.animation(contenuOrigine && vue.origine ? vue.animation : null);
  }, [pret, contenuOrigine, vue.origine, vue.animation]);
  useEffect(() => () => { modeleOrigine.current?.modele.liberer(); modeleOrigine.current = null; }, []);
  useEffect(() => { if (pret) pilote.current?.selection(choisie); }, [pret, desc, choisie]);
  // Une fiche ouverte à droite : la vue 3D se recentre dans la place qui reste à gauche.
  const ficheOuverte = liste || (desc !== null && (choisie !== null || infoScene || pieceFichier !== null));
  useEffect(() => {
    if (!pret) return;
    const fiche = ficheOuverte ? boite.current?.querySelector<HTMLElement>('.labo-panneau') : null;
    pilote.current?.marge(fiche ? fiche.offsetWidth + 12 : 0);
  }, [pret, ficheOuverte, taille.width]);
  useEffect(() => { setSceneContext(desc ? { id: desc.id, piece: choisie } : null); }, [desc, choisie, setSceneContext]);
  useEffect(() => () => setSceneContext(null), [setSceneContext]);

  // ── La souris et le doigt sur la scène (gestes communs avec le globe de Monde 3D) : glisser pour tourner, pincer ou
  // molette pour s'approcher, survoler pour nommer, cliquer pour décrire, double-cliquer pour agrandir.
  const nommer = (id: string | null, x = 0, y = 0) => {
    pilote.current?.survol(id);
    const nom = id ? descRef.current?.pieces.find(p => p.id === id)?.nom : undefined;
    setBulle(b => (nom ? (b?.nom === nom && Math.abs(b.x - x) + Math.abs(b.y - y) < 2 ? b : { nom, x, y }) : null));
  };
  const gestes = useGestes3d(canvasRef, pret && desc !== null, {
    tourner: (dx, dy) => pilote.current?.tourner(dx, dy),
    zoom: dy => pilote.current?.zoom(dy),
    survol: (x, y) => nommer(pilote.current?.choisir(x, y) ?? null, x, y),
    glisse: () => nommer(null),
    clic: (x, y) => {
      // En vue d'origine, une pièce que le fichier décrit lui-même a sa propre fiche ; sinon, la pièce de la scène.
      const duFichier = pilote.current?.choisirFichier(x, y) ?? null;
      const id = duFichier ? null : pilote.current?.choisir(x, y) ?? null;
      setPieceFichier(duFichier); setChoisie(id);
      if (id || duFichier) { setInfoScene(false); setListe(false); }
    },
  });
  const surDouble = (e: MouseEvent<HTMLCanvasElement>) => {
    const p = pilote.current;
    if (!p || !desc) return;
    const b = e.currentTarget.getBoundingClientRect(), id = p.choisir(e.clientX - b.left, e.clientY - b.top);
    if (id) { setChoisie(id); setInfoScene(false); p.viser([id], true); } else p.viser(null);
  };

  // Une photo du sujet, d'un clic (sur son panneau ou son nom dans la légende) : le lieu sur le globe, comme dans le chat.
  const voirSurGlobe = (ph: { nom: string; la: number; lo: number }) => showOnGlobe({ focus: { lat: ph.la, lon: ph.lo, nom: ph.nom }, couche: 'guide' });
  const photoSousPointeur = (e: { clientX: number; clientY: number; currentTarget: HTMLCanvasElement }) => {
    const p = pilote.current;
    if (!p || desc || !photosVues.length) return null;
    const b = e.currentTarget.getBoundingClientRect();
    return p.photoSous(e.clientX - b.left, e.clientY - b.top);
  };
  const surClicPhoto = (e: MouseEvent<HTMLCanvasElement>) => {
    const i = photoSousPointeur(e), ph = i === null ? null : sujet.photos[i];
    if (ph) voirSurGlobe(ph);
  };
  const surPointeur = (e: PointerEvent<HTMLCanvasElement>) => {
    gestes.onPointerMove(e);
    if (e.pointerType !== 'mouse') return;
    const i = photoSousPointeur(e);
    pilote.current?.survolPhoto(i);
    e.currentTarget.style.cursor = i === null ? '' : 'pointer';
  };

  const agrandir = (id: string) => pilote.current?.viser([id], true);
  const isoler = (id: string) => { setVue(v => ({ ...v, isolees: [id], masquees: v.masquees.filter(x => x !== id) })); pilote.current?.viser([id]); };
  const masquer = (id: string) => { setVue(v => ({ ...v, masquees: [...v.masquees.filter(x => x !== id), id] })); setChoisie(null); };
  const surTouche = (e: KeyboardEvent) => {
    if (e.key !== 'Escape' || (!choisie && !infoScene && !liste && !pieceFichier)) return;
    e.stopPropagation(); setChoisie(null); setInfoScene(false); setListe(false); setPieceFichier(null);
  };
  // La liste « Modèles 3D » : un nom ouvre la scène (en hologramme) ; l'icône de la vue d'origine, sur la ligne d'un
  // modèle importé en GLB, l'ouvre telle que son fichier la décrit. Une scène retirée quitte l'écran si c'est elle qui
  // est affichée.
  const ouvrirDeLaListe = (id: string, origine = false) => {
    void charger(id).then(d => {
      if (!d) return;
      setVue(v => ({ ...v, origine: origine && fichierOrigine(d) !== null, animation: null }));
      pilote.current?.viser(null);
    });
  };
  const apresRetrait = (id: string) => { if (descRef.current?.id === id) fermer(); };

  const libelle = desc
    ? `Scène 3D « ${desc.titre} »${vue.origine ? ', vue d’origine (le fichier tel qu’il a été importé)' : ''}. Glisser pour tourner, molette pour s’approcher, clic sur une pièce pour la décrire.`
    : `Noyau holographique d'AURORA : ${ETIQUETTES[etat].toLowerCase()}${photosVues.length ? `, avec les photos : ${photosVues.map(p => p.nom).join(', ')}` : ''}`;
  return (
    <div className={`labo ${desc ? 'labo-en-scene' : ''} ${cerveau ? 'labo-avec-cerveau' : ''}`} ref={boite} onKeyDown={surTouche}>
      <canvas ref={canvasRef} className="labo-canvas" role="img" aria-label={libelle} {...gestes} onPointerMove={surPointeur} onClick={surClicPhoto}
        onPointerLeave={e => { nommer(null); pilote.current?.survolPhoto(null); e.currentTarget.style.cursor = ''; }} onDoubleClick={surDouble} />
      {!pret && <div className="labo-chargement"><Spinner size={14} /> Chargement du noyau 3D…</div>}
      {cerveau && <CerveauFlottant place="centre" />}
      {desc && <EnteteScene desc={desc} onInfo={() => { setChoisie(null); setPieceFichier(null); setListe(false); setInfoScene(v => !v); }}
        renommer={modeleGarde(desc.origine) ? nom => void regler('renommer', nom) : undefined}
        origine={vue.origine ? { lecture: lectureOrigine, contenu: contenuOrigine } : undefined} />}
      {liste && <PanneauModeles ouverte={desc?.id ?? null} origine={vue.origine} ouvrir={ouvrirDeLaListe} retiree={apresRetrait} fermer={() => setListe(false)} />}
      {!liste && desc && pieceFichier && <PanneauPieceFichier desc={desc} piece={pieceFichier} fermer={() => setPieceFichier(null)} />}
      {!liste && desc && !pieceFichier && choisie && <PanneauPiece desc={desc} id={choisie} fermer={() => setChoisie(null)} agrandir={agrandir} isoler={isoler} masquer={masquer} />}
      {!liste && desc && !pieceFichier && !choisie && infoScene && <PanneauScene desc={desc} fermer={() => setInfoScene(false)} />}
      {bulle && <span className="labo-bulle" style={{ left: bulle.x, top: bulle.y }} aria-hidden="true">{bulle.nom}</span>}
      <div className="labo-bas">
        {desc && <BarreScene desc={desc} vue={vue} changer={setVue} recentrer={() => pilote.current?.viser(null)} fermer={fermer}
          redresser={modeleGarde(desc.origine) ? () => void regler('redresser') : undefined}
          tourner={modeleGarde(desc.origine) ? () => void regler('tourner') : undefined}
          glb={fichierOrigine(desc) !== null} animations={vue.origine ? contenuOrigine?.animations : undefined}
          piecesOrigine={vue.origine ? contenuOrigine?.pieces : undefined} coqueOrigine={vue.origine && contenuOrigine?.coque === true} />}
        {!desc && photosVues.length > 0 && (
          <div className="labo-photos" role="group" aria-label={`Photos du sujet${sujet.titre ? ` : ${sujet.titre}` : ''}`}>
            <Camera size={13} aria-hidden="true" />
            {sujet.titre && <span className="labo-photos-titre">{sujet.titre}</span>}
            {sujet.rang && <span className="labo-photos-rang" title="Les photos de ce sujet changent toutes les 9 secondes">{sujet.rang}</span>}
            {photosVues.map(ph => (
              <span key={ph.img} className="labo-photo">
                <button type="button" onClick={() => voirSurGlobe(ph)} title={`Voir ${ph.nom} sur le globe`}>{ph.nom}</button>
                <a href={`https://commons.wikimedia.org/wiki/File:${encodeURIComponent(ph.img)}`} target="_blank" rel="noopener noreferrer"
                  title="Auteur et licence de la photo sur Wikimedia Commons (nouvel onglet)">Commons</a>
              </span>
            ))}
            <button type="button" className="labo-photos-fermer" onClick={sujet.fermer} title="Retirer les photos" aria-label="Retirer les photos">
              <X size={12} aria-hidden="true" />
            </button>
          </div>
        )}
        <div className="labo-bas-ligne">
          <span className={`labo-etat labo-etat-${etat}`} role="status"><i aria-hidden="true" />{ETIQUETTES[etat]}</span>
          <button type="button" className={`labo-effets ${effets ? 'labo-effets-on' : ''}`} aria-pressed={effets} onClick={basculerEffets}
            title={effets ? 'Couper les animations avancées (particules, ondes)' : 'Rallumer les animations avancées'}>
            <Sparkles size={13} aria-hidden="true" /> Animations
          </button>
          <button type="button" className={`labo-effets ${liste ? 'labo-effets-on' : ''}`} aria-pressed={liste}
            onClick={() => { setChoisie(null); setPieceFichier(null); setInfoScene(false); setListe(v => !v); }}
            title={liste ? 'Fermer la liste des modèles 3D' : 'Modèles 3D : ouvrir une scène gardée, ou en retirer une'}>
            <Boxes size={13} aria-hidden="true" /> Modèles 3D
          </button>
        </div>
      </div>
      {erreurScene && (
        <p className="labo-erreur labo-erreur-scene" role="alert">
          {erreurScene}
          <button type="button" className="icon-btn" onClick={() => setErreurScene('')} aria-label="Fermer le message"><X size={14} /></button>
        </p>
      )}
      {etat === 'erreur' && message && <p className="labo-erreur" role="alert">{message}</p>}
    </div>
  );
}
