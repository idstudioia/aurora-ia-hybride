"""C3, first part (16/11/2026, ID Studio IA : « continue la suite ») — reminders, timers and alarms.

« Rappelle-moi à 18 h d'appeler Paul », « rappelle-moi dans 20 minutes de sortir le plat », « minuteur de 10
minutes », « réveille-moi à 7 h » : the moment is read in the sentence by the code below (``understand``), never
by an AI. The reminder is kept in the settings table (key 'reminders': no migration), and the service says it when
its time comes (main._reminder_loop, event « reminder.due »). The interface then shows it — and reads it aloud when
the voice is on — until « Fait ». One whose time came while AURORA was off is said at its next start, with its hour:
a reminder is never lost, only late.

Nothing here sends, deletes or spends anything: a reminder is words shown at a moment.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta

from .. import db
from ..core.normalize import strip_accents, words_to_numbers
from ..core.prompts import MONTHS, WEEKDAYS

KEY = 'reminders'
MAX_PENDING, MAX_TEXT, MAX_AHEAD_DAYS = 50, 200, 366
KINDS = ('reminder', 'timer', 'alarm')
_STAMP = '%Y-%m-%d %H:%M:%S'   # local time of the PC, to the second (a timer of 90 seconds)
_LOCK = threading.Lock()       # the tool (a request) and the service's loop both read then write the list


def _local_now() -> datetime:
    return datetime.now()


# ------------------------------------------------------------------ reading the moment in the sentence

_NUM = (r'(?:\d{1,3}|vingt[ -]cinq|quarante[ -]cinq|une|un|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|'
        r'douze|quinze|vingt|trente|quarante|cinquante|soixante)')
_B = r'(?<![a-zà-ÿ0-9])'    # not glued to a word before
_L = r'(?![a-zà-ÿ])'        # not followed by a letter: « 3 s » is seconds, « 3 semaines » is not
_DUR_WORDS = re.compile(r"(?:(?P<half>une?\s+demi[- ]?heure)|(?P<q3>trois\s+quarts?\s+d['’ ]\s*heure)|"
                        r"(?P<q1>un\s+quart\s+d['’ ]\s*heure))" + _L, re.I)
_DUR_HOURS = re.compile(rf"(?P<n>{_NUM})\s*(?:heures?|h){_L}(?:\s*(?:et\s+)?(?:(?P<half>demie?)|(?P<quarter>quart)|"
                        rf"(?P<m>[0-5]?\d)(?:\s*(?:minutes?|min|mn){_L})?)(?![a-zà-ÿ0-9]))?", re.I)
_DUR_OTHER = re.compile(rf"(?P<n>{_NUM})\s*(?P<unit>secondes?|sec|minutes?|min|mn|jours?|semaines?|s|j){_L}"
                        rf"(?:\s*(?:et\s+)?(?P<extra>[0-5]?\d)(?:\s*(?:secondes?|sec|s){_L})?(?![a-zà-ÿ0-9]))?", re.I)
_IN = re.compile(_B + r"(?:dans|d['’ ]ici)\s+", re.I)
_TIMER = re.compile(_B + r"(?:minuteur|minuterie|timer|compte\s+[àa]\s+rebours|chrono(?:m[èe]tre)?)" + _L +
                    r"(?:\s+(?:de|pour|sur)\s+|\s+d['’]\s*|\s+)?", re.I)
_HOUR_TAIL = (r"(?:\s+et\s+(?P<half>demie?)|\s+et\s+(?P<quarter>quart)|\s+moins\s+le\s+quart(?P<minus>))?"
              r"(?:\s+(?:(?P<am>du\s+matin)|(?P<pm>du\s+soir|de\s+l['’ ]\s*apr[èe]s[- ]midi)))?")
_AT = re.compile(_B + r"(?:[àa]|pour|vers)\s+(?:(?P<noon>midi)|(?P<midnight>minuit)|"
                 r"(?P<h>2[0-3]|[01]?\d)\s*(?:heures?|h|:)\s*(?P<m>[0-5]\d)?)(?![a-zà-ÿ0-9])" + _HOUR_TAIL, re.I)
# « réveil 7 h 30 », « rappelle-moi 18h appeler Paul »: an hour said without « à » — read only when no duration was.
_BARE_HOUR = re.compile(_B + r"(?P<h>2[0-3]|[01]?\d)\s*(?:heures?|h)\s*(?P<m>[0-5]\d)?(?![a-zà-ÿ0-9])" + _HOUR_TAIL, re.I)
_MONTH = r"janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|septembre|octobre|novembre|d[ée]cembre"
_DAY = re.compile(
    _B + r"(?:(?P<after>apr[èe]s[- ]demain)|(?P<tomorrow>demain)|"
    r"(?P<today>aujourd['’ ]?hui|ce\s+matin|ce\s+midi|cet\s+apr[èe]s[- ]midi|ce\s+soir|cette\s+nuit)|"
    r"(?P<weekday>lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)(?P<next>\s+prochain)?|"
    # « le 25 » alone is a date only just before the hour or at the end (« sortir le 2 roues » is not one)
    rf"le\s+(?P<dom>3[01]|[0-2]?\d)(?:er)?(?:\s*/\s*(?P<mon>1[0-2]|0?\d)|\s+(?P<month>{_MONTH})|"
    r"(?=\s*$|\s*[,;.!?]|\s+(?:[àa]|vers|pour)\s+(?:\d|midi|minuit))))"
    r"(?![a-zà-ÿ0-9])(?:\s+(?P<part>matin|midi|apr[èe]s[- ]midi|soir)" + _L + r")?", re.I)

_POLITE = (r"(?:(?:dis\s+)?aurora\s*[,:]?\s+)?(?:s['’ ]il\s+(?:te|vous)\s+pla[îi]t\s*,?\s+|stp\s*,?\s+|svp\s*,?\s+)?"
           r"(?:est[- ]ce\s+que\s+tu\s+peux\s+|peux[- ]tu\s+|tu\s+peux\s+|pourrais[- ]tu\s+|tu\s+pourrais\s+|"
           r"pouvez[- ]vous\s+|il\s+faut\s+|je\s+veux\s+que\s+tu\s+|j['’ ]aimerais\s+que\s+tu\s+)?")
_THING = r"rappels?|alarmes?|r[ée]veils?|minuteurs?|minuteries?|timers?|comptes?\s+[àa]\s+rebours|chronos?|chronom[èe]tres?"
_CMD = re.compile(
    r"^\s*" + _POLITE + r"(?:"
    r"(?:me\s+|m['’ ]\s*)?(?P<wake>r[ée]veill(?:e[sz]?|er))(?:[- ](?:moi|nous))?|"
    r"(?:me\s+|m['’ ]\s*)?(?:rappel(?:le[sz]?|er)|pr[ée]vien(?:s|ir)|pr[ée]venez|avertis|avertir)(?:[- ](?:moi|nous))?|"
    r"(?:(?:fai(?:s|tes)[- ]moi|me\s+faire)\s+penser|pense[sz]?\s+[àa]\s+me\s+(?:rappeler|pr[ée]venir|faire\s+penser))"
    r"(?:\s+[àa](?=\s))?|"
    r"(?:(?:mets|mettre|mettez|programme[rsz]?|cr[ée]e[rsz]?|ajoute[rsz]?|r[èe]gle[rsz]?|lance[rsz]?|"
    r"d[ée]marre[rsz]?|fai(?:s|tes))(?:[- ]moi)?\s+)?(?:(?:un|une|le|la|mon|ma)\s+)?(?P<object>" + _THING +
    r"))" + _L, re.I)
_LIST = re.compile(
    r"^\s*" + _POLITE + r"(?:(?:quels?\s+sont|liste[rsz]?|montre[rsz]?(?:[- ]moi)?|affiche[rsz]?(?:[- ]moi)?|"
    r"donne[rsz]?(?:[- ]moi)?|dis[- ]moi|ai[- ]je|est[- ]ce\s+que\s+j['’ ]ai|y\s+a[- ]t[- ]il|"
    r"combien\s+(?:de\s+|d['’]\s*)?)\s*(?:(?:mes|les|des|un|une|tous\s+mes|tous\s+les|quels?|quelles?)\s+)?"
    rf"(?:{_THING})|(?:mes|les)\s+(?:{_THING})\s*\??\s*$|combien\s+de\s+temps\s+(?:il\s+)?reste|"
    rf"o[ùu]\s+en\s+est\s+(?:le|mon|la|ma)\s+(?:{_THING})|temps\s+restant)", re.I)
_CANCEL = re.compile(
    r"^\s*" + _POLITE + r"(?:annule[rsz]?|supprime[rsz]?|arr[êe]te[rsz]?|enl[èe]ve[rsz]?|retire[rsz]?|efface[rsz]?|"
    r"stoppe[rsz]?|coupe[rsz]?|d[ée]sactive[rsz]?)\s+(?P<det>les|mes|tous\s+(?:les|mes)|toutes\s+(?:les|mes)|le|la|"
    rf"l['’]|mon|ma|ce|cette)\s*(?:(?:dernier|derni[èe]re|prochain|prochaine)\s+)?(?P<thing>{_THING})" + _L, re.I)
# What links the order to the thing to remember: « de », « d' », « que », « pour » — and « d appeler », as the voice writes it.
_HEAD = re.compile(r"^(?:[\s,;:.!?\-–—]+|(?:qu['’ ]\s*il\s+faut|il\s+faut|je\s+dois|de|que|pour)\s+|(?:d|qu)['’]\s*|"
                   r"(?:d|qu)\s+(?=[aeiouyhàâéèêëîïôöûü]))+", re.I)
_TAIL = re.compile(r"(?:[\s,;:.!?\-–—]+|(?<![a-zà-ÿ])(?:s['’ ]il\s+(?:te|vous)\s+pla[îi]t|stp|svp|merci)(?![a-zà-ÿ]))+$", re.I)


def _number(word: str) -> int:
    """« 20 », « vingt-cinq », « une »: the number of a duration (core/normalize.words_to_numbers reads the words)."""
    return int(words_to_numbers(' '.join(word.lower().replace('-', ' ').split())))


def _duration_at(s: str, pos: int) -> tuple[int, int] | None:
    """(seconds, end) of the duration written at ``pos`` of ``s``: « 20 minutes », « 1 h 30 », « une demi-heure »."""
    m = _DUR_WORDS.match(s, pos)
    if m:
        return (1800 if m.group('half') else 2700 if m.group('q3') else 900), m.end()
    m = _DUR_HOURS.match(s, pos)
    if m:
        extra = 30 if m.group('half') else 15 if m.group('quarter') else int(m.group('m') or 0)
        return _number(m.group('n')) * 3600 + extra * 60, m.end()
    m = _DUR_OTHER.match(s, pos)
    if m:
        unit = m.group('unit').lower()
        size = 604800 if unit.startswith('sem') else 1 if unit.startswith('s') else 60 if unit.startswith('m') else 86400
        if size == 60 and m.group('extra'):                     # « 2 minutes 30 »
            return _number(m.group('n')) * 60 + int(m.group('extra')), m.end()
        return _number(m.group('n')) * size, m.end('unit')
    return None


def _kind_of(thing: str | None) -> str:
    word = (thing or '').lower()
    return 'alarm' if re.match(r'alarme|r[ée]veil', word) else 'reminder' if word.startswith('rappel') else 'timer'


def _clock(m: re.Match, evening: bool) -> tuple[int, int]:
    """(hour, minute) of an hour said: « 18 h 30 », « midi et demie », « 8 h du soir », « 8 h moins le quart »."""
    named = m.groupdict()
    if named.get('noon'):
        hour, minute = 12, 0
    elif named.get('midnight'):
        hour, minute = 0, 0
    else:
        hour, minute = int(m.group('h')), int(m.group('m') or 0)
    if m.group('half'):
        minute = 30
    elif m.group('quarter'):
        minute = 15
    elif m.group('minus') is not None:
        hour, minute = (hour - 1) % 24, 45
    if (m.group('pm') or (evening and not m.group('am'))) and 1 <= hour < 12:
        hour += 12
    return hour, minute


def _date(day: re.Match, now: datetime, hour: int, minute: int) -> datetime | None:
    """The day said (« demain », « lundi », « le 25 novembre ») at that hour; None when the date does not exist."""
    base = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day.group('today'):
        return base
    if day.group('tomorrow') or day.group('after'):
        return base + timedelta(days=2 if day.group('after') else 1)
    if day.group('weekday'):
        ahead = (WEEKDAYS.index(day.group('weekday').lower()) - now.weekday()) % 7
        return base + timedelta(days=7 if ahead == 0 and (day.group('next') or base <= now) else ahead)
    try:
        number = int(day.group('dom'))
        if day.group('mon') or day.group('month'):
            said = strip_accents(day.group('month') or '').lower()
            month = int(day.group('mon')) if day.group('mon') else 1 + [
                i for i, name in enumerate(MONTHS) if strip_accents(name) == said][0]
            found = base.replace(month=month, day=number)
            return found if found > now else found.replace(year=now.year + 1)
        found = base.replace(day=number)
        if found > now:
            return found
        year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        return base.replace(year=year, month=month, day=number)
    except (ValueError, IndexError):
        return None


def understand(text: str, now: datetime | None = None) -> dict:
    """What a reminder request asks, read in its words — the slots of the intent « reminder.set »:
    {'due': « 2026-11-16 18:00:00 » (local) or None, 'text': what to remember, 'kind': reminder | timer | alarm,
    'why': None, or why there is no ``due`` — 'when' (no moment understood), 'hour' (a day without an hour),
    'past' (an hour already gone today), 'far' (more than a year ahead)}; or {'show': True} (« quels sont mes
    rappels ? »); or {'cancel': kind, 'every': bool} (« annule le minuteur », « supprime tous mes rappels »).
    An hour without a day is today, or tomorrow when it is already past: the answer always says the full date."""
    now = (now or _local_now()).replace(microsecond=0)
    s = ' '.join(str(text or '').replace('’', "'").replace(' ', ' ').replace(' ', ' ').split())
    out: dict = {'due': None, 'text': '', 'kind': 'reminder', 'why': None}
    cancel = _CANCEL.match(s)
    if cancel:
        thing = cancel.group('thing').lower()   # « rebours » ends with an s and is one
        every = bool(re.match(r'les|mes|tou', cancel.group('det'), re.I)) or (
            thing.endswith('s') and not thing.endswith('rebours'))
        return {**out, 'cancel': _kind_of(cancel.group('thing')), 'every': every}
    if _LIST.match(s):
        return {**out, 'show': True}

    spans: list[tuple[int, int]] = []   # the pieces that say when or give the order: not what is to remember
    command = _CMD.match(s)
    if command:
        spans.append(command.span())
        out['kind'] = 'alarm' if command.group('wake') else _kind_of(command.group('object')) \
            if command.group('object') else 'reminder'
    due, said = None, ''
    timer = _TIMER.search(s)
    length = _duration_at(s, timer.end()) if timer else None
    if timer and length:
        out['kind'], due, said = 'timer', now + timedelta(seconds=length[0]), s[timer.end():length[1]]
        spans.append((timer.start(), length[1]))
    day = _DAY.search(s)
    evening = bool(day and re.search(r'soir|apr[èe]s[- ]midi', day.group(0), re.I))
    if due is None:
        for lead in _IN.finditer(s):
            length = _duration_at(s, lead.end())
            if not length:
                continue
            spans.append((lead.start(), length[1]))
            at = _AT.search(s) if length[0] % 86400 == 0 else None   # « dans 2 jours à 9 h »
            if at:
                spans.append(at.span())
                hour, minute = _clock(at, evening)
                due = (now + timedelta(seconds=length[0])).replace(hour=hour, minute=minute, second=0)
            else:
                due = now + timedelta(seconds=length[0])
            break
    if due is None:
        at = _AT.search(s) or _BARE_HOUR.search(s, command.end() if command else 0)
        if at:
            spans.append(at.span())
            hour, minute = _clock(at, evening)
            if day:
                spans.append(day.span())
                due = _date(day, now, hour, minute)
                if due is None:
                    out['why'] = 'when'
                elif due <= now:
                    due, out['why'] = None, 'past'
            else:
                due = now.replace(hour=hour, minute=minute, second=0)
                if due <= now:
                    due += timedelta(days=1)
        elif day:
            spans.append(day.span())
            out['why'] = 'hour'
        else:
            out['why'] = 'when'
    if due is not None and due - now > timedelta(days=MAX_AHEAD_DAYS):
        due, out['why'] = None, 'far'

    kept, last = [], 0                  # the sentence without these pieces (they may overlap: « mets un minuteur de … »)
    for start, end in sorted(spans):
        kept.append(s[last:start] if start > last else '')
        last = max(last, end)
    rest = _TAIL.sub('', _HEAD.sub('', ' '.join(' '.join(kept + [s[last:]]).split())))
    rest = (rest[:1].upper() + rest[1:])[:MAX_TEXT]
    if out['kind'] == 'timer':
        said = ' '.join(said.split())
        label = f"Minuteur {'d’' if re.match(r'une?(?![a-zà-ÿ])', said, re.I) else 'de '}{said}" if said else 'Minuteur'
        out['text'] = f'{label} — {rest}' if rest else label
    else:
        out['text'] = rest or ('Réveil' if command and command.group('wake') else
                               'Alarme' if out['kind'] == 'alarm' else 'Rappel')
    out['due'] = due.strftime(_STAMP) if due else None
    return out


# ------------------------------------------------------------------ words

def moment_words(due: datetime, now: datetime | None = None, *, seconds: bool = False) -> str:
    """« aujourd'hui à 18:00 », « demain à 09:00 », « lundi 23 novembre à 08:00 » (« à 16:52:30 » for a timer)."""
    now = now or _local_now()
    hour = due.strftime('%H:%M:%S' if seconds and due.second else '%H:%M')
    days = (due.date() - now.date()).days
    day = "aujourd’hui" if days == 0 else 'demain' if days == 1 else 'hier' if days == -1 else (
        f"{WEEKDAYS[due.weekday()]} {due.day}{'er' if due.day == 1 else ''} {MONTHS[due.month - 1]}"
        + (f' {due.year}' if due.year != now.year else ''))
    return f'{day} à {hour}'


def parse_stamp(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value), _STAMP)
    except ValueError:
        return None


# ------------------------------------------------------------------ the list kept by the service

def list_reminders() -> list[dict]:
    """Every reminder kept: those to come (state « waiting ») and those whose time came and that were not answered
    « Fait » yet (state « due »), the due ones first, then by time."""
    value = db.get_setting(KEY, [])
    items = [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []
    return sorted(items, key=lambda r: (r.get('state') != 'due', str(r.get('due') or ''), r.get('id') or 0))


def add(text: str, due: datetime, kind: str = 'reminder', now: datetime | None = None) -> dict:
    """Keeps a reminder for ``due`` (local time). Raises ValueError with a French message."""
    now = (now or _local_now()).replace(microsecond=0)
    if kind not in KINDS:
        raise ValueError('Type de rappel inconnu.')
    if due <= now:
        raise ValueError('Ce moment est déjà passé.')
    if due - now > timedelta(days=MAX_AHEAD_DAYS):
        raise ValueError('Un rappel se programme au plus un an à l’avance.')
    with _LOCK:
        items = list_reminders()
        if len([r for r in items if r.get('state') == 'waiting']) >= MAX_PENDING:
            raise ValueError(f'{MAX_PENDING} rappels en attente au plus : supprimez-en un avant d’en ajouter.')
        item = {'id': max([int(r.get('id') or 0) for r in items] + [0]) + 1,
                'text': ' '.join(str(text or '').split())[:MAX_TEXT] or 'Rappel', 'kind': kind,
                'due': due.strftime(_STAMP), 'created_at': now.strftime(_STAMP), 'state': 'waiting', 'fired_at': None}
        db.set_setting(KEY, items + [item])
    return item


def remove(reminder_id: int) -> dict | None:
    """« Fait » on a reminder that rang, or « Supprimer » before its time. Returns it, None when unknown."""
    with _LOCK:
        items = list_reminders()
        gone = next((r for r in items if r.get('id') == int(reminder_id)), None)
        if gone is not None:
            db.set_setting(KEY, [r for r in items if r is not gone])
    return gone


def cancel(kind: str, every: bool) -> tuple[list[dict], list[dict]]:
    """« Annule le minuteur »: removes the reminders of that kind still to come — all of them when ``every``, or the
    only one there is. With several and a singular (« annule le rappel »), nothing is removed: which one is not said.
    Returns (removed, the ones of that kind still kept)."""
    with _LOCK:
        items = list_reminders()
        found = [r for r in items if r.get('kind') == kind and r.get('state') == 'waiting']
        gone = found if every or len(found) == 1 else []
        if gone:
            db.set_setting(KEY, [r for r in items if r not in gone])
    return gone, [r for r in found if r not in gone]


def fire_due(now: datetime | None = None) -> list[dict]:
    """The reminders whose time has come, marked « due » (with « fired_at ») so that each is said once by the
    service and stays shown until « Fait ». One whose time passed while the service was off is found here too."""
    now = (now or _local_now()).replace(microsecond=0)
    stamp = now.strftime(_STAMP)
    with _LOCK:
        items = list_reminders()
        rung = [r for r in items if r.get('state') == 'waiting' and str(r.get('due') or '') <= stamp]
        if rung:
            for item in rung:
                item.update(state='due', fired_at=stamp)
            db.set_setting(KEY, items)
    return rung


def snooze(reminder_id: int, minutes: int, now: datetime | None = None) -> dict | None:
    """« Dans 10 min »: a reminder that rang goes back to waiting, ``minutes`` from now. None when unknown."""
    now = (now or _local_now()).replace(microsecond=0)
    with _LOCK:
        items = list_reminders()
        item = next((r for r in items if r.get('id') == int(reminder_id)), None)
        if item is not None:
            item.update(state='waiting', fired_at=None,
                        due=(now + timedelta(minutes=max(1, min(int(minutes), 1440)))).strftime(_STAMP))
            db.set_setting(KEY, items)
    return item
