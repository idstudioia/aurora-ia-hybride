// Tests de la vue d'origine d'un modèle importé en GLB (laboratoire 3D, 12/11/2026) : ce qui décide si elle est
// proposée, le refus de toute adresse hors du fichier, la pose du modèle (la même que l'hologramme), ses animations
// et son squelette, et sa place dans la scène. Les fichiers GLB sont fabriqués ici, en mémoire (tests/aide-glb.mjs) :
// ni navigateur, ni serveur, ni réseau (toute requête ferait échouer le test), et ~/.aurora n'est jamais touché.
// Ce que ces tests ne voient pas : le dessin lui-même (textures, éclairage, reflets), qui demande une carte graphique.
// Lancer depuis frontend : node --experimental-strip-types --test tests/origine.test.mjs
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { charger } from './aide-carte.mjs';
import { glb, tampon } from './aide-glb.mjs';

const { contenuGlb } = await charger('components/hologram/glb.ts');
const { DEBOUT, VUE_DEPART, construireScene, fichierOrigine, libelleAnimation, maillages, quartsPoses, validerScene } = await charger('components/labo/scene.ts');
const { preparerMaillages } = await charger('components/labo/maillage.ts');
const { gestionnaireFerme, lireOrigine } = await charger('components/labo/origine.ts');

// Aucune requête n'est permise : chacune est notée, et refusée. Seul passe ce qui est écrit dans le fichier lui-même
// (« data: », ou « blob: » : une image du fichier, que le lecteur de three.js ouvre ainsi) — rien ne sort du programme.
const requetes = [], vraiFetch = globalThis.fetch;
globalThis.fetch = (adresse, options) => {
  if (/^(?:data|blob):/.test(String(adresse))) return vraiFetch(adresse, options);
  requetes.push(String(adresse));
  return Promise.reject(new Error('aucune requête n’est permise dans ces tests'));
};
after(() => { globalThis.fetch = vraiFetch; });

const FICHIER = 'imp_dragon.glb';
/** La description d'une scène d'une seule pièce, comme le service en garde une pour un modèle importé. */
const scene = (origine, id, forme) => ({
  format: 'aurora.scene/1', id, titre: 'Dragon', domaine: 'objet', nature: 'illustration', origine,
  avertissement: 'Modèle 3D importé : montré tel quel.', resume: 'Un modèle donné à AURORA.', fonctionnement: 'Montré tel quel.', mots: ['dragon'],
  camera: { position: [2.7, 2.1, 3.3], cible: [0, 1, 0] }, groupes: [],
  pieces: [{ id: 'modele', nom: 'Dragon', groupe: null, parent: null, position: [0, 0, 0], forme, couleur: 'cyan', fonction: 'Le modèle entier, en un seul bloc.' }],
});
const maillage = (fichier, reglage = {}) => ({ type: 'maillage', fichier, taille: 2, ...reglage });

/** Un petit modèle sans squelette : deux triangles (côtés 1, 2 et 3), une matière, et trois animations de son nœud —
 *  « Walking » (il avance de 4 selon z), une sans nom (il glisse de 6 selon x) et « Vide », qui ne fait rien bouger. */
function modeleAnime(noeud = {}, plus = {}) {
  const { bin, vues } = tampon([
    ['f32', [0, 0, 0, 1, 0, 0, 0, 2, 0, 0, 0, 3]], ['u16', [0, 1, 2, 0, 2, 3]],
    ['f32', [0, 1]], ['f32', [0, 0, 0, 0, 0, 4]], ['f32', [0, 0, 0, 6, 0, 0]],
  ]);
  const piste = sortie => ({ channels: [{ sampler: 0, target: { node: 0, path: 'translation' } }], samplers: [{ input: 2, output: sortie }] });
  return glb({
    asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0, ...noeud }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1, material: 0 }] }],
    materials: [{ pbrMetallicRoughness: { baseColorFactor: [1, .5, 0, 1] } }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: 4, type: 'VEC3', min: [0, 0, 0], max: [1, 2, 3] },
      { bufferView: 1, componentType: 5123, count: 6, type: 'SCALAR' },
      { bufferView: 2, componentType: 5126, count: 2, type: 'SCALAR', min: [0], max: [1] },
      { bufferView: 3, componentType: 5126, count: 2, type: 'VEC3' }, { bufferView: 4, componentType: 5126, count: 2, type: 'VEC3' },
    ],
    bufferViews: vues, buffers: [{ byteLength: bin.byteLength }],
    animations: [{ name: 'Walking', ...piste(3) }, piste(4), { name: 'Vide', channels: [], samplers: [] }],
    ...plus,
  }, bin);
}
/** Le même, déplacé et tourné de 30° dans son fichier (de travers : sa boîte ne se déduit plus de celle de ses sommets
 *  bruts) : les deux lecteurs, hologramme et vue d'origine, doivent s'accorder. */
const DEPLACE = { rotation: [0, 0, Math.sin(Math.PI / 12), Math.cos(Math.PI / 12)], translation: [3, 1, -2] };

/** Un triangle (côtés 1 et 2) tenu par un os deux fois plus grand que lui : dessiné, il mesure 2 sur 4. Son animation
 *  « Saut » monte l'os de 3. */
function modeleSquelette() {
  const { bin, vues } = tampon([
    ['f32', [0, 0, 0, 1, 0, 0, 0, 2, 0]], ['u16', [0, 1, 2]], ['u8', Array(12).fill(0)], ['f32', [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]],
    ['f32', [0, 1]], ['f32', [0, 0, 0, 0, 3, 0]],
  ]);
  return glb({
    asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0, 1] }],
    nodes: [{ mesh: 0, skin: 0 }, { name: 'os', scale: [2, 2, 2] }], skins: [{ joints: [1] }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0, JOINTS_0: 2, WEIGHTS_0: 3 }, indices: 1 }] }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 2, 0] },
      { bufferView: 1, componentType: 5123, count: 3, type: 'SCALAR' },
      { bufferView: 2, componentType: 5121, count: 3, type: 'VEC4' }, { bufferView: 3, componentType: 5126, count: 3, type: 'VEC4' },
      { bufferView: 4, componentType: 5126, count: 2, type: 'SCALAR', min: [0], max: [1] }, { bufferView: 5, componentType: 5126, count: 2, type: 'VEC3' },
    ],
    bufferViews: vues, buffers: [{ byteLength: bin.byteLength }],
    animations: [{ name: 'Saut', channels: [{ sampler: 0, target: { node: 1, path: 'translation' } }], samplers: [{ input: 4, output: 5 }] }],
  }, bin);
}

const arrondi = v => v.toArray().map(x => Math.round(x * 1e4) / 1e4 + 0);
/** La boîte d'un objet tel qu'il serait dessiné : sommet par sommet, squelette compris. */
const boiteDe = objet => {
  objet.updateMatrixWorld(true);
  const b = new THREE.Box3().setFromObject(objet, true);
  return { min: arrondi(b.min), max: arrondi(b.max) };
};
const leMaillage = m => { let trouve = null; m.objet.traverse(o => { if (o.isMesh) trouve = o; }); return trouve; };

test('la vue d’origine n’est proposée que pour un modèle importé en GLB', () => {
  const importe = scene('importe', 'imp_dragon', maillage(FICHIER));
  assert.equal(validerScene(importe).ok, true);                       // la description d'essai est celle d'une vraie scène
  assert.equal(fichierOrigine(importe), FICHIER);
  assert.equal(fichierOrigine(scene('importe', 'imp_dragon', maillage(FICHIER, { debout: '-x', quart: 3 }))), FICHIER);
  // Un STL ou un OBJ n'a ni matière ni animation ; une forme reconstruite depuis une photo n'a pas de couleurs.
  assert.equal(fichierOrigine(scene('importe', 'imp_filtre', maillage('imp_filtre.stl'))), null);
  assert.equal(fichierOrigine(scene('importe', 'imp_boitier', maillage('imp_boitier.obj'))), null);
  assert.equal(fichierOrigine(scene('reconstruit', 'imp_avion', maillage('imp_avion.glb'))), null);
  // Une scène d'AURORA ou une maquette générée n'a pas de fichier.
  const boite = { type: 'boite', l: 1, h: 1, p: 1 };
  assert.equal(fichierOrigine(scene('predefini', 'bras', boite)), null);
  assert.equal(fichierOrigine(scene(undefined, 'bras', boite)), null);
  assert.equal(fichierOrigine(scene('genere', 'gen_fusee', boite)), null);
  // Une scène s'ouvre toujours en hologramme, sans animation en cours.
  assert.equal(VUE_DEPART.origine, false);
  assert.equal(VUE_DEPART.animation, null);
});

test('ce que le fichier contient est lu dans sa description : matières, textures, animations et leurs noms', () => {
  assert.deepEqual(contenuGlb(modeleAnime()), { matieres: 1, textures: 0, animations: ['Walking', '', 'Vide'] });
  const riche = modeleAnime({}, {
    materials: [{}, {}], textures: [{ source: 0 }, { source: 1 }, { source: 0 }],
    images: [{ bufferView: 0, mimeType: 'image/png' }, { uri: 'data:image/png;base64,AAAA' }],   // deux contenus du fichier lui-même
    animations: [{ name: '  Armature|Marche\n|Base  ' }, { name: 12 }, { name: 'x'.repeat(200) }],
  });
  const lu = contenuGlb(riche);
  assert.equal(lu.matieres, 2);
  assert.equal(lu.textures, 3);
  assert.deepEqual(lu.animations, ['Armature|Marche |Base', '', 'x'.repeat(60)]);   // nettoyé, borné, jamais inventé
  assert.deepEqual(contenuGlb(glb({ asset: { version: '2.0' } }, null)), { matieres: 0, textures: 0, animations: [] });
  // Le nom affiché : celui du fichier, sinon son rang, dit sans nom.
  assert.equal(libelleAnimation('Walking', 0), '« Walking »');
  assert.equal(libelleAnimation('', 1), 'animation 2 (sans nom dans le fichier)');
});

test('toute adresse hors du fichier est refusée, où qu’elle soit écrite', () => {
  const avec = plus => () => contenuGlb(modeleAnime({}, plus));
  assert.throws(avec({ images: [{ uri: 'https://exemple.invalid/peau.png' }] }), /adresse hors du fichier \(« https:\/\/exemple\.invalid\/peau\.png »\)/);
  assert.throws(avec({ images: [{ uri: 'textures/peau.png' }] }), /hors du fichier/);          // un fichier voisin non plus
  assert.throws(avec({ images: [{ uri: '//exemple.invalid/peau.png' }] }), /hors du fichier/);
  assert.throws(avec({ images: [{ uri: ' data:image/png;base64,AAAA' }] }), /hors du fichier/);  // pas tout à fait « data: »
  assert.throws(avec({ images: [{ bufferView: 0, mimeType: 'image/png', uri: 'http://exemple.invalid/a.png' }] }), /hors du fichier/);
  assert.throws(avec({ buffers: [{ byteLength: 104 }, { uri: 'formes.bin', byteLength: 8 }] }), /hors du fichier/);
  assert.throws(avec({ extensions: { EXT_inconnue: { sources: [{ profond: { uri: 'file:///C:/secret.txt' } }] } } }), /hors du fichier/);
  assert.throws(avec({ images: [{ uri: 42 }] }), /adresse illisible/);
  assert.throws(avec({ images: [{ uri: `https://exemple.invalid/${'a'.repeat(300)}` }] }), /« .{60}… »/);   // l'adresse citée est coupée
  // Un tableau peut s'appeler comme il veut : seul le champ « uri » d'un objet est une adresse.
  assert.doesNotThrow(avec({ extras: { uri_de_la_doc: 'https://exemple.invalid', liste: ['uri', 'https://exemple.invalid'] } }));
  // Un format qui demande un décodeur à part est dit tel, pas deviné.
  assert.throws(avec({ extensionsRequired: ['KHR_texture_basisu'] }), /compressé \(KHR_texture_basisu\)/);
  assert.throws(avec({ extensionsRequired: ['KHR_draco_mesh_compression'] }), /compressé/);
  assert.throws(() => contenuGlb(new TextEncoder().encode('ceci n’est pas un modèle 3D').buffer), /pas un fichier GLB/);
  const faux = new Uint8Array(modeleAnime());
  faux[20] = 0x5b;   // la description ne commence plus par une accolade : ce n'est plus du JSON
  assert.throws(() => contenuGlb(faux.buffer), /illisible/);
});

test('le lecteur de three.js ne reçoit qu’un gestionnaire fermé : aucune requête ne part', async () => {
  const g = gestionnaireFerme();
  assert.equal(g.resolveURL('data:image/png;base64,AAAA'), 'data:image/png;base64,AAAA');
  assert.equal(g.resolveURL('blob:http://127.0.0.1:5173/3f2a'), 'blob:http://127.0.0.1:5173/3f2a');
  for (const adresse of ['https://exemple.invalid/a.bin', 'http://127.0.0.1:8765/api/health', 'formes.bin', '/formes.bin', '', 'file:///C:/a', 'javascript:alert(1)']) {
    assert.throws(() => g.resolveURL(adresse), /hors du fichier/, adresse);
  }
  // Même sans le premier contrôle (contenuGlb), le lecteur lui-même ne va rien chercher : sa lecture s'arrête.
  const { bin, vues } = tampon([['u16', [0, 1, 2]]]);
  const dehors = glb({
    asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [{ bufferView: 1, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] }, { bufferView: 0, componentType: 5123, count: 3, type: 'SCALAR' }],
    bufferViews: [...vues, { buffer: 1, byteOffset: 0, byteLength: 36 }],
    buffers: [{ byteLength: bin.byteLength }, { uri: 'https://exemple.invalid/formes.bin', byteLength: 36 }],
  }, bin);
  await assert.rejects(new Promise((fait, refuse) => new GLTFLoader(gestionnaireFerme()).parse(dehors, '', fait, refuse)), /hors du fichier/);
  assert.deepEqual(requetes, []);
});

test('un fichier qui cite une adresse n’est pas montré en vue d’origine, et rien n’est demandé au réseau', async () => {
  const cite = modeleAnime({}, { images: [{ uri: 'https://exemple.invalid/peau.png' }], textures: [{ source: 0 }] });
  await assert.rejects(lireOrigine(cite, maillage(FICHIER), 0), /adresse hors du fichier/);
  await assert.rejects(lireOrigine(new ArrayBuffer(64), maillage(FICHIER), 0), /pas un fichier GLB/);
  assert.deepEqual(requetes, []);
});

test('une texture rangée dans le fichier est lue à travers le gestionnaire fermé, puis rendue', async () => {
  // Node n'a ni « self » ni décodeur d'images : le strict nécessaire est prêté au lecteur le temps de ce test. L'image
  // n'est pas décodée (ses octets sont quelconques) : ce test suit son chemin, pas son dessin.
  let decodees = 0, fermees = 0;
  globalThis.self = globalThis;
  globalThis.createImageBitmap = async blob => { decodees++; return { width: 2, height: 2, type: blob.type, close() { fermees++; } }; };
  try {
    const { bin, vues } = tampon([
      ['f32', [0, 0, 0, 1, 0, 0, 0, 2, 0]], ['u16', [0, 1, 2]], ['f32', [0, 0, 1, 0, 0, 1]], ['u8', [137, 80, 78, 71, 13, 10, 26, 10]],
    ]);
    const fichier = glb({
      asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0, TEXCOORD_0: 2 }, indices: 1, material: 0 }] }],
      materials: [{ pbrMetallicRoughness: { baseColorTexture: { index: 0 } } }],
      textures: [{ source: 0 }], images: [{ bufferView: 3, mimeType: 'image/png' }],
      accessors: [
        { bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 2, 0] },
        { bufferView: 1, componentType: 5123, count: 3, type: 'SCALAR' }, { bufferView: 2, componentType: 5126, count: 3, type: 'VEC2' },
      ],
      bufferViews: vues, buffers: [{ byteLength: bin.byteLength }],
    }, bin);
    const m = await lireOrigine(fichier, maillage(FICHIER), 0);
    assert.deepEqual(m.contenu, { matieres: 1, textures: 1, animations: [] });
    const carte = leMaillage(m).material.map;
    assert.equal(carte.isTexture, true);            // la matière du fichier porte bien sa texture
    assert.equal(carte.image.type, 'image/png');
    assert.equal(decodees, 1);
    assert.deepEqual(requetes, []);                 // lue dans le fichier : aucune adresse n'a été demandée
    let rendue = false;
    carte.addEventListener('dispose', () => { rendue = true; });
    m.liberer();
    assert.equal(rendue, true);
    assert.equal(fermees, 1);                       // l'image décodée est rendue elle aussi
  } finally {
    delete globalThis.self; delete globalThis.createImageBitmap;
  }
});

test('le modèle d’origine est posé comme sa forme en hologramme, dans les six positions et les quatre quarts de tour', async () => {
  const fichier = modeleAnime(DEPLACE), lire = async () => fichier;
  const m = await lireOrigine(fichier, maillage(FICHIER), 0);
  let comparees = 0;
  for (const debout of DEBOUT) {
    for (const quart of [undefined, 0, 1, 2, 3]) {   // sans quart dit : le modèle montre son grand côté de lui-même
      const forme = maillage(FICHIER, quart === undefined ? { debout } : { debout, quart });
      for (const g of maillages.values()) g.dispose();
      maillages.clear(); quartsPoses.clear();
      await preparerMaillages(scene('importe', 'imp_dragon', forme), lire);
      const holo = maillages.get(FICHIER).boundingBox;
      m.poser(forme, quartsPoses.get(FICHIER));
      assert.deepEqual(boiteDe(m.objet), { min: arrondi(holo.min), max: arrondi(holo.max) }, `debout ${debout}, quart ${quart}`);
      comparees++;
    }
  }
  assert.equal(comparees, 30);
  // Posé : centré, sur le sol, son plus grand côté (ici la profondeur) à la taille dite, 2.
  m.poser(maillage(FICHIER), 0);
  const { min, max } = boiteDe(m.objet);
  assert.deepEqual([min[0] + max[0], min[1], min[2], max[2]], [0, 0, -1, 1]);
  assert.ok(max[0] - min[0] < 2 && max[1] < 2);
  m.liberer();
});

test('les animations du fichier sont lues sous leur nom, une à la fois ; l’arrêt rend la pose du fichier', async () => {
  const m = await lireOrigine(modeleAnime(DEPLACE), maillage(FICHIER), 0);
  // « Vide » ne fait rien bouger : elle n'est pas proposée. Les autres gardent le nom du fichier ('' : sans nom).
  assert.deepEqual(m.contenu, { matieres: 1, textures: 0, animations: ['Walking', ''] });
  const noeud = leMaillage(m), place = () => arrondi(noeud.position);
  assert.deepEqual(place(), [3, 1, -2]);
  assert.equal(m.avancer(.5), false);            // rien n'est lu tant qu'on ne l'a pas demandé
  assert.deepEqual(place(), [3, 1, -2]);
  m.jouer(0);                                    // « Walking » : de 0 à 4 selon z en une seconde
  assert.equal(m.avancer(.5), true);
  assert.deepEqual(place(), [0, 0, 2]);
  m.jouer(1);                                    // l'autre prend sa place, elle ne s'y ajoute pas
  assert.equal(m.avancer(.25), true);
  assert.deepEqual(place(), [1.5, 0, 0]);
  assert.equal(m.avancer(1), true);              // elle reprend en boucle : 1,25 s, soit le quart du trajet
  assert.deepEqual(place(), [1.5, 0, 0]);
  m.jouer(null);
  assert.deepEqual(place(), [3, 1, -2]);         // la pose du fichier
  assert.equal(m.avancer(.5), false);
  m.jouer(7);                                    // un rang qui n'existe pas : rien n'est lu
  assert.equal(m.avancer(.5), false);
  assert.deepEqual(place(), [3, 1, -2]);
  // Reposer le modèle arrête la lecture : il est mesuré dans la pose du fichier.
  m.jouer(0); m.avancer(.5);
  m.poser(maillage(FICHIER, { debout: 'z' }), 0);
  assert.deepEqual(place(), [3, 1, -2]);
  assert.equal(m.avancer(.5), false);
  m.liberer();
});

test('une matière que le fichier dit transparente écrit quand même la profondeur : vu de dos, on ne voit pas à travers', async () => {
  // Trois morceaux, trois matières : « BLEND » (ce que Blender écrit dès que la texture a une couche alpha, même pleine :
  // le cas des modèles animés de Meshy donnés par ID Studio IA le 12/11/2026), une matière ordinaire, et une matière découpée.
  const morceau = material => ({ attributes: { POSITION: 0 }, indices: 1, material });
  const m = await lireOrigine(modeleAnime({}, {
    meshes: [{ primitives: [morceau(0), morceau(1), morceau(2)] }],
    materials: [{ name: 'dite transparente', alphaMode: 'BLEND', doubleSided: true }, { name: 'ordinaire' }, { name: 'découpée', alphaMode: 'MASK', alphaCutoff: .4 }],
  }), maillage(FICHIER), 0);
  const lues = {};
  m.objet.traverse(o => { if (o.isMesh) lues[o.material.name] = o.material; });
  assert.deepEqual(Object.keys(lues).sort(), ['dite transparente', 'découpée', 'ordinaire']);
  // Sans écriture de la profondeur (ce que fait le lecteur de three.js pour « BLEND »), les faces de derrière et de
  // l'intérieur se dessinent par-dessus celles de devant. La matière reste celle du fichier pour tout le reste.
  const dite = lues['dite transparente'];
  assert.equal(dite.depthWrite, true);
  assert.equal(dite.transparent, true);
  assert.equal(dite.side, THREE.DoubleSide);
  assert.equal(dite.alphaTest, .02);                // un point presque invisible n'est pas dessiné : il ne cache rien
  // Les autres matières ne sont pas touchées.
  assert.deepEqual([lues.ordinaire.transparent, lues.ordinaire.depthWrite, lues.ordinaire.alphaTest], [false, true, 0]);
  assert.deepEqual([lues['découpée'].transparent, lues['découpée'].depthWrite, lues['découpée'].alphaTest], [false, true, .4]);
  m.liberer();
});

test('un fichier fait de plusieurs pièces s’écarte en vue éclatée, de ce que le fichier dit ou du centre du modèle', async () => {
  // Deux pièces sous un même parent deux fois plus grand qu'elles. « extras.aurora.eclate » dit l'écart de chacune, en
  // parts de la plus grande dimension du modèle (ici 8 : de x = 0 à x = 8), dans le repère du fichier.
  const noeuds = extras => [
    { children: [1, 2], scale: [2, 2, 2] },
    { name: 'haut', mesh: 0, ...(extras ? { extras: { aurora: { eclate: [0, .5, 0], nom: 'Capot' } } } : {}) },
    { name: 'cote', mesh: 0, translation: [3, 0, 0], ...(extras ? { extras: { aurora: { eclate: [.25, 0, 0] } } } : {}) },
  ];
  const fichier = modeleAnime({}, { nodes: noeuds(true), animations: [] });
  const m = await lireOrigine(fichier, maillage(FICHIER), 0);
  assert.equal(m.contenu.pieces, 2);
  const haut = m.objet.getObjectByName('haut'), cote = m.objet.getObjectByName('cote');
  const stable = () => { for (let i = 0; i < 40; i++) m.avancer(.5); };
  const assemble = boiteDe(m.objet);
  assert.equal(m.avancer(.5), false);              // rien ne bouge tant qu'on n'a rien demandé
  m.eclater(true);
  assert.equal(m.avancer(.1), true);               // les pièces partent en douceur…
  assert.ok(haut.position.y > 0 && haut.position.y < 2);
  stable();
  assert.deepEqual(arrondi(haut.position), [0, 2, 0]);   // … 0,5 × 8 = 4 dans le fichier, soit 2 dans son parent
  assert.deepEqual(arrondi(cote.position), [4, 0, 0]);   // 3 + (0,25 × 8) / 2
  assert.equal(m.avancer(.5), false);              // arrivées : plus rien ne bouge
  // Reposé pendant qu'il est écarté, le modèle garde la taille de ses pièces remontées.
  m.poser(maillage(FICHIER), 0);
  m.eclater(false); stable();
  assert.deepEqual(arrondi(haut.position), [0, 0, 0]);
  assert.deepEqual(arrondi(cote.position), [3, 0, 0]);
  assert.deepEqual(boiteDe(m.objet), assemble);
  m.liberer();

  // Un écart mal écrit n'est pas lu ; et sans rien d'écrit, deux pièces s'éloignent du centre du modèle.
  const mal = modeleAnime({}, { nodes: [{ children: [1, 2] }, { name: 'haut', mesh: 0, extras: { aurora: { eclate: [0, 'haut', 0] } } },
    { name: 'cote', mesh: 0, translation: [3, 0, 0], extras: { aurora: { eclate: [1, 0] } } }], animations: [] });
  const sans = await lireOrigine(mal, maillage(FICHIER), 0);
  assert.equal(sans.contenu.pieces, 2);
  sans.eclater(true); for (let i = 0; i < 40; i++) sans.avancer(.5);
  assert.ok(sans.objet.getObjectByName('haut').position.x < 0);    // à gauche du centre : part vers la gauche
  assert.ok(sans.objet.getObjectByName('cote').position.x > 3);    // à droite : part vers la droite
  sans.liberer();

  // Dans la scène, le bouton « Éclater » pilote aussi les pièces du fichier, même lu après coup.
  const d = scene('importe', 'imp_dragon', maillage(FICHIER));
  for (const g of maillages.values()) g.dispose();
  maillages.clear(); quartsPoses.clear();
  await preparerMaillages(d, async () => fichier);
  const labo = construireScene(THREE, d, -1.62);
  labo.eclater(true);
  const lu = await lireOrigine(fichier, d.pieces[0].forme, quartsPoses.get(FICHIER));
  labo.origine(lu);
  for (let i = 0; i < 40; i++) labo.avancer(.5, true, 1);
  assert.deepEqual(arrondi(lu.objet.getObjectByName('haut').position), [0, 2, 0]);
  labo.eclater(false);
  for (let i = 0; i < 40; i++) labo.avancer(.5, true, 1);
  assert.deepEqual(arrondi(lu.objet.getObjectByName('haut').position), [0, 0, 0]);
  labo.liberer(); lu.liberer();
});

test('la fiche d’une pièce du fichier se lit au clic, et sa coque peut devenir transparente', async () => {
  // Un capot (la coque) devant une batterie (une pièce), chacun avec sa matière. « extras.aurora » les décrit.
  const morceau = material => ({ primitives: [{ attributes: { POSITION: 0 }, indices: 1, material }] });
  const fichier = modeleAnime({}, {
    meshes: [morceau(0), morceau(1)], materials: [{ name: 'peau' }, { name: 'enveloppe' }], animations: [],
    nodes: [
      { children: [1, 2, 3] },
      { name: 'capot', mesh: 0, translation: [0, 0, 1], extras: { aurora: { genre: 'coque', nom: 'Capot', role: 'Le dessus de la coque.', eclate: [0, .4, 0] } } },
      { name: 'batterie', mesh: 1, extras: { aurora: {
        genre: 'piece', nom: `Batterie\u0007LiPo ${'x'.repeat(200)}`, role: 'La réserve d’énergie.',
        proprietes: [{ nom: 'Énergie', valeur: '55,5 Wh' }, { nom: 'Sans valeur' }, 'pas une propriété', { nom: 'Prise', valeur: 'XT60' },
          ...Array.from({ length: 12 }, (_, i) => ({ nom: `n${i}`, valeur: 'v' }))],
      } } },
      { name: 'muet', mesh: 1, translation: [0, 0, 2], extras: { aurora: { genre: 'piece', nom: 42 } } },   // sans nom lisible : pas de fiche
    ],
  });
  const m = await lireOrigine(fichier, maillage(FICHIER), 0);
  assert.equal(m.contenu.coque, true);
  assert.equal(m.contenu.pieces, undefined);                         // une seule pièce dit son écart : rien à écarter
  m.objet.updateMatrixWorld(true);
  const capot = m.objet.getObjectByName('capot');
  const devant = capot.localToWorld(new THREE.Vector3(.2, .2, 6)), cible = capot.localToWorld(new THREE.Vector3(.2, .2, 0));
  const viseur = new THREE.Raycaster(devant, cible.clone().sub(devant).normalize());
  // La pièce la plus proche qui a une fiche : le nœud « muet », touché le premier, n'en a pas.
  assert.deepEqual(m.choisir(viseur), { nom: 'Capot', genre: 'coque', role: 'Le dessus de la coque.', proprietes: [] });
  const peau = capot.material;
  assert.deepEqual([peau.transparent, peau.opacity, peau.depthWrite], [false, 1, true]);
  // Coque transparente : on la voit à peine, et on vise à travers elle.
  m.interieur(true);
  assert.deepEqual([peau.transparent, peau.opacity, peau.depthWrite], [true, .16, false]);
  const batterie = m.choisir(viseur);
  assert.equal(batterie.genre, 'piece');
  assert.equal(batterie.nom, `Batterie LiPo ${'x'.repeat(200)}`.slice(0, 80));      // borné, sans caractère de contrôle
  assert.equal(batterie.proprietes.length, 8);                                        // huit au plus, les mal écrites écartées
  assert.deepEqual(batterie.proprietes.slice(0, 2), [{ nom: 'Énergie', valeur: '55,5 Wh' }, { nom: 'Prise', valeur: 'XT60' }]);
  assert.deepEqual([m.objet.getObjectByName('batterie').material.transparent, m.objet.getObjectByName('batterie').material.opacity], [false, 1]);
  // La coque redevient pleine : elle est de nouveau la première touchée.
  m.interieur(false);
  assert.deepEqual([peau.transparent, peau.opacity, peau.depthWrite], [false, 1, true]);
  assert.equal(m.choisir(viseur).nom, 'Capot');
  assert.equal(m.choisir(new THREE.Raycaster(devant, new THREE.Vector3(0, 1, 0))), null);   // rien sous ce rayon
  m.liberer();

  // Dans la scène : la fiche du fichier ne se lit qu'en vue d'origine, et « Intérieur » pilote aussi sa coque.
  const d = scene('importe', 'imp_dragon', maillage(FICHIER));
  for (const g of maillages.values()) g.dispose();
  maillages.clear(); quartsPoses.clear();
  await preparerMaillages(d, async () => fichier);
  const labo = construireScene(THREE, d, -1.62), lu = await lireOrigine(fichier, d.pieces[0].forme, quartsPoses.get(FICHIER));
  const versCapot = () => {
    labo.avancer(1 / 60, true, 1); labo.racine.updateMatrixWorld(true);
    const c = lu.objet.getObjectByName('capot'), a = c.localToWorld(new THREE.Vector3(.2, .2, 6)), b = c.localToWorld(new THREE.Vector3(.2, .2, 0));
    return new THREE.Raycaster(a, b.sub(a).normalize());
  };
  assert.equal(labo.choisirFichier(versCapot()), null);              // en hologramme : pas de fiche du fichier
  labo.interieur(true);
  labo.origine(lu);
  assert.equal(lu.objet.getObjectByName('capot').material.opacity, .16);   // lu après coup, il prend l'état de la vue
  assert.equal(labo.choisirFichier(versCapot()).genre, 'piece');
  labo.interieur(false);
  assert.equal(labo.choisirFichier(versCapot()).nom, 'Capot');
  labo.origine(null);
  assert.equal(labo.choisirFichier(versCapot()), null);
  labo.liberer(); lu.liberer();
});

test('un modèle à squelette est mesuré et animé avec son squelette', async () => {
  const m = await lireOrigine(modeleSquelette(), maillage('imp_robot.glb'), 0);
  assert.deepEqual(m.contenu.animations, ['Saut']);
  const peau = leMaillage(m);
  assert.equal(peau.isSkinnedMesh, true);
  assert.equal(peau.frustumCulled, false);       // il sort de sa boîte de départ quand il bouge : toujours dessiné
  // Son os le dessine deux fois plus grand (2 sur 4) : c'est cette taille-là qui est ramenée à 2, pas celle des
  // sommets écrits dans le fichier (1 sur 2), qui le laisserait deux fois trop grand à l'écran.
  assert.deepEqual(boiteDe(m.objet), { min: [-.5, 0, 0], max: [.5, 2, 0] });
  m.jouer(0);                                    // « Saut » : l'os monte de 3 en une seconde, la peau le suit
  assert.equal(m.avancer(.5), true);
  assert.deepEqual(boiteDe(m.objet), { min: [-.5, .75, 0], max: [.5, 2.75, 0] });
  m.jouer(null);
  assert.deepEqual(boiteDe(m.objet), { min: [-.5, 0, 0], max: [.5, 2, 0] });
  m.liberer();
});

test('dans la scène, le modèle d’origine prend la place de l’hologramme, puis la lui rend', async () => {
  const fichier = modeleAnime(), d = scene('importe', 'imp_dragon', maillage(FICHIER));
  for (const g of maillages.values()) g.dispose();
  maillages.clear(); quartsPoses.clear();
  await preparerMaillages(d, async () => fichier);
  const labo = construireScene(THREE, d, -1.62), holo = labo.pieces[0].maillage;
  const m = await lireOrigine(fichier, d.pieces[0].forme, quartsPoses.get(FICHIER));
  const image = (dt = 1 / 60) => { const bouge = labo.avancer(dt, true, 1); labo.racine.updateMatrixWorld(true); return bouge; };
  image();
  assert.equal(holo.visible, true);
  const boiteHolo = new THREE.Box3().setFromObject(holo);
  // Un rayon tiré sur une face de l'hologramme, de face.
  const p = holo.geometry.getAttribute('position'), ix = holo.geometry.index.array;
  const [a, b, c] = [0, 1, 2].map(k => new THREE.Vector3().fromBufferAttribute(p, ix[k]).applyMatrix4(holo.matrixWorld));
  const centre = a.clone().add(b).add(c).divideScalar(3), normale = b.clone().sub(a).cross(c.clone().sub(a)).normalize();
  const viseur = new THREE.Raycaster(centre.clone().addScaledVector(normale, 5), normale.clone().negate());
  assert.equal(labo.choisir(viseur), 'modele');

  labo.origine(m);
  assert.equal(image(), false);
  assert.equal(holo.visible, false);              // l'hologramme ne se dessine plus…
  assert.equal(m.objet.visible, true);            // … le fichier est montré à sa place,
  assert.equal(m.objet.parent, holo.parent);      // dans la même articulation (il tourne avec le présentoir),
  assert.equal(m.lumieres.parent, labo.racine);
  const boiteOrigine = new THREE.Box3().setFromObject(m.objet, true);
  assert.deepEqual({ min: arrondi(boiteOrigine.min), max: arrondi(boiteOrigine.max) }, { min: arrondi(boiteHolo.min), max: arrondi(boiteHolo.max) });   // au même endroit, à la même taille
  assert.equal(labo.choisir(viseur), 'modele');   // et le modèle se désigne toujours
  // L'animation du fichier passe par la scène : elle la fait bouger, puis s'arrête.
  labo.animation(0);
  assert.equal(image(.5), true);
  assert.deepEqual(arrondi(leMaillage(m).position), [0, 0, 2]);
  labo.animation(null);
  assert.equal(image(.5), false);
  assert.deepEqual(arrondi(leMaillage(m).position), [0, 0, 0]);
  // Masqué, le modèle d'origine se cache comme son hologramme.
  labo.visibles(['modele'], null); image();
  assert.equal(m.objet.visible, false);
  assert.equal(labo.choisir(viseur), null);
  labo.visibles([], null); image();
  assert.equal(m.objet.visible, true);

  labo.origine(null); image();
  assert.equal(m.objet.parent, null);
  assert.equal(m.lumieres.parent, null);
  assert.equal(holo.visible, true);               // retour à l'hologramme

  // La scène ne libère pas le modèle d'origine : il est à celui qui l'a lu (Laboratoire.tsx).
  let libere = false;
  leMaillage(m).geometry.addEventListener('dispose', () => { libere = true; });
  labo.origine(m); image();
  labo.liberer();
  assert.equal(m.objet.parent, null);
  assert.equal(libere, false);
  m.liberer();
  assert.equal(libere, true);
  assert.deepEqual(requetes, []);
});
