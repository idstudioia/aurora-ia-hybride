// Tests de la relecture de la phrase dictée (voix D1). Lancer depuis frontend :
//   node --experimental-strip-types --test tests/dictee.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CONFIANCE_MINIMALE, corrigerDictee, douteDictee, reponseConfirmation } from '../src/lib/dictee.ts';

test('les corrections d’Athéna, chacune montrée', () => {
  const a = corrigerDictee('des pistes d’amélioration pour le système guitare');
  assert.equal(a.texte, 'des pistes d’amélioration pour le système VITA');
  assert.deepEqual(a.corrections, ['« système guitare » → « système VITA »']);
  assert.equal(corrigerDictee("je t'aime pour savoir un peu tes capacités").texte, "j'aimerais savoir un peu tes capacités");
  assert.equal(corrigerDictee('ouvre-la dans un ongle').texte, 'ouvre-la dans un onglet');
  assert.equal(corrigerDictee('vous ma playlist musique').texte, 'ouvre ma playlist musique');
  assert.equal(corrigerDictee("c'est quoi le système Victor").texte, "c'est quoi le système VITA");
  // le contexte est obligatoire : ces phrases ne bougent pas
  for (const juste of ['joue de la guitare', 'je t’aime', "j'ai cassé un ongle", 'vous pouvez ouvrir ma playlist', 'le système Victor Hugo']) {
    const r = corrigerDictee(juste);
    assert.equal(r.texte, juste);
    assert.deepEqual(r.corrections, []);
  }
  // deux dictées de suite : la règle globale marche les deux fois (lastIndex remis à zéro)
  assert.equal(corrigerDictee('le projet guitare').texte, 'le projet VITA');
  assert.equal(corrigerDictee('le projet guitare').texte, 'le projet VITA');
});

test('les phrases mal entendues du 8/11 éveillent un doute', () => {
  assert.match(douteDictee('tu Pétra') ?? '', /pronom suivi d’un nom/);          // n° 48
  assert.match(douteDictee('mondial où est Petra') ?? '', /début a sans doute été perdu/);   // n° 49
  assert.match(douteDictee('je voudrais savoir le prix de') ?? '', /fin semble coupée/);
  assert.match(douteDictee('le le le globe') ?? '', /répété trois fois/);
  assert.match(douteDictee('tu') ?? '', /petit mot/);
});

test('Whisper (voix D3) : un mot inconnu tout en capitales est un nom mal entendu', () => {
  assert.match(douteDictee('TIPETRA', null) ?? '', /« TIPETRA » est écrit tout en capitales/);   // 9/11, « tu Pétra »
  assert.match(douteDictee('Montre-moi PETRA sur le globe.', null) ?? '', /« PETRA »/);
  for (const juste of ['C’est quoi le système VITA ?', 'la COVID-19 en France', 'un petit jeu en 3D', 'la NASA et l’OTAN', 'ouvre AURORA'])
    assert.equal(douteDictee(juste, null), null, juste);
});

test('la confiance du moteur : basse = doute, inconnue (0 ou null) = rien', () => {
  assert.match(douteDictee('montre-moi Paris', 0.31) ?? '', /confiance 31 %/);
  assert.equal(douteDictee('montre-moi Paris', 0.92), null);
  assert.equal(douteDictee('montre-moi Paris', 0), null);
  assert.equal(douteDictee('montre-moi Paris', null), null);
  assert.equal(douteDictee('montre-moi Paris', CONFIANCE_MINIMALE), null);
});

test('les phrases justes passent sans doute', () => {
  for (const juste of [
    'Situe Pétra', 'Petra sur le globe', 'Où est la Jordanie sur le globe ?', 'Montre-moi où est la Jordanie sur le globe',
    'Dis-moi où est Pétra', 'dis moi où est Pétra', 'Zoome sur la Jordanie', 'Quelle heure est-il ?', 'Raconte-moi l’histoire du Maroc',
    'Parle-m’en', 'donne-le', 'merci', 'météo', 'Bonjour, tu peux me faire un petit jeu ?', 'Lance la commande dir',
    'Et toi ?', 'Tu es là ?', 'Je confirme', 'Comment ça va ?', 'Pourquoi le ciel est bleu',
  ]) assert.equal(douteDictee(juste), null, juste);
});

test('la réponse à « C’est bien ça ? »', () => {
  for (const oui of ['oui', 'Oui.', 'ouais', 'c’est ça', "c'est bien ça", 'exactement', 'voilà', 'oui oui', 'oui envoie', 'd’accord']) {
    assert.equal(reponseConfirmation(oui).reponse, 'oui', oui);
  }
  assert.deepEqual(reponseConfirmation('non'), { reponse: 'non', reste: '' });
  assert.deepEqual(reponseConfirmation('Non, situe Pétra'), { reponse: 'non', reste: 'situe Pétra' });
  assert.deepEqual(reponseConfirmation('non je voulais dire situe Pétra'), { reponse: 'non', reste: 'situe Pétra' });
  assert.deepEqual(reponseConfirmation('Situe Pétra'), { reponse: 'autre', reste: 'Situe Pétra' });
  // « oui » suivi d'une longue phrase : c'est une nouvelle demande, pas une confirmation
  assert.equal(reponseConfirmation('oui alors montre-moi la météo de demain à Lyon').reponse, 'autre');
  assert.equal(reponseConfirmation('nonante-deux').reponse, 'autre');
});
