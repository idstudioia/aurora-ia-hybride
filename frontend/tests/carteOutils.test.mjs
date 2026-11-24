// Carte des outils de la page Tâches (11/11/2026) : la nouvelle disposition (brain/rings.ts, toolMapLayout), son
// dessin (brain/render.ts) et ses textes (brain/toolMap.ts). Hors navigateur, sans serveur.
// Le jeu de données est la vraie réponse de GET /api/tools/map (tests/data/carte-outils.json : 45 outils, 7 grandes
// familles), produite par capabilities.tool_map sur une base temporaire ; seuls les nombres d'utilisations sont inventés.
// Lancer depuis frontend : node --experimental-strip-types --test tests/carteOutils.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { charger, fauxContexte, fauxNavigateur, hasard, lireCarte, melanger, textesEcrits } from './aide-carte.mjs';

fauxNavigateur();

const rings = await charger('components/brain/rings.ts');
const model = await charger('components/brain/model.ts');
const effects = await charger('components/brain/effects.ts');
const render = await charger('components/brain/render.ts');
const carte = await charger('components/brain/toolMap.ts');

const CARTE = lireCarte();
const GRAPHE = carte.toolMapGraph(CARTE);
const disposer = (tools = GRAPHE.tools, toolGroups = GRAPHE.tool_groups) => rings.ringLayout({ items: [], groups: [], routines: [], tools, toolGroups });
const aPlat = geo => ({ ...geo, slots: [...geo.slots.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)) });
const placeDe = (geo, nom) => geo.slots.get(rings.toolNodeId(nom));
const xy = p => [Math.cos(p.a) * p.r, Math.sin(p.a) * p.r];

/** Deux hexagones ne se touchent pas : la distance entre leurs centres dépasse la somme de leurs rayons. */
function sansSuperposition(geo, tools, nom) {
  const pts = tools.map(t => ({ t, p: placeDe(geo, t.name) }));
  let plusProche = Infinity;
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      const [x1, y1] = xy(pts[i].p); const [x2, y2] = xy(pts[j].p);
      const marge = Math.hypot(x2 - x1, y2 - y1) - pts[i].p.size - pts[j].p.size;
      plusProche = Math.min(plusProche, marge);
      assert.ok(marge > 1, `${nom} : ${pts[i].t.name} et ${pts[j].t.name} se touchent (marge ${marge.toFixed(2)})`);
    }
  }
  return plusProche;
}

// ---------------------------------------------------------------- le jeu de données

test('le jeu de données a la forme de la route : 45 outils, 7 grandes familles', () => {
  assert.deepEqual(Object.keys(CARTE).sort(), ['absent_modules', 'checked_at', 'families', 'groups', 'internet', 'mode', 'tools', 'usage']);
  assert.equal(CARTE.tools.length, 45);
  assert.equal(CARTE.groups.length, 7);
  assert.deepEqual(CARTE.groups.map(g => g.title), ['Votre PC', 'Web et monde', 'Calculs et données', '3D', 'Studio', 'Agir', 'Mémoire et écriture']);
  assert.ok(carte.isToolMap(CARTE));
  for (const t of CARTE.tools) {
    assert.deepEqual(Object.keys(t).sort(), ['description', 'example', 'family', 'group', 'last_used_at', 'name', 'needs', 'reason', 'state', 'title', 'uses']);
    assert.ok(carte.TOOL_STATES.includes(t.state), t.name);
  }
});

// ---------------------------------------------------------------- la disposition

test('les 45 outils ont une place, dans 7 secteurs rangés dans l’ordre du service', () => {
  const geo = disposer();
  assert.equal(geo.toolSectors.length, 7);
  assert.deepEqual(geo.toolSectors.map(s => s.id), CARTE.groups.map(g => g.id));
  assert.deepEqual(geo.toolSectors.map(s => s.title), CARTE.groups.map(g => g.title));
  assert.deepEqual(geo.toolSectors.map(s => s.count), CARTE.groups.map(g => g.tools));   // 8, 14, 8, 4, 7, 4, 0
  assert.deepEqual(geo.toolSectors.map(s => s.builtins), [0, 0, 0, 0, 0, 2, 5]);
  for (const t of CARTE.tools) assert.ok(placeDe(geo, t.name), `${t.name} a une place`);
  assert.equal([...geo.slots.keys()].filter(id => id.startsWith('tool:')).length, 45);
  // Ni disque mémoire ni anneau des routines sur la carte.
  assert.deepEqual(geo.sectors, []);
  assert.deepEqual(geo.categories, []);
  assert.equal([...geo.slots.keys()].filter(id => id.startsWith('routine:')).length, 0);
});

test('le résultat est le même à chaque lancement, quel que soit l’ordre d’arrivée des outils', () => {
  const reference = aPlat(disposer());
  assert.deepStrictEqual(aPlat(disposer()), reference);
  const alea = hasard(2026);
  for (let i = 0; i < 25; i++) assert.deepStrictEqual(aPlat(disposer(melanger(GRAPHE.tools, alea))), reference, `mélange ${i}`);
  assert.deepStrictEqual(aPlat(disposer([...GRAPHE.tools].reverse())), reference);
  // Un doublon dans la réponse ne crée pas une deuxième place.
  assert.deepStrictEqual(aPlat(disposer([...GRAPHE.tools, GRAPHE.tools[3]])), reference);
});

test('les outils d’une même grande famille sont voisins : chacun dans le secteur de sa famille, et lui seul', () => {
  const geo = disposer();
  const secteurs = geo.toolSectors;
  // Les secteurs se suivent sans se chevaucher, sur un seul tour.
  for (let i = 0; i < secteurs.length; i++) {
    assert.ok(secteurs[i].a1 > secteurs[i].a0, `${secteurs[i].title} a une largeur`);
    if (i) assert.ok(secteurs[i].a0 > secteurs[i - 1].a1, `${secteurs[i].title} commence après ${secteurs[i - 1].title}`);
  }
  assert.ok(secteurs.at(-1).a1 - secteurs[0].a0 < 2 * Math.PI, 'un seul tour');
  for (const t of GRAPHE.tools) {
    const p = placeDe(geo, t.name);
    const dedans = secteurs.filter(s => p.a > s.a0 && p.a < s.a1);
    assert.equal(dedans.length, 1, `${t.name} est dans un seul secteur`);
    assert.equal(dedans[0].id, t.group, `${t.name} est dans le secteur de sa grande famille`);
    // L'hexagone entier tient dans la bande de son secteur (ni sur le cœur, ni dehors, ni chez le voisin).
    assert.ok(p.r - p.size >= geo.r0 && p.r + p.size <= geo.r1, `${t.name} tient entre les deux bords du disque`);
    const bord = Math.min(p.a - dedans[0].a0, dedans[0].a1 - p.a);
    assert.ok(Math.sin(Math.min(bord, Math.PI / 2)) * p.r >= p.size, `${t.name} ne dépasse pas de son secteur`);
  }
  // Un secteur sans outil (capacités intégrées) reste visible.
  const vide = secteurs.find(s => s.count === 0);
  assert.equal(vide.title, 'Mémoire et écriture');
  assert.ok(vide.a1 - vide.a0 >= 0.25);
});

test('deux hexagones ne sont jamais superposés, quels que soient les nombres d’utilisations', () => {
  const avec = uses => GRAPHE.tools.map(t => ({ ...t, uses: uses(t) }));
  const marges = [sansSuperposition(disposer(), GRAPHE.tools, 'jeu de données')];
  marges.push(sansSuperposition(disposer(avec(() => 0)), GRAPHE.tools, 'aucun outil utilisé'));
  marges.push(sansSuperposition(disposer(avec(() => 250_000)), GRAPHE.tools, 'tous au maximum'));
  const alea = hasard(7);
  for (let i = 0; i < 20; i++) {
    const tools = avec(() => Math.floor(10 ** (alea() * 4.5)) - 1);
    marges.push(sansSuperposition(disposer(tools), tools, `tirage ${i}`));
  }
  assert.ok(Math.min(...marges) > 1);
});

test('la taille d’un hexagone grandit avec le nombre réel d’utilisations, entre deux bornes', () => {
  const tailles = [0, 1, 9, 99, 999, 100_000].map(rings.toolMapSize);
  assert.equal(tailles[0], 10);
  assert.equal(tailles[4], 17);
  assert.equal(tailles[5], 17);
  for (let i = 1; i < tailles.length; i++) assert.ok(tailles[i] >= tailles[i - 1]);
  const geo = disposer();
  assert.ok(placeDe(geo, 'web.search').size > placeDe(geo, 'time.now').size);       // 1 340 > 212 utilisations
  assert.ok(placeDe(geo, 'time.now').size > placeDe(geo, 'files.restore').size);    // 212 > 0
  // Sans compteur (uses null), l'outil est dessiné à la plus petite taille : le dessin n'invente pas d'usage.
  const sans = carte.toolMapGraph({ ...CARTE, tools: CARTE.tools.map(t => ({ ...t, uses: null })) });
  assert.ok(sans.tools.every(t => t.uses === 0 && t.counted === false));
  assert.ok(sans.tools.every(t => placeDe(disposer(sans.tools), t.name).size === 10));
});

test('un outil sans grande famille connue est montré à part, jamais perdu', () => {
  const tools = GRAPHE.tools.map(t => (t.name === 'time.now' ? { ...t, group: 'inconnue' } : t.name === 'web.search' ? { ...t, group: undefined } : t));
  const geo = disposer(tools);
  assert.equal(geo.toolSectors.length, 8);
  assert.equal(geo.toolSectors.at(-1).title, 'Autres outils');
  assert.equal(geo.toolSectors.at(-1).count, 2);
  assert.equal([...geo.slots.keys()].filter(id => id.startsWith('tool:')).length, 45);
  sansSuperposition(geo, tools, 'avec « Autres outils »');
  // Aucune grande famille annoncée : tout est sous « Autres outils », rien ne disparaît.
  const seul = disposer(GRAPHE.tools, []);
  assert.deepEqual(seul.toolSectors.map(s => [s.title, s.count]), [['Autres outils', 45]]);
  sansSuperposition(seul, GRAPHE.tools, 'un seul secteur');
  // Aucun outil du tout : sept secteurs vides, sans erreur.
  const rien = disposer([]);
  assert.equal(rien.toolSectors.length, 7);
  assert.ok(rien.toolSectors.every(s => s.count === 0 && Number.isFinite(s.a0) && Number.isFinite(s.a1)));
});

test('beaucoup de familles (les 17 familles fines, puis 60) : toujours aucun hexagone superposé', () => {
  const fines = CARTE.families.filter(f => !f.builtin).map(f => ({ id: f.id, title: f.title }));
  assert.equal(fines.length, 17);
  const parFamille = GRAPHE.tools.map(t => ({ ...t, group: t.family }));
  const geo = disposer(parFamille, fines);
  assert.equal(geo.toolSectors.length, 17);
  sansSuperposition(geo, parFamille, '17 familles');
  const soixante = Array.from({ length: 60 }, (_, i) => ({ id: `g${i}`, title: `Famille ${i}` }));
  const etales = GRAPHE.tools.map((t, i) => ({ ...t, group: `g${(i * 7) % 60}` }));
  sansSuperposition(disposer(etales, soixante), etales, '60 familles');
});

// ---------------------------------------------------------------- la simulation (ce que BrainGraph anime)

function simulation() {
  const sim = new rings.RingSim(true);
  sim.sync(GRAPHE, 1000, true);
  sim.settle();
  return sim;
}

test('RingSim en carte des outils : chaque outil est à sa place, cliquable, et le centre reste le cœur', () => {
  const sim = simulation();
  assert.equal(sim.toolMap, true);
  assert.equal(sim.extras.length, 45);
  assert.ok(sim.extras.every(n => n.type === 'tool'));
  for (const t of GRAPHE.tools) {
    const n = sim.resolve(rings.toolNodeId(t.name));
    const [x, y] = xy(placeDe(sim.geo, t.name));
    assert.ok(Math.abs(n.x - x) < 1e-9 && Math.abs(n.y - y) < 1e-9, `${t.name} est sur sa place`);
    assert.equal(n.tool.state, t.state);
    assert.equal(sim.hit(n.x + 2, n.y - 2, 6, 2000)?.id, n.id, `${t.name} est trouvé sous le pointeur`);
  }
  // Au centre : le cœur, jamais un niveau de mémoire (ils ne sont pas dessinés sur la carte).
  assert.equal(sim.hit(0, 0, 6, 2000)?.type, 'core');
  assert.equal(sim.hit(3, 3, 6, 2000)?.type, 'core');
  for (let a = 0; a < 6.28; a += 0.05) for (let r = 0; r < 90; r += 6) assert.notEqual(sim.hit(Math.cos(a) * r, Math.sin(a) * r, 6, 2000)?.type, 'level');
  // Le cadrage garde de la place pour les noms des familles, écrits hors du disque.
  const b = sim.bounds();
  assert.ok(b.maxX > sim.geo.outer + 100 && b.minX < -sim.geo.outer - 100 && b.maxY > sim.geo.outer && b.minY < -sim.geo.outer);
});

test('un outil qui sert s’allume depuis le cœur (événement réel tool.result)', () => {
  const sim = simulation();
  const e = { id: 9, kind: 'tool.result', subject_type: 'step', subject_id: 1, run_id: 4, label: '', payload: { tool: 'time.now', ok: true }, created_at: '2026-11-11T10:00:00.000Z' };
  const plan = effects.planEffects(model.classifyEvent(e), sim, { now: 5000, speed: 1, reduced: false, live: true });
  const particule = plan.effects.find(x => x.type === 'particle');
  assert.equal(particule.to, 'tool:time.now');
  assert.deepEqual([particule.fx, particule.fy], [0, 0], 'la particule part du centre');
  assert.ok(plan.effects.some(x => x.type === 'pulse' && x.node === 'tool:time.now'));
});

// ---------------------------------------------------------------- le dessin (ordres envoyés au canvas)

function dessiner(sim, { k = 1, selectedId = null, hoverId = null, effets = [] } = {}) {
  const { ctx, journal } = fauxContexte();
  render.drawScene(ctx, {
    sim, effects: effets, now: 5700, w: 1000, h: 560, cam: { k, x: 0, y: 0 }, selectedId, hoverId, highlight: null,
    statusOf: n => n.status, metaOf: () => null, counts: { immediate: 0, persistent: 0, semantic: 0, actions: 0 },
    reduced: false, rings: sim, compact: false,
  });
  return journal;
}
/** Nombre d'hexagones tracés : un tracé de 6 sommets fermé (render.ts, hexPath). */
const hexagones = journal => journal.filter((o, i) => o[0] === 'closePath' && journal.slice(i - 5, i).every(p => p[0] === 'lineTo') && journal[i - 6]?.[0] === 'moveTo').length;

test('le dessin : 45 hexagones, les 7 noms de famille lisibles, plus aucun décor de mémoire', () => {
  const journal = dessiner(simulation());
  assert.equal(hexagones(journal), 45);
  const textes = textesEcrits(journal);
  for (const g of CARTE.groups) assert.ok(textes.includes(g.title.toUpperCase()), `« ${g.title} » est écrit`);
  assert.ok(textes.includes('AURORA'));
  assert.ok(textes.includes('14 outils') && textes.includes('8 outils') && textes.includes('4 outils') && textes.includes('7 outils'));
  assert.ok(textes.includes('5 capacités intégrées'), 'la famille sans outil dit ce qu’elle contient');
  // Aucun texte courbé (MÉMOIRE · N, ROUTINES · N, OUTILS · N, noms des niveaux) : ils s'écrivent lettre par lettre.
  assert.deepEqual(textes.filter(t => [...t].length === 1), []);
  for (const t of textes) assert.ok(!/MÉMOIRE ·|ROUTINES|IMMÉDIATE|PERSISTANTE|SÉMANTIQUE/.test(t), t);
  // Le cerveau au centre (signe de la mémoire, tracé depuis un chemin SVG) n'est pas dessiné ; sur les anneaux, si.
  const cerveau = j => j.filter(o => o[0] === 'stroke' && o[1]?.chemin?.some(c => c[0] === 'd')).length;
  assert.equal(cerveau(journal), 0);
  const memoire = new rings.RingSim();
  memoire.sync({ nodes: [], links: [], groups: [], routines: [], tools: [] }, 1000, true);
  assert.equal(cerveau(dessiner(memoire)), 4);
});

test('le dessin : l’état ne tient pas à la couleur seule (contour en pointillés et signe pour ce qui ne peut pas servir)', () => {
  const nonDisponibles = CARTE.tools.filter(t => t.state !== 'available');
  assert.ok(nonDisponibles.length > 0, 'le jeu de données a des outils désactivés ou indisponibles');
  const journal = dessiner(simulation());
  // Un contour en pointillés par outil non disponible (le secteur sans outil a aussi le sien, plus long : 3 / 4).
  const pointilles = journal.filter(o => o[0] === 'setLineDash' && o[1].length === 2 && o[1][1] === 2.5);
  assert.equal(pointilles.length, nonDisponibles.length);
  // Le signe (barre ou croix) est tracé en blanc à bouts ronds ; le point clair des outils disponibles ne l'est pas pour eux.
  const signes = journal.filter(o => o[0] === 'strokeStyle =' && o[1] === 'rgba(255,255,255,0.9)').length;
  assert.equal(signes, nonDisponibles.length);
  const pointsClairs = journal.filter(o => o[0] === 'fillStyle =' && o[1] === 'rgba(255,255,255,0.75)').length;
  assert.equal(pointsClairs, 45 - nonDisponibles.length);
  // Sur les anneaux de la mémoire, les mêmes outils sans état gardent tous leur point clair et aucun pointillé d'état.
  const memoire = new rings.RingSim();
  memoire.sync({ nodes: [], links: [], groups: [], routines: [], tools: GRAPHE.tools.map(t => ({ name: t.name, title: t.title, uses: t.uses, last_used_at: t.last_used_at })) }, 1000, true);
  memoire.settle();
  const jm = dessiner(memoire, { k: 0.75 });
  assert.equal(jm.filter(o => o[0] === 'fillStyle =' && o[1] === 'rgba(255,255,255,0.75)').length, 45);
  assert.equal(jm.filter(o => o[0] === 'setLineDash' && o[1].length === 2 && o[1][1] === 2.5).length, 0);
});

test('le dessin : l’outil choisi garde son anneau blanc, et les familles restent écrites', () => {
  const sim = simulation();
  const sans = dessiner(sim);
  const avec = dessiner(sim, { selectedId: 'tool:shell.run' });
  const blancs = j => j.filter(o => o[0] === 'strokeStyle =' && o[1] === '#ffffff').length;
  assert.equal(blancs(sans), 0);
  assert.equal(blancs(avec), 1);
  for (const g of CARTE.groups) assert.ok(textesEcrits(avec).includes(g.title.toUpperCase()));
  // Vue rapprochée : les noms des outils apparaissent (ceux qui ne se chevauchent pas).
  const pres = textesEcrits(dessiner(sim, { k: 1.6 }));
  assert.ok(CARTE.tools.filter(t => pres.includes(t.title.length > 24 ? `${t.title.slice(0, 23)}…` : t.title)).length >= 10);
});

test('aux tailles de la page Tâches, la carte cadrée garde ses 7 noms de famille entiers dans le cadre', () => {
  // Le cadrage de BrainGraph (fitView) : tout le dessin dans le canvas, avec ses marges. Largeur de lettre prise
  // large (8 px pour des capitales grasses de 11 px) : les vrais textes sont un peu plus étroits.
  const LETTRE = 8;
  const sim = simulation();
  const b = sim.bounds();
  for (const [w, h] of [[1500, 590], [1100, 590], [900, 560], [820, 500], [760, 460]]) {
    const k = Math.max(0.25, Math.min(1.6, Math.min((w - 60) / (b.maxX - b.minX), (h - 40) / (b.maxY - b.minY))));
    const { ctx, journal } = fauxContexte(LETTRE);
    render.drawScene(ctx, {
      sim, effects: [], now: 5700, w, h, cam: { k, x: 0, y: 0 }, selectedId: null, hoverId: null, highlight: null,
      statusOf: n => n.status, metaOf: () => null, counts: { immediate: 0, persistent: 0, semantic: 0, actions: 0 },
      reduced: false, rings: sim, compact: false,
    });
    const ecrits = journal.filter(o => o[0] === 'fillText');
    for (const g of CARTE.groups) {
      const titre = g.title.toUpperCase();
      const o = ecrits.find(e => e[1] === titre);
      assert.ok(o, `${w}×${h} : « ${g.title} » est écrit`);
      const demi = ([...titre].length * LETTRE) / 2 + 3;
      assert.ok(o[2] - demi >= 0 && o[2] + demi <= w, `${w}×${h} : « ${g.title} » tient en largeur (x ${o[2].toFixed(0)}, demi-largeur ${demi})`);
      assert.ok(o[3] - 8 >= 0 && o[3] + 8 <= h, `${w}×${h} : « ${g.title} » tient en hauteur (y ${o[3].toFixed(0)})`);
    }
    // Sous chaque nom, ce que la famille contient.
    assert.equal(ecrits.filter(e => / outils?$| capacités? intégrées?$/.test(e[1])).length, 7, `${w}×${h} : les 7 comptes sont écrits`);
    // Le disque entier (hexagones compris) est dans le cadre.
    for (const n of sim.extras) {
      const x = w / 2 + n.x * k; const y = h / 2 + n.y * k;
      assert.ok(x - n.r * k > 0 && x + n.r * k < w && y - n.r * k > 0 && y + n.r * k < h, `${w}×${h} : ${n.id} est dans le cadre`);
    }
    assert.ok(k >= 0.7, `${w}×${h} : les hexagones restent lisibles (zoom ${k.toFixed(2)})`);
  }
});

// ---------------------------------------------------------------- les textes (honnêteté)

test('les trois états ont chacun leur mot ; un état inconnu n’est jamais dit « Disponible »', () => {
  assert.deepEqual(carte.TOOL_STATE_LABELS, { available: 'Disponible', disabled: 'Désactivé', absent: 'Indisponible' });
  assert.equal(carte.toolStateLabel('available'), 'Disponible');
  assert.equal(carte.toolStateLabel('absent'), 'Indisponible');
  for (const inconnu of ['nouveau', '', null, undefined]) assert.equal(carte.toolStateLabel(inconnu), 'État inconnu');
});

test('le compteur : « compteur indisponible » quand il manque, jamais 0', () => {
  assert.equal(carte.USES_LABEL, 'Utilisé dans une demande');
  assert.equal(carte.usesText(null), 'compteur indisponible');
  assert.equal(carte.usesText(undefined), 'compteur indisponible');
  assert.equal(carte.usesText(Number.NaN), 'compteur indisponible');
  assert.equal(carte.usesText(0), 'jamais');
  assert.equal(carte.usesText(1), '1 fois');
  assert.equal(carte.usesText(212), '212 fois');
  for (const v of [null, undefined, Number.NaN]) assert.ok(!/\d/.test(carte.usesText(v)), 'aucun chiffre sans compteur');
  assert.equal(carte.graphUsesText({ uses: 0, counted: false }), 'compteur indisponible');
  assert.equal(carte.graphUsesText({ uses: 0, counted: true }), 'jamais');
  assert.equal(carte.graphUsesText({ uses: 3 }), '3 fois');
});

test('la phrase d’exemple : le texte exact quand il n’y en a pas', () => {
  assert.equal(carte.NO_EXAMPLE, "Pas de phrase d'exemple vérifiée pour cet outil");
  assert.equal(CARTE.tools.filter(t => t.example === null).length, 29);   // 28 sur le vrai PC si une quarantaine existe
  assert.ok(CARTE.tools.filter(t => t.example !== null).every(t => typeof t.example === 'string' && t.example.trim()));
});

test('ce que la carte donne au dessin : les 4 champs d’un outil, sa famille, son état', () => {
  assert.deepEqual(Object.keys(GRAPHE).sort(), ['groups', 'links', 'nodes', 'routines', 'tool_groups', 'tools']);
  assert.deepEqual([GRAPHE.nodes, GRAPHE.links, GRAPHE.groups, GRAPHE.routines], [[], [], [], []]);   // aucun souvenir envoyé
  assert.equal(GRAPHE.tools.length, 45);
  for (const [i, t] of CARTE.tools.entries()) {
    const g = GRAPHE.tools[i];
    assert.deepEqual([g.name, g.title, g.uses, g.last_used_at], [t.name, t.title, t.uses, t.last_used_at]);
    assert.deepEqual([g.group, g.family, g.state, g.reason, g.counted], [t.group, t.family, t.state, t.reason, true]);
  }
  assert.deepEqual(GRAPHE.tool_groups.map(g => [g.id, g.builtins]), [['pc', 0], ['monde', 0], ['calcul', 0], ['3d', 0], ['studio', 0], ['agir', 2], ['memoire', 5]]);
});

test('la liste : chaque outil une seule fois sous sa grande famille, les capacités intégrées à part', () => {
  const liste = carte.toolList(CARTE);
  assert.deepEqual(liste.map(g => g.title), CARTE.groups.map(g => g.title));
  assert.deepEqual(liste.map(g => g.tools.length), [8, 14, 8, 4, 7, 4, 0]);
  const noms = liste.flatMap(g => g.tools.map(t => t.name));
  assert.equal(noms.length, 45);
  assert.equal(new Set(noms).size, 45);
  for (const g of liste) for (const t of g.tools) assert.equal(t.group, g.id);
  // Les capacités intégrées : sous leur grande famille, sans outil, jamais mêlées aux outils.
  assert.deepEqual(liste.find(g => g.id === 'memoire').builtins.map(f => f.title), ['Rédaction', 'Mémoire', 'Conversations passées', 'Documents joints', 'Pages']);
  assert.deepEqual(liste.find(g => g.id === 'agir').builtins.map(f => f.title), ['Routines', 'Agent']);
  assert.equal(liste.flatMap(g => g.builtins).length, 7);
  assert.ok(liste.flatMap(g => g.builtins).every(f => f.builtin && f.tools === 0));
  // Dans une grande famille, les outils suivent l'ordre des familles du service, puis leur nom.
  assert.deepEqual(liste.find(g => g.id === 'agir').tools.map(t => t.name), ['pc.gesture', 'pc.pointer', 'app.launch', 'shell.run']);
  // Un outil que le service n'a rangé nulle part est sous « Autres outils », pas perdu.
  const perdu = carte.toolList({ ...CARTE, tools: CARTE.tools.map(t => (t.name === 'time.now' ? { ...t, group: 'inconnue' } : t)) });
  assert.equal(perdu.at(-1).title, 'Autres outils');
  assert.deepEqual(perdu.at(-1).tools.map(t => t.name), ['time.now']);
  assert.equal(perdu.flatMap(g => g.tools).length, 45);
});

test('les petits textes de la fiche et le refus d’une réponse sans forme', () => {
  assert.deepEqual(carte.stateCounts(CARTE), { available: 39, disabled: 2, absent: 4 });
  assert.equal(carte.familyOf(CARTE, 'weather').title, 'Météo');
  assert.equal(carte.familyOf(CARTE, 'inconnue'), null);
  assert.equal(carte.permissionText(true), 'demandé avant d’agir');
  assert.equal(carte.permissionText(false), 'pas demandé');
  assert.match(carte.permissionText(null), /interdit/);
  assert.equal(carte.permissionText(undefined), 'inconnu');
  assert.deepEqual([true, false, null].map(carte.internetText), ['répond', 'ne répond pas', 'non vérifié']);
  for (const mauvais of [null, undefined, {}, [], 'Not Found', { tools: [] }, { tools: [], groups: [] }, { detail: 'Not Found' }]) assert.equal(carte.isToolMap(mauvais), false);
});
