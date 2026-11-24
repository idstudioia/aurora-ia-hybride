// Voix D3 : fin de la parole d'après le niveau du micro (src/lib/finParole.ts).
// Lancer : node --experimental-strip-types --test tests/finParole.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { creerFinDeParole, SEUIL_MAX, SEUIL_MIN } from '../src/lib/finParole.ts';

const REGLAGES = { silenceApresParoleMs: 2000, attentePremierMotMs: 8000, maxMs: 120_000 };
const TRAME = 50;

/** Joue une suite de [durée en ms, niveau] et rend le premier verdict qui n'est pas « continue », avec son heure. */
function jouer(morceaux, reglages = REGLAGES) {
  const f = creerFinDeParole(reglages);
  let t = 0;
  for (const [duree, rms] of morceaux) {
    for (let fin = t + duree; t < fin; t += TRAME) {
      const v = f.niveau(rms, t);
      if (v !== 'continue') return { verdict: v, t, aParle: f.aParle(), seuil: f.seuil() };
    }
  }
  return { verdict: 'continue', t, aParle: f.aParle(), seuil: f.seuil() };
}

test('une phrase suivie de 2 s de silence finit l’écoute', () => {
  const r = jouer([[500, 0.002], [1500, 0.08], [3000, 0.002]]);
  assert.equal(r.verdict, 'fin');
  assert.ok(r.aParle);
  assert.ok(r.t >= 1950 + 2000 && r.t <= 1950 + 2100, `fin à ${r.t} ms`);   // dernière trame forte : 1950 ms
});

test('une pause de réflexion plus courte que 2 s ne coupe pas', () => {
  const r = jouer([[400, 0.002], [1000, 0.08], [1500, 0.003], [1000, 0.07], [2500, 0.003]]);
  assert.equal(r.verdict, 'fin');
  assert.ok(r.t >= 3850 + 2000, `coupé trop tôt, à ${r.t} ms`);   // dernière trame forte : 3850 ms
});

test('personne ne parle : rien après 8 s', () => {
  const r = jouer([[9000, 0.003]]);
  assert.equal(r.verdict, 'rien');
  assert.equal(r.aParle, false);
  assert.ok(r.t >= 8000 && r.t < 8100);
});

test('un clic bref n’est pas de la parole', () => {
  const r = jouer([[400, 0.002], [100, 0.3], [9000, 0.002]]);
  assert.equal(r.verdict, 'rien');
});

test('le seuil suit le bruit de fond, dans ses bornes', () => {
  assert.equal(jouer([[300, 0.001], [100, 0.001]]).seuil, SEUIL_MIN);            // pièce calme
  assert.ok(Math.abs(jouer([[300, 0.01], [100, 0.01]]).seuil - 0.03) < 1e-9);   // ventilateur : 3 × le fond
  assert.equal(jouer([[300, 0.2], [100, 0.2]]).seuil, SEUIL_MAX);               // on parlait dès le clic
  // un bruit de fond de ventilateur n'est pas pris pour une parole
  assert.equal(jouer([[300, 0.01], [9000, 0.012]]).verdict, 'rien');
});

test('on parlait dès le clic : la voix est quand même entendue', () => {
  const r = jouer([[300, 0.12], [1200, 0.1], [2500, 0.004]]);
  assert.equal(r.verdict, 'fin');
  assert.ok(r.aParle);
});

test('deux minutes au plus, même si la personne parle encore', () => {
  const r = jouer([[300, 0.002], [200_000, 0.09]]);
  assert.equal(r.verdict, 'fin');
  assert.ok(r.t >= 120_000 && r.t < 120_100);
});
