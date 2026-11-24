// Le laboratoire 3D de l'accueil : la vue d'origine d'un modèle importé en GLB (12/11/2026, ID Studio IA : « un système avec une
// icône pour les modèles 3D déjà construits en glb avec les textures, une vue (modèle 3D origine) … et si on peut
// ajouter les animations ça serait super aussi pour ceux qui en ont déjà »). L'hologramme (maillage.ts) ne garde que la
// forme du fichier ; ici le fichier est montré tel quel : ses maillages, ses matières, ses textures, son squelette et
// ses animations. Ils sont lus par le lecteur de three.js (GLTFLoader) : un squelette et ses animations ne se lisent
// pas en quelques lignes comme la forme seule (hologram/glb.ts). Ce module n'est chargé que quand la vue est demandée.
// Sécurité : le fichier vient de l'utilisateur, et rien dedans ne déclenche une requête. `contenuGlb` refuse d'abord
// toute adresse hors du fichier ; le lecteur ne reçoit ensuite qu'un gestionnaire fermé, qui ne laisse passer que ce
// qui est écrit dans le fichier lui-même. Rien du fichier n'est exécuté, et aucun décodeur n'est téléchargé.
// Honnêteté : c'est le fichier tel qu'il a été importé, AURORA ne l'a ni construit ni vérifié. Une animation lue est
// celle du fichier, jamais un mouvement calculé ici ; seul l'éclairage est d'AURORA.
import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { SceneForme } from '../../lib/types';
import { contenuGlb, type ContenuGlb } from '../hologram/glb';
import { libererObjets, type PieceFichier, type VueOrigine } from './scene';

type FormeMaillage = Extract<SceneForme, { type: 'maillage' }>;

export interface ModeleOrigine extends VueOrigine {
  /** Ce que le fichier contient : ses matières et ses textures (comptées dans sa description) et le nom de chaque
   *  animation qui fait bouger quelque chose, dans l'ordre où `jouer` les numérote. */
  readonly contenu: ContenuGlb;
  /** Pose le modèle comme sa forme en hologramme : `f` vient de la scène, `quart` est le quart de tour réellement
   *  appliqué à la forme (scene.ts, quartsPoses). La lecture s'arrête : la mesure se fait dans la pose du fichier. */
  poser(f: FormeMaillage, quart: number): void;
  /** Rend la mémoire graphique : maillages, matières, textures, squelettes. */
  liberer(): void;
}

/** Le seul gestionnaire donné au lecteur de three.js. Une adresse n'y passe que si elle désigne un contenu du fichier
 *  lui-même : « data: » (écrit dans la description) ou « blob: » (une image du fichier, que le lecteur ouvre ainsi).
 *  Toute autre adresse arrête la lecture avant la moindre requête. */
export function gestionnaireFerme(): THREE.LoadingManager {
  const g = new THREE.LoadingManager();
  g.setURLModifier(url => {
    if (/^(?:data|blob):/i.test(url)) return url;
    throw new Error('adresse hors du fichier refusée : rien n’est chargé hors du fichier');
  });
  return g;
}

const AXE_X = new THREE.Vector3(1, 0, 0), AXE_Y = new THREE.Vector3(0, 1, 0), AXE_Z = new THREE.Vector3(0, 0, 1);
/** La rotation qui met debout l'axe dit du fichier : la même que pour la forme en hologramme (maillage.ts, poser). */
function redresser(debout: FormeMaillage['debout']): THREE.Quaternion {
  const q = new THREE.Quaternion();
  switch (debout ?? 'y') {
    case 'z': return q.setFromAxisAngle(AXE_X, -Math.PI / 2);
    case '-z': return q.setFromAxisAngle(AXE_X, Math.PI / 2);
    case '-y': return q.setFromAxisAngle(AXE_X, Math.PI);
    case 'x': return q.setFromAxisAngle(AXE_Z, Math.PI / 2);
    case '-x': return q.setFromAxisAngle(AXE_Z, -Math.PI / 2);
    default: return q;
  }
}

/** Lit le fichier GLB d'un modèle importé (ses octets, rendus par api.labModel) et le pose comme sa forme en
 *  hologramme. Une erreur dit pourquoi, en français : la vue d'origine n'est alors pas montrée. */
export async function lireOrigine(donnees: ArrayBuffer, f: FormeMaillage, quart: number): Promise<ModeleOrigine> {
  const decrit = contenuGlb(donnees);   // refuse d'abord toute adresse hors du fichier
  const gltf = await new Promise<GLTF>((fait, refuse) => {
    new GLTFLoader(gestionnaireFerme()).parse(donnees, '', fait, (e: unknown) => {
      refuse(new Error(`ce fichier GLB n’a pas pu être lu (${e instanceof Error ? e.message : 'erreur du lecteur'})`));
    });
  });
  const modele: THREE.Object3D | undefined = gltf.scene ?? gltf.scenes[0];
  if (!modele) throw new Error('aucune scène dans ce fichier GLB');

  // `objet` : ce que la scène place et fait grandir (scene.ts). `porte` : la pose du fichier (debout, quart de tour,
  // centré, sur le sol, à la taille dite).
  const objet = new THREE.Group(), porte = new THREE.Group();
  objet.add(porte); porte.add(modele);
  const matieres = new Set<THREE.Material>(), textures = new Set<THREE.Texture>(), squelettes = new Set<THREE.Skeleton>();
  modele.traverse(o => {
    const m = o as THREE.SkinnedMesh;
    // Un maillage à squelette sort de sa boîte de départ quand il bouge : il est toujours dessiné.
    if (m.isSkinnedMesh) { m.frustumCulled = false; squelettes.add(m.skeleton); }
    for (const mat of Array.isArray(m.material) ? m.material : m.material ? [m.material] : []) {
      matieres.add(mat);
      // 12/11/2026 (ID Studio IA : « quand on tourne les modèles 3D en glb on voit à travers, vu de dos ») : une matière que le
      // fichier dit transparente (alphaMode BLEND : Blender l'écrit dès que la texture a une couche alpha, même pleine)
      // est lue par three.js sans écriture de la profondeur ; les faces de derrière et de l'intérieur se dessinent
      // alors par-dessus celles de devant. Ici la profondeur est écrite : une face cachée reste cachée. Un point
      // presque invisible (alpha sous 2 %) n'est pas dessiné, donc ne cache rien ; un point à demi transparent reste
      // mêlé à ce qui est déjà dessiné derrière lui.
      if (mat.transparent && !mat.depthWrite) { mat.depthWrite = true; mat.alphaTest = Math.max(mat.alphaTest, .02); }
      for (const v of Object.values(mat)) if ((v as THREE.Texture | null)?.isTexture) textures.add(v as THREE.Texture);
    }
  });

  // 12/11/2026 (ID Studio IA : « couper le modèle et mettre les pièces dedans ») : un fichier fait de plusieurs pièces peut
  // s'écarter. Un nœud qui porte « extras.aurora.eclate » (écrit par docs/robot-quadrupede/coque_glb.py) dit son écart :
  // trois nombres, en parts de la plus grande dimension du modèle, dans le repère du fichier. À défaut, un fichier d'au
  // moins deux maillages sans squelette s'écarte de lui-même : chaque maillage s'éloigne du centre du modèle. L'écart
  // est gardé dans le repère du parent du nœud : une pièce accrochée à un os le suit quand l'animation joue.
  const ecartables: { noeud: THREE.Object3D; base: THREE.Vector3; ecart: THREE.Vector3 }[] = [];
  modele.updateMatrixWorld(true);
  {
    const tout = new THREE.Box3().setFromObject(modele, true), dimension = Math.max(...tout.getSize(new THREE.Vector3()).toArray());
    const versParent = (o: THREE.Object3D, v: THREE.Vector3) =>
      (o.parent ? v.applyMatrix3(new THREE.Matrix3().setFromMatrix4(o.parent.matrixWorld).invert()) : v);
    const ditEcart = (o: THREE.Object3D): number[] | null => {
      const e = (o.userData as { aurora?: { eclate?: unknown } }).aurora?.eclate;
      return Array.isArray(e) && e.length === 3 && e.every(v => typeof v === 'number' && Number.isFinite(v)) ? e as number[] : null;
    };
    const dits: THREE.Object3D[] = [], rigides: THREE.Mesh[] = [];
    modele.traverse(o => {
      if (ditEcart(o)) dits.push(o);
      const m = o as THREE.SkinnedMesh;
      if (m.isMesh && !m.isSkinnedMesh) rigides.push(m);
    });
    if (Number.isFinite(dimension) && dimension > 0) {
      for (const o of dits) {
        const e = ditEcart(o)!;
        ecartables.push({ noeud: o, base: o.position.clone(), ecart: versParent(o, new THREE.Vector3(e[0], e[1], e[2]).multiplyScalar(dimension)) });
      }
      if (!dits.length && rigides.length > 1) {
        const centre = tout.getCenter(new THREE.Vector3()), b = new THREE.Box3();
        for (const m of rigides) {
          const versDehors = b.setFromObject(m).getCenter(new THREE.Vector3()).sub(centre).multiplyScalar(.8);
          ecartables.push({ noeud: m, base: m.position.clone(), ecart: versParent(m, versDehors) });
        }
      }
    }
  }
  let ecartCible = 0, ecartement = 0;
  const ecarter = () => { for (const p of ecartables) p.noeud.position.copy(p.base).addScaledVector(p.ecart, ecartement); };

  // La fiche d'une pièce et la coque (12/11/2026, ID Studio IA : « la fiche au clic et la coque transparente », à la façon de
  // sa page VITA). « extras.aurora » d'un nœud dit son nom, son genre (« coque » ou « piece »), son rôle et ses
  // caractéristiques. Ce sont des textes du fichier : bornés, montrés tels quels, jamais exécutés.
  const texteDit = (v: unknown, max: number): string => (typeof v === 'string' ? v.replace(/[\u0000-\u001f]+/g, ' ').trim().slice(0, max) : '');
  const ficheDe = (o: THREE.Object3D): PieceFichier | null => {
    const a = (o.userData as { aurora?: Record<string, unknown> | null }).aurora;
    if (!a || typeof a !== 'object') return null;
    const nom = texteDit(a.nom, 80);
    if (!nom) return null;
    const proprietes = (Array.isArray(a.proprietes) ? a.proprietes as unknown[] : []).slice(0, 32)
      .map(p => ({ nom: texteDit((p as { nom?: unknown } | null)?.nom, 40), valeur: texteDit((p as { valeur?: unknown } | null)?.valeur, 80) }))
      .filter(p => p.nom && p.valeur).slice(0, 8);                 // huit au plus, comme une pièce d'une scène
    return { nom, genre: a.genre === 'coque' || a.genre === 'piece' ? a.genre : null, role: texteDit(a.role, 600), proprietes };
  };
  // Les matières de la coque, et ce qu'elles étaient avant d'être rendues transparentes.
  const coque = new Map<THREE.Material, { transparent: boolean; opacity: number; depthWrite: boolean }>();
  modele.traverse(o => {
    if (ficheDe(o)?.genre !== 'coque') return;
    o.traverse(m => {
      const mats = (m as THREE.Mesh).material;
      for (const mat of Array.isArray(mats) ? mats : mats ? [mats] : []) coque.set(mat, { transparent: mat.transparent, opacity: mat.opacity, depthWrite: mat.depthWrite });
    });
  });
  let transparente = false;
  const touches: THREE.Intersection[] = [];

  // Les animations du fichier, sous le nom qu'il leur donne ; celles qui ne font rien bouger ne sont pas proposées.
  const mixeur = new THREE.AnimationMixer(modele);
  const animations = gltf.animations.map((clip, i) => ({ clip, nom: decrit.animations[i] ?? '' })).filter(a => a.clip.tracks.length > 0);
  let lue: THREE.AnimationAction | null = null;
  const jouer = (rang: number | null) => {
    mixeur.stopAllAction(); lue = null;   // le modèle revient à la pose du fichier
    const a = rang === null ? undefined : animations[rang];
    if (a) lue = mixeur.clipAction(a.clip).reset().play();
  };

  const boite = new THREE.Box3(), cote = new THREE.Vector3();
  const poser = (forme: FormeMaillage, q: number) => {
    jouer(null);
    const ecartGarde = ecartement;
    ecartement = 0; ecarter();   // mesuré pièces remontées : la vue éclatée ne change pas la taille du modèle
    porte.quaternion.setFromAxisAngle(AXE_Y, q * Math.PI / 2).multiply(redresser(forme.debout));
    porte.position.set(0, 0, 0); porte.scale.setScalar(1);
    // Mesuré hors de la scène (où le modèle tourne et grandit), sommet par sommet, squelette compris : la même boîte
    // que celle de la forme en hologramme.
    objet.remove(porte);
    porte.updateMatrixWorld(true);
    boite.setFromObject(porte, true);
    objet.add(porte);
    const grand = Math.max(...boite.getSize(cote).toArray());
    if (boite.isEmpty() || !Number.isFinite(grand) || !(grand > 0)) throw new Error('ce modèle n’a pas de dimensions lisibles');
    const k = forme.taille / grand;
    porte.scale.setScalar(k);
    porte.position.set(-(boite.min.x + boite.max.x) / 2 * k, -boite.min.y * k, -(boite.min.z + boite.max.z) / 2 * k);
    ecartement = ecartGarde; ecarter();
  };

  // L'éclairage, simple et fixe : un peu de jour partout et une lampe en haut, du côté d'où la scène est regardée.
  const lumieres = new THREE.Group(), lampe = new THREE.DirectionalLight(0xffffff, 2.5);
  lampe.position.set(2.5, 5, 4);
  lumieres.add(new THREE.AmbientLight(0xffffff, .3), lampe);
  let reflets: THREE.WebGLRenderTarget | null = null, prepare = false;

  const liberer = () => {
    mixeur.stopAllAction(); mixeur.uncacheRoot(modele);
    objet.removeFromParent(); lumieres.removeFromParent();
    libererObjets(objet);   // géométries et matières
    for (const t of textures) { t.dispose(); (t.image as { close?: () => void } | null)?.close?.(); }
    for (const s of squelettes) s.dispose();
    reflets?.dispose(); reflets = null;
  };
  try { poser(f, quart); } catch (e) { liberer(); throw e; }

  return {
    objet, lumieres,
    contenu: {
      matieres: decrit.matieres, textures: decrit.textures, animations: animations.map(a => a.nom),
      ...(ecartables.length > 1 ? { pieces: ecartables.length } : {}),
      ...(coque.size > 0 ? { coque: true } : {}),
    },
    eclater(on) { ecartCible = on ? 1 : 0; },
    choisir(rayon) {
      touches.length = 0;
      rayon.intersectObject(objet, true, touches);           // de la plus proche à la plus lointaine
      let derriere: PieceFichier | null = null;
      for (const t of touches) {
        let fiche: PieceFichier | null = null;
        for (let o: THREE.Object3D | null = t.object; o && !fiche; o = o.parent) fiche = ficheDe(o);
        if (!fiche) continue;
        // À travers une coque transparente, on vise ce qu'elle contient ; elle-même, seulement s'il n'y a rien derrière.
        if (transparente && fiche.genre === 'coque') { derriere ??= fiche; continue; }
        return fiche;
      }
      return derriere;
    },
    interieur(on) {
      transparente = on;
      for (const [mat, avant] of coque) {
        mat.transparent = on || avant.transparent;
        mat.opacity = on ? .16 : avant.opacity;
        mat.depthWrite = on ? false : avant.depthWrite;
        mat.needsUpdate = true;
      }
    },
    preparer(rendu) {
      if (prepare) return;
      prepare = true;
      const finesse = rendu.capabilities.getMaxAnisotropy();
      for (const t of textures) t.anisotropy = finesse;   // une texture vue de biais reste nette
      // Une matière de fichier (métal, vernis) ne se lit que si elle a quelque chose à refléter : la pièce claire et
      // neutre des exemples de three.js, dessinée ici (aucune image n'est chargée). Sans elle, le jour est renforcé.
      try {
        const pmrem = new THREE.PMREMGenerator(rendu), piece = new RoomEnvironment(rendu);
        reflets = pmrem.fromScene(piece, .04);
        pmrem.dispose(); piece.dispose();
        for (const m of matieres) {
          const mat = m as THREE.MeshStandardMaterial;
          if (mat.isMeshStandardMaterial) { mat.envMap = reflets.texture; mat.needsUpdate = true; }
        }
      } catch (e) {
        console.warn('Vue d’origine : reflets non préparés, éclairage simple seul.', e);
        lumieres.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.5));
      }
    },
    jouer,
    avancer(dt) {
      let bouge = false;
      if (ecartement !== ecartCible) {                       // les pièces s'écartent ou remontent, en douceur
        ecartement += (ecartCible - ecartement) * (1 - Math.exp(-dt * 3.2));
        if (Math.abs(ecartCible - ecartement) < .0005) ecartement = ecartCible;
        ecarter(); bouge = true;
      }
      if (lue) { mixeur.update(dt); bouge = true; }
      return bouge;
    },
    poser, liberer,
  };
}
