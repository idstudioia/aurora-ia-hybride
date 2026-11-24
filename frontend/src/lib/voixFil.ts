// La voix au fil de l'écriture (11/11/2026). ID Studio IA : « la rapidité des réponses en vocal, il est un peu long ; sur Athéna
// on a un système qui améliore le temps des réponses ». AURORA ne parlait qu'une fois la réponse entière écrite et
// vérifiée. Athéna parle dès deux phrases écrites, puis phrase par phrase : ses deux règles sont reprises ici telles
// quelles (Athéna payante, client/src/lib/voixDecision.ts, 24/09 et 05/10/2026), avec leurs cas de test.
// Fonctions pures, sans navigateur : tests/voixFil.test.mjs.

/** Un mot court suivi d'un point n'est pas une fin de phrase : « M. », « ex. », une initiale. */
const ABREVIATION = /^(?:[A-Za-zÀ-ÿ]|M|Mme|Dr|Pr|St|etc|ex|cf|env|fig|vol|n°|réf|ref)$/i;
const motAvant = (texte: string, fin: number) => texte.slice(0, fin).split(/\s+/).pop() || '';

/**
 * Le début qu'on peut déjà dire pendant que la réponse s'écrit encore : jusqu'à la fin de la deuxième phrase (60
 * caractères au moins), ou une seule phrase longue (140). null tant qu'il faut attendre.
 *  - une fin de phrase est . ! ? ou … suivi d'un blanc : « 1.5 » et « V7.6 » n'en sont pas ;
 *  - une abréviation courte ne compte pas ;
 *  - un bloc de code ouvert : on attend (on ne lit pas du code à moitié).
 */
export function debutPrononcable(texte: string): string | null {
  if (!texte || texte.includes('```')) return null;
  const fins: number[] = [];
  const motif = /[.!?…]+(?=\s)/g;
  let m: RegExpExecArray | null;
  while ((m = motif.exec(texte))) {
    if (m[0] === '.' && ABREVIATION.test(motAvant(texte, m.index))) continue;
    fins.push(m.index + m[0].length);
    if (fins.length >= 2 && fins[1] >= 60) return texte.slice(0, fins[1]);
  }
  if (fins.length === 1 && fins[0] >= 140) return texte.slice(0, fins[0]);
  return null;
}

/**
 * Jusqu'où le texte en cours est fait de phrases ENTIÈRES : une longueur. Mêmes fins de phrase que
 * `debutPrononcable`, plus le retour à la ligne (titres, listes sans point). Jamais dans un bloc de code, ouvert
 * ou fermé : la voix reçoit chaque phrase finie, jamais une phrase à moitié écrite.
 */
export function finDesPhrasesEcrites(texte: string): number {
  const codes: [number, number][] = [];
  for (let i = texte.indexOf('```'); i >= 0;) {
    const j = texte.indexOf('```', i + 3);
    codes.push([i, j < 0 ? Infinity : j + 3]);
    if (j < 0) break;
    i = texte.indexOf('```', j + 3);
  }
  const dansCode = (k: number) => codes.some(([a, b]) => k >= a && k < b);
  let fin = 0;
  const motif = /[.!?…]+(?=\s)|\n/g;
  let m: RegExpExecArray | null;
  while ((m = motif.exec(texte))) {
    if (dansCode(m.index)) continue;
    if (m[0] === '.' && ABREVIATION.test(motAvant(texte, m.index))) continue;
    fin = m.index + m[0].length;
  }
  return fin;
}

/**
 * Ce qu'il reste à dire de la réponse finale après ce qui a été dit pendant l'écriture. '' : tout est dit. null : la
 * réponse finale n'est pas celle qui s'écrivait (un autre moteur a pris le relais, le texte a été retenu) ; rien de
 * ce qui a été dit ne s'y retrouve, et c'est à l'appelant de décider (AURORA lit alors la vraie réponse).
 * La réponse finale peut porter une note devant ou derrière le texte écrit : on reprend juste après ce texte.
 */
export function resteADire(finale: string, dit: string): string | null {
  const d = dit.trim();
  if (!d) return finale;
  let i = finale.indexOf(d);
  if (i >= 0) return finale.slice(i + d.length);
  const queue = d.slice(-80);   // la fin de ce qui a été dit suffit à retrouver l'endroit (un blanc a pu changer avant)
  if (queue.length >= 40) {
    i = finale.lastIndexOf(queue);
    if (i >= 0) return finale.slice(i + queue.length);
  }
  return null;
}
