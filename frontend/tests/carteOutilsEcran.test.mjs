// Carte des outils de la page Tâches : ce que l'écran ÉCRIT (11/11/2026). Les composants (components/toolmap) sont
// rendus en HTML par React, dans Node, avec la vraie réponse de la route (tests/data/carte-outils.json). On lit le
// texte produit : les mots des états, les phrases d'honnêteté, les boutons présents. Ni navigateur, ni serveur, ni
// canvas : la mise en page, les couleurs et les gestes (clic, Échap, focus) ne sont PAS vérifiés ici.
// Lancer depuis frontend : node --experimental-strip-types --test tests/carteOutilsEcran.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { charger, fauxNavigateur, lireCarte } from './aide-carte.mjs';

fauxNavigateur();

const { default: ToolMapPanel } = await charger('components/toolmap/ToolMapPanel.tsx');
const { default: ToolMapView } = await charger('components/toolmap/ToolMapView.tsx');
const carte = await charger('components/brain/toolMap.ts');
const format = await charger('lib/format.ts');

const CARTE = lireCarte();
const rien = () => undefined;
const etat = (map, error = '') => ({ map, error, loading: false, reload: rien });
const html = (composant, props) => renderToStaticMarkup(createElement(composant, props));
/** Le texte lisible d'un morceau de HTML : balises retirées, entités décodées, espaces réunis. */
const texte = h => h.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
  .replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();
const boutons = h => [...h.matchAll(/<button\b[^>]*>(.*?)<\/button>/gs)].map(m => texte(m[1]) || (/aria-label="([^"]*)"/.exec(m[0])?.[1] ?? ''));
const fiche = (tool, map = CARTE) => html(ToolMapPanel, { state: etat(map), selected: tool, onSelect: rien });

// ---------------------------------------------------------------- la liste (rien de choisi)

test('la liste : les 45 outils en boutons, chacun avec le mot de son état', () => {
  const h = html(ToolMapPanel, { state: etat(CARTE), selected: null, onSelect: rien });
  const items = [...h.matchAll(/<button\b[^>]*class="toolmap-item"[^>]*>(.*?)<\/button>/gs)];
  assert.equal(items.length, 45);
  assert.equal(boutons(h).length, 45, 'aucun autre bouton dans la liste');
  for (const t of CARTE.tools) {
    const item = items.find(m => m[0].includes(`data-tool="${t.name}"`));
    assert.ok(item, `${t.name} est dans la liste`);
    const lu = texte(item[1]);
    assert.ok(lu.includes(texte(t.title)), `${t.name} : son nom`);
    assert.ok(lu.endsWith(carte.TOOL_STATE_LABELS[t.state]), `${t.name} : le mot « ${carte.TOOL_STATE_LABELS[t.state]} » est écrit (${lu})`);
    assert.ok(item[0].includes(`data-state="${t.state}"`));
  }
  const lu = texte(h);
  let fin = -1;
  for (const g of CARTE.groups) {   // les grandes familles, dans l'ordre du service
    const ici = lu.indexOf(g.title, fin + 1);
    assert.ok(ici > fin, `« ${g.title} » vient à sa place`);
    fin = ici;
  }
  assert.ok(lu.includes('Outils par grande famille'));
  assert.ok(!h.includes('<a '), 'aucun lien');
});

test('la liste : les capacités intégrées sont dites « capacité intégrée », sans bouton, sans état, sans hexagone', () => {
  const h = html(ToolMapPanel, { state: etat(CARTE), selected: null, onSelect: rien });
  const integrees = [...h.matchAll(/<li class="toolmap-builtin">(.*?)<\/li>/gs)];
  assert.equal(integrees.length, 7);
  assert.deepEqual(integrees.map(m => /<strong>(.*?)<\/strong>/.exec(m[1])[1]).sort(),
    ['Agent', 'Conversations passées', 'Documents joints', 'Mémoire', 'Pages', 'Routines', 'Rédaction']);
  for (const m of integrees) {
    assert.ok(texte(m[1]).endsWith('capacité intégrée'));
    assert.ok(!m[1].includes('<button') && !m[1].includes('<svg') && !m[1].includes('data-state'));
    for (const mot of Object.values(carte.TOOL_STATE_LABELS)) assert.ok(!texte(m[1]).includes(mot), `pas de « ${mot} » pour une capacité intégrée`);
  }
  assert.ok(texte(h).includes('Mémoire et écriture aucun outil'));
});

// ---------------------------------------------------------------- la fiche

test('la fiche de chacun des 45 outils : nom, identifiant, familles, description telle quelle, état et raison', () => {
  for (const t of CARTE.tools) {
    const h = fiche(t.name);
    const lu = texte(h);
    const famille = CARTE.families.find(f => f.id === t.family);
    const grande = CARTE.groups.find(g => g.id === t.group);
    assert.ok(lu.includes('Fiche d\'un outil'), t.name);
    assert.ok(lu.includes(texte(t.title)), `${t.name} : nom lisible`);
    assert.ok(lu.includes(`Identifiant : ${t.name}`), `${t.name} : identifiant`);
    assert.ok(lu.includes(`Grande famille ${grande.title}`), `${t.name} : grande famille`);
    assert.ok(lu.includes(`Famille ${famille.title}`), `${t.name} : famille`);
    assert.ok(lu.includes(texte(t.description)), `${t.name} : description telle quelle`);
    assert.ok(lu.includes(`Son état ${carte.TOOL_STATE_LABELS[t.state]}`), `${t.name} : état en un mot`);
    if (t.reason) assert.ok(lu.includes(texte(t.reason)), `${t.name} : la raison est écrite`);
    assert.ok(lu.includes(`Internet ${t.needs.internet ? 'oui' : 'non'}`), `${t.name} : Internet`);
    assert.ok(lu.includes(`Votre accord ${carte.permissionText(t.needs.permission)}`), `${t.name} : accord`);
    assert.ok(lu.includes(texte(t.needs.permission_reason)), `${t.name} : pourquoi l'accord`);
    assert.ok(lu.includes(`Niveau de risque Risque ${{ low: 'faible', medium: 'moyen', high: 'élevé' }[t.needs.risk]}`), `${t.name} : risque`);
    assert.ok(lu.includes(`Utilisé dans une demande ${texte(carte.usesText(t.uses))}`), `${t.name} : compteur`);
    // Un outil sans nom lisible dans le code : on le dit, on n'invente pas de nom.
    assert.equal(lu.includes('le code ne lui donne pas d’autre nom'), t.title === t.name, t.name);
  }
});

test('la fiche : la phrase d’exemple est montrée et peut être copiée, jamais envoyée ; sans phrase, on le dit', () => {
  let avec = 0;
  let sans = 0;
  for (const t of CARTE.tools) {
    const h = fiche(t.name);
    const lu = texte(h);
    if (t.example) {
      avec++;
      assert.ok(lu.includes(texte(t.example)), `${t.name} : la phrase est écrite`);
      assert.ok(!lu.includes(carte.NO_EXAMPLE));
      assert.ok(lu.includes('La phrase n\'est pas envoyée d\'ici'), `${t.name} : dit qu'elle n'est pas envoyée`);
      // Les seuls boutons : fermer la fiche et copier. Rien qui envoie, essaie ou lance.
      assert.deepEqual(boutons(h), ['Fermer la fiche (Échap)', 'Copier la phrase'], t.name);
    } else {
      sans++;
      assert.ok(lu.includes("Pas de phrase d'exemple vérifiée pour cet outil"), `${t.name} : le texte exact`);
      assert.deepEqual(boutons(h), ['Fermer la fiche (Échap)'], t.name);
    }
    assert.ok(!/<a\b|<form\b|<input\b|<textarea\b/.test(h), `${t.name} : ni lien, ni formulaire`);
    assert.ok(!/essayer maintenant|envoyer|lancer/i.test(boutons(h).join(' ')));
  }
  assert.deepEqual([avec, sans], [16, 29]);
});

test('la fiche : sans compteur, « compteur indisponible », jamais 0 ; avec, le nombre et la date réels', () => {
  const sansCompteur = { ...CARTE, usage: null, tools: CARTE.tools.map(t => ({ ...t, uses: null, last_used_at: null })) };
  for (const nom of ['time.now', 'shell.run', 'studio.site']) {
    const lu = texte(fiche(nom, sansCompteur));
    assert.ok(lu.includes('Utilisé dans une demande compteur indisponible'), nom);
    assert.ok(lu.includes('Dernière fois inconnue'), nom);
    assert.ok(!/Utilisé dans une demande (0|jamais)/.test(lu), `${nom} : ni 0 ni « jamais » sans compteur`);
  }
  const jamais = texte(fiche('files.restore'));
  assert.ok(jamais.includes('Utilisé dans une demande jamais') && jamais.includes('Dernière fois jamais'));
  const souvent = texte(fiche('time.now'));
  assert.ok(souvent.includes('Utilisé dans une demande 212 fois'));
  assert.ok(souvent.includes(texte(format.fmtDateTime('2026-11-11T08:41:07.000Z'))), 'la date réelle de la dernière utilisation');
});

test('la fiche : un état que l’interface ne connaît pas n’est pas dit « Disponible » ; un outil disparu rend la liste', () => {
  const etrange = { ...CARTE, tools: CARTE.tools.map(t => (t.name === 'time.now' ? { ...t, state: 'en_panne', reason: 'Raison donnée par le service.' } : t)) };
  const lu = texte(fiche('time.now', etrange));
  assert.ok(lu.includes('Son état État inconnu Raison donnée par le service.'));
  assert.ok(!lu.includes('Son état Disponible'));
  // L'outil choisi n'existe pas (carte relue sans lui) : pas de fiche vide, la liste revient.
  const h = fiche('outil.disparu');
  assert.ok(texte(h).includes('Outils par grande famille'));
  assert.ok(!h.includes('toolmap-card'));
});

test('le panneau : si la route échoue, un message clair et « Réessayer », pas une liste vide', () => {
  const h = html(ToolMapPanel, { state: etat(null, 'Service local injoignable'), selected: null, onSelect: rien });
  assert.ok(texte(h).includes('La liste des outils n\'a pas pu être lue : Service local injoignable'));
  assert.deepEqual(boutons(h), ['Réessayer']);
  assert.ok(texte(html(ToolMapPanel, { state: etat(null), selected: null, onSelect: rien })).includes('Lecture des outils…'));
});

// ---------------------------------------------------------------- la carte (cadre autour du canvas)

test('la carte : le canvas est annoncé en mots, la légende écrit les trois états et leurs nombres', () => {
  const h = html(ToolMapView, { state: etat(CARTE), selected: null, onSelect: rien });
  assert.match(h, /<canvas[^>]*role="img"/);
  const label = /<canvas[^>]*aria-label="([^"]*)"/.exec(h)[1].replace(/&#x27;/g, "'");
  assert.ok(label.startsWith("Carte des outils d'AURORA : 45 outils autour du cœur, en 7 grandes familles."), label);
  assert.ok(label.includes('La liste à côté donne les mêmes outils au clavier'));
  assert.ok(!label.includes('Cerveau'), 'ce n’est pas annoncé comme le cerveau de la mémoire');
  const legende = texte(/<ul class="toolmap-legend"[^>]*>(.*?)<\/ul>/s.exec(h)[1]);
  assert.equal(legende, 'Disponible 39 Désactivé 2 Indisponible 4');
  const lu = texte(h);
  assert.ok(lu.includes(CARTE.usage.note), 'la note du service est écrite telle quelle');
  assert.ok(lu.includes(`Compté depuis le ${texte(format.fmtDateTime(CARTE.usage.since))}.`));
  assert.ok(lu.includes('Mode Équilibré') && lu.includes('Internet : répond'));
  assert.ok(!h.includes('role="alert"'), 'aucune alerte quand tous les modules sont chargés');
  assert.deepEqual(boutons(h).slice(-1), ['Actualiser']);
});

test('la carte : un module d’outils non chargé est dit en clair, en haut', () => {
  const absent = { ...CARTE, absent_modules: [{ module: 'sky', reason: 'Le module d’outils « sky » n’a pas pu être chargé (ImportError).' }] };
  const h = html(ToolMapView, { state: etat(absent), selected: null, onSelect: rien });
  const alerte = /<div class="toolmap-alert" role="alert">(.*?)<\/div>/s.exec(h);
  assert.ok(alerte, 'une alerte est affichée');
  assert.ok(h.indexOf('toolmap-alert') < h.indexOf('toolmap-stage'), 'au-dessus de la carte');
  assert.ok(texte(alerte[1]).includes("Un module d'outils n'a pas pu être chargé : ses outils ne sont pas sur la carte."));
  assert.ok(texte(alerte[1]).includes('Le module d’outils « sky » n’a pas pu être chargé (ImportError).'));
  const deux = { ...CARTE, absent_modules: [...absent.absent_modules, { module: 'web', reason: 'Autre raison.' }] };
  assert.ok(texte(html(ToolMapView, { state: etat(deux), selected: null, onSelect: rien })).includes("2 modules d'outils n'ont pas pu être chargés"));
});

test('la carte : si la route échoue, « Carte des outils indisponible » avec la raison, et aucun canvas vide', () => {
  const h = html(ToolMapView, { state: etat(null, 'Service local injoignable'), selected: null, onSelect: rien });
  assert.ok(texte(h).includes('Carte des outils indisponible Service local injoignable'));
  assert.ok(!h.includes('<canvas'), 'pas de carte vide');
  assert.ok(!h.includes('toolmap-legend'));
  assert.deepEqual(boutons(h), ['Réessayer']);
  // Une relecture qui échoue garde la dernière carte, et le dit.
  const garde = html(ToolMapView, { state: etat(CARTE, 'Service local injoignable'), selected: null, onSelect: rien });
  assert.ok(garde.includes('<canvas'));
  assert.match(texte(garde), /La dernière lecture a échoué \(Service local injoignable\)\. La carte montre l'état lu à \d\d:\d\d/);
});

test('la carte : sans compteur, elle dit que la taille des hexagones ne dit rien ; en mode privé, « non vérifié »', () => {
  const sans = { ...CARTE, usage: null, mode: 'prive', internet: null, tools: CARTE.tools.map(t => ({ ...t, uses: null })) };
  const lu = texte(html(ToolMapView, { state: etat(sans), selected: null, onSelect: rien }));
  assert.ok(lu.includes("Compteur d'utilisations indisponible : la taille des hexagones ne dit rien de leur usage."));
  assert.ok(lu.includes('Mode Privé') && lu.includes('Internet : non vérifié'));
  const vide = { ...CARTE, usage: { ...CARTE.usage, since: null } };
  assert.ok(texte(html(ToolMapView, { state: etat(vide), selected: null, onSelect: rien })).includes('Le journal est vide : rien n’est encore compté.'));
});
