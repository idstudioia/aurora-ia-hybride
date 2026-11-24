// Tolerant reader for the routing bench result (/api/quality/latest, /api/quality/bench).
// Only re-arranges what the bench wrote; unknown layouts are reported as such by the caller.
import { getPath, isObj, num, pickNum, pickStr, type Json } from '../../lib/tolerant';

export interface BenchCategory { key: string; label: string; accuracy: number | null; n: number | null }
export interface BenchMethod {
  key: string; label: string; accuracy: number | null; n: number | null;
  p50: number | null; p95: number | null; categories: BenchCategory[];
}
export interface BenchConfusion { expected: string; predicted: string; count: number | null; method: string | null; text: string | null }
export interface BenchReplayCase {
  runId: number | null; text: string; expected: string;
  /** Understood when it was reported; now: understood today (rules alone), nowSemantic: with similarity. */
  reported: string | null; now: string; nowSemantic: string | null;
}
/** A1: the « Signaler » reports replayed by the bench, apart from the corpus score (bench_routing._replay_reports). */
export interface BenchReplay {
  /** Set when the reports could not be read: the bench ran without them. */
  error: string | null;
  n: number; ignored: number;
  rules: { correct: number; n: number } | null;
  semantic: { correct: number; n: number } | null;
  cases: BenchReplayCase[];
}
export interface BenchView {
  methods: BenchMethod[]; confusions: BenchConfusion[]; total: number | null;
  at: string | null; machine: string | null; note: string | null; defaultSemantic: boolean | null;
  /** Secondary measurements stated by the bench (composed intents, determinism…), already in French. */
  extras: string[];
  /** null when this bench run did not replay the reports (command line, older service). */
  replay: BenchReplay | null;
}

const META_KEYS = new Set(['n', 'total', 'count', 'size', 'ran_at', 'created_at', 'timestamp', 'date', 'generated_at', 'machine', 'host',
  'secondary_intents', 'determinism', 'recommendation', 'reports',
  'confusions', 'top_confusions', 'errors', 'mistakes', 'by_category', 'categories', 'corpus', 'version', 'note', 'notes', 'llm', 'path']);

export const pct = (v: number | null) => (v === null ? null : v <= 1.0000001 ? v * 100 : v);

const METHOD_LABELS: [RegExp, string][] = [
  [/llm/i, 'Règles + sémantique + LLM local'],
  [/(rules?[^a-z]*(\+|_|and|et)[^a-z]*sem|hybrid|combined)/i, 'Règles + similarité sémantique'],
  [/^sem/i, 'Similarité sémantique seule'],
  [/(rule|regle|règle)/i, 'Règles seules'],
  [/(engine|full|decision|moteur)/i, 'Moteur complet'],
];
export const methodLabel = (k: string) => METHOD_LABELS.find(([re]) => re.test(k))?.[1] ?? k;

const CAT_LABELS: [RegExp, string][] = [
  [/(faute|typo|misspel|orth)/i, 'Fautes'], [/(voi|voice|asr|speech|oral)/i, 'Voix'], [/ambig/i, 'Ambigu'],
  [/(contre|counter|negative|adversar|hard_neg)/i, 'Contre-exemples'], [/(clean|standard|normal|nominal|simple)/i, 'Formulations normales'],
  [/(compos|multi)/i, 'Composées'],
];
export const categoryLabel = (k: string) => CAT_LABELS.find(([re]) => re.test(k))?.[1] ?? k;

const accOf = (v: unknown) => (typeof v === 'number' ? pct(v) : pct(pickNum(v, ['accuracy', 'acc', 'exactitude', 'accuracy_pct', 'score', 'overall.accuracy', 'overall'])));
const nOf = (v: unknown) => pickNum(v, ['n', 'total', 'count', 'size', 'cases']);

function readCategories(v: unknown): BenchCategory[] {
  if (!isObj(v)) return [];
  return Object.entries(v).map(([k, c]) => ({ key: k, label: categoryLabel(k), accuracy: accOf(c), n: nOf(c) }))
    .filter(c => c.accuracy !== null);
}

function readMethod(key: string, m: unknown, topCats: Json | null, latency: unknown = null): BenchMethod | null {
  const accuracy = accOf(m);
  if (accuracy === null) return null;
  const cats = readCategories(isObj(m) ? (m.by_category ?? m.by_tag ?? m.categories ?? m.per_category) : null);
  return {
    key, label: methodLabel(key), accuracy, n: nOf(m),
    p50: pickNum(m, ['p50_ms', 'latency_p50_ms', 'latency.p50_ms', 'latency.p50', 'latency_ms.p50', 'p50', 'median_ms']) ?? pickNum(latency, ['p50_ms', 'p50']),
    p95: pickNum(m, ['p95_ms', 'latency_p95_ms', 'latency.p95_ms', 'latency.p95', 'latency_ms.p95', 'p95']) ?? pickNum(latency, ['p95_ms', 'p95']),
    categories: cats.length ? cats : readCategories(topCats?.[key]),
  };
}

function readConfusion(c: unknown, method: string | null): BenchConfusion | null {
  if (Array.isArray(c) && c.length >= 2) return { expected: String(c[0]), predicted: String(c[1]), count: num(c[2]), method, text: null };
  if (!isObj(c)) return null;
  const pair = pickStr(c, ['pair']);
  if (pair && /(->|→|>)/.test(pair)) {
    const [a, b] = pair.split(/\s*(?:->|→|>)\s*/);
    return { expected: a, predicted: b ?? '?', count: nOf(c), method, text: null };
  }
  const expected = pickStr(c, ['expected', 'gold', 'true', 'label', 'intent', 'attendu']);
  const predicted = pickStr(c, ['predicted', 'got', 'pred', 'actual', 'obtenu', 'result']);
  if (!expected || !predicted) return null;
  const examples = Array.isArray(c.examples) ? c.examples.filter((x): x is string => typeof x === 'string') : [];
  return { expected, predicted, count: nOf(c), method, text: pickStr(c, ['text', 'phrase', 'input', 'example']) ?? examples[0] ?? null };
}

const scoreOf = (v: unknown) => {
  const correct = pickNum(v, ['correct']);
  const n = pickNum(v, ['n']);
  return correct !== null && n !== null ? { correct, n } : null;
};

function readReplay(v: unknown): BenchReplay | null {
  if (!isObj(v)) return null;
  const cases = (Array.isArray(v.cases) ? v.cases : []).filter(isObj).map(c => ({
    runId: num(c.run_id), text: pickStr(c, ['text']) ?? '', expected: pickStr(c, ['expected']) ?? '',
    reported: pickStr(c, ['reported']), now: pickStr(c, ['now']) ?? '', nowSemantic: pickStr(c, ['now_semantic']),
  }));
  return { error: pickStr(v, ['error']), n: num(v.n) ?? cases.length, ignored: num(v.ignored) ?? 0,
    rules: scoreOf(v.rules), semantic: scoreOf(v.rules_semantic), cases };
}

export function parseBench(raw: unknown): BenchView | null {
  if (!isObj(raw)) return null;
  const b: Json = isObj(raw.result) ? raw.result as Json : isObj(raw.data) && !raw.methods ? raw.data as Json : raw;
  const topCats = isObj(b.by_category) ? b.by_category as Json : null;
  const methods: BenchMethod[] = [];
  const holder = b.methods ?? b.results ?? b.by_method ?? b.scores ?? b;
  if (Array.isArray(holder)) {
    holder.forEach((m, i) => {
      const key = pickStr(m, ['method', 'name', 'label', 'id']) ?? `méthode ${i + 1}`;
      const r = readMethod(key, m, topCats); if (r) methods.push(r);
    });
  } else if (isObj(holder)) {
    for (const [k, m] of Object.entries(holder)) {
      if (META_KEYS.has(k) || k.startsWith('latency_') || (!isObj(m) && typeof m !== 'number')) continue;
      const r = readMethod(k, m, topCats, b[`latency_${k}`]); if (r) methods.push(r);
    }
  }
  const confusions: BenchConfusion[] = [];
  const topConf = b.confusions ?? b.top_confusions;
  if (Array.isArray(topConf)) topConf.forEach(c => { const r = readConfusion(c, null); if (r) confusions.push(r); });
  if (!confusions.length && isObj(holder)) {
    for (const [k, m] of Object.entries(holder)) {
      const list = isObj(m) ? (m.confusions ?? m.top_confusions) : null;
      if (Array.isArray(list)) list.forEach(c => { const r = readConfusion(c, methodLabel(k)); if (r) confusions.push(r); });
    }
  }
  if (!methods.length) return null;
  const sem = b.semantic_default ?? b.semantic_enabled ?? b.semantic_router_default ?? b.default_semantic_router ?? getPath(b, 'recommendation.semantic_router');
  const extras: string[] = [];
  const sec = isObj(b.secondary_intents) ? b.secondary_intents : null;
  if (sec && num(sec.expected) !== null && num(sec.found) !== null) extras.push(`Intentions composées détectées : ${sec.found} sur ${sec.expected}`);
  const det = isObj(b.determinism) ? b.determinism : null;
  if (det && num(det.runs) !== null && num(det.mismatches) !== null) extras.push(`Déterminisme : ${det.mismatches} différence(s) sur ${det.runs} passages identiques`);
  return {
    methods, confusions: confusions.slice(0, 8),
    total: nOf(b) ?? pickNum(b, ['corpus.n', 'corpus.size']) ?? methods.find(m => m.n !== null)?.n ?? null,
    at: pickStr(b, ['ran_at', 'created_at', 'timestamp', 'generated_at', 'date']),
    machine: pickStr(b, ['machine', 'host', 'platform', 'cpu']),
    note: pickStr(b, ['note', 'notes', 'conclusion', 'verdict', 'recommendation.reason']),
    defaultSemantic: typeof sem === 'boolean' ? sem : null,
    extras,
    replay: readReplay(b.reports),
  };
}
