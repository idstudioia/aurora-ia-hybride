// Les vues 3D de la fiche technique interactive d'un projet (15/11/2026). ID Studio IA : « fait la fiche technique interactive
// avec les model 3D et les animation quant on clique sur le cadre ». Le modèle du projet (un fichier GLB) est lu par le
// lecteur du laboratoire (labo/origine.ts : rien dans le fichier ne déclenche de requête, rien n'est exécuté). Un seul
// moteur de rendu sert toute la fiche : il fait l'image fixe de chaque cadre de vue, puis devient la vue vivante du
// cadre que l'on ouvre (le modèle tourne à la souris, s'éclate, joue l'animation du fichier).
// Honnêteté : une animation lue est celle du fichier ; la fiche d'une pièce est celle que le fichier écrit.
// Ce module n'est chargé que lorsqu'une fiche a un modèle.
import * as THREE from 'three';
import { lireOrigine } from '../components/labo/origine';
import type { PieceFichier } from '../components/labo/scene';
import type { FicheCadre } from '../lib/types';

/** L'image fixe d'un cadre de vue. `boite` : où le modèle tombe dans l'image (gauche, droite, haut, bas, en parts de
 *  sa largeur et de sa hauteur), pour y tracer les cotes. `reperes` : où poser les numéros de la légende. */
export interface ImageVue { adresse: string; boite: [number, number, number, number]; reperes: { n: number; x: number; y: number }[] }
/** La vue vivante d'un cadre ouvert. */
export interface VueVivante {
  eclater(on: boolean): void;
  /** Lit l'animation de ce rang du fichier ; null : arrêt. */
  jouer(rang: number | null): void;
  interieur(on: boolean): void;
  recentrer(): void;
  fermer(): void;
}
export interface AtelierVues {
  /** Le nom que le fichier donne à chacune de ses animations. */
  readonly animations: string[];
  /** Le fichier est fait de plusieurs pièces qui peuvent s'écarter ; il dit quelles pièces sont sa coque. */
  readonly pieces: boolean;
  readonly coque: boolean;
  image(cadre: FicheCadre, largeur: number, hauteur: number): ImageVue;
  /** Ouvre la vue vivante dans `conteneur` ; `surPiece` reçoit la fiche de la pièce cliquée (null : dans le vide). */
  ouvrir(conteneur: HTMLElement, cadre: FicheCadre, surPiece: (p: PieceFichier | null) => void): VueVivante;
  liberer(): void;
}

const RAD = Math.PI / 180;

/** Lit le modèle (ses octets) et prépare le moteur de rendu de la fiche. Une erreur dit pourquoi, en français. */
export async function creerAtelier(donnees: ArrayBuffer): Promise<AtelierVues> {
  // Posé comme dans le laboratoire : debout, centré, sur le sol, sa plus grande dimension à 1.
  const modele = await lireOrigine(donnees, { type: 'maillage', fichier: 'fiche-technique.glb', taille: 1, debout: 'y' }, 0);
  const toile = document.createElement('canvas');
  let rendu: THREE.WebGLRenderer;
  try {
    rendu = new THREE.WebGLRenderer({ canvas: toile, antialias: true, alpha: true, preserveDrawingBuffer: true });
  } catch {
    modele.liberer();
    throw new Error('la carte graphique n’a pas pu ouvrir de vue 3D');
  }
  rendu.setClearColor(0x000000, 0);
  const scene = new THREE.Scene();
  scene.add(modele.objet, modele.lumieres);
  modele.preparer(rendu);
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, .01, 100);
  const boite = new THREE.Box3(), centre = new THREE.Vector3(), taille = new THREE.Vector3(), point = new THREE.Vector3(), regard = new THREE.Vector3();
  let vivante: VueVivante | null = null;

  /** Pose la caméra à l'azimut et à la hauteur dits (0, 0 : de face), autour de `vers`, à `distance`. */
  const viser = (azimut: number, hauteur: number, vers: THREE.Vector3, distance: number) => {
    regard.set(Math.sin(azimut * RAD) * Math.cos(hauteur * RAD), Math.sin(hauteur * RAD), Math.cos(azimut * RAD) * Math.cos(hauteur * RAD));
    camera.position.copy(vers).addScaledVector(regard, distance);
    camera.up.set(0, 1, 0);
    camera.lookAt(vers);
    camera.updateMatrixWorld(true);
  };
  /** Mesure le modèle tel qu'il est (pièces écartées ou non) : sa boîte, son centre, son rayon. */
  const mesurer = (): number => {
    modele.objet.updateMatrixWorld(true);
    boite.setFromObject(modele.objet, true);
    boite.getCenter(centre);
    return boite.getSize(taille).length() / 2 || 1;
  };
  /** Met les pièces dans l'état dit, tout de suite, animation arrêtée. */
  const figer = (eclate: boolean) => { modele.jouer(null); modele.eclater?.(eclate); modele.avancer(60); };

  const image = (cadre: FicheCadre, largeur: number, hauteur: number): ImageVue => {
    figer(cadre.eclate);
    const rayon = mesurer(), ratio = largeur / hauteur;
    viser(cadre.azimut, cadre.hauteur, centre, rayon * 4);
    // La boîte du modèle vue par la caméra : ses huit coins. Pour une vue de face, de côté ou d'arrière, c'est exact.
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (let i = 0; i < 8; i++) {
      point.set(i & 1 ? boite.max.x : boite.min.x, i & 2 ? boite.max.y : boite.min.y, i & 4 ? boite.max.z : boite.min.z).applyMatrix4(camera.matrixWorldInverse);
      x0 = Math.min(x0, point.x); x1 = Math.max(x1, point.x); y0 = Math.min(y0, point.y); y1 = Math.max(y1, point.y);
    }
    const demiH = Math.max(y1 - y0, (x1 - x0) / ratio) * 1.16 / 2, demiL = demiH * ratio, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    camera.left = cx - demiL; camera.right = cx + demiL; camera.top = cy + demiH; camera.bottom = cy - demiH;
    camera.near = .01; camera.far = rayon * 10;
    camera.updateProjectionMatrix();
    rendu.setPixelRatio(1);
    rendu.setSize(Math.max(2, Math.round(largeur)), Math.max(2, Math.round(hauteur)), false);
    rendu.render(scene, camera);
    // Les numéros de la légende : sur le point de chaque pièce le plus proche de la caméra (un point que l'on voit,
    // même pour un anneau ou deux panneaux, dont le milieu est dans le vide). Deux numéros ne se couvrent pas.
    const reperes: ImageVue['reperes'] = [];
    for (const r of cadre.reperes) {
      let pres = -Infinity, trouve = false;
      const ici = new THREE.Vector3();
      modele.objet.traverse(o => {
        const nom = (o.userData as { aurora?: { nom?: unknown } }).aurora?.nom;
        if (typeof nom !== 'string' || !nom.toLowerCase().startsWith(r.piece.toLowerCase())) return;
        o.traverse(m => {
          const positions = (m as THREE.Mesh).geometry?.getAttribute('position');
          if (!positions) return;
          for (let i = 0, pas = Math.max(1, Math.floor(positions.count / 3000)); i < positions.count; i += pas) {
            point.fromBufferAttribute(positions, i).applyMatrix4(m.matrixWorld).applyMatrix4(camera.matrixWorldInverse);
            if (point.z > pres) { pres = point.z; ici.copy(point); trouve = true; }
          }
        });
      });
      if (!trouve) continue;
      ici.applyMatrix4(camera.projectionMatrix);
      let x = (ici.x + 1) / 2;
      const y = (1 - ici.y) / 2;
      while (reperes.some(p => Math.abs(p.x - x) < .045 && Math.abs(p.y - y) < .045)) x += .05;
      reperes.push({ n: r.n, x, y });
    }
    return { adresse: toile.toDataURL('image/png'), boite: [(x0 - camera.left) / (2 * demiL), (x1 - camera.left) / (2 * demiL), (camera.top - y1) / (2 * demiH), (camera.top - y0) / (2 * demiH)], reperes };
  };

  const ouvrir = (conteneur: HTMLElement, cadre: FicheCadre, surPiece: (p: PieceFichier | null) => void): VueVivante => {
    vivante?.fermer();
    // Le rayon du modèle assemblé puis éclaté : le cadrage suit l'état des pièces sans trembler pendant une animation.
    figer(true);
    const rayonEclate = mesurer();
    figer(false);
    const rayonAssemble = mesurer(), autour = centre.clone();
    modele.eclater?.(cadre.eclate);
    modele.avancer(60);
    let azimut = cadre.azimut, hauteur = cadre.hauteur, zoom = 1, eclate = cadre.eclate, demi = eclate ? rayonEclate : rayonAssemble;
    let actif = true, sale = true, image0 = 0, dernier = performance.now(), ratio = 1;
    conteneur.appendChild(toile);
    toile.style.width = '100%'; toile.style.height = '100%'; toile.style.display = 'block'; toile.style.touchAction = 'none'; toile.style.cursor = 'grab';
    const dimensionner = () => {
      const r = conteneur.getBoundingClientRect(), l = Math.max(2, Math.round(r.width)), h = Math.max(2, Math.round(r.height));
      rendu.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
      rendu.setSize(l, h, false);
      ratio = l / h; sale = true;
    };
    const dessiner = () => {
      viser(azimut, hauteur, autour, rayonEclate * 4);
      const demiH = demi * 1.1 / zoom / Math.min(1, ratio), demiL = demiH * ratio;
      camera.left = -demiL; camera.right = demiL; camera.top = demiH; camera.bottom = -demiH;
      camera.near = .01; camera.far = rayonEclate * 10;
      camera.updateProjectionMatrix();
      rendu.render(scene, camera);
    };
    const boucle = (t: number) => {
      if (!actif) return;
      const dt = Math.min(.1, Math.max(0, (t - dernier) / 1000));
      dernier = t;
      if (modele.avancer(dt)) sale = true;
      const cible = eclate ? rayonEclate : rayonAssemble;
      if (Math.abs(cible - demi) > 1e-4) { demi += (cible - demi) * (1 - Math.exp(-dt * 3.2)); sale = true; }
      if (sale) { sale = false; dessiner(); }
      image0 = requestAnimationFrame(boucle);
    };
    // La souris : glisser fait tourner, la molette approche, un clic sans glisser désigne une pièce.
    let depart: { x: number; y: number; bouge: boolean } | null = null;
    const appui = (e: PointerEvent) => { depart = { x: e.clientX, y: e.clientY, bouge: false }; toile.setPointerCapture(e.pointerId); toile.style.cursor = 'grabbing'; };
    const glisse = (e: PointerEvent) => {
      if (!depart) return;
      const dx = e.clientX - depart.x, dy = e.clientY - depart.y;
      if (!depart.bouge && Math.hypot(dx, dy) < 4) return;
      depart = { x: e.clientX, y: e.clientY, bouge: true };
      azimut -= dx * .4; hauteur = Math.max(-85, Math.min(85, hauteur + dy * .3)); sale = true;
    };
    const lache = (e: PointerEvent) => {
      toile.style.cursor = 'grab';
      const clic = depart && !depart.bouge;
      depart = null;
      if (!clic || !modele.choisir) return;
      const r = toile.getBoundingClientRect(), rayon = new THREE.Raycaster();
      dessiner();
      rayon.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, 1 - (e.clientY - r.top) / r.height * 2), camera);
      surPiece(modele.choisir(rayon));
    };
    const molette = (e: WheelEvent) => { e.preventDefault(); zoom = Math.max(.6, Math.min(8, zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12))); sale = true; };
    toile.addEventListener('pointerdown', appui); toile.addEventListener('pointermove', glisse); toile.addEventListener('pointerup', lache);
    toile.addEventListener('wheel', molette, { passive: false });
    const veille = new ResizeObserver(dimensionner);
    veille.observe(conteneur);
    dimensionner();
    image0 = requestAnimationFrame(boucle);
    const vue: VueVivante = {
      eclater(on) { eclate = on; modele.eclater?.(on); sale = true; },
      jouer(rang) { modele.jouer(rang); sale = true; },
      interieur(on) { modele.interieur?.(on); sale = true; },
      recentrer() { azimut = cadre.azimut; hauteur = cadre.hauteur; zoom = 1; sale = true; },
      fermer() {
        if (!actif) return;
        actif = false;
        cancelAnimationFrame(image0);
        veille.disconnect();
        toile.removeEventListener('pointerdown', appui); toile.removeEventListener('pointermove', glisse); toile.removeEventListener('pointerup', lache);
        toile.removeEventListener('wheel', molette);
        modele.interieur?.(false);
        figer(false);
        toile.remove();
        if (vivante === vue) vivante = null;
      },
    };
    vivante = vue;
    return vue;
  };

  return {
    animations: modele.contenu.animations, pieces: (modele.contenu.pieces ?? 0) > 1, coque: !!modele.contenu.coque,
    image, ouvrir,
    liberer() {
      vivante?.fermer();
      modele.liberer();
      rendu.dispose();
      rendu.forceContextLoss();
    },
  };
}
