// Tests du lecteur de forme GLB (laboratoire 3D, modèle importé : phase 7). Lancer depuis frontend :
//   node --experimental-strip-types --test tests/glb.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { accorderFaces, geometrieGlb } from '../src/components/hologram/glb.ts';
import { glb } from './aide-glb.mjs';

/** Un triangle (0,0,0) (1,0,0) (0,1,0) et ses trois indices sur 16 bits. */
function triangle() {
  const bin = new ArrayBuffer(44), dv = new DataView(bin);
  [0, 0, 0, 1, 0, 0, 0, 1, 0].forEach((v, i) => dv.setFloat32(i * 4, v, true));
  [0, 1, 2].forEach((v, i) => dv.setUint16(36 + i * 2, v, true));
  return bin;
}
const BASE = {
  meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
  accessors: [{ bufferView: 0, count: 3, componentType: 5126 }, { bufferView: 1, count: 3, componentType: 5123 }],
  bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 36 }, { buffer: 0, byteOffset: 36, byteLength: 6 }],
};
const nombres = a => Array.from(a, x => Math.round(x * 1000) / 1000);

test('tous les maillages du fichier sont réunis, chacun à sa place', () => {
  const g = geometrieGlb(THREE, glb({
    ...BASE, scene: 0, scenes: [{ nodes: [0] }],
    nodes: [{ children: [1, 2], scale: [2, 2, 2] }, { mesh: 0 }, { mesh: 0, translation: [1, 0, 0] }],
  }, triangle()));
  assert.deepEqual(nombres(g.getAttribute('position').array), [0, 0, 0, 2, 0, 0, 0, 2, 0, 2, 0, 0, 4, 0, 0, 2, 2, 0]);
  assert.deepEqual(Array.from(g.index.array), [0, 1, 2, 3, 4, 5]);
});

test('sans scène déclarée, les nœuds sans parent sont les racines', () => {
  const g = geometrieGlb(THREE, glb({ ...BASE, nodes: [{ mesh: 0, matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 5, 0, 1] }] }, triangle()));
  assert.deepEqual(nombres(g.getAttribute('position').array), [0, 5, 0, 1, 5, 0, 0, 6, 0]);
});

test('positions entrelacées avec d’autres données, sans indices', () => {
  const bin = new ArrayBuffer(72), dv = new DataView(bin);
  [[0, 0, 0], [3, 0, 0], [0, 0, 3]].forEach((p, k) => p.forEach((v, c) => { dv.setFloat32(k * 24 + c * 4, v, true); dv.setFloat32(k * 24 + 12 + c * 4, 9, true); }));
  const g = geometrieGlb(THREE, glb({
    nodes: [{ mesh: 0 }], meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
    accessors: [{ bufferView: 0, count: 3, componentType: 5126 }], bufferViews: [{ buffer: 0, byteLength: 72, byteStride: 24 }],
  }, bin));
  assert.deepEqual(nombres(g.getAttribute('position').array), [0, 0, 0, 3, 0, 0, 0, 0, 3]);
  assert.deepEqual(Array.from(g.index.array), [0, 1, 2]);
});

test('ce qui n’est pas lu est refusé avec sa raison, jamais deviné', () => {
  const noeud = { nodes: [{ mesh: 0 }] };
  assert.throws(() => geometrieGlb(THREE, new TextEncoder().encode('ceci n’est pas un modèle 3D').buffer), /pas un fichier GLB/);
  assert.throws(() => geometrieGlb(THREE, glb({ ...BASE, ...noeud, extensionsRequired: ['KHR_draco_mesh_compression'] }, triangle())), /compressé/);
  assert.throws(() => geometrieGlb(THREE, glb({ ...BASE, ...noeud }, null)), /d’autres fichiers/);   // rien n'est chargé hors du fichier
  assert.throws(() => geometrieGlb(THREE, glb({ ...BASE, nodes: [] }, triangle())), /aucune forme/);
  const hors = triangle();
  new DataView(hors).setUint16(40, 7, true);   // une face qui vise un sommet absent
  assert.throws(() => geometrieGlb(THREE, glb({ ...BASE, ...noeud }, hors)), /face hors des sommets/);
  const court = { ...BASE, ...noeud, accessors: [{ bufferView: 0, count: 300, componentType: 5126 }, BASE.accessors[1]] };
  assert.throws(() => geometrieGlb(THREE, glb(court, triangle())), /tronqué/);
});

// 10/11/2026 (ID Studio IA : « la couleur blanc ressort trop ») : les faces d'un modèle de Pixal3D sont tournées au hasard.
/** Une forme à sommets partagés. */
function forme(points, faces) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(points.flat()), 3));
  g.setIndex(new THREE.BufferAttribute(new Uint32Array(faces.flat()), 1));
  return g;
}
/** Le volume que les faces enferment (positif : elles regardent dehors), et le nombre d'arêtes que leurs deux faces
 *  parcourent dans le même sens (zéro quand toutes les faces sont d'accord). */
function sens(g) {
  const ix = Array.from(g.index.array), p = g.getAttribute('position'), v = i => new THREE.Vector3().fromBufferAttribute(p, i);
  let volume = 0;
  const vues = new Set();
  let desaccords = 0;
  for (let f = 0; f < ix.length; f += 3) {
    volume += v(ix[f]).dot(v(ix[f + 1]).cross(v(ix[f + 2]))) / 6;
    for (let e = 0; e < 3; e++) {
      const cle = `${ix[f + e]}>${ix[f + (e + 1) % 3]}`;
      if (vues.has(cle)) desaccords++;
      vues.add(cle);
    }
  }
  return { volume: Math.round(volume * 1000) / 1000, desaccords };
}
const COINS = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]];
const DEHORS = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]];   // un tétraèdre, ses quatre faces vers l'extérieur

const envers = ([a, b, c]) => [a, c, b];

test('les faces tournées au hasard sont remises dans le sens du plus grand nombre', () => {
  const melange = forme(COINS, [DEHORS[0], envers(DEHORS[1]), DEHORS[2], envers(DEHORS[3])]);   // deux sur quatre à l'envers
  assert.equal(sens(melange).desaccords, 4);
  assert.equal(accorderFaces(melange), 2);
  assert.deepEqual(sens(melange), { volume: 0.167, desaccords: 0 });   // à égalité : le sens de la première face
  // Une seule face à l'envers, et c'est la première lue : c'est elle qui est retournée, pas les trois autres.
  const une = forme(COINS, [envers(DEHORS[0]), DEHORS[1], DEHORS[2], DEHORS[3]]);
  assert.equal(accorderFaces(une), 1);
  assert.deepEqual(sens(une), { volume: 0.167, desaccords: 0 });
  assert.equal(accorderFaces(une), 0);                                 // un second passage ne change plus rien
});

test('un fichier dont les faces sont déjà d’accord n’est jamais touché', () => {
  const bon = forme(COINS, DEHORS);
  assert.equal(accorderFaces(bon), 0);
  assert.deepEqual(Array.from(bon.index.array), DEHORS.flat());
  const dedans = forme(COINS, DEHORS.map(envers));                     // d'accord entre elles, tournées vers l'intérieur
  assert.equal(accorderFaces(dedans), 0);
  assert.deepEqual(sens(dedans), { volume: -0.167, desaccords: 0 });
  // Une forme sans sommets partagés (STL, OBJ) n'a pas de voisinage : elle reste telle quelle.
  const libre = new THREE.BufferGeometry();
  libre.setAttribute('position', new THREE.BufferAttribute(new Float32Array([0, 0, 0, 0, 1, 0, 1, 0, 0]), 3));
  assert.equal(accorderFaces(libre), 0);
});

test('chaque îlot est accordé à part, et une arête à trois faces ne se franchit pas', () => {
  const loin = COINS.map(([x, y, z]) => [x + 5, y, z]), plus4 = f => f.map(i => i + 4);
  // Le premier tétraèdre regarde dehors, le second dedans ; chacun a une face à l'envers.
  const deux = forme([...COINS, ...loin], [DEHORS[0], DEHORS[1], envers(DEHORS[2]), DEHORS[3],
    ...[envers(DEHORS[0]), envers(DEHORS[1]), envers(DEHORS[2]), DEHORS[3]].map(plus4)]);
  assert.equal(accorderFaces(deux), 2);
  assert.deepEqual(sens(deux), { volume: 0, desaccords: 0 });          // +1/6 et -1/6 : chacun a gardé son propre sens
  // Trois faces autour de la même arête (0-1), comme les ailettes d'un modèle allégé : aucune n'entraîne les autres.
  const ailettes = forme([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1]], [[0, 1, 2], [0, 1, 3], [0, 1, 4]]);
  assert.equal(accorderFaces(ailettes), 0);
  assert.deepEqual(Array.from(ailettes.index.array), [0, 1, 2, 0, 1, 3, 0, 1, 4]);
});
