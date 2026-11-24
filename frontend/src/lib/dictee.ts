// La phrase dictée, relue avant d'être envoyée (voix D1, 9/11/2026, demande d’ID Studio IA : « D1, puis D3 », avec les
// réglages d'Athéna). Tout est fait par le code, jamais par un modèle :
//  1. les corrections de dictée vraiment constatées, reprises telles quelles d'Athéna
//     (client/src/lib/vocabulaireDicte.ts) : chacune a sa phrase datée, et toute correction faite est MONTRÉE ;
//  2. le doute : une phrase qui semble mal entendue n'est pas envoyée. AURORA demande d'abord
//     « J'ai compris : « … ». C'est bien ça ? ». Chaque raison vient d'une erreur réelle (tâches 47 à 49 du
//     8/11/2026) ou de la confiance que donne le moteur de reconnaissance lui-même.
// Module pur, sans import : il est testé par node (frontend/tests/dictee.test.mjs).

export interface CorrectionDictee {
  /** Ce que le moteur a écrit. */
  entendu: RegExp;
  /** Par quoi le remplacer. `$1` etc. sont utilisables. */
  corrige: string;
  /** La phrase réelle, et sa date. Sans elle, la règle n'a pas de preuve. */
  observe: string;
}

// Athéna, vocabulaireDicte.ts:39-104 : fautes du moteur de Chrome réellement entendues chez ID Studio IA. Le contexte est
// obligatoire dans chaque règle : « guitare » seul reste « guitare », « je t'aime » seul n'est pas touché.
export const CORRECTIONS_DICTEE: CorrectionDictee[] = [
  {
    entendu: /\b(syst[èe]me|projet|veste|gamme|dispositif)\s+guitare\b/gi,
    corrige: '$1 VITA',
    observe: '10/09/2026 19:46 — « des pistes d\'amélioration pour le système VITA » transcrit « pour le système guitare ».',
  },
  {
    // Le « pour » est avalé par la règle : le laisser donnait « j'aimerais pour savoir ».
    entendu: /\bje\s+t'aime\s+(?:pour\s+)?(?=savoir\b)/gi,
    corrige: "j'aimerais ",
    observe: '10/09/2026 19:14 — « j\'aimerais savoir un peu tes capacités » transcrit « je t\'aime pour savoir un peu tes capacités ».',
  },
  {
    entendu: /\bconfir[eè]me\b/gi,
    corrige: 'confirme',
    observe: '10/09/2026 19:48 — « je confirme » transcrit « je confireme ».',
  },
  {
    // « j'ai cassé un ongle » reste intact : seule la tournure d'ouverture est corrigée.
    entendu: /\b(dans|sur)\s+(un|le|ce|l'autre)\s+ongle\b/gi,
    corrige: '$1 $2 onglet',
    observe: '11/09/2026 17:54 — « ouvre-la dans un onglet » transcrit « dans un ongle ».',
  },
  {
    // Début de phrase seulement, suivi de « ma/la playlist » : « vous pouvez… » reste intact.
    entendu: /^\s*vous\s+(?=(?:ma|la)\s+playlist\b)/i,
    corrige: 'ouvre ',
    observe: '29/09/2026 10:31 — « ouvre ma playlist musique » transcrit « vous ma playlist musique ».',
  },
  {
    // « Victor Hugo » reste intact.
    entendu: /\b(syst[èe]me|projet|veste|dispositif)\s+victor\b(?!\s+hugo)/gi,
    corrige: '$1 VITA',
    observe: '29/09/2026 10:26 — « c\'est quoi le système VITA » transcrit « c\'est quoi le système Victor ».',
  },
];

/** Applique les corrections connues et dit lesquelles ont servi, sous la forme « « guitare » → « VITA » »
 *  (Athéna, corrigerDictee) : une correction silencieuse qui se trompe serait pire que la faute d'origine. */
export function corrigerDictee(texte: string): { texte: string; corrections: string[] } {
  let sortie = String(texte || '');
  const faites: string[] = [];
  for (const c of CORRECTIONS_DICTEE) {
    // `replace` avec un motif global déplace `lastIndex` : remis à zéro, sinon une dictée sur deux échapperait.
    c.entendu.lastIndex = 0;
    const avant = sortie;
    sortie = sortie.replace(c.entendu, c.corrige);
    c.entendu.lastIndex = 0;
    if (sortie !== avant) {
      const trouve = avant.match(new RegExp(c.entendu.source, 'i'))?.[0] ?? '';
      const remplace = sortie.match(new RegExp(c.corrige.replace(/\$\d/g, '\\S+'), 'i'))?.[0] ?? c.corrige;
      faites.push(`« ${trouve.trim()} » → « ${remplace.trim()} »`);
    }
  }
  return { texte: sortie, corrections: faites };
}

/** En dessous, le moteur de reconnaissance doute lui-même de ce qu'il a entendu (0 veut dire « inconnu »).
 *  Valeur de départ, pas encore mesurée sur ta voix : à régler quand on aura des essais. */
export const CONFIANCE_MINIMALE = 0.5;

const PRONOMS = new Set(['je', 'tu', 'il', 'elle', 'on', 'nous', 'vous', 'ils', 'elles']);
// Les mots qui ouvrent normalement une question en « où, quand, comment… » : « dis-moi où », « montre où »…
const OUVERTURES = new Set([
  'et', 'mais', 'alors', 'donc', 'bon', 'ok', 'okay', 'aurora', 'bonjour', 'salut', 'voilà', 'voila', 'là', 'la',
  'ah', 'oh', 'euh', 'hé', 'hey', 'dis', 'montre', 'regarde', 'vois', 'explique', 'indique', 'rappelle', 'demande',
  'cherche', 'trouve', 'vérifie', 'verifie', 'situe', 'sais', 'savoir', 'devine', 'raconte', 'je', 'tu', 'on', 'nous',
  'vous', "c'est", 'c’est', 'depuis', 'jusque', 'puis', 'ensuite', 'maintenant', 'ici', 'toi', 'moi', 'sinon', 'mais',
]);
// Une phrase qui s'arrête sur l'un de ces mots est coupée : « le prix de », « je voudrais le ».
const FINS_COUPEES = new Set([
  'de', 'du', 'des', 'le', 'la', 'les', 'un', 'une', 'à', 'au', 'aux', 'pour', 'et', 'ou', 'sur', 'dans', 'avec',
  'par', 'vers', 'chez', 'mon', 'ma', 'mes', 'ton', 'ta', 'tes', 'son', 'sa', 'ses', 'ce', 'cet', 'cette', 'ces',
  'notre', 'votre', 'leur', 'leurs',
]);
const QUESTIONS = new Set(['où', 'quand', 'comment', 'pourquoi', 'combien']);
// Les sigles et noms écrits en capitales qu'un moteur rend à juste titre (VITA vient du vocabulaire d'Athéna).
const SIGLES = new Set([
  'VITA', 'AURORA', 'ATHÉNA', 'ATHENA', 'ASTRA', 'NASA', 'OTAN', 'UNESCO', 'WIFI', 'HTML', 'JSON', 'HTTP', 'HTTPS',
  'CUDA', 'LINUX', 'COVID', 'SNCF', 'URSS', 'IKEA', 'FIFA', 'UEFA',
]);

const motsDe = (texte: string) => texte.trim().replace(/[.?!…,;:]+$/u, '').trim().split(/\s+/).filter(Boolean);

/** Pourquoi la phrase semble mal entendue, ou null. `confiance` : la plus basse donnée par le moteur pour les
 *  morceaux définitifs (null ou 0 : il n'en a pas donné). */
export function douteDictee(texte: string, confiance: number | null = null): string | null {
  const mots = motsDe(texte);
  if (!mots.length) return null;
  if (confiance !== null && confiance > 0 && confiance < CONFIANCE_MINIMALE) {
    return `le moteur de reconnaissance n’est pas sûr de ce qu’il a entendu (confiance ${Math.round(confiance * 100)} %)`;
  }
  const bas = mots.map(m => m.toLowerCase());
  if (mots.length === 1 && (PRONOMS.has(bas[0]) || FINS_COUPEES.has(bas[0]))) {
    return 'la phrase se réduit à un petit mot';
  }
  // n° 48, 8/11/2026 : « tu Pétra », sans doute « Situe Pétra ». Un pronom suivi d'un nom propre, sans verbe.
  if (mots.length === 2 && PRONOMS.has(bas[0]) && /^\p{Lu}/u.test(mots[1])) {
    return 'un pronom suivi d’un nom, sans verbe : un mot a sans doute été mal entendu';
  }
  // n° 49, 8/11/2026 : « mondial où est Petra », dont le début a été perdu. Un mot isolé avant « où ».
  if (mots.length >= 3 && QUESTIONS.has(bas[1]) && !OUVERTURES.has(bas[0]) && !bas[0].includes('-')) {
    return `la phrase commence par « ${mots[0]} » avant « ${mots[1]} » : son début a sans doute été perdu`;
  }
  if (mots.length >= 2 && FINS_COUPEES.has(bas[bas.length - 1])) {
    return `la phrase s’arrête sur « ${mots[mots.length - 1]} » : la fin semble coupée`;
  }
  for (let i = 2; i < bas.length; i++) {
    if (bas[i] === bas[i - 1] && bas[i] === bas[i - 2]) return `« ${mots[i]} » est répété trois fois de suite`;
  }
  // 9/11/2026, Whisper (voix D3) : « tu Pétra » lu « TIPETRA ». Un mot inconnu tout en capitales est un nom mal
  // entendu ; aucune des 47 vraies phrases dictées par le navigateur n'en contient (seul « 3D », trop court).
  const capitales = mots.map(m => m.replace(/^[«“"'(]+|[»”"').,;:!?…]+$/gu, ''))
    .find(m => /^\p{Lu}{4,}$/u.test(m) && !SIGLES.has(m));
  if (capitales) return `« ${capitales} » est écrit tout en capitales : un nom sans doute mal entendu`;
  return null;
}

/** La réponse à « C'est bien ça ? ». 'oui' : envoyer la phrase. 'non' : ne pas l'envoyer ; `reste` est alors la
 *  demande corrigée s'il y en a une (« Non, situe Pétra »). 'autre' : une nouvelle demande, relue à son tour. */
export function reponseConfirmation(texte: string): { reponse: 'oui' | 'non' | 'autre'; reste: string } {
  const t = texte.trim().replace(/\s+/g, ' ');
  const bas = t.toLowerCase().replace(/’/g, "'");
  // Fin de mot écrite à la main : `\b` ne voit pas « à » ni « ç » comme des lettres (« voilà », « ça »).
  const oui = bas.match(/^(?:oui|ouais|exact|exactement|c'est (?:bien )?(?:ça|ca|correct|bon)|voilà|voila|tout à fait|absolument|d'accord|ok|okay|vas-y|envoie|envoie-la|parfait|correct)(?![\p{L}\p{N}'-])/u);
  if (oui && motsDe(t).length <= 5) return { reponse: 'oui', reste: '' };
  const non = bas.match(/^(?:non|nan|pas du tout|pas ça|annule|laisse tomber|c'est pas ça|ce n'est pas ça|faux)(?![\p{L}\p{N}'-])/u);
  if (non) {
    const reste = t.slice(non[0].length).replace(/^[\s,.;:!?…-]+/, '').replace(/^(?:j'ai dit|je voulais dire|je disais)\s*:?\s*/i, '').trim();
    return { reponse: 'non', reste: motsDe(reste).length >= 2 ? reste : '' };
  }
  return { reponse: 'autre', reste: t };
}
