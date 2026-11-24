"""Post-run checks: provenance, measured numbers, capability claims, cloud consent, actions;
and origin(), the real path of an answer shown as a badge (mesuré, source, mémoire, modèle seul).

check() is pure (testable); verify_and_emit() also emits verify.passed / verify.failed.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from ..tasks import events

ACTION_TOOLS = ('files.quarantine', 'process.terminate', 'files.restore')
UNIT = (r'(?:%|°\s?C|°|Go|Mo|Ko|To|GB|MB|KB|TB|GHz|MHz|ms|kb/s|Mb/s|ko/s|Mo/s|km/h|km|m/s|mm|hPa|W|V|'
        r'cœurs?|coeurs?|processus|fichiers?|octets)')
NUMBER = r'[-+]?\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|[-+]?\d+(?:[.,]\d+)?'
MEASURE_RE = re.compile(rf'(?<![\w.,])({NUMBER})\s?{UNIT}(?![\w])', re.I)
# « 1 298,00 € » is one number: its thousands groups are read first (P3, 29/10/2026: « 298,00 » was flagged).
DECIMAL_RE = re.compile(r'(?<![\w.,])((?:[-+]?\d{1,3}(?:[   ]\d{3})+|[-+]?\d+)[.,]\d+)(?![\w.,]*\d)')
ANY_NUMBER_RE = re.compile(NUMBER)
PC_CLAIM_RE = re.compile(r'(cpu|processeur|ram|mémoire vive|memoire vive|disque|gpu|batterie)[^.\n]{0,40}?\d+(?:[.,]\d+)?\s?%|'
                         r'(allum|tourne depuis|démarr|demarr|uptime|en marche)[^.\n]{0,40}?\d+\s?(?:jours?|heures?|h\b|'
                         r'minutes?|semaines?)', re.I)
NO_INTERNET_RE = re.compile(
    r'(n[’\']ai pas (d[’\'])?acc[eè]s [àa] (l[’\'])?internet|je ne (peux|suis) pas (connect|acc[eé]der|aller sur) [^.]{0,15}internet|'
    r'je n[’\']ai pas de connexion|sans acc[eè]s [àa] internet, je)', re.I)
DID_SEARCH_RE = re.compile(
    r'j[’\']ai (cherch|recherch|consult|parcouru|navigu)[^.\n]{0,30}(internet|en ligne|web|wikip)|'
    # 28/10, run n° 8: « j'ai effectué une recherche via les outils d'internet disponibles (Wikipedia) »
    r'j[’\']ai (?:effectué|effectue|fait|lancé|lance|mené|mene|réalisé|realise) (?:une|des|ma|mes|quelques|plusieurs) '
    r'(?:petites? |rapides? |courtes? )?recherches?\b|'
    r'apr[eè]s avoir (?:cherch|recherch|consult)\w*[^.\n]{0,30}(?:internet|en ligne|web|wikip|sources?)|'
    r'(?:selon|d[’\']apr[eè]s) (?:mes|ma) recherches?\b|'
    r'en (?:consultant|cherchant|recherchant|parcourant) [^.\n]{0,25}(?:internet|en ligne|le web|wikip)', re.I)
# Promise of a search that no step performs (Athéna, tests/interdits.ts « promettre-une-recherche », 06/09).
SEARCH_PROMISE_RE = re.compile(r'\bje (?:peux|vais) (?:donc )?(?:re)?chercher (?:maintenant|pour vous|pour toi)|'
                               r'\bje lance (?:la|une) recherche\b|'
                               r'\bje vais (?:lancer|faire|effectuer|mener) (?:une|la|des) recherches?\b', re.I)
# The reverse: a search that DID run, denied. 28/10, real run « Cherche sur le web Le Comte de Monte-Cristo »,
# after SearXNG brought 3 sources: « Pour cette demande, aucune recherche web active n'a été effectuée ».
# « pas de recherche supplémentaire » stays true after one search: not a denial.
DENIED_SEARCH_RE = re.compile(
    r'\baucune recherche (?:web |en ligne |internet |sur internet )?(?:active |réelle |reelle )?'
    r'n[’\']a (?:été|ete) (?:effectu|fait|lanc|réalis|realis|men)\w*|'
    r'\bje n[’\']ai (?:pas (?:pu )?(?:effectu|fait|fai|lanc|men)\w* (?:de |une |aucune )?|'
    r'(?:effectué|fait|lancé|mené) aucune )recherches?\b(?! (?:supplémentaire|complémentaire|additionnelle|autre))|'
    r'\bsans (?:avoir )?(?:effectué|fait|lancé|mené) (?:de |aucune |une )?recherche\b', re.I)
ACTION_CLAIMS = {
    'files.quarantine': re.compile(r'(j[’\']ai|nous avons|a été|ont été|sont été)\s+(supprim|effac|déplac|deplac|'
                                   r'mis en quarantaine|nettoy|vid)', re.I),
    'process.terminate': re.compile(r'(j[’\']ai|nous avons|a été|ont été)\s+(arrêt|arret|ferm|tu[ée]|termin|stopp)',
                                    re.I),
    'system.change': re.compile(r'(j[’\']ai|nous avons|a été|ont été)\s+(désinstall|desinstall|install|modifi|'
                                r'optimis|redémarr|redemarr|désactiv|desactiv|activ|mis à jour)', re.I),
    # Promises of things AURORA cannot do (reminders, messages) or did not do (saving a memory).
    'memory.save': re.compile(r'j[’\']ai (?:bien )?(?:noté|note|retenu|mémorisé|memorise|enregistré)\b', re.I),
    # 16/11/2026 (C3): a reminder, a timer or an alarm is real once the tool « reminder.set » ended (tools/reminders.py);
    # said by a model alone, it is still a promise of something that was not done.
    'reminder.set': re.compile(r'je vous (?:rappellerai|préviendrai|previendrai)|je vais (?:vous )?programmer\b|'
                               r'c[’\']est programmé|(?:rappel|alarme|réveil|minuteur) (?:est |a été )?(?:programmé|'
                               r'réglé|lancé)|j[’\']ai (?:programmé|réglé)\b', re.I),
    # Messages: AURORA has no tool that sends one.
    'reminder': re.compile(r'je vous (?:enverrai|recontacterai)|je vais (?:vous )?envoyer\b|j[’\']ai envoyé\b', re.I),
}
NEGATION_RE = re.compile(r"(n[’']|\bne\b|\bpas\b|\baucun|\brien\b|\bjamais\b|\bsans\b|\bpourrai|\bpeux\b|\bvoulez|\bsouhaitez|\bsi\b)",
                         re.I)


def _to_float(text: str) -> float | None:
    clean = re.sub(r'[   ]', '', text).replace(',', '.')
    try:
        return float(clean)
    except ValueError:
        return None


def numbers_in(value: Any, out: set[float] | None = None) -> set[float]:
    """Every number found in a nested structure (numbers and numbers inside strings)."""
    out = set() if out is None else out
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        out.add(float(value))
    elif isinstance(value, str):
        for match in ANY_NUMBER_RE.findall(value):
            number = _to_float(match)
            if number is not None:
                out.add(number)
    elif isinstance(value, dict):
        for item in value.values():
            numbers_in(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            numbers_in(item, out)
    return out


def _decimals(text: str) -> int:
    clean = re.sub(r'[   ]', '', text).replace(',', '.')
    return len(clean.split('.', 1)[1]) if '.' in clean else 0


def _matches(number: float, places: int, known: Iterable[float], *, scaled: bool = True) -> bool:
    """scaled: also accept unit conversions (×1024, ×1000, ×100…); never derived from 0, 1 or 2,
    which appear everywhere in measured data and would « confirm » almost any number."""
    tolerance = 0.5 * 10 ** (-places) + 1e-9
    for value in known:
        candidates = (value,) if not scaled or abs(value) <= 2 else (
            value, value * 1024, value / 1024, value * 1000, value / 1000, value * 100)
        for candidate in candidates:
            if abs(candidate - number) <= max(tolerance, abs(candidate) * 0.005):
                return True
    return False


_PERCENT_IN_TEXT = re.compile(rf'({NUMBER})\s?%')


def percent_values(value: Any, key: str = '', out: set[float] | None = None) -> set[float]:
    """Measured percentages only: fields named *percent* / *pct* and numbers written « N % » in texts."""
    out = set() if out is None else out
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        if 'percent' in key or 'pct' in key:
            out.add(float(value))
    elif isinstance(value, str):
        for raw in _PERCENT_IN_TEXT.findall(value):
            number = _to_float(raw)
            if number is not None:
                out.add(number)
    elif isinstance(value, dict):
        for k, item in value.items():
            percent_values(item, str(k).lower(), out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            percent_values(item, key, out)
    return out


# extended=True (A3, reviews/etapes-restantes.md): what a model also writes from memory — years, numbers of 100
# and more, amounts and quantities with a word (« paru en 1838 », « 8 milliards », « 324 mètres »).
_YEAR_RE = re.compile(r'(?<![\d.,])(1\d{3}|20\d{2})(?![\d.,]*\d)')
_FIGURE_RE = re.compile(rf'(?<![\w.,])({NUMBER})(\s?(?:€|\$|euros?|dollars?|millions?|milliards?|mètres?|metres?|'
                        r'kilomètres?|kilometres?|kilos?|kg|tonnes?|habitants?|ans|années|siècles?|chapitres?|pages?|'
                        r'volumes?|espèces?|langues?|pays))?(?![\w])', re.I)


def answer_numbers(answer: str, *, extended: bool = False) -> list[tuple[str, float, int]]:
    """Measurement-like numbers of an answer: with a unit, or decimals. Citations [n] and list markers
    ignored. ``extended`` adds years, numbers of 100 and more, amounts and quantities, all in text order
    (small counts such as « 3 conseils » stay out)."""
    text = re.sub(r'\[\d+\]', ' ', answer)
    text = re.sub(r'(?m)^\s*\d+[.)]\s', ' ', text)
    seen: list[tuple[str, float, int]] = []
    spans = []
    for match in MEASURE_RE.finditer(text):
        number = _to_float(match.group(1))
        if number is not None:
            seen.append((match.group(0).strip(), number, _decimals(match.group(1))))
            spans.append(match.span(1))
    for match in DECIMAL_RE.finditer(text):
        if any(a <= match.start(1) < b for a, b in spans):
            continue
        number = _to_float(match.group(1))
        if number is not None:
            seen.append((match.group(1), number, _decimals(match.group(1))))
            spans.append(match.span(1))
    if not extended:
        return seen
    starts = [a for a, _ in spans]
    for pattern in (_YEAR_RE, _FIGURE_RE):
        for match in pattern.finditer(text):
            number = _to_float(match.group(1))
            unit = match.group(2) if pattern is _FIGURE_RE else None
            if number is None:
                continue
            if unit and match.span(1) in spans:      # « 42,9 » seen as a decimal: « 42,9 millions » says more
                index = spans.index(match.span(1))
                if seen[index][0] == match.group(1):
                    seen[index] = (match.group(0).strip(), number, _decimals(match.group(1)))
                continue
            if any(a < match.end(1) and match.start(1) < b for a, b in spans):
                continue
            if pattern is _FIGURE_RE and not (unit or abs(number) >= 100 or _decimals(match.group(1))):
                continue
            seen.append((match.group(0).strip(), number, _decimals(match.group(1))))
            spans.append(match.span(1))
            starts.append(match.start(1))
    return [item for _, item in sorted(zip(starts, seen), key=lambda pair: pair[0])]


def unverified_figures(answer: str, question: str = '', limit: int = 8) -> list[str]:
    """Dates and figures of an answer written by the model ALONE (origin « modele »): nothing measured nor
    sourced backs them, so the interface marks them « non vérifié ». The numbers of the user's own question
    and AURORA's verification note are left out. Distinct, in text order."""
    text = answer.split('\n\nVérification AURORA :')[0]
    asked = numbers_in(question)
    raws = [raw for raw, value, _ in answer_numbers(text, extended=True) if not _matches(value, 0, asked, scaled=False)]
    return list(dict.fromkeys(raws))[:limit]


def _claims(pattern: re.Pattern, text: str, *, skip_negated: bool = True) -> list[str]:
    """Affirmative matches only: a negation / condition earlier in the same clause cancels it."""
    found = []
    for match in pattern.finditer(text):
        if skip_negated:
            window = re.split(r'[.!?;:\n]', text[max(0, match.start() - 40):match.start()])[-1]
            if NEGATION_RE.search(window) or NEGATION_RE.search(match.group(0)):
                continue
        found.append(match.group(0))
    return found


REFERENCE_RE = re.compile(r'\[(\d{1,2})\]')
# A point a document numbers itself, at the start of a line: « 6. … », « 6) … », « (6) … », « 6 - … ». Not « 6 h 30 »,
# « 12 V », « 3,5 m » nor a date: the number must be followed by its mark, then by a space or the end of the line.
_DOC_POINT_RE = re.compile(r'(?m)^[ \t>#*•(-]*(\d{1,2})\s?[.)–—-](?=\s|$)')


def document_references(texts: Iterable[Any]) -> set[int]:
    """What a reference [n] may point to in the documents a model read: a document by its rank (« [1] » for the
    first or the only one) and what a document numbers itself, a point (« 6. … », « 6) … ») or a mark between
    brackets (« [6] »). A document without text gave nothing to cite.

    Bench of 22/11/2026 (reviews/banc-modeles): asked about a joined sheet, models cited its points « [6] » and
    « [7] », or the sheet itself « [1] », and a right answer got « Référence(s) … sans source correspondante ».
    Only where the reference points is checked, not what the point says: the same depth as for a web source."""
    found: set[int] = set()
    for rank, text in enumerate((str(t or '') for t in texts or ()), 1):
        if not text.strip():
            continue
        found.add(rank)
        found.update(int(n) for n in REFERENCE_RE.findall(text))
        found.update(int(n) for n in _DOC_POINT_RE.findall(text))
    return found


def check(*, answer: str, steps: list[dict], caps: dict, mode: str, permissions: dict[int, dict],
          llm_text_used: bool, user_text: str = '', documents: Iterable[Any] = ()) -> dict:
    """steps: [{status, stage, tool, requires_permission, permission_id, result(dict)}].
    documents: the text of each joined document as far as a model read it to write this answer."""
    checks: list[dict] = []
    done = [s for s in steps if s.get('status') == 'done']
    tool_steps = [s for s in done if s.get('stage') == 'tool']
    ran = [s for s in steps if s.get('stage') == 'tool' and s.get('status') in ('done', 'error') and s.get('result')]

    # 1. provenance of every tool result (failed ones included: an error also has an origin)
    missing = [s.get('tool') for s in ran if not str((s.get('result') or {}).get('provenance') or '').strip()]
    failed = len(ran) - len(tool_steps)
    if not ran:
        checks.append({'name': 'provenance', 'ok': True, 'detail': 'Aucun outil utilisé pour cette demande.'})
    elif missing:
        checks.append({'name': 'provenance', 'ok': False,
                       'detail': 'Résultat sans provenance : ' + ', '.join(str(m) for m in missing)})
    else:
        # B7 (reviews/a5-verifications.md): a failed call is named apart, never mixed with what answered.
        failed_tools = [f"{s.get('tool')} ({(s.get('result') or {}).get('provenance')})"
                        for s in ran if s.get('status') != 'done']
        answered = sorted({str((s.get('result') or {}).get('provenance')) for s in tool_steps} - {'', 'None'})
        checks.append({'name': 'provenance', 'ok': True,
                       'detail': f'{len(ran)} résultat(s) d’outil, provenance : ' + (', '.join(answered) or 'aucune')
                       + (f" ; en échec : {', '.join(failed_tools)}" if failed else '')})

    # 2. measured numbers quoted by an LLM must exist in the measured data
    measured = [s.get('result') or {} for s in tool_steps if (s.get('result') or {}).get('ok')]
    if not llm_text_used:
        checks.append({'name': 'mesures', 'ok': True,
                       'detail': 'Réponse composée sans LLM, directement depuis les données des outils.'})
    elif measured:
        raw_data = [m.get('data') for m in measured] + [m.get('summary') for m in measured]
        known = numbers_in(raw_data + [m.get('sources') for m in measured])
        user_numbers = numbers_in(user_text)
        known |= user_numbers
        # A percentage is compared with measured percentages only, without unit conversion.
        percents = percent_values(raw_data) | user_numbers
        unmatched = []
        for raw, number, places in answer_numbers(answer):
            if raw.rstrip().endswith('%'):
                ok = _matches(number, places, percents, scaled=False)
            else:
                ok = _matches(number, places, known)
            if not ok:
                unmatched.append(raw)
        if unmatched:
            checks.append({'name': 'mesures', 'ok': False,
                           'detail': 'Valeurs absentes des données mesurées : ' + ', '.join(dict.fromkeys(unmatched))})
        else:
            checks.append({'name': 'mesures', 'ok': True,
                           'detail': 'Aucune valeur contraire aux mesures détectée.'})
    else:
        user_numbers = numbers_in(user_text)
        claims = []
        for match in PC_CLAIM_RE.finditer(answer):
            raw = re.findall(r'\d+(?:[.,]\d+)?', match.group(0))[-1]
            value = _to_float(raw)
            if value is None or not _matches(value, _decimals(raw), user_numbers):
                claims.append(match.group(0))
        if claims:
            checks.append({'name': 'mesures', 'ok': False,
                           'detail': 'Mesure du PC citée sans outil : ' + ' ; '.join(claims[:3])})
        else:
            checks.append({'name': 'mesures', 'ok': True, 'detail': 'Aucune mesure citée sans donnée d’outil.'})

    # 3. capability claims consistent with the real state
    issues = []
    if caps.get('internet') and _claims(NO_INTERNET_RE, answer, skip_negated=False):
        issues.append('affirme ne pas avoir Internet alors que les outils en ligne sont disponibles')
    web_done = any(s.get('tool') in ('web.search', 'news.latest', 'news.world') for s in tool_steps)
    if not web_done and _claims(DID_SEARCH_RE, answer):
        issues.append('affirme avoir cherché en ligne sans étape de recherche exécutée')
    if not web_done and _claims(SEARCH_PROMISE_RE, answer, skip_negated=False):
        issues.append('promet une recherche qu’aucune étape ne lance')
    if web_done and _claims(DENIED_SEARCH_RE, answer, skip_negated=False):
        issues.append('nie avoir cherché alors qu’une recherche a bien été faite')
    checks.append({'name': 'capacites', 'ok': not issues,
                   'detail': '; '.join(issues) if issues else 'Affirmations cohérentes avec l’état réel.'})

    # 3b. citations [n] of an LLM answer must point to a source actually provided, with some text
    #     (a numbered source with an empty body gave the model nothing to cite); 22/11/2026: a joined document
    #     the model read is one too, by its rank or by a point it numbers itself (document_references)
    if llm_text_used:
        available = 0
        for step in tool_steps:
            if step.get('tool') in ('web.search', 'news.latest'):
                data = (step.get('result') or {}).get('data') or {}
                items = data.get('results') or data.get('items') or []
                if not items and isinstance(data.get('instant'), dict):
                    items = [data['instant']]
                available += sum(1 for i in items if isinstance(i, dict) and str(
                    i.get('text') or i.get('extract') or i.get('summary') or i.get('snippet') or '').strip())
        cited = sorted({int(n) for n in REFERENCE_RE.findall(answer)})
        joined = document_references(documents)
        beyond = [n for n in cited if n < 1 or n > available]          # not one of the numbered sources
        orphans = [n for n in beyond if n not in joined]
        in_document = ', '.join(f'[{n}]' for n in beyond if n in joined)
        checks.append({'name': 'sources', 'ok': not orphans,
                       'detail': (f"Référence(s) {', '.join(f'[{n}]' for n in orphans)} sans source correspondante"
                                  if orphans else 'Aucune référence citée.' if not cited else
                                  f'{len(cited)} référence(s), toutes vers une source fournie'
                                  + (f' ({in_document} : document joint, son rang ou un point qu’il numérote).'
                                     if in_document else '.'))})

    # 4. consent for every cloud step
    cloud = [s for s in done if s.get('stage') == 'cloud_ai']
    problems = []
    for step in cloud:
        if mode == 'prive':
            problems.append(f"étape {step.get('idx')} envoyée à Claude en mode privé")
        elif step.get('requires_permission'):
            perm = permissions.get(int(step.get('permission_id') or 0))
            if not perm or perm.get('status') != 'granted':
                problems.append(f"étape {step.get('idx')} envoyée sans consentement accordé")
    if not cloud:
        checks.append({'name': 'consentement', 'ok': True, 'detail': 'Aucune donnée envoyée à Claude.'})
    else:
        checks.append({'name': 'consentement', 'ok': not problems,
                       'detail': '; '.join(problems) if problems else
                       f'{len(cloud)} envoi(s) à Claude, chacun conforme à la politique du mode.'})

    # 5. no action announced without a finished step (AURORA has no messaging tool at all; a reminder needs its tool)
    done_tools = {s.get('tool') for s in tool_steps if (s.get('result') or {}).get('ok')}
    done_tools |= {s.get('tool') for s in done if s.get('stage') == 'memory'}
    announced = []
    for tool, pattern in ACTION_CLAIMS.items():
        hits = _claims(pattern, answer)
        if hits and tool not in done_tools:
            announced += hits
    checks.append({'name': 'actions', 'ok': not announced,
                   'detail': ('Action annoncée sans étape exécutée : ' + ' ; '.join(announced[:3])) if announced
                   else 'Aucune action annoncée sans étape terminée.'})

    return {'ok': all(c['ok'] for c in checks), 'checks': checks}


ORIGIN_KINDS = ('mesure', 'source', 'memoire', 'modele')


def origin(steps: list[dict], *, llm_text_used: bool, memories_used: int = 0) -> dict | None:
    """Where an answer really comes from, read on the steps that ran (the badge under each answer).

    Athéna's three registers (docs/JOURNAL_ORIGINE.md, 06/09/2026) plus memory (article of 14/10/2026):
    - mesure: a local tool read or computed it on this PC (system, files, time, calculation…);
    - source: a network tool brought back documents with a link (web, weather, news, earthquakes);
    - memoire: AURORA's memory was searched or written, or memories were given to the model;
    - modele: the model wrote with none of the above — where it can be wrong.
    ``written_by`` says when an AI wrote the text over measured or sourced data: the data can be right
    and a reworded sentence wrong. Steps use check()'s shape: {stage, status, tool, result}.
    None when nothing answered: a question back, a refusal, an error, a pending permission."""
    from ..tools.base import REGISTRY  # lazy: owned by another module
    kinds: list[str] = []
    tools: list[str] = []
    sources = 0
    for step in steps:
        if step.get('status') != 'done':
            continue
        result = step.get('result') or {}
        name = str(step.get('tool') or '')
        if step.get('stage') == 'tool' and result.get('ok'):
            if name.startswith('lab.'):
                continue   # 08/11/2026: a 3D scene is an illustration read from data/scenes, never a measure
            spec = REGISTRY.get(name)
            if spec is not None and spec.network:
                linked = [s for s in result.get('sources') or [] if isinstance(s, dict) and s.get('url')]
                if not linked:
                    continue                  # nothing to show: not a source
                sources += len(linked)
                kinds.append('source')
            else:
                kinds.append('mesure')
            tools.append(name)
        elif step.get('stage') == 'memory' and (result.get('hits') or result.get('memory')):
            kinds.append('memoire')
            tools.append(name)
    if llm_text_used and memories_used:
        kinds.append('memoire')
    writer = None
    if llm_text_used:
        writer = 'cloud' if any(s.get('stage') == 'cloud_ai' and s.get('status') == 'done' for s in steps) else 'local'
    if not kinds:
        if not llm_text_used:
            return None
        kinds = ['modele']
    return {'kinds': sorted(set(kinds), key=ORIGIN_KINDS.index), 'written_by': writer,
            'tools': list(dict.fromkeys(tools)), 'sources': sources}


def verify_and_emit(run_id: int, step_id: int | None, *, mods: dict | None = None, **kwargs: Any) -> dict:
    """check() with each check's level (``mods``, the setting verify_mods), then verify.passed / verify.failed."""
    result = apply_levels(check(**kwargs), mods)
    kind = 'verify.passed' if result['ok'] else 'verify.failed'
    label = ('Vérification réussie' if result['ok'] else
             'Vérification : réponse de l’IA retenue' if result['blocked'] else 'Vérification : point(s) à signaler')
    events.emit(kind, label, subject_type='step' if step_id else 'task', subject_id=step_id or run_id,
                run_id=run_id, stage='verify', payload={'checks': result['checks'], 'blocked': result['blocked']},
                level='info' if result['ok'] else 'warn')
    return result


def failure_note(verification: dict) -> str:
    """Short honest note appended to the answer when a check failed."""
    failed = [c for c in verification.get('checks', []) if not c.get('ok')]
    if not failed:
        return ''
    return 'Vérification AURORA : ' + ' ; '.join(c['detail'] for c in failed) + \
        '. Fiez-vous aux données mesurées affichées.'


# A4 (M1): each check of check() is a « mod » in the form of Athéna's modsAtelier.ts (05/10/2026): a name, what
# it guards and a level, chosen in Paramètres › Qualité (setting verify_mods). « alerter » is the behaviour so far:
# the answer is shown with failure_note(). « bloquer »: the AI's text is withheld (Athéna « refus »: not shown as
# valid) and the tools' own summaries, written by code, are shown instead. There is no « off »: these checks
# carry CLAUDE.md rules (measures come from a real tool, nothing goes to Claude without consent).
LEVELS = ('alerter', 'bloquer')
MODS: dict[str, dict[str, str]] = {
    'provenance': {'name': 'Chaque résultat a une origine',
                   'description': 'Tout résultat d’outil dit d’où il vient (psutil, SearXNG, horloge…).'},
    'mesures': {'name': 'Les chiffres viennent des mesures',
                'description': 'Un chiffre cité par l’IA (CPU, RAM, disque…) existe dans les données mesurées.'},
    'capacites': {'name': 'Pas de fausse affirmation sur AURORA',
                  'description': 'L’IA ne prétend pas avoir cherché en ligne sans recherche, ne nie pas une recherche '
                                 'faite et ne dit pas être privée d’Internet quand elle y a accès.'},
    'sources': {'name': 'Chaque référence a sa source',
                'description': 'Une référence [n] de la réponse renvoie à une source réellement fournie.'},
    'consentement': {'name': 'Rien vers Claude sans accord',
                     'description': 'Chaque envoi à Claude respecte le mode et l’accord donné.'},
    'actions': {'name': 'Pas d’action annoncée sans l’avoir faite',
                'description': 'L’IA n’annonce pas avoir supprimé, arrêté, noté ou programmé ce qu’aucune étape n’a fait.'},
}


def levels(chosen: Any = None) -> dict[str, str]:
    """The level of every mod: the user's choice when valid, « alerter » otherwise."""
    chosen = chosen if isinstance(chosen, dict) else {}
    return {name: chosen[name] if chosen.get(name) in LEVELS else 'alerter' for name in MODS}


def apply_levels(result: dict, chosen: Any = None) -> dict:
    """check()'s result, each check with its mod name (``label``) and ``level``; ``blocked``: the failed
    checks set to « bloquer » (the AI's text is then withheld by the orchestrator)."""
    level = levels(chosen)
    checks = [{**c, 'label': MODS.get(c['name'], {}).get('name', c['name']), 'level': level.get(c['name'], 'alerter')}
              for c in result.get('checks', [])]
    return {**result, 'checks': checks, 'blocked': [c['name'] for c in checks if not c['ok'] and c['level'] == 'bloquer']}


def withheld_answer(verification: dict, steps: list[dict]) -> str:
    """Shown instead of the AI's text when a « bloquer » mod failed: why, then what the tools returned
    themselves (their summaries, written by code, never by an AI). Steps use check()'s shape."""
    blocked = set(verification.get('blocked') or [])
    why = ' ; '.join(f"{c.get('label') or c['name']} — {c['detail']}"
                     for c in verification.get('checks', []) if c['name'] in blocked)
    parts = [f'Réponse de l’IA retenue par la vérification AURORA : {why}.']
    summaries = [str(s['result']['summary']).strip() for s in steps
                 if s.get('stage') == 'tool' and s.get('status') == 'done' and (s.get('result') or {}).get('ok')
                 and str((s.get('result') or {}).get('summary') or '').strip()]
    if summaries:
        parts.append('Ce que les outils ont mesuré ou trouvé :\n' + '\n'.join(f'- {x}' for x in dict.fromkeys(summaries)))
    else:
        parts.append('Aucun outil n’a fourni de donnée pour cette demande. Le texte retenu reste consultable sous la '
                     'réponse ; ce contrôle peut passer en « alerter » dans Paramètres › Qualité.')
    return '\n\n'.join(parts)
