"""D1 « Routines » (reviews/etapes-restantes.md): a named list of ordinary requests, launched by a phrase.

The steps are sent by the interface as if typed (same rights, same permission cards); the service stores the
routines, recognises their phrase and answers with the steps to send (RunResult.ui.routine).
Temporary AURORA_HOME (TempEnv): the real base is never opened.
"""
import asyncio
import logging
import unittest
from datetime import datetime
from unittest import mock

from aurora.tasks import artefacts, routines, runs
from tests.doubles_core import FakeDecision, TempEnv, build
from tests.test_orchestrator import CoreTestCase

try:   # the HTTP part reuses the API test fixture
    from tests.test_api import NO_TOKEN, ApiTestCase
except Exception:  # pragma: no cover - fastapi missing
    ApiTestCase = None

MORNING = {'name': 'Matin', 'triggers': ['Bonjour AURORA, on commence'],
           'steps': ['Quelle heure est-il ?', 'Météo à Paris', 'Quoi de neuf aujourd’hui ?']}
MONDAY = datetime(2026, 11, 16)   # a lundi (weekday 0), local time: the schedules of D2 are tried around it


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def test_create_update_delete(self):
        saved = routines.save_routine(MORNING)
        self.assertEqual((saved['id'], saved['name'], saved['runs']), (1, 'Matin', 0))
        self.assertEqual(saved['steps'], MORNING['steps'])
        changed = routines.save_routine({**MORNING, 'steps': ['Quelle heure est-il ?']}, saved['id'])
        self.assertEqual((changed['id'], changed['created_at'], changed['steps']),
                         (saved['id'], saved['created_at'], ['Quelle heure est-il ?']))
        routines.mark_run(saved['id'])
        self.assertEqual(routines.get_routine(saved['id'])['runs'], 1)
        self.assertTrue(routines.delete_routine(saved['id']))
        self.assertFalse(routines.delete_routine(saved['id']))
        self.assertEqual(routines.list_routines(), [])

    def test_refusals_in_french(self):
        routines.save_routine(MORNING)
        for bad, why in [({'name': '', 'steps': ['x']}, 'Nom'),
                         ({'name': 'Vide', 'steps': []}, 'étapes'),
                         ({'name': 'Longue', 'steps': [f'demande {i}' for i in range(11)]}, 'étapes'),
                         ({'name': 'Boucle', 'steps': ['Lance la routine Matin']}, 'ne peut pas lancer'),
                         ({'name': 'Boucle 2', 'steps': ['Bonjour AURORA, on commence']}, 'ne peut pas lancer'),
                         ({'name': 'matin', 'steps': ['Quelle heure est-il ?']}, 'lance déjà'),
                         ({'name': 'Soir', 'triggers': ['Bonjour aurora on commence'], 'steps': ['x']}, 'lance déjà')]:
            with self.subTest(bad), self.assertRaisesRegex(ValueError, why):
                routines.save_routine(bad)
        with self.assertRaisesRegex(ValueError, 'introuvable'):
            routines.save_routine(MORNING, 99)
        self.assertEqual(len(routines.list_routines()), 1)                    # a refusal saves nothing

    def test_the_phrase_that_launches_a_routine(self):
        saved = routines.save_routine(MORNING)
        for text in ('Bonjour AURORA, on commence', 'bonjour aurora on commence !', 'Matin',
                     'Lance la routine Matin', 'lance ma routine du matin', 'Démarre la routine matin'):
            with self.subTest(text):
                self.assertEqual((routines.match(text) or {}).get('id'), saved['id'])
        for text in ('Quelle heure est-il ?', 'Bonjour AURORA', 'Lance la routine du soir', ''):
            with self.subTest(text):
                self.assertIsNone(routines.match(text))
        self.assertEqual(routines.asks_for_a_routine('Lance la routine du soir'), 'soir')
        self.assertIsNone(routines.asks_for_a_routine('Quelle heure est-il ?'))


class LaunchFlowTests(CoreTestCase):
    async def ask(self, text: str, decision: str = 'chat.general') -> dict:
        orch, *_ = build(FakeDecision(decision))
        return await orch.handle_request(text)

    async def test_its_phrase_launches_the_routine_and_hands_the_steps_to_the_interface(self):
        saved = routines.save_routine(MORNING)
        result = await self.ask('Bonjour AURORA, on commence')    # the router would say « conversation »
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['decision']['intent'], 'routine.run')
        self.assertIn('Phrase de la routine « Matin »', result['decision']['explanation'])
        self.assertEqual(result['ui'], {'routine': {'id': saved['id'], 'name': 'Matin', 'steps': MORNING['steps']}})
        self.assertIn('3 étape(s), lancées l’une après l’autre ci-dessous', result['answer'])   # never « done »
        self.assertIn('2. Météo à Paris', result['answer'])
        self.assertEqual(routines.get_routine(saved['id'])['runs'], 1)
        self.assertEqual([s['stage'] for s in result['steps'] if s['stage'] == 'tool'], [])   # no tool of its own

    async def test_the_steps_sent_by_a_routine_are_found_again_by_its_report(self):
        routines.save_routine(MORNING)
        orch, *_ = build(FakeDecision('chat.general'))
        launch = await orch.handle_request('Lance la routine Matin')
        sent = [await orch.handle_request(step, launch['conversation_id'], 'routine')
                for step in launch['ui']['routine']['steps']]
        self.assertEqual({runs.get_run(r['task_id'])['source'] for r in sent}, {'routine'})   # was « api »
        self.assertEqual(artefacts.routine_step_runs(launch, 3), [r['task_id'] for r in sent])

    async def test_an_unknown_routine_says_which_ones_exist(self):
        routines.save_routine(MORNING)
        result = await self.ask('Lance la routine du soir', decision='routine.run')
        self.assertIn('Je ne trouve pas de routine « soir »', result['answer'])
        self.assertIn('« Matin »', result['answer'])
        self.assertIsNone(result['ui'])
        routines.delete_routine(1)
        none = await self.ask('Lance la routine du soir', decision='routine.run')
        self.assertIn('Aucune routine n’est enregistrée', none['answer'])


class ScheduleTests(unittest.TestCase):
    """D2, first part: an hour and days kept in the routine; ``due`` says when the service must launch it."""

    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def save(self, at: datetime, schedule, routine_id=None, **more) -> dict:
        with mock.patch.object(routines, '_local_now', return_value=at):
            return routines.save_routine({**MORNING, **more, 'schedule': schedule}, routine_id)

    def test_a_schedule_is_checked_and_kept(self):
        saved = self.save(MONDAY.replace(hour=7), {'time': '08:30', 'days': [4, 0, 0]})
        self.assertEqual(saved['schedule'], {'time': '08:30', 'days': [0, 4], 'since': '2026-11-16 07:00'})
        self.assertEqual(saved['auto'], None)
        renamed = routines.save_routine({**MORNING, 'name': 'Matin calme'}, saved['id'])     # no key « schedule »
        self.assertEqual(renamed['schedule'], saved['schedule'])
        again = self.save(MONDAY.replace(hour=15), {'time': '08:30', 'days': [0, 4]}, saved['id'])
        self.assertEqual(again['schedule']['since'], '2026-11-16 07:00')                    # unchanged: same « since »
        moved = self.save(MONDAY.replace(hour=15), {'time': '09:00', 'days': [0, 4]}, saved['id'])
        self.assertEqual(moved['schedule']['since'], '2026-11-16 15:00')
        self.assertIsNone(self.save(MONDAY, None, saved['id'])['schedule'])
        for bad, why in [({'time': '8:30', 'days': [0]}, 'Heure'), ({'time': '24:00', 'days': [0]}, 'Heure'),
                         ({'days': [0]}, 'Heure'), ('08:30', 'Heure'), ({'time': '08:30', 'days': []}, 'Jours'),
                         ({'time': '08:30', 'days': [7]}, 'Jours'), ({'time': '08:30', 'days': ['lundi']}, 'Jours')]:
            with self.subTest(bad), self.assertRaisesRegex(ValueError, why):
                self.save(MONDAY, bad, saved['id'])
        self.assertIsNone(routines.get_routine(saved['id'])['schedule'])                    # a refusal saves nothing

    def test_due_from_its_hour_once_and_only_on_its_days(self):
        saved = self.save(MONDAY.replace(hour=7), {'time': '08:00', 'days': [0, 2]})
        slot = '2026-11-16 08:00'
        self.assertEqual(routines.due(MONDAY.replace(hour=7, minute=59)), [])
        for late in (MONDAY.replace(hour=8), MONDAY.replace(hour=8, minute=1), MONDAY.replace(hour=23, minute=59)):
            self.assertEqual([(r['id'], s) for r, s in routines.due(late)], [(saved['id'], slot)], late)
        self.assertEqual(routines.due(MONDAY.replace(day=17, hour=9)), [])                  # mardi: not one of its days
        self.assertEqual(routines.view(saved, MONDAY.replace(hour=7, minute=59))['next'], slot)   # what the panel shows
        routines._set_auto(saved['id'], {'slot': slot, 'over': True})
        self.assertEqual(routines.due(MONDAY.replace(hour=8, minute=5)), [])                # launched: never twice
        self.assertEqual(routines.next_slot(routines.get_routine(saved['id']), MONDAY.replace(hour=8, minute=5)),
                         '2026-11-18 08:00')                                                # mercredi, its other day
        self.assertEqual([s for _r, s in routines.due(MONDAY.replace(day=18, hour=8))], ['2026-11-18 08:00'])
        self.assertIsNone(routines.view({**MORNING, 'schedule': None})['next'])

    def test_an_hour_already_past_when_set_waits_for_the_next_day(self):
        self.save(MONDAY.replace(hour=15), {'time': '08:00', 'days': list(range(7))})
        self.assertEqual(routines.due(MONDAY.replace(hour=15, minute=1)), [])
        self.assertEqual(routines.due(MONDAY.replace(hour=23, minute=59)), [])
        self.assertEqual([s for _r, s in routines.due(MONDAY.replace(day=17, hour=8))], ['2026-11-17 08:00'])
        self.save(MONDAY.replace(day=17, hour=9), {'time': '09:00', 'days': list(range(7))}, 1)   # set at its very minute
        self.assertEqual(routines.due(MONDAY.replace(day=17, hour=9, minute=30)), [])

    def test_on_an_event_in_place_of_an_hour(self):
        """D2, second part: « start » — AURORA has just started. On its days only, once a day at most."""
        saved = self.save(MONDAY.replace(hour=7), {'event': 'start', 'days': [2, 0]})
        self.assertEqual(saved['schedule'], {'event': 'start', 'days': [0, 2], 'since': '2026-11-16 07:00'})
        seen = routines.view(saved, MONDAY.replace(hour=7))
        self.assertEqual((seen['next'], seen['events']), (None, {'start': 'au démarrage d’AURORA'}))
        nine = MONDAY.replace(hour=9, minute=12)          # the service's first check, 45 s after it started
        self.assertEqual(routines.due(nine), [])                                            # not a startup: nothing
        self.assertEqual([(r['id'], s) for r, s in routines.due(nine, started=45.0)], [(saved['id'], '2026-11-16 09:12')])
        self.assertEqual(routines.due(MONDAY.replace(day=17, hour=9), started=45.0), [])    # mardi: not one of its days
        routines._set_auto(saved['id'], {'slot': '2026-11-16 09:12', 'over': True})
        self.assertEqual(routines.due(MONDAY.replace(hour=14), started=45.0), [])           # a second startup that day
        self.assertEqual([s for _r, s in routines.due(MONDAY.replace(day=18, hour=6, minute=50), started=45.0)],
                         ['2026-11-18 06:50'])
        for bad, why in [({'event': 'reboot', 'days': [0]}, 'Événement'), ({'event': 'start', 'days': []}, 'Jours')]:
            with self.subTest(bad), self.assertRaisesRegex(ValueError, why):
                self.save(MONDAY, bad, saved['id'])
        hour = self.save(MONDAY.replace(hour=15), {'time': '08:00', 'days': [0, 2]}, saved['id'])   # back to an hour
        self.assertEqual(hour['schedule'], {'time': '08:00', 'days': [0, 2], 'since': '2026-11-16 15:00'})
        self.assertEqual(routines.due(MONDAY.replace(hour=16), started=45.0), [])
        # Set once AURORA has started (found on the real trial, 16/11/2026: it left at once): it waits for the next startup.
        just = self.save(MONDAY.replace(day=17, hour=9, minute=30), {'event': 'start', 'days': list(range(7))},
                         name='Soir', triggers=[])
        self.assertEqual(routines.due(MONDAY.replace(day=17, hour=9, minute=30, second=40), started=45.0), [])
        self.assertEqual(routines.due(MONDAY.replace(day=17, hour=9, minute=31, second=5), started=70.0), [])
        self.assertEqual([r['id'] for r, _s in routines.due(MONDAY.replace(day=17, hour=13), started=45.0)], [just['id']])

    def test_a_launch_left_running_by_a_stopped_service_is_closed(self):
        saved = self.save(MONDAY.replace(hour=7), {'time': '08:00', 'days': [0]})
        routines._set_auto(saved['id'], {'slot': '2026-11-16 08:00', 'steps': 3, 'done': 1, 'over': False})
        self.assertEqual(routines.close_interrupted(), 1)
        auto = routines.get_routine(saved['id'])['auto']
        self.assertEqual((auto['over'], auto['interrupted'], auto['done']), (True, True, 1))
        self.assertEqual(routines.close_interrupted(), 0)


class ScheduledLaunchTests(CoreTestCase):
    """The service launches a due routine through the same requests as its « Lancer » button."""

    def due_routine(self) -> tuple[dict, str]:
        with mock.patch.object(routines, '_local_now', return_value=MONDAY.replace(hour=7)):
            routines.save_routine({**MORNING, 'schedule': {'time': '08:00', 'days': [0]}})
        (routine, slot), = routines.due(MONDAY.replace(hour=8, minute=7))
        return routine, slot

    async def test_its_hour_launches_the_routine_and_sends_every_step(self):
        routine, slot = self.due_routine()
        orch, *_ = build(FakeDecision('chat.general'))
        report = await routines.run_scheduled(routine, slot, orch.handle_request, MONDAY.replace(hour=8, minute=7))
        self.assertEqual((report['steps'], report['done'], report['waiting'], report['failed']), (3, 3, 0, 0))
        self.assertEqual((report['slot'], report['late_min'], report['over']), ('2026-11-16 08:00', 7, True))
        launch = runs.get_run(report['run_id'])
        self.assertEqual((launch['source'], launch['input_text']), ('schedule', 'Lance la routine Matin'))
        sent = artefacts.routine_step_runs({'task_id': report['run_id'], 'conversation_id': report['conversation_id']}, 3)
        self.assertEqual([runs.get_run(i)['input_text'] for i in sent], MORNING['steps'])    # same conversation, in order
        kept = routines.get_routine(routine['id'])
        self.assertEqual((kept['auto'], kept['runs']), (report, 1))
        self.assertEqual(routines.due(MONDAY.replace(hour=8, minute=30)), [])               # the hour is spent
        for run_id in [report['run_id'], *sent]:                    # each request says nobody is in front of the screen
            self.assertEqual(runs.decision_of(runs.get_run(run_id))['options'], {'unattended': True})

    async def test_no_agreement_given_beforehand_applies_when_nobody_is_there(self):
        from aurora.tools import youtube_comments
        orch, *_ = build(FakeDecision('chat.general'))
        self.mode('equilibre')
        state = {'settings': {'mode': 'equilibre'}, 'sensitivity': 'normal', 'run': {'conversation_id': 3}, 'options': {}}
        alone = {**state, 'options': {'unattended': True}}
        granted = {'reason': 'Accord donné pour toute la conversation.'}
        with mock.patch.object(orch.deps.consent, 'conversation_consent', return_value=granted), \
                mock.patch.object(youtube_comments, 'auto_publish', return_value=True):
            self.assertEqual(orch._standing_cloud_consent(state), granted)                  # typed: the consent holds
            self.assertIsNone(orch._standing_cloud_consent(alone))                          # by the schedule: a click
            self.assertIsNotNone(orch._standing_comment_consent('youtube.comment', state))
            self.assertIsNone(orch._standing_comment_consent('youtube.comment', alone))

    async def test_a_step_that_breaks_or_waits_never_stops_the_next_ones(self):
        routine, slot = self.due_routine()
        asked = []

        async def handle(text, conversation_id, source, options):
            asked.append((text, conversation_id, source))
            self.assertEqual(options, {'unattended': True})
            if source == 'schedule':
                return {'task_id': 5, 'conversation_id': 9, 'status': 'done',
                        'ui': {'routine': {'id': routine['id'], 'name': 'Matin', 'steps': MORNING['steps']}}}
            if text == 'Météo à Paris':
                raise RuntimeError('panne')
            return {'status': 'waiting_permission' if text.startswith('Quoi') else 'done'}

        report = await routines.run_scheduled(routine, slot, handle, MONDAY.replace(hour=8))
        self.assertEqual(asked, [('Lance la routine Matin', None, 'schedule')] + [(s, 9, 'routine') for s in MORNING['steps']])
        self.assertEqual((report['done'], report['failed'], report['waiting'], report['over']), (1, 1, 1, True))

    async def test_a_routine_that_cannot_be_launched_is_said_and_its_hour_is_still_spent(self):
        routine, slot = self.due_routine()

        async def handle(text, conversation_id, source, options):
            return {'task_id': 5, 'conversation_id': 9, 'status': 'done', 'ui': None}

        report = await routines.run_scheduled(routine, slot, handle, MONDAY.replace(hour=8))
        self.assertEqual((report['steps'], report['done'], report['over']), (0, 0, True))
        self.assertIn('pas pu être lancée', report['error'])
        self.assertEqual(routines.due(MONDAY.replace(hour=8, minute=1)), [])


class ProposalTests(CoreTestCase):
    """D2, last part: « réussi 5 fois de suite, alors proposé en automatique » — counted in the journal of the
    requests, never guessed; only a proposal, that the person can refuse for good."""

    async def by_hand(self, orch, times: int = 1, steps: int | None = None) -> None:
        """Launches « Matin » as the interface does: the launch, then its steps (``steps``: only the first ones)."""
        for _ in range(times):
            launch = await orch.handle_request('Lance la routine Matin')
            for step in launch['ui']['routine']['steps'][:steps]:
                await orch.handle_request(step, launch['conversation_id'], 'routine')

    def proposed(self, routine_id: int):
        return routines.view(routines.get_routine(routine_id))['proposal']

    async def test_five_launches_by_hand_that_went_to_their_end_and_only_then(self):
        saved = routines.save_routine(MORNING)
        orch, *_ = build(FakeDecision('chat.general'))
        await self.by_hand(orch, 4)
        self.assertIsNone(self.proposed(saved['id']))
        await self.by_hand(orch)
        found = self.proposed(saved['id'])
        self.assertEqual(found['count'], 5)
        self.assertRegex(found['time'], r'^([01]\d|2[0-3]):[0-5][05]$')                   # their usual hour, to 5 min
        await self.by_hand(orch, steps=2)                 # the interface closed before the last step: the row is broken
        self.assertIsNone(self.proposed(saved['id']))
        await self.by_hand(orch, 5)
        self.assertEqual(self.proposed(saved['id'])['count'], 5)                           # counted from that launch on
        await self.by_hand(orch, 9)
        self.assertEqual(self.proposed(saved['id'])['count'], 12)                          # no more launches are read

    async def test_a_step_in_error_breaks_the_row(self):
        saved = routines.save_routine(MORNING)
        orch, *_ = build(FakeDecision('chat.general'))
        await self.by_hand(orch, 5)
        launch = await orch.handle_request('Lance la routine Matin')
        sent = [await orch.handle_request(step, launch['conversation_id'], 'routine') for step in MORNING['steps']]
        self.assertEqual(self.proposed(saved['id'])['count'], 6)
        runs.set_run_status(sent[1]['task_id'], 'error', error='panne', emit_event=False)
        self.assertIsNone(self.proposed(saved['id']))

    async def test_a_launch_by_the_schedule_is_not_counted_and_a_schedule_ends_the_proposal(self):
        saved = routines.save_routine(MORNING)
        orch, *_ = build(FakeDecision('chat.general'))
        await self.by_hand(orch, 4)
        with mock.patch.object(routines, '_local_now', return_value=MONDAY.replace(hour=7)):
            timed = routines.save_routine({**MORNING, 'schedule': {'time': '08:00', 'days': [0]}}, saved['id'])
        await routines.run_scheduled(timed, '2026-11-16 08:00', orch.handle_request, MONDAY.replace(hour=8))
        await self.by_hand(orch)                          # 5 by hand around one launch by the schedule
        self.assertIsNone(self.proposed(saved['id']))     # it has a schedule: nothing to propose
        routines.save_routine({**MORNING, 'schedule': None}, saved['id'])
        self.assertEqual(self.proposed(saved['id'])['count'], 5)                           # the 5 by hand, not 6

    async def test_changed_steps_start_again_and_a_refusal_is_kept(self):
        saved = routines.save_routine(MORNING)
        orch, *_ = build(FakeDecision('chat.general'))
        await self.by_hand(orch, 5)
        self.assertEqual(self.proposed(saved['id'])['count'], 5)
        refused = routines.save_routine({**MORNING, 'propose': False}, saved['id'])        # « Ne plus proposer »
        self.assertEqual((refused['no_proposal'], self.proposed(saved['id'])), (True, None))
        renamed = routines.save_routine({**MORNING, 'triggers': []}, saved['id'])          # no key « propose »: kept
        self.assertTrue(renamed['no_proposal'])
        routines.save_routine({**MORNING, 'propose': True}, saved['id'])
        self.assertEqual(self.proposed(saved['id'])['count'], 5)
        routines.save_routine({**MORNING, 'steps': MORNING['steps'][:2]}, saved['id'])     # not the routine that succeeded
        self.assertIsNone(self.proposed(saved['id']))


if ApiTestCase is not None:
    class ServiceLoopTests(CoreTestCase):
        async def test_the_service_loop_launches_what_is_due_and_only_once(self):
            from aurora import main
            from aurora.tasks import events
            with mock.patch.object(routines, '_local_now', return_value=MONDAY.replace(hour=7)):
                saved = routines.save_routine({**MORNING, 'schedule': {'time': '08:00', 'days': [0]}})
            orch, *_ = build(FakeDecision('chat.general'))
            with mock.patch.object(routines, '_local_now', return_value=MONDAY.replace(hour=8, minute=2)), \
                    mock.patch.object(main, '_orchestrator', return_value=orch):
                task = asyncio.create_task(main._routine_loop(0.01, 0))
                for _ in range(500):
                    await asyncio.sleep(0.02)
                    if (routines.get_routine(saved['id'])['auto'] or {}).get('over'):
                        break
                await asyncio.sleep(0.2)          # more rounds of the loop: the same hour is not launched again
                task.cancel()
            kept = routines.get_routine(saved['id'])
            self.assertEqual((kept['runs'], kept['auto']['done'], kept['auto']['late_min']), (1, 3, 2))
            said = [e for e in events.list_events(limit=200) if e['kind'] == 'routine.scheduled']
            self.assertEqual([(e['payload']['routine_id'], e['payload']['done']) for e in said], [(saved['id'], 3)])

        async def test_the_startup_launches_what_is_set_on_it_once_a_day(self):
            from aurora import main
            from aurora.tasks import events
            with mock.patch.object(routines, '_local_now', return_value=MONDAY.replace(hour=7)):
                saved = routines.save_routine({**MORNING, 'schedule': {'event': 'start', 'days': [0]}})
            orch, *_ = build(FakeDecision('chat.general'))

            async def one_startup(wait_for: int) -> asyncio.Task:
                """AURORA starts: its loop, left running once the routine ``wait_for`` has been launched."""
                task = asyncio.create_task(main._routine_loop(0.01, 0))
                for _ in range(500):
                    await asyncio.sleep(0.02)
                    if (routines.get_routine(wait_for)['auto'] or {}).get('over'):
                        break
                return task

            with mock.patch.object(routines, '_local_now', return_value=MONDAY.replace(hour=9, minute=12)), \
                    mock.patch.object(main, '_orchestrator', return_value=orch):
                first = await one_startup(saved['id'])
                later = routines.save_routine({'name': 'Soir', 'steps': ['Quelle heure est-il ?'],
                                               'schedule': {'event': 'start', 'days': [0]}})   # set while it runs
                await asyncio.sleep(0.2)          # more rounds of the loop: they are not a startup
                first.cancel()
                self.assertIsNone(routines.get_routine(later['id'])['auto'])
            with mock.patch.object(routines, '_local_now', return_value=MONDAY.replace(hour=14)), \
                    mock.patch.object(main, '_orchestrator', return_value=orch):
                second = await one_startup(later['id'])   # started again that day: « Soir » leaves, « Matin » does not
                await asyncio.sleep(0.2)
                second.cancel()
            kept = routines.get_routine(saved['id'])
            self.assertEqual((kept['runs'], kept['auto']['slot'], kept['auto']['done'], kept['auto']['late_min']),
                             (1, '2026-11-16 09:12', 3, 0))
            self.assertEqual(routines.get_routine(later['id'])['runs'], 1)
            said = [e['label'] for e in events.list_events(limit=200, ascending=True) if e['kind'] == 'routine.scheduled']
            self.assertEqual(said, ['Routine « Matin » lancée au démarrage d’AURORA',
                                    'Routine « Soir » lancée au démarrage d’AURORA'])

    class RoutineApiTests(ApiTestCase):
        def test_routes_require_the_token(self):
            for method, path in (('get', '/api/routines'), ('post', '/api/routines'), ('put', '/api/routines/1'),
                                 ('delete', '/api/routines/1')):
                res = getattr(self.client, method)(path, headers=NO_TOKEN, **({'json': MORNING} if method in ('post', 'put') else {}))
                self.assertEqual(res.status_code, 401, path)
            self.assertEqual(routines.list_routines(), [])

        def test_create_list_update_delete_over_http(self):
            created = self.client.post('/api/routines', json=MORNING)
            self.assertEqual(created.status_code, 200, created.text)
            rid = created.json()['id']
            self.assertEqual([r['name'] for r in self.client.get('/api/routines').json()], ['Matin'])
            bad = self.client.post('/api/routines', json={'name': 'Boucle', 'steps': ['Lance la routine Matin']})
            self.assertEqual(bad.status_code, 422)
            self.assertIn('ne peut pas lancer', bad.json()['detail'])
            updated = self.client.put(f'/api/routines/{rid}', json={**MORNING, 'name': 'Matin calme'})
            self.assertEqual(updated.json()['name'], 'Matin calme')
            self.assertEqual(self.client.put('/api/routines/999', json=MORNING).status_code, 404)
            self.assertEqual(self.client.delete(f'/api/routines/{rid}').status_code, 204)
            self.assertEqual(self.client.delete(f'/api/routines/{rid}').status_code, 404)

        def test_the_schedule_over_http(self):
            rid = self.client.post('/api/routines', json=MORNING).json()['id']
            self.assertIsNone(self.client.get('/api/routines').json()[0]['schedule'])
            hour = {'time': '08:30', 'days': [0, 1, 2, 3, 4]}
            set_ = self.client.put(f'/api/routines/{rid}', json={**MORNING, 'schedule': hour})
            self.assertEqual(set_.status_code, 200, set_.text)
            self.assertEqual({k: set_.json()['schedule'][k] for k in hour}, hour)
            self.assertRegex(set_.json()['next'], r'^\d{4}-\d\d-\d\d 08:30$')
            self.assertEqual(self.client.get('/api/routines').json()[0]['next'], set_.json()['next'])
            kept = self.client.put(f'/api/routines/{rid}', json={**MORNING, 'name': 'Matin calme'})   # not sent: kept
            self.assertEqual(kept.json()['schedule'], set_.json()['schedule'])
            for bad, why in (({'time': '25:00', 'days': [0]}, 'Heure'), ({'time': '08:30', 'days': []}, 'Jours')):
                res = self.client.put(f'/api/routines/{rid}', json={**MORNING, 'name': 'Matin calme', 'schedule': bad})
                self.assertEqual(res.status_code, 422, bad)
                self.assertIn(why, res.json()['detail'])
            start = self.client.put(f'/api/routines/{rid}', json={**MORNING, 'name': 'Matin calme',
                                                                  'schedule': {'event': 'start', 'days': [5, 6]}})
            self.assertEqual(start.status_code, 200, start.text)                # D2, second part: on an event
            self.assertEqual((start.json()['schedule']['event'], start.json()['next'], start.json()['events']),
                             ('start', None, {'start': 'au démarrage d’AURORA'}))
            unknown = self.client.put(f'/api/routines/{rid}', json={**MORNING, 'name': 'Matin calme',
                                                                    'schedule': {'event': 'reboot', 'days': [0]}})
            self.assertEqual(unknown.status_code, 422)
            self.assertIn('Événement', unknown.json()['detail'])
            off = self.client.put(f'/api/routines/{rid}', json={**MORNING, 'name': 'Matin calme', 'schedule': None})
            self.assertIsNone(off.json()['schedule'])
            self.assertEqual((off.json()['proposal'], off.json()['no_proposal']), (None, False))   # D2, last part
            no = self.client.put(f'/api/routines/{rid}', json={**MORNING, 'name': 'Matin calme', 'propose': False})
            self.assertEqual((no.status_code, no.json()['no_proposal']), (200, True))               # « Ne plus proposer »

        def test_nobody_outside_the_service_can_claim_to_be_the_schedule(self):
            res = self.client.post('/api/ask', json={'text': 'Quelle heure est-il ?', 'source': 'schedule'})
            self.assertEqual(res.status_code, 422)


if __name__ == '__main__':
    unittest.main()
