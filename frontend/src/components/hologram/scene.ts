// Globe hologramme : la scène three.js, portée d'Athéna (client/src/components/HologrammeTerre.tsx, 04/10 → 12/10/2026).
// Construite une fois, pilotée ensuite par `Pilote`. Dans AURORA, le globe est toujours « grand » (il remplit la page
// Monde 3D) : le petit globe en coin d'Athéna et ses avions de décoration ne sont pas repris ; ses anneaux et son socle
// restent visibles de loin. Nouveau dans AURORA : allerA() (sur le modèle du recentrage de avions(), Athéna l. 768),
// le repère du lieu demandé et l'animation de recherche (balayage, ondes, halo plus vif) — une animation seulement.
// Les continents viennent de la Blue Marble (domaine public) : on garde les points d'une spirale de Fibonacci qui
// tombent sur une terre (4 194 points sur 14 000, 30 %, pour 29 % de terres réelles).
import type * as Trois from 'three';
import type { GlobeAircraft, GlobeEvent, GlobeSatellite, GlobeTrafficData, GlobeWindData } from '../../lib/types';
import { api } from '../../lib/api';
import { RAD, angularDistanceDeg, clamp, easeInOutCubic, norm180 } from '../globe/geo';
import { chargerModele } from './glb';
import { VERTEX_NORMALE, VERTEX_UV } from './shaders';
import {
  ECHELLES, EXAGERATION_AVIONS, IMAGE_LOCALE, MAX_AVIONS_AFFICHES, PHOTOS_GUIDE, STATIONS, ZOOM_DETAIL, chargerLocal, couleurEchelle,
  photoCommons, sansParentheses, valeurGrille, versXYZ, type Capitale, type CarteValeurs, type Choix, type Lieu, type PointActu, type SiteGuide,
} from './outils';

type Three = typeof Trois;
type Categorie = 'ligne' | 'leger' | 'rapide';
type V3 = [number, number, number];

export interface Pilote {
  /** La taille du canevas en pixels CSS, et le coin réservé aux icônes (en haut à gauche). */
  redimensionner(l: number, h: number, reserve: { l: number; h: number }): void;
  avions(lieu: Lieu | null, liste: GlobeAircraft[], demiCote: number): void;
  monde(d: GlobeTrafficData | null): void;
  pointSurGlobe(x: number, y: number): { lat: number; lon: number } | null;
  voirMonde(): void;
  vent(g: GlobeWindData | null): void;
  evenements(liste: GlobeEvent[]): void;
  couches(c: { meteo: boolean; volants: boolean; routes: boolean }): void;
  carte(v: CarteValeurs | null): void;
  valeurEn(x: number, y: number): { lat: number; lon: number; valeur: number } | null;
  satellites(liste: GlobeSatellite[]): void;
  /** Image du jour ; liberer : URL d'objet (blob:) à rendre une fois l'image lue. */
  texture(url: string, liberer?: boolean): void;
  choisir(x: number, y: number): Choix | null;
  surligner(c: Choix | null): void;
  zoom(dy: number): void;
  tourner(dx: number, dy: number): void;
  guide(sites: SiteGuide[] | null, choisir: (i: number) => void): void;
  /** Photos de Wikimedia Commons permises (jamais en Mode Privé). */
  photos(permis: boolean): void;
  detail(permis: boolean): void;
  actualites(points: PointActu[] | null): void;
  choisirActu(x: number, y: number): string | null;
  /** Aller en douceur vers un point (rotation et zoom) ; distance en rayons terrestres depuis le centre. */
  allerA(lat: number, lon: number, distance?: number): void;
  /** Le repère du lieu demandé à AURORA (cercle qui pulse), ou aucun. */
  repere(p: { lat: number; lon: number } | null): void;
  /** Animation de recherche : balayage, ondes, halo plus vif. Animation seulement. */
  recherche(on: boolean): void;
  mouvementReduit(on: boolean): void;
}

export interface SceneGlobe { pilote: Pilote; liberer(): void }


/** null : pas de WebGL (le moteur de rendu n'a pas pu naître). */
export function creerScene(THREE: Three, canvas: HTMLCanvasElement, boite: () => HTMLDivElement | null): SceneGlobe | null {
  let rendu: Trois.WebGLRenderer;
  try { rendu = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true }); } catch { return null; }
  let annule = false, image = 0;
  let lCss = Math.max(1, canvas.clientWidth), hCss = Math.max(1, canvas.clientHeight), reserveL = 0, reserveH = 0, dimensionne = false;
  rendu.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  rendu.setSize(lCss, hCss, false);
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(32, lCss / hCss, .01, 20);
  /** Distance de la caméra au centre, en rayons terrestres : 3,9 montre la Terre entière (le grand globe d'Athéna). */
  let distanceDefaut = 3.9, distance = 3.9, distanceCible = 3.9;
  camera.position.set(0, 0, distance); camera.lookAt(0, 0, 0);

  const teinte = new THREE.Color('#3d8bff');
  const terre = new THREE.Group(); scene.add(terre);
  // Sphère invisible qui masque la face arrière : sinon les continents ne se lisent pas.
  const masque = new THREE.Mesh(new THREE.SphereGeometry(.985, 48, 48), new THREE.MeshBasicMaterial({ colorWrite: false }));
  masque.renderOrder = -1; terre.add(masque);
  // L'image de la Terre (mélange additif). Deux images : celle du jour (NASA GIBS, ou la Blue Marble hors direct) et la
  // Blue Marble du PC dessous. Là où l'image du jour est noire (zone pas encore survolée par le satellite), on voit la
  // Blue Marble, assombrie : le 04/10, cette bande noire faisait un grand trou dans le globe (ID Studio IA).
  const matFond = new THREE.ShaderMaterial({
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    uniforms: { jour: { value: null }, base: { value: null } },
    vertexShader: VERTEX_UV,
    fragmentShader: 'uniform sampler2D jour;uniform sampler2D base;varying vec2 vUv;void main(){vec3 j=texture2D(jour,vUv).rgb;vec3 b=texture2D(base,vUv).rgb;float vide=1.-smoothstep(.02,.06,max(j.r,max(j.g,j.b)));gl_FragColor=linearToOutputTexel(vec4(mix(j,b*.85,vide)*.8,1.));}',
  });
  let texBase: Trois.Texture | null = null;
  new THREE.TextureLoader().load(IMAGE_LOCALE, tex => {
    if (annule) { tex.dispose(); return; }
    tex.colorSpace = THREE.SRGBColorSpace; texBase = tex; matFond.uniforms.base.value = tex;
  });
  const fond = new THREE.Mesh(new THREE.SphereGeometry(.99, 128, 64), matFond);
  fond.visible = false; fond.renderOrder = -.5; terre.add(fond);
  // La carte détaillée (ID Studio IA, 12/10 : « améliore la qualité de la map en zoom max »). L'image du monde fait 2 048 px,
  // ~20 km par pixel : floue de près. Au zoom fort, et seulement le direct activé, la région regardée est redemandée à
  // la NASA (Blue Marble NG, ~500 m par pixel, /api/globe/detail) et posée par-dessus, bords fondus.
  const matDetail = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, uniforms: { carte: { value: null }, fondu: { value: 0 } },
    vertexShader: VERTEX_UV,
    fragmentShader: 'uniform sampler2D carte;uniform float fondu;varying vec2 vUv;void main(){vec2 b=min(vUv,1.-vUv);float bord=smoothstep(0.,.08,min(b.x,b.y));gl_FragColor=linearToOutputTexel(vec4(texture2D(carte,vUv).rgb*.8,bord*fondu));}',
  });
  const detailMesh = new THREE.Mesh(new THREE.BufferGeometry(), matDetail);
  detailMesh.renderOrder = -.4; detailMesh.visible = false; terre.add(detailMesh);
  let detailPermis = false, detailBbox: number[] | null = null, detailEnCours = false, detailVoulu = '', detailVuDepuis = 0;
  const poserDetail = (b: number[], tex: Trois.Texture) => {
    const N = 40, pos: number[] = [], uv: number[] = [], idx: number[] = [];
    for (let i = 0; i <= N; i++) for (let j = 0; j <= N; j++) {
      const la = b[0] + (b[2] - b[0]) * i / N, lo = b[1] + (b[3] - b[1]) * j / N;
      pos.push(...versXYZ(la, lo, .992)); uv.push(j / N, i / N);
    }
    for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) { const a = i * (N + 1) + j; idx.push(a, a + 1, a + N + 1, a + 1, a + N + 2, a + N + 1); }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2)); g.setIndex(idx);
    detailMesh.geometry.dispose(); detailMesh.geometry = g;
    (matDetail.uniforms.carte.value as Trois.Texture | null)?.dispose(); matDetail.uniforms.carte.value = tex;
    detailBbox = b; detailMesh.visible = true;
  };
  /**
   * À chaque image : la région regardée, et faut-il la redemander ? Seulement immobile depuis 0,4 s (pas pendant un
   * glisser), si la vue sort de la région déjà chargée ou si celle-ci est 2,5 fois trop grande.
   */
  const majDetail = (aspect: number) => {
    const voulu = detailPermis && distance < ZOOM_DETAIL && !!detailBbox;
    const f = matDetail.uniforms.fondu;
    f.value += ((voulu ? 1 : 0) - f.value) * .12;
    if (!detailPermis || distance >= ZOOM_DETAIL) return;
    // Demi-angle vu au sol depuis la caméra (loi des sinus), en degrés, avec une marge de 1,4.
    const demi = (a: number) => { const s = distance * Math.sin(a); return s >= 1 ? 80 : (Math.asin(s) - a) / RAD; };
    const dLa = Math.min(30, demi(16 * RAD) * 1.4), dLo = Math.min(60, demi(Math.atan(Math.tan(16 * RAD) * aspect)) * 1.4 / Math.max(Math.cos(centreLat * RAD), .15));
    const v = [centreLat - dLa, centreLon - dLo, centreLat + dLa, centreLon + dLo];
    // Le service refuse une région de plus de 40° de haut (sky.DETAIL_MAX_LAT_SPAN) : on attend d'être plus près
    // plutôt que d'envoyer une demande perdue d'avance à chaque vue (08/11/2026).
    if (2 * dLa > 40) return;
    const cle = v.map(x => x.toFixed(1)).join(',');
    if (cle !== detailVoulu) { detailVoulu = cle; detailVuDepuis = performance.now(); return; }
    if (detailEnCours || performance.now() - detailVuDepuis < 400) return;
    const b = detailBbox, couvre = b && v[0] >= b[0] && v[1] >= b[1] && v[2] <= b[2] && v[3] <= b[3] && (b[2] - b[0]) < (v[2] - v[0]) * 2.5;
    if (couvre) return;
    detailEnCours = true;
    // 25 s au plus : une demande restée en attente bloquait toutes les suivantes (Athéna, 12/10).
    api.globeImage('detail', v.map(x => x.toFixed(3)).join(','), AbortSignal.timeout(25000)).then(rep => {
      if (annule) return;
      // Échec : on réessaie dans 30 s (5 min en Mode Privé, qui répond 403 sans appel réseau).
      if (!rep.ok) { detailEnCours = false; detailVuDepuis = performance.now() + (rep.private_mode ? 300000 : 30000); return; }
      const url = URL.createObjectURL(rep.blob);
      new THREE.TextureLoader().load(url, tex => {
        URL.revokeObjectURL(url);
        detailEnCours = false;
        if (annule) { tex.dispose(); return; }
        tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4; tex.minFilter = THREE.LinearFilter; tex.generateMipmaps = false;
        if (rep.bbox) poserDetail(rep.bbox, tex); else tex.dispose();
      }, undefined, () => { URL.revokeObjectURL(url); detailEnCours = false; });
    }).catch(() => { detailEnCours = false; detailVuDepuis = performance.now() + 30000; });
  };

  const matLigne = new THREE.LineBasicMaterial({ color: teinte, transparent: true, opacity: .3, blending: THREE.AdditiveBlending, depthWrite: false });
  const cercle = (r: number, y: number, n = 96) => {
    const p: Trois.Vector3[] = [];
    for (let i = 0; i <= n; i++) { const a = i / n * Math.PI * 2; p.push(new THREE.Vector3(Math.cos(a) * r, y, Math.sin(a) * r)); }
    return new THREE.BufferGeometry().setFromPoints(p);
  };
  for (let lat = -75; lat <= 75; lat += 15) { const a = lat * RAD; terre.add(new THREE.Line(cercle(Math.cos(a), Math.sin(a)), matLigne)); }
  for (let lon = 0; lon < 180; lon += 20) { const m = new THREE.Line(cercle(1, 0), matLigne); m.rotation.x = Math.PI / 2; m.rotation.z = lon * RAD; terre.add(m); }
  const gPts = new THREE.BufferGeometry();
  // Le grand globe d'Athéna : points fins, plus pâles, qui s'effacent au zoom fort (l'image prend le relais).
  const matPts = new THREE.PointsMaterial({ color: teinte, size: .012, transparent: true, opacity: .45, blending: THREE.AdditiveBlending, depthWrite: false });
  terre.add(new THREE.Points(gPts, matPts));
  const img = new Image(); img.src = IMAGE_LOCALE;
  img.decode().then(() => {
    if (annule) return;
    const cv = document.createElement('canvas'); cv.width = 1024; cv.height = 512;
    const cx = cv.getContext('2d', { willReadFrequently: true }); if (!cx) return;
    cx.drawImage(img, 0, 0, 1024, 512);
    const px = cx.getImageData(0, 0, 1024, 512).data, pts: number[] = [], N = 14000, or = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < N; i++) {
      const y = 1 - 2 * (i + .5) / N, r = Math.sqrt(1 - y * y), a = i * or, x = Math.cos(a) * r, z = Math.sin(a) * r;
      const lat = Math.asin(y), lon = Math.atan2(-z, x), u = Math.floor((lon + Math.PI) / (2 * Math.PI) * 1023), v = Math.floor((Math.PI / 2 - lat) / Math.PI * 511), k = (v * 1024 + u) * 4;
      // Mer = bleu dominant ; terre, désert et glace = le reste.
      if (px[k] + px[k + 1] > px[k + 2] * 1.3 || px[k] + px[k + 1] + px[k + 2] > 600) pts.push(x * 1.004, y * 1.004, z * 1.004);
    }
    gPts.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3)); gPts.computeBoundingSphere();
    canvas.dataset.continents = String(pts.length / 3);
  }).catch(() => { /* carte absente : le globe reste en lignes seules */ });
  // Le halo ; « vif » l'avive pendant une recherche.
  const matHalo = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms: { c: { value: teinte }, vif: { value: 0 } },
    vertexShader: VERTEX_NORMALE,
    fragmentShader: 'uniform vec3 c;uniform float vif;varying vec3 n;varying vec3 p;void main(){float f=pow(1.-abs(dot(n,vec3(0,0,1.))),2.5-vif*.9);gl_FragColor=vec4(mix(c,vec3(.7,.95,1.),vif*.4),f*.9*(1.+vif*.8));}',
  });
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(1.02, 48, 48), matHalo));
  // Anneaux et socle : la parure du petit globe d'Athéna. Visibles de loin, effacés en zoomant.
  const matDecor = new THREE.LineBasicMaterial({ color: teinte, transparent: true, opacity: .3, blending: THREE.AdditiveBlending, depthWrite: false });
  const anneaux = [1.35, 1.55].map((r, i) => { const a = new THREE.Line(cercle(r, 0, 128), matDecor); a.rotation.x = 1.2 + i * .25; a.rotation.y = i * .6; scene.add(a); return a; });
  const socle = [.9, .65, .4].map(r => { const s = new THREE.Line(cercle(r, -1.35, 96), matDecor); scene.add(s); return s; });
  // L'animation de recherche : une coquille juste au-dessus du sol, dans le repère de la scène (elle ne tourne pas avec
  // la Terre). Un méridien de lumière en fait le tour, des cercles partent du point face à la caméra.
  const matRecherche = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { temps: { value: 0 }, force: { value: 0 }, mouvement: { value: 1 }, c: { value: new THREE.Color('#7fe8ff') } },
    vertexShader: VERTEX_NORMALE,
    fragmentShader: 'uniform float temps;uniform float force;uniform float mouvement;uniform vec3 c;varying vec3 n;varying vec3 p;void main(){'
      + 'float face=max(dot(n,vec3(0.,0.,1.)),0.);'
      + 'float d=abs(mod(atan(p.x,p.z)-temps*1.7+3.14159265,6.2831853)-3.14159265);float bande=exp(-d*d*28.)*mouvement;'
      + 'float r=acos(clamp(p.z,-1.,1.));float onde=pow(fract(r*2.4-temps*.8),10.)*(1.-smoothstep(.2,1.5,r))*mouvement;'
      + 'gl_FragColor=vec4(c,force*(bande*.6+onde*.5+.1*face)*(.35+.65*face));}',
  });
  const coque = new THREE.Mesh(new THREE.SphereGeometry(1.012, 96, 64), matRecherche);
  coque.renderOrder = 2; coque.visible = false; scene.add(coque);

  // ── Le ciel : avions, région, satellites. Accrochés à la Terre (coordonnées géographiques).
  const ciel = new THREE.Group(); terre.add(ciel);
  const zone = new THREE.Group(); ciel.add(zone); // avions, capitales, météo, carte routière
  const ambre = new THREE.Color('#ffb347'), cyan = new THREE.Color('#7ff7ff');
  // Silhouettes, à plat : nez vers +Z, dessus vers +Y, longueur 1 ; remplacées par les modèles d’ID Studio IA dès qu'ils sont lus.
  const triangles = (t: number[]) => { const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(t, 3)); return g; };
  const formeAvion = triangles([
    0, 0, .5, -.06, 0, -.4, .06, 0, -.4, // fuselage
    0, 0, .15, -.48, 0, -.12, .48, 0, -.12, // ailes
    0, 0, -.3, -.18, 0, -.5, .18, 0, -.5, // empennage
  ]);
  const formeSat = triangles([
    -.12, 0, .15, .12, 0, .15, .12, 0, -.15, -.12, 0, .15, .12, 0, -.15, -.12, 0, -.15, // corps
    -.6, 0, .1, -.16, 0, .1, -.16, 0, -.1, -.6, 0, .1, -.16, 0, -.1, -.6, 0, -.1, // panneau gauche
    .16, 0, .1, .6, 0, .1, .6, 0, -.1, .16, 0, .1, .6, 0, -.1, .16, 0, -.1, // panneau droit
  ]);
  // Une famille d'instances par modèle : la silhouette d'abord, remplacée par le modèle 3D dès qu'il est lu.
  // S'il manque, la silhouette reste — rien d'autre n'en dépend.
  const famille = (parent: Trois.Object3D, forme: Trois.BufferGeometry, couleur: Trois.Color, max: number, fichier: string, redresser: (g: Trois.BufferGeometry) => void) => {
    const m = new THREE.InstancedMesh(forme, new THREE.MeshBasicMaterial({ color: couleur, side: THREE.DoubleSide, transparent: true, opacity: .95, depthWrite: false }), max);
    m.count = 0; m.frustumCulled = false; parent.add(m);
    chargerModele(THREE, `/terre/modeles/${fichier}`).then(({ geometrie, texture }) => {
      if (annule) { geometrie.dispose(); texture?.dispose(); return; }
      redresser(geometrie); m.geometry = geometrie;
      (m.material as Trois.Material).dispose();
      m.material = texture ? new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide }) : new THREE.MeshBasicMaterial({ color: couleur, side: THREE.DoubleSide });
    }).catch(() => { /* modèle absent : la silhouette reste */ });
    return m;
  };
  // Vérifié sur leurs rendus (Athéna, 04/10) : avions nez vers −X → tournés vers +Z ; satellite et station
  // panneaux vers +Z → couchés vers +Y, face au ciel.
  const nezVersZ = (g: Trois.BufferGeometry) => { g.rotateY(Math.PI / 2); }, faceAuCiel = (g: Trois.BufferGeometry) => { g.rotateX(-Math.PI / 2); };
  const avionsMeshes: Record<Categorie, Trois.InstancedMesh> = {
    ligne: famille(zone, formeAvion, ambre, MAX_AVIONS_AFFICHES, 'avion-commercial.glb', nezVersZ),
    leger: famille(zone, formeAvion, ambre, MAX_AVIONS_AFFICHES, 'petit-avion.glb', nezVersZ),
    rapide: famille(zone, formeAvion, ambre, MAX_AVIONS_AFFICHES, 'avion-chasse.glb', nezVersZ),
  };
  const satsMeshes: Record<'satellite' | 'station', Trois.InstancedMesh> = {
    satellite: famille(ciel, formeSat, cyan, 32, 'satellite.glb', faceAuCiel),
    station: famille(ciel, formeSat, cyan, 4, 'station.glb', faceAuCiel),
  };
  // Trafic mondial : silhouettes simples. Un modèle de 8 000 triangles × 1 500 avions serait trop lourd.
  const mondeMesh = new THREE.InstancedMesh(formeAvion, new THREE.MeshBasicMaterial({ color: ambre, side: THREE.DoubleSide, transparent: true, opacity: .9, depthWrite: false }), 1500);
  mondeMesh.count = 0; mondeMesh.frustumCulled = false; zone.add(mondeMesh);
  let traficMonde: GlobeTrafficData | null = null;
  // Capitales : un point blanc chacune, et leur nom selon le zoom (les plus grandes d'abord).
  const gCapitales = new THREE.BufferGeometry();
  zone.add(new THREE.Points(gCapitales, new THREE.PointsMaterial({ color: 0xffffff, size: 3, sizeAttenuation: false, transparent: true, opacity: .9, depthWrite: false })));
  let capitales: { nom: string; rang: number; p: V3 }[] = [], etiquettesCapitales: HTMLSpanElement[] = [], ordreCapitales: number[] = [];
  chargerLocal<{ capitales: Capitale[] }>('/terre/capitales.json').then(d => {
    if (annule || !Array.isArray(d?.capitales)) return;
    capitales = d.capitales.map(c => ({ nom: c.nom, rang: c.rang, p: versXYZ(c.lat, c.lon, 1.002) }));
    ordreCapitales = capitales.map((_, i) => i).sort((a, b) => capitales[a].rang - capitales[b].rang);
    gCapitales.setAttribute('position', new THREE.Float32BufferAttribute(capitales.flatMap(c => c.p), 3));
    construireEtiquettes();
  }).catch(() => { /* fichier absent : pas de capitales */ });

  // ── Couche météo : vent, pluie, catastrophes (icône 1).
  const meteoCouche = new THREE.Group(); meteoCouche.visible = false; zone.add(meteoCouche);
  const N_VENT = 4000, N_PLUIE = 1500;
  const posVent = new Float32Array(N_VENT * 6), colVent = new Float32Array(N_VENT * 8), gVent = new THREE.BufferGeometry();
  gVent.setAttribute('position', new THREE.BufferAttribute(posVent, 3)); gVent.setAttribute('color', new THREE.BufferAttribute(colVent, 4)); // RVBA : la traînée s'efface vers l'arrière
  // Mélange normal, pas additif : en additif, le blanc des nuages effaçait le vent (Athéna, 04/10).
  meteoCouche.add(new THREE.LineSegments(gVent, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false })));
  // Pluie : des points bleus qui tombent et s'éteignent (des traits verticaux, vus d'en haut, restaient immobiles).
  const posPluie = new Float32Array(N_PLUIE * 3), colPluie = new Float32Array(N_PLUIE * 4), gPluie = new THREE.BufferGeometry();
  gPluie.setAttribute('position', new THREE.BufferAttribute(posPluie, 3)); gPluie.setAttribute('color', new THREE.BufferAttribute(colPluie, 4)); gPluie.setDrawRange(0, 0);
  // Points ronds (texture dessinée) : un disque pour l'événement, un anneau qui s'élargit autour.
  const dessin = (anneau: boolean) => {
    const c = document.createElement('canvas'); c.width = c.height = 64; const x = c.getContext('2d');
    if (x && anneau) { x.strokeStyle = '#fff'; x.lineWidth = 5; x.beginPath(); x.arc(32, 32, 27, 0, Math.PI * 2); x.stroke(); }
    else if (x) { const g = x.createRadialGradient(32, 32, 0, 32, 32, 32); g.addColorStop(0, '#fff'); g.addColorStop(.55, 'rgba(255,255,255,.95)'); g.addColorStop(1, 'rgba(255,255,255,0)'); x.fillStyle = g; x.fillRect(0, 0, 64, 64); }
    return new THREE.CanvasTexture(c);
  };
  const disque = dessin(false), anneau = dessin(true);
  meteoCouche.add(new THREE.Points(gPluie, new THREE.PointsMaterial({ size: 4, sizeAttenuation: false, map: disque, vertexColors: true, transparent: true, depthWrite: false })));
  const gEv = new THREE.BufferGeometry();
  const matEv = new THREE.PointsMaterial({ color: 0xff2a2a, size: 10, sizeAttenuation: false, map: disque, transparent: true, depthWrite: false });
  const matOnde = new THREE.PointsMaterial({ color: 0xff3b3b, size: 12, sizeAttenuation: false, map: anneau, transparent: true, depthWrite: false });
  meteoCouche.add(new THREE.Points(gEv, matEv), new THREE.Points(gEv, matOnde));
  let grille: GlobeWindData | null = null, gouttes: { lat: number; lon: number; phase: number }[] = [], listeEv: { e: GlobeEvent; p: V3 }[] = [];
  const particules = Array.from({ length: N_VENT }, () => ({ lat: 0, lon: 0, age: 0, vie: 1 }));
  // Répartition uniforme sur la sphère (asin), sinon les pôles seraient surpeuplés.
  const renaitre = (p: { lat: number; lon: number; age: number; vie: number }) => { p.lat = Math.asin(Math.random() * 1.96 - .98) / RAD; p.lon = Math.random() * 360 - 180; p.age = 0; p.vie = 3 + Math.random() * 5; };
  particules.forEach(p => { renaitre(p); p.age = Math.random() * p.vie; });
  /** Vent mesuré au point, interpolé entre les 4 nœuds voisins de la grille (m/s vers l'est, vers le nord). */
  const ventEn = (g: GlobeWindData, lat: number, lon: number): [number, number] => [valeurGrille(g, g.u, lat, lon), valeurGrille(g, g.v, lat, lon)];
  // La carte colorée (température, humidité ou qualité de l'air) : une image 360 × 180 peinte depuis la grille
  // mesurée, posée sur une sphère juste au-dessus de l'image NASA.
  const toile = document.createElement('canvas'); toile.width = 360; toile.height = 180;
  const texCarte = new THREE.CanvasTexture(toile); texCarte.colorSpace = THREE.SRGBColorSpace;
  const sphereCarte = new THREE.Mesh(new THREE.SphereGeometry(.993, 128, 64), new THREE.MeshBasicMaterial({ map: texCarte, transparent: true, opacity: .55, depthWrite: false }));
  sphereCarte.renderOrder = 1; sphereCarte.visible = false; meteoCouche.add(sphereCarte);
  let carteActuelle: CarteValeurs | null = null;

  // ── Carte routière : chargée à la première activation (3,4 Mo), jamais avant.
  const routiere = new THREE.Group(); routiere.visible = false; zone.add(routiere);
  const gVilles = new THREE.BufferGeometry();
  routiere.add(new THREE.Points(gVilles, new THREE.PointsMaterial({ color: 0xffffff, size: 2.5, sizeAttenuation: false, transparent: true, opacity: .85, depthWrite: false })));
  const matRoutes = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: .85, depthWrite: false });
  let villes: { n: string; r: number; p: V3 }[] = [], routiereDemandee = false, etiquettesVilles: HTMLSpanElement[] = [];
  // Couleurs « carte routière » : autoroutes rouges, nationales jaunes, routes blanches, ferries bleus, le reste gris.
  const COULEURS_ROUTES: Record<number, V3> = { 0: [.55, .55, .62], 1: [1, .25, .2], 2: [1, .82, .2], 3: [.95, .95, .95], 4: [.35, .65, 1] };
  const chargerRoutiere = async () => {
    if (routiereDemandee) return; routiereDemandee = true;
    try {
      const [v, b] = await Promise.all([
        chargerLocal<{ villes: { n: string; r: number; la: number; lo: number }[] }>('/terre/villes.json'),
        fetch('/terre/routes.bin').then(r => { if (!r.ok) throw new Error('routes.bin'); return r.arrayBuffer(); }),
      ]);
      if (annule) return;
      villes = v.villes.map(x => ({ n: x.n, r: x.r, p: versXYZ(x.la, x.lo, 1.003) }));
      gVilles.setAttribute('position', new THREE.Float32BufferAttribute(villes.flatMap(x => x.p), 3));
      // routes.bin : -32768, type, puis des couples (lat × 100, lon × 100). Deux passes : compter, puis remplir.
      const d = new Int16Array(b);
      let segments = 0;
      for (let i = 0; i < d.length;) { i += 2; let n = 0; while (i < d.length && d[i] !== -32768) { i += 2; n++; } segments += Math.max(0, n - 1); }
      const pos = new Float32Array(segments * 6), col = new Float32Array(segments * 6);
      let k = 0;
      for (let i = 0; i < d.length;) {
        i++; const cc = COULEURS_ROUTES[d[i++]] ?? COULEURS_ROUTES[0]; let prec: V3 | null = null;
        while (i < d.length && d[i] !== -32768) {
          const p = versXYZ(d[i] / 100, d[i + 1] / 100, 1.0025); i += 2;
          if (prec) { pos.set(prec, k); pos.set(p, k + 3); col.set(cc, k); col.set(cc, k + 3); k += 6; }
          prec = p;
        }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('color', new THREE.BufferAttribute(col, 3));
      const lignes = new THREE.LineSegments(g, matRoutes); lignes.frustumCulled = false; routiere.add(lignes);
      construireEtiquettes();
    } catch {
      routiereDemandee = false; // fichiers absents : on pourra réessayer
    }
  };

  // ── Le guide de voyage : un point doré par lieu du patrimoine mondial ; les PHOTOS viennent en zoomant
  // (PHOTOS_GUIDE), les plus connus d'abord, et jamais en Mode Privé.
  const guideCouche = new THREE.Group(); guideCouche.visible = false; terre.add(guideCouche);
  const gGuide = new THREE.BufferGeometry();
  guideCouche.add(new THREE.Points(gGuide, new THREE.PointsMaterial({ color: 0xffc35a, size: 4, sizeAttenuation: false, transparent: true, opacity: .9, depthWrite: false })));
  let sitesGuide: { n: string; img?: string; p: V3 }[] = [], cartesGuide: HTMLButtonElement[] = [], choisirSite: (i: number) => void = () => {}, photosPermises = false;
  // ── L'actualité : des points violets ronds (carrés et pâles, on ne les voyait pas — Athéna, 12/10).
  const actuCouche = new THREE.Group(); actuCouche.visible = false; terre.add(actuCouche);
  const gActu = new THREE.BufferGeometry(), gActuFort = new THREE.BufferGeometry();
  const matActu = new THREE.PointsMaterial({ color: 0xb36bff, size: 11, sizeAttenuation: false, map: disque, transparent: true, depthWrite: false });
  const matActuFort = new THREE.PointsMaterial({ color: 0xc78dff, size: 17, sizeAttenuation: false, map: disque, transparent: true, depthWrite: false });
  const matActuOnde = new THREE.PointsMaterial({ color: 0xc78dff, size: 14, sizeAttenuation: false, map: anneau, transparent: true, depthWrite: false });
  actuCouche.add(new THREE.Points(gActu, matActu), new THREE.Points(gActuFort, matActuFort), new THREE.Points(gActuFort, matActuOnde));
  let pointsActu: { pays: string; p: V3 }[] = [];
  // ── Le repère du lieu demandé à AURORA (nouveau) : un point clair et un cercle qui pulse.
  const repereCouche = new THREE.Group(); repereCouche.visible = false; terre.add(repereCouche);
  const gRepere = new THREE.BufferGeometry();
  gRepere.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0], 3));
  const matRepereOnde = new THREE.PointsMaterial({ color: 0x7ff7ff, size: 18, sizeAttenuation: false, map: anneau, transparent: true, depthWrite: false });
  repereCouche.add(new THREE.Points(gRepere, new THREE.PointsMaterial({ color: 0xe9fdff, size: 8, sizeAttenuation: false, map: disque, transparent: true, depthWrite: false })),
    new THREE.Points(gRepere, matRepereOnde));
  const gRegion = new THREE.BufferGeometry(), gVille = new THREE.BufferGeometry();
  zone.add(new THREE.LineLoop(gRegion, new THREE.LineBasicMaterial({ color: ambre, transparent: true, opacity: .45, depthWrite: false })));
  ciel.add(new THREE.Points(gVille, new THREE.PointsMaterial({ color: 0xffffff, size: .016, depthWrite: false })));
  const traces = new THREE.Group(); ciel.add(traces);
  const matTrace = new THREE.LineBasicMaterial({ color: cyan, transparent: true, opacity: .35, depthWrite: false });
  const gSurbrillance = new THREE.BufferGeometry();
  gSurbrillance.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0], 3));
  const matSurbrillance = new THREE.PointsMaterial({ color: 0xffffff, size: .045, transparent: true, opacity: .35, depthWrite: false });
  const surbrillance = new THREE.Points(gSurbrillance, matSurbrillance);
  surbrillance.visible = false; ciel.add(surbrillance);

  /**
   * Un avion entre deux mesures. La source ne donne qu'une position par requête, relue toutes les 30 s. Entre deux,
   * la position est PROLONGÉE avec la vitesse et le cap mesurés, PROLONGER_MAX secondes au plus ; au-delà, l'avion
   * s'arrête là plutôt que de voler dans le vide. Quand la mesure suivante arrive, l'écart avec la position affichée
   * est rattrapé en RATTRAPAGE ms au lieu d'un saut. Le trafic MONDIAL n'est relu que toutes les 5 min (quota
   * d'OpenSky) : prolongé jusqu'à PROLONGER_MONDE.
   */
  interface Vol { a: GlobeAircraft; corrLat: number; corrLon: number; t0: number; p: V3 }
  // Tailles multipliées par la hauteur de la caméra au-dessus du sol : ~12 px à l'écran quel que soit le zoom.
  const PROLONGER_MAX = 150, PROLONGER_MONDE = 420, RATTRAPAGE = 2500, TAILLE_AVION = .032, TAILLE_MONDE = .018, TAILLE_SAT = .045;
  let vols: Vol[] = [];
  interface SatAnime { s: GlobeSatellite; recuA: number; p: V3 }
  let satsAnimes: SatAnime[] = [];
  let lieu: Lieu | null = null, choisi: { genre: 'avion'; id: string } | { genre: 'sat'; norad: number } | { genre: 'evenement'; p: V3 } | null = null;
  let textureActuelle: Trois.Texture | null = null, seqTexture = 0;
  // Le point du globe face à la caméra. Il défile seul tant qu'on n'a ni glissé ni choisi de zone.
  let centreLat = 20, centreLon = 0, tourneSeule = true, demiCoteZone = 0, volantsVisibles = true, dernierGeste = 0;
  /** Un lieu demandé à AURORA : la Terre reste dessus jusqu'au prochain glisser. */
  let focusFixe = false, reduit = false, rechercheVoulue = false, forceRecherche = 0, tempsRecherche = 0;
  let trajet: { t0: number; duree: number; la0: number; lo0: number; la1: number; dlo: number; d0: number; d1: number; bosse: number } | null = null;
  let etiquettesSats: HTMLSpanElement[] = [];
  terre.rotation.set(centreLat * RAD, -Math.PI / 2 - centreLon * RAD, 0);
  const rayonAvion = (a: GlobeAircraft) => 1 + Math.max(a.altitude ?? 0, 0) / 6371000 * EXAGERATION_AVIONS + .004;
  const prolonger = (a: GlobeAircraft, maintenant: number): [number, number] => {
    if (a.vitesse == null || a.cap == null) return [a.lat, a.lon];
    const s = Math.min(Math.max((maintenant - a.mesure) / 1000, 0), PROLONGER_MAX);
    const d = a.vitesse / 3.6 * s / 6371000 / RAD; // degrés d'arc parcourus
    return [a.lat + d * Math.cos(a.cap * RAD), a.lon + d * Math.sin(a.cap * RAD) / Math.max(Math.cos(a.lat * RAD), .2)];
  };
  /** Le satellite le long de sa trace (une position calculée par minute), interpolée. */
  const surTrace = (x: SatAnime, maintenant: number): V3 => {
    const tr = x.s.trace;
    if (tr.length < 2) return [x.s.lat, x.s.lon, 0];
    const m = Math.max(0, (maintenant - x.recuA) / 60000), i = Math.min(Math.floor(m), tr.length - 2), f = Math.min(m - i, 1);
    const [la0, lo0] = tr[i], [la1, lo1] = tr[i + 1], dlo = ((lo1 - lo0 + 540) % 360) - 180;
    const la = la0 + (la1 - la0) * f;
    return [la, lo0 + dlo * f, Math.atan2(dlo * Math.cos(la * RAD), la1 - la0) / RAD];
  };

  const mat = new THREE.Matrix4(), vX = new THREE.Vector3(), vY = new THREE.Vector3(), vZ = new THREE.Vector3(), echelle = new THREE.Vector3();
  /** Pose une silhouette : à plat sur la sphère (Y = verticale), nez vers le cap (0 = nord). */
  const poser = (mesh: Trois.InstancedMesh, i: number, lat: number, lon: number, r: number, cap: number, taille: number): V3 => {
    const a = lat * RAD, o = lon * RAD, h = cap * RAD, ca = Math.cos(a), sa = Math.sin(a), co = Math.cos(o), so = Math.sin(o);
    vY.set(ca * co, sa, -ca * so);
    // nord = dérivée par la latitude, est = dérivée par la longitude (même convention que versXYZ)
    vZ.set(Math.cos(h) * -sa * co + Math.sin(h) * -so, Math.cos(h) * ca, Math.cos(h) * sa * so + Math.sin(h) * -co);
    vX.crossVectors(vY, vZ);
    mat.makeBasis(vX, vY, vZ).scale(echelle.set(taille, taille, taille)).setPosition(vY.x * r, vY.y * r, vY.z * r);
    mesh.setMatrixAt(i, mat);
    return [vY.x * r, vY.y * r, vY.z * r];
  };

  const monde = new THREE.Vector3();
  /** Visible si devant l'horizon : p·c ≥ |p|² (sphère unité vue depuis c). */
  const versEcran = (local: V3, l: number, h: number) => {
    monde.set(local[0], local[1], local[2]); terre.localToWorld(monde);
    if (monde.dot(camera.position) < monde.lengthSq() * .999) return null;
    const v = monde.clone().project(camera);
    if (v.z > 1 || Math.abs(v.x) > 1.05 || Math.abs(v.y) > 1.05) return null;
    return { x: (v.x + 1) / 2 * l, y: (1 - v.y) / 2 * h };
  };

  const construireEtiquettes = () => {
    const b = boite();
    if (!b) { etiquettesSats = []; etiquettesCapitales = []; etiquettesVilles = []; cartesGuide = []; return; }
    b.replaceChildren();
    const etiquette = (classe: string, texte = '') => { const el = document.createElement('span'); el.className = classe; el.textContent = texte; b.appendChild(el); return el; };
    etiquettesSats = satsAnimes.map(x => etiquette('holo-etiquette', sansParentheses(x.s.nom)));
    etiquettesCapitales = capitales.map(c => etiquette('holo-etiquette holo-capitale', c.nom));
    cartesGuide = guideCouche.visible && sitesGuide.length ? Array.from({ length: PHOTOS_GUIDE(0) }, () => {
      const el = document.createElement('button'); el.type = 'button'; el.className = 'holo-guide-carte'; el.style.display = 'none';
      const photo = document.createElement('img'); photo.alt = ''; photo.loading = 'lazy'; photo.decoding = 'async'; photo.referrerPolicy = 'no-referrer';
      photo.onerror = () => { photo.style.visibility = 'hidden'; }; photo.onload = () => { photo.style.visibility = ''; };
      const nom = document.createElement('span'); el.append(photo, nom);
      el.addEventListener('click', ev => { ev.stopPropagation(); const i = Number(el.dataset.site); if (Number.isFinite(i)) choisirSite(i); });
      b.appendChild(el); return el;
    }) : [];
    etiquettesVilles = villes.length ? Array.from({ length: 140 }, () => { const el = etiquette('holo-etiquette holo-ville'); el.style.display = 'none'; return el; }) : [];
  };

  let tMeteo = performance.now();
  /**
   * Vent : chaque particule suit le vent MESURÉ (direction et force), en accéléré — 0,35° par seconde pour 1 m/s,
   * sinon rien ne bouge à l'œil. Traînée de 1 s, du jaune (fort) au cyan (calme). Pluie : des gouttes tombent là où
   * Open-Meteo mesure de la pluie. Mouvement réduit : vent et gouttes immobiles.
   */
  const animerMeteo = () => {
    const t = performance.now(), dt = reduit ? 0 : Math.min((t - tMeteo) / 1000, .1); tMeteo = t;
    const g = grille;
    if (g) {
      const ACCEL = .35;
      particules.forEach((p, i) => {
        p.age += dt; if (p.age > p.vie) renaitre(p);
        const [u, v] = ventEn(g, p.lat, p.lon), c = Math.max(Math.cos(p.lat * RAD), .15);
        p.lat = Math.max(-84, Math.min(84, p.lat + v * ACCEL * dt)); p.lon = ((p.lon + u * ACCEL * dt / c + 540) % 360) - 180;
        posVent.set(versXYZ(p.lat, p.lon, 1.008), i * 6);
        posVent.set(versXYZ(p.lat - v * ACCEL, p.lon - u * ACCEL / c, 1.008), i * 6 + 3);
        const fort = Math.min(Math.hypot(u, v) / 15, 1), fondu = Math.max(0, Math.min(p.age, p.vie - p.age, 1));
        colVent.set([.35 + .65 * fort, .95 - .2 * fort, 1 - .8 * fort, .95 * fondu, .35 + .65 * fort, .95 - .2 * fort, 1 - .8 * fort, 0], i * 8);
      });
      gVent.attributes.position.needsUpdate = true; gVent.attributes.color.needsUpdate = true;
    }
    gouttes.forEach((goutte, i) => {
      const cycle = reduit ? .5 : (t / 1000 * .7 + goutte.phase) % 1;
      posPluie.set(versXYZ(goutte.lat, goutte.lon, 1.004 + .02 * (1 - cycle)), i * 3);
      colPluie.set([.4, .7, 1, Math.sin(cycle * Math.PI)], i * 4);
    });
    gPluie.attributes.position.needsUpdate = true; gPluie.attributes.color.needsUpdate = true;
    const ph = reduit ? .4 : (t / 1000 % 1.6) / 1.6;
    matOnde.size = 10 + 26 * ph; matOnde.opacity = .75 * (1 - ph);
  };

  /** Une image : avions prolongés, satellites sur leur trace, étiquettes, surbrillance. */
  const animerCiel = () => {
    // Hauteur de la caméra au-dessus du sol : les tailles en dépendent.
    const maintenant = Date.now(), k = distance - 1;
    const n: Record<Categorie, number> = { ligne: 0, leger: 0, rapide: 0 };
    for (const v of vols) {
      const [la, lo] = prolonger(v.a, maintenant), f = Math.max(0, 1 - (maintenant - v.t0) / RATTRAPAGE), c = v.a.categorie ?? 'ligne';
      v.p = poser(avionsMeshes[c], n[c]++, la + v.corrLat * f, lo + v.corrLon * f, rayonAvion(v.a), v.a.cap ?? 0, TAILLE_AVION * k);
    }
    (Object.keys(avionsMeshes) as Categorie[]).forEach(c => { avionsMeshes[c].count = n[c]; avionsMeshes[c].instanceMatrix.needsUpdate = true; });
    // Trafic mondial, sauf dans la zone détaillée (ses avions y sont déjà, en modèles 3D).
    let nm = 0;
    if (traficMonde) {
      const s = Math.min(Math.max((maintenant - traficMonde.mesureA) / 1000, 0), PROLONGER_MONDE);
      const dLoZone = lieu ? demiCoteZone / Math.max(Math.cos(lieu.lat * RAD), .2) : 0;
      for (const [la, lo, cap, v, alt] of traficMonde.avions) {
        if (nm >= 1500) break;
        if (lieu && Math.abs(la - lieu.lat) < demiCoteZone && Math.abs(((lo - lieu.lon + 540) % 360) - 180) < dLoZone) continue;
        const d = v / 3.6 * s / 6371000 / RAD;
        poser(mondeMesh, nm++, la + d * Math.cos(cap * RAD), lo + d * Math.sin(cap * RAD) / Math.max(Math.cos(la * RAD), .2),
          1 + Math.max(alt, 0) / 6371000 * EXAGERATION_AVIONS + .004, cap, TAILLE_MONDE * k);
      }
    }
    mondeMesh.count = nm; mondeMesh.instanceMatrix.needsUpdate = true;
    // Noms des capitales : les 38 plus grandes de loin, toutes de près — jamais l'un sur l'autre : les plus grandes
    // d'abord, une étiquette qui en recouvre une déjà posée est cachée.
    const seuil = distance > 3 ? 1 : distance > 2 ? 3 : 9, poses: number[][] = [];
    for (const i of ordreCapitales) {
      const c = capitales[i], el = etiquettesCapitales[i]; if (!el) continue;
      const e = c.rang <= seuil ? versEcran(c.p, lCss, hCss) : null;
      const x = e ? e.x + 5 : 0, y = e ? e.y - 6 : 0, l = c.nom.length * 5.6 + 6;
      const libre = e && !poses.some(([a, b, la]) => x < a + la && a < x + l && Math.abs(y - b) < 12);
      el.style.display = libre ? '' : 'none';
      if (libre) { poses.push([x, y, l]); el.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`; }
    }
    // Villes : de plus en plus nombreuses en zoomant, les plus importantes d'abord, sans chevauchement.
    // Au-delà de 2,6, seules les capitales ont leur nom.
    if (routiere.visible && etiquettesVilles.length) {
      matRoutes.opacity = distance > 3 ? .35 : .85;
      const rangMax = distance > 2.6 ? -1 : distance > 2 ? 3 : distance > 1.5 ? 6 : 10;
      let u = 0;
      for (const v of villes) {
        if (u >= etiquettesVilles.length || v.r > rangMax) break; // triées par importance
        const e = versEcran(v.p, lCss, hCss); if (!e) continue;
        const x = e.x + 4, y = e.y - 5, l = v.n.length * 5.2 + 6;
        if (poses.some(([a, b, la]) => x < a + la && a < x + l && Math.abs(y - b) < 11)) continue;
        poses.push([x, y, l]);
        const el = etiquettesVilles[u++]; el.textContent = v.n; el.style.display = ''; el.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
      }
      for (; u < etiquettesVilles.length; u++) etiquettesVilles[u].style.display = 'none';
    }
    if (guideCouche.visible && cartesGuide.length) {
      const max = PHOTOS_GUIDE(distance), LARG = 92, HAUT = photosPermises ? 78 : 26;
      let u = 0;
      for (let i = 0; i < sitesGuide.length && u < max; i++) { // triés par notoriété
        const s = sitesGuide[i], e = versEcran(s.p, lCss, hCss); if (!e) continue;
        // Pas sous la barre d'icônes, en haut à gauche (Athéna, 12/10 : une photo la recouvrait).
        const x = Math.min(Math.max(e.x - LARG / 2, 0), lCss - LARG), y = Math.min(Math.max(e.y - HAUT - 6, x < reserveL ? reserveH : 0), hCss - HAUT);
        if (poses.some(([a, b, la]) => x < a + la && a < x + LARG && y < b + HAUT && b < y + 12)) continue;
        poses.push([x, y, LARG]); for (let kk = 12; kk < HAUT; kk += 12) poses.push([x, y + kk, LARG]);
        const el = cartesGuide[u++];
        if (el.dataset.site !== String(i)) {
          el.dataset.site = String(i); el.title = s.n; el.setAttribute('aria-label', s.n);
          const photo = el.firstChild as HTMLImageElement, nom = el.lastChild as HTMLSpanElement;
          nom.textContent = s.n; photo.style.visibility = 'hidden';
          el.classList.toggle('holo-sans-photo', !(photosPermises && s.img));
          if (photosPermises && s.img) photo.src = photoCommons(s.img, 160); else photo.removeAttribute('src');
        }
        el.style.display = ''; el.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
      }
      for (; u < cartesGuide.length; u++) cartesGuide[u].style.display = 'none';
    }
    if (meteoCouche.visible) animerMeteo();
    const ns = { satellite: 0, station: 0 };
    satsAnimes.forEach((x, i) => {
      const [la, lo, cap] = surTrace(x, maintenant), genre = STATIONS.includes(x.s.norad) ? 'station' : 'satellite';
      if (ns[genre] >= satsMeshes[genre].instanceMatrix.count) return;
      x.p = poser(satsMeshes[genre], ns[genre]++, la, lo, 1 + x.s.altitude / 6371, cap, TAILLE_SAT * k * (genre === 'station' ? 1.4 : 1));
      const el = etiquettesSats[i], e = el && volantsVisibles && versEcran(x.p, lCss, hCss);
      if (el) { el.style.display = e ? '' : 'none'; if (e) el.style.transform = `translate(${Math.round(e.x + 10)}px, ${Math.round(e.y - 10)}px)`; }
    });
    (['satellite', 'station'] as const).forEach(g => { satsMeshes[g].count = ns[g]; satsMeshes[g].instanceMatrix.needsUpdate = true; });
    const c = choisi;
    const p = !c ? null : c.genre === 'avion' ? vols.find(v => v.a.id === c.id)?.p : c.genre === 'sat' ? satsAnimes.find(x => x.s.norad === c.norad)?.p : c.p;
    surbrillance.visible = !!p;
    // Taille liée au zoom comme les modèles : fixe, elle devenait un grand carré blanc de près.
    matSurbrillance.size = TAILLE_AVION * k * 1.6;
    if (p) { gSurbrillance.attributes.position.setXYZ(0, p[0], p[1], p[2]); gSurbrillance.attributes.position.needsUpdate = true; }
  };

  const pointSurGlobe = (x: number, y: number) => {
    // Rayon depuis la caméra vers le point cliqué, contre la sphère de rayon 1.
    const ray = new THREE.Raycaster(); ray.setFromCamera(new THREE.Vector2(x / lCss * 2 - 1, 1 - y / hCss * 2), camera);
    const p = ray.ray.intersectSphere(new THREE.Sphere(new THREE.Vector3(), 1), new THREE.Vector3());
    if (!p) return null;
    terre.worldToLocal(p);
    return { lat: Math.asin(Math.max(-1, Math.min(1, p.y / p.length()))) / RAD, lon: Math.atan2(-p.z, p.x) / RAD };
  };

  const majAvions = (l: Lieu | null, liste: GlobeAircraft[], demiCote: number) => {
    if (l && (!lieu || l.lat !== lieu.lat || l.lon !== lieu.lon)) { centreLat = l.lat; centreLon = l.lon; distanceCible = Math.min(distanceCible, 1.9); tourneSeule = false; trajet = null; }
    lieu = l; demiCoteZone = demiCote;
    const maintenant = Date.now(), avant = new Map(vols.map(v => [v.a.id, v]));
    vols = liste.slice(0, MAX_AVIONS_AFFICHES).map(a => {
      const [la, lo] = prolonger(a, maintenant), v = avant.get(a.id);
      const neuf: Vol = { a, corrLat: 0, corrLon: 0, t0: maintenant, p: [0, 0, 0] };
      if (!v) return neuf;
      // Où il était affiché à l'instant, moins où la nouvelle mesure le place : l'écart à rattraper.
      const [la0, lo0] = prolonger(v.a, maintenant), f = Math.max(0, 1 - (maintenant - v.t0) / RATTRAPAGE);
      const dla = la0 + v.corrLat * f - la, dlo = ((lo0 + v.corrLon * f - lo + 540) % 360) - 180;
      return Math.abs(dla) + Math.abs(dlo) > 1 ? neuf : { ...neuf, corrLat: dla, corrLon: dlo };
    });
    const reg: number[] = [], vil: number[] = [];
    if (l) {
      // Le carré interrogé par le service, dessiné sur le sol.
      const dLo = demiCote / Math.max(Math.cos(l.lat * RAD), .2), N = 24;
      const bord = (la: number, lo: number) => reg.push(...versXYZ(la, lo, 1.003));
      for (let i = 0; i < N; i++) bord(l.lat - demiCote, l.lon - dLo + 2 * dLo * i / N);
      for (let i = 0; i < N; i++) bord(l.lat - demiCote + 2 * demiCote * i / N, l.lon + dLo);
      for (let i = 0; i < N; i++) bord(l.lat + demiCote, l.lon + dLo - 2 * dLo * i / N);
      for (let i = 0; i < N; i++) bord(l.lat + demiCote - 2 * demiCote * i / N, l.lon - dLo);
      vil.push(...versXYZ(l.lat, l.lon, 1.004));
    }
    gRegion.setAttribute('position', new THREE.Float32BufferAttribute(reg, 3));
    gVille.setAttribute('position', new THREE.Float32BufferAttribute(vil, 3));
  };

  const pilote: Pilote = {
    redimensionner(l, h, reserve) {
      if (l < 2 || h < 2) return;
      const premier = !dimensionne; dimensionne = true;
      lCss = l; hCss = h; reserveL = reserve.l; reserveH = reserve.h;
      rendu.setSize(l, h, false);
      camera.aspect = l / h; camera.updateProjectionMatrix();
      // Plus haut que large (téléphone) : la Terre entière doit tenir en largeur.
      const d = l < h ? Math.min(6, 3.9 * h / l) : 3.9;
      if (Math.abs(distanceCible - distanceDefaut) < 1e-3 && !trajet) {
        distanceCible = d;
        if (premier) { distance = d; camera.position.set(0, 0, d); camera.lookAt(0, 0, 0); }
      }
      distanceDefaut = d;
      if (premier) construireEtiquettes();
    },
    avions: majAvions,
    satellites(liste) {
      const maintenant = Date.now();
      satsAnimes = liste.slice(0, 32).map(s => ({ s, recuA: maintenant, p: [0, 0, 0] as V3 }));
      traces.children.slice().forEach(o => { (o as Trois.Line).geometry.dispose(); traces.remove(o); });
      for (const s of liste) {
        const r = 1 + s.altitude / 6371;
        const pts = s.trace.map(([la, lo]) => new THREE.Vector3(...versXYZ(la, lo, r)));
        if (pts.length > 1) traces.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), matTrace));
      }
      construireEtiquettes(); // textContent seulement : une étiquette par satellite
    },
    texture(url, liberer = false) {
      // Le dernier demandé gagne : l'image NASA arrivée après un retour à la Blue Marble ne la remplace pas.
      const n = ++seqTexture;
      new THREE.TextureLoader().load(url, tex => {
        if (liberer) URL.revokeObjectURL(url);
        if (annule || n !== seqTexture) { tex.dispose(); return; }
        tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
        textureActuelle?.dispose(); textureActuelle = tex;
        matFond.uniforms.jour.value = tex; fond.visible = true;
      }, undefined, () => { if (liberer) URL.revokeObjectURL(url); /* image absente : le globe reste en points */ });
    },
    choisir(x, y) {
      // Sur les positions AFFICHÉES (celles qui bougent), pas sur les mesures d'origine.
      let meilleur: Choix | null = null, dMin = 14;
      if (meteoCouche.visible) for (const ev of listeEv) { const e = versEcran(ev.p, lCss, hCss); if (e) { const d = Math.hypot(e.x - x, e.y - y); if (d < dMin) { dMin = d; meilleur = { genre: 'evenement', e: ev.e }; } } }
      if (!volantsVisibles) return meilleur;
      for (const s of satsAnimes) { const e = versEcran(s.p, lCss, hCss); if (e) { const d = Math.hypot(e.x - x, e.y - y); if (d < dMin) { dMin = d; meilleur = { genre: 'sat', s: s.s }; } } }
      for (const v of vols) { const e = versEcran(v.p, lCss, hCss); if (e) { const d = Math.hypot(e.x - x, e.y - y); if (d < dMin) { dMin = d; meilleur = { genre: 'avion', a: v.a }; } } }
      return meilleur;
    },
    monde(d) { traficMonde = d; },
    vent(g) {
      grille = g; gouttes = [];
      if (g) g.pluie.forEach((mm, k) => {
        if (!(mm >= .05)) return;
        const la = g.lat0 + Math.floor(k / g.nLon) * g.pas, lo = g.lon0 + (k % g.nLon) * g.pas;
        for (let n = Math.min(25, 4 + Math.round(mm * 40)); n > 0 && gouttes.length < N_PLUIE; n--)
          gouttes.push({ lat: la + (Math.random() - .5) * g.pas, lon: lo + (Math.random() - .5) * g.pas, phase: Math.random() });
      });
      gPluie.setDrawRange(0, gouttes.length);
    },
    evenements(liste) {
      listeEv = liste.filter(e => Number.isFinite(e.lat) && Number.isFinite(e.lon)).map(e => ({ e, p: versXYZ(e.lat, e.lon, 1.005) }));
      gEv.setAttribute('position', new THREE.Float32BufferAttribute(listeEv.flatMap(x => x.p), 3)); gEv.computeBoundingSphere();
    },
    carte(v) {
      carteActuelle = v && v.mode !== 'aucune' ? v : null;
      sphereCarte.visible = !!carteActuelle;
      const x = toile.getContext('2d');
      if (!carteActuelle || !x) return;
      const img = x.createImageData(360, 180), ech = ECHELLES[carteActuelle.mode as Exclude<typeof carteActuelle.mode, 'aucune'>].arrets;
      for (let py = 0; py < 180; py++) for (let px = 0; px < 360; px++) {
        const val = valeurGrille(carteActuelle, carteActuelle.valeurs, 89.5 - py, px - 179.5), [rr, gg, bb] = couleurEchelle(ech, val), o = (py * 360 + px) * 4;
        img.data[o] = rr; img.data[o + 1] = gg; img.data[o + 2] = bb; img.data[o + 3] = Number.isFinite(val) ? 255 : 0;
      }
      x.putImageData(img, 0, 0); texCarte.needsUpdate = true;
    },
    valeurEn(x, y) {
      if (!carteActuelle || !meteoCouche.visible) return null;
      const p = pointSurGlobe(x, y);
      return p ? { ...p, valeur: valeurGrille(carteActuelle, carteActuelle.valeurs, p.lat, p.lon) } : null;
    },
    couches(c) {
      meteoCouche.visible = c.meteo;
      routiere.visible = c.routes;
      if (c.routes) void chargerRoutiere();
      else etiquettesVilles.forEach(el => { el.style.display = 'none'; });
      for (const m of [mondeMesh, traces, ...Object.values(avionsMeshes), ...Object.values(satsMeshes)]) m.visible = c.volants;
      if (!c.volants) etiquettesSats.forEach(el => { el.style.display = 'none'; });
      volantsVisibles = c.volants;
    },
    pointSurGlobe,
    voirMonde() { majAvions(null, [], 0); distanceCible = distanceDefaut; focusFixe = false; trajet = null; repereCouche.visible = false; },
    actualites(points) {
      actuCouche.visible = !!points;
      const liste = (points ?? []).filter(x => Number.isFinite(x.lat) && Number.isFinite(x.lon));
      pointsActu = liste.map(x => ({ pays: x.pays, p: versXYZ(x.lat, x.lon, 1.006) }));
      const pos = (fort: boolean) => liste.filter(x => (x.n >= 4) === fort).flatMap(x => versXYZ(x.lat, x.lon, 1.006));
      gActu.setAttribute('position', new THREE.Float32BufferAttribute(pos(false), 3)); gActuFort.setAttribute('position', new THREE.Float32BufferAttribute(pos(true), 3));
    },
    choisirActu(x, y) {
      if (!actuCouche.visible) return null;
      let meilleur: string | null = null, dMin = 16;
      for (const a of pointsActu) { const e = versEcran(a.p, lCss, hCss); if (e) { const d = Math.hypot(e.x - x, e.y - y); if (d < dMin) { dMin = d; meilleur = a.pays; } } }
      return meilleur;
    },
    detail(permis) { detailPermis = permis; if (!permis) { detailBbox = null; detailMesh.visible = false; matDetail.uniforms.fondu.value = 0; } },
    guide(liste, choisir) {
      choisirSite = choisir;
      guideCouche.visible = !!liste;
      sitesGuide = (liste ?? []).map(x => ({ n: x.n, img: x.img, p: versXYZ(x.la, x.lo, 1.004) }));
      gGuide.setAttribute('position', new THREE.Float32BufferAttribute(sitesGuide.flatMap(x => x.p), 3));
      construireEtiquettes();
    },
    photos(permis) {
      if (permis === photosPermises) return;
      photosPermises = permis;
      cartesGuide.forEach(el => { el.dataset.site = ''; }); // à redessiner : avec ou sans photo
    },
    surligner(c) { choisi = !c ? null : c.genre === 'avion' ? { genre: 'avion', id: c.a.id } : c.genre === 'sat' ? { genre: 'sat', norad: c.s.norad } : { genre: 'evenement', p: versXYZ(c.e.lat, c.e.lon, 1.005) }; },
    zoom(dy) {
      distanceCible = Math.min(6, Math.max(1.12, distanceCible * Math.exp(dy * .0012)));
      dernierGeste = performance.now(); trajet = null;
      if (distanceCible < ZOOM_DETAIL) tourneSeule = false;
    },
    tourner(dx, dy) {
      // Plus on est près, plus le glisser est fin. Degrés par pixel.
      const k = .23 * (distance - 1);
      tourneSeule = false; focusFixe = false; trajet = null; dernierGeste = performance.now();
      centreLon = ((centreLon - dx * k + 540) % 360) - 180;
      centreLat = Math.max(-80, Math.min(80, centreLat + dy * k));
    },
    allerA(lat, lon, d) {
      // Sur le modèle de avions() : le centre visé vient face à la caméra, la distance diminue (1,9 pour une zone,
      // 2,2 par défaut : sous ZOOM_DETAIL, la carte détaillée sert). En douceur : un trajet de 0,9 à 2,4 s, qui
      // prend un peu de recul quand le lieu est loin. Mouvement réduit : on y est tout de suite.
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      const la1 = clamp(lat, -80, 80), lo1 = norm180(lon), d1 = d != null && Number.isFinite(d) ? clamp(d, 1.12, 6) : Math.min(distanceCible, 2.2);
      const ecart = angularDistanceDeg({ lat: centreLat, lon: centreLon }, { lat: la1, lon: lo1 });
      tourneSeule = false; focusFixe = true; dernierGeste = performance.now();
      if (reduit) { centreLat = la1; centreLon = lo1; distanceCible = d1; trajet = null; return; }
      trajet = { t0: performance.now(), duree: clamp(900 + ecart * 9, 900, 2400), la0: centreLat, lo0: centreLon, la1, dlo: norm180(lo1 - centreLon), d0: distance, d1, bosse: clamp(ecart / 120, 0, 1) * 1.2 };
    },
    repere(p) {
      repereCouche.visible = !!p;
      if (p && Number.isFinite(p.lat) && Number.isFinite(p.lon)) {
        const [x, y, z] = versXYZ(p.lat, p.lon, 1.007);
        gRepere.attributes.position.setXYZ(0, x, y, z); gRepere.attributes.position.needsUpdate = true; gRepere.computeBoundingSphere();
      }
    },
    recherche(on) { rechercheVoulue = on; },
    mouvementReduit(on) { reduit = on; if (on) tourneSeule = false; },
  };

  const horloge = new THREE.Clock();
  const angle = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));
  const boucle = () => {
    image = requestAnimationFrame(boucle);
    if (document.hidden) return;
    const dt = Math.min(horloge.getDelta(), .1), t = horloge.elapsedTime, maintenant = performance.now();
    if (trajet) {
      const e = easeInOutCubic(clamp((maintenant - trajet.t0) / trajet.duree, 0, 1));
      centreLat = trajet.la0 + (trajet.la1 - trajet.la0) * e; centreLon = norm180(trajet.lo0 + trajet.dlo * e);
      distanceCible = trajet.d0 + (trajet.d1 - trajet.d0) * e + trajet.bosse * Math.sin(Math.PI * e);
      if (e >= 1) trajet = null;
    }
    // Le centre choisi vient face à la caméra (rotation Y puis X). Arrêtée par un glisser, la Terre repart seule après
    // 15 s sans geste, sauf si une zone, un objet ou un lieu demandé à AURORA est choisi.
    if (!tourneSeule && !reduit && !lieu && !choisi && !focusFixe && distance >= ZOOM_DETAIL && maintenant - dernierGeste > 15000) tourneSeule = true;
    if (tourneSeule && !reduit) centreLon = ((centreLon - dt * 4 + 540) % 360) - 180;
    const ry = -Math.PI / 2 - centreLon * RAD, rx = centreLat * RAD, k = reduit ? 1 : Math.min(1, dt * 4);
    terre.rotation.y += angle(ry - terre.rotation.y) * k;
    terre.rotation.x += (rx - terre.rotation.x) * k;
    if (Math.abs(distanceCible - distance) > 1e-3) {
      distance += (distanceCible - distance) * (reduit ? 1 : Math.min(1, dt * 5));
      camera.position.set(0, 0, distance); camera.lookAt(0, 0, 0);
    }
    matLigne.opacity = reduit ? .25 : .22 + .06 * Math.sin(t * 3);
    // Anneaux et socle : visibles de loin, effacés en zoomant ; ils tournent et le socle respire.
    const loin = clamp((distance - 2.9) / .8, 0, 1);
    matDecor.opacity = matLigne.opacity * loin;
    for (const o of [...anneaux, ...socle]) o.visible = loin > .01;
    if (!reduit && loin > .01) {
      anneaux[0].rotation.z += dt * .3; anneaux[1].rotation.z -= dt * .2;
      socle.forEach((s, i) => { const e = 1 + .06 * Math.sin(t * 2 + i); s.scale.set(e, 1, e); });
    }
    if (actuCouche.visible) { const ph = reduit ? .4 : (t % 2) / 2; matActuOnde.size = 16 + 22 * ph; matActuOnde.opacity = .8 * (1 - ph); }
    if (repereCouche.visible) { const ph = reduit ? .4 : (t % 1.8) / 1.8; matRepereOnde.size = 14 + 30 * ph; matRepereOnde.opacity = .9 * (1 - ph); }
    majDetail(lCss / hCss);
    matPts.opacity = .45 * clamp((distance - 1.6) / .9, 0, 1);
    // La recherche : monte vite, s'éteint en douceur.
    forceRecherche += ((rechercheVoulue ? 1 : 0) - forceRecherche) * Math.min(1, dt * (rechercheVoulue ? 3 : 1.4));
    if (!rechercheVoulue && forceRecherche < .004) forceRecherche = 0;
    coque.visible = forceRecherche > 0;
    if (coque.visible) {
      tempsRecherche += dt;
      matRecherche.uniforms.temps.value = tempsRecherche; matRecherche.uniforms.force.value = forceRecherche; matRecherche.uniforms.mouvement.value = reduit ? 0 : 1;
    }
    matHalo.uniforms.vif.value = forceRecherche;
    terre.updateMatrixWorld(); animerCiel();
    rendu.render(scene, camera);
  };
  boucle();
  pilote.texture(IMAGE_LOCALE);

  return {
    pilote,
    liberer() {
      annule = true; cancelAnimationFrame(image);
      boite()?.replaceChildren();
      const textures = new Set<Trois.Texture>([disque, anneau, texCarte]);
      if (textureActuelle) textures.add(textureActuelle);
      if (texBase) textures.add(texBase);
      scene.traverse(o => {
        const m = o as Trois.Mesh;
        m.geometry?.dispose();
        if (o instanceof THREE.InstancedMesh) o.dispose();
        const mats = Array.isArray(m.material) ? m.material : m.material ? [m.material] : [];
        for (const x of mats) {
          for (const v of Object.values(x)) if (v instanceof THREE.Texture) textures.add(v);
          if (x instanceof THREE.ShaderMaterial) for (const u of Object.values(x.uniforms)) if (u.value instanceof THREE.Texture) textures.add(u.value);
          x.dispose();
        }
      });
      textures.forEach(tex => tex.dispose());
      rendu.dispose();
      rendu.forceContextLoss();
    },
  };
}
