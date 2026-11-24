"""Recall of past conversations (E5, 29/10/2026): the exchanges are READ from the messages table, never generated.

Ported from Athéna's rappelConversations.ts and conversationEnCours.ts (06-17/10/2026), with their measured rules:
  - « mes dernières conversations » (no subject): the list of the last conversations, read in the base;
  - « on avait parlé de X », « retrouve notre conversation sur X »: the exchanges where X appears, with their date;
  - « tu te souviens de mon X » is careful: a memory that holds every word of X answers instead, and only
    exchanges whose QUESTION holds every word count (« ma playlist » gave 44 unrelated exchanges without it);
  - a question about the CURRENT conversation (« de quoi on a parlé ») is not a recall: that conversation is read;
  - the current conversation is left out, and a recall request is never itself a found exchange.
An exchange imported from Athéna is cited as Athéna's answer, never as a fact checked by AURORA.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime

from .. import db
from . import search

_TIME = (r"(?:\s+(?:tout\s+[àa]\s+l['’]\s*heure|hier|avant[-\s]hier|ce\s+matin|la\s+derni[èe]re\s+fois|"
         r"l['’]\s*autre\s+(?:jour|fois)|la\s+semaine\s+derni[èe]re|avant))*")

WITHOUT_SUBJECT = [
    re.compile(r"\b(?:derni[èe]res?|pr[ée]c[ée]dentes?|anciennes?|autres?)\s+conversations?\b", re.I),
    re.compile(r"\bconversations?\s+(?:pr[ée]c[ée]dentes?|pass[ée]es?|d['’]\s*hier|d['’]\s*avant|"
               r"d['’]\s*avant[-\s]hier)\b", re.I),
    re.compile(r"\bderniers?\s+sujets?\b[^.?!]{0,60}\b(?:abord|parl|discut|souviens|rappelles)", re.I),
]
WITH_SUBJECT = [
    re.compile(r"\bon\s+(?:en\s+)?(?:avait|a)\s+(?:d[ée]j[àa]\s+)?(?:parl[ée]|discut[ée])" + _TIME +
               r"\s+(?:(?:des|du|de)\s+|d['’]\s*)(.+)$", re.I),   # « des » before « de » (« de|des » cut « s modules »)
    re.compile(r"\bce\s+qu['’]\s*on\s+(?:avait|a)\s+(?:dit|vu|d[ée]cid[ée]|trouv[ée])" + _TIME +
               r"\s+(?:sur|à\s+propos\s+de|a\s+propos\s+de|pour)\s+(.+)$", re.I),
    re.compile(r"\bqu['’]\s*est[-\s]ce\s+qu['’]\s*on\s+(?:avait|a)\s+(?:dit|vu|d[ée]cid[ée])" + _TIME +
               r"\s+(?:sur|à\s+propos\s+de|a\s+propos\s+de)\s+(.+)$", re.I),
    re.compile(r"\b(?:retrouve|cherche|recherche|regarde)[-\s]+(?:moi\s+)?(?:dans\s+)?(?:notre|nos|la|les|une)\s+"
               r"(?:anciennes?\s+|derni[èe]res?\s+|pr[ée]c[ée]dentes?\s+)?(?:conversations?|discussions?|[ée]changes?)"
               r"(?:\s+(?:pass[ée]e?s|pr[ée]c[ée]dentes?|d['’]\s*hier))?\s+(?:sur|à\s+propos\s+de|a\s+propos\s+de|de|du|"
               r"des|o[ùu]\s+on\s+(?:a|avait)\s+parl[ée]\s+de)?\s*(.+)$", re.I),
]
REMEMBER_MY = [
    re.compile(r"\b(?:tu\s+te\s+(?:souviens|rappelles)|te\s+(?:souviens|rappelles)[-\s]tu|rappelle[-\s]toi)\s+"
               r"(?:de\s+|d['’]\s*)?((?:mon|ma|mes|notre|nos)\s+.+)$", re.I),
    re.compile(r"\b(?:tu\s+te\s+(?:souviens|rappelles)|te\s+(?:souviens|rappelles)[-\s]tu)\s+(?:(?:des|du|de)\s+|d['’]\s*)"
               r"(.+?)\s+(?:dont\s+on\s+(?:a|avait)\s+(?:d[ée]j[àa]\s+)?parl[ée]|qu['’]\s*on\s+(?:a|avait)\s+(?:vu|dit))\b",
               re.I),
]
# conversationEnCours.ts: the question is about THIS conversation, which is read, not recalled.
CURRENT_CONVERSATION = [
    re.compile(r"r[ée]sum[ée]r?\b[^.?!]{0,30}?\bce\s+qu['’]\s*on\s+(?:vient\s+de\s+(?:dire|voir)|a\s+(?:dit|vu|fait))",
               re.I),
    # 22/11/2026: « récapitule notre discussion » too, and never « notre conversation d'hier » (a past one).
    re.compile(r"(?:r[ée]sum[ée]r?|r[ée]capitul\w*)\b[^.?!]{0,30}?\b(?:notre|cette|la)\s+(?:conversation|discussion|"
               r"[ée]change)\b(?!\s+(?:d['’]hier|d['’]avant|pr[ée]c[ée]dente|pass[ée]e))", re.I),
    # 22/11/2026 (banc de conversation, tour 14) : « résume ce dont on a parlé depuis le début », « fais-moi un résumé
    # de ce qu'on s'est dit ». Ces phrases partaient chercher un souvenir (« on a parlé ») et n'en trouvaient aucun.
    re.compile(r"(?:r[ée]sum[ée]r?|r[ée]capitul\w*|fais(?:[-\s]moi)?\s+(?:un\s+|le\s+)?(?:r[ée]sum[ée]|point|"
               r"r[ée]capitulatif|bilan))\b[^.?!]{0,40}?\bce\s+(?:dont\s+|qu['’]\s*)(?:on|nous)\s+(?:a|avons|s['’]\s*est|"
               r"nous\s+sommes)\s+(?:parl[ée]|dit|discut[ée]|vu|[ée]chang[ée])", re.I),
    re.compile(r"\bma\s+(?:premi[èe]re|derni[èe]re|pr[ée]c[ée]dente)\s+question\b", re.I),
    re.compile(r"\bde\s+quoi\s+(?:on|nous)\s+(?:a|avons)\s+parl[ée]", re.I),
    re.compile(r"\bce\s+que\s+je\s+(?:viens\s+de\s+te\s+dire|t['’]ai\s+dit\s+(?:avant|plus\s+haut|tout\s+[àa]\s+l['’]heure))",
               re.I),
    re.compile(r"\bce\s+que\s+tu\s+viens\s+de\s+(?:me\s+)?(?:dire|parler|expliquer|raconter|proposer|r[ée]pondre)\b", re.I),
    re.compile(r"\b(?:premi[èe]re?|derni[èe]re?)\s+(?:message|question|chose)\b[^.?!]{0,50}?\b(?:cette|la|notre)\s+"
               r"(?:conversation|discussion)\b(?!\s+(?:d['’]hier|d['’]avant|pr[ée]c[ée]dente|pass[ée]e))", re.I),
]
_CUT = re.compile(r"\s*[,;:?!]|\s+(?:et\s+)?(?:tu\s+te\s+(?:souviens|rappelles)|te\s+(?:souviens|rappelles)[-\s]tu|"
                  r"est[-\s]ce\s+que)\b", re.I)
_TRAILING_TIME = re.compile(_TIME + r"\s*[?!.]*\s*$", re.I)
_FOLLOWED = re.compile(r"[,;:]\s*\S|\s(?:tu\s+te\s+(?:souviens|rappelles)|te\s+(?:souviens|rappelles)[-\s]tu|"
                       r"est[-\s]ce\s+que)\b", re.I)
LONG_QUESTION = 600   # a pasted document counts half (measured in Athéna, 06/10)


def about_current_conversation(text: str) -> bool:
    return any(p.search(text or '') for p in CURRENT_CONVERSATION)


def _clean_subject(subject: str) -> str:
    subject = _CUT.split(subject)[0]
    subject = _TRAILING_TIME.sub('', subject)
    return re.sub(r'[?!.\s]+$', '', subject).strip()


def recall_request(text: str) -> dict | None:
    """{'mode': 'subject', 'subject', 'careful'} | {'mode': 'list'} | None (not a recall of past conversations)."""
    message = (text or '').strip()
    if about_current_conversation(message):
        return None
    for pattern in WITH_SUBJECT:
        found = pattern.search(message)
        if found:
            subject = _clean_subject(found.group(1))
            if len(subject) >= 3 and len(subject.split()) <= 8:
                return {'mode': 'subject', 'subject': subject, 'careful': bool(_FOLLOWED.search(found.group(1)))}
    for pattern in REMEMBER_MY:
        found = pattern.search(message)
        if found:
            subject = _clean_subject(found.group(1))
            if len(subject) >= 3 and len(subject.split()) <= 8:
                return {'mode': 'subject', 'subject': subject, 'careful': True}
    if any(p.search(message) for p in WITHOUT_SUBJECT) and not re.search(r'\b(?:cette|la\s+m[êe]me)\s+conversation\b',
                                                                        message, re.I):
        return {'mode': 'list'}
    return None


def terms(subject: str) -> list[str]:
    """The words of a subject that are searched (folded, stop words removed): Athéna's clesHistorique."""
    return [t for t in search.query_terms(subject) if len(t) >= 3 or t.isdigit()][:30]


def _key(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip().lower()


def _meta(row: dict) -> dict:
    try:
        meta = json.loads(row.get('meta_json') or '{}')
    except (TypeError, ValueError):
        meta = {}
    return meta if isinstance(meta, dict) else {}


def exchange_pairs(exclude_conversation: int | None = None) -> list[dict]:
    """Each user question with the answer that followed it in the same conversation (current one left out)."""
    rows = db.rows("SELECT id, conversation_id, role, content, meta_json, created_at FROM messages "
                   "WHERE role IN ('user', 'assistant') AND conversation_id != ? ORDER BY conversation_id, id",
                   (int(exclude_conversation or 0),))
    pairs = []
    for i, row in enumerate(rows):
        if row['role'] != 'user' or not str(row['content'] or '').strip():
            continue
        reply = next((r for r in rows[i + 1:i + 4] if r['conversation_id'] == row['conversation_id']), None)
        pairs.append({'question': {**row, 'meta': _meta(row)},
                      'reply': {**reply, 'meta': _meta(reply)} if reply and reply['role'] == 'assistant' else None})
    pairs.sort(key=lambda p: (p['question']['created_at'], p['question']['id']))
    return pairs


def _word(term: str) -> re.Pattern:
    return re.compile(r'(?<![a-z0-9])' + re.escape(term))


def excerpt_around(text: str, words: list[str], size: int = 260) -> str:
    """The part of an answer around the first word searched, so that one sees WHY it comes out."""
    flat = re.sub(r'\s+', ' ', text or '').strip()
    folded = search.fold(flat)
    positions = [p for p in (folded.find(w) for w in words) if p >= 0]
    if not positions or min(positions) < size // 2:
        return shorten(flat, size)
    start = flat.rfind(' ', 0, max(0, min(positions) - size // 3))
    return '…' + shorten(flat[start + 1:], size)


def shorten(text: str, size: int) -> str:
    flat = re.sub(r'\s+', ' ', text or '').strip()
    return flat if len(flat) <= size else re.sub(r'\s+\S*$', '', flat[:size]) + '…'


def search_subject(subject: str, pairs: list[dict], *, only_if_found: bool = False) -> dict:
    """Exchanges about a subject, scored like BM25 (a rare word weighs more), the subject IN the question counting
    more and a pasted document less. Few words: all must be there; more: the essential of their weight."""
    words = terms(subject)
    out = {'mode': 'subject', 'subject': subject, 'careful': only_if_found, 'words': words, 'found': [],
           'count': 0, 'searched': len(pairs), 'since': pairs[0]['question']['created_at'] if pairs else None}
    if not words or not pairs:
        return out
    texts = [search.fold(f"{p['question']['content']} {(p['reply'] or {}).get('content') or ''}") for p in pairs]
    patterns = {w: _word(w) for w in words}
    weight = {}
    for w, pattern in patterns.items():
        n = sum(1 for t in texts if pattern.search(t))
        weight[w] = math.log(1 + (len(pairs) - n + 0.5) / (n + 0.5))
    total = sum(weight.values())
    scored, seen = [], set()
    for pair, text in zip(pairs, texts):
        present = [w for w in words if patterns[w].search(text)]
        if not present:
            continue
        base = sum(weight[w] for w in present)
        if len(words) <= 2 and len(present) < len(words):
            continue
        if len(words) > 2 and base < 0.6 * total:
            continue
        question = pair['question']['content']
        if recall_request(question):
            continue   # a recall request is not a conversation ABOUT the subject (Athéna, 17/10)
        in_question = sum(1 for w in words if patterns[w].search(search.fold(question)))
        if only_if_found and in_question < len(words):
            continue
        score = (base + total * in_question / len(words)) * (0.5 if len(question) > LONG_QUESTION else 1)
        scored.append((score, pair['question']['id'], pair))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    for _score, _id, pair in scored:
        key = _key(pair['question']['content'])
        if key in seen:
            continue   # the same question asked again: only the most recent
        seen.add(key)
        out['found'].append(pair)
    out['count'] = len(out['found'])
    out['found'] = out['found'][:3]
    return out


def recent_conversations(exclude_conversation: int | None = None, count: int = 5) -> dict:
    """The last conversations (current one left out), with their first questions, read in the base."""
    conversations = db.rows(
        "SELECT c.id, c.title, c.created_at, c.updated_at, (SELECT COUNT(*) FROM messages m WHERE "
        "m.conversation_id = c.id AND m.role = 'user') AS questions FROM conversations c WHERE c.id != ? AND "
        "EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user') "
        "ORDER BY c.updated_at DESC, c.id DESC", (int(exclude_conversation or 0),))
    shown = []
    for conv in conversations[:count]:
        first = db.rows("SELECT content, meta_json FROM messages WHERE conversation_id = ? AND role = 'user' "
                        "ORDER BY id LIMIT 3", (conv['id'],))
        shown.append({**conv, 'subjects': [shorten(m['content'], 70) for m in first],
                      'imported_from': _meta(first[0]).get('imported_from') if first else None,
                      'sensitive': any(_meta(m).get('sensitive') for m in first)})
    oldest = conversations[-1]['created_at'] if conversations else None
    return {'mode': 'list', 'conversations': shown, 'total': len(conversations), 'since': oldest,
            'sensitive': any(c['sensitive'] for c in shown)}


def memory_covers(subject: str, memories: list[dict]) -> bool:
    """A memory holds EVERY word of the subject (careful recall: the memory answers, Athéna 17/10)."""
    words = terms(subject)
    return bool(words) and any(all(w in search.fold(f"{m.get('title') or ''} {m.get('detail') or ''}") for w in words)
                               for m in memories)


# 22/11/2026 (banc de conversation, tour 8) : « C'est quand, la démonstration du projet Vega ? », après « retiens que
# la démonstration aura lieu le 14 janvier à 15 h ». Aucune règle ne reconnaît la question : elle partait au modèle,
# qui recevait bien le souvenir mais y ajoutait un jour de la semaine ou une année que personne n'avait dits. Quand
# un souvenir tient tous les mots du sujet ET une date ou une heure, c'est lui qui répond, lu par le code.
_WHEN = r"(?:quand|quel\s+jour|[àa]\s+quelle\s+heure|[àa]\s+quelle\s+date|quelle\s+date)"
DATED_QUESTION = [
    re.compile(r"^(?:c['’]\s*est|ce\s+sera|c['’]\s*[ée]tait|(?:elle|il)\s+(?:est|sera))\s+" + _WHEN +
               r"(?:\s+d[ée]j[àa])?\s*,?\s+(.+)$", re.I),
    re.compile(r"^" + _WHEN + r"\s+(?:a\s+lieu|aura\s+lieu|est(?:\s+pr[ée]vue?|\s+fix[ée]e?)?|sera|se\s+passe|se\s+tient|"
               r"se\s+d[ée]roule|commence|d[ée]bute|tombe)\s+(.+)$", re.I),
    re.compile(r"^(.+?)\s*,?\s+c['’ ]\s*est\s+" + _WHEN + r"(?:\s+d[ée]j[àa])?$", re.I),
    re.compile(r"^quelle\s+est\s+(?:la\s+date|l['’]\s*heure)\s+(?:de\s+|du\s+|des\s+|d['’]\s*)(.+)$", re.I),
]
_TODAY = re.compile(r"^(?:aujourd['’ ]?\s*hui|jour|ce\s+jour|maintenant|demain|hier)$", re.I)
_SAYS_A_DATE = re.compile(
    r"\b\d{1,2}\s*(?:er)?\s+(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|"
    r"d[ée]cembre)\b|\b\d{1,2}\s?(?:h|heures?)(?:\s?\d{2})?\b|\b\d{1,2}[:/]\d{1,2}\b|\b(?:lundi|mardi|mercredi|jeudi|"
    r"vendredi|samedi|dimanche|demain|apr[èe]s[-\s]demain|ce\s+soir|ce\s+matin|midi|minuit)\b", re.I)


def dated_subject(text: str) -> str | None:
    """What a question asks the date or the hour of (« C'est quand, X ? », « À quelle heure est X ? », « Quelle est
    la date de X ? »), or None. Only the wording: whether a memory answers is the caller's check."""
    message = re.sub(r'[?!.\s]+$', '', (text or '').strip())
    for pattern in DATED_QUESTION:
        found = pattern.match(message)
        if found:
            subject = found.group(1).strip(' ,;:')
            if subject and not _TODAY.match(subject) and terms(subject) and len(subject.split()) <= 12:
                return subject
    return None


def dated(memory: dict) -> bool:
    """A memory that says a date or an hour (« le 14 janvier », « à 15 h », « mardi », « demain »)."""
    return bool(_SAYS_A_DATE.search(f"{memory.get('title') or ''} {memory.get('detail') or ''}"))


def recall_for(text: str, *, conversation_id: int | None, memories: list[dict]) -> dict | None:
    """The recall to show for a request, or None (not a recall, or the current conversation / a memory answers)."""
    asked = recall_request(text)
    if asked is None:
        return None
    if asked['mode'] == 'list':
        return recent_conversations(conversation_id)
    words = terms(asked['subject'])
    if conversation_id and words:
        # The messages BEFORE this request (it is stored in the conversation before being handled).
        current = search.fold(' '.join(r['content'] for r in db.rows(
            'SELECT content FROM messages WHERE conversation_id = ? AND TRIM(content) != ?',
            (int(conversation_id), (text or '').strip()))))
        if all(w in current for w in words):
            return None   # the subject is in this very conversation: it is read, not recalled
    if asked['careful'] and memory_covers(asked['subject'], memories):
        return None
    found = search_subject(asked['subject'], exchange_pairs(conversation_id), only_if_found=asked['careful'])
    if asked['careful'] and not found['found']:
        return None
    found['sensitive'] = any(p['question']['meta'].get('sensitive') or (p['reply'] or {}).get('meta', {}).get('sensitive')
                             for p in found['found'])
    return found


# --------------------------------------------------------------------------- the answer, read and never generated

def when(iso: str | None, *, hour: bool = True) -> str:
    """« mardi 3 septembre à 14:05 », in the PC's local time (the French names of core/prompts.py)."""
    from ..core.prompts import MONTHS, WEEKDAYS   # lazy: the core module imports the memory one
    if not iso:
        return 'date inconnue'
    try:
        moment = datetime.fromisoformat(str(iso).replace('Z', '+00:00')).astimezone()
    except ValueError:
        return str(iso)[:10]
    day = f'{WEEKDAYS[moment.weekday()]} {moment.day} {MONTHS[moment.month - 1]}'
    return f'{day} à {moment:%H:%M}' if hour else day


def answer(recall: dict) -> str:
    """The text of a recall: what the base holds, dated; Athéna's answers named as hers."""
    if recall['mode'] == 'list':
        convs = recall['conversations']
        if not convs:
            return 'Je n’ai encore aucune conversation passée dans la base.'
        lines = [f"Vos {len(convs)} dernières conversations, lues dans la base (sur {recall['total']} depuis le "
                 f"{when(recall['since'], hour=False)}) :", '']
        for conv in convs:
            origin = ' — échange avec Athéna' if conv.get('imported_from') == 'athena' else ''
            subjects = ', '.join(f'« {s} »' for s in conv['subjects'])
            lines.append(f"**Le {when(conv['created_at'])}** ({conv['questions']} question(s){origin}) : {subjects}")
        lines += ['', 'Pour en retrouver une précise : « on avait parlé de … » ou « retrouve notre conversation sur … ».']
        return '\n'.join(lines)
    subject, found = recall['subject'], recall['found']
    if not recall['words']:
        return f'« {subject} » est trop vague pour que je le cherche dans vos conversations : donnez-moi un mot plus précis.'
    if not found:
        return (f'Je n’ai trouvé **aucune** conversation passée qui parle de « {subject} ». J’ai cherché dans '
                f"{recall['searched']} question(s), depuis le {when(recall['since'], hour=False)}. Je n’en invente pas : "
                'avec un autre mot, je relance.')
    count = recall['count']
    lines = [f'J’ai retrouvé **{count} échange(s)** sur « {subject} » dans vos conversations passées'
             + (' ; voici les 3 qui en parlent le plus' if count > 3 else '') + '. Lus dans la base, pas reconstitués :', '']
    for pair in found:
        q, reply = pair['question'], pair['reply']
        athena = q['meta'].get('imported_from') == 'athena'
        lines.append(f"**Le {when(q['created_at'])}** — vous {'aviez demandé à Athéna' if athena else 'm’aviez demandé'} : "
                     f"« {shorten(q['content'], 200)} »")
        if reply:
            said = 'Athéna avait répondu (réponse d’Athéna, non vérifiée par AURORA)' if athena else 'J’avais répondu'
            lines.append(f"{said} : « {excerpt_around(reply['content'], recall['words'])} »")
        else:
            lines.append('(la réponse n’a pas été gardée)')
        lines.append('')
    lines.append(f"Cherché dans {recall['searched']} question(s) depuis le {when(recall['since'], hour=False)}.")
    return '\n'.join(lines)
