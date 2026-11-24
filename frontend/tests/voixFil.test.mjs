// La voix au fil de l'écriture (src/lib/voixFil.ts) : les deux règles d'Athéna, avec ses cas de test du 24/09 et du
// 05/10/2026 (Athéna payante, tests/voix-fil-ecriture.test.ts), et la reprise à la réponse finale.
// Lancer depuis frontend : node --experimental-strip-types --test tests/voixFil.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { debutPrononcable, finDesPhrasesEcrites, resteADire } from '../src/lib/voixFil.ts';

test('deux phrases écrites : elles partent', () => {
  const t = "Je ne peux pas vérifier la date exacte de sortie de ces modèles. Voici ce que ma recherche a trouvé sur le web aujourd'hui. Premier";
  assert.equal(debutPrononcable(t), "Je ne peux pas vérifier la date exacte de sortie de ces modèles. Voici ce que ma recherche a trouvé sur le web aujourd'hui.");
});
test('une seule phrase courte : on attend', () => {
  assert.equal(debutPrononcable('Bonjour A.B. Je '), null);
  assert.equal(debutPrononcable('Oui. Non. '), null);   // deux fins mais moins de 60 caractères
  assert.equal(debutPrononcable(''), null);
});
test('ni un décimal, ni une version, ni une abréviation ne coupent une phrase', () => {
  assert.equal(debutPrononcable('Le module tire 8.5 A sous 12 V pour la version V7.6 du brevet, par ex. ce montage '), null);
});
test('bloc de code ouvert : on attend la fin', () => {
  assert.equal(debutPrononcable("Voici le code. Il compile sans erreur sur l'ESP32 du prototype.\n```cpp\nvoid setup(){}"), null);
});
test('une longue phrase seule finit par partir', () => {
  const t = 'La thermorégulation active de VITA repose sur des modules Peltier réversibles qui pompent la chaleur du fluide vers les dissipateurs placés dans le dos du vêtement. Et';
  assert.ok(debutPrononcable(t)?.endsWith('vêtement.'));
});

const fin = t => t.slice(0, finDesPhrasesEcrites(t));
test('la suite : seules les phrases finies partent', () => {
  assert.equal(fin(' Cette tension dépend de la différence de température. On a ainsi commencé à cla'),
    ' Cette tension dépend de la différence de température.');
  assert.equal(fin(' On a ainsi commencé à cla'), '');
});
test('la suite : ni un décimal, ni une version, ni une abréviation ne coupent', () => {
  assert.equal(fin(' Le module tire 8.5 A pour la version V7.6 du brevet, par ex. ce montage '), '');
});
test('la suite : un titre ou une ligne de liste sans point est finie au retour à la ligne', () => {
  assert.equal(fin('\n\n**Les débuts**\n- Seebeck, 1821\n- Pel'), '\n\n**Les débuts**\n- Seebeck, 1821\n');
});
test('la suite : jamais dans un bloc de code, ouvert ou fermé', () => {
  assert.equal(fin(' Voici le code.\n```cpp\nvoid setup(){}\nvoid lo'), ' Voici le code.\n');
  assert.equal(fin(' Voici le code.\n```cpp\nvoid setup(){}\n```\nIl compile. Et'), ' Voici le code.\n```cpp\nvoid setup(){}\n```\nIl compile.');
});

// La réponse finale d'AURORA n'est pas toujours le texte écrit tel quel : une note de vérification à la fin, une note
// devant (« Claude n'est pas utilisé : … »), ou une tout autre réponse quand un autre moteur a pris le relais.
const DIT = 'Le Maroc est un pays d’Afrique du Nord. Sa capitale est Rabat, et sa plus grande ville Casablanca.';
test('la réponse finale continue ce qui a été dit : le reste seulement', () => {
  assert.equal(resteADire(`${DIT} On y parle arabe et amazighe.`, DIT), ' On y parle arabe et amazighe.');
  assert.equal(resteADire(DIT, DIT), '');                                   // tout est déjà dit
  assert.equal(resteADire(`${DIT}\n\nNote : une source est ancienne.`, DIT), '\n\nNote : une source est ancienne.');
});
test('une note devant la réponse ne fait pas tout relire', () => {
  assert.equal(resteADire(`Claude n’est pas utilisé : budget atteint.\n\n${DIT} Fin.`, DIT), ' Fin.');
});
test('un blanc changé au début : la fin de ce qui a été dit retrouve l’endroit', () => {
  assert.equal(resteADire(`${DIT.replace('Le Maroc', 'Le  Maroc')} Suite.`, DIT), ' Suite.');
});
test('une autre réponse que celle qui s’écrivait : null, à l’appelant de lire la vraie', () => {
  assert.equal(resteADire('Voici la réponse de Claude, qui dit tout autre chose.', DIT), null);
  assert.equal(resteADire('Réponse entière.', '  '), 'Réponse entière.');   // rien n'avait été dit
});
