"""Routing benchmark: accuracy (rules alone / rules + similarity / + local LLM), per tag,
main confusions, latency p50/p95, determinism. Writes AURORA_HOME/bench_latest.json.

Usage (from backend/):
    python tests/bench_routing.py                 # rules, rules + similarity
    python tests/bench_routing.py --llm           # + Ollama classifier when confidence is low
    python tests/bench_routing.py --llm --llm-all --models qwen3.5:4b,granite4.2:3b
    python tests/bench_routing.py --embed         # + bge-m3 similarity variant
The corpus (tests/data/routing_bench.jsonl) never overlaps the training examples.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurora.core import normalize as normalize_mod  # noqa: E402
from aurora.core import router, semantic  # noqa: E402
from aurora.core.router import RULE_CONFIDENT, DecisionEngine, OllamaIntentClassifier  # noqa: E402

BENCH_PATH = Path(__file__).resolve().parent / 'data' / 'routing_bench.jsonl'
TAGS = ('normal', 'faute', 'voix', 'ambigu', 'contre-exemple')


def load_bench(path: Path | None = None) -> list[dict]:
    rows = []
    with Path(path or BENCH_PATH).open(encoding='utf-8') as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 3)


def _score(rows: list[dict], predicted: list[str], methods: list[str] | None = None) -> dict:
    by_tag: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_intent: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    confusions: Counter = Counter()
    examples: dict[tuple[str, str], list[str]] = defaultdict(list)
    failures = []
    correct = 0
    for i, (row, got) in enumerate(zip(rows, predicted)):
        ok = got == row['intent']
        correct += ok
        by_tag[row.get('tag', 'normal')][0] += ok
        by_tag[row.get('tag', 'normal')][1] += 1
        by_intent[row['intent']][0] += ok
        by_intent[row['intent']][1] += 1
        if not ok:
            confusions[(row['intent'], got)] += 1
            examples[(row['intent'], got)].append(row['text'])
            failures.append({'text': row['text'], 'expected': row['intent'], 'got': got, 'tag': row.get('tag'),
                             'method': methods[i] if methods else None})
    n = len(rows)
    return {
        'correct': correct, 'n': n, 'accuracy': round(correct / n, 4) if n else 0.0,
        'by_tag': {tag: {'correct': c, 'n': t, 'accuracy': round(c / t, 4) if t else 0.0}
                   for tag, (c, t) in sorted(by_tag.items())},
        'by_intent': {name: {'correct': c, 'n': t} for name, (c, t) in sorted(by_intent.items())},
        'confusions': [{'expected': e, 'got': g, 'count': c, 'examples': examples[(e, g)][:3]}
                       for (e, g), c in confusions.most_common(10)],
        'failures': failures,
    }


def _clear_caches() -> None:
    normalize_mod._normalize_cached.cache_clear()
    normalize_mod._fuzzy.cache_clear()


def _latency(engine: DecisionEngine, texts: list[str]) -> dict:
    engine.decide_fast('initialisation')  # builds lazy indexes outside the measurement
    _clear_caches()
    samples = []
    for text in texts:
        start = time.perf_counter()
        engine.decide_fast(text)
        samples.append((time.perf_counter() - start) * 1000)
    return {'p50_ms': _percentile(samples, 0.5), 'p95_ms': _percentile(samples, 0.95),
            'max_ms': round(max(samples), 3) if samples else 0.0, 'mean_ms': round(statistics.fmean(samples), 3)}


def _secondary_recall(rows: list[dict], decisions: list) -> dict:
    expected = found = 0
    for row, decision in zip(rows, decisions):
        for intent in row.get('secondary') or []:
            expected += 1
            found += intent in (decision.secondary_intents or [])
    return {'expected': expected, 'found': found, 'recall': round(found / expected, 4) if expected else None}


def _replay_reports(rows: list, use_semantic: bool) -> dict:
    """A1: the user's real reports (tasks/reports.bench_rows, or an exported file) replayed apart from the
    corpus, never in its score. Like Athéna's non-regression bench (scripts/banc-nonregression.ts): once
    a case is fixed, 100 % is expected; the day it drops, an error came back."""
    cases = [r for r in rows if isinstance(r, dict) and str(r.get('text') or '').strip() and r.get('intent')]
    rules = DecisionEngine(use_semantic=False)
    now = [rules.decide_fast(r['text']).intent for r in cases]
    block: dict = {'n': len(cases), 'ignored': len(rows) - len(cases), 'rules': _score(cases, now)}
    semantic_now: list[str] = []
    if use_semantic:
        engine = DecisionEngine(use_semantic=True)
        semantic_now = [engine.decide_fast(r['text']).intent for r in cases]
        block['rules_semantic'] = _score(cases, semantic_now)
    block['cases'] = [{'run_id': r.get('run_id'), 'text': r['text'], 'expected': r['intent'],
                       'reported': r.get('got_intent'), 'now': got,
                       **({'now_semantic': semantic_now[i]} if semantic_now else {})}
                      for i, (r, got) in enumerate(zip(cases, now))]
    return block


def add_to_corpus(rows: list, path: Path | None = None) -> dict:
    """A1: appends exported reports (« Exporter pour le banc ») to the corpus under the rules that
    test_router.BenchCorpusTests checks: a known intent and tag, a sentence once, never a training example.
    Only the corpus keys are written. Returns {'added': [rows], 'skipped': [{'text', 'reason'}]}."""
    from aurora.core.intents import is_intent
    target = Path(path or BENCH_PATH)
    normalize = normalize_mod.normalize
    known = {normalize(r['text']) for r in load_bench(target)}
    training = {normalize(r['text']) for r in semantic.load_examples()}
    added: list[dict] = []
    skipped: list[dict] = []
    for row in rows:
        row = row if isinstance(row, dict) else {}
        text, intent, tag = str(row.get('text') or '').strip(), row.get('intent'), row.get('tag') or 'normal'
        key = normalize(text) if text else ''
        if not text:
            reason = 'phrase vide'
        elif not is_intent(intent):
            reason = f'intention inconnue : {intent}'
        elif tag not in TAGS:
            reason = f'étiquette inconnue : {tag}'
        elif key in known:
            reason = 'déjà dans le banc'
        elif key in training:
            reason = 'identique à un exemple d’entraînement'
        else:
            known.add(key)
            added.append({'text': text, 'intent': intent, 'tag': tag})
            continue
        skipped.append({'text': text, 'reason': reason})
    if added:
        ends_open = target.read_bytes()[-1:] not in (b'', b'\n')
        with target.open('a', encoding='utf-8', newline='\n') as fh:
            fh.write('\n' if ends_open else '')
            fh.writelines(json.dumps(row, ensure_ascii=False) + '\n' for row in added)
    return {'added': added, 'skipped': skipped}


def _combine(rule_decision, match) -> str:
    """Same policy as DecisionEngine.decide_fast, for an external similarity match."""
    if rule_decision.method == 'rule' and rule_decision.confidence >= RULE_CONFIDENT:
        return rule_decision.intent
    if match is not None and match.confident:
        return match.intent
    return rule_decision.intent


async def _llm_runs(rows: list[dict], rule_decisions: list, models: list[str], llm_all: bool,
                    use_semantic: bool) -> dict:
    out: dict = {}
    try:
        from aurora.providers.ollama import ollama
        up = await ollama.available()
    except Exception as exc:  # provider missing or broken
        return {'ok': False, 'error': f'Fournisseur Ollama indisponible ({type(exc).__name__}).'}
    if not up:
        return {'ok': False, 'error': 'Ollama ne répond pas : banc LLM non exécuté.'}
    labels = list(router.INTENTS)
    instructions = router.llm_instructions()
    for model in models:
        classifier = OllamaIntentClassifier(model)
        result: dict = {'model': model}
        # hybrid: the classifier is only consulted when the fast path is not confident
        engine = DecisionEngine(llm_classifier=classifier, use_semantic=use_semantic)
        hybrid_pred, hybrid_methods, calls, call_ms = [], [], 0, []
        for row, base in zip(rows, rule_decisions):
            start = time.perf_counter()
            decision = await engine.decide(row['text'])
            elapsed = (time.perf_counter() - start) * 1000
            if base.confidence < engine.llm_threshold and base.method != 'explicit':
                calls += 1
                call_ms.append(elapsed)
            hybrid_pred.append(decision.intent)
            hybrid_methods.append(decision.method)
        hybrid = _score(rows, hybrid_pred, hybrid_methods)
        base_pred = [d.intent for d in rule_decisions]
        hybrid['llm_calls'] = calls
        hybrid['llm_latency'] = {'p50_ms': _percentile(call_ms, 0.5), 'p95_ms': _percentile(call_ms, 0.95)}
        hybrid['recovered'] = [r['text'] for r, b, h in zip(rows, base_pred, hybrid_pred)
                               if b != r['intent'] and h == r['intent']]
        hybrid['lost'] = [r['text'] for r, b, h in zip(rows, base_pred, hybrid_pred)
                          if b == r['intent'] and h != r['intent']]
        result['hybrid'] = hybrid
        if llm_all:
            preds, lat, failures = [], [], 0
            for row in rows:
                start = time.perf_counter()
                verdict = await classifier.classify(row['text'], labels, instructions=instructions, timeout=20)
                lat.append((time.perf_counter() - start) * 1000)
                if verdict is None:
                    failures += 1
                preds.append(verdict[0] if verdict else 'chat.general')
            alone = _score(rows, preds)
            alone['errors'] = failures
            alone['latency'] = {'p50_ms': _percentile(lat, 0.5), 'p95_ms': _percentile(lat, 0.95)}
            result['llm_alone'] = alone
        out[model] = result
    out['ok'] = True
    return out


async def _embed_run(rows: list[dict], rule_decisions: list) -> dict:
    emb = semantic.EmbeddingRouter()
    if not await emb.ensure_index():
        return {'ok': False, 'error': 'Embeddings bge-m3 indisponibles (Ollama arrêté ou modèle absent).'}
    preds = []
    for row, base in zip(rows, rule_decisions):
        match = None
        if not (base.method == 'rule' and base.confidence >= RULE_CONFIDENT):
            match = await emb.classify(row['text'])
        preds.append(_combine(base, match))
    result = _score(rows, preds)
    base_pred = [d.intent for d in rule_decisions]
    result['recovered'] = [r['text'] for r, b, h in zip(rows, base_pred, preds) if b != r['intent'] and h == r['intent']]
    result['lost'] = [r['text'] for r, b, h in zip(rows, base_pred, preds) if b == r['intent'] and h != r['intent']]
    result['ok'] = True
    result['model'] = emb.model
    return result


def _fast_model() -> str:
    try:
        from aurora import config
        return config.local_fast_model()
    except Exception:
        return 'qwen3.5:4b'


def _output_path() -> Path:
    try:
        from aurora import config
        return Path(config.bench_latest_path())
    except Exception:
        return semantic.aurora_home() / 'bench_latest.json'


def run_bench(use_semantic: bool = True, use_llm: bool = False, *, llm_models: list[str] | None = None,
              llm_all: bool = False, use_embed: bool = False, write: bool = True,
              path: Path | None = None, reports: list | None = None) -> dict:
    """``reports``: real reported sentences (A1), replayed in report['reports'], apart from the corpus."""
    rows = load_bench(path)
    texts = [r['text'] for r in rows]
    rules_engine = DecisionEngine(use_semantic=False)
    rule_decisions = [rules_engine.decide_fast(t) for t in texts]
    rules_pred = [d.intent for d in rule_decisions]
    report: dict = {
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-4] + 'Z',
        'corpus': {'n': len(rows), 'tags': dict(Counter(r.get('tag', 'normal') for r in rows)),
                   'intents': len({r['intent'] for r in rows})},
        'rules': _score(rows, rules_pred, [d.method for d in rule_decisions]),
        'secondary_intents': _secondary_recall(rows, rule_decisions),
        'latency_rules': _latency(rules_engine, texts),
        'default_semantic_router': router.DEFAULT_SEMANTIC_ROUTER,
    }
    second = [rules_engine.decide_fast(t).intent for t in texts]
    report['determinism'] = {'runs': 2, 'mismatches': sum(a != b for a, b in zip(rules_pred, second))}

    if use_semantic:
        sem_engine = DecisionEngine(use_semantic=True)
        sem_decisions = [sem_engine.decide_fast(t) for t in texts]
        sem_pred = [d.intent for d in sem_decisions]
        sem = _score(rows, sem_pred, [d.method for d in sem_decisions])
        sem['semantic_used'] = sum(1 for d in sem_decisions if d.method == 'semantic')
        sem['recovered'] = [r['text'] for r, b, s in zip(rows, rules_pred, sem_pred)
                            if b != r['intent'] and s == r['intent']]
        sem['lost'] = [r['text'] for r, b, s in zip(rows, rules_pred, sem_pred)
                       if b == r['intent'] and s != r['intent']]
        sem['threshold'] = semantic.get_router().threshold
        sem['margin'] = semantic.get_router().margin
        report['rules_semantic'] = sem
        report['latency_rules_semantic'] = _latency(sem_engine, texts)
        enable = sem['correct'] >= report['rules']['correct'] and not sem['lost'] and bool(sem['recovered'])
        report['recommendation'] = {
            'semantic_router': enable,
            'reason': (f"règles+similarité {sem['correct']}/{len(rows)} contre règles seules "
                       f"{report['rules']['correct']}/{len(rows)} ; {len(sem['recovered'])} récupérée(s), "
                       f"{len(sem['lost'])} perdue(s)") + (' : activation justifiée.' if enable else
                                                           ' : pas de gain sans perte, routeur sémantique désactivé.'),
        }
        report['determinism']['mismatches'] += sum(
            a != b.intent for a, b in zip(sem_pred, [sem_engine.decide_fast(t) for t in texts]))

    if reports is not None:
        report['reports'] = _replay_reports(reports, use_semantic)

    if use_llm or use_embed:
        async def extra() -> None:
            if use_llm:
                models = llm_models or [_fast_model()]
                base_sem = bool(report.get('recommendation', {}).get('semantic_router'))
                base_decisions = rule_decisions if not base_sem else \
                    [DecisionEngine(use_semantic=True).decide_fast(t) for t in texts]
                report['llm'] = await _llm_runs(rows, base_decisions, models, llm_all, base_sem)
            if use_embed:
                report['rules_embeddings'] = await _embed_run(rows, rule_decisions)
        asyncio.run(extra())

    if write:
        out = _output_path()
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            report['written_to'] = str(out)
        except OSError as exc:
            report['written_to'] = None
            report['write_error'] = str(exc)
    return report


def _print(report: dict) -> None:
    n = report['corpus']['n']
    print(f"Banc de routage : {n} phrases, {report['corpus']['intents']} intentions, tags {report['corpus']['tags']}")

    def line(name: str, block: dict) -> None:
        tags = ' | '.join(f"{t} {v['correct']}/{v['n']}" for t, v in block['by_tag'].items())
        print(f"  {name:<28} {block['correct']}/{block['n']} ({block['accuracy'] * 100:.1f} %)  [{tags}]")

    line('Règles seules', report['rules'])
    if 'rules_semantic' in report:
        sem = report['rules_semantic']
        line('Règles + similarité', sem)
        print(f"    similarité utilisée {sem['semantic_used']} fois ; récupérées {len(sem['recovered'])}, "
              f"perdues {len(sem['lost'])}")
        for t in sem['recovered']:
            print(f"      + {t}")
        for t in sem['lost']:
            print(f"      - {t}")
        print(f"  Recommandation : {report['recommendation']['reason']}")
    emb = report.get('rules_embeddings')
    if emb:
        if emb.get('ok'):
            line('Règles + embeddings bge-m3', emb)
            print(f"    récupérées {len(emb['recovered'])}, perdues {len(emb['lost'])}")
        else:
            print(f"  Embeddings : {emb.get('error')}")
    llm = report.get('llm')
    if llm:
        if not llm.get('ok'):
            print(f"  LLM : {llm.get('error')}")
        for model, res in llm.items():
            if not isinstance(res, dict) or 'hybrid' not in res:
                continue
            line(f'+ LLM {model}', res['hybrid'])
            print(f"    appels LLM {res['hybrid']['llm_calls']} (p50 {res['hybrid']['llm_latency']['p50_ms']} ms) ; "
                  f"récupérées {len(res['hybrid']['recovered'])}, perdues {len(res['hybrid']['lost'])}")
            if 'llm_alone' in res:
                line(f'LLM seul {model}', res['llm_alone'])
                print(f"    erreurs {res['llm_alone']['errors']}, latence p50 {res['llm_alone']['latency']['p50_ms']} ms"
                      f" p95 {res['llm_alone']['latency']['p95_ms']} ms")
    lat = report['latency_rules']
    print(f"  Latence decide_fast (règles) : p50 {lat['p50_ms']} ms, p95 {lat['p95_ms']} ms, max {lat['max_ms']} ms")
    if 'latency_rules_semantic' in report:
        lat = report['latency_rules_semantic']
        print(f"  Latence decide_fast (règles+similarité) : p50 {lat['p50_ms']} ms, p95 {lat['p95_ms']} ms")
    sec = report['secondary_intents']
    print(f"  Intentions secondaires attendues trouvées : {sec['found']}/{sec['expected']}")
    print(f"  Déterminisme : {report['determinism']['mismatches']} écart(s)")
    print('  Principales confusions (règles) :')
    for c in report['rules']['confusions']:
        print(f"    {c['expected']} → {c['got']} ×{c['count']} : {c['examples'][0]}")
    replay = report.get('reports')
    if replay is not None:
        print(f"  Signalements rejoués, hors score du corpus : {replay['rules']['correct']}/{replay['n']} "
              f"bien compris (règles seules)" + (f", {replay['ignored']} ligne(s) illisible(s)" if replay['ignored'] else ''))
        for case in replay['cases']:
            if case['now'] != case['expected']:
                print(f"    encore faux : « {case['text']} » attendu {case['expected']}, compris {case['now']}")
    if report.get('written_to'):
        print(f"  Résultats écrits dans {report['written_to']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Banc d’essai du routeur AURORA')
    parser.add_argument('--llm', action='store_true', help='ajoute le classifieur Ollama (confiance faible)')
    parser.add_argument('--llm-all', action='store_true', help='mesure aussi le LLM seul sur tout le corpus')
    parser.add_argument('--models', default='', help='modèles Ollama séparés par des virgules')
    parser.add_argument('--embed', action='store_true', help='variante similarité par embeddings bge-m3')
    parser.add_argument('--no-semantic', action='store_true')
    parser.add_argument('--no-write', action='store_true')
    parser.add_argument('--json', action='store_true', help='affiche le rapport JSON complet')
    parser.add_argument('--reports', metavar='FICHIER',
                        help='rejoue un fichier « Exporter pour le banc » (Paramètres › Qualité), hors score du corpus')
    parser.add_argument('--add-reports', metavar='FICHIER',
                        help='ajoute ce fichier au corpus (doublons et exemples d’entraînement écartés), sans lancer le banc')
    args = parser.parse_args(argv)
    if args.add_reports:
        result = add_to_corpus(load_bench(Path(args.add_reports)))
        print(f"{len(result['added'])} phrase(s) ajoutée(s) à {BENCH_PATH.name}, {len(result['skipped'])} écartée(s)")
        for row in result['added']:
            print(f"  + « {row['text']} » → {row['intent']} ({row['tag']})")
        for row in result['skipped']:
            print(f"  - « {row['text']} » : {row['reason']}")
        return 0
    models = [m.strip() for m in args.models.split(',') if m.strip()] or None
    report = run_bench(use_semantic=not args.no_semantic, use_llm=args.llm, llm_models=models,
                       llm_all=args.llm_all, use_embed=args.embed, write=not args.no_write,
                       reports=load_bench(Path(args.reports)) if args.reports else None)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print(report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
