// La page Mémoire n'a pas bougé (11/11/2026). La carte des outils de la page Tâches élargit les fichiers du cerveau
// (brain/rings.ts, brain/render.ts) derrière une propriété facultative. Ce test charge ces fichiers deux fois : tels
// qu'ils étaient sur la branche main avant la carte (git show, commit fea03d3) et tels qu'ils sont maintenant. SANS
// la nouvelle propriété, il compare chiffre à chiffre : les places calculées, les places à l'écran, et la suite
// complète des ordres de dessin envoyés au canvas. Hors navigateur, sans serveur.
// Lancer depuis frontend : node --experimental-strip-types --test tests/anneaux.test.mjs
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { REF_AVANT, charger, fauxContexte, fauxNavigateur, hasard, lireCarte, versionAvant } from './aide-carte.mjs';

fauxNavigateur();

const FICHIERS = [
  'components/brain/rings.ts', 'components/brain/sim.ts', 'components/brain/model.ts', 'components/brain/render.ts',
  'components/brain/effects.ts', 'lib/format.ts', 'components/chat/text.ts', 'lib/tolerant.ts',
];
const avant = versionAvant(FICHIERS);
after(() => avant.ranger());

const versions = {
  avant: {
    rings: await avant.charger('components/brain/rings.ts'), sim: await avant.charger('components/brain/sim.ts'),
    model: await avant.charger('components/brain/model.ts'), render: await avant.charger('components/brain/render.ts'),
    effects: await avant.charger('components/brain/effects.ts'),
  },
  maintenant: {
    rings: await charger('components/brain/rings.ts'), sim: await charger('components/brain/sim.ts'),
    model: await charger('components/brain/model.ts'), render: await charger('components/brain/render.ts'),
    effects: await charger('components/brain/effects.ts'),
  },
};

// ---------------------------------------------------------------- jeux de données (reproductibles)

/** Les 45 vrais outils, avec seulement les 4 champs que la page Mémoire reçoit (/api/memories/graph). */
const OUTILS = lireCarte().tools.map(t => ({ name: t.name, title: t.title, uses: t.uses ?? 0, last_used_at: t.last_used_at }));
/** Les mêmes, avec en plus les champs de la carte des outils (famille, état…) : ils ne doivent rien déplacer ici. */
const OUTILS_ENRICHIS = lireCarte().tools.map(t => ({
  name: t.name, title: t.title, uses: t.uses ?? 0, last_used_at: t.last_used_at,
  group: t.group, family: t.family, state: t.state, reason: t.reason, counted: true,
}));
const ROUTINES = [
  { id: 1, name: 'Point du matin', steps: 3, runs: 41, last_run_at: '2026-11-11T07:00:00.000Z' },
  { id: 2, name: 'Veille espace', steps: 2, runs: 6, last_run_at: '2026-11-09T19:00:00.000Z' },
  { id: 3, name: 'Ménage du disque', steps: 4, runs: 0, last_run_at: null },
  { id: 7, name: 'Bilan de la semaine', steps: 5, runs: 130, last_run_at: '2026-11-10T18:00:00.000Z' },
];
const CATEGORIES = {
  immediate: ['conversations'],
  persistent: ['general', 'projets', 'preferences', 'famille', 'autre'],
  semantic: ['general', 'science', 'espace'],
  actions: ['weather.current', 'web.search', 'chat.general', 'system.diagnose', 'time.now'],
};
const STATUTS = ['done', 'done', 'done', 'error', 'running', 'cancelled'];
const VERIFS = ['unverified', 'verified', 'user_confirmed', 'contradicted'];

/** Un graphe de mémoire de la forme de /api/memories/graph : n points, des liens, les vrais comptes par catégorie. */
function graphe(n, graine, outils = OUTILS) {
  const alea = hasard(graine);
  const nodes = [{ id: 'core', type: 'core', label: 'AURORA' }];
  for (const niveau of Object.keys(CATEGORIES)) nodes.push({ id: `level:${niveau}`, type: 'level', label: niveau, level: niveau });
  const groupes = new Map();
  const souvenirs = [];
  for (let i = 1; i <= n; i++) {
    const tirage = alea();
    const niveau = tirage < 0.12 ? 'immediate' : tirage < 0.55 ? 'persistent' : tirage < 0.7 ? 'semantic' : 'actions';
    const cats = CATEGORIES[niveau];
    const categorie = cats[Math.floor(alea() * cats.length)];
    const date = new Date(Date.UTC(2026, 9, 1) + Math.floor(alea() * 40 * 86_400_000)).toISOString();
    let noeud;
    if (niveau === 'immediate') noeud = { id: `c:${i}`, type: 'conversation', label: `Conversation ${i}`, level: niveau, created_at: date };
    else if (niveau === 'actions') noeud = { id: `r:${i}`, type: 'run', label: `Demande ${i}`, category: categorie, level: niveau, created_at: date, status: STATUTS[Math.floor(alea() * STATUTS.length)] };
    else {
      noeud = {
        id: `m:${i}`, type: 'memory', label: `Souvenir ${i}`, category: categorie, kind: 'fact', level: niveau, weight: alea(),
        created_at: date, pinned: alea() < 0.1, verification: VERIFS[Math.floor(alea() * VERIFS.length)],
      };
      souvenirs.push(noeud.id);
    }
    nodes.push(noeud);
    const cle = `${niveau}|${niveau === 'immediate' ? 'conversations' : categorie}`;
    const g = groupes.get(cle) ?? { level: niveau, category: cle.split('|')[1], count: 0, shown: 0 };
    g.count++; g.shown++;
    groupes.set(cle, g);
  }
  // La base en contient plus que ce qui est envoyé, et une catégorie n'a aucun point envoyé.
  for (const g of groupes.values()) if (alea() < 0.4) g.count += Math.floor(alea() * 60);
  groupes.set('persistent|archives', { level: 'persistent', category: 'archives', count: 17, shown: 0 });
  const links = [];
  for (let i = 0; i < Math.min(40, souvenirs.length - 1); i++) {
    const a = souvenirs[Math.floor(alea() * souvenirs.length)];
    const b = souvenirs[Math.floor(alea() * souvenirs.length)];
    if (a !== b) links.push({ source: a, target: b, relation: i % 9 === 0 ? 'contradicts' : 'related', weight: alea() });
  }
  const groups = [...groupes.values()];
  const totals = { immediate: 0, persistent: 0, semantic: 0, actions: 0 };
  for (const g of groups) totals[g.level] += g.count;
  return { nodes, links, groups, totals, tools: outils, routines: ROUTINES, limit: 1000 };
}

/** Ce que RingSim donne à ringLayout pour un graphe (rings.ts, layoutAnchors). */
function entree(rings, model, g) {
  const items = [];
  for (const n of g.nodes) {
    if (n.type === 'core' || n.type === 'level') continue;
    items.push({
      id: n.id, level: model.nodeLevel(n), category: rings.ringCategory(n.type, n.category),
      weight: typeof n.weight === 'number' ? n.weight : 0.5, order: Date.parse(n.created_at),
    });
  }
  return { items, groups: g.groups, tools: g.tools, routines: g.routines };
}

const aPlat = geo => ({ ...geo, slots: [...geo.slots.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)) });

const JEUX = [
  ['600 points', graphe(600, 11)],
  ['1 300 points (anneaux pleins)', graphe(1300, 29)],
  ['25 points', graphe(25, 5)],
  ['aucun point, les outils seuls', { nodes: [], links: [], groups: [], tools: OUTILS, routines: [] }],
  ['rien du tout', { nodes: [], links: [], groups: [], tools: [], routines: [] }],
];

// ---------------------------------------------------------------- les places calculées

test(`les places calculées sont celles de la branche main (${REF_AVANT.slice(0, 7)}), chiffre à chiffre`, () => {
  let outilsCompares = 0;
  for (const [nom, g] of JEUX) {
    const a = versions.avant.rings.ringLayout(entree(versions.avant.rings, versions.avant.model, g));
    const b = versions.maintenant.rings.ringLayout(entree(versions.maintenant.rings, versions.maintenant.model, g));
    assert.equal(b.toolSectors, undefined, `${nom} : sans la nouvelle propriété, pas de secteur d'outils`);
    assert.deepStrictEqual(aPlat(b), aPlat(a), nom);
    // L'anneau des outils, outil par outil : rayon, angle, taille, strictement égaux.
    for (const t of g.tools) {
      const id = versions.maintenant.rings.toolNodeId(t.name);
      const pa = a.slots.get(id);
      const pb = b.slots.get(id);
      assert.ok(pa && pb, `${nom} : ${t.name} a une place`);
      for (const champ of ['ring', 'r', 'a', 'size']) assert.ok(Object.is(pb[champ], pa[champ]), `${nom} : ${t.name}.${champ} ${pb[champ]} ≠ ${pa[champ]}`);
      outilsCompares++;
    }
  }
  assert.equal(outilsCompares, 45 * 4, 'les 45 outils comparés dans chacun des 4 jeux qui en ont');
});

test('les champs en plus d’un outil (famille, état…) ne déplacent rien sans la nouvelle propriété', () => {
  const g = graphe(600, 11);
  const a = versions.avant.rings.ringLayout(entree(versions.avant.rings, versions.avant.model, g));
  const b = versions.maintenant.rings.ringLayout(entree(versions.maintenant.rings, versions.maintenant.model, { ...g, tools: OUTILS_ENRICHIS }));
  assert.deepStrictEqual(aPlat(b), aPlat(a));
});

// ---------------------------------------------------------------- les places à l'écran (RingSim)

/** Fait vivre une simulation en anneaux comme le fait BrainGraph : données, mise en place, puis 3 s de rotation. */
function vivre(v, g, { rotation }) {
  const sim = new v.rings.RingSim();
  sim.sync(g, 1000, false);
  sim.settle(160);
  sim.rotating = rotation;
  for (let t = 1000; t <= 4000; t += 16.7) { sim.tick(t); sim.prune(t, false); }
  return sim;
}
const places = sim => ({
  noeuds: sim.nodes.map(n => [n.id, n.type, n.x, n.y, n.r, n.ax, n.ay]),
  extras: sim.extras.map(n => [n.id, n.type, n.label, n.x, n.y, n.r]),
  rot: { ...sim.rot },
  bornes: sim.bounds(),
});

test('RingSim : mêmes places à l’écran qu’avant, à l’arrêt et en rotation', () => {
  for (const [nom, g] of JEUX) {
    for (const rotation of [false, true]) {
      const a = vivre(versions.avant, g, { rotation });
      const b = vivre(versions.maintenant, g, { rotation });
      assert.equal(b.toolMap, false, 'sans argument, RingSim reste les anneaux de la mémoire');
      assert.deepStrictEqual(places(b), places(a), `${nom}, rotation ${rotation}`);
    }
  }
});

test('RingSim : le survol trouve les mêmes points qu’avant (outils, moyeux, cœur, vide)', () => {
  const g = graphe(600, 11);
  const a = vivre(versions.avant, g, { rotation: false });
  const b = vivre(versions.maintenant, g, { rotation: false });
  const alea = hasard(3);
  const points = [[0, 0], [5, -3], ...b.extras.map(n => [n.x + 1, n.y - 1]), ...b.nodes.slice(0, 60).map(n => [n.x, n.y])];
  for (let i = 0; i < 400; i++) points.push([(alea() - 0.5) * 900, (alea() - 0.5) * 900]);
  let trouves = 0;
  for (const [x, y] of points) {
    const na = a.hit(x, y, 9, 5000);
    const nb = b.hit(x, y, 9, 5000);
    assert.equal(nb?.id ?? null, na?.id ?? null, `point (${x}, ${y})`);
    if (nb) trouves++;
  }
  assert.ok(trouves > 100, `le survol a bien trouvé des points (${trouves})`);
  assert.equal(b.hit(0, 0, 9, 5000)?.type, 'core');
});

// ---------------------------------------------------------------- le dessin (ordres envoyés au canvas)

const evenement = (kind, payload, extra = {}) => ({
  id: 1, kind, subject_type: 'step', subject_id: 3, run_id: null, label: '', payload, created_at: '2026-11-11T10:00:00.000Z', ...extra,
});

/** Dessine une image comme BrainGraph (drawScene) et rend le journal des ordres reçus par le canvas. */
function dessiner(v, sim, { anneaux, k = 0.75, hoverId = null, selectedId = null, highlight = null, compact = false, reduced = false, evenements = [] }) {
  const effets = [];
  for (const e of evenements) {
    const classe = v.model.classifyEvent(e);
    if (classe) effets.push(...v.effects.planEffects(classe, sim, { now: 5000, speed: 1, reduced, live: true }).effects);
  }
  const counts = { immediate: 0, persistent: 0, semantic: 0, actions: 0 };
  for (const n of sim.nodes) if (!n.fixed && !n.hidden && !n.dyingAt && n.level) counts[n.level]++;
  const { ctx, journal } = fauxContexte();
  v.render.drawScene(ctx, {
    sim, effects: effets, now: 5700, w: 1100, h: 650, cam: { k, x: 12, y: -8 }, selectedId, hoverId, highlight,
    statusOf: n => n.status,
    metaOf: n => (n.num !== null && typeof n.raw?.pinned === 'boolean' ? { pinned: n.raw.pinned, verification: n.raw.verification } : null),
    counts, reduced, rings: anneaux ? sim : null, compact,
  });
  return journal;
}

const EVENEMENTS = [
  evenement('tool.result', { tool: 'time.now', ok: true }),
  evenement('tool.result', { tool: 'web.search', ok: false }),
  evenement('route.decided', { intent: 'routine.run', slots: { routine_id: 7 } }, { subject_type: 'task' }),
  evenement('memory.recalled', { memory_ids: [3, 8, 12, 20] }, { subject_type: 'memory' }),
];

test('anneaux de la mémoire : le canvas reçoit exactement les mêmes ordres de dessin qu’avant', () => {
  const g = graphe(600, 11);
  const premierSouvenir = g.nodes.find(n => n.type === 'memory').id;
  const cas = [
    ['vue d’ensemble', {}],
    ['zoom fort (tous les titres)', { k: 2.6 }],
    ['vue très éloignée', { k: 0.3 }],
    ['un outil survolé, un souvenir choisi', { hoverId: 'tool:web.search', selectedId: premierSouvenir }],
    ['une routine survolée', { hoverId: 'routine:7' }],
    ['recherche en cours', { highlight: new Map(g.nodes.filter(n => n.type === 'memory').slice(0, 12).map((n, i) => [n.id, 1 - i / 12])) }],
    ['outils et routines en service', { evenements: EVENEMENTS }],
    ['animations réduites', { evenements: EVENEMENTS, reduced: true }],
    ['vignette de l’accueil', { compact: true, k: 0.2 }],
  ];
  let ordres = 0;
  for (const [nom, options] of cas) {
    const ja = dessiner(versions.avant, vivre(versions.avant, g, { rotation: true }), { anneaux: true, ...options });
    const jb = dessiner(versions.maintenant, vivre(versions.maintenant, g, { rotation: true }), { anneaux: true, ...options });
    assert.ok(ja.length > 500, `${nom} : le dessin d’avant a bien produit des ordres (${ja.length})`);
    assert.equal(jb.length, ja.length, `${nom} : même nombre d’ordres`);
    assert.deepStrictEqual(jb, ja, nom);
    ordres += jb.length;
  }
  assert.ok(ordres > 20_000, `ordres de dessin comparés : ${ordres}`);
});

test('anneaux : les outils seuls, sans la nouvelle propriété, se dessinent comme avant', () => {
  const g = { nodes: [], links: [], groups: [], tools: OUTILS, routines: [] };
  const ja = dessiner(versions.avant, vivre(versions.avant, g, { rotation: false }), { anneaux: true, hoverId: 'tool:time.now', evenements: EVENEMENTS });
  const jb = dessiner(versions.maintenant, vivre(versions.maintenant, g, { rotation: false }), { anneaux: true, hoverId: 'tool:time.now', evenements: EVENEMENTS });
  assert.deepStrictEqual(jb, ja);
});

test('vue en lobes : le canvas reçoit exactement les mêmes ordres de dessin qu’avant', () => {
  const g = graphe(120, 7);
  const lobes = v => { const sim = new v.sim.BrainSim(); sim.sync(g, 1000, false); sim.settle(160); return sim; };
  for (const options of [{}, { k: 1.6, selectedId: g.nodes.find(n => n.type === 'memory').id }, { evenements: EVENEMENTS }]) {
    const ja = dessiner(versions.avant, lobes(versions.avant), { anneaux: false, ...options });
    const jb = dessiner(versions.maintenant, lobes(versions.maintenant), { anneaux: false, ...options });
    assert.ok(ja.length > 300);
    assert.deepStrictEqual(jb, ja);
  }
});
