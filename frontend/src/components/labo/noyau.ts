// Le noyau holographique de l'accueil (cahier des charges « L'évolution holographique d'Aurora », § 2 et 3, 08/11/2026 ;
// maquette : reviews/maquettes-accueil/1-reference-accueil.webp). Il prend la place du globe au centre ; le globe reste
// en secours (bouton « Globe », ou d'office sans WebGL). Même schéma que le globe de Monde 3D (hologram/scene.ts) :
// construit une fois, piloté ensuite par `PiloteNoyau`, tout libéré à la fin.
// Ce n'est qu'une animation : chaque état vient d'un signal réel de l'interface (voir useEtatNoyau, Laboratoire.tsx).
import type * as Trois from 'three';
import type { SceneDescription } from '../../lib/types';
import { VERTEX_NORMALE, VERTEX_UV } from '../hologram/shaders';
import { creerForme, NOMS_FORMES, type ChoixForme, type FormeNoyau, type NomForme } from './formes';
import { creerNuee, formeRepos, semerScene, type CommunsNuee, type Semis } from './nuee';
import { construireScene, libererObjets, type PieceFichier, type SceneLabo, type VueOrigine } from './scene';

type Three = typeof Trois;

/** Génération, exploration et simulation servent aux scènes du laboratoire (phase 3). Mémoire et action (11/11/2026) :
 *  AURORA lit ou écrit sa mémoire, ou lance un outil qui n'est pas une recherche. */
export type EtatNoyau = 'repos' | 'ecoute' | 'parole' | 'analyse' | 'recherche' | 'memoire' | 'action' | 'generation' | 'exploration' | 'simulation' | 'erreur';

export interface PiloteNoyau {
  /** La taille du canevas en pixels CSS. */
  redimensionner(l: number, h: number): void;
  etat(e: EtatNoyau): void;
  /** Le choix d’ID Studio IA sur le bouton « Noyau » de l'accueil (11/11/2026) : « auto » (la forme suit le travail d'AURORA),
   *  « noyau » (le noyau seul, toujours) ou une forme tenue à la main. Une forme choisie à la main ne dit rien du
   *  travail en cours : l'état réel reste écrit sous le noyau. */
  forme(choix: ChoixForme): void;
  /** Lu à chaque image en écoute et en parole : le niveau réel de la voix, de 0 à 1 (lib/voice.ts). */
  niveau(lire: () => number): void;
  mouvementReduit(on: boolean): void;
  /** Les animations avancées (particules, perles, ondes). Coupées : 30 images par seconde au plus. */
  effets(on: boolean): void;
  /** Les photos du sujet (ui.lieux), à gauche puis à droite du noyau, à des adresses qu'une texture peut lire
   *  (vignettesCommons) ; null : ce côté reste vide. null pour la liste : aucune photo. */
  photos(liste: (PhotoNoyau | null)[] | null): void;
  /** La photo sous ce point (pixels CSS du canevas) : 0 à gauche, 1 à droite, ou null. */
  photoSous(x: number, y: number): number | null;
  survolPhoto(i: number | null): void;
  /** Banc d'essai : n images d'affilée, la carte graphique attendue à chaque fois (en lisant un pixel). Renvoie le coût
   *  moyen d'une image en ms. Attention : si le navigateur cale la lecture sur son affichage (panneau limité à 30 images
   *  par seconde), le banc mesure cette attente ; le coût réel est alors dans data-ms-gpu et data-ms-cpu du canevas. */
  banc(n: number): number;
  // ── Phase 3 : la scène du laboratoire (scene.ts), posée sur le socle ; le noyau se retire dans un coin de la vue.
  /** Une description déjà validée (validerScene), ou null : retour au noyau seul. */
  scene(d: SceneDescription | null): void;
  eclater(on: boolean): void;
  animer(on: boolean): void;
  interieur(on: boolean): void;
  /** 12/11/2026 : la vue d'origine d'un modèle importé en GLB (scene.ts, VueOrigine), posée sur la scène affichée à la
   *  place de son hologramme (donc après `scene`) ; null : retour à l'hologramme. */
  origine(v: VueOrigine | null): void;
  /** En vue d'origine : lit l'animation du fichier de ce rang, ou l'arrête (null). */
  animation(rang: number | null): void;
  visibles(masquees: string[], isolees: string[] | null): void;
  selection(id: string | null): void;
  survol(id: string | null): void;
  signaler(ids: string[]): void;
  /** La caméra vise ces pièces (null : vue d'ensemble prévue par la scène) ; `serre` : au plus près. */
  viser(ids: string[] | null, serre?: boolean): void;
  /** Glisser (pixels) et molette ou pincement : la caméra tourne autour de la scène et s'en approche. */
  tourner(dx: number, dy: number): void;
  zoom(dy: number): void;
  /** La pièce sous ce point (pixels CSS du canevas), ou null. */
  choisir(x: number, y: number): string | null;
  /** 12/11/2026 : en vue d'origine, la pièce du fichier sous ce point, si le fichier décrit ses pièces ; null sinon. */
  choisirFichier(x: number, y: number): PieceFichier | null;
  /** Pixels couverts à droite par une fiche : la vue se recentre dans la place qui reste (0 : aucune fiche). */
  marge(px: number): void;
}
export interface SceneNoyau { pilote: PiloteNoyau; liberer(): void }
export interface PhotoNoyau { url: string; nom: string }

/** `voix` allume aussi l'habillage du globe autour du noyau (arcs, cercles, ondes de la voix) ; `spheres` fait filer les
 *  perles des anneaux, grossies et suivies d'une traînée (08/11/2026, demande du propriétaire : des sphères qui tournent
 *  quand AURORA réfléchit, l'animation de la Terre quand elle parle). */
interface Reglage { vitesse: number; intensite: number; alerte: number; liens: number; onde: number; retrait: number; voix: number; spheres: number }
/** Ce que chaque état change ; on glisse d'un réglage à l'autre en une seconde environ, sans rien reconstruire.
 *  `memoire` et `action` (11/11/2026) : AURORA vient de lire ou d'écrire sa mémoire, ou de lancer un outil. */
const REGLAGES: Record<EtatNoyau, Reglage> = {
  repos: { vitesse: 1, intensite: .8, alerte: 0, liens: .15, onde: 0, retrait: 0, voix: 0, spheres: 0 },
  ecoute: { vitesse: 1.2, intensite: .95, alerte: 0, liens: .3, onde: .5, retrait: 0, voix: 1, spheres: 0 },
  parole: { vitesse: 1.2, intensite: .95, alerte: 0, liens: .3, onde: .3, retrait: 0, voix: 1, spheres: .4 },
  analyse: { vitesse: 2.6, intensite: 1, alerte: 0, liens: 1, onde: .6, retrait: 0, voix: 0, spheres: 1 },
  recherche: { vitesse: 2.2, intensite: 1, alerte: 0, liens: .8, onde: 1, retrait: 0, voix: 0, spheres: 1 },
  memoire: { vitesse: 1.6, intensite: 1, alerte: 0, liens: .6, onde: .5, retrait: 0, voix: 0, spheres: .6 },
  action: { vitesse: 2.8, intensite: 1.05, alerte: 0, liens: 1, onde: .9, retrait: 0, voix: 0, spheres: 1 },
  generation: { vitesse: 3, intensite: 1.1, alerte: 0, liens: 1, onde: .8, retrait: .3, voix: 0, spheres: .7 },
  exploration: { vitesse: .6, intensite: .45, alerte: 0, liens: .1, onde: 0, retrait: 1, voix: 0, spheres: 0 },
  simulation: { vitesse: .6, intensite: .45, alerte: 0, liens: .1, onde: 0, retrait: 1, voix: 0, spheres: 0 },
  erreur: { vitesse: .5, intensite: .7, alerte: 1, liens: .05, onde: 0, retrait: 0, voix: 0, spheres: 0 },
};
const CLES = Object.keys(REGLAGES.repos) as (keyof Reglage)[];
/** En choix « Automatique », la forme que prend le noyau pour chaque travail réel d'AURORA (11/11/2026, ID Studio IA : « des
 *  formes différentes suivant son travail ») : elle cherche ou regarde le monde, elle lit ou écrit sa mémoire, elle
 *  lance un outil. Chacun de ces états vient d'un vrai pas d'exécution (lib/store.tsx : searching, remembering, acting). */
const FORME_DU_TRAVAIL: Partial<Record<EtatNoyau, NomForme>> = { recherche: 'cosmique', memoire: 'organique', action: 'energie' };

/** Qualité adaptative, sur deux mesures réelles : on descend d'un cran quand on dessine moins de 45 images par
 *  seconde ET qu'une image coûte plus de 12 ms (calcul complet, carte graphique comprise). Le seul nombre d'images ne
 *  suffit pas : une fenêtre sans le focus ou en arrière-plan en reçoit 16 à 30 par seconde, si légère soit l'image. */
const QUALITES = [
  { ratio: .75, particules: 4000 },
  { ratio: 1, particules: 8000 },
  { ratio: 2, particules: 14000 },
];
/** Animations coupées : la nuée garde sa forme avec moins de points (les étincelles en font partie). */
const PARTICULES_SANS_EFFETS = 3000;
const MAX_PARTICULES = QUALITES[QUALITES.length - 1].particules;
const SEUIL_LENT = 45;
const COUT_MAX_MS = 12;

const RAD = Math.PI / 180;
const Y_SOCLE = -1.62;
/** Pour les faisceaux : coordonnées de texture et normale vue, pour des bords qui s'effacent (sinon un bloc plein). */
const VERTEX_FAISCEAU = 'varying vec2 vUv;varying vec3 n;void main(){vUv=uv;n=normalize(normalMatrix*normal);gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}';

function lueur(g: CanvasRenderingContext2D, n: number) {
  const r = g.createRadialGradient(n / 2, n / 2, 0, n / 2, n / 2, n / 2);
  r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(.22, 'rgba(255,255,255,.6)'); r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r; g.fillRect(0, 0, n, n);
}

/** Le tracé du « Λ » d'AURORA et de son petit triangle, sur un carré de 256 (le logo et sa lueur). */
const jambesA = (g: CanvasRenderingContext2D) => { g.beginPath(); g.moveTo(50, 212); g.lineTo(128, 50); g.lineTo(206, 212); };
const pointeA = (g: CanvasRenderingContext2D) => { g.beginPath(); g.moveTo(128, 138); g.lineTo(158, 200); g.lineTo(98, 200); g.closePath(); };

/** Le symbole d'AURORA (maquette 1) : un grand « Λ » plein et un petit triangle dedans, blanc vers cyan puis violet. */
function logoA(g: CanvasRenderingContext2D, n: number) {
  g.scale(n / 256, n / 256);
  const d = g.createLinearGradient(0, 44, 0, 214);
  d.addColorStop(0, '#f0fdff'); d.addColorStop(.45, '#5cd6ff'); d.addColorStop(1, '#b65cff');
  g.lineJoin = 'miter'; g.lineCap = 'butt'; g.strokeStyle = d; g.fillStyle = d; g.shadowColor = 'rgba(110,200,255,.95)';
  for (const flou of [38, 0]) {
    g.shadowBlur = flou; g.lineWidth = 30;
    jambesA(g); g.stroke(); pointeA(g); g.fill();
  }
  g.shadowBlur = 0; g.lineWidth = 5; g.strokeStyle = 'rgba(255,255,255,.7)';
  jambesA(g); g.stroke();
}

/** La lueur du « A » (maquettes : un néon qui déborde, cyan en haut, violet en bas), sans post-traitement : sa forme
 *  très floue. Seule l'ombre est gardée (le tracé est dessiné hors du cadre, son ombre ramenée dedans), puis teintée. */
function lueurA(g: CanvasRenderingContext2D, n: number) {
  g.save(); g.translate(-2 * n, 0); g.scale(n / 256, n / 256);
  g.shadowOffsetX = 2 * n; g.shadowBlur = n * .11; g.shadowColor = '#fff';
  g.lineJoin = 'round'; g.lineWidth = 38; g.strokeStyle = g.fillStyle = '#fff';
  jambesA(g); g.stroke(); pointeA(g); g.fill(); g.restore();
  const d = g.createLinearGradient(0, n * .2, 0, n * .85);
  d.addColorStop(0, '#5ad2ff'); d.addColorStop(1, '#a55cff');
  g.globalCompositeOperation = 'source-in'; g.fillStyle = d; g.fillRect(0, 0, n, n);
}

/** null : pas de WebGL. surPanne(raison) : le moteur s'arrête en route (contexte WebGL perdu par le pilote graphique
 *  ou la veille, erreur pendant une image) → le globe prend le relais. */
export function creerNoyau(THREE: Three, canvas: HTMLCanvasElement, surPanne: (raison: string) => void): SceneNoyau | null {
  let rendu: Trois.WebGLRenderer;
  try { rendu = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true }); } catch { return null; }
  let annule = false, image = 0, qualite = QUALITES.length - 1;
  let l = Math.max(1, canvas.clientWidth), h = Math.max(1, canvas.clientHeight);
  const ratio = () => Math.min(window.devicePixelRatio || 1, QUALITES[qualite].ratio);
  rendu.setPixelRatio(ratio()); rendu.setSize(l, h, false);
  const perte = (e: Event) => { e.preventDefault(); if (!annule) surPanne('le moteur 3D a perdu son contexte graphique.'); };
  canvas.addEventListener('webglcontextlost', perte);

  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(30, l / h, .1, 60);
  const CIBLE = new THREE.Vector3(0, -.25, 0), posNoyau = new THREE.Vector3();
  /** Tout le noyau et son socle tiennent dans le cadre, quelle que soit la forme de la zone. Avec une scène, la caméra
   *  glisse de ce cadre vers l'orbite autour d'elle (avancer). */
  const cadrer = () => {
    const t = Math.tan(15 * RAD), d = Math.max(1.9 / t, 2.35 / (t * camera.aspect));
    posNoyau.set(0, CIBLE.y + d * .26, d * .97);
  };
  cadrer(); camera.position.copy(posNoyau); camera.lookAt(CIBLE);

  const CYAN = new THREE.Color('#38d6ff'), VIOLET = new THREE.Color('#8f5cff'), MAGENTA = new THREE.Color('#c65cff');
  const BLANC = new THREE.Color('#ffffff'), ROUGE = new THREE.Color('#ff4d6a');
  /** Les couleurs qui virent au rouge en cas d'erreur. */
  const teintes: { c: Trois.Color; base: Trois.Color }[] = [];
  const suivre = (c: Trois.Color, base: Trois.Color) => { c.copy(base); teintes.push({ c, base: base.clone() }); return c; };
  const additif = { transparent: true, depthWrite: false, blending: THREE.AdditiveBlending };
  const textures: Trois.Texture[] = [];
  const dessiner = (f: (g: CanvasRenderingContext2D, n: number) => void, n: number) => {
    const cv = document.createElement('canvas'); cv.width = cv.height = n;
    const g = cv.getContext('2d'); if (g) f(g, n);
    const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; textures.push(t); return t;
  };
  const texLueur = dessiner(lueur, 64), texLogo = dessiner(logoA, 256), texLueurLogo = dessiner(lueurA, 128);
  const cercle = (r: number, y = 0, n = 96) => {
    const p: Trois.Vector3[] = [];
    for (let i = 0; i <= n; i++) { const a = i / n * Math.PI * 2; p.push(new THREE.Vector3(Math.cos(a) * r, y, Math.sin(a) * r)); }
    return new THREE.BufferGeometry().setFromPoints(p);
  };

  // ── Le noyau : une sphère de lumière (bord cyan en haut, violet en bas), sa grille, un réseau de nœuds reliés,
  // une lueur intérieure et le « A » qui fait toujours face.
  const noyau = new THREE.Group(); scene.add(noyau);
  const tourne = new THREE.Group(); noyau.add(tourne);
  const matHalo = new THREE.ShaderMaterial({
    ...additif, uniforms: { c1: { value: suivre(new THREE.Color(), CYAN) }, c2: { value: suivre(new THREE.Color(), VIOLET) }, force: { value: .8 } },
    vertexShader: VERTEX_NORMALE,
    fragmentShader: 'uniform vec3 c1;uniform vec3 c2;uniform float force;varying vec3 n;varying vec3 p;void main(){float f=pow(1.-abs(dot(n,vec3(0.,0.,1.))),1.9);gl_FragColor=vec4(mix(c2,c1,p.y*.5+.5),(f*1.5+.08)*force);}',
  });
  noyau.add(new THREE.Mesh(new THREE.SphereGeometry(1, 64, 48), matHalo));
  const matGrille = new THREE.LineBasicMaterial({ ...additif, opacity: .16 }); suivre(matGrille.color, CYAN);
  for (let lat = -60; lat <= 60; lat += 30) { const a = lat * RAD; tourne.add(new THREE.Line(cercle(Math.cos(a), Math.sin(a), 72), matGrille)); }
  for (let lon = 0; lon < 180; lon += 30) { const m = new THREE.Line(cercle(1, 0, 72), matGrille); m.rotation.x = Math.PI / 2; m.rotation.z = lon * RAD; tourne.add(m); }

  const N = 150, or = Math.PI * (3 - Math.sqrt(5)), noeuds: Trois.Vector3[] = [];
  for (let i = 0; i < N; i++) {
    const y = 1 - 2 * (i + .5) / N, r = Math.sqrt(1 - y * y), a = i * or;
    noeuds.push(new THREE.Vector3(Math.cos(a) * r * .96, y * .96, Math.sin(a) * r * .96));
  }
  const matNoeuds = new THREE.PointsMaterial({ ...additif, map: texLueur, size: .085, opacity: .9 }); suivre(matNoeuds.color, new THREE.Color('#9ff4ff'));
  tourne.add(new THREE.Points(new THREE.BufferGeometry().setFromPoints(noeuds), matNoeuds));
  // Les liens : chaque nœud vers ses trois voisins ; un signal les parcourt, plus souvent pendant une analyse.
  const segs: number[] = [], phases: number[] = [], bouts: number[] = [], vus = new Set<string>(), paires: [number, number][] = [];
  noeuds.forEach((a, i) => {
    noeuds.map((b, j) => ({ j, d: a.distanceToSquared(b) })).filter(x => x.j !== i).sort((x, y) => x.d - y.d).slice(0, 3).forEach(({ j }) => {
      const cle = i < j ? `${i}-${j}` : `${j}-${i}`;
      if (vus.has(cle)) return;
      vus.add(cle); paires.push([i, j]);
      const b = noeuds[j], ph = (i * 0.618 + j * 0.382) % 1;
      segs.push(a.x, a.y, a.z, b.x, b.y, b.z); phases.push(ph, ph); bouts.push(0, 1);
    });
  });
  const gLiens = new THREE.BufferGeometry();
  gLiens.setAttribute('position', new THREE.Float32BufferAttribute(segs, 3));
  gLiens.setAttribute('ph', new THREE.Float32BufferAttribute(phases, 1));
  gLiens.setAttribute('bout', new THREE.Float32BufferAttribute(bouts, 1));
  const matLiens = new THREE.ShaderMaterial({
    ...additif, uniforms: { c: { value: suivre(new THREE.Color(), CYAN) }, temps: { value: 0 }, liens: { value: .15 }, force: { value: .8 } },
    vertexShader: 'attribute float ph;attribute float bout;varying float vp;varying float vb;void main(){vp=ph;vb=bout;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
    fragmentShader: 'uniform vec3 c;uniform float temps;uniform float liens;uniform float force;varying float vp;varying float vb;void main(){float s=fract(vp*3.+temps*.45);float d=(vb-s)*7.;gl_FragColor=vec4(c,.1*force+liens*exp(-d*d)*1.3);}',
  });
  tourne.add(new THREE.LineSegments(gLiens, matLiens));
  const sprite = (couleur: Trois.Color, opacite: number, taille: number, map = texLueur) => {
    const m = new THREE.SpriteMaterial({ ...additif, map, opacity: opacite }); suivre(m.color, couleur);
    const s = new THREE.Sprite(m); s.scale.setScalar(taille); return s;
  };
  // Les lueurs (maquettes, sans post-traitement) : l'aura violette, une brume bleue autour de la sphère, le cœur, et le
  // néon du « A » derrière lui.
  const aura = sprite(VIOLET, .2, 4.4), brume = sprite(new THREE.Color('#4a6bff'), .15, 3.1), coeur = sprite(new THREE.Color('#5a86ff'), .5, 2.1);
  const lueurLogo = sprite(BLANC, .6, 1.3, texLueurLogo), logo = sprite(BLANC, .95, 1.2, texLogo);
  noyau.add(aura, brume, coeur, lueurLogo, logo);

  // ── Trois anneaux en orbite, chacun avec ses perles de lumière.
  const ANNEAUX = [
    { r: 1.32, incl: [1.25, 0, .25], v: .35, perles: 2, c: CYAN },
    { r: 1.52, incl: [1.45, .5, -.2], v: -.25, perles: 1, c: MAGENTA },
    { r: 1.74, incl: [1.62, -.4, .1], v: .18, perles: 3, c: CYAN },
  ];
  const perles: Trois.Sprite[] = [], matsAnneaux: Trois.MeshBasicMaterial[] = [], matsLueursAnneaux: Trois.ShaderMaterial[] = [];
  /** Chaque perle a un halo (doux au repos) et une traînée (éteinte au repos) ; en réflexion elles filent autour du
   *  noyau (avancer). */
  const TRAINEE = 6;
  const filantes: { perle: Trois.Sprite; halo: Trois.Sprite; trainee: Trois.Sprite[]; ang: number; r: number; dir: number }[] = [];
  const anneaux = ANNEAUX.map(a => {
    const pivot = new THREE.Group(); pivot.rotation.set(a.incl[0], a.incl[1], a.incl[2]); noyau.add(pivot);
    const roue = new THREE.Group(); pivot.add(roue);
    const mat = new THREE.MeshBasicMaterial({ ...additif, opacity: .5 }); suivre(mat.color, a.c); matsAnneaux.push(mat);
    roue.add(new THREE.Mesh(new THREE.TorusGeometry(a.r, .008, 6, 200), mat));
    // Sa lueur (maquettes) : un tube plus large, clair en son milieu et fondu sur ses bords.
    const lueurAnneau = new THREE.ShaderMaterial({
      ...additif, uniforms: { c: { value: suivre(new THREE.Color(), a.c) }, o: { value: .2 } }, vertexShader: VERTEX_NORMALE,
      fragmentShader: 'uniform vec3 c;uniform float o;varying vec3 n;varying vec3 p;void main(){gl_FragColor=vec4(c,pow(abs(n.z),3.)*o);}',
    });
    matsLueursAnneaux.push(lueurAnneau);
    roue.add(new THREE.Mesh(new THREE.TorusGeometry(a.r, .045, 10, 200), lueurAnneau));
    for (let i = 0; i < a.perles; i++) {
      const p = sprite(a.c, .95, .13), ang = i / a.perles * Math.PI * 2;
      p.position.set(Math.cos(ang) * a.r, Math.sin(ang) * a.r, 0); roue.add(p); perles.push(p);
      const halo = sprite(a.c, 0, .4); halo.position.copy(p.position); halo.visible = false; roue.add(halo);
      const trainee = Array.from({ length: TRAINEE }, () => { const s = sprite(a.c, 0, .1); s.visible = false; roue.add(s); return s; });
      filantes.push({ perle: p, halo, trainee, ang, r: a.r, dir: Math.sign(a.v) });
    }
    return { roue, v: a.v, dir: Math.sign(a.v) };
  });

  // ── En écoute et en parole : l'habillage du globe de l'accueil (globe/engine.ts, drawHud) autour du noyau. Ondes de la
  // voix, un cercle fin, un cercle pointillé qui tourne, trois arcs lumineux qui tournent en sens inverse, un cadran
  // gradué et une mire de chaque côté. Il fait toujours face à la caméra (avancer) et reste éteint au repos.
  const hud = new THREE.Group(); hud.visible = false; noyau.add(hud);
  /** Chaque matière de l'habillage et son opacité une fois allumé. */
  const matsHud: [Trois.Material, number][] = [];
  const matHud = <M extends Trois.MeshBasicMaterial | Trois.LineBasicMaterial>(m: M, couleur: string, on: number) => {
    suivre(m.color, new THREE.Color(couleur)); matsHud.push([m, on]); return m;
  };
  const segments = (pts: number[], m: Trois.LineBasicMaterial) => {
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    const s = new THREE.LineSegments(g, m); hud.add(s); return s;
  };
  hud.add(new THREE.Mesh(new THREE.RingGeometry(1.056, 1.064, 128), matHud(new THREE.MeshBasicMaterial({ ...additif, opacity: 0 }), '#78d2ff', .4)));
  const pts: number[] = [];
  for (let i = 0; i < 90; i++) {
    const a = i / 90 * Math.PI * 2, b = a + .022;
    pts.push(Math.cos(a) * 1.1, Math.sin(a) * 1.1, 0, Math.cos(b) * 1.1, Math.sin(b) * 1.1, 0);
  }
  const pointille = segments(pts, matHud(new THREE.LineBasicMaterial({ ...additif, opacity: 0 }), '#22c8ff', .6));
  const tourArcs = new THREE.Group(); hud.add(tourArcs);
  const matArc = matHud(new THREE.MeshBasicMaterial({ ...additif, opacity: 0 }), '#2faaff', .9);
  const matArcLueur = matHud(new THREE.MeshBasicMaterial({ ...additif, opacity: 0 }), '#22c8ff', .22);
  for (let i = 0; i < 3; i++) {
    const a0 = i * Math.PI * 2 / 3, long = 1.05;
    tourArcs.add(new THREE.Mesh(new THREE.RingGeometry(1.138, 1.152, 48, 1, a0, long), matArc));
    tourArcs.add(new THREE.Mesh(new THREE.RingGeometry(1.118, 1.172, 48, 1, a0, long), matArcLueur));
    const bout = sprite(new THREE.Color('#bef0ff'), 0, .085); matsHud.push([bout.material, .95]);
    bout.position.set(Math.cos(a0 + long) * 1.145, Math.sin(a0 + long) * 1.145, 0); tourArcs.add(bout);
  }
  pts.length = 0;
  for (let i = 0; i < 120; i++) {
    const a = i / 120 * Math.PI * 2, r1 = 1.19 + (i % 10 === 0 ? .03 : .012);
    pts.push(Math.cos(a) * 1.19, Math.sin(a) * 1.19, 0, Math.cos(a) * r1, Math.sin(a) * r1, 0);
  }
  segments(pts, matHud(new THREE.LineBasicMaterial({ ...additif, opacity: 0 }), '#78d2ff', .42));
  pts.length = 0;
  for (const s of [-1, 1]) {   // la mire, au-delà de l'anneau extérieur (rayon 1,74)
    pts.push(s * 1.84, 0, 0, s * 2, 0, 0);
    for (let k = 0; k < 4; k++) { const x = s * (1.87 + k * .04); pts.push(x, -.03, 0, x, .03, 0); }
  }
  segments(pts, matHud(new THREE.LineBasicMaterial({ ...additif, opacity: 0 }), '#22c8ff', .55));
  const ondesVoix = [0, 1, 2].map(() => {
    const m = new THREE.MeshBasicMaterial({ ...additif, opacity: 0 }); suivre(m.color, new THREE.Color('#22c8ff'));
    const o = new THREE.Mesh(new THREE.RingGeometry(.984, 1, 128), m); hud.add(o); return o;
  });

  // ── Les photos du sujet (08/11/2026, demande du propriétaire : « les images du sujet, 2 images dans la partie centrale,
  // de chaque côté de la sphère, dans l'hologramme », d'après une galerie de panneaux courbes en three.js). Deux panneaux
  // un peu courbés qui entourent le noyau : la photo teintée de lumière, des lignes de balayage, un cadre lumineux, le nom
  // du lieu dessous. Une photo se matérialise de bas en haut à son arrivée, une fois l'ancienne effacée ; les panneaux
  // s'effacent devant une scène, et se cachent quand la place manque à côté du noyau.
  const RATIO_PHOTO = .75, COURBE = 2.2;
  const gPhoto = new THREE.PlaneGeometry(1, RATIO_PHOTO, 32, 1), sommets = gPhoto.attributes.position;
  for (let i = 0; i < sommets.count; i++) {
    const a = sommets.getX(i) / COURBE;   // les bords avancent vers la caméra
    sommets.setXYZ(i, Math.sin(a) * COURBE, sommets.getY(i), (1 - Math.cos(a)) * COURBE);
  }
  const gEtiquette = new THREE.PlaneGeometry(1, 80 / 512);
  // La photo en « couverture » (recadrée au format du panneau), teintée, balayée ; elle apparaît sous la ligne `app`.
  const FRAGMENT_PHOTO = 'uniform sampler2D carte;uniform float ri;uniform vec3 c;uniform float temps;uniform float app;'
    + 'uniform float survol;uniform float force;uniform float anim;varying vec2 vUv;void main(){'
    + `vec2 uv=vUv;float k=${(1 / RATIO_PHOTO).toFixed(4)}/ri;if(k<1.)uv.x=.5+(uv.x-.5)*k;else uv.y=.5+(uv.y-.5)/k;`
    + 'vec3 im=texture2D(carte,uv).rgb;float lum=dot(im,vec3(.299,.587,.114));'
    // 11/11/2026 (ID Studio IA : « les photos sont un peu troubles et il manque un peu de lumière ») : la teinte passe de 30 %
    // à 10 %, les lignes de balayage de 14 % à 4 %, les ombres sont ouvertes et l'image éclaircie de 12 %.
    + 'vec3 col=mix(im,c*(lum*1.5+.08),.1);'
    + 'col=pow(col,vec3(.88))*1.12*(1.+.22*survol)*(.96+.04*sin(vUv.y*260.+temps*4.*anim));'
    + 'float bande=exp(-pow((vUv.y-fract(1.-temps*.11))*16.,2.))*.12*anim;'
    + 'float e=min(min(vUv.x,1.-vUv.x),min(vUv.y,1.-vUv.y));'
    + 'float bord=smoothstep(.012,0.,e)*(.75+.25*survol)+smoothstep(.07,0.,e)*.12;'
    + 'float front=app*1.08;float ligne=exp(-pow((vUv.y-front)*40.,2.))*(1.-step(1.,app));'
    + 'float clin=1.-.03*anim*step(.965,fract(sin(floor(temps*9.)*43.758)*917.3));'
    + 'gl_FragColor=vec4(col+c*(bord+bande+ligne*1.5),(min(1.,smoothstep(0.,.035,e)+bord)*step(vUv.y,front)+ligne)*force*clin);}';
  interface PanneauPhoto {
    cote: number; groupe: Trois.Group; cadre: Trois.Mesh; mat: Trois.ShaderMaterial; matEt: Trois.MeshBasicMaterial; halo: Trois.Sprite;
    /** La photo affichée, et celle qui attend que l'ancienne soit effacée. */
    url: string; tex: Trois.Texture | null; texEt: Trois.Texture | null;
    attente: { url: string; tex: Trois.Texture; et: Trois.Texture; ri: number } | null;
    voulu: boolean; vis: number; app: number; survol: number;
  }
  const panneaux: PanneauPhoto[] = [-1, 1].map(cote => {
    const groupe = new THREE.Group(); groupe.visible = false; groupe.rotation.y = -cote * .32; scene.add(groupe);
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, side: THREE.DoubleSide,
      uniforms: {
        carte: { value: null }, ri: { value: 4 / 3 }, c: { value: suivre(new THREE.Color(), CYAN) }, temps: { value: 0 },
        app: { value: 0 }, survol: { value: 0 }, force: { value: 0 }, anim: { value: 1 },
      },
      vertexShader: VERTEX_UV, fragmentShader: FRAGMENT_PHOTO,
    });
    const cadre = new THREE.Mesh(gPhoto, mat); groupe.add(cadre);
    const matEt = new THREE.MeshBasicMaterial({ ...additif, opacity: 0 }); suivre(matEt.color, BLANC);
    const etiquette = new THREE.Mesh(gEtiquette, matEt); etiquette.position.set(0, -RATIO_PHOTO / 2 - .1, .03); groupe.add(etiquette);
    const halo = sprite(CYAN, 0, 1.4); halo.position.z = -.12; groupe.add(halo);
    return { cote, groupe, cadre, mat, matEt, halo, url: '', tex: null, texEt: null, attente: null, voulu: false, vis: 0, app: 0, survol: 0 };
  });
  /** Faux quand la place manque à côté du noyau (zone étroite) : les photos se cachent, la légende reste (Laboratoire). */
  let placePhotos = true, photoSurvolee: number | null = null, seqPhotos = 0;
  /** Du bord de la sphère au bord du cadre, à hauteur du noyau ; la marge couvre les bords qui avancent vers la caméra. */
  const placerPhotos = () => {
    // 11/11/2026 : jusqu'à 1,35 de large (1,15 avant) quand l'écran laisse la place : les détails se lisent mieux.
    const demi = posNoyau.distanceTo(CIBLE) * Math.tan(15 * RAD) * (l / h), larg = Math.min(1.35, demi - .16 - 1.12);
    placePhotos = larg >= .55;
    for (const p of panneaux) { p.groupe.scale.setScalar(larg); p.groupe.position.set(p.cote * (demi - .16 - larg / 2), .06, .1); }
  };
  placerPhotos();
  /** Le nom du lieu, écrit sous la photo (coupé d'un « … » s'il est trop long). */
  const etiquetteDe = (nom: string) => {
    const cv = document.createElement('canvas'); cv.width = 512; cv.height = 80;
    const g = cv.getContext('2d');
    if (g) {
      g.font = '600 34px "Segoe UI", system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
      let texte = nom;
      while (texte.length > 4 && g.measureText(texte).width > 480) texte = texte.slice(0, -2).trimEnd() + '…';
      g.shadowColor = 'rgba(80,200,255,.9)'; g.shadowBlur = 12; g.fillStyle = '#e6fbff'; g.fillText(texte, 256, 42);
    }
    const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; return t;
  };
  const chargeur = new THREE.TextureLoader();   // crossOrigin « anonymous » : thumb.wikimedia.org le permet
  const montrerPhotos = (liste: (PhotoNoyau | null)[] | null) => {
    const n = ++seqPhotos;
    panneaux.forEach((p, i) => {
      const ph = liste?.[i] ?? null;
      if (p.attente) { p.attente.tex.dispose(); p.attente.et.dispose(); p.attente = null; }
      if (ph && ph.url === p.url && p.voulu) return;   // la même photo reste
      p.voulu = false;
      if (!ph) return;
      chargeur.load(ph.url, tex => {
        if (annule || n !== seqPhotos) { tex.dispose(); return; }
        // Pas d'espace sRGB : le shader rend la photo telle quelle (la sortie n'est pas reconvertie).
        // 11/11/2026 : la photo est plus grande que son panneau (960 px) ; réduite par paliers et lue de biais avec
        // toute la finesse de la carte graphique, elle reste nette sur le panneau courbe et tourné.
        tex.minFilter = THREE.LinearMipmapLinearFilter; tex.generateMipmaps = true;
        tex.anisotropy = rendu.capabilities.getMaxAnisotropy();
        const im = tex.image as { width?: number; height?: number } | undefined;
        p.attente = { url: ph.url, tex, et: etiquetteDe(ph.nom), ri: im?.width && im.height ? im.width / im.height : 4 / 3 };
        sale = true;
      }, undefined, () => { /* photo refusée ou absente : ce côté reste vide */ });
    });
    sale = true;
  };

  // ── La nuée (nuee.ts, 09/11/2026) : des milliers de points qui dessinent le noyau (amas sur les nœuds, traits le long
  // des liens, poussière de la sphère, étincelles autour). Elle remplace les 520 particules semées au hasard autour du
  // noyau ; la qualité en dessine plus ou moins.
  const communs: CommunsNuee = { temps: { value: 0 }, echelle: { value: 1 }, scintille: { value: 1 } };
  const { repos, aleat } = formeRepos(MAX_PARTICULES, noeuds, paires);
  const nuee = creerNuee(THREE, repos, aleat, suivre(new THREE.Color(), CYAN), suivre(new THREE.Color(), MAGENTA), communs);
  scene.add(nuee.points);

  // ── Le socle : cercles concentriques, graduations, disque lumineux, ondes et faisceau vers le noyau.
  const socle = new THREE.Group(); socle.position.y = Y_SOCLE; scene.add(socle);
  const matCercles = new THREE.LineBasicMaterial({ ...additif, opacity: .5 }); suivre(matCercles.color, CYAN);
  const matCerclesV = new THREE.LineBasicMaterial({ ...additif, opacity: .5 }); suivre(matCerclesV.color, MAGENTA);
  [.45, .8, 1.15, 1.5, 1.95, 2.3].forEach((r, i) => socle.add(new THREE.Line(cercle(r, 0, 128), i % 2 ? matCerclesV : matCercles)));
  // Des arcs de lumière qui tournent sur le socle, deux sens opposés.
  const arcs = [{ r: 1.32, l: 1.1, c: CYAN, v: .22 }, { r: 1.78, l: .75, c: MAGENTA, v: -.15 }].map(a => {
    const tour = new THREE.Group(); socle.add(tour);
    const m = new THREE.MeshBasicMaterial({ ...additif, opacity: .8 }); suivre(m.color, a.c);
    for (const depart of [0, Math.PI]) {
      const arc = new THREE.Mesh(new THREE.TorusGeometry(a.r, .014, 4, 64, a.l), m);
      arc.rotation.set(-Math.PI / 2, 0, depart); tour.add(arc);
    }
    return { tour, v: a.v };
  });
  const grad: number[] = [];
  for (let i = 0; i < 96; i++) {
    const a = i / 96 * Math.PI * 2, r1 = i % 8 === 0 ? 1.8 : 1.7;
    grad.push(Math.cos(a) * 1.62, 0, Math.sin(a) * 1.62, Math.cos(a) * r1, 0, Math.sin(a) * r1);
  }
  const gGrad = new THREE.BufferGeometry(); gGrad.setAttribute('position', new THREE.Float32BufferAttribute(grad, 3));
  const graduations = new THREE.LineSegments(gGrad, matCercles); socle.add(graduations);
  const matDisque = new THREE.ShaderMaterial({
    ...additif, side: THREE.DoubleSide, uniforms: { c: { value: suivre(new THREE.Color(), CYAN) }, force: { value: .8 } },
    vertexShader: VERTEX_UV,
    fragmentShader: 'uniform vec3 c;uniform float force;varying vec2 vUv;void main(){float r=length(vUv-.5)*2.;float a=pow(max(1.-r,0.),2.2)*.8+smoothstep(.025,0.,abs(r-.62))*.4;gl_FragColor=vec4(c,a*force);}',
  });
  const disque = new THREE.Mesh(new THREE.CircleGeometry(2.1, 96), matDisque); disque.rotation.x = -Math.PI / 2; socle.add(disque);
  const ondes = [0, 1].map(() => {
    const m = new THREE.MeshBasicMaterial({ ...additif, side: THREE.DoubleSide, opacity: 0 }); suivre(m.color, CYAN);
    const o = new THREE.Mesh(new THREE.RingGeometry(.97, 1, 128), m); o.rotation.x = -Math.PI / 2; o.visible = false; socle.add(o);
    return o;
  });
  const matFaisceau = new THREE.ShaderMaterial({
    ...additif, side: THREE.DoubleSide, uniforms: { c: { value: suivre(new THREE.Color(), CYAN) }, force: { value: .8 } },
    vertexShader: VERTEX_FAISCEAU,
    fragmentShader: 'uniform vec3 c;uniform float force;varying vec2 vUv;varying vec3 n;void main(){gl_FragColor=vec4(c,pow(1.-vUv.y,1.6)*.5*pow(abs(n.z),1.2)*force);}',
  });
  const faisceau = new THREE.Mesh(new THREE.CylinderGeometry(.5, 1.05, 1.5, 64, 1, true), matFaisceau);
  faisceau.position.y = Y_SOCLE + .75; scene.add(faisceau);
  // Le pilier de lumière qui traverse le noyau, du socle jusqu'en haut de la zone, en s'effaçant.
  const matPilier = new THREE.ShaderMaterial({
    ...additif, side: THREE.DoubleSide, uniforms: { c: { value: suivre(new THREE.Color(), new THREE.Color('#7fe8ff')) }, force: { value: .8 } },
    vertexShader: VERTEX_FAISCEAU,
    // Plus pâle là où il traverse le noyau (hauteurs 0,17 à 0,71 du pilier) : le « A » reste lisible.
    fragmentShader: 'uniform vec3 c;uniform float force;varying vec2 vUv;varying vec3 n;void main(){float y=vUv.y;float dans=smoothstep(.12,.24,y)*(1.-smoothstep(.64,.76,y));'
      + 'gl_FragColor=vec4(c,(pow(1.-y,1.3)*.5+.06)*(1.-.75*dans)*pow(abs(n.z),3.)*force);}',
  });
  const pilier = new THREE.Mesh(new THREE.CylinderGeometry(.16, .26, 3.7, 32, 1, true), matPilier);
  pilier.position.y = Y_SOCLE + 1.85; scene.add(pilier);

  // ── La scène du laboratoire et la caméra en orbite autour d'elle. `melange` va de 0 (noyau seul) à 1 (scène) :
  // le noyau rapetisse et monte au-dessus de la scène, la caméra glisse de son cadre vers l'orbite.
  let labo: SceneLabo | null = null, melangeCible = 0, melange = 0, laboBouge = false;
  /** `eclate` : les pièces sont écartées. `ensemble` : la caméra est sur la vue d'ensemble (pas sur une pièce, pas zoomée). */
  let eclate = false, ensemble = false;
  /** Largeur couverte par une fiche à droite (voulue, puis suivie en douceur). */
  let marge = 0, margeCour = 0;
  const orbite = { cible: new THREE.Vector3(), theta: 0, phi: .3, d: 5 };
  const voulu = { cible: new THREE.Vector3(), theta: 0, phi: .3, d: 5 };
  const posOrbite = new THREE.Vector3(), regard = new THREE.Vector3(), rayon = new THREE.Raycaster(), coin = new THREE.Vector3();
  /** Le titre en haut et la barre d'outils en bas couvrent environ un cinquième de la hauteur : la vue d'ensemble s'éloigne d'autant. */
  const MARGE_CADRE = 1.22;
  /** La distance où une sphère de ce rayon tient dans le cadre (la place laissée par la fiche), à `ecart` près de la cible. */
  const distancePour = (r: number, ecart = 0) => {
    const v = camera.fov * RAD, hz = 2 * Math.atan(Math.tan(v / 2) * Math.max(.3, (l - marge) / h));
    return (r * 1.08 + ecart) / Math.sin(Math.min(v, hz) / 2);
  };
  /** La vue prévue par la scène : sa direction de caméra, sa cible, à une distance où elle tient entière, assemblée ou
   *  éclatée. `garderAngle` : seuls la cible et le recul changent (éclater depuis la vue d'ensemble garde l'angle choisi). */
  const vueEnsemble = (garderAngle = false) => {
    if (!labo) return;
    if (!garderAngle) {
      const c = labo.desc.camera, dir = new THREE.Vector3(c.position[0] - c.cible[0], c.position[1] - c.cible[1], c.position[2] - c.cible[2]);
      if (dir.lengthSq() < 1e-6) dir.set(.6, .35, .7);
      dir.normalize();
      voulu.theta = Math.atan2(dir.x, dir.z); voulu.phi = Math.max(-.15, Math.min(1.35, Math.asin(dir.y)));
    }
    const b = eclate ? labo.eclatee : labo;
    // La cible prévue suit le centre quand les pièces s'écartent.
    voulu.cible.copy(labo.cibleCamera).add(b.centre).sub(labo.centre);
    voulu.d = distancePour(b.rayon, voulu.cible.distanceTo(b.centre)) * MARGE_CADRE;
    ensemble = true;
  };

  // ── L'animation.
  let cible: Reglage = REGLAGES.repos, lireNiveau: () => number = () => 0, reduit = false, effetsOn = true, sale = true;
  const cour: Reglage = { ...REGLAGES.repos };
  let voix = 0, temps = 0, horlogeT = 0, alerteVue = -1, dernierRendu = 0;
  /** L'avance propre des perles en réflexion ; la rotation et les ondes de l'habillage du globe. */
  let phaseSpheres = 0, spinHud = 0, vagueHud = 0;
  let fenetre = performance.now(), images = 0, lentes = 0, cout = 0;
  const depart = performance.now();
  const appliquerQualite = () => {
    rendu.setPixelRatio(ratio()); rendu.setSize(l, h, false);
    nuee.points.geometry.setDrawRange(0, effetsOn ? QUALITES[qualite].particules : Math.min(PARTICULES_SANS_EFFETS, QUALITES[qualite].particules));
    communs.echelle.value = h * rendu.getPixelRatio() / 2;
    canvas.dataset.qualite = String(qualite);
  };
  appliquerQualite();

  // ── Le vol de la nuée (étape 2) : du noyau vers l'objet de la scène, de l'objet vers le noyau, ou d'un objet à l'autre.
  // `semis` : les points de la scène ouverte ; `cap` : ceux vers lesquels la nuée va (null : le noyau). Arrivée sur un
  // objet, chaque point de la nuée s'efface et le point de la pièce prend le relais (`pose`) ; au départ, l'inverse.
  const DUREE_VOL = 1.8, pose = { value: 0 };
  let semis: Semis | null = null, cap: Semis | null = null;
  const lancerVol = (vers: Semis | null) => {
    const u = nuee.uniforms;
    if (cap) { cap.ecrireMonde(nuee.depart.array as Float32Array); nuee.depart.needsUpdate = true; u.departNoyau.value = 0; }
    else u.departNoyau.value = 1;
    if (vers) { vers.ecrireMonde(nuee.arrivee.array as Float32Array); nuee.arrivee.needsUpdate = true; u.arriveeNoyau.value = 0; u.relais.value = 1; }
    else { u.arriveeNoyau.value = 1; u.relais.value = 0; }
    cap = vers; u.vol.value = reduit ? 1 : 0; sale = true;
  };

  // ── Les formes du noyau (formes.ts). `choix` : ce qu’ID Studio IA a choisi sur le bouton « Noyau » de l'accueil — « auto »
  // (la forme suit le travail d'AURORA : Cosmique pendant une recherche), « noyau » (jamais de forme), ou une forme
  // tenue à la main. Chaque forme n'est chargée que la première fois qu'on en a besoin (3 à 7 Mo). Tant qu'elle n'est
  // pas là, ou si son fichier manque, le noyau reste tel qu'il est.
  const formes: Partial<Record<NomForme, FormeNoyau>> = {}, demandees = new Set<NomForme>();
  const vues: Record<NomForme, number> = { cosmique: 0, organique: 0, energie: 0 };
  let choix: ChoixForme = 'auto', formeVue = '', formeDuTravail: NomForme | null = null;
  const demanderForme = (nom: NomForme) => {
    if (demandees.has(nom)) return;
    demandees.add(nom);
    creerForme(THREE, nom, communs, sprite, Y_SOCLE).then(f => {
      if (annule) { f.liberer(); return; }
      formes[nom] = f; scene.add(f.groupe); sale = true;
    }).catch(e => { console.warn(`Forme « ${nom} » non chargée :`, e); });
  };

  /** Une image : les réglages, les mouvements et les couleurs avancent de dt secondes. */
  const avancer = (dt: number) => {
    // Les réglages glissent vers ceux de l'état (aussitôt si le mouvement est réduit).
    const k = reduit ? 1 : 1 - Math.exp(-dt * 3);
    for (const c of CLES) cour[c] += (cible[c] - cour[c]) * k;
    // La scène entre ou sort en douceur ; sortie finie, elle est libérée.
    melange += (melangeCible - melange) * (reduit ? 1 : 1 - Math.exp(-dt * 2.4));
    if (labo && melangeCible === 0 && melange < .004) { labo.liberer(); labo = null; semis = null; melange = 0; }
    const ms = melange * melange * (3 - 2 * melange);
    const niveau = cour.voix > .01 && !reduit ? Math.max(0, Math.min(1, lireNiveau())) : 0;
    voix += (niveau - voix) * Math.min(1, dt * 12);
    if (!reduit) { temps += dt * cour.vitesse; horlogeT += dt; }
    const t = horlogeT;

    if (Math.abs(cour.alerte - alerteVue) > .002) {
      alerteVue = cour.alerte;
      for (const x of teintes) x.c.copy(x.base).lerp(ROUGE, cour.alerte * .85);
    }
    const souffle = reduit ? 1 : .92 + .08 * Math.sin(t * 1.3) - .2 * cour.alerte * (.5 + .5 * Math.sin(t * 3));
    const f = cour.intensite * (1 + .4 * voix * cour.voix) * (1 - .5 * Math.max(cour.retrait * (1 - ms), .7 * ms)) * souffle;
    tourne.rotation.y = temps * .12; tourne.rotation.x = .25 * Math.sin(temps * .05);
    // Avec une scène, le noyau devient une petite sphère qui veille dans un coin de la vue (placée après la caméra).
    noyau.scale.setScalar((1 + .06 * voix * cour.voix + (reduit ? 0 : .012 * Math.sin(t * 1.1))) * (1 - .45 * cour.retrait * (1 - ms)));
    noyau.position.set(0, reduit ? 0 : .05 * Math.sin(t * .6), 0);
    // En réflexion, les perles filent autour du noyau (un tour en 2,5 s environ), grossies, avec halo et traînée ; en
    // parole elles ralentissent et battent avec la voix.
    if (!reduit) phaseSpheres += dt * cour.spheres * 1.8;
    for (const a of anneaux) a.roue.rotation.z = temps * a.v + phaseSpheres * a.dir;
    const sph = cour.spheres, taille = .16 * (1 + .9 * sph + .8 * voix * cour.voix * sph);
    const trainer = effetsOn && !reduit && sph > .01, pas = .035 + .05 * sph;
    for (const fl of filantes) {
      fl.perle.scale.setScalar(taille);
      // Au repos aussi, chaque perle luit doucement (maquettes) ; en réflexion son halo s'avive.
      fl.halo.visible = effetsOn;
      if (fl.halo.visible) { fl.halo.scale.setScalar(taille * 3.4); fl.halo.material.opacity = (.22 + .3 * sph) * Math.min(1.2, f); }
      fl.trainee.forEach((s, j) => {
        s.visible = trainer;
        if (!trainer) return;
        const k = (j + 1) / (TRAINEE + 1), b = fl.ang - fl.dir * (j + 1) * pas;
        s.position.set(Math.cos(b) * fl.r, Math.sin(b) * fl.r, 0);
        s.scale.setScalar(taille * (1 - .65 * k));
        s.material.opacity = .75 * sph * (1 - k);
      });
    }
    // La nuée : plus vive quand le noyau s'anime et avec la voix ; elle frémit et scintille, sauf en mouvement réduit.
    const un = nuee.uniforms, vivant = effetsOn && !reduit ? 1 : 0;
    un.temps.value = t; un.scintille.value = vivant; un.tourbillon.value = vivant;
    if (un.vol.value < 1) un.vol.value = reduit ? 1 : Math.min(1, un.vol.value + dt / DUREE_VOL);
    pose.value = cap ? Math.max(0, Math.min(1, (un.vol.value - .72) / .28)) : 0;
    un.force.value = Math.min(1.4, .45 + .55 * f) * (1 + .5 * voix * cour.voix);
    matHalo.uniforms.force.value = f;
    matLiens.uniforms.temps.value = temps; matLiens.uniforms.liens.value = cour.liens + voix * .6 * cour.voix; matLiens.uniforms.force.value = f;
    matNoeuds.opacity = .6 + .4 * Math.min(1, f);
    (coeur.material as Trois.SpriteMaterial).opacity = .44 * f + .3 * voix;
    (aura.material as Trois.SpriteMaterial).opacity = .28 * f;
    (brume.material as Trois.SpriteMaterial).opacity = .15 * f;
    const vueLogo = 1 - .6 * cour.retrait;
    (logo.material as Trois.SpriteMaterial).opacity = .95 * vueLogo;
    (lueurLogo.material as Trois.SpriteMaterial).opacity = .62 * vueLogo * (.7 + .3 * Math.min(1, f) + .3 * voix);
    for (const m of matsAnneaux) m.opacity = .62 * Math.min(1.2, f);
    for (const m of matsLueursAnneaux) m.uniforms.o.value = .2 * Math.min(1.2, f);
    matDisque.uniforms.force.value = f * (1 + .3 * cour.onde);
    matFaisceau.uniforms.force.value = f * (1 + .6 * cour.onde + .5 * voix) * (1 - .35 * ms);
    matCercles.opacity = matCerclesV.opacity = .38 + .3 * Math.min(1, f);
    // Le pilier traverserait la scène : il s'efface devant elle.
    matPilier.uniforms.force.value = f * (1 + .8 * cour.onde + .6 * voix) * (1 - .92 * ms);
    for (const a of arcs) a.tour.rotation.y = temps * a.v;
    // Les ondes partent du socle pendant l'écoute, l'analyse et la recherche.
    ondes.forEach((o, i) => {
      o.visible = effetsOn && !reduit && cour.onde > .01;
      if (!o.visible) return;
      const ph = (t * .5 + i * .5) % 1;
      o.scale.setScalar(.4 + ph * 1.8); (o.material as Trois.MeshBasicMaterial).opacity = cour.onde * (1 - ph) * .45;
    });

    laboBouge = labo ? labo.avancer(dt, reduit, ms) : false;
    // La caméra : l'orbite suit les gestes en douceur ; la vue glisse du cadre du noyau à l'orbite selon le mélange.
    const kc = reduit ? 1 : 1 - Math.exp(-dt * 7);
    orbite.theta += (voulu.theta - orbite.theta) * kc; orbite.phi += (voulu.phi - orbite.phi) * kc; orbite.d += (voulu.d - orbite.d) * kc;
    orbite.cible.lerp(voulu.cible, kc);
    const cp = Math.cos(orbite.phi);
    posOrbite.set(Math.sin(orbite.theta) * cp, Math.sin(orbite.phi), Math.cos(orbite.theta) * cp).multiplyScalar(orbite.d).add(orbite.cible);
    // Une fiche ouverte à droite : on dessine la partie droite d'un cadre plus large, dont le centre tombe au milieu
    // de la place libre (le lancer de rayon de choisir() suit, il passe par la même projection).
    margeCour += (marge - margeCour) * kc;
    if (margeCour > .5) { camera.aspect = (l + margeCour) / h; camera.setViewOffset(l + margeCour, h, margeCour, 0, l, h); }
    else if (camera.view?.enabled) { camera.aspect = l / h; camera.clearViewOffset(); }
    camera.position.lerpVectors(posNoyau, posOrbite, ms);
    regard.lerpVectors(CIBLE, orbite.cible, ms);
    camera.lookAt(regard);
    // Au-dessus de la scène, le noyau sortait du cadre dès qu'elle est haute (bras éclaté) : il se tient en haut à droite
    // de la vue, en avant de la scène, quel que soit l'angle choisi, et garde la même taille à l'écran quel que soit le
    // recul (son diamètre : 11 % de la hauteur de la vue ; la sphère du noyau a un rayon de 1).
    if (ms > 0) {
      camera.updateMatrixWorld();
      const loin = orbite.d * .75;
      coin.set(.64 - 2 * margeCour / l, .55, .5).unproject(camera).sub(camera.position).normalize().multiplyScalar(loin).add(camera.position);
      noyau.position.lerp(coin, ms);
      noyau.scale.multiplyScalar(1 + (.11 * loin * Math.tan(camera.fov * RAD / 2) - 1) * ms);
    }
    // La forme voulue (choisie à la main, ou celle du travail en cours) prend la place du noyau, qui se replie en son
    // centre avec sa nuée ; devant une scène, elle s'efface. D'une forme à l'autre, l'une s'efface pendant que l'autre
    // vient. Sans mouvement, tout change d'un coup, comme les réglages.
    const voulue: NomForme | null = choix === 'auto' ? formeDuTravail : choix === 'noyau' ? null : choix;
    let vf = 0, montree = '', devant: FormeNoyau | undefined;
    for (const nom of NOMS_FORMES) {
      const f = formes[nom];
      vues[nom] += ((f && voulue === nom ? 1 - ms : 0) - vues[nom]) * k;
      if (vues[nom] < .002 && voulue !== nom) vues[nom] = 0;
      // La forme bat et s'éclaire avec le niveau réel de la voix, pendant l'écoute et la parole.
      f?.avancer(t, dt, vues[nom], effetsOn && !reduit, voix * cour.voix);
      if (vues[nom] > vf) { vf = vues[nom]; devant = f; montree = vf > .5 ? nom : ''; }
    }
    if (vf > .002) { noyau.scale.multiplyScalar(1 - .96 * vf); un.force.value *= 1 - vf; }
    if (montree !== formeVue) { formeVue = montree; canvas.dataset.forme = montree; }
    // L'habillage de la voix (cercles, arcs, ondes) est porté par le noyau : avec une forme, il ne se replie pas avec
    // lui. Il garde sa taille, s'élargit pour entourer le lotus ou les rubans, et se pose à la hauteur de la forme.
    if (devant && vf > .002) {
      hud.scale.setScalar((1 + (devant.voix.tour - 1) * vf) / (1 - .96 * vf));
      hud.position.y = devant.voix.y * vf / noyau.scale.y;
    } else if (hud.scale.x !== 1 || hud.position.y !== 0) { hud.scale.setScalar(1); hud.position.y = 0; }
    // La forme du repos suit le noyau là où il est (et tourne avec son réseau) ; ses points rapetissent avec lui.
    noyau.updateMatrixWorld(true);
    un.noyauMat.value.copy(tourne.matrixWorld); un.taille.value = .04 * noyau.scale.x;
    // L'habillage du globe : allumé en écoute et en parole, il tourne et bat avec la voix, et s'efface devant une scène.
    // Il fait face à la caméra (le noyau, lui, ne tourne jamais).
    const g = cour.voix * (1 - ms);
    hud.visible = g > .01;
    if (hud.visible) {
      hud.quaternion.copy(camera.quaternion);
      if (!reduit) {
        spinHud = (spinHud + dt * (.04 + .5 * cour.voix + .6 * voix)) % (Math.PI * 2);
        vagueHud = (vagueHud + dt * (.35 + .9 * voix) * cour.voix) % 1;
      }
      pointille.rotation.z = spinHud; tourArcs.rotation.z = -spinHud * 1.4;
      for (const [m, on] of matsHud) m.opacity = on * g;
      matArcLueur.opacity = (.22 + .35 * voix) * g;
      ondesVoix.forEach((o, i) => {
        o.visible = effetsOn && !reduit;
        if (!o.visible) return;
        const ph = (vagueHud + i / 3) % 1;
        o.scale.setScalar(1.03 + .22 * ph);
        o.material.opacity = g * (1 - ph) * (.2 + .7 * voix);
      });
    }
    // Les photos du sujet : la nouvelle attend que l'ancienne soit effacée, puis se matérialise en 0,9 s ; elles flottent
    // doucement et s'effacent devant une scène.
    const kp = reduit ? 1 : 1 - Math.exp(-dt * 5);
    panneaux.forEach((p, i) => {
      if (p.attente && p.vis < .02) {
        p.tex?.dispose(); p.texEt?.dispose();
        const a = p.attente; p.attente = null;
        p.tex = a.tex; p.texEt = a.et; p.url = a.url;
        p.mat.uniforms.carte.value = a.tex; p.mat.uniforms.ri.value = a.ri;
        p.matEt.map = a.et; p.matEt.needsUpdate = true;
        p.voulu = true; p.app = 0;
      }
      p.vis += ((p.voulu && placePhotos ? 1 : 0) - p.vis) * kp;
      if (p.voulu) p.app = reduit ? 1 : Math.min(1, p.app + dt / .9);
      p.survol += ((photoSurvolee === i ? 1 : 0) - p.survol) * kp;
      const force = p.vis * (1 - ms);
      p.groupe.visible = force > .01;
      if (!p.groupe.visible) return;
      p.groupe.position.y = .06 + (reduit ? 0 : .035 * Math.sin(t * .7 + p.cote * 1.3));
      const u = p.mat.uniforms;
      u.temps.value = t; u.app.value = p.app; u.survol.value = p.survol; u.force.value = force;
      u.anim.value = effetsOn && !reduit ? 1 : 0;
      p.matEt.opacity = .9 * force * Math.max(0, Math.min(1, (p.app - .5) * 2));
      p.halo.material.opacity = (.1 + .08 * p.survol) * force;
    });
  };

  /** Attendre que la carte graphique ait vraiment fini l'image : lire un pixel l'y oblige (finish() n'attend pas
   *  partout ; mesuré : 0,13 ms par image avec finish, invraisemblable). */
  const pixel = new Uint8Array(4);
  const attendreGpu = () => { const gl = rendu.getContext(); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel); };
  /** Le minuteur de la carte graphique, s'il existe : il chronomètre le dessin lui-même, sans attendre l'écran. Lire un
   *  pixel, au contraire, peut attendre le rythme de l'affichage (mesuré le 08/11/2026 dans un panneau limité à 30
   *  images par seconde : 33 ms par lecture, même sans rien dessiner ; la qualité tombait au plus bas à tort). */
  const gl2 = rendu.getContext() instanceof WebGL2RenderingContext ? rendu.getContext() as WebGL2RenderingContext : null;
  const minuteur = gl2?.getExtension('EXT_disjoint_timer_query_webgl2') ?? null;
  let requete: WebGLQuery | null = null, cpuMesure = 0;
  const noter = (ms: number) => { cout = cout ? cout * .6 + ms * .4 : ms; canvas.dataset.msImage = cout.toFixed(1); };
  /** Le résultat arrive quelques images plus tard : on le lit sans jamais attendre. Une image coûte le plus long des deux
   *  temps (processeur, carte graphique), qui travaillent en parallèle. */
  const lireMinuteur = () => {
    if (!requete || !minuteur || !gl2) return;
    const perdu = gl2.getParameter(minuteur.GPU_DISJOINT_EXT) as boolean;
    const dispo = gl2.getQueryParameter(requete, gl2.QUERY_RESULT_AVAILABLE) as boolean;
    if (!dispo && !perdu) return;
    if (dispo && !perdu) {
      const gpu = (gl2.getQueryParameter(requete, gl2.QUERY_RESULT) as number) / 1e6;
      canvas.dataset.msGpu = gpu.toFixed(2); canvas.dataset.msCpu = cpuMesure.toFixed(2);
      noter(Math.max(gpu, cpuMesure));
    }
    gl2.deleteQuery(requete); requete = null;
  };

  const horloge = new THREE.Clock();
  const imageSuivante = (maintenant: number) => {
    if (document.hidden) { horloge.getDelta(); fenetre = maintenant; images = 0; return; }
    // Mouvement réduit : une image seulement quand quelque chose change, ou quand la scène bouge à la demande (« anime-le »).
    if (reduit && !sale && !laboBouge) return;
    if (!effetsOn && !sale && maintenant - dernierRendu < 32) return;
    const dt = Math.min(horloge.getDelta(), .1);
    sale = false; dernierRendu = maintenant;
    avancer(dt);
    // La première image de chaque fenêtre de 2 s est chronométrée jusqu'au bout (carte graphique comprise) : par le
    // minuteur de la carte s'il existe, sinon en lisant un pixel. Pas pendant les 2 premières secondes : la compilation
    // des shaders y gonfle le temps (mesuré : la qualité baissait alors qu'une image coûtait 2,5 ms).
    lireMinuteur();
    const chrono = images === 0 && maintenant - depart > 2000;
    const parMinuteur = chrono && minuteur !== null && gl2 !== null && requete === null;
    const t0 = performance.now();
    if (parMinuteur) { requete = gl2.createQuery(); gl2.beginQuery(minuteur.TIME_ELAPSED_EXT, requete); }
    rendu.render(scene, camera);
    if (parMinuteur) { gl2.endQuery(minuteur.TIME_ELAPSED_EXT); cpuMesure = performance.now() - t0; }
    else if (chrono && !minuteur) { attendreGpu(); noter(performance.now() - t0); }

    // La mesure : images réellement dessinées sur 2 s. Lente et coûteuse deux fois de suite → un cran de qualité en moins.
    images++;
    if (maintenant - fenetre >= 2000) {
      const ips = images * 1000 / (maintenant - fenetre);
      canvas.dataset.ips = ips.toFixed(0);
      if (effetsOn && !reduit && maintenant - depart > 4000 && qualite > 0) {
        lentes = ips < SEUIL_LENT && cout > COUT_MAX_MS ? lentes + 1 : 0;
        if (lentes >= 2) { qualite--; lentes = 0; appliquerQualite(); }
      }
      fenetre = maintenant; images = 0;
    }
  };
  // Une erreur du moteur arrête la boucle à la première fois (et non à chaque image) et rend la place au globe :
  // le reste d'AURORA continue (cahier des charges, critère 10).
  const boucle = (maintenant: number) => {
    image = requestAnimationFrame(boucle);
    try { imageSuivante(maintenant); } catch (e) {
      cancelAnimationFrame(image);
      if (!annule) { annule = true; console.error('Noyau 3D arrêté :', e); surPanne('le moteur 3D a rencontré une erreur.'); }
    }
  };
  image = requestAnimationFrame(boucle);

  const pilote: PiloteNoyau = {
    redimensionner(nl, nh) {
      l = Math.max(1, nl); h = Math.max(1, nh);
      rendu.setSize(l, h, false); camera.aspect = l / h; camera.updateProjectionMatrix(); cadrer(); placerPhotos(); sale = true;
      communs.echelle.value = h * rendu.getPixelRatio() / 2;
    },
    etat(e) {
      cible = REGLAGES[e] ?? REGLAGES.repos; canvas.dataset.etat = e; sale = true;
      formeDuTravail = FORME_DU_TRAVAIL[e] ?? null;
      if (formeDuTravail && choix === 'auto') demanderForme(formeDuTravail);
    },
    forme(c) {
      choix = c; canvas.dataset.choixForme = c; sale = true;
      if (c !== 'auto' && c !== 'noyau') demanderForme(c);
      else if (c === 'auto' && formeDuTravail) demanderForme(formeDuTravail);
    },
    niveau(lire) { lireNiveau = lire; },
    mouvementReduit(on) { reduit = on; sale = true; },
    effets(on) {
      effetsOn = on; appliquerQualite();
      for (const p of perles) p.visible = on;
      sale = true;
    },
    photos(liste) { montrerPhotos(liste); },
    photoSous(x, y) {
      if (melange > .5) return null;
      camera.updateMatrixWorld();
      rayon.setFromCamera(new THREE.Vector2(x / l * 2 - 1, 1 - y / h * 2), camera);
      const i = panneaux.findIndex(p => p.groupe.visible && p.vis > .5 && p.app > .5 && rayon.intersectObject(p.cadre, false).length > 0);
      return i < 0 ? null : i;
    },
    survolPhoto(i) { if (i !== photoSurvolee) { photoSurvolee = i; sale = true; } },
    banc(n) {
      attendreGpu();
      const t0 = performance.now();
      for (let i = 0; i < n; i++) { avancer(1 / 60); rendu.render(scene, camera); attendreGpu(); }
      return (performance.now() - t0) / Math.max(1, n);
    },
    scene(d) {
      sale = true;
      // La nuée quitte l'objet pour revenir au noyau (les pièces sont encore là, dans leur pose du moment).
      if (!d) { if (melangeCible !== 0 && cap) lancerVol(null); melangeCible = 0; return; }
      melangeCible = 1;
      if (labo?.desc === d) { if (cap !== semis) lancerVol(semis); return; }
      // La nouvelle scène est construite et semée avant de libérer l'ancienne : la nuée part d'où elle est (le noyau, ou
      // l'objet précédent) et vole vers le nouvel objet.
      const nouveau = construireScene(THREE, d, Y_SOCLE);
      scene.add(nouveau.racine);
      const nouveauSemis = semerScene(THREE, nouveau.pieces, MAX_PARTICULES, communs, pose);
      lancerVol(nouveauSemis);
      labo?.liberer();
      labo = nouveau; semis = nouveauSemis;
      eclate = false;
      vueEnsemble();
      // L'orbite part déjà de la vue prévue : c'est le mélange qui fait glisser la caméra depuis le cadre du noyau.
      if (melange < .05) { orbite.theta = voulu.theta; orbite.phi = voulu.phi; orbite.d = voulu.d; orbite.cible.copy(voulu.cible); }
    },
    eclater(on) {
      eclate = on; labo?.eclater(on); sale = true;
      if (ensemble) vueEnsemble(true);
    },
    animer(on) { labo?.animer(on); sale = true; },
    interieur(on) { labo?.interieur(on); sale = true; },
    origine(v) { v?.preparer(rendu); labo?.origine(v); sale = true; },
    animation(rang) { labo?.animation(rang); sale = true; },
    visibles(masquees, isolees) { labo?.visibles(masquees, isolees); sale = true; },
    selection(id) { labo?.selection(id); sale = true; },
    survol(id) { labo?.survol(id); sale = true; },
    signaler(ids) { labo?.signaler(ids); sale = true; },
    viser(ids, serre = false) {
      sale = true;
      if (!labo) return;
      const b = ids?.length ? labo.englobant(ids) : null;
      if (!b) { vueEnsemble(); return; }
      ensemble = false;
      voulu.cible.copy(b.centre);
      voulu.d = Math.max(.6, distancePour(Math.max(b.rayon, .15)) * (serre ? 1.3 : 1.7));
    },
    tourner(dx, dy) {
      voulu.theta -= dx * .0065;
      voulu.phi = Math.max(-.15, Math.min(1.35, voulu.phi + dy * .0055));
      sale = true;
    },
    zoom(dy) {
      const loin = labo ? distancePour(Math.max(labo.rayon, labo.eclatee.rayon)) * 2.6 : 12;
      voulu.d = Math.max(.5, Math.min(loin, voulu.d * Math.exp(dy * .0012)));
      ensemble = false;
      sale = true;
    },
    marge(px) {
      const m = Math.max(0, Math.min(l * .6, px));
      if (Math.abs(m - marge) < .5) return;
      marge = m; sale = true;
      if (ensemble) vueEnsemble(true);   // la vue d'ensemble tient dans la place qui reste
    },
    choisir(x, y) {
      if (!labo || melange < .5) return null;
      camera.updateMatrixWorld();
      rayon.setFromCamera(new THREE.Vector2(x / l * 2 - 1, 1 - y / h * 2), camera);
      return labo.choisir(rayon);
    },
    choisirFichier(x, y) {
      if (!labo || melange < .5) return null;
      camera.updateMatrixWorld();
      rayon.setFromCamera(new THREE.Vector2(x / l * 2 - 1, 1 - y / h * 2), camera);
      return labo.choisirFichier(rayon);
    },
  };

  return {
    pilote,
    liberer() {
      annule = true; cancelAnimationFrame(image);
      canvas.removeEventListener('webglcontextlost', perte);
      if (requete) { gl2?.deleteQuery(requete); requete = null; }
      seqPhotos++;   // une photo encore en route sera libérée à son arrivée
      for (const p of panneaux) { p.tex?.dispose(); p.texEt?.dispose(); p.attente?.tex.dispose(); p.attente?.et.dispose(); }
      for (const nom of NOMS_FORMES) { formes[nom]?.liberer(); delete formes[nom]; }   // avec la texture de leur modèle
      libererObjets(scene);   // le noyau et la scène du laboratoire si elle est là
      labo = null;
      textures.forEach(t => t.dispose());
      rendu.dispose();
      // Contexte déjà perdu (panne) : rien à rendre, et three.js avertirait que l'extension manque.
      if (!rendu.getContext().isContextLost()) rendu.forceContextLoss();
    },
  };
}
