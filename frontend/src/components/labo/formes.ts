// Les formes du noyau (11/11/2026, ID Studio IA : « des formes différentes suivant son travail », puis « ajoute aussi au noyau
// la sélection manuelle »). Au centre de l'accueil, le noyau peut céder la place à l'une de trois formes :
//   - « Cosmique » : la sphère aux galaxies, quand AURORA cherche ou regarde le monde ;
//   - « Organique » : le lotus, quand elle lit ou écrit sa mémoire ;
//   - « Énergie pure » : les rubans, quand elle lance un outil qui n'est pas une recherche.
// En choix « Automatique », chacune vient d'un vrai pas d'exécution (lib/store.tsx : searching, remembering, acting ;
// useEtatNoyau dans Laboratoire.tsx). ID Studio IA peut aussi en garder une à la main, sur le bouton « Noyau » de l'accueil,
// qui se déplie (HomePage.tsx).
// Les modèles sont ceux d’ID Studio IA, faits avec Meshy, allégés (public/noyau/SOURCE.txt) ; les effets autour viennent de la
// page d'essai reviews/maquettes-accueil/formes-noyau-essai (essai 2), où ID Studio IA les a vus avant qu'ils n'entrent ici.
// Ce n'est qu'une animation : rien ici ne mesure ni ne calcule quoi que ce soit de réel.
import type * as Trois from 'three';
import { chargerModele } from '../hologram/glb';
import { FRAGMENT_POINT, surSphere, type CommunsNuee } from './nuee';
import { libererObjets } from './scene';

type Three = typeof Trois;

export type NomForme = 'cosmique' | 'organique' | 'energie';
export const NOMS_FORMES: readonly NomForme[] = ['cosmique', 'organique', 'energie'];
/** Ce qu’ID Studio IA choisit sur le bouton « Noyau » de l'accueil (HomePage, où sont les noms affichés) : la forme suit le
 *  travail d'AURORA, le noyau seul, ou une forme tenue à la main. */
export type ChoixForme = 'auto' | 'noyau' | NomForme;

export interface FormeNoyau {
  groupe: Trois.Group;
  /** Où le noyau pose son habillage de la voix autour de la forme : la hauteur de son centre, et son agrandissement. */
  voix: { y: number; tour: number };
  /** `vue` : de 0 (absente) à 1 (là). `vivant` : faux en mouvement réduit ou animations coupées (rien ne tourne).
   *  `parle` : le niveau réel de la voix, de 0 à 1, pendant l'écoute et la parole (0 le reste du temps) : la forme bat,
   *  s'éclaire et ses comètes filent avec lui (ID Studio IA, 11/11/2026 : « ajouter des animations de parole sur les nouveaux
   *  modèles, comme le style du modèle de base quand elle parle, ou mieux »). */
  avancer(t: number, dt: number, vue: number, vivant: boolean, parle: number): void;
  liberer(): void;
}

/** Un point de lumière posé à la main : sa place, sa couleur, son éclat (de 0 à 1). */
interface Lumiere { p: number[]; c: Trois.Color; e: number }

/** Des points de lumière fixes, chacun de sa couleur (le même point rond et doux que la nuée). Ni la nuée (creerNuee :
 *  des points qui volent d'une forme à l'autre, deux couleurs) ni les points d'une scène (semerScene : ils prennent la
 *  couleur et la force de leur pièce) ne dessinent ça. `force` part de 0 : la forme la règle à chaque image. */
function constellation(THREE: Three, liste: Lumiere[], taille: number, fremit: number, communs: CommunsNuee): Trois.Points<Trois.BufferGeometry, Trois.ShaderMaterial> {
  const n = liste.length, pos = new Float32Array(n * 3), coul = new Float32Array(n * 3), aleat = new Float32Array(n * 2);
  liste.forEach((q, i) => { pos.set(q.p, i * 3); coul.set([q.c.r, q.c.g, q.c.b], i * 3); aleat.set([q.e, Math.random()], i * 2); });
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('couleur', new THREE.BufferAttribute(coul, 3));
  g.setAttribute('aleat', new THREE.BufferAttribute(aleat, 2));
  const mat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { ...communs, taille: { value: taille }, fremit: { value: fremit }, force: { value: 0 } },
    vertexShader: 'uniform float temps;uniform float taille;uniform float fremit;uniform float echelle;uniform float scintille;'
      + 'attribute vec3 couleur;attribute vec2 aleat;varying vec3 vC;varying float vE;void main(){'
      + 'vec3 w=vec3(sin(aleat.y*53.+temps*1.9),sin(aleat.y*47.+temps*1.6+1.7),sin(aleat.y*59.+temps*2.2+3.1));'
      + 'vec4 mv=modelViewMatrix*vec4(position+w*fremit*scintille,1.);gl_Position=projectionMatrix*mv;'
      + 'gl_PointSize=clamp(taille*(.35+aleat.x*aleat.x*1.6)*echelle/-mv.z,1.,40.);vC=couleur;vE=aleat.x;}',
    fragmentShader: 'uniform float force;uniform float temps;uniform float scintille;varying vec3 vC;varying float vE;void main(){' + FRAGMENT_POINT
      + 'vec3 c=mix(vC,vec3(1.),smoothstep(.82,1.,vE)*.55);float s=1.-scintille*.4*(.5+.5*sin(temps*2.4+vE*71.));'
      + 'gl_FragColor=vec4(c,a*force*s);}',
  });
  const points = new THREE.Points(g, mat);
  points.frustumCulled = false;
  return points;
}

/** Ce qui change d'une forme à l'autre dans le dessin du modèle. `echelle` : le modèle a sa plus grande dimension
 *  ramenée à 1 ; `y` : sa hauteur au-dessus du centre du noyau. `jour` : la clarté sans lampe ; `eclat` : ses parties
 *  claires brillent d'elles-mêmes ; `lisere` : la lumière sur ses bords ; `courant` : des traînées de lumière qui
 *  tournent autour de son axe (l'énergie court dans les rubans) ; `seve` : une onde de lumière qui monte (les pétales). */
/** `penche` : le modèle bascule un peu vers le haut (en radians, négatif : sa face avant se lève). Le noyau est regardé
 *  d'un peu au-dessus (15° environ) : sur la sphère, le logo sculpté tombait sous le centre (ID Studio IA, 11/11/2026, devant
 *  Cosmique : « régler un peu le logo du A en le tournant un peu vers le haut pour qu'il soit plus centré, léger »).
 *  Le lotus et les rubans ont leur logo près de leur axe : il ne descend presque pas, ils ne sont pas penchés. */
/** `tour` : l'agrandissement de l'habillage de la voix du noyau (cercles et ondes, de rayon 1 environ) pour qu'il
 *  entoure la forme : la sphère a la taille du noyau, le lotus et les rubans sont plus larges. */
interface Recette { echelle: number; y: number; penche: number; tour: number; jour: number; eclat: number; lisere: number; courant: number; seve: number }
const RECETTES: Record<NomForme, Recette> = {
  cosmique: { echelle: 2.9, y: -.12, penche: -.18, tour: 1.02, jour: .5, eclat: 1.1, lisere: 1.5, courant: 0, seve: 0 },
  organique: { echelle: 3, y: -.05, penche: 0, tour: 1.6, jour: .5, eclat: 1, lisere: .9, courant: 0, seve: .55 },
  energie: { echelle: 3.1, y: 0, penche: 0, tour: 1.62, jour: .62, eclat: 1.3, lisere: 1.1, courant: 1.25, seve: 0 },
};
/** Les orbites où court une comète : rayon, aplatissement, bascule (1,57 : à plat), direction de la bascule, vitesse, couleur. */
type Orbite = [number, number, number, number, number, string];
const ORBITES_COSMIQUE: Orbite[] = [
  [1.42, 1, 1.32, .4, .55, '#ffb547'], [1.66, .95, 1.18, 2.3, -.4, '#38d6ff'], [1.92, .9, 1.42, 4.1, .32, '#c65cff'], [2.18, .86, 1.28, 5.3, -.25, '#ffb547'],
];
const ORBITES_ENERGIE: Orbite[] = [
  [1.18, .98, 1.36, .2, 1.5, '#bfeeff'], [1.42, .95, 1.5, 1.9, -1.15, '#38d6ff'], [1.62, .93, 1.3, 3.4, .95, '#c65cff'],
  [1.82, .9, 1.44, 4.8, -.8, '#38d6ff'], [2.02, .86, 1.22, .9, .65, '#8f5cff'],
];

/** Une forme du noyau : le modèle d’ID Studio IA dessiné avec sa texture, et autour de lui les effets de sa maquette.
 *    - Cosmique : des paillettes sur sa peau, quatre orbites à comète, de la poussière d'étoiles ;
 *    - Organique : une étoile à chaque pointe de pétale, une tige de lumière, du pollen en hélice, des lucioles ;
 *    - Énergie pure : des traînées de lumière dans les rubans, cinq comètes, un tourbillon jusqu'au socle, des étincelles.
 *  Le modèle reste de face, sans tourner (son logo est sculpté sur sa face avant) : il flotte et respire comme le
 *  noyau. Il apparaît et disparaît grain par grain, sans transparence. `lueur` : la fabrique de lueurs du noyau (noyau.ts, `sprite`), pour que celles de la forme soient les
 *  mêmes. `ySocle` : la hauteur du socle du noyau, où descendent la tige et le tourbillon. */
export async function creerForme(THREE: Three, nom: NomForme, communs: CommunsNuee,
  lueur: (couleur: Trois.Color, opacite: number, taille: number) => Trois.Sprite, ySocle: number): Promise<FormeNoyau> {
  const recette = RECETTES[nom];
  const { geometrie, texture } = await chargerModele(THREE, `/noyau/formes/${nom}.glb`);
  if (!geometrie.getAttribute('normal')) geometrie.computeVertexNormals();
  geometrie.computeBoundingBox();
  const R = Math.random, gauss = () => Math.sqrt(-2 * Math.log(R() + 1e-9)) * Math.cos(6.283185 * R());
  const teinte = (h: string) => new THREE.Color(h);
  const CYAN = teinte('#38d6ff'), VIOLET = teinte('#8f5cff'), MAGENTA = teinte('#c65cff'), AMBRE = teinte('#ffb547');
  const GLACE = teinte('#bfeeff'), ROSE = teinte('#e9a8ff'), BLANC = teinte('#ffffff');
  const entre = (a: Trois.Color, b: Trois.Color, t: number) => a.clone().lerp(b, Math.min(1, Math.max(0, t)));

  const groupe = new THREE.Group(); groupe.visible = false; groupe.position.y = recette.y;
  const porte = new THREE.Group(); groupe.add(porte);
  // Dans le repère du groupe : le pied du modèle et le socle du noyau.
  const boite = geometrie.boundingBox, pied = (boite ? boite.min.y : -.4) * recette.echelle, sol = ySocle - recette.y;
  // Le modèle : sa texture d'origine, une lampe de face et un peu de jour, ses parties claires qui brillent d'elles-mêmes,
  // un liseré de lumière sur ses bords. `app` : sa part visible, grain par grain (0 : rien, 1 : tout).
  const matiere = new THREE.ShaderMaterial({
    side: THREE.DoubleSide,
    uniforms: {
      temps: communs.temps, carte: { value: texture }, c1: { value: CYAN }, c2: { value: VIOLET }, app: { value: 0 },
      jour: { value: recette.jour }, eclat: { value: recette.eclat }, lisere: { value: recette.lisere },
      courant: { value: recette.courant }, seve: { value: recette.seve }, parle: { value: 0 },
    },
    vertexShader: 'varying vec2 vUv;varying vec3 vN;varying vec3 vP;varying vec3 vM;void main(){vUv=uv;vM=position;vN=normalMatrix*normal;'
      + 'vec4 mv=modelViewMatrix*vec4(position,1.);vP=mv.xyz;gl_Position=projectionMatrix*mv;}',
    fragmentShader: 'uniform sampler2D carte;uniform vec3 c1;uniform vec3 c2;uniform float app;uniform float temps;uniform float jour;uniform float eclat;'
      + 'uniform float lisere;uniform float courant;uniform float seve;uniform float parle;varying vec2 vUv;varying vec3 vN;varying vec3 vP;varying vec3 vM;void main(){'
      + 'if(fract(sin(dot(floor(gl_FragCoord.xy),vec2(12.9898,78.233)))*43758.5453)>app)discard;'
      + 'vec3 t=texture2D(carte,vUv).rgb;vec3 n=normalize(vN);if(!gl_FrontFacing)n=-n;vec3 v=normalize(-vP);'
      + 'float face=max(dot(n,normalize(vec3(.3,.55,.78))),0.);float bord=pow(1.-max(dot(n,v),0.),2.4);float clair=dot(t,vec3(.2126,.7152,.0722));'
      // Avec la voix : les parties claires et le liseré s'avivent.
      + 'vec3 c=t*(jour+.8*face)+t*smoothstep(.3,.85,clair)*eclat*(1.+.9*parle)*(.85+.15*sin(temps*1.7+vP.y*3.));c+=mix(c2,c1,.5+.5*n.y)*bord*lisere*(1.+.8*parle);'
      // Le modèle tient dans une boîte de côté 1 : ses mesures sont ramenées à celles de l'essai (côté 1,9).
      + 'vec3 m=vM*1.9;float flux=courant*(1.+parle)*pow(.5+.5*sin(atan(m.z,m.x)*2.+m.y*5.-temps*2.6),6.)+seve*pow(.5+.5*sin(m.y*7.-length(m.xz)*3.-temps*1.5),5.);'
      // Avec la voix : des vagues de lumière montent dans le modèle, plus fortes quand la voix est forte.
      + 'flux+=parle*.9*pow(.5+.5*sin(m.y*8.-temps*7.),3.);'
      + 'c+=(t*1.5+mix(c2,c1,.6)*.3)*flux;'
      + 'gl_FragColor=vec4(c,1.);\n#include <colorspace_fragment>\n}',
  });
  const maille = new THREE.Mesh(geometrie, matiere); maille.scale.setScalar(recette.echelle); maille.rotation.x = recette.penche; porte.add(maille);

  /** Chaque nuage de points et sa force quand la forme est entièrement là ; chaque lueur et son opacité ; ce qui tourne. */
  const nuages: [Trois.ShaderMaterial, number][] = [], halos: [Trois.SpriteMaterial, number][] = [];
  const tours: [Trois.Object3D, 'y' | 'z', number][] = [];
  const allumer = (parent: Trois.Object3D, liste: Lumiere[], taille: number, force: number, fremit = .008) => {
    const p = constellation(THREE, liste, taille, fremit, communs);
    parent.add(p); nuages.push([p.material, force]);
    return p;
  };
  const pos = geometrie.getAttribute('position'), nor = geometrie.getAttribute('normal');
  /** Des paillettes sur la peau du modèle : ses propres sommets, pris au hasard, un peu décollés. */
  const paillettes = (n: number, c1: Trois.Color, c2: Trois.Color, taille: number, force: number) => {
    const peau: Lumiere[] = [], d = .012 / recette.echelle;
    for (let i = 0; i < n; i++) {
      const k = Math.floor(R() * pos.count);
      peau.push({ p: [pos.getX(k) + nor.getX(k) * d, pos.getY(k) + nor.getY(k) * d, pos.getZ(k) + nor.getZ(k) * d], c: entre(c1, c2, R()), e: R() });
    }
    allumer(maille, peau, taille, force, .008 / recette.echelle);
  };
  /** Un anneau de points où court une comète (sa tête est blanche, sa traîne s'éteint). */
  const orbiter = ([rayon, plat, bascule, direction, vitesse, couleur]: Orbite, n: number, fin: number, taille: number) => {
    const liste: Lumiere[] = [], tete = R() * 6.283185, traine = 1.6 + R() * 1.4, base = teinte(couleur);
    for (let i = 0; i < n; i++) {
      const th = R() * 6.283185, retard = ((tete - th) % 6.283185 + 6.283185) % 6.283185, vif = Math.exp(-retard / traine);
      const r = rayon + gauss() * fin * (1 + 2.5 * (1 - vif));
      liste.push({ p: [Math.cos(th) * r, Math.sin(th) * r * plat, gauss() * fin], c: entre(base, BLANC, vif * vif * .6), e: .04 + vif * .96 * (.55 + .45 * R()) });
    }
    const pivot = new THREE.Group();
    pivot.rotation.order = 'YXZ'; pivot.rotation.set(bascule, direction, 0); groupe.add(pivot);
    tours.push([allumer(pivot, liste, taille, 1), 'z', vitesse]);
  };
  /** De la poussière autour du modèle, entre deux rayons, un peu aplatie ; elle tourne lentement. */
  const saupoudrer = (n: number, r0: number, r1: number, c1: Trois.Color, c2: Trois.Color, c3: Trois.Color, vitesse: number) => {
    const liste: Lumiere[] = [];
    for (let i = 0; i < n; i++) {
      const [x, y, z] = surSphere(r0 + Math.pow(R(), .7) * (r1 - r0));
      liste.push({ p: [x, y * .75, z], c: entre(c1, R() < .25 ? c3 : c2, R() * .7), e: R() });
    }
    tours.push([allumer(groupe, liste, .042, .85), 'y', vitesse]);
  };
  // Une lueur derrière le modèle, qui la cache là où il est devant elle.
  for (const [couleur, opacite, taille] of [['#3f7dff', .4, 4.4], ['#8f5cff', .16, 6.2]] as [string, number, number][]) {
    const s = lueur(teinte(couleur), 0, taille);
    s.position.z = -1.7; groupe.add(s); halos.push([s.material, opacite]);
  }

  if (nom === 'cosmique') {
    paillettes(4200, GLACE, ROSE, .024, .8);
    for (const o of ORBITES_COSMIQUE) orbiter(o, 2600, .01, .046);
    saupoudrer(2400, 1.25, 2.8, GLACE, MAGENTA, AMBRE, -.03);
  } else if (nom === 'organique') {
    paillettes(5200, GLACE, BLANC, .03, .95);
    // Une étoile à chaque pointe de pétale : le sommet le plus éloigné du centre dans chaque direction (vu de face),
    // gardé seulement s'il dépasse ses deux voisins (une pointe, pas un bord).
    const secteurs = 36, loin = new Float32Array(secteurs), qui = new Int32Array(secteurs).fill(-1);
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), y = pos.getY(i), dist = Math.hypot(x, y), s = Math.floor((Math.atan2(y, x) / 6.283185 + .5) * secteurs) % secteurs;
      if (dist > loin[s]) { loin[s] = dist; qui[s] = i; }
    }
    for (let s = 0; s < secteurs; s++) {
      if (qui[s] < 0 || loin[s] <= loin[(s + 1) % secteurs] || loin[s] < loin[(s + secteurs - 1) % secteurs]) continue;
      const etoile = lueur(GLACE, 0, .32 / recette.echelle);
      etoile.position.set(pos.getX(qui[s]), pos.getY(qui[s]), pos.getZ(qui[s]) + .012); maille.add(etoile); halos.push([etoile.material, .95]);
    }
    // La tige de lumière, du socle au pied de la fleur : large en bas, fine en haut.
    const tige: Lumiere[] = [], haut = pied + .35;
    for (let i = 0; i < 5200; i++) {
      const t = Math.pow(R(), 1.2), r = (.6 + (.03 - .6) * t) * Math.abs(gauss()) * .6, a = R() * 6.283185;
      tige.push({ p: [Math.cos(a) * r, sol + (haut - sol) * t, Math.sin(a) * r], c: entre(CYAN, GLACE, t), e: .15 + R() * .7 });
    }
    tours.push([allumer(groupe, tige, .032, .9), 'y', .4]);
    // Deux rubans de pollen qui montent en hélice autour de la fleur.
    const pollen: Lumiere[] = [];
    for (let i = 0; i < 5200; i++) {
      const t = R(), brin = Math.floor(R() * 2), a = brin * Math.PI + t * 7.5 + gauss() * .05, r = 1.95 - .75 * t + gauss() * .02;
      pollen.push({ p: [Math.cos(a) * r, -1.45 - recette.y + 3 * t + gauss() * .02, Math.sin(a) * r], c: entre(CYAN, ROSE, t), e: .25 + R() * .75 });
    }
    tours.push([allumer(groupe, pollen, .042, 1), 'y', .28]);
    saupoudrer(1300, 1, 2.6, GLACE, ROSE, CYAN, -.04);
  } else {
    paillettes(6500, GLACE, CYAN, .026, .9);
    for (const o of ORBITES_ENERGIE) orbiter(o, 3000, .012, .044);
    // Le tourbillon à cinq brins, du pied des rubans jusqu'au socle.
    const spire: Lumiere[] = [], haut = pied + .3;
    for (let i = 0; i < 7500; i++) {
      const t = Math.pow(R(), .8), brin = Math.floor(R() * 5), r = .5 * Math.pow(1 - t, 1.6) + .02 + Math.abs(gauss()) * .015, a = brin * 1.2566 + t * 8.5 + gauss() * .1;
      spire.push({ p: [Math.cos(a) * r, haut + (sol - haut) * t, Math.sin(a) * r], c: entre(MAGENTA, CYAN, t), e: .15 + R() * .8 });
    }
    tours.push([allumer(groupe, spire, .032, .95), 'y', 1.3]);
    saupoudrer(1300, .8, 2.5, GLACE, MAGENTA, CYAN, .05);
  }

  return {
    groupe,
    voix: { y: recette.y, tour: recette.tour },
    avancer(t, dt, vue, vivant, parle = 0) {
      groupe.visible = vue > .01;
      if (!groupe.visible) return;
      matiere.uniforms.app.value = Math.min(1, vue * 1.15);
      matiere.uniforms.parle.value = parle;
      // Avec la voix : les points et les lueurs s'avivent, les comètes et la poussière filent, le modèle bat.
      for (const [m, force] of nuages) m.uniforms.force.value = force * vue * (1 + .7 * parle);
      for (const [m, opacite] of halos) m.opacity = opacite * vue * (1 + .5 * parle);
      groupe.scale.setScalar((.86 + .14 * vue) * (1 + .05 * parle));
      if (!vivant) return;
      for (const [objet, axe, vitesse] of tours) objet.rotation[axe] += vitesse * dt * (1 + 1.5 * parle);
      // Le modèle ne tourne ni ne se balance plus (ID Studio IA, 11/11/2026, devant Cosmique dans le vrai AURORA : « tu peux
      // couper la rotation des nouveaux modèles 3D, mets les paramètres du noyau par défaut »). Il bouge comme le
      // noyau (noyau.ts, avancer) : il flotte de 0,05 et respire de 1,2 %, toujours de face. Les comètes et la
      // poussière autour, elles, tournent encore.
      porte.position.y = .05 * Math.sin(t * .6); porte.scale.setScalar(1 + .012 * Math.sin(t * 1.1));
    },
    liberer() {
      groupe.removeFromParent();
      libererObjets(groupe);   // géométries et matières ; la texture du modèle est à la forme
      texture?.dispose();
    },
  };
}
