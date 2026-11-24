// Globe hologramme : le lecteur GLB maison d'Athéna (HologrammeTerre.tsx, 04/10/2026).
// Il lit un .glb produit par scripts/alleger-modele-globe.py d'Athéna : un maillage, une primitive,
// POSITION / TEXCOORD_0 / indices, texture de couleur en JPEG. Modèles faits par ID Studio IA avec Meshy,
// allégés de 93 Mo à 3,1 Mo (public/terre/modeles). Pas besoin du GLTFLoader de three.js pour si peu.
import type * as Trois from 'three';

type Three = typeof Trois;

interface Gltf {
  bufferViews: { byteOffset?: number; byteLength: number }[];
  accessors: { bufferView: number; byteOffset?: number; count: number; componentType: number }[];
  meshes: { primitives: { attributes: Record<string, number>; indices: number }[] }[];
  materials?: { pbrMetallicRoughness?: { baseColorTexture?: { index: number } } }[];
  textures?: { source: number }[];
  images?: { bufferView: number; mimeType: string }[];
}

type Tableau = Float32ArrayConstructor | Uint32ArrayConstructor | Uint16ArrayConstructor;

export interface Modele { geometrie: Trois.BufferGeometry; texture: Trois.Texture | null }

export async function chargerModele(THREE: Three, url: string): Promise<Modele> {
  const rep = await fetch(url);
  if (!rep.ok) throw new Error(`${url} : ${rep.status}`);
  const b = await rep.arrayBuffer(), dv = new DataView(b);
  if (b.byteLength < 28 || dv.getUint32(0, true) !== 0x46546c67) throw new Error('pas un glb'); // « glTF »
  const lj = dv.getUint32(12, true), j = JSON.parse(new TextDecoder().decode(new Uint8Array(b, 20, lj))) as Gltf;
  const bin = 20 + lj + 8; // en-tête (12) + bloc JSON (8 + lj) + en-tête du bloc binaire (8)
  const vue = (i: number) => ({ debut: bin + (j.bufferViews[i].byteOffset ?? 0), longueur: j.bufferViews[i].byteLength });
  const lire = (i: number, Type: Tableau, n: number) => {
    const a = j.accessors[i];
    return new Type(b, vue(a.bufferView).debut + (a.byteOffset ?? 0), a.count * n);
  };
  const p = j.meshes[0].primitives[0], g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(lire(p.attributes.POSITION, Float32Array, 3), 3));
  if (p.attributes.TEXCOORD_0 != null) g.setAttribute('uv', new THREE.BufferAttribute(lire(p.attributes.TEXCOORD_0, Float32Array, 2), 2));
  // Les normales du fichier, quand il en a (11/11/2026, les formes du noyau : labo/formes.ts) : le liseré de lumière en dépend.
  if (p.attributes.NORMAL != null) g.setAttribute('normal', new THREE.BufferAttribute(lire(p.attributes.NORMAL, Float32Array, 3), 3));
  g.setIndex(new THREE.BufferAttribute(lire(p.indices, j.accessors[p.indices].componentType === 5125 ? Uint32Array : Uint16Array, 1), 1));
  const ref = j.materials?.[0]?.pbrMetallicRoughness?.baseColorTexture;
  let texture: Trois.Texture | null = null;
  const source = ref && j.textures?.[ref.index]?.source, img = source != null ? j.images?.[source] : undefined;
  if (img) {
    const v = vue(img.bufferView);
    const bmp = await createImageBitmap(new Blob([new Uint8Array(b, v.debut, v.longueur)], { type: img.mimeType }));
    texture = new THREE.Texture(bmp);
    texture.flipY = false; texture.colorSpace = THREE.SRGBColorSpace; texture.needsUpdate = true;
  }
  return { geometrie: g, texture };
}

// ── 10/11/2026 : la forme seule d'un .glb quelconque (un modèle 3D donné par ID Studio IA au laboratoire de l'accueil,
// labo/maillage.ts). Le lecteur ci-dessus ne lit que les fichiers allégés du globe (un maillage, une primitive) ;
// celui-ci réunit tous les maillages du fichier, chacun à sa place, en une seule géométrie. Textures et matières sont
// ignorées : le noyau dessine en hologramme. Rien n'est chargé hors du fichier (un .glb qui renvoie à d'autres
// fichiers est refusé), et un fichier compressé (Draco, Meshopt) est dit tel, pas deviné.
interface GltfForme {
  scene?: number; scenes?: { nodes?: number[] }[];
  nodes?: { mesh?: number; children?: number[]; matrix?: number[]; translation?: number[]; rotation?: number[]; scale?: number[] }[];
  meshes?: { primitives: { attributes: Record<string, number>; indices?: number; mode?: number; extensions?: Record<string, unknown> }[] }[];
  accessors?: { bufferView?: number; byteOffset?: number; count: number; componentType: number; sparse?: unknown }[];
  bufferViews?: { buffer: number; byteOffset?: number; byteLength: number; byteStride?: number }[];
  extensionsRequired?: string[];
}
const FLOTTANT = 5126, MAX_SOMMETS = 6_000_000, COMPRESSE = /draco|meshopt|quantization/i;

/** L'en-tête d'un .glb et son bloc JSON (la description du modèle), pour `geometrieGlb` et `contenuGlb`. Rien d'autre
 *  n'est lu ici : ni maillage, ni image. */
function jsonGlb(b: ArrayBuffer): { j: unknown; dv: DataView; debutBin: number } {
  const dv = new DataView(b);
  if (b.byteLength < 20 || dv.getUint32(0, true) !== 0x46546c67) throw new Error('ce n’est pas un fichier GLB');
  const lj = dv.getUint32(12, true), debutBin = 20 + lj;
  if (dv.getUint32(16, true) !== 0x4e4f534a || debutBin > b.byteLength) throw new Error('fichier GLB illisible');
  try { return { j: JSON.parse(new TextDecoder().decode(new Uint8Array(b, 20, lj))), dv, debutBin }; }
  catch { throw new Error('fichier GLB illisible'); }   // une description qui n'est pas du JSON : dit en français
}

export function geometrieGlb(THREE: Three, b: ArrayBuffer): Trois.BufferGeometry {
  const lu = jsonGlb(b), dv = lu.dv, debutBin = lu.debutBin, j = lu.j as GltfForme;
  const refuse = (j.extensionsRequired ?? []).find(e => COMPRESSE.test(e));
  if (refuse) throw new Error(`modèle compressé (${refuse}) : ce format n’est pas lu`);
  if (debutBin + 8 > b.byteLength || dv.getUint32(debutBin + 4, true) !== 0x004e4942) throw new Error('ce fichier GLB renvoie à d’autres fichiers : ils ne sont pas chargés');
  const bin = debutBin + 8, finBin = bin + dv.getUint32(debutBin, true);
  if (finBin > b.byteLength) throw new Error('fichier GLB tronqué');
  /** Les nombres d'un accesseur, quel que soit leur rangement (côte à côte ou entrelacés avec d'autres données). */
  const lire = (i: number, composantes: number): Float32Array | Uint32Array => {
    const a = j.accessors?.[i], v = a && a.bufferView != null ? j.bufferViews?.[a.bufferView] : undefined;
    if (!a || !v || v.buffer !== 0 || a.sparse || !(a.count > 0)) throw new Error('fichier GLB illisible (données absentes)');
    const t = a.componentType, taille = t === FLOTTANT || t === 5125 ? 4 : t === 5123 ? 2 : t === 5121 ? 1 : 0;
    if (!taille) throw new Error('fichier GLB : type de nombres non lu');
    const pas = v.byteStride || taille * composantes, debut = bin + (v.byteOffset ?? 0) + (a.byteOffset ?? 0);
    if (debut + (a.count - 1) * pas + taille * composantes > finBin) throw new Error('fichier GLB tronqué');
    const out = t === FLOTTANT ? new Float32Array(a.count * composantes) : new Uint32Array(a.count * composantes);
    for (let k = 0; k < a.count; k++) for (let c = 0; c < composantes; c++) {
      const o = debut + k * pas + c * taille;
      out[k * composantes + c] = t === FLOTTANT ? dv.getFloat32(o, true) : t === 5125 ? dv.getUint32(o, true) : t === 5123 ? dv.getUint16(o, true) : dv.getUint8(o);
    }
    return out;
  };
  const positions: Float32Array[] = [], faces: Uint32Array[] = [], v = new THREE.Vector3();
  let sommets = 0;
  const visiter = (n: number, parent: Trois.Matrix4, profondeur: number) => {
    const noeud = j.nodes?.[n];
    if (!noeud || profondeur > 64) return;
    const locale = new THREE.Matrix4();
    if (noeud.matrix?.length === 16) locale.fromArray(noeud.matrix);
    else locale.compose(new THREE.Vector3().fromArray(noeud.translation ?? [0, 0, 0]), new THREE.Quaternion().fromArray(noeud.rotation ?? [0, 0, 0, 1]), new THREE.Vector3().fromArray(noeud.scale ?? [1, 1, 1]));
    const monde = parent.clone().multiply(locale);
    for (const p of (noeud.mesh != null ? j.meshes?.[noeud.mesh]?.primitives : undefined) ?? []) {
      if ((p.mode ?? 4) !== 4 || p.attributes?.POSITION == null) continue;   // des triangles seulement
      if (Object.keys(p.extensions ?? {}).some(e => COMPRESSE.test(e))) throw new Error('modèle compressé (Draco) : ce format n’est pas lu');
      if (j.accessors?.[p.attributes.POSITION]?.componentType !== FLOTTANT) throw new Error('modèle compressé (positions entières) : ce format n’est pas lu');
      const pos = lire(p.attributes.POSITION, 3) as Float32Array, nb = pos.length / 3;
      for (let k = 0; k < pos.length; k += 3) v.fromArray(pos, k).applyMatrix4(monde).toArray(pos, k);
      const idx = p.indices != null ? Uint32Array.from(lire(p.indices, 1)) : Uint32Array.from({ length: nb }, (_, k) => k);
      for (let k = 0; k < idx.length; k++) { if (idx[k] >= nb) throw new Error('fichier GLB illisible (face hors des sommets)'); idx[k] += sommets; }
      sommets += nb;
      if (sommets > MAX_SOMMETS) throw new Error('modèle trop détaillé pour être affiché');
      positions.push(pos); faces.push(idx.subarray(0, idx.length - idx.length % 3));
    }
    for (const enfant of noeud.children ?? []) visiter(enfant, monde, profondeur + 1);
  };
  const tous = (j.nodes ?? []).map((_, i) => i);
  const racines = j.scenes?.[j.scene ?? 0]?.nodes ?? tous.filter(i => !(j.nodes ?? []).some(x => x.children?.includes(i)));
  for (const r of racines) visiter(r, new THREE.Matrix4(), 0);
  if (!sommets) throw new Error('aucune forme lisible dans ce fichier GLB');
  const reunis = new Float32Array(sommets * 3), index = new Uint32Array(faces.reduce((s, f) => s + f.length, 0));
  let op = 0, oi = 0;
  for (const p of positions) { reunis.set(p, op); op += p.length; }
  for (const f of faces) { index.set(f, oi); oi += f.length; }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(reunis, 3));
  g.setIndex(new THREE.BufferAttribute(index, 1));
  return g;
}

// ── 12/11/2026 : ce qu'un .glb dit contenir, pour la vue d'origine d'un modèle importé (labo/origine.ts : le fichier
// montré tel quel, avec ses matières, ses textures et ses animations, par le lecteur de three.js). Seul le bloc JSON
// est lu ici, AVANT de donner le fichier à ce lecteur : un .glb peut citer une image ou un tampon par une adresse
// (« uri »), que le lecteur irait chercher. Tout ce qui n'est pas dans le fichier lui-même est refusé, avec sa raison.
export interface ContenuGlb {
  /** Comptées dans la description du fichier. */
  matieres: number; textures: number;
  /** Le nom de chaque animation du fichier, dans l'ordre du fichier ('' : le fichier ne lui donne pas de nom). */
  animations: string[];
  /** 12/11/2026 : le nombre de pièces qui s'écartent en vue éclatée (labo/origine.ts). Absent pour un modèle en un
   *  seul bloc : le bouton « Éclater » ne lui est pas proposé. */
  pieces?: number;
  /** Vrai quand le fichier dit lesquelles de ses pièces sont sa coque : elle peut alors être rendue transparente. */
  coque?: boolean;
}
/** Ces formats demandent un décodeur à part, qu'AURORA n'a pas : le fichier est dit tel, pas deviné. */
const DECODEUR = /draco|meshopt|basisu/i;
const liste = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** La première adresse hors du fichier que cite cette description, ou null. Seul un contenu écrit dans le fichier
 *  lui-même (« data: ») est admis, où que soit le mot « uri » (tampons, images, ou une extension). */
function adresseExterne(racine: unknown): string | null {
  const pile: unknown[] = [racine];
  while (pile.length) {
    const x = pile.pop();
    if (typeof x !== 'object' || x === null) continue;
    for (const [cle, v] of Object.entries(x)) {
      if (cle !== 'uri') pile.push(v);
      else if (typeof v !== 'string' || !/^data:/i.test(v)) return typeof v === 'string' ? v : 'adresse illisible';
    }
  }
  return null;
}

export function contenuGlb(b: ArrayBuffer): ContenuGlb {
  const j = jsonGlb(b).j as { extensionsRequired?: unknown; materials?: unknown; textures?: unknown; animations?: unknown } | null;
  if (typeof j !== 'object' || j === null) throw new Error('fichier GLB illisible');
  const dehors = adresseExterne(j);
  if (dehors !== null) {
    throw new Error(`ce fichier GLB cite une adresse hors du fichier (« ${dehors.length > 60 ? `${dehors.slice(0, 60)}…` : dehors} ») : rien n’est chargé hors du fichier`);
  }
  const refuse = liste(j.extensionsRequired).find(e => typeof e === 'string' && DECODEUR.test(e));
  if (refuse) throw new Error(`modèle compressé (${refuse}) : ce format n’est pas lu`);
  return {
    matieres: liste(j.materials).length, textures: liste(j.textures).length,
    animations: liste(j.animations).map(a => {
      const nom = (a as { name?: unknown } | null)?.name;
      return typeof nom === 'string' ? nom.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 60) : '';
    }),
  };
}

/** Met les faces d'un modèle dans le même sens, de proche en proche (10/11/2026, ID Studio IA devant un avion fait par
 *  Pixal3D : « la couleur blanc ressort trop »). Les modèles de Pixal3D sortent avec des faces tournées au hasard d'un
 *  côté ou de l'autre (mesuré sur l'avion brut : un voisinage sur sept). Trois défauts en venaient :
 *    - une face qui tourne le dos n'est pas dessinée : la surface était trouée, comme pointillée ;
 *    - les normales des sommets s'annulaient entre faces opposées : la matière brillait au hasard ;
 *    - chaque frontière entre deux faces opposées était tracée comme une arête vive (80 000 traits de trop).
 *  On part d'une face et on gagne ses voisines par les arêtes que deux faces seulement partagent ; dans chaque îlot
 *  ainsi parcouru, c'est le sens du plus grand nombre qui est gardé, et les autres faces sont retournées. Un fichier
 *  dont les faces sont déjà d'accord n'est donc jamais touché, même une surface ouverte faite pour être vue d'un seul
 *  côté. Rend le nombre de faces retournées — 0 aussi pour une forme dont les sommets ne sont pas partagés (STL,
 *  OBJ) : elle n'a aucun voisinage à lire.
 *  Appelée par labo/maillage.ts quand la forme est posée ; `geometrieGlb` rend le fichier tel qu'il est. */
export function accorderFaces(g: Trois.BufferGeometry): number {
  const index = g.index, pos = g.getAttribute('position');
  if (!index || !pos) return 0;
  const ix = index.array as Uint16Array | Uint32Array, n = Math.floor(ix.length / 3), sommets = pos.count;
  // Pour chaque demi-arête (le côté e de la face f, au rang 3f + e) : la demi-arête de la face voisine, ou -1.
  const jumelle = new Int32Array(n * 3).fill(-1), vues = new Map<number, number>();
  for (let h = 0; h < n * 3; h++) {
    const a = ix[h], b = ix[h - h % 3 + (h % 3 + 1) % 3];
    if (a === b) continue;
    const cle = a < b ? a * sommets + b : b * sommets + a, autre = vues.get(cle);
    if (autre === undefined) vues.set(cle, h);
    else if (autre >= 0 && jumelle[autre] < 0) { jumelle[autre] = h; jumelle[h] = autre; }
    else if (autre >= 0) { jumelle[jumelle[autre]] = -1; jumelle[autre] = -1; vues.set(cle, -1); }   // trois faces : on n'y passe pas
  }
  const sens = new Int8Array(n), file = new Int32Array(n);   // sens : 0 pas encore vue, 1 comme la première, -1 à l'opposé
  let retournees = 0;
  for (let depart = 0; depart < n; depart++) {
    if (sens[depart]) continue;
    let tete = 0, queue = 0, opposees = 0;
    sens[depart] = 1; file[queue++] = depart;
    while (tete < queue) {
      const f = file[tete++];
      if (sens[f] < 0) opposees++;
      for (let e = 0; e < 3; e++) {
        const j = jumelle[f * 3 + e];
        if (j < 0) continue;
        const voisine = Math.floor(j / 3);
        if (sens[voisine]) continue;
        // L'arête commune parcourue dans le même sens par les deux faces : elles se tournent le dos.
        sens[voisine] = ix[j] === ix[f * 3 + e] ? -sens[f] : sens[f];
        file[queue++] = voisine;
      }
    }
    const garde = opposees * 2 > queue ? -1 : 1;   // le sens du plus grand nombre ; à égalité, celui de la première face
    for (let i = 0; i < queue; i++) {
      const f = file[i];
      if (sens[f] !== garde) { const k = ix[f * 3 + 1]; ix[f * 3 + 1] = ix[f * 3 + 2]; ix[f * 3 + 2] = k; retournees++; }
      sens[f] = 1;
    }
  }
  if (retournees) index.needsUpdate = true;
  return retournees;
}
