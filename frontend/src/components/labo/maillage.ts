// Le laboratoire 3D de l'accueil : un modèle 3D donné par ID Studio IA (STL, GLB, OBJ ; reviews/accueil-holographique.md,
// phase 7). Ce module n'est chargé que pour une scène importée. Il lit le fichier gardé par le service, n'en retient
// que la forme (ni textures, ni matières, ni rien hors du fichier), la met debout, centrée, posée sur le sol et à la
// taille dite par la scène, puis la range dans `maillages` (scene.ts) : la construction de la scène reste immédiate.
import * as THREE from 'three';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import type { SceneDescription, SceneForme } from '../../lib/types';
import { accorderFaces, geometrieGlb } from '../hologram/glb';
import { maillages, quartsPoses } from './scene';

type FormeMaillage = Extract<SceneForme, { type: 'maillage' }>;
/** Au-delà, le navigateur peine à afficher : le modèle est refusé avec sa raison, pas montré à moitié. */
const MAX_SOMMETS = 6_000_000;

/** La forme seule d'un fichier 3D, dans son repère d'origine. Les lecteurs STL et OBJ sont ceux de three.js. */
function lireForme(fichier: string, donnees: ArrayBuffer): THREE.BufferGeometry {
  const ext = fichier.slice(fichier.lastIndexOf('.') + 1).toLowerCase();
  if (ext === 'glb') return geometrieGlb(THREE, donnees);
  const g = new THREE.BufferGeometry();
  if (ext === 'stl') {
    g.setAttribute('position', new STLLoader().parse(donnees).getAttribute('position'));
    return g;
  }
  if (ext !== 'obj') throw new Error('type de modèle 3D non lu (STL, GLB ou OBJ)');
  // Un fichier OBJ peut porter plusieurs objets : leurs triangles sont réunis (les lignes et les points sont ignorés).
  const morceaux: ArrayLike<number>[] = [];
  let nombres = 0;
  new OBJLoader().parse(new TextDecoder().decode(donnees)).traverse(o => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    const pos = (m.geometry.index ? m.geometry.toNonIndexed() : m.geometry).getAttribute('position');
    if (!pos) return;
    morceaux.push(pos.array); nombres += pos.array.length;
    if (nombres > MAX_SOMMETS * 3) throw new Error('modèle trop détaillé pour être affiché');
  });
  const reunis = new Float32Array(nombres);
  let o = 0;
  for (const m of morceaux) { reunis.set(m, o); o += m.length; }
  g.setAttribute('position', new THREE.BufferAttribute(reunis, 3));
  return g;
}

/** Met la forme comme la scène la veut : debout (un STL tient le plus souvent selon z), centrée, posée sur le sol,
 *  son plus grand côté à la taille dite. Les faces sont d'abord mises dans le même sens (`accorderFaces`, hologram/glb.ts :
 *  celles d'un modèle de Pixal3D sont tournées au hasard), puis les normales sont recalculées d'après les triangles
 *  (celles d'un fichier sont parfois fausses ou absentes). */
function poser(g: THREE.BufferGeometry, f: FormeMaillage, grandCote: boolean): number {
  const pos = g.getAttribute('position');
  if (!pos || pos.count < 3) throw new Error('aucune forme lisible dans ce fichier');
  if (pos.count > MAX_SOMMETS) throw new Error('modèle trop détaillé pour être affiché');
  // L'axe du fichier qui doit pointer vers le haut (« Redresser » les essaie tous les six) : toujours une rotation,
  // jamais un miroir, donc la forme n'est pas inversée.
  switch (f.debout ?? 'y') {
    case 'z': g.rotateX(-Math.PI / 2); break;
    case '-z': g.rotateX(Math.PI / 2); break;
    case '-y': g.rotateX(Math.PI); break;
    case 'x': g.rotateZ(Math.PI / 2); break;
    case '-x': g.rotateZ(-Math.PI / 2); break;
  }
  g.computeBoundingBox();
  const b = g.boundingBox!, cote = b.getSize(new THREE.Vector3()), grand = Math.max(cote.x, cote.y, cote.z);
  if (!Number.isFinite(grand) || !(grand > 0)) throw new Error('ce modèle n’a pas de dimensions lisibles');
  // Plus profond que large : il est tourné d'un quart de tour sur lui-même, pour montrer son grand côté (sa forme ne
  // change pas). Une pièce longue posée dans la profondeur se voyait par le bout, toute petite. Pas pour une forme
  // reconstruite depuis une photo (`grandCote` faux) : elle garde le côté photographié vers l'observateur. Et plus
  // du tout dès qu’ID Studio IA a tourné le modèle lui-même (`quart` gardé par le service) : c'est son choix qui compte.
  const quart = f.quart ?? (grandCote && cote.z > cote.x * 1.05 ? 1 : 0);
  if (quart) { g.rotateY(quart * Math.PI / 2); g.computeBoundingBox(); }
  g.translate(-(b.min.x + b.max.x) / 2, -b.min.y, -(b.min.z + b.max.z) / 2);
  const k = f.taille / grand;
  g.scale(k, k, k);
  accorderFaces(g);
  g.computeVertexNormals();
  g.computeBoundingBox(); g.computeBoundingSphere();
  return quart;
}

/** Lit les fichiers 3D qu'une scène demande et qui ne sont pas déjà en mémoire. `lire` rend le contenu d'un fichier
 *  gardé par le service (api.labModel). Une erreur dit pourquoi, en français : la scène n'est alors pas construite. */
export async function preparerMaillages(d: SceneDescription, lire: (fichier: string) => Promise<ArrayBuffer>): Promise<void> {
  for (const p of d.pieces) {
    const f = p.forme;
    if (f.type !== 'maillage' || maillages.has(f.fichier)) continue;
    const g = lireForme(f.fichier, await lire(f.fichier));
    quartsPoses.set(f.fichier, poser(g, f, d.origine !== 'reconstruit'));
    maillages.set(f.fichier, g);
  }
}
