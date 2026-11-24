"""D1 « Routines » (image « Skill backbone »: done twice by hand, then a routine): a named list of requests,
saved once, launched by a phrase.

Each step is an ordinary request sentence. The interface runs the steps one after the other exactly as if
they were typed (same router, tools, permission cards, verification and journal): a routine never gets a
right that a typed request does not have, and anything that deletes, sends or spends still asks each time.
A step can never launch a routine (no loop). Stored in the settings table (key 'routines'): no migration.

D2, first part (16/11/2026, ID Studio IA : « continue avec la configuration de la routine ») — a routine can have a schedule:
an hour and days of the week. The service then launches it by itself (main._routine_loop, ``due`` then
``run_scheduled``) through the very same requests as its « Lancer » button: nothing is allowed that a typed
request would not be, and what deletes, sends or spends still waits for an agreement in the interface.

D2, second part (16/11/2026, ID Studio IA : « continue la suite ») — in place of an hour, a schedule can name an event of
EVENTS, a closed list of what the service really knows. Today one: « start », AURORA has just started (once a day
at most). Same launch, same report, same rules.

D2, last part — « réussi 5 fois de suite, alors proposé en automatique » (image « Skill backbone »): ``proposal``
counts, in the journal of the requests, the launches by hand that went to their end one after the other; from
PROPOSE_AFTER, the interface proposes to give the routine a schedule. Only a proposal: nothing is ever scheduled
without a click, and « Ne plus proposer » is kept in the routine (``no_proposal``).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from .. import db
from ..core.normalize import normalize

log = logging.getLogger(__name__)
KEY = 'routines'
MAX_ROUTINES, MAX_STEPS, MAX_TRIGGERS = 30, 10, 5
MAX_NAME, MAX_TRIGGER, MAX_STEP = 60, 120, 300
_HOUR = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')
# The option every request of a scheduled launch carries: nobody is in front of the screen, so no agreement given
# beforehand applies (orchestrator._standing_cloud_consent, _standing_comment_consent) — each one waits for a click.
UNATTENDED = {'unattended': True}
_MINUTE = '%Y-%m-%d %H:%M'   # a schedule lives in local time, to the minute: « 2026-11-16 08:00 »
# The events a routine can be launched on, with the words the interface shows (« Lancer seule … »). Closed list:
# an event is added here only when the service really knows it happened.
EVENTS = {'start': 'au démarrage d’AURORA'}
PROPOSE_AFTER, _PROPOSE_READ = 5, 12   # launches by hand in a row before the proposal; how many are read at most
# « lance ma routine du matin », « démarre la routine Matin », « routine matin »
_LAUNCH = re.compile(r'^(?:(?:lance|lancer|lances|lancez|demarre|demarrer|demarrez|execute|executer|executez|joue|'
                     r'jouer|fais|faire)(?: moi)? )?(?:la |ma |une |notre )?routine (?:du |de la |de l |des |de |d )?'
                     r'(?P<name>.+)$')


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _local_now() -> datetime:
    return datetime.now()


def list_routines() -> list[dict]:
    """Every routine. ``schedule`` (D2: {'time', 'days', 'since'} or None) and ``auto`` (its last launch by the
    schedule, or None) are always there, also for a routine saved before D2."""
    value = db.get_setting(KEY, [])
    return [{'schedule': None, 'auto': None, **r} for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def get_routine(routine_id: int) -> dict | None:
    return next((r for r in list_routines() if r.get('id') == int(routine_id)), None)


def match(text: str) -> dict | None:
    """The routine a request launches: its name or one of its phrases, said alone (« Bonjour AURORA, on
    commence »), or « lance la routine <nom> ». None otherwise."""
    norm = normalize(text or '')
    if not norm:
        return None
    routines = list_routines()
    for routine in routines:
        if norm in {normalize(p) for p in [routine.get('name', '')] + list(routine.get('triggers') or [])}:
            return routine
    launched = _LAUNCH.match(norm)
    if launched:
        wanted = launched.group('name').strip()
        return next((r for r in routines if normalize(r.get('name', '')) == wanted), None)
    return None


def asks_for_a_routine(text: str) -> str | None:
    """« lance la routine X »: the name asked (normalised), even when no routine has it; None otherwise."""
    launched = _LAUNCH.match(normalize(text or ''))
    return launched.group('name').strip() if launched else None


def _schedule(value: Any, current: dict | None, now: datetime) -> dict | None:
    """The schedule asked for a routine, checked: {'time': '08:30', 'days': [0..6] with 0 = lundi, 'since': the
    local minute it was set} — or, in place of the hour, an event of EVENTS: {'event': 'start', 'days', 'since'}.
    None removes it. « since » keeps an hour already past when the schedule is set from being launched that same
    day; it is kept while the schedule does not change."""
    if value is None:
        return None
    event = str(value.get('event') or '') if isinstance(value, dict) else ''
    if event and event not in EVENTS:
        raise ValueError('Événement de lancement inconnu.')
    hour = str(value.get('time') or '') if isinstance(value, dict) else ''
    if not event and not _HOUR.match(hour):
        raise ValueError('Heure de l’horaire invalide : écrivez-la comme 08:30.')
    try:
        days = sorted({int(d) for d in value.get('days') or []})
    except (TypeError, ValueError):
        days = []
    if not days or days[0] < 0 or days[-1] > 6:
        raise ValueError('Jours de l’horaire invalides : au moins un jour, de lundi à dimanche.')
    wanted = {'event': event, 'days': days} if event else {'time': hour, 'days': days}
    same = bool(current) and {k: v for k, v in current.items() if k != 'since'} == wanted
    return {**wanted, 'since': current['since'] if same else now.strftime(_MINUTE)}


def save_routine(data: dict, routine_id: int | None = None) -> dict:
    """Create (routine_id None) or replace a routine. Raises ValueError with a French message. The schedule (D2)
    changes only when ``data`` has the key « schedule »: saving the name or the steps keeps it."""
    routines = list_routines()
    current = get_routine(routine_id) if routine_id is not None else None
    if routine_id is not None and current is None:
        raise ValueError('Routine introuvable.')
    name = ' '.join(str(data.get('name') or '').split())
    if not 1 <= len(name) <= MAX_NAME:
        raise ValueError(f'Nom de routine requis (1 à {MAX_NAME} caractères).')
    triggers = [' '.join(str(t).split()) for t in (data.get('triggers') or []) if str(t).strip()]
    steps = [' '.join(str(s).split()) for s in (data.get('steps') or []) if str(s).strip()]
    if len(triggers) > MAX_TRIGGERS or any(not 3 <= len(t) <= MAX_TRIGGER for t in triggers):
        raise ValueError(f'Au plus {MAX_TRIGGERS} phrases de lancement, de 3 à {MAX_TRIGGER} caractères chacune.')
    if not 1 <= len(steps) <= MAX_STEPS or any(len(s) > MAX_STEP for s in steps):
        raise ValueError(f'Une routine a de 1 à {MAX_STEPS} étapes, de {MAX_STEP} caractères au plus chacune.')
    for step in steps:
        if match(step) or asks_for_a_routine(step) is not None:
            raise ValueError(f'Une étape ne peut pas lancer une routine : « {step} ».')
    others = [r for r in routines if r.get('id') != (current or {}).get('id')]
    taken = {normalize(p) for r in others for p in [r.get('name', '')] + list(r.get('triggers') or [])}
    for phrase in [name] + triggers:
        if normalize(phrase) in taken:
            raise ValueError(f'« {phrase} » lance déjà une autre routine.')
    if current is None and len(routines) >= MAX_ROUTINES:
        raise ValueError(f'{MAX_ROUTINES} routines au plus.')
    routine = {'id': (current or {}).get('id') or max([r.get('id', 0) for r in routines] + [0]) + 1,
               'name': name, 'triggers': triggers, 'steps': steps,
               'created_at': (current or {}).get('created_at') or _now(), 'updated_at': _now(),
               'runs': (current or {}).get('runs', 0), 'last_run_at': (current or {}).get('last_run_at'),
               'schedule': (_schedule(data['schedule'], (current or {}).get('schedule'), _local_now())
                            if 'schedule' in data else (current or {}).get('schedule')),
               'auto': (current or {}).get('auto'),
               # « Ne plus proposer » (the proposal of D2): changed only when ``data`` has the key « propose ».
               'no_proposal': (not data['propose']) if data.get('propose') is not None
               else bool((current or {}).get('no_proposal'))}
    db.set_setting(KEY, others + [routine] if current is None else
                   [routine if r.get('id') == routine['id'] else r for r in routines])
    return routine


def delete_routine(routine_id: int) -> bool:
    routines = list_routines()
    kept = [r for r in routines if r.get('id') != int(routine_id)]
    if len(kept) == len(routines):
        return False
    db.set_setting(KEY, kept)
    return True


def mark_run(routine_id: int) -> None:
    db.set_setting(KEY, [{**r, 'runs': int(r.get('runs') or 0) + 1, 'last_run_at': _now()}
                         if r.get('id') == int(routine_id) else r for r in list_routines()])


# ------------------------------------------------------------------ D2: launched by its schedule

def next_slot(routine: dict, now: datetime | None = None) -> str | None:
    """The next slot its schedule launches (« 2026-11-16 08:00 »: a day and its hour, local time), None without a
    schedule. Today's slot counts from midnight to midnight — its hour may be past: AURORA started late, it is
    launched late, the same day —, unless it was already launched, or was already past when the schedule was set.
    None too for a routine launched on an event: nobody knows when it will happen."""
    schedule = routine.get('schedule')
    if not isinstance(schedule, dict) or schedule.get('event'):
        return None
    now, spent = now or _local_now(), (routine.get('auto') or {}).get('slot')
    for ahead in range(8):
        day = now + timedelta(days=ahead)
        slot = f"{day:%Y-%m-%d} {schedule.get('time')}"
        if day.weekday() in (schedule.get('days') or []) and slot > str(schedule.get('since') or '') and slot != spent:
            return slot
    return None


def proposal(routine: dict) -> dict | None:
    """« Réussi 5 fois de suite, alors proposé en automatique »: for a routine without a schedule, how many times
    in a row it was launched by hand and went to its end — every step it has now, sent in order, each « done ».
    Read in the journal of the requests, newest launch first, stopped at the first one that did not go to its end
    (the interface closed in the middle, a step in error or still waiting for an agreement). A launch by the
    schedule is not counted: nobody was there to see it. Returns {'count', 'time'} from PROPOSE_AFTER launches —
    ``time``: their usual hour (the median, local, to five minutes), what the interface proposes — and None below,
    with a schedule, or once the person answered « Ne plus proposer »."""
    if routine.get('schedule') or routine.get('no_proposal') or int(routine.get('runs') or 0) < PROPOSE_AFTER:
        return None
    from . import artefacts, runs   # lazy: the journal of the requests, and the steps a launch sent
    steps, count, minutes = [str(s) for s in routine.get('steps') or []], 0, []
    for launch in db.rows("SELECT id, conversation_id, status, decision_json, created_at FROM tasks WHERE intent = "
                          "'routine.run' AND source NOT IN ('routine', 'schedule') AND created_at >= ? "
                          "ORDER BY id DESC LIMIT 200", (str(routine.get('created_at') or '')[:19],)):
        # « created_at >= »: a routine deleted long ago may have had the same number — its launches are not this one's.
        decided = runs.decision_of(launch).get('decision') or {}   # the routine this launch found (its slot)
        if (decided.get('slots') or {}).get('routine_id') != routine.get('id'):
            continue
        sent = artefacts.routine_step_runs({'task_id': launch['id'], 'conversation_id': launch['conversation_id']},
                                           MAX_STEPS)
        found = {r['id']: r for r in db.rows('SELECT id, input_text, status FROM tasks WHERE id IN (%s)'
                                             % ','.join('?' * len(sent)), sent)} if sent else {}
        if launch['status'] != 'done' or [(found.get(i) or {}).get('input_text') for i in sent] != steps \
                or any((found.get(i) or {}).get('status') != 'done' for i in sent):
            break
        count += 1
        try:
            at = datetime.fromisoformat(str(launch['created_at']).replace('Z', '+00:00')).astimezone()
            minutes.append(at.hour * 60 + at.minute)
        except ValueError:
            pass
        if count >= _PROPOSE_READ:
            break
    if count < PROPOSE_AFTER:
        return None
    usual = sorted(minutes)[len(minutes) // 2] // 5 * 5 if minutes else None
    return {'count': count, 'time': None if usual is None else f'{usual // 60:02d}:{usual % 60:02d}'}


def view(routine: dict, now: datetime | None = None) -> dict:
    """A routine as the interface reads it: what is stored, ``next`` (next_slot), computed at each reading,
    ``events``: the events this service can launch a routine on, with their words (EVENTS), and ``proposal``
    (D2, last part): {'count', 'time'} once it went to its end PROPOSE_AFTER times in a row by hand, else None."""
    try:
        proposed = proposal(routine)
    except Exception:   # an unreadable journal never hides the routines themselves
        log.exception('Routine %s: proposal unreadable', routine.get('id'))
        proposed = None
    return {**routine, 'next': next_slot(routine, now), 'events': dict(EVENTS), 'proposal': proposed}


def due(now: datetime | None = None, started: float | None = None) -> list[tuple[dict, str]]:
    """The routines to launch now, each with its slot. By an hour: the next slot of the routine, once its minute is
    reached. On the event « start » (``started``: given at the service's first check only, the seconds since it
    started): the minute of this check, on the days of the routine, once a day at most — a second startup the same
    day launches nothing, nor a startup after the routine already left by itself that day — and only for a
    schedule set before this startup (« since » strictly before its minute: one set just after AURORA started
    waits for the next startup). Launching spends the slot (run_scheduled), so a routine is due once."""
    now = now or _local_now()
    minute, found = now.strftime(_MINUTE), []
    began = '' if started is None else (now - timedelta(seconds=started)).strftime(_MINUTE)
    for routine in list_routines():
        schedule = routine.get('schedule')
        if isinstance(schedule, dict) and schedule.get('event'):
            spent = str((routine.get('auto') or {}).get('slot') or '')
            if schedule['event'] == 'start' and str(schedule.get('since') or '') < began \
                    and now.weekday() in (schedule.get('days') or []) and spent[:10] != minute[:10]:
                found.append((routine, minute))
        elif (slot := next_slot(routine, now)) and slot <= minute:
            found.append((routine, slot))
    return found


def _set_auto(routine_id: int, report: dict) -> None:
    db.set_setting(KEY, [{**r, 'auto': dict(report)} if r.get('id') == int(routine_id) else r
                         for r in list_routines()])


def close_interrupted() -> int:
    """At the start of the service: a launch by the schedule still marked « running » belongs to a process that
    is gone. It is closed as interrupted, so the interface never shows it running for ever. Returns how many."""
    stale = [r for r in list_routines() if isinstance(r.get('auto'), dict) and not r['auto'].get('over')]
    for routine in stale:
        _set_auto(routine['id'], {**routine['auto'], 'over': True, 'interrupted': True})
    return len(stale)


async def run_scheduled(routine: dict, slot: str, handle: Callable[..., Awaitable[dict]],
                        now: datetime | None = None) -> dict:
    """Launches a routine whose hour has come, exactly as its « Lancer » button does: the launch request, then
    each step as an ordinary request (source « routine ») in the same conversation, one after the other.
    ``handle`` is the orchestrator's handle_request — same router, tools, permission cards and journal: a step that
    deletes, sends or spends waits for an agreement as when typed (counted « waiting »), and the next one goes on,
    as in the interface. The slot is written first: whatever happens next, an hour is never launched twice.
    Returns the report kept in the routine (« auto »)."""
    late = (now or _local_now()) - datetime.strptime(slot, _MINUTE)
    report: dict[str, Any] = {'slot': slot, 'at': _now(), 'late_min': max(0, int(late.total_seconds() // 60)),
                              'run_id': None, 'conversation_id': None, 'steps': len(routine.get('steps') or []),
                              'done': 0, 'waiting': 0, 'failed': 0, 'over': False}
    _set_auto(routine['id'], report)
    phrase = f"Lance la routine {routine.get('name', '')}"
    try:
        # The button's sentence; a name it would not find again (« du matin ») is said alone, which always matches.
        launch = await handle(phrase if (match(phrase) or {}).get('id') == routine['id'] else str(routine.get('name')),
                              None, 'schedule', dict(UNATTENDED))
        launched = (launch.get('ui') or {}).get('routine') or {}
        steps = [str(s) for s in launched.get('steps') or []] if launched.get('id') == routine['id'] else []
        report.update(run_id=launch.get('task_id'), conversation_id=launch.get('conversation_id'), steps=len(steps))
        if not steps:
            report['error'] = 'La routine n’a pas pu être lancée.'
        for step in steps:
            try:
                status = (await handle(step, launch.get('conversation_id'), 'routine', dict(UNATTENDED))).get('status')
            except Exception:   # one step that breaks never stops the following ones (as in the interface)
                log.exception('Routine %s: a scheduled step failed', routine.get('id'))
                status = 'error'
            report['done' if status == 'done' else 'waiting' if status == 'waiting_permission' else 'failed'] += 1
            _set_auto(routine['id'], report)
    finally:
        report['over'] = True
        _set_auto(routine['id'], report)
    return report
