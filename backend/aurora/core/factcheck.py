"""Check before answering: the subject of a factual question, so its source is fetched first.

Port of Athéna's verificationService.ts (sujetAVerifier, 06/09/2026). The defect it fixes: asked « Qui a
écrit Le Comte de Monte-Cristo, et en quelle année ? », the model answered « Alexandre Dumas » (right) and
« paru en 1838 » (wrong: 1844-1846), adding « Certitude : ces informations sont bien établies ». A
certainty label declares a confidence, it checks nothing: the answer is to fetch the source first.

Detection is the risky part. Athéna's day showed four times that a pattern too broad does more harm than
good, so the phrasings are narrow and any doubt returns None: better not to check than to check the wrong
subject. The source itself goes through the web search (SearXNG first, tools/web.py), and the check only
enriches the answer: when it brings nothing, the model answers exactly as before (planner / orchestrator).
"""
from __future__ import annotations

import re

# Phrasings that ask for a verifiable fact, and the subject they carry (Athéna's TOURNURES, same order).
# Python's \b knows accented letters (Athéna's 23/09 accent defect cannot happen here).
_FACT_PHRASINGS = [
    # « qui a écrit / inventé / composé / peint / fondé … »
    re.compile(r"\bqui\s+a\s+(?:écrit|ecrit|invent[ée]|compos[ée]|peint|fond[ée]|découvert|decouvert|"
               r"réalis[ée]|realis[ée]|construit)\s+(.{3,80}?)\s*\??$", re.I),
    # « en quelle année … Le Comte de Monte-Cristo » (the work comes later in the sentence)
    re.compile(r"\bqu(?:i|el|elle)\b.{0,40}(?<!\w)(?:écrit|ecrit|invent|découvert|decouvert)\w*(?!\w).*?"
               r"\b(?:de|du|des|d')\s+(.{3,80}?)\s*\??$", re.I),
    # « qu'est-ce que X », « c'est quoi X »; « qu'est-ce qu'un X » has no space after « qu' » (Athéna missed it)
    re.compile(r"\b(?:qu'est[- ]ce\s+que\s+|qu'est[- ]ce\s+qu'\s*|c'est\s+quoi\s+)(?:le\s+|la\s+|les\s+|l'|un\s+|une\s+)?"
               r"(.{3,60}?)\s*\??$", re.I),
    # « parle-moi de X », « qui est X »
    re.compile(r"\b(?:parle[- ]moi\s+d[eu']|qui\s+(?:est|était|etait))\s+(.{3,60}?)\s*\??$", re.I),
    # « quand est né / mort / a eu lieu X »
    re.compile(r"\bquand\s+(?:est|a)\s+\w+\s+(.{3,60}?)\s*\??$", re.I),
    # « en quelle année … X »: the year of an event, not the current year
    re.compile(r"\ben\s+quelle\s+ann[ée]e\s+(?:a[- ]t[- ]il\s+|a[- ]t[- ]elle\s+|est[- ]il\s+|est[- ]elle\s+)?"
               r"(?:été\s+|ete\s+)?\w*\s*(.{3,60}?)\s*\??$", re.I),
]

# Subjects that belong to the user, not to an encyclopaedia (Athéna's A_LUI, plus AURORA itself): the web
# has nothing to say about his patent nor his files; asking it would be useless at best, misleading at worst.
_HIS = re.compile(r"\b(vita|ath[ée]na|aurora|lazar|polyedge|brevet|peltier\s+tec1|esp32|amb82|s\.\s?n\b|imprimante|"
                  r"creality|mon\s+projet|mes?\s+(?:fichiers?|projets?)|(?:mon|le|ton)\s+(?:pc|ordinateur)|"
                  r"ma\s+machine)\b", re.I)

# An encyclopaedia subject is a NOUN, not a clause with a conjugated verb nor a pronoun. Found on 06/09 by
# talking, not by the bench: « Qu'est-ce que tu remarques sur le PC d’ID Studio IA en ce moment ? » was taken for
# a definition, and Wikipedia returned the article on Che Guevara.
_NOT_A_SUBJECT = re.compile(r"^(?:je|tu|il|elle|on|nous|vous|ils|elles|ce|ca|ça|cela)\b|"
                            r"\b(?:remarques?|penses?|crois|vois|fais|dis|sais|veux|peux|mieux)\b", re.I)
_PRONOUN_ONLY = re.compile(r"^(?:[cç]a|cela|toi|moi|nous|ce|cette|quoi|tu|il|elle)$", re.I)

# Invented stories are not documentary research (Athéna's enrichirParLaSource).
_STORY = re.compile(r"\b(?:invente|raconte.{0,25}(?:une|petite|belle) histoire)\b", re.I)


def _clean(subject: str) -> str:
    """Noise left at the end of the question, which would distort the subject."""
    out = re.sub(r"[\"'«»*]", ' ', subject)
    out = re.sub(r"\b(?:exactement|précisément|precisement|au\s+juste|stp|s'il\s+te\s+plaît)\b", '', out, flags=re.I)
    out = re.sub(r'\s*,?\s*et\s+en\s+quelle\s+ann[ée]e.*$', '', out, flags=re.I)
    return re.sub(r'\s+', ' ', out).strip()


def subject_to_verify(message: str) -> str | None:
    """The subject to check before answering, or None when the question does not ask for one."""
    text = str(message or '').strip().replace('’', "'")
    if len(text) < 8 or len(text) > 300:
        return None
    if _HIS.search(text) or _STORY.search(text):
        return None
    for phrasing in _FACT_PHRASINGS:
        match = phrasing.search(text)
        if not match:
            continue
        subject = _clean(match.group(1) or '')
        if len(subject) < 3 or _PRONOUN_ONLY.match(subject) or _NOT_A_SUBJECT.search(subject):
            return None
        return subject
    return None
