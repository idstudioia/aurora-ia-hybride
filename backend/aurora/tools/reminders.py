"""C3, first part (16/11/2026): the tool « reminder.set » — keeps a reminder, a timer or an alarm, lists those
to come, or cancels them (tasks/reminders.py holds the list and reads the moment in the sentence).

The moment was read in the person's own words by the code (tasks/reminders.understand: the slots of the intent
« reminder.set »); this tool checks it and writes it down. It sends nothing and touches nothing else on the PC.
Every answer says exactly what was done, with the full date and hour — and says so when nothing was.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import Params, Source, ToolResult, fr_duration, now_iso, tool

KIND_WORDS = {'reminder': 'rappel', 'timer': 'minuteur', 'alarm': 'réveil'}
# What the answer says when no moment could be read: nothing was kept, and how to say it.
WHY = {
    'when': 'Je n’ai pas compris à quel moment. Redites-le avec une heure ou une durée, par exemple « rappelle-moi à '
            '18 h d’appeler Paul » ou « rappelle-moi dans 20 minutes de sortir le plat ».',
    'hour': 'Il manque l’heure. Redites-le avec l’heure, par exemple « rappelle-moi demain à 9 h d’appeler Paul ».',
    'past': 'Cette heure est déjà passée aujourd’hui. Redites-le avec une heure à venir, ou avec « demain ».',
    'far': 'Un rappel se programme au plus un an à l’avance.',
}
SHOWN = ('Il s’affichera dans AURORA et sera lu à voix haute si la voix est activée ; AURORA doit être démarrée à ce '
         'moment-là, sinon il sera dit à son prochain démarrage.')


class ReminderParams(Params):
    due: str | None = Field(default=None, max_length=19)     # « 2026-11-16 18:00:00 », local time of the PC
    text: str = Field(default='', max_length=300)
    kind: Literal['reminder', 'timer', 'alarm'] = 'reminder'
    why: str | None = Field(default=None, max_length=10)
    show: bool = False
    cancel: Literal['reminder', 'timer', 'alarm'] | None = None
    every: bool = False


def _line(item: dict, now) -> str:
    from ..tasks import reminders   # lazy: the list is owned by the tasks module
    due = reminders.parse_stamp(item.get('due'))
    if due is None:
        return f"« {item.get('text')} »"
    left = (due - now).total_seconds()
    when = reminders.moment_words(due, now, seconds=item.get('kind') == 'timer')
    state = f'dans {fr_duration(left)}' if left > 0 else 'en attente de votre « Fait »'
    return f"{when} ({state}) — « {item.get('text')} »"


@tool('reminder.set', description='Noter un rappel, un minuteur ou un réveil pour un moment donné, lister ceux à venir '
      'ou les annuler.', action='reminder.set', params=ReminderParams, provenance='clock', timeout_s=5)
def reminder_tool(p: ReminderParams) -> ToolResult:
    from ..tasks import reminders   # lazy: the list is owned by the tasks module
    now = reminders._local_now().replace(microsecond=0)
    source = [Source(title='Rappels d’AURORA (horloge du PC)', kind='local', retrieved_at=now_iso())]

    def done(summary: str, **data) -> ToolResult:
        return ToolResult(ok=True, data={'now': now.strftime('%Y-%m-%d %H:%M:%S'), **data}, summary=summary,
                          provenance='clock', sources=source)

    if p.cancel:
        gone, kept = reminders.cancel(p.cancel, p.every)
        word = KIND_WORDS[p.cancel]
        if gone:
            lines = ' ; '.join(_line(r, now) for r in gone)
            return done(f"{len(gone)} {word}{'s' if len(gone) > 1 else ''} annulé{'s' if len(gone) > 1 else ''} : "
                        f'{lines}.', removed=gone, action='cancel')
        if kept:
            lines = '\n'.join(f'{i}. {_line(r, now)}' for i, r in enumerate(kept, 1))
            return done(f'Vous avez {len(kept)} {word}s à venir et je ne sais pas lequel annuler : rien n’a été '
                        f'annulé.\n{lines}\nSupprimez celui que vous voulez dans la page Tâches, ou dites « annule '
                        f'tous mes {word}s ».', removed=[], action='cancel')
        return done(f'Aucun {word} à venir : rien à annuler.', removed=[], action='cancel')

    if p.show:
        items = reminders.list_reminders()
        if not items:
            return done('Aucun rappel ni minuteur à venir.', items=[], action='list')
        lines = '\n'.join(f'{i}. {_line(r, now)}' for i, r in enumerate(items, 1))
        return done(f"{len(items)} rappel{'s' if len(items) > 1 else ''} ou minuteur{'s' if len(items) > 1 else ''} :"
                    f'\n{lines}', items=items, action='list')

    due = reminders.parse_stamp(p.due) if p.due else None
    if due is None:
        return done(f"{WHY.get(p.why or 'when', WHY['when'])} Rien n’a été programmé.", created=None, action='none')
    try:
        item = reminders.add(p.text, due, p.kind, now)
    except ValueError as exc:
        return done(f'{exc} Rien n’a été programmé.', created=None, action='none')
    when = reminders.moment_words(due, now, seconds=p.kind == 'timer')
    left = fr_duration((due - now).total_seconds())
    if p.kind == 'timer':
        summary = f"{item['text']} lancé : fin {when}. {SHOWN}"
    elif p.kind == 'alarm':
        summary = f"Réveil réglé pour {when} (dans {left}) — « {item['text']} ». {SHOWN}"
    else:
        summary = f"Rappel réglé pour {when} (dans {left}) — « {item['text']} ». {SHOWN}"
    return done(summary, created=item, action='set')
