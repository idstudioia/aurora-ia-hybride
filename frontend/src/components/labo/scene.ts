// Le laboratoire 3D de l'accueil, phase 3 (cahier des charges § 4, 5 et 7 ; reviews/accueil-holographique.md § 2) :
// une scène `aurora.scene/1` est une DESCRIPTION (nombres bornés, noms de formes, textes), jamais du code. Le service
// la valide (tools/lab.py) ; elle est revalidée ici avant de construire quoi que ce soit, puis construite en pièces
// holographiques. Le noyau (noyau.ts) la pose sur son socle, la dessine et règle la caméra.
import type * as Trois from 'three';
import type { SceneCouleur, SceneDebout, SceneDescription, SceneForme, ScenePiece } from '../../lib/types';
import { VERTEX_NORMALE } from '../hologram/shaders';

type Three = typeof Trois;

// ── Validation : les mêmes bornes que le service (tools/lab.py). Une scène refusée ne construit rien.
const ID = /^[a-z][a-z0-9_]{0,39}$/;
const DOMAINES = new Set(['robotique', 'electronique', 'mecanique', 'physique', 'chimie', 'biologie', 'mathematiques', 'informatique', 'astronomie', 'objet']);
/** Le fichier d'un modèle 3D importé, tel que le service le nomme (tools/lab.py, MODEL_FILE) : jamais un chemin. */
const FICHIER_MODELE = /^imp_[a-z0-9_]{1,36}\.(?:stl|glb|obj)$/;
const NATURES = new Set(['illustration', 'modele_calcule', 'simulation']);
const COULEURS = new Set(['cyan', 'violet', 'ambre', 'vert']);
const AXES = new Set(['x', 'y', 'z']);
export const MAX_PIECES = 64;

class Refus extends Error {}
function exiger(ok: unknown, raison: string): asserts ok { if (!ok) throw new Refus(raison); }
const estObjet = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const estNombre = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const absent = (v: unknown) => v === undefined || v === null;
/** Un modèle gardé par le service dans son dossier, en un seul bloc : un fichier 3D d’ID Studio IA (« importe ») ou une forme
 *  reconstruite sur ce PC depuis une de ses photos (« reconstruit », « Photo vers 3D », 10/11/2026). */
export const modeleGarde = (origine: unknown): boolean => origine === 'importe' || origine === 'reconstruit';
/** Les six façons de poser un modèle (l'axe du fichier qui pointe vers le haut), dans l'ordre où « Redresser » les
 *  essaie : le même ordre que le service (tools/lab.py, MODEL_UP). */
export const DEBOUT: readonly SceneDebout[] = ['y', 'z', '-y', '-z', 'x', '-x'];
/** Les quarts de tour réellement appliqués à chaque modèle en mémoire (maillage.ts) : « Tourner » part de là, même
 *  quand le modèle a été tourné tout seul pour montrer son grand côté. */
export const quartsPoses = new Map<string, number>();
const texte =(v: unknown, max: number, quoi: string) =>
  exiger(typeof v === 'string' && v.trim() !== '' && v.length <= max, `${quoi} : texte de 1 à ${max} caractères attendu`);
const vecteur = (v: unknown, max: number, quoi: string) =>
  exiger(Array.isArray(v) && v.length === 3 && v.every(estNombre) && Math.hypot(...v) <= max, `${quoi} : trois nombres attendus, de norme ${max} au plus`);
const dimension = (v: unknown, quoi: string) => exiger(estNombre(v) && v > 0 && v <= 10, `${quoi} : dimension dans ]0 ; 10] attendue`);
const angle = (v: unknown): v is number => estNombre(v) && Math.abs(v) <= 360;

function validerForme(f: unknown, quoi: string) {
  exiger(estObjet(f), `${quoi} : forme attendue`);
  if (!absent(f.decalage)) vecteur(f.decalage, 20, `${quoi}, décalage`);
  if (!absent(f.axe)) exiger(AXES.has(f.axe as string), `${quoi} : axe x, y ou z attendu`);
  switch (f.type) {
    case 'boite': dimension(f.l, `${quoi}, largeur`); dimension(f.h, `${quoi}, hauteur`); dimension(f.p, `${quoi}, profondeur`); break;
    case 'cylindre':
      dimension(f.rayon, `${quoi}, rayon`); dimension(f.hauteur, `${quoi}, hauteur`);
      if (!absent(f.rayon_haut)) dimension(f.rayon_haut, `${quoi}, rayon du haut`);
      break;
    case 'sphere': dimension(f.rayon, `${quoi}, rayon`); break;
    case 'tore': dimension(f.rayon, `${quoi}, rayon`); dimension(f.epaisseur, `${quoi}, épaisseur`); break;
    case 'maillage':   // phase 7 : la forme vient d'un fichier 3D gardé par le service (un modèle importé)
      exiger(typeof f.fichier === 'string' && FICHIER_MODELE.test(f.fichier), `${quoi} : nom de fichier 3D invalide`);
      dimension(f.taille, `${quoi}, taille`);
      exiger(absent(f.debout) || DEBOUT.includes(f.debout as SceneDebout), `${quoi} : axe debout x, y, z, -x, -y ou -z attendu`);
      exiger(absent(f.quart) || (Number.isInteger(f.quart) && (f.quart as number) >= 0 && (f.quart as number) <= 3), `${quoi} : quart de tour de 0 à 3 attendu`);
      break;
    default: exiger(false, `${quoi} : forme inconnue (boite, cylindre, sphere, tore ou maillage)`);
  }
}

function validerPiece(p: unknown, i: number, groupes: Set<string>, vues: Set<string>) {
  exiger(estObjet(p) && typeof p.id === 'string' && ID.test(p.id), `pièce n° ${i + 1} : identifiant invalide`);
  const quoi = `pièce « ${p.id} »`;
  exiger(!vues.has(p.id), `${quoi} déclarée deux fois`);
  texte(p.nom, 60, `${quoi}, nom`);
  exiger(absent(p.groupe) || groupes.has(p.groupe as string), `${quoi} : groupe inconnu`);
  exiger(absent(p.parent) || vues.has(p.parent as string), `${quoi} : le parent doit être une pièce déclarée avant elle`);
  vecteur(p.position, 20, `${quoi}, position`);
  if (!absent(p.rotation)) exiger(Array.isArray(p.rotation) && p.rotation.length === 3 && p.rotation.every(angle), `${quoi}, rotation : trois angles dans [-360 ; 360] attendus`);
  validerForme(p.forme, quoi);
  exiger(COULEURS.has(p.couleur as string), `${quoi} : couleur cyan, violet, ambre ou vert attendue`);
  if (!absent(p.articulation)) {
    const a = p.articulation;
    exiger(estObjet(a) && AXES.has(a.axe as string) && angle(a.min) && angle(a.max) && angle(a.repos) && a.min <= a.repos && a.repos <= a.max,
      `${quoi} : articulation invalide (axe x, y ou z ; min ≤ repos ≤ max, en degrés)`);
    if (!absent(a.animation)) {
      const n = a.animation;
      exiger(estObjet(n) && estNombre(n.amplitude) && n.amplitude >= 0 && n.amplitude <= 360 && estNombre(n.periode) && n.periode > 0 && n.periode <= 120
        && (absent(n.phase) || (estNombre(n.phase) && n.phase >= 0 && n.phase <= 1))
        && (absent(n.continu) || typeof n.continu === 'boolean'), `${quoi} : animation invalide`);
    }
  }
  if (!absent(p.eclate)) vecteur(p.eclate, 20, `${quoi}, vue éclatée`);
  texte(p.fonction, 600, `${quoi}, fonction`);
  if (!absent(p.proprietes)) {
    exiger(Array.isArray(p.proprietes) && p.proprietes.length <= 8, `${quoi} : 8 propriétés au plus`);
    for (const q of p.proprietes) { exiger(estObjet(q), `${quoi} : propriété invalide`); texte(q.nom, 40, `${quoi}, propriété`); texte(q.valeur, 80, `${quoi}, valeur`); }
  }
}

export type Validation = { ok: true; scene: SceneDescription } | { ok: false; erreur: string };

/** La description, si elle respecte `aurora.scene/1` ; sinon la raison, en français. Rien n'est jamais exécuté. */
export function validerScene(x: unknown): Validation {
  try {
    exiger(estObjet(x), 'objet JSON attendu');
    exiger(x.format === 'aurora.scene/1', 'format « aurora.scene/1 » attendu');
    exiger(typeof x.id === 'string' && ID.test(x.id), 'identifiant de scène invalide');
    texte(x.titre, 80, 'titre');
    exiger(DOMAINES.has(x.domaine as string), 'domaine inconnu');
    exiger(NATURES.has(x.nature as string), 'nature attendue : illustration, modele_calcule ou simulation');
    // Phase 4 : une scène générée le dit (id « gen_… », origine « genere ») et n'est jamais qu'une illustration.
    // Phase 7 : un modèle 3D donné par ID Studio IA le dit aussi (id « imp_… », origine « importe »), et n'est qu'une illustration.
    // « Photo vers 3D » : une forme reconstruite depuis une photo (origine « reconstruit ») est gardée comme un modèle importé.
    exiger(absent(x.origine) || x.origine === 'predefini' || x.origine === 'genere' || modeleGarde(x.origine), 'origine attendue : predefini, genere, importe ou reconstruit');
    exiger((x.origine === 'genere') === (x.id as string).startsWith('gen_'),'une scène générée a un identifiant « gen_… » et l’origine « genere »');
    exiger(modeleGarde(x.origine) === (x.id as string).startsWith('imp_'), 'un modèle importé a un identifiant « imp_… » et l’origine « importe » ou « reconstruit »');
    exiger((x.origine !== 'genere' && !modeleGarde(x.origine)) || x.nature === 'illustration', 'une scène générée ou importée est une illustration, jamais une simulation');
    texte(x.avertissement, 300, 'avertissement'); texte(x.resume, 400, 'résumé'); texte(x.fonctionnement, 1500, 'fonctionnement');
    exiger(estObjet(x.camera), 'caméra attendue');
    vecteur(x.camera.position, 50, 'caméra, position'); vecteur(x.camera.cible, 50, 'caméra, cible');
    exiger(Array.isArray(x.groupes) && x.groupes.length <= 16, 'groupes : 16 au plus');
    const groupes = new Set<string>();
    x.groupes.forEach((g: unknown, i: number) => {
      exiger(estObjet(g) && typeof g.id === 'string' && ID.test(g.id) && !groupes.has(g.id), `groupe n° ${i + 1} : identifiant invalide ou en double`);
      texte(g.nom, 60, `groupe « ${g.id} », nom`);
      groupes.add(g.id);
    });
    exiger(Array.isArray(x.pieces) && x.pieces.length >= 1 && x.pieces.length <= MAX_PIECES, `pièces : de 1 à ${MAX_PIECES}`);
    const vues = new Set<string>();
    x.pieces.forEach((p: unknown, i: number) => { validerPiece(p, i, groupes, vues); vues.add((p as { id: string }).id); });
    for (const p of x.pieces as { forme: { type: string; fichier?: string } }[]) {
      exiger(p.forme.type !== 'maillage' || (modeleGarde(x.origine) && p.forme.fichier!.startsWith(`${x.id}.`)),
        'un maillage n’est permis que dans un modèle importé, et porte le nom de sa scène');
    }
    return { ok: true, scene: x as unknown as SceneDescription };
  } catch (e) {
    if (e instanceof Refus) return { ok: false, erreur: `Scène refusée : ${e.message}.` };
    throw e;
  }
}

/** Ce que l'utilisateur ou AURORA a changé dans la scène affichée (Laboratoire.tsx en garde l'état).
 *  12/11/2026, pour un modèle importé en GLB : `origine`, la vue d'origine (le fichier tel quel, avec ses matières, à la
 *  place de l'hologramme) ; `animation`, le rang de l'animation du fichier en cours de lecture (null : aucune). */
export interface VueScene {
  eclate: boolean; anime: boolean; interieur: boolean; masquees: string[]; isolees: string[] | null;
  origine: boolean; animation: number | null;
}
export const VUE_DEPART: VueScene = { eclate: false, anime: false, interieur: false, masquees: [], isolees: null, origine: false, animation: null };

/** La scène ouverte sur l'accueil, retenue hors des composants : en revenant sur l'accueil, Laboratoire.tsx la
 *  rouvre, et l'accueil (HomePage.tsx) montre le noyau même si le globe était le choix enregistré. */
export const sceneAccueil: { id: string | null } = { id: null };

/** Les pièces que désigne un ordre : une pièce, ou toutes celles d'un groupe (aucune si rien ne correspond). */
export function piecesVisees(d: SceneDescription, piece?: string | null, groupe?: string | null): string[] {
  if (piece) return d.pieces.some(p => p.id === piece) ? [piece] : [];
  if (groupe) return d.pieces.filter(p => p.groupe === groupe).map(p => p.id);
  return [];
}

/** Libère la mémoire graphique d'un objet et de tout ce qu'il porte (géométries, matières). Pour le noyau comme pour
 *  une scène retirée ; les textures sont libérées par leur propriétaire. */
export function libererObjets(o: Trois.Object3D) {
  o.traverse(x => {
    const m = x as Trois.Mesh;
    m.geometry?.dispose();
    const mats = Array.isArray(m.material) ? m.material : m.material ? [m.material] : [];
    for (const mat of mats) mat.dispose();
  });
}

// ── Construction : chaque pièce est un pivot (position, rotation) qui porte son articulation, qui porte le maillage et
// les pièces enfants. Matière holographique : bords lumineux (comme le halo du noyau), remplissage léger, arêtes nettes.
const RAD = Math.PI / 180;
const TEINTES: Record<SceneCouleur, string> = { cyan: '#38d6ff', violet: '#a77bff', ambre: '#ffb547', vert: '#3dffa8' };
/** Les pièces apparaissent quand la nuée du noyau, partie vers l'objet, commence à s'y poser (noyau.ts, DUREE_VOL). */
const DEBUT_APPARITION = 1;
const FRAGMENT_PIECE ='uniform vec3 c;uniform float force;uniform float clair;uniform float plein;varying vec3 n;varying vec3 p;'
  + 'void main(){float f=pow(1.-abs(n.z),2.);float s=.86+.14*sin(gl_FragCoord.y*1.4);'
  + 'gl_FragColor=vec4(mix(c,vec3(1.),clair),(plein+f*.9)*force*s);}';

/** Les formes des modèles importés (phase 7), lues et mises à la taille par labo/maillage.ts AVANT la construction, qui
 *  reste immédiate. Laboratoire.tsx n'en garde qu'une scène à la fois : un fichier peut peser des dizaines de Mo. */
export const maillages = new Map<string, Trois.BufferGeometry>();
/** Les fichiers 3D qu'une scène demande (aucun, sauf pour un modèle importé). */
export const fichiersMaillage = (d: SceneDescription): string[] => d.pieces.flatMap(p => (p.forme.type === 'maillage' ? [p.forme.fichier] : []));
/** Au-delà de ce nombre de triangles, un maillage n'a plus de visée au triangle près (trop lourd). */
const MAILLAGE_DENSE = 60_000;
/** ID Studio IA, 10/11/2026 (« le satellite n'est pas super, on peut faire mieux ») : un modèle détaillé garde ses arêtes
 *  tracées jusqu'à ce nombre de triangles. Sans elles, ses grandes faces vues de face restent presque vides (la
 *  matière n'éclaire que les bords), et un objet fait de plaques (panneaux, capots) ne se lit plus. Son angle est
 *  plus large que pour une maquette : les petites facettes d'un modèle allégé ne sont pas des arêtes. */
const MAILLAGE_ARETES_MAX = 400_000;
const ANGLE_ARETES = 28, ANGLE_ARETES_DENSE = 40;
/** ID Studio IA, 11/11/2026 (« mets-moi des animations dans les modèles que tu peux ») : un modèle en un seul bloc (importé, ou
 *  reconstruit depuis une photo) n'a aucune articulation. « Animer » le fait tourner sur son socle, comme sur un
 *  présentoir : un tour en ce nombre de secondes. Ce n'est pas le mouvement de l'objet, et le bouton le dit. */
const PERIODE_PRESENTOIR = 24;
/** Vrai pour une pièce qui tourne ainsi : un maillage importé sans articulation. */
export const surPresentoir = (p: ScenePiece): boolean => p.forme.type === 'maillage' && !p.articulation;

// ── 12/11/2026 (ID Studio IA : « une vue (modèle 3D origine) » pour les modèles « déjà construits en glb avec les textures », et
// leurs animations « pour ceux qui en ont déjà ») : la vue d'origine. L'hologramme ne garde que la forme d'un fichier ;
// la vue d'origine montre le fichier tel quel (labo/origine.ts), à la même place et à la même taille.
/** Le fichier que la vue d'origine montre : celui d'un modèle importé en GLB. null pour toute autre scène : un STL ou
 *  un OBJ n'a ni matière ni animation à montrer, une forme reconstruite depuis une photo n'a pas de couleurs, et une
 *  scène d'AURORA ou une maquette générée n'a pas de fichier. L'icône et le bouton ne sont proposés que si ce n'est pas null. */
export const fichierOrigine = (d: SceneDescription): string | null =>
  (d.origine === 'importe' ? fichiersMaillage(d).find(f => f.endsWith('.glb')) ?? null : null);
/** Le nom d'une animation tel que l'interface l'écrit : celui que le fichier lui donne, sinon son rang, dit sans nom. */
export const libelleAnimation = (nom: string, rang: number): string => (nom ? `« ${nom} »` : `animation ${rang + 1} (sans nom dans le fichier)`);
/** Ce que la scène reçoit pour la vue d'origine (le modèle lu par labo/origine.ts). */
export interface VueOrigine {
  /** Le modèle du fichier, déjà posé comme sa forme en hologramme (debout, centré, sur le sol, à la taille dite). */
  readonly objet: Trois.Object3D;
  /** Son éclairage : les matières d'un fichier ont besoin de lumière, celles de l'hologramme non. */
  readonly lumieres: Trois.Object3D;
  /** Ce que seul le moteur de rendu peut préparer (les reflets des matières), une fois. */
  preparer(rendu: Trois.WebGLRenderer): void;
  /** Lit l'animation du fichier de ce rang ; null : arrêt, le modèle revient à la pose du fichier. */
  jouer(rang: number | null): void;
  /** Une image : dt secondes de l'animation en cours. Vrai tant qu'une animation est lue, ou que des pièces bougent. */
  avancer(dt: number): boolean;
  /** 12/11/2026 : écarte les pièces du fichier, ou les remonte (un fichier fait de plusieurs pièces seulement). */
  eclater?(on: boolean): void;
  /** La pièce du fichier sous ce rayon, quand le fichier décrit ses pièces ; null sinon. */
  choisir?(rayon: Trois.Raycaster): PieceFichier | null;
  /** Rend transparente la coque du fichier (ses nœuds dits « coque »), ou la rend pleine. */
  interieur?(on: boolean): void;
}
/** 12/11/2026 (ID Studio IA : « la fiche au clic et la coque transparente », à la façon de sa page VITA) : une pièce d'un fichier
 *  GLB, telle que le fichier la décrit dans « extras.aurora ». Ce sont des textes du fichier, montrés tels quels. */
export interface PieceFichier {
  nom: string; genre: 'coque' | 'piece' | null; role: string; proprietes: { nom: string; valeur: string }[];
}

function geometrie(THREE: Three, f: SceneForme): Trois.BufferGeometry {
  switch (f.type) {
    // Sans fichier lu, le volume du modèle (jamais atteint : Laboratoire.tsx refuse la scène si la lecture échoue).
    case 'maillage': return maillages.get(f.fichier) ?? new THREE.BoxGeometry(f.taille, f.taille, f.taille);
    case 'boite': return new THREE.BoxGeometry(f.l, f.h, f.p);
    case 'cylindre': return new THREE.CylinderGeometry(f.rayon_haut ?? f.rayon, f.rayon, f.hauteur, 40);
    case 'sphere': return new THREE.SphereGeometry(f.rayon, 32, 20);
    case 'tore': return new THREE.TorusGeometry(f.rayon, f.epaisseur, 14, 56);
  }
}

/** L'axe d'un cylindre ou d'un tore oriente le maillage seul ; le décalage le déplace dans le repère de la pièce. */
function orienter(m: Trois.Mesh, f: SceneForme) {
  if (f.decalage) m.position.set(f.decalage[0], f.decalage[1], f.decalage[2]);
  const axe = f.type === 'cylindre' || f.type === 'tore' ? f.axe ?? 'y' : 'y';
  if (f.type === 'cylindre') { if (axe === 'x') m.rotation.z = Math.PI / 2; else if (axe === 'z') m.rotation.x = Math.PI / 2; }
  if (f.type === 'tore') { if (axe === 'y') m.rotation.x = Math.PI / 2; else if (axe === 'x') m.rotation.y = Math.PI / 2; }
}

interface Piece {
  d: ScenePiece; pivot: Trois.Group; joint: Trois.Group; maillage: Trois.Mesh;
  mat: Trois.ShaderMaterial; matAretes: Trois.LineBasicMaterial; couleur: Trois.Color; base: Trois.Vector3;
  angle: number; vis: number; visCible: number; retard: number;
}

export interface SceneLabo {
  readonly desc: SceneDescription;
  readonly racine: Trois.Group;
  /** Sphère englobante dans le repère du monde (scène assemblée, au repos) et le haut de la scène. */
  readonly centre: Trois.Vector3;
  readonly rayon: number;
  readonly haut: number;
  /** La même sphère, scène éclatée (au repos) : la vue d'ensemble s'y cadre quand les pièces sont écartées. */
  readonly eclatee: { centre: Trois.Vector3; rayon: number };
  /** La cible de la caméra prévue par la description, dans le repère du monde. */
  readonly cibleCamera: Trois.Vector3;
  /** Chaque pièce, son maillage et sa matière : la nuée du noyau sème ses points dessus (nuee.ts, semerScene). */
  readonly pieces: readonly { id: string; maillage: Trois.Mesh; mat: Trois.ShaderMaterial }[];
  /** Une image : dt secondes ; `melange` 0 → 1 fait apparaître la scène. Vrai tant que quelque chose bouge. */
  avancer(dt: number, reduit: boolean, melange: number): boolean;
  eclater(on: boolean): void;
  animer(on: boolean): void;
  interieur(on: boolean): void;
  /** La vue d'origine d'un modèle importé en GLB : le fichier tel quel prend la place de son hologramme (null : retour
   *  à l'hologramme). Le modèle reste à celui qui l'a lu : la scène ne le libère pas. */
  origine(v: VueOrigine | null): void;
  /** En vue d'origine : lit l'animation du fichier de ce rang, ou l'arrête (null). */
  animation(rang: number | null): void;
  /** Masquées : invisibles. Isolées (si non null) : les autres deviennent des fantômes, qu'on ne peut pas choisir. */
  visibles(masquees: string[], isolees: string[] | null): void;
  selection(id: string | null): void;
  survol(id: string | null): void;
  /** Ces pièces clignotent quelques secondes (« montre les servomoteurs »). */
  signaler(ids: string[]): void;
  choisir(rayon: Trois.Raycaster): string | null;
  /** 12/11/2026 : en vue d'origine, la pièce du fichier sous ce rayon, si le fichier décrit ses pièces ; null sinon. */
  choisirFichier(rayon: Trois.Raycaster): PieceFichier | null;
  /** Sphère englobante de ces pièces, dans le repère du monde (null : aucune n'existe). */
  englobant(ids: string[]): { centre: Trois.Vector3; rayon: number } | null;
  liberer(): void;
}

/** La scène est réduite (jamais agrandie) pour tenir sur le socle du noyau, posée à la hauteur `socleY`. */
export function construireScene(THREE: Three, d: SceneDescription, socleY: number): SceneLabo {
  const racine = new THREE.Group();
  const additif = { transparent: true, depthWrite: false, blending: THREE.AdditiveBlending };
  const BLANC = new THREE.Color('#ffffff');
  const parId = new Map<string, Piece>(), pieces: Piece[] = [];
  d.pieces.forEach((pd, i) => {
    const pivot = new THREE.Group(), joint = new THREE.Group();
    pivot.add(joint);
    const base = new THREE.Vector3(pd.position[0], pd.position[1], pd.position[2]);
    pivot.position.copy(base);
    if (pd.rotation) pivot.rotation.set(pd.rotation[0] * RAD, pd.rotation[1] * RAD, pd.rotation[2] * RAD);
    const geo = geometrie(THREE, pd.forme), couleur = new THREE.Color(TEINTES[pd.couleur]);
    const mat = new THREE.ShaderMaterial({
      ...additif, uniforms: { c: { value: couleur.clone() }, force: { value: 0 }, clair: { value: 0 }, plein: { value: .14 } },
      vertexShader: VERTEX_NORMALE, fragmentShader: FRAGMENT_PIECE,
    });
    const maillage = new THREE.Mesh(geo, mat);
    orienter(maillage, pd.forme);
    maillage.userData.piece = pd.id;
    const matAretes = new THREE.LineBasicMaterial({ ...additif, color: couleur.clone(), opacity: 0 });
    // Un modèle importé très détaillé (jusqu'à un million de triangles) : pas d'arêtes tracées, et le pointeur vise sa
    // boîte englobante plutôt que chacun de ses triangles (sinon chaque mouvement de souris les parcourt tous).
    const triangles = (geo.index ?? geo.getAttribute('position')).count / 3;
    const dense = pd.forme.type === 'maillage' && triangles > MAILLAGE_DENSE;
    if (!dense || triangles <= MAILLAGE_ARETES_MAX) {
      maillage.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo, dense ? ANGLE_ARETES_DENSE : ANGLE_ARETES), matAretes));   // même décalage, même axe
    }
    if (dense) {
      const inverse = new THREE.Matrix4(), local = new THREE.Ray(), touche = new THREE.Vector3();
      maillage.raycast = (viseur, touches) => {
        if (!geo.boundingBox) geo.computeBoundingBox();
        local.copy(viseur.ray).applyMatrix4(inverse.copy(maillage.matrixWorld).invert());
        if (!local.intersectBox(geo.boundingBox!, touche)) return;
        const point = touche.clone().applyMatrix4(maillage.matrixWorld), distance = viseur.ray.origin.distanceTo(point);
        if (distance >= viseur.near && distance <= viseur.far) touches.push({ distance, point, object: maillage });
      };
    }
    joint.add(maillage);
    const parent = pd.parent ? parId.get(pd.parent) : undefined;
    (parent ? parent.joint : racine).add(pivot);
    const a = pd.articulation;
    const p: Piece = { d: pd, pivot, joint, maillage, mat, matAretes, couleur, base, angle: a ? a.repos : 0, vis: 1, visCible: 1, retard: DEBUT_APPARITION + i * .06 };
    if (a) joint.rotation[a.axe] = p.angle * RAD;
    parId.set(pd.id, p); pieces.push(p);
  });

  // Mise à l'échelle et pose sur le socle, d'après la scène assemblée et au repos.
  const boite = new THREE.Box3(), sphere = new THREE.Sphere();
  racine.updateMatrixWorld(true);
  boite.setFromObject(racine).getBoundingSphere(sphere);
  racine.scale.setScalar(Math.min(1, 1.9 / Math.max(sphere.radius, 1e-3)));
  racine.position.y = socleY + .01;
  racine.updateMatrixWorld(true);
  boite.setFromObject(racine).getBoundingSphere(sphere);
  const centre = sphere.center.clone(), rayon = sphere.radius, haut = boite.max.y;
  // Un modèle importé : le service garde le fichier sans en lire la forme, sa description ne connaît donc pas ses
  // proportions. La caméra vise son centre (une pièce plate, visée trop haut, se voyait de loin et en bas du cadre).
  const c = d.camera.cible, cibleCamera = modeleGarde(d.origine) ? centre.clone() : racine.localToWorld(new THREE.Vector3(c[0], c[1], c[2]));
  // Les écarts s'additionnent le long de la chaîne (chaque pièce part de sa mère déjà écartée) : on les pose un instant.
  for (const p of pieces) { const e = p.d.eclate; if (e) p.pivot.position.set(p.base.x + e[0], p.base.y + e[1], p.base.z + e[2]); }
  racine.updateMatrixWorld(true);
  boite.setFromObject(racine).getBoundingSphere(sphere);
  const eclatee = { centre: sphere.center.clone(), rayon: sphere.radius };
  for (const p of pieces) p.pivot.position.copy(p.base);
  racine.updateMatrixWorld(true);

  let eclCible = 0, ecl = 0, anime = false, tAnim = 0, age = 0, xray = false;
  let choisie: string | null = null, survolee: string | null = null;
  const signal = new Set<string>();
  let tSignal = 0;
  const FIN_APPARITION = DEBUT_APPARITION + pieces.length * .06 + .5;
  // La vue d'origine (12/11/2026) : le modèle du fichier se pose dans l'articulation du bloc importé, à la place de son
  // hologramme. Il tourne donc sur le présentoir avec lui, et se cache avec lui.
  const bloc = pieces.find(p => p.d.forme.type === 'maillage');
  let origine: VueOrigine | null = null;
  const poserOrigine = (v: VueOrigine | null) => {
    if (origine) { origine.objet.removeFromParent(); origine.lumieres.removeFromParent(); }
    origine = bloc ? v : null;
    if (!origine || !bloc) return;
    origine.objet.position.copy(bloc.maillage.position);   // le même décalage que la forme
    origine.objet.visible = false;                          // montré par `avancer`, avec le bloc
    bloc.joint.add(origine.objet); racine.add(origine.lumieres);
    origine.eclater?.(eclCible === 1);                      // lu après coup, il prend l'état de la vue (éclatée ou non)
    origine.interieur?.(xray);                              // … et sa coque, pleine ou transparente
  };

  return {
    desc: d, racine, centre, rayon, haut, cibleCamera, eclatee,
    pieces: pieces.map(p => ({ id: p.d.id, maillage: p.maillage, mat: p.mat })),
    avancer(dt, reduit, melange) {
      const k = reduit ? 1 : 1 - Math.exp(-dt * 3.2), kAngle = reduit ? 1 : 1 - Math.exp(-dt * 8);
      age += dt; ecl += (eclCible - ecl) * k;
      if (anime) tAnim += dt;
      if (tSignal > 0) { tSignal -= dt; if (tSignal <= 0) signal.clear(); }
      let bouge = anime || tSignal > 0 || Math.abs(eclCible - ecl) > .002 || (!reduit && age < FIN_APPARITION);
      for (const p of pieces) {
        const a = p.d.articulation;
        if (a) {
          const an = a.animation;
          // Un tour par période pour une rotation continue (rotor, roue : phase 4), sinon un va-et-vient borné.
          const cible = !anime || !an ? p.angle
            : an.continu ? a.repos + 360 * (tAnim / an.periode + (an.phase ?? 0))
              : Math.min(a.max, Math.max(a.min, a.repos + an.amplitude * Math.sin(2 * Math.PI * (tAnim / an.periode + (an.phase ?? 0)))));
          p.angle += (cible - p.angle) * kAngle;
          p.joint.rotation[a.axe] = p.angle * RAD;
        } else if (anime && surPresentoir(p.d)) {
          p.angle = (p.angle + 360 * dt / PERIODE_PRESENTOIR) % 360;   // à l'arrêt, il reste où il est
          p.joint.rotation.y = p.angle * RAD;
        }
        const e = p.d.eclate;
        if (e) p.pivot.position.set(p.base.x + e[0] * ecl, p.base.y + e[1] * ecl, p.base.z + e[2] * ecl);
        p.vis += (p.visCible - p.vis) * k;
        if (Math.abs(p.visCible - p.vis) > .002) bouge = true;
        // Apparition l'une après l'autre (le noyau « construit la scène ») ; d'un coup si le mouvement est réduit.
        const app = reduit ? 1 : Math.min(1, Math.max(0, (age - p.retard) * 2.6));
        const sel = p.d.id === choisie ? 1 : 0, sur = p.d.id === survolee ? 1 : 0;
        const pulse = signal.has(p.d.id) ? (reduit ? 1 : .5 + .5 * Math.sin(age * 7)) : 0;
        const f = p.vis * melange * app;
        p.mat.uniforms.force.value = f * (1 + .55 * sel + .3 * sur + .7 * pulse);
        p.mat.uniforms.clair.value = .35 * sel + .15 * sur + .25 * pulse;
        p.mat.uniforms.plein.value = xray ? .035 : .14;
        p.matAretes.opacity = Math.min(1, f * ((xray ? .95 : .6) + .4 * sel + .2 * sur + .4 * pulse));
        p.matAretes.color.copy(p.couleur).lerp(BLANC, .5 * Math.max(sel, pulse * .6));
        p.maillage.visible = f > .002;
        p.maillage.scale.setScalar(.82 + .18 * (1 - (1 - app) ** 3));
        if (origine && p === bloc) {
          // Le modèle du fichier est opaque : il ne peut pas venir en fondu comme l'hologramme. Il grandit à sa place
          // (et rapetisse quand la scène se ferme ou qu'on le masque) ; son hologramme, lui, ne se dessine pas.
          p.maillage.visible = false;
          origine.objet.visible = f > .02;
          origine.objet.scale.setScalar(Math.max(.02, Math.min(1, f)));
        }
      }
      if (origine?.avancer(dt)) bouge = true;
      return bouge;
    },
    eclater(on) { eclCible = on ? 1 : 0; origine?.eclater?.(on); },
    animer(on) { anime = on; },
    interieur(on) { xray = on; origine?.interieur?.(on); },
    // En vue d'origine, une pièce que le fichier décrit lui-même se désigne par ses propres triangles (au clic seulement).
    choisirFichier(ray) { return origine && bloc && bloc.visCible >= 1 ? origine.choisir?.(ray) ?? null : null; },
    origine: poserOrigine,
    animation(rang) { origine?.jouer(rang); },
    visibles(masquees, isolees) {
      for (const p of pieces) p.visCible = masquees.includes(p.d.id) ? 0 : isolees && !isolees.includes(p.d.id) ? .1 : 1;
    },
    selection(id) { choisie = id; },
    survol(id) { survolee = id; },
    signaler(ids) { signal.clear(); for (const id of ids) signal.add(id); tSignal = ids.length ? 3.2 : 0; },
    choisir(ray) {
      // En vue d'origine, le bloc se vise par sa forme en hologramme (cachée, mais à la même place) : les triangles
      // d'un modèle animé ne sont pas parcourus à chaque mouvement de souris.
      const cibles = pieces.filter(p => p.visCible >= 1 && (p.maillage.visible || (origine !== null && p === bloc))).map(p => p.maillage);
      const id = ray.intersectObjects(cibles, false)[0]?.object.userData.piece;
      return typeof id === 'string' ? id : null;
    },
    englobant(ids) {
      const choix = ids.map(id => parId.get(id)).filter((p): p is Piece => !!p);
      if (!choix.length) return null;
      racine.updateMatrixWorld(true);
      const b = new THREE.Box3(), s = new THREE.Sphere();
      for (const p of choix) b.union(new THREE.Box3().setFromObject(p.maillage));
      b.getBoundingSphere(s);
      return { centre: s.center.clone(), rayon: s.radius };
    },
    liberer() { poserOrigine(null); racine.removeFromParent(); libererObjets(racine); },   // le modèle d'origine n'est pas à la scène
  };
}
