// Aides des tests de la carte des outils et des anneaux de la mémoire (tests/anneaux.test.mjs, tests/carteOutils.test.mjs).
// Rien ici n'ouvre un navigateur ni un serveur : on charge les fichiers .ts du dessin dans Node, et on remplace le
// canvas par un enregistreur qui note chaque ordre de dessin.
import { register } from 'node:module';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Les fichiers de src s'importent entre eux sans extension (« ./model »), ce que Vite accepte mais pas Node, et
// certains sont en .tsx : tests/crochets.mjs s'en charge. Les imports « import type » sont retirés par Node.
register('./crochets.mjs', import.meta.url);

const ICI = dirname(fileURLToPath(import.meta.url));
export const FRONTEND = join(ICI, '..');
const DEPOT = join(FRONTEND, '..');

/** Le code « d'avant » : la branche main du 11/11/2026, juste avant la carte des outils (commit fea03d3).
 *  AURORA_REF_AVANT permet de comparer à une autre version (par exemple « main »). */
export const REF_AVANT = process.env.AURORA_REF_AVANT || 'fea03d316ba5763979beef558b72dd26a9d50af7';

/** Charge un fichier de src (version de travail). */
export const charger = chemin => import(pathToFileURL(join(FRONTEND, 'src', chemin)).href);

/**
 * Sort des fichiers de src tels qu'ils étaient dans REF_AVANT (git show), dans un dossier temporaire qui garde
 * leur arborescence. Rend { charger(chemin), ranger() }. Lève une erreur claire si git ou la version manque.
 */
export function versionAvant(chemins) {
  const dossier = mkdtempSync(join(tmpdir(), 'aurora-avant-'));
  writeFileSync(join(dossier, 'package.json'), '{ "type": "module" }\n', 'utf8');   // comme frontend/package.json
  for (const chemin of chemins) {
    const texte = execFileSync('git', ['show', `${REF_AVANT}:frontend/src/${chemin}`], { cwd: DEPOT, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
    const cible = join(dossier, 'src', chemin);
    mkdirSync(dirname(cible), { recursive: true });
    writeFileSync(cible, texte, 'utf8');
  }
  return {
    dossier,
    charger: chemin => import(pathToFileURL(join(dossier, 'src', chemin)).href),
    ranger: () => rmSync(dossier, { recursive: true, force: true }),
  };
}

/** La vraie réponse de la route (45 outils, 7 grandes familles), produite hors serveur par capabilities.tool_map sur
 *  une base temporaire. Seuls les nombres d'utilisations sont inventés, pour avoir des hexagones de tailles variées. */
export const lireCarte = () => JSON.parse(readFileSync(join(ICI, 'data', 'carte-outils.json'), 'utf8'));

/** Hasard reproductible (mulberry32) : les mêmes jeux de données à chaque lancement. */
export function hasard(graine) {
  let a = graine >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function melanger(liste, alea) {
  const copie = [...liste];
  for (let i = copie.length - 1; i > 0; i--) {
    const j = Math.floor(alea() * (i + 1));
    [copie[i], copie[j]] = [copie[j], copie[i]];
  }
  return copie;
}

// ---------------------------------------------------------------- canvas enregistreur

class Chemin {   // remplace Path2D
  constructor(d) { this.ordres = d === undefined ? [] : [['d', d]]; }
  moveTo(...a) { this.ordres.push(['moveTo', ...a]); }
  lineTo(...a) { this.ordres.push(['lineTo', ...a]); }
  arc(...a) { this.ordres.push(['arc', ...a]); }
  closePath() { this.ordres.push(['closePath']); }
}

class Degrade {
  constructor(sorte, args) { this.sorte = sorte; this.args = args; this.arrets = []; }
  addColorStop(o, c) { this.arrets.push([o, c]); }
}

class Lueur {   // le petit canvas d'une lueur (render.ts, glow) : reconnu par ce qu'on y a peint
  constructor() { this.width = 0; this.height = 0; this.peint = []; }
  getContext() {
    const lueur = this;
    return {
      createRadialGradient: (...a) => new Degrade('radial', a),
      set fillStyle(v) { lueur.peint.push(decrire(v)); },
      fillRect: (...a) => { lueur.peint.push(['fillRect', ...a]); },
    };
  }
}

function decrire(v) {
  if (v instanceof Chemin) return { chemin: v.ordres };
  if (v instanceof Degrade) return { degrade: v.sorte, args: v.args, arrets: v.arrets };
  if (v instanceof Lueur) return { lueur: v.peint };
  if (Array.isArray(v)) return v.map(decrire);
  return v;
}

/** Installe les deux objets du navigateur dont le dessin a besoin (document.createElement('canvas'), Path2D). */
export function fauxNavigateur() {
  globalThis.Path2D = Chemin;
  globalThis.document = {
    hidden: false,
    addEventListener() {},
    createElement(nom) {
      if (nom !== 'canvas') throw new Error(`élément inattendu : ${nom}`);
      return new Lueur();
    },
  };
}

/**
 * Un faux contexte 2D : chaque appel et chaque réglage est noté dans `journal`, dans l'ordre, avec ses nombres
 * exacts. Deux dessins sont « les mêmes » quand leurs journaux sont identiques. measureText rend une largeur
 * calculée (6,5 px par caractère, ou `largeurLettre`), la même pour les deux versions comparées.
 */
export function fauxContexte(largeurLettre = 6.5) {
  const journal = [];
  const etat = new Map();
  const ctx = new Proxy({}, {
    get(_cible, nom) {
      if (typeof nom !== 'string') return undefined;
      if (etat.has(nom)) return etat.get(nom);
      if (nom === 'measureText') return texte => { journal.push(['measureText', texte]); return { width: [...String(texte)].length * largeurLettre }; };
      if (nom === 'createLinearGradient') return (...a) => new Degrade('lineaire', a);
      if (nom === 'createRadialGradient') return (...a) => new Degrade('radial', a);
      return (...args) => { journal.push([nom, ...args.map(decrire)]); };
    },
    set(_cible, nom, valeur) {
      etat.set(nom, valeur);
      journal.push([`${String(nom)} =`, decrire(valeur)]);
      return true;
    },
  });
  return { ctx, journal };
}

/** Les textes écrits sur le canvas (fillText), dans l'ordre. Les textes courbés sont écrits lettre par lettre. */
export const textesEcrits = journal => journal.filter(o => o[0] === 'fillText').map(o => o[1]);
