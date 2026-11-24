"""Too-vague search subjects: AURORA asks for a precision instead of searching at random.

Ported from Athéna (server/services/sujetVague.ts, 08/10/2026) with its test cases. Observed there:
« une petite recherche sur les derniers modèles d'hier » brought back Renault, Peugeot and the VW Golf.

Vague = once the small words, the time words (« derniers », « hier »…) and ONE catch-all word
(« modèles », « version », « type »…) are removed, nothing is left. Two words or more: a real subject.
The user's answer (« d'IA », « de voitures ») completes the pending subject on the next turn,
without any model (subject_after_precision); the pending subject is kept in the message meta.
"""
from __future__ import annotations

import re
import unicodedata

from .normalize import strip_accents

SMALL = frozenset('le la les l un une des du de d sur en et a au aux ce cet cette ces mon ma mes quel quels '
                  'quelle quelles plus tout tous'.split())
TIME = frozenset('dernier derniers derniere dernieres nouveau nouveaux nouvel nouvelle nouvelles recent recents '
                 'recente recentes hier aujourd hui moment actuel actuels actuelle actuelles semaine mois annee jour '
                 'sorti sortis sortie sorties meilleur meilleurs meilleure meilleures'.split())

# The catch-all word, and the question that goes with it.
CATCH_ALL: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({'modele', 'modeles', 'model', 'models'}),
     'Des modèles de quoi ? Par exemple : d’IA, de voitures, de téléphones.'),
    (frozenset({'version', 'versions'}), 'La version de quoi ? Par exemple : d’Ollama, de Windows, d’une application.'),
    (frozenset({'type', 'types'}), 'Le type de quoi ? Précisez le sujet.'),
    (frozenset({'nouveaute', 'nouveautes', 'actualite', 'actualites', 'news', 'info', 'infos', 'information',
                'informations'}), 'Sur quel sujet ? Par exemple : l’IA, l’espace, la technologie.'),
    (frozenset({'truc', 'trucs', 'chose', 'choses', 'produit', 'produits'}), 'De quoi parlez-vous exactement ?'),
)
_CATCH_ALL_WORDS = frozenset().union(*(words for words, _ in CATCH_ALL))

_LEADING_FILLER = re.compile(r"^(?:c['’]?est|ceux|celles?|plut[ôo]t|euh|ben|bah)[\s,]+", re.I)
_REFUSAL = re.compile(r'^(?:non|laisse|annule|rien|pas la peine|oublie|stop)\b', re.I)


def _words(text: str) -> list[str]:
    return [w for w in re.split(r'[^a-z0-9.:]+', re.sub(r"['’]", ' ', strip_accents(text).lower())) if w]


def vague_question(subject: str) -> str | None:
    """The question to ask when the subject is too vague; None when it is precise enough to search."""
    rest = [w for w in _words(subject) if w not in SMALL and w not in TIME]
    if len(rest) != 1:   # nothing left: nothing to ask; two words or more: a real subject
        return None
    return next((question for words, question in CATCH_ALL if rest[0] in words), None)


def subject_after_precision(message: str, pending: str) -> str | None:
    """Next turn: the user answers the question (« d'IA », « de voitures »). Returns the completed
    subject, or None for a refusal or a long sentence (then it is a new request)."""
    pending = unicodedata.normalize('NFC', pending or '').strip()
    if not pending:
        return None
    precision = _LEADING_FILLER.sub('', unicodedata.normalize('NFC', (message or '').strip()))
    precision = re.sub(r'[.!?\s]+$', '', precision)
    if not precision or len(_words(precision)) > 8 or _REFUSAL.match(precision):
        return None
    # « des modèles d'IA »: the answer says everything again, it becomes the subject.
    if vague_question(precision) is None and any(w in _CATCH_ALL_WORDS for w in _words(precision)):
        return precision
    # « d'IA »: the precision goes right after the catch-all word (« les derniers modèles d'IA d'hier »).
    folded = strip_accents(pending).lower()
    key = next((w for w in _words(pending) if w in _CATCH_ALL_WORDS), None)
    where = re.search(rf'\b{key}\b', folded) if key and len(folded) == len(pending) else None
    if where:
        return f'{pending[:where.end()]} {precision}{pending[where.end():]}'
    return f'{pending} {precision}'
