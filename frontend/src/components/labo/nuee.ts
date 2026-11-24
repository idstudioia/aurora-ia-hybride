// La nuée de particules du noyau (09/11/2026, demande d’ID Studio IA : « porter la nuée de particules d'Athéna dans le noyau
// d'AURORA », en se rapprochant des maquettes). Idée reprise d'Athéna (client/public/model3d.html : des milliers de points
// qui volent d'une forme à l'autre), réécrite ici. Deux choix d’ID Studio IA :
// - aucune forme écrite à l'avance dans le code (ni ADN, ni atome, ni virus, ni objet comme chez Athéna) : la nuée forme
//   le noyau au repos, et sinon l'objet de la scène ouverte, quel qu'il soit (décrit à la demande, voir scene.ts) ;
// - le vol est calculé par la carte graphique (une position de départ, une d'arrivée, un avancement), et non point par
//   point en JavaScript à chaque image comme chez Athéna.
// Ce n'est qu'une animation : rien ici ne mesure ni ne calcule quoi que ce soit de réel.
import type * as Trois from 'three';
import { MeshSurfaceSampler } from 'three/examples/jsm/math/MeshSurfaceSampler.js';

type Three = typeof Trois;

/** Un point au hasard sur une sphère de rayon r (pour la forme du repos, et pour les formes du travail : formes.ts). */
export const surSphere = (r: number): number[] => {
  const u = Math.random() * 2 - 1, a = Math.random() * Math.PI * 2, s = Math.sqrt(1 - u * u);
  return [Math.cos(a) * s * r, u * r, Math.sin(a) * s * r];
};

/** Le noyau au repos, dans son repère (sphère de rayon 1) : des amas de points sur les nœuds du réseau, des traits de
 *  points le long de ses liens, une poussière sur toute la sphère et un halo d'étincelles autour. L'ordre est mélangé :
 *  n'importe quel début de la liste couvre tout le noyau (une qualité plus basse dessine moins de points).
 *  `aleat` : pour chaque point, son retard au départ d'un vol, son éclat et sa teinte (de 0 à 1). */
export function formeRepos(n: number, noeuds: Trois.Vector3[], paires: [number, number][]): { repos: Float32Array; aleat: Float32Array } {
  const repos = new Float32Array(n * 3), aleat = new Float32Array(n * 3), R = Math.random;
  for (let i = 0; i < n; i++) {
    const k = i / n;
    let p: number[], eclat = R();
    if (k < .16 && noeuds.length) {
      // Amas autour d'un nœud : les points les plus brillants de la constellation.
      const c = noeuds[Math.floor(R() * noeuds.length)], e = .03 * Math.sqrt(-2 * Math.log(R() + 1e-6));
      const [x, y, z] = surSphere(e);
      p = [c.x + x, c.y + y, c.z + z]; eclat = .55 + .45 * R();
    } else if (k < .42 && paires.length) {
      // Le long d'un lien : un trait de lumière fait de points.
      const [a, b] = paires[Math.floor(R() * paires.length)], f = R(), A = noeuds[a], B = noeuds[b], j = () => (R() - .5) * .012;
      p = [A.x + (B.x - A.x) * f + j(), A.y + (B.y - A.y) * f + j(), A.z + (B.z - A.z) * f + j()]; eclat *= .7;
    } else if (k < .74) {
      p = surSphere(.9 + .1 * Math.sqrt(R())); eclat *= .55;   // la poussière de la sphère, plus dense au bord
    } else {
      const [x, y, z] = surSphere(1.22 + Math.pow(R(), .7) * 1.65);
      p = [x, y * .7, z]; eclat *= .8;   // les étincelles autour, aplaties comme les anneaux
    }
    repos.set(p, i * 3); aleat.set([R(), eclat, R()], i * 3);
  }
  // Mélange (Fisher-Yates), positions et hasards ensemble.
  for (let i = n - 1; i > 0; i--) {
    const j = Math.floor(R() * (i + 1));
    for (let c = 0; c < 3; c++) {
      const a = i * 3 + c, b = j * 3 + c;
      [repos[a], repos[b]] = [repos[b], repos[a]]; [aleat[a], aleat[b]] = [aleat[b], aleat[a]];
    }
  }
  return { repos, aleat };
}

/** Les valeurs communes à la nuée et aux points d'une scène, réglées une fois par image par le noyau. */
export interface CommunsNuee {
  temps: { value: number };
  /** Hauteur de dessin / 2 (en pixels réels) : la taille d'un point suit la distance, comme PointsMaterial. */
  echelle: { value: number };
  /** 0 : points fixes et sans scintillement (mouvement réduit, animations coupées). */
  scintille: { value: number };
}

export interface Nuee {
  points: Trois.Points;
  uniforms: {
    /** Le repère du noyau (sa matrice monde) : la forme du repos le suit partout, même rangé dans un coin. */
    noyauMat: { value: Trois.Matrix4 };
    /** L'avancement du vol, de 0 (départ) à 1 (arrivée). */
    vol: { value: number };
    /** 1 : le départ (ou l'arrivée) est le noyau au repos ; 0 : les positions `depart` (ou `arrivee`), dans le monde. */
    departNoyau: { value: number }; arriveeNoyau: { value: number };
    /** 1 : à l'arrivée, chaque point s'efface (les points de la scène prennent le relais, sans saut). */
    relais: { value: number };
    tourbillon: { value: number }; taille: { value: number }; force: { value: number };
    /** La taille des points posés sur la scène (dans le monde) : en vol, chaque point passe de l'une à l'autre. Sans
     *  elle, la nuée rapetissait avec le noyau rangé dans son coin et le vol ne se voyait presque pas. */
    tailleScene: { value: number };
    c1: { value: Trois.Color }; c2: { value: Trois.Color };
  } & CommunsNuee;
  depart: Trois.BufferAttribute;
  arrivee: Trois.BufferAttribute;
}

/** Un point rond et doux, plus clair au centre (pas de texture). Partagé avec les formes du travail (formes.ts). */
export const FRAGMENT_POINT ='vec2 q=gl_PointCoord*2.-1.;float d=dot(q,q);if(d>1.)discard;float a=exp(-d*5.)*.9+exp(-d*1.6)*.25;';

export function creerNuee(THREE: Three, repos: Float32Array, aleat: Float32Array, c1: Trois.Color, c2: Trois.Color, communs: CommunsNuee): Nuee {
  const n = repos.length / 3, g = new THREE.BufferGeometry();
  const depart = new THREE.BufferAttribute(new Float32Array(n * 3), 3), arrivee = new THREE.BufferAttribute(new Float32Array(n * 3), 3);
  depart.setUsage(THREE.DynamicDrawUsage); arrivee.setUsage(THREE.DynamicDrawUsage);
  g.setAttribute('position', new THREE.BufferAttribute(repos, 3));
  g.setAttribute('aleat', new THREE.BufferAttribute(aleat, 3));
  g.setAttribute('depart', depart); g.setAttribute('arrivee', arrivee);
  const uniforms = {
    ...communs,
    noyauMat: { value: new THREE.Matrix4() }, vol: { value: 1 }, departNoyau: { value: 1 }, arriveeNoyau: { value: 1 }, relais: { value: 0 },
    tourbillon: { value: 1 }, taille: { value: .04 }, force: { value: 1 }, tailleScene: { value: .034 }, c1: { value: c1 }, c2: { value: c2 },
  };
  const mat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms,
    // Chaque point part avec un retard (jusqu'à 40 % du vol) : la nuée s'étire en flot. Au milieu du vol, elle tourbillonne
    // et ses points grossissent et s'avivent ; au repos, elle frémit à peine.
    vertexShader: 'uniform mat4 noyauMat;uniform float vol;uniform float departNoyau;uniform float arriveeNoyau;uniform float temps;'
      + 'uniform float taille;uniform float tailleScene;uniform float echelle;uniform float tourbillon;uniform float scintille;'
      + 'attribute vec3 depart;attribute vec3 arrivee;attribute vec3 aleat;varying float vTeinte;varying float vEclat;varying float vFin;varying float vVol;'
      + 'void main(){vec3 r=(noyauMat*vec4(position,1.)).xyz;vec3 a=mix(depart,r,departNoyau);vec3 b=mix(arrivee,r,arriveeNoyau);'
      + 'float t=clamp((vol-aleat.x*.4)/.6,0.,1.);t=t*t*(3.-2.*t);float m=4.*t*(1.-t);'
      + 'vec3 w=vec3(sin(aleat.x*53.+temps*1.9),sin(aleat.y*47.+temps*1.6+1.7),sin(aleat.z*59.+temps*2.2+3.1));'
      // Le tourbillon : chaque point tourne autour de son chemin (un anneau qui s'ouvre puis se referme), pas un cube.
      + 'float th=aleat.x*6.283+temps*1.7+t*4.;float R=m*(.2+.5*aleat.y)*tourbillon;'
      + 'vec3 v=vec3(cos(th)*R,m*.35*(aleat.z-.4)*tourbillon,sin(th)*R);'
      + 'vec3 p=mix(a,b,t)+v+w*.012*scintille;vec4 mv=modelViewMatrix*vec4(p,1.);gl_Position=projectionMatrix*mv;'
      + 'float tz=mix(mix(tailleScene,taille,departNoyau),mix(tailleScene,taille,arriveeNoyau),t);'
      + 'gl_PointSize=clamp(tz*(.35+aleat.y*aleat.y*1.6)*(1.+m*1.2)*echelle/-mv.z,1.,40.);vTeinte=aleat.z;vEclat=aleat.y;vFin=t;vVol=m;}',
    fragmentShader: 'uniform vec3 c1;uniform vec3 c2;uniform float force;uniform float temps;uniform float scintille;uniform float relais;'
      + 'varying float vTeinte;varying float vEclat;varying float vFin;varying float vVol;void main(){' + FRAGMENT_POINT
      + 'vec3 c=mix(mix(c1,c2,vTeinte),vec3(1.),smoothstep(.82,1.,vEclat)*.55);'
      + 'float s=1.-scintille*.45*(.5+.5*sin(temps*2.4+vEclat*71.+vTeinte*37.));'
      + 'gl_FragColor=vec4(c,a*force*(1.+vVol*1.2)*s*(1.-relais*smoothstep(.85,1.,vFin)));}',
  });
  const points = new THREE.Points(g, mat);
  points.frustumCulled = false;   // ses points sont placés par le shader : la sphère englobante de `position` ne vaut rien
  return { points, uniforms, depart, arrivee };
}

/** Les points d'une scène, posés sur ses pièces. */
export interface Semis {
  /** Pour le point i de la nuée : la position, dans le monde, du point de la scène qui lui revient, dans la pose actuelle
   *  des pièces (assemblées, écartées, animées). Les matrices des pièces doivent être à jour. */
  ecrireMonde(dest: Float32Array): void;
}

/** Les pièces d'une scène, telles que scene.ts les montre (SceneLabo.pieces). */
interface PieceSemee { maillage: Trois.Mesh; mat: Trois.ShaderMaterial }

/** Un objet en particules : la fonction genererObjetLocal d'Athéna, élargie à n'importe quel objet. Chez Athéna, quatre
 *  objets écrits à la main (ventilateur, radiateur, pompe, batterie) ; ici, les pièces de la scène ouverte, quelle qu'elle
 *  soit. `n` points sont répandus à la surface des pièces (l'échantillonneur de three.js : au hasard, au prorata de l'aire
 *  de chaque triangle), un peu plus que leur part pour les petites pièces. Chaque pièce porte ses points : ils suivent ses
 *  mouvements (vue écartée, animation) et s'allument avec elle (mêmes valeurs que sa matière : couleur, force, clarté).
 *  `pose` (0 à 1) : leur visibilité, que le noyau règle quand la nuée arrive ou repart. */
export function semerScene(THREE: Three, pieces: readonly PieceSemee[], n: number, communs: CommunsNuee, pose: { value: number }): Semis {
  const echant = pieces.map(p => new MeshSurfaceSampler(p.maillage).build());
  const poids = echant.map(s => Math.pow(s.distribution?.[s.distribution.length - 1] ?? 0, .8));
  const total = poids.reduce((a, b) => a + b, 0) || 1;
  const minimum = Math.min(12, Math.floor(n / Math.max(1, pieces.length) / 2));
  const parts = poids.map(w => Math.max(minimum, Math.floor(n * w / total)));
  const reste = n - parts.reduce((a, b) => a + b, 0), grande = parts.indexOf(Math.max(...parts));
  parts[grande] = Math.max(0, parts[grande] + reste);   // la plus grande pièce prend l'écart d'arrondi
  const taille = { value: .026 }, v = new THREE.Vector3(), R = Math.random;
  const locales: Float32Array[] = [], qui: number[] = [], rang: number[] = [];
  pieces.forEach((p, i) => {
    const m = parts[i], pos = new Float32Array(m * 3), al = new Float32Array(m * 3);
    for (let j = 0; j < m; j++) { echant[i].sample(v); v.toArray(pos, j * 3); al.set([R(), R(), R()], j * 3); qui.push(i); rang.push(j); }
    locales.push(pos);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('aleat', new THREE.BufferAttribute(al, 3));
    const u = p.mat.uniforms;
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { c: u.c, force: u.force, clair: u.clair, pose, taille, ...communs },
      vertexShader: 'uniform float taille;uniform float echelle;attribute vec3 aleat;varying float vE;void main(){'
        + 'vec4 mv=modelViewMatrix*vec4(position,1.);gl_Position=projectionMatrix*mv;'
        + 'gl_PointSize=clamp(taille*(.4+aleat.y*aleat.y*1.4)*echelle/-mv.z,1.,24.);vE=aleat.y;}',
      fragmentShader: 'uniform vec3 c;uniform float force;uniform float clair;uniform float pose;uniform float temps;uniform float scintille;'
        + 'varying float vE;void main(){' + FRAGMENT_POINT
        + 'vec3 col=mix(c,vec3(1.),clamp(clair+smoothstep(.85,1.,vE)*.5,0.,1.));'
        + 'float s=1.-scintille*.4*(.5+.5*sin(temps*2.4+vE*71.));gl_FragColor=vec4(col,a*min(force,1.6)*pose*s*.8);}',
    });
    p.maillage.add(new THREE.Points(g, mat));
  });
  // L'ordre de la nuée est mélangé : n'importe quel début de la liste couvre toutes les pièces.
  for (let i = qui.length - 1; i > 0; i--) {
    const j = Math.floor(R() * (i + 1));
    [qui[i], qui[j]] = [qui[j], qui[i]]; [rang[i], rang[j]] = [rang[j], rang[i]];
  }
  return {
    ecrireMonde(dest) {
      const k = Math.min(qui.length, dest.length / 3);
      for (let i = 0; i < k; i++) v.fromArray(locales[qui[i]], rang[i] * 3).applyMatrix4(pieces[qui[i]].maillage.matrixWorld).toArray(dest, i * 3);
    },
  };
}
