"""Engine selection (contract §5): deterministic tool, local Ollama or Claude, per privacy mode.

- measure intents -> 'tool' (deterministic template, no LLM); system.diagnose -> 'tool+local'
  when Ollama is up (the LLM only interprets the measured causes).
- actions -> 'tool' (permission handled by the planner / policy); ui.navigate -> 'none'.
- lookup / generative -> local first; Claude only if the mode allows it, Claude is configured,
  budget remains, and (explicit request, or Ollama unavailable). Otherwise Claude is the fallback
  of a local answer that could not be given (local_cannot_answer, the orchestrator adds the step).
- prive: never Claude. equilibre and avance: each send is previewed; the consent may cover the
  whole conversation, never for sensitive data (04/11/2026, ID Studio IA).
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from .intents import intent_kind
from .normalize import normalize

TIERS = ('rapide', 'standard', 'expert')
_DEFAULT_CLOUD_MODELS = {'rapide': 'claude-haiku-5-5', 'standard': 'claude-sonnet-5-5', 'expert': 'claude-opus-5-5'}
# CLAUDE.md « Les trois modes » : Équilibré uses Haiku and Sonnet only; Opus (expert tier) is reserved to Avancé.
TIERS_BY_MODE = {'equilibre': ('rapide', 'standard'), 'avance': TIERS}
EXPERT_RESERVED_NOTE = 'Opus (palier expert) est réservé au mode Avancé : palier standard (Sonnet) utilisé.'


def tier_for_mode(mode: str, tier: str | None) -> tuple[str, str]:
    """(tier allowed in this mode, French note when it had to be lowered). Privé never reaches the cloud."""
    tier = tier if tier in TIERS else 'standard'
    allowed = TIERS_BY_MODE.get(mode)
    if not allowed or tier in allowed:
        return tier, ''
    return 'standard', EXPERT_RESERVED_NOTE
# memory.search is answered by a template (planner: no LLM step), so it is labelled 'tool' too.
_TOOL_ONLY_LOOKUPS = {'files.scan', 'meta.capabilities', 'news.latest', 'memory.search'}
_TOOL_THEN_AI = {'web.search', 'data.analyze'}


@dataclass
class EngineChoice:
    engine: str                 # 'tool' | 'tool+local' | 'tool+cloud' | 'local' | 'cloud' | 'none'
    cloud_tier: str | None = None
    model: str | None = None
    needs_consent: bool = False
    reason: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- user preference

_LOCAL_PREF = re.compile(
    r"\b(?:en local|localement|reste en local|reste local|sans (?:le )?cloud|sans claude|pas de cloud|pas claude|"
    r"n utilise pas (?:le cloud|claude|internet)|ne passe pas par (?:le cloud|claude)|hors ligne|"
    r"avec (?:l ia locale|ollama|qwen|le modele local)|utilise (?:l ia locale|ollama|qwen|le modele local)|"
    r"en prive|rien ne doit sortir|ne sors? rien)\b")
_CLOUD_PREF = re.compile(
    r"\b(?:utilise claude|avec claude|demande a claude|passe par claude|via claude|par claude|"
    r"utilise le cloud|passe par le cloud|via le cloud|avec le cloud|en mode expert|mode expert|"
    r"modele expert|ia la plus puissante|meilleur modele|modele le plus puissant|claude (?:opus|sonnet|haiku|fable))\b")
_EXPERT_PREF = re.compile(r"\b(?:mode expert|modele expert|en expert|ia la plus puissante|meilleur modele|"
                          r"modele le plus puissant|claude opus|claude fable|analyse approfondie|tres approfondi)\b")
_RAPIDE_PREF = re.compile(r"\b(?:reponse rapide|en vitesse|vite fait|rapidement|claude haiku|en bref|"
                          r"version courte)\b")


def detect_engine_preference(text: str) -> dict:
    """{'engine': 'local'|'cloud'|'auto', 'tier': 'rapide'|'expert'|None, 'matched': str}."""
    norm = normalize(text)
    local = _LOCAL_PREF.search(norm)
    if local:
        return {'engine': 'local', 'tier': None, 'matched': local.group(0)}
    cloud = _CLOUD_PREF.search(norm)
    tier = None
    if _EXPERT_PREF.search(norm):
        tier = 'expert'
    elif _RAPIDE_PREF.search(norm):
        tier = 'rapide'
    if cloud:
        return {'engine': 'cloud', 'tier': tier, 'matched': cloud.group(0)}
    return {'engine': 'auto', 'tier': tier, 'matched': ''}


# --------------------------------------------------------------------------- local answer that is not one

# 04/11/2026, ID Studio IA: « si elle peut pas répondre, la elle passe ensuite par toi ». Answers of the local model that
# say so, observed (AURORA, gemma4:e4b, 29/10; Athéna's history imported in AURORA, 06/09):
#   run 15 « Je vous informe que je ne peux pas effectuer de recherche directe sur votre site web, car je n'ai pas
#           la capacité d'accéder à des URLs spécifiques »
#   run 16 « Je vous rappelle que je ne peux pas naviguer directement sur votre chaîne YouTube »
#   run 20 « Je ne peux pas voir votre carte mémoire. »
#   message 180 « Je n'ai pas d'information actualisée en temps réel sur les nouveautés récentes [...] Peltier »
#   message 226 « Je n’ai pas accès en temps réel aux données financières actuelles (prix de l’action Tesla »
# Not refusals: run 33 « j'aurais besoin de savoir de quelle vidéo il s'agit » (a question back), run 21
# « je ne navigue pas de manière libre ; j'utilise des outils spécifiques », and message 196, an answer whose
# caveat « Je ne dispose pas de la définition explicite du sigle » comes at character 161: only the opening of
# the answer is read (the refusals above start before character 70).
# verify.NO_INTERNET_RE is close but narrower (a false « no Internet » claim), so it is not reused here.
_CANNOT_RE = re.compile(
    r"\bje ne (?:peux|pourrai) pas (?:effectuer|naviguer|voir|acceder|lire|consulter|repondre|vous (?:donner|dire|"
    r"fournir)|donner|fournir|trouver|savoir|connaitre|obtenir|verifier)\b|"
    r"\bje n ai pas (?:acces|d information|d informations|de donnees|la capacite|les informations|de connaissances?)\b|"
    r"\bje ne (?:sais|connais) pas\b|\bje ne dispose pas\b|\bje ne suis pas (?:en mesure|capable)\b|"
    r"\bje suis incapable\b|\bil m est impossible\b")
CANNOT_WINDOW = 150


def local_cannot_answer(text: str) -> str:
    """The phrase by which a local answer says it cannot answer ('' when it answers)."""
    found = _CANNOT_RE.search(normalize((text or '')[:CANNOT_WINDOW]))
    return found.group(0) if found else ''


# --------------------------------------------------------------------------- complexity

_COMPLEX_WORDS = re.compile(
    r"\b(?:complexe|complexes|complique|compliquee|compliques|detaille|detaillee|approfondi|approfondie|"
    r"en profondeur|rigoureux|rigoureuse|demonstration|demontre|preuve|architecture|strategie|strategique|"
    r"compare|comparaison|comparatif|critique|technique|techniques|scientifique|scientifiques|juridique|"
    r"algorithme|optimisation|exhaustif|exhaustive|nuance|synthese|these|multi etapes|raisonnement)\b")
_SIMPLE_WORDS = re.compile(r"\b(?:en bref|rapidement|une phrase|court|courte|simple|simplement|vite)\b")


def estimate_complexity(text: str, decision: Any = None) -> dict:
    """Heuristic complexity -> tier. score <= 0 rapide, 1-2 standard, >= 3 expert."""
    norm = normalize(text)
    score = 0
    reasons: list[str] = []
    length = len(text or '')
    if length > 1500:
        score += 2
        reasons.append('texte long')
    elif length > 400:
        score += 1
        reasons.append('texte développé')
    hits = sorted(set(_COMPLEX_WORDS.findall(norm)))
    if hits:
        score += min(2, len(hits))
        reasons.append('vocabulaire exigeant (' + ', '.join(hits[:3]) + ')')
    intent = getattr(decision, 'intent', None) if decision is not None else None
    secondary = list(getattr(decision, 'secondary_intents', None) or []) if decision is not None else []
    if intent in ('doc.analyze', 'task.plan', 'agent.run'):
        score += 1
        reasons.append('analyse, planification ou agent')
    if len(secondary) >= 2:
        score += 1
        reasons.append('demande composée')
    if _SIMPLE_WORDS.search(norm):
        score -= 1
    tier = 'rapide' if score <= 0 else ('standard' if score <= 2 else 'expert')
    return {'score': score, 'tier': tier, 'reasons': reasons}


# --------------------------------------------------------------------------- selection

def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _cloud_model(caps: dict, tier: str) -> str:
    tiers = ((caps or {}).get('cloud') or {}).get('tiers') or {}
    model = (tiers.get(tier) or {}).get('model') if isinstance(tiers.get(tier), dict) else None
    if model:
        return model
    env = os.getenv(f'AURORA_CLOUD_MODEL_{tier.upper()}')
    return env.strip() if env and env.strip() else _DEFAULT_CLOUD_MODELS[tier]


def _local_model(caps: dict) -> str:
    model = ((caps or {}).get('ollama') or {}).get('chat_model')
    if model:
        return model
    env = os.getenv('AURORA_LOCAL_MODEL')
    return env.strip() if env and env.strip() else 'qwen3.5:9b'


def select_engine(decision: Any, *, mode: str, text: str, caps: dict, user_pref: str = 'auto',
                  cloud_tier_pref: str | None = None, sensitivity: str = 'normal',
                  budget_remaining_usd: float = 0.0) -> EngineChoice:
    caps = caps or {}
    intent = _get(decision, 'intent', 'chat.general') or 'chat.general'
    kind = intent_kind(intent)
    mode = mode if mode in ('prive', 'equilibre', 'avance') else 'equilibre'
    ollama_up = bool((caps.get('ollama') or {}).get('up'))
    cloud_configured = bool((caps.get('cloud') or {}).get('configured'))
    local_model = _local_model(caps)

    # Phase 4 (08/11/2026): an object asked in 3D that has no scene is described by a model (local first), then
    # checked and kept by the code (tools/lab.build_generated): a lookup followed by an AI step, like web.search.
    slots = _get(decision, 'slots', None) or {}
    build_scene = intent == 'ui.navigate' and bool(str(slots.get('sujet_3d') or '').strip())
    if build_scene:   # 13/11/2026: the maquettes may have their own local model (Paramètres): the one that is named
        local_model = str((caps.get('ollama') or {}).get('scene_model') or '') or local_model
    # Measures and actions: deterministic tools, no generation.
    if intent == 'ui.navigate' and not build_scene:
        return EngineChoice('none', reason="Navigation dans l'interface : aucun moteur IA n'est nécessaire.")
    if intent == 'system.diagnose':
        if ollama_up:
            return EngineChoice('tool+local', model=local_model,
                                reason="Diagnostic mesuré par les outils ; l'IA locale interprète seulement les "
                                       "causes probables.")
        return EngineChoice('tool', reason="Diagnostic mesuré par les outils (IA locale indisponible : causes "
                                           "présentées sans interprétation).")
    if kind == 'measure':
        return EngineChoice('tool', reason="Valeur mesurée par un outil : réponse déterministe, sans IA.")
    if kind == 'action' and not build_scene:
        return EngineChoice('tool', reason="Action réalisée par un outil local, avec autorisation si elle est "
                                           "sensible.")
    if intent in _TOOL_ONLY_LOOKUPS:
        return EngineChoice('tool', reason="Données lues par un outil : réponse construite sans IA.")

    # Lookups that an LLM summarises, and generative intents.
    with_tool = intent in _TOOL_THEN_AI or build_scene
    pref = detect_engine_preference(text)
    wanted = user_pref if user_pref in ('local', 'cloud') else pref['engine']
    # A Claude tier picked in the composer is only the tier used IF Claude answers: the local model goes first.
    # 29/10/2026 it meant « Claude » (« Standard (Sonnet) » chosen, the local model answered); 04/11/2026 ID Studio IA:
    # « elle veut toujours en premier passer par toi Claude [...] c'est le modèle local en premier que je veux et
    # si elle peut pas répondre, elle passe ensuite par toi ». Runs 23 to 35 (29-31/10) went to Claude that way.
    complexity = estimate_complexity(text, decision)
    # The expert tier (Opus) is used only on explicit request: a complex text alone gets the standard tier.
    auto_tier = 'standard' if complexity['tier'] == 'expert' else complexity['tier']
    tier = cloud_tier_pref if cloud_tier_pref in TIERS else (pref['tier'] or auto_tier)
    if wanted == 'cloud' and tier == 'rapide' and not cloud_tier_pref and not pref['tier']:
        tier = 'standard'   # explicit "use Claude" defaults to the standard tier

    # A complex text no longer goes to Claude first (04/11/2026): the local model tries, Claude is its fallback.
    cloud_reason = ''
    if wanted == 'cloud':
        cloud_reason = 'demande explicite'
    elif wanted != 'local' and not ollama_up:
        cloud_reason = 'IA locale indisponible'

    blocked = ''
    if cloud_reason:
        if mode == 'prive':
            blocked = 'Mode privé : Claude n’est jamais utilisé'
        elif not cloud_configured:
            blocked = 'Claude n’est pas configuré'
        elif budget_remaining_usd <= 0:
            blocked = 'budget cloud du jour épuisé'

    if cloud_reason and not blocked:
        needs_consent = True   # every cloud send is previewed and approved, in every mode (CLAUDE.md)
        consent_txt = ' Votre accord est demandé avant tout envoi (aperçu du texte rédigé, modèle, coût).'
        tier, tier_note = tier_for_mode(mode, tier)
        engine = 'tool+cloud' if with_tool else 'cloud'
        return EngineChoice(engine, cloud_tier=tier, model=_cloud_model(caps, tier), needs_consent=needs_consent,
                            reason=f"Claude, palier {tier} ({cloud_reason}).{consent_txt}"
                                   + (f' {tier_note}' if tier_note else ''))

    if ollama_up:
        engine = 'tool+local' if with_tool else 'local'
        what = "recherche par l'outil puis synthèse locale" if with_tool else 'traitement local'
        if build_scene:
            what = "description de l'objet par l'IA locale, vérifiée par le code avant d'être affichée"
        if wanted == 'local':
            why = f'Moteur local demandé : {what}'
        elif blocked:
            why = f'{blocked} ; {what}'
        else:
            why = f'{what[0].upper()}{what[1:]} d’abord'
            if mode != 'prive' and cloud_configured:
                why += ', Claude en secours si elle ne peut pas répondre'
        return EngineChoice(engine, model=local_model, reason=f"{why} ({local_model}).")

    # No local model available.
    if build_scene:
        why = f"{blocked} ; " if blocked else ''
        return EngineChoice('tool', reason=f"{why}aucune IA disponible pour décrire l'objet : la scène 3D ne peut pas "
                                           "être construite.")
    if with_tool:
        why = f"{blocked} ; " if blocked else ''
        return EngineChoice('tool', reason=f"{why}IA locale indisponible : résultats de l'outil présentés sans "
                                           "synthèse.")
    why = f"{blocked}. " if blocked else ''
    return EngineChoice('none', reason=f"{why}Aucun moteur IA disponible : Ollama est arrêté"
                                       f"{'' if cloud_configured and mode != 'prive' else ' et Claude ne peut pas être utilisé'}.")
