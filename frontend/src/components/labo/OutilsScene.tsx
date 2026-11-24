// Les commandes et les panneaux d'une scène du laboratoire (phase 3, 08/11/2026) : l'en-tête qui dit ce qu'est la scène
// (illustration, modèle calculé ou simulation : cahier § 8), la barre d'outils et le panneau de la pièce choisie.
// Tout le texte vient de la description validée (scene.ts) ; rien n'est inventé ici. Panneaux et badges : ceux de
// l'accueil (ui.tsx : Panel, Badge), pas de nouveau style de carte.
import { useEffect, useState, type ReactNode } from 'react';
import { ArrowUpFromLine, Box, Crosshair, Expand, Eye, EyeOff, Film, Info, Layers, LocateFixed, Orbit, Palette, Pause, Pencil, Play, RotateCw, Shrink, Trash2, X, ZoomIn } from 'lucide-react';
import { api } from '../../lib/api';
import type { SceneCatalogueItem, SceneDescription, SceneNature } from '../../lib/types';
import type { ContenuGlb } from '../hologram/glb';
import { Badge, Panel } from '../ui';
import { fichierOrigine, libelleAnimation, surPresentoir, validerScene, type PieceFichier, type VueScene } from './scene';

const NATURES: Record<SceneNature, { texte: string; ton: 'violet' | 'cyan' | 'green' }> = {
  illustration: { texte: 'Illustration pédagogique', ton: 'violet' },
  modele_calcule: { texte: 'Modèle calculé', ton: 'cyan' },
  simulation: { texte: 'Simulation scientifique', ton: 'green' },
};

/** Ce qu'est la scène (cahier § 8) et, pour une scène construite à la demande (phase 4), qu'elle est générée
 *  automatiquement : modèles prédéfinis, objets générés et simulations ne se confondent pas. */
function Nature({ desc }: { desc: SceneDescription }) {
  const n = NATURES[desc.nature];
  return (
    <>
      <Badge tone={n.ton} title={desc.avertissement}>{n.texte}</Badge>
      {desc.origine === 'genere' && <Badge tone="amber" title={desc.avertissement}>Généré automatiquement</Badge>}
      {/* Phase 7 : un modèle 3D donné par ID Studio IA, montré tel quel (ni construit ni vérifié par AURORA). */}
      {desc.origine === 'importe' && <Badge tone="cyan" title={desc.avertissement}>Modèle importé</Badge>}
      {/* « Photo vers 3D » : une forme qu'une IA locale a reconstruite depuis une photo ; ce que la photo ne montre pas est inventé. */}
      {desc.origine === 'reconstruit' && <Badge tone="amber" title={desc.avertissement}>Reconstruit depuis une photo</Badge>}
    </>
  );
}

const fermerBouton = (fermer: () => void) => (
  <button type="button" className="icon-btn" onClick={fermer} title="Fermer (Échap)" aria-label="Fermer le panneau"><X size={16} /></button>
);

const ORIGINE_TITRE = 'Vue d’origine : le fichier GLB tel qu’il a été importé, avec ses matières et ses textures. AURORA ne l’a ni construit ni vérifié.';
const compte = (n: number, un: string, plusieurs: string) => `${n} ${n > 1 ? plusieurs : un}`;

/** Ce que la vue d'origine montre, dit tel quel (12/11/2026) : ce que le fichier contient (compté dans le fichier, pas
 *  estimé) et ce qui n'y est pas. Seul l'éclairage vient d'AURORA. */
function NoteOrigine({ lecture, contenu }: { lecture: boolean; contenu: ContenuGlb | null }) {
  if (!contenu) return lecture ? <p className="labo-avertissement" role="status">Vue d’origine : lecture du fichier…</p> : null;
  const a = contenu.animations;
  return (
    <p className="labo-avertissement">
      Vue d’origine : le fichier tel qu’il a été importé
      {contenu.textures > 0 ? `, avec ses matières et ses textures (${compte(contenu.matieres, 'matière', 'matières')}, ${compte(contenu.textures, 'texture', 'textures')})`
        : contenu.matieres > 0 ? `, avec ses matières (${compte(contenu.matieres, 'matière', 'matières')}) ; il ne contient aucune texture`
          : ' ; il ne contient ni matière ni texture, sa forme est montrée dans une matière neutre'}
      . L’éclairage est celui d’AURORA.{' '}
      {a.length === 0 ? 'Ce fichier ne contient aucune animation.'
        : a.length === 1 ? `Animation du fichier : ${libelleAnimation(a[0], 0)}.`
          : `Animations du fichier : ${a.length}, au choix dans la barre du bas.`}
    </p>
  );
}

/** `renommer` (10/11/2026) : donné pour un modèle gardé seulement (importé, ou reconstruit depuis une photo). Le crayon
 *  ouvre le nom en saisie ; Entrée le garde, Échap ou un clic ailleurs l'abandonne.
 *  `origine` (12/11/2026) : donné en vue d'origine seulement — la lecture en cours, puis ce que le fichier contient. */
export function EnteteScene({ desc, onInfo, renommer, origine }: {
  desc: SceneDescription; onInfo: () => void; renommer?: (titre: string) => void; origine?: { lecture: boolean; contenu: ContenuGlb | null };
}) {
  const [saisie, setSaisie] = useState<string | null>(null);
  useEffect(() => { setSaisie(null); }, [desc.id, desc.titre]);
  const valider = () => {
    const nom = (saisie ?? '').trim();
    setSaisie(null);
    if (nom && nom !== desc.titre) renommer?.(nom);
  };
  return (
    <div className="labo-entete">
      <div className="labo-titre">
        {saisie === null ? <strong>{desc.titre}</strong> : (
          <form onSubmit={e => { e.preventDefault(); valider(); }}>
            <input className="labo-nom" autoFocus value={saisie} maxLength={76} placeholder="Nouveau nom, puis Entrée" aria-label="Nouveau nom du modèle"
              onChange={e => setSaisie(e.target.value)} onBlur={() => setSaisie(null)}
              onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); setSaisie(null); } }} />
          </form>
        )}
        <Nature desc={desc} />
        {origine && <Badge tone="green" title={ORIGINE_TITRE}>Vue d’origine</Badge>}
        {renommer && saisie === null && (
          <button type="button" className="icon-btn" onClick={() => setSaisie(desc.titre)} aria-label="Renommer ce modèle"
            title="Renommer ce modèle : son nom sert ensuite à le rouvrir (« montre-moi … »)"><Pencil size={14} /></button>
        )}
        <button type="button" className="icon-btn" onClick={onInfo} title="Fonctionnement de la scène" aria-label="Fonctionnement de la scène"><Info size={15} /></button>
      </div>
      <p className="labo-avertissement">{desc.avertissement}</p>
      {origine && <NoteOrigine lecture={origine.lecture} contenu={origine.contenu} />}
    </div>
  );
}

interface BarreProps {
  desc: SceneDescription; vue: VueScene; changer: (f: (v: VueScene) => VueScene) => void;
  recentrer: () => void; fermer: () => void;
  /** 10/11/2026, pour un modèle gardé seulement : le poser autrement, le tourner d'un quart de tour. */
  redresser?: () => void; tourner?: () => void;
  /** 12/11/2026 : `glb`, la scène est un modèle importé en GLB (la vue d'origine est proposée) ; `animations`, en vue
   *  d'origine, le nom des animations que son fichier contient (absent tant que le fichier n'est pas lu). */
  glb?: boolean; animations?: string[];
  /** 12/11/2026 : en vue d'origine, le nombre de pièces du fichier qui s'écartent (0 ou absent : un seul bloc). */
  piecesOrigine?: number;
  /** En vue d'origine : le fichier dit quelle est sa coque, elle peut être rendue transparente (bouton « Intérieur »). */
  coqueOrigine?: boolean;
}

export function BarreScene({ desc, vue, changer, recentrer, fermer, redresser, tourner, glb = false, animations = [], piecesOrigine = 0, coqueOrigine = false }: BarreProps) {
  // 11/11/2026 : un modèle en un seul bloc n'a pas d'articulation ; « Animer » le fait tourner sur son socle (scene.ts).
  const presentoir = !desc.pieces.some(p => p.articulation?.animation) && desc.pieces.some(surPresentoir);
  const mobile = presentoir || desc.pieces.some(p => p.articulation?.animation);
  // Un modèle en un seul bloc (importé) n'a rien à écarter : le bouton ne s'affiche pas plutôt que de ne rien faire.
  // 12/11/2026 : en vue d'origine, un fichier fait de plusieurs pièces s'écarte lui aussi (labo/origine.ts).
  const eclatable = vue.eclate || desc.pieces.some(p => p.eclate) || (vue.origine && piecesOrigine > 1);
  const filtre = vue.masquees.length > 0 || vue.isolees !== null;
  const outil = (on: boolean | null, titre: string, action: () => void, contenu: ReactNode) => (
    <button type="button" className={`labo-outil ${on ? 'labo-outil-on' : ''}`} aria-pressed={on ?? undefined} onClick={action} title={titre}>{contenu}</button>
  );
  // L'animation du fichier choisie dans la liste (la première au départ) ; elle n'est lue que sur le bouton « Lire ».
  const [choix, setChoix] = useState(0);
  useEffect(() => { setChoix(0); }, [desc.id]);
  const rang = Math.min(choix, animations.length - 1), enLecture = vue.animation !== null;
  return (
    <div className="labo-outils" role="toolbar" aria-label={`Commandes de la scène « ${desc.titre} »`}>
      {glb && outil(vue.origine, vue.origine ? 'Revenir à l’hologramme' : ORIGINE_TITRE, () => changer(v => ({ ...v, origine: !v.origine, animation: null })),
        <>{vue.origine ? <Box size={13} aria-hidden="true" /> : <Palette size={13} aria-hidden="true" />} {vue.origine ? 'Hologramme' : 'Origine'}</>)}
      {eclatable && outil(vue.eclate, vue.eclate ? 'Remonter les pièces' : 'Vue éclatée : écarter les pièces', () => changer(v => ({ ...v, eclate: !v.eclate })),
        <>{vue.eclate ? <Shrink size={13} aria-hidden="true" /> : <Expand size={13} aria-hidden="true" />} {vue.eclate ? 'Remonter' : 'Éclater'}</>)}
      {/* L'animation du fichier (vue d'origine seulement, et seulement s'il en contient) : son nom, puis Lire ou Arrêter. */}
      {vue.origine && animations.length > 0 && (
        <span className="labo-lecteur" role="group" aria-label="Animation du fichier">
          <Film size={13} aria-hidden="true" />
          {animations.length > 1 ? (
            <select value={rang} aria-label="Choisir une animation du fichier" title="Les animations que le fichier contient, sous le nom qu’il leur donne"
              onChange={e => { const i = Number(e.target.value); setChoix(i); if (enLecture) changer(v => ({ ...v, animation: i })); }}>
              {animations.map((nom, i) => <option key={i} value={i}>{libelleAnimation(nom, i)}</option>)}
            </select>
          ) : <span title="La seule animation que le fichier contient, sous le nom qu’il lui donne">{libelleAnimation(animations[0], 0)}</span>}
          {outil(enLecture, enLecture ? 'Arrêter l’animation : le modèle revient à la pose du fichier'
            : `Lire l’animation ${libelleAnimation(animations[rang], rang)} du fichier (ce n’est pas un mouvement calculé par AURORA)`,
            () => changer(v => ({ ...v, animation: v.animation === null ? rang : null })),
            <>{enLecture ? <Pause size={13} aria-hidden="true" /> : <Play size={13} aria-hidden="true" />} {enLecture ? 'Arrêter' : 'Lire'}</>)}
        </span>
      )}
      {/* En vue d'origine, le présentoir garde son nom : « Animer » se confondrait avec l'animation du fichier. */}
      {mobile && outil(vue.anime, vue.anime ? 'Arrêter le mouvement'
        : presentoir ? 'Faire tourner le modèle sur son socle, comme sur un présentoir (ce n’est pas le mouvement de l’objet)' : 'Montrer le mouvement des articulations',
        () => changer(v => ({ ...v, anime: !v.anime })),
        <>{vue.anime ? <Pause size={13} aria-hidden="true" /> : <Play size={13} aria-hidden="true" />} {vue.origine && presentoir ? 'Présentoir' : vue.anime ? 'Arrêter' : 'Animer'}</>)}
      {/* L'intérieur en transparence est une vue de l'hologramme. En vue d'origine, le bouton n'est proposé que si le
          fichier dit quelle est sa coque (12/11/2026) : c'est elle qui devient transparente. */}
      {!vue.origine && outil(vue.interieur, vue.interieur ? 'Revenir aux pièces pleines' : 'Voir l’intérieur en transparence', () => changer(v => ({ ...v, interieur: !v.interieur })),
        <><Layers size={13} aria-hidden="true" /> Intérieur</>)}
      {vue.origine && coqueOrigine && outil(vue.interieur, vue.interieur ? 'Revenir à la coque pleine' : 'Rendre la coque transparente, pour voir les pièces dedans',
        () => changer(v => ({ ...v, interieur: !v.interieur })), <><Layers size={13} aria-hidden="true" /> Intérieur</>)}
      {filtre && outil(null, 'Montrer toutes les pièces', () => changer(v => ({ ...v, masquees: [], isolees: null })),
        <><Eye size={13} aria-hidden="true" /> Tout montrer</>)}
      {redresser && outil(null, 'Redresser : pose le modèle dans une autre position (il y en a six). Recliquez jusqu’à la bonne ; elle est gardée pour la prochaine fois.', redresser,
        <><ArrowUpFromLine size={13} aria-hidden="true" /> Redresser</>)}
      {tourner && outil(null, 'Tourner le modèle d’un quart de tour sur lui-même (gardé pour la prochaine fois)', tourner,
        <><RotateCw size={13} aria-hidden="true" /> Tourner</>)}
      {outil(null, 'Revenir à la vue d’ensemble', recentrer, <><LocateFixed size={13} aria-hidden="true" /> Recentrer</>)}
      {outil(null, 'Fermer la scène et revenir au noyau', fermer, <><Orbit size={13} aria-hidden="true" /> Noyau</>)}
    </div>
  );
}

interface PanneauProps {
  desc: SceneDescription; id: string; fermer: () => void;
  agrandir: (id: string) => void; isoler: (id: string) => void; masquer: (id: string) => void;
}

export function PanneauPiece({ desc, id, fermer, agrandir, isoler, masquer }: PanneauProps) {
  const p = desc.pieces.find(x => x.id === id);
  if (!p) return null;
  const groupe = p.groupe ? desc.groupes.find(g => g.id === p.groupe)?.nom : undefined;
  return (
    <Panel className="labo-panneau" title={p.nom} subtitle={groupe} actions={fermerBouton(fermer)}>
      <p>{p.fonction}</p>
      {p.proprietes && p.proprietes.length > 0 && (
        <dl>{p.proprietes.map(q => <div key={q.nom}><dt>{q.nom}</dt><dd>{q.valeur}</dd></div>)}</dl>
      )}
      {p.articulation && <p className="labo-note">Articulation autour de l’axe {p.articulation.axe.toUpperCase()}, de {p.articulation.min}° à {p.articulation.max}°.</p>}
      <div className="labo-panneau-actions">
        <button type="button" className="btn btn-sm" onClick={() => agrandir(p.id)}><ZoomIn size={13} /> Agrandir</button>
        <button type="button" className="btn btn-sm" onClick={() => isoler(p.id)}><Crosshair size={13} /> Isoler</button>
        <button type="button" className="btn btn-sm" onClick={() => masquer(p.id)}><EyeOff size={13} /> Masquer</button>
      </div>
      {/* L'avertissement entier est déjà sous le titre de la scène ; ici le badge suffit (il le montre au survol). */}
      <p className="labo-note"><Nature desc={desc} /></p>
    </Panel>
  );
}

/** 12/11/2026 (ID Studio IA : « la fiche au clic », à la façon de sa page VITA) : la fiche d'une pièce d'un fichier GLB, en vue
 *  d'origine. Son nom, son rôle et ses caractéristiques sont ceux que le fichier écrit lui-même (« extras.aurora ») :
 *  AURORA les montre, elle ne les a ni écrits ni vérifiés, et le dit. */
export function PanneauPieceFichier({ desc, piece, fermer }: { desc: SceneDescription; piece: PieceFichier; fermer: () => void }) {
  const genre = piece.genre === 'coque' ? 'Coque du modèle' : piece.genre === 'piece' ? 'Pièce posée dans le modèle' : undefined;
  return (
    <Panel className="labo-panneau" title={piece.nom} subtitle={genre} actions={fermerBouton(fermer)}>
      {piece.role && <p>{piece.role}</p>}
      {piece.proprietes.length > 0 && (
        <dl>{piece.proprietes.map((q, i) => <div key={i}><dt>{q.nom}</dt><dd>{q.valeur}</dd></div>)}</dl>
      )}
      <p className="labo-note">Fiche écrite dans le fichier du modèle : AURORA la montre telle quelle, sans l’avoir vérifiée.</p>
      <p className="labo-note"><Nature desc={desc} /></p>
    </Panel>
  );
}

const ORIGINES: Record<string, string> = {
  predefini: 'Scène d’AURORA', genere: 'Maquette générée', importe: 'Modèle importé', reconstruit: 'Reconstruit depuis une photo',
};
/** Les modèles importés dont le fichier est un GLB (identifiant → oui ou non), retenus tant que l'interface est ouverte.
 *  La liste du service ne dit pas le type du fichier : il est lu dans la description de chaque modèle importé, une
 *  seule fois (le fichier d'un modèle gardé ne change pas ; un modèle retiré est oublié ici). */
const glbConnus = new Map<string, boolean>();

/** La liste des scènes gardées (11/11/2026, ID Studio IA : « retirer un modèle gardé depuis l’interface, et le sélectionner si je
 *  veux le revoir »). Un clic sur un nom ouvre la scène ; la corbeille demande d'abord une confirmation (CLAUDE.md),
 *  comme celle d'une conversation. Le service met alors les fichiers de côté, sans rien effacer, et dit où. Une scène
 *  prédéfinie fait partie d'AURORA : elle n'a pas de corbeille.
 *  12/11/2026 (ID Studio IA : « une icône en plus sur la partie (Modèles 3D) » pour les modèles « en glb avec les textures ») :
 *  la ligne d'un modèle importé en GLB porte une icône de plus, qui ouvre la scène en vue d'origine (`ouvrir(id, true)`)
 *  ou, si elle y est déjà (`origine`), revient à l'hologramme. Les autres lignes ne changent pas. */
export function PanneauModeles({ ouverte, origine, ouvrir, retiree, fermer }: {
  ouverte: string | null; origine: boolean; ouvrir: (id: string, origine?: boolean) => void; retiree: (id: string) => void; fermer: () => void;
}) {
  const [scenes, setScenes] = useState<SceneCatalogueItem[] | null>(null);
  const [, setGlbLus] = useState(0);
  const [erreur, setErreur] = useState('');
  const [aConfirmer, setAConfirmer] = useState<string | null>(null);
  const [enCours, setEnCours] = useState(false);
  const [note, setNote] = useState('');
  const lire = async () => {
    try {
      const r = await api.labScenes();
      if (r.ok && Array.isArray(r.data?.scenes)) { setScenes(r.data.scenes); setErreur(''); }
      else setErreur(`Liste indisponible : ${r.error || 'réponse illisible.'}`);
    } catch (e) {
      setErreur(`Liste indisponible : ${e instanceof Error ? e.message : 'service local injoignable.'}`);
    }
  };
  // Relue quand la scène affichée change : un modèle qui vient d'être fait ou importé y apparaît.
  useEffect(() => { void lire(); }, [ouverte]);
  // Quels modèles importés sont des GLB : lu une fois par modèle dans sa description, l'un après l'autre. L'icône de la
  // vue d'origine vient sur sa ligne à ce moment ; sans réponse du service, la ligne reste comme avant.
  useEffect(() => {
    const aLire = (scenes ?? []).filter(s => s.origine === 'importe' && !glbConnus.has(s.id));
    if (!aLire.length) return;
    let annule = false;
    void (async () => {
      for (const s of aLire) {
        try {
          const r = await api.labScene(s.id), v = r.ok ? validerScene(r.data) : null;
          if (v?.ok) glbConnus.set(s.id, fichierOrigine(v.scene) !== null);
        } catch { /* service injoignable : pas d'icône pour cette ligne */ }
        if (annule) return;
        setGlbLus(n => n + 1);
      }
    })();
    return () => { annule = true; };
  }, [scenes]);
  const retirer = async (s: SceneCatalogueItem) => {
    setEnCours(true); setErreur('');
    try {
      const fait = await api.labRemoveScene(s.id);
      glbConnus.delete(s.id);   // son identifiant peut resservir à un autre fichier
      setAConfirmer(null);
      setNote(`« ${fait.titre} » est retiré. Rien n’est effacé : ses fichiers sont mis de côté dans ${fait.mis_de_cote}`);
      retiree(s.id);
      await lire();
    } catch (e) {
      setErreur(e instanceof Error ? e.message : 'Scène non retirée : service local injoignable.');
    } finally {
      setEnCours(false);
    }
  };
  return (
    <Panel className="labo-panneau labo-panneau-liste" title="Modèles 3D" subtitle="Ouvrir ou retirer une scène" actions={fermerBouton(fermer)}>
      {scenes === null && !erreur && <p className="labo-note">Lecture de la liste…</p>}
      {scenes !== null && scenes.length === 0 && <p className="labo-note">Aucune scène pour l’instant.</p>}
      {scenes !== null && scenes.length > 0 && (
        <ul className="labo-liste">
          {scenes.map(s => {
            const retirable = s.origine !== undefined && s.origine !== 'predefini';
            const enOrigine = s.id === ouverte && origine;
            return (
              <li key={s.id} className={s.id === ouverte ? 'labo-liste-on' : undefined}>
                <button type="button" className="labo-liste-nom" onClick={() => ouvrir(s.id)} aria-current={s.id === ouverte ? 'true' : undefined}
                  title={enOrigine ? 'Revenir à l’hologramme' : s.id === ouverte ? 'Scène affichée' : `Ouvrir « ${s.titre} »`}>
                  <span>{s.titre}</span><small>{ORIGINES[s.origine ?? 'predefini'] ?? s.origine}</small>
                </button>
                {glbConnus.get(s.id) === true && (
                  <button type="button" className={`icon-btn ${enOrigine ? 'labo-liste-origine-on' : ''}`} aria-pressed={enOrigine} onClick={() => ouvrir(s.id, !enOrigine)}
                    title={enOrigine ? 'Vue d’origine affichée : revenir à l’hologramme' : ORIGINE_TITRE} aria-label={`Vue d’origine de « ${s.titre} »`}>
                    <Palette size={14} />
                  </button>
                )}
                {retirable && aConfirmer !== s.id && (
                  <button type="button" className="icon-btn" onClick={() => { setAConfirmer(s.id); setErreur(''); }} disabled={enCours}
                    title="Retirer de la liste (ses fichiers sont mis de côté, pas effacés)" aria-label={`Retirer « ${s.titre} »`}>
                    <Trash2 size={14} />
                  </button>
                )}
                {aConfirmer === s.id && (
                  <div className="labo-liste-confirme" role="alertdialog" aria-label="Confirmer le retrait">
                    <p>Retirer « {s.titre} » ? Il quitte la liste ; ses fichiers sont mis de côté dans le dossier d’AURORA, rien n’est effacé.</p>
                    <div className="labo-panneau-actions">
                      <button type="button" className="btn btn-sm btn-danger" onClick={() => void retirer(s)} disabled={enCours}><Trash2 size={13} /> Oui, retirer</button>
                      <button type="button" className="btn btn-sm" onClick={() => setAConfirmer(null)} disabled={enCours}>Non</button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {note && <p className="labo-note" role="status">{note}</p>}
      {erreur && <p className="labo-note labo-liste-erreur" role="alert">{erreur}</p>}
    </Panel>
  );
}

export function PanneauScene({ desc, fermer }: { desc: SceneDescription; fermer: () => void }) {
  return (
    <Panel className="labo-panneau" title={desc.titre} subtitle={<Nature desc={desc} />} actions={fermerBouton(fermer)}>
      <p>{desc.resume}</p>
      <p>{desc.fonctionnement}</p>
      <p className="labo-note">{desc.avertissement}</p>
    </Panel>
  );
}
