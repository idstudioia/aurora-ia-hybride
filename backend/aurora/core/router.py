"""Decision engine (contract §5): one place decides the intent of every request.

Order: intent_hint ('explicit') -> rules -> similarity (only when the rules are not
confident, threshold + margin) -> optional local LLM classifier (async path only, short
timeout, JSON-constrained) -> chat.general ('default').

Determinism: rules and similarity are pure functions; LLM answers are cached per
normalised text, so the same request always gets the same decision within a process.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any

from . import rules
from .intents import DEFAULT_INTENT, INTENTS, intent_label, is_intent
from .normalize import extract_slots, normalize

# Decided from tests/bench_routing.py (first measure on 205 sentences: rules alone 190/205, rules + n-gram
# similarity 190/205, 3 recovered, 3 lost). The corpus grows with the real errors (239 sentences on 28/10/2026,
# rules alone 224/239). The similarity router is enabled by default only if it gains without losing: OFF.
DEFAULT_SEMANTIC_ROUTER: bool = False
SEMANTIC_DEFAULT = DEFAULT_SEMANTIC_ROUTER          # name read by config.py
# Live run (same corpus): rules + qwen3.5:4b on low-confidence requests 194/205 (4 recovered,
# 0 lost, 22 calls, p50 ~1 s); rules + granite4.2:3b 188/205 (2 recovered, 4 lost); bge-m3
# similarity 190/205 (4 recovered, 4 lost). The LLM stays opt-in (settings.llm_router): it adds
# about a second on uncertain requests and its answer depends on Ollama being up.
DEFAULT_LLM_ROUTER: bool = False

RULE_CONFIDENT = 0.5      # rule confidence at/above which rules decide alone
LLM_THRESHOLD = 0.5       # below this confidence the LLM classifier (if any) is consulted
LLM_MIN_CONFIDENCE = 0.5  # the classifier must be at least this sure to override
LLM_TIMEOUT_S = 8.0
LLM_CACHE_SIZE = 512


@dataclass
class Decision:
    intent: str
    confidence: float = 0.0
    method: str = 'default'          # 'explicit' | 'rule' | 'semantic' | 'llm' | 'default'
    slots: dict = field(default_factory=dict)
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    secondary_intents: list[str] = field(default_factory=list)
    normalized: str = ''
    duration_ms: float = 0.0
    explanation: str = ''

    def to_dict(self) -> dict:
        data = asdict(self)
        data['alternatives'] = [[name, float(score)] for name, score in self.alternatives]
        return data


def _fmt(value: float) -> str:
    return f'{value:.2f}'.replace('.', ',')


def llm_instructions() -> str:
    lines = [
        "Tu es le routeur d'intentions d'AURORA, assistante personnelle sur un PC Windows.",
        "Choisis l'UNE intention qui correspond à la demande de l'utilisateur.",
        "Repères : « notre / mon projet, mes notes, ce qu'on a dit » = memory.search ; informations publiques,"
        " découvertes = web.search ; « retiens / note que » = memory.save ; « je me souviens » = chat.general ;"
        " mesure directe du PC = system.status ; « pourquoi / lent / analyse le PC » = system.diagnose ;"
        " lister des fichiers = files.scan ; supprimer / nettoyer = files.cleanup ; question de culture"
        " générale, explication, rédaction = chat.general.",
        'Intentions :',
    ]
    for name, spec in INTENTS.items():
        lines.append(f"- {name} : {spec['description']}")
    return '\n'.join(lines)


class OllamaIntentClassifier:
    """Adapter around providers.ollama.classify (lazy import) for a given model."""

    def __init__(self, model: str | None = None, *, timeout: float = LLM_TIMEOUT_S):
        self.model = model
        self.timeout = timeout

    async def classify(self, text: str, labels: list[str], *, instructions: str,
                       timeout: float | None = None) -> tuple[str, float] | None:
        try:
            from aurora.providers.ollama import ollama  # lazy: owned by another module
        except Exception:
            return None
        return await ollama.classify(text, labels, instructions=instructions,
                                     timeout=timeout or self.timeout, model=self.model)


class DecisionEngine:
    def __init__(self, *, llm_classifier: Any = None, use_semantic: bool = True,
                 semantic_threshold: float | None = None, semantic_margin: float | None = None,
                 llm_threshold: float = LLM_THRESHOLD, llm_timeout: float = LLM_TIMEOUT_S):
        self.llm_classifier = llm_classifier
        self.use_semantic = use_semantic
        self.semantic_threshold = semantic_threshold
        self.semantic_margin = semantic_margin
        self.llm_threshold = llm_threshold
        self.llm_timeout = llm_timeout
        self._llm_cache: OrderedDict[str, tuple[str, float] | None] = OrderedDict()
        self._instructions = llm_instructions()

    def configure(self, *, use_semantic: bool | None = None, llm_classifier: Any = ...) -> None:
        if use_semantic is not None:
            self.use_semantic = use_semantic
        if llm_classifier is not ...:
            self.llm_classifier = llm_classifier
            self._llm_cache.clear()

    # ------------------------------------------------------------------ fast path

    def decide_fast(self, text: str, *, intent_hint: str | None = None) -> Decision:
        started = time.perf_counter()
        text = text or ''
        norm = normalize(text)
        note = ''
        if intent_hint:
            if is_intent(intent_hint):
                return self._finish(Decision(
                    intent=intent_hint, confidence=1.0, method='explicit',
                    slots=extract_slots(intent_hint, text, norm), normalized=norm,
                    explanation=f"Intention transmise par l'interface : {intent_label(intent_hint)}."), started)
            note = f' Intention « {str(intent_hint)[:40]} » inconnue : ignorée.'
        if not norm:
            return self._finish(Decision(intent=DEFAULT_INTENT, confidence=0.0, method='default', normalized=norm,
                                         explanation='Demande vide : conversation générale.' + note), started)

        result = rules.analyze(text, norm)
        rule_ok = result.confidence >= RULE_CONFIDENT and (
            result.intent != DEFAULT_INTENT or bool(result.matched.get(DEFAULT_INTENT)))
        if rule_ok:
            decision = Decision(intent=result.intent, confidence=result.confidence, method='rule',
                                alternatives=result.alternatives, secondary_intents=result.secondary_intents,
                                normalized=norm, explanation=self._rule_explanation(result) + note)
            return self._finish(decision, started, text, result.clauses)

        match = None
        if self.use_semantic:
            from . import semantic  # numpy index built on first use
            match = semantic.classify(norm, threshold=self.semantic_threshold, margin=self.semantic_margin)
        if match is not None and match.confident:
            neighbour = next((t for i, t, _ in match.neighbours if i == match.intent), '')
            decision = Decision(
                intent=match.intent, confidence=match.confidence, method='semantic',
                alternatives=match.alternatives(),
                secondary_intents=[s for s in result.secondary_intents if s != match.intent], normalized=norm,
                explanation=(f"Règles incertaines ; demande proche de l'exemple « {neighbour} » → "
                             f"{intent_label(match.intent)} (similarité {_fmt(match.similarity)}).") + note)
            return self._finish(decision, started, text, result.clauses)

        if result.intent != DEFAULT_INTENT:
            decision = Decision(intent=result.intent, confidence=result.confidence, method='rule',
                                alternatives=result.alternatives, secondary_intents=result.secondary_intents,
                                normalized=norm,
                                explanation=self._rule_explanation(result) + ' Confiance faible.' + note)
            return self._finish(decision, started, text, result.clauses)

        decision = Decision(intent=DEFAULT_INTENT, confidence=round(result.score, 4), method='default',
                            alternatives=result.alternatives, secondary_intents=result.secondary_intents,
                            normalized=norm,
                            explanation='Aucune règle ni exemple proche : conversation générale.' + note)
        return self._finish(decision, started, text, result.clauses)

    # ------------------------------------------------------------------ async path

    async def decide(self, text: str, *, intent_hint: str | None = None, context: dict | None = None) -> Decision:
        started = time.perf_counter()
        decision = self.decide_fast(text, intent_hint=intent_hint)
        if decision.method == 'explicit':
            return decision
        if (self.llm_classifier is not None and decision.confidence < self.llm_threshold
                and decision.normalized):
            verdict = await self._classify_llm(text, decision.normalized)
            if verdict is not None:
                label, confidence = verdict
                if label in INTENTS and confidence >= LLM_MIN_CONFIDENCE and label != decision.intent:
                    previous = (decision.intent, decision.confidence)
                    alternatives = [previous] + [a for a in decision.alternatives if a[0] != label]
                    decision = Decision(
                        intent=label, confidence=round(float(confidence), 4), method='llm',
                        slots=extract_slots(label, text, decision.normalized), alternatives=alternatives[:3],
                        secondary_intents=[s for s in decision.secondary_intents if s != label],
                        normalized=decision.normalized,
                        explanation=(f'Règles incertaines ; classifieur local → {intent_label(label)} '
                                     f'(confiance {_fmt(float(confidence))}).'))
                elif label == decision.intent:
                    decision.explanation += f' Confirmé par le classifieur local ({_fmt(float(confidence))}).'
        attachments = (context or {}).get('attachments') if isinstance(context, dict) else None
        # « qui a écrit ce code ? » about the attached file; « que vois-tu sur mon écran ? » with a capture joined (E2)
        if attachments and (decision.intent == DEFAULT_INTENT or (
                decision.intent == 'meta.capabilities' and (rules.asks_answer_author(decision.normalized)
                                                            or rules.SCREEN_ASKED.search(decision.normalized)))):
            decision.intent = 'doc.analyze'
            decision.slots = extract_slots('doc.analyze', text, decision.normalized)
            decision.explanation += ' Document joint : analyse de document.'
        decision.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return decision

    async def _classify_llm(self, text: str, norm: str) -> tuple[str, float] | None:
        if norm in self._llm_cache:
            self._llm_cache.move_to_end(norm)
            return self._llm_cache[norm]
        labels = list(INTENTS)
        classifier = self.llm_classifier
        fn = getattr(classifier, 'classify', classifier)
        verdict = None
        try:
            call = fn(text, labels, instructions=self._instructions, timeout=self.llm_timeout)
            if inspect.isawaitable(call):
                call = await asyncio.wait_for(call, timeout=self.llm_timeout + 1.0)
            if isinstance(call, (tuple, list)) and len(call) == 2:
                verdict = (str(call[0]), float(call[1]))
        except Exception:
            verdict = None
            return None  # failures are not cached: Ollama may come back
        self._llm_cache[norm] = verdict
        while len(self._llm_cache) > LLM_CACHE_SIZE:
            self._llm_cache.popitem(last=False)
        return verdict

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _rule_explanation(result: rules.RuleResult) -> str:
        labels = rules.explain(result)
        base = f'Règles : {labels} → {intent_label(result.intent)}' if labels else \
            f'Règles → {intent_label(result.intent)}'
        text = f'{base} (confiance {_fmt(result.confidence)}).'
        if result.secondary_intents:
            text += ' Demande composée : aussi ' + ', '.join(intent_label(s) for s in result.secondary_intents) + '.'
        return text

    @staticmethod
    def _finish(decision: Decision, started: float, text: str | None = None,
                clauses: list[tuple[str, str]] | None = None) -> Decision:
        if text is not None and not decision.slots:
            decision.slots = compound_slots(decision.intent, text, decision.normalized,
                                            decision.secondary_intents, clauses or [])
        decision.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return decision


def compound_slots(intent: str, text: str, normalized: str, secondary: list[str],
                   clauses: list[tuple[str, str]]) -> dict:
    """Slots of the primary intent; for a compound request they come from its own clause, and the
    slots of secondary clauses are added under keys the primary does not use (e.g. city, query)."""
    if not secondary or not clauses:
        return extract_slots(intent, text, normalized)
    own = next((clause for clause, name in clauses if name == intent), None)
    slots = extract_slots(intent, own or text, None if own else normalized)
    for name in secondary:
        clause = next((c for c, n in clauses if n == name), None)
        if clause is None:
            continue
        for key, value in extract_slots(name, clause).items():
            if key not in slots and value not in (None, '', [], {}):
                if key == 'query' and own and len(str(value).split()) < 3:
                    value = f'{value} {own}'.strip()   # "recherche des solutions" -> + subject clause
                slots[key] = value
    return slots


_ENGINE: DecisionEngine | None = None


def get_engine() -> DecisionEngine:
    """Shared engine (lazy). Uses settings.semantic_router / llm_router when readable."""
    global _ENGINE
    if _ENGINE is None:
        use_semantic, use_llm = DEFAULT_SEMANTIC_ROUTER, DEFAULT_LLM_ROUTER
        try:
            from aurora import config  # lazy: owned by another module
            settings = config.load_settings()
            use_semantic = bool(settings.get('semantic_router', use_semantic))
            use_llm = bool(settings.get('llm_router', use_llm))
        except Exception:
            pass
        _ENGINE = DecisionEngine(llm_classifier=OllamaIntentClassifier() if use_llm else None,
                                 use_semantic=use_semantic)
    return _ENGINE


def reset_engine() -> None:
    global _ENGINE
    _ENGINE = None
