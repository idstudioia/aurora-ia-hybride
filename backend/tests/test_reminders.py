"""C3, first part (16/11/2026): reminders, timers and alarms (tasks/reminders.py, tools/reminders.py).

The moment is read in the sentence by the code; the reminder is kept, said by the service when its time comes, and
shown until « Fait ». Temporary AURORA_HOME (TempEnv): the real base is never opened; the clock is given, never read.
"""
import asyncio
import logging
import unittest
from datetime import datetime, timedelta
from unittest import mock

from aurora.core import verify
from aurora.core.normalize import extract_slots
from aurora.security import policy
from aurora.tasks import events, reminders
from aurora.tools.base import run_tool
from tests.doubles_core import FakeClaude, FakeDecision, FakeEngine, FakeOllama, FakeRouter, TempEnv, make_caps
from tests.test_pc_control import RealTools   # the real registry and run_tool, reused

try:   # the HTTP part reuses the API test fixture
    from tests.test_api import NO_TOKEN, ApiTestCase
except Exception:  # pragma: no cover - fastapi missing
    ApiTestCase = None

NOW = datetime(2026, 11, 16, 16, 0, 0)   # a lundi, 16 h: every sentence below is read from that moment


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def at(clock: datetime = NOW):
    """The clock of the reminders, given (a context manager)."""
    return mock.patch.object(reminders, '_local_now', return_value=clock)


class UnderstandTests(unittest.TestCase):
    def test_the_moment_and_the_thing_are_read_in_the_sentence(self):
        for said, due, kind, text in [
            ('Rappelle-moi à 18 h d’appeler Paul', '2026-11-16 18:00:00', 'reminder', 'Appeler Paul'),
            ('rappelle-moi à 18h30 de sortir le poulet', '2026-11-16 18:30:00', 'reminder', 'Sortir le poulet'),
            ('Rappelle-moi dans 20 minutes de sortir le plat du four', '2026-11-16 16:20:00', 'reminder',
             'Sortir le plat du four'),
            ('rappelle moi dans une heure et demie d appeler maman', '2026-11-16 17:30:00', 'reminder', 'Appeler maman'),
            ('Rappelle-moi dans 1h30 que j’ai rendez-vous', '2026-11-16 17:30:00', 'reminder', "J'ai rendez-vous"),
            ('rappelle-moi demain à 9 h : rendez-vous chez le dentiste', '2026-11-17 09:00:00', 'reminder',
             'Rendez-vous chez le dentiste'),
            ('Aurora, rappelle-moi ce soir à 8 h de sortir les poubelles', '2026-11-16 20:00:00', 'reminder',
             'Sortir les poubelles'),
            ('Rappelle-moi à 15 h d’aller chercher le colis', '2026-11-17 15:00:00', 'reminder',     # 15 h is past: demain
             'Aller chercher le colis'),
            ('rappelle-moi lundi à 8 h de payer le loyer', '2026-11-23 08:00:00', 'reminder', 'Payer le loyer'),
            ('rappelle-moi vendredi à 14h15 la réunion', '2026-11-20 14:15:00', 'reminder', 'La réunion'),
            ('Rappelle-moi le 25 à 10 h de payer la facture', '2026-11-25 10:00:00', 'reminder', 'Payer la facture'),
            ('Rappelle-moi le 1er décembre à 9h d’ouvrir le calendrier', '2026-12-01 09:00:00', 'reminder',
             'Ouvrir le calendrier'),
            ('Rappelle-moi le 3/01 à 9 h les vœux', '2027-01-03 09:00:00', 'reminder', 'Les vœux'),
            ('rappelle-moi à midi et demie de manger', '2026-11-17 12:30:00', 'reminder', 'Manger'),
            ('Rappelle-moi à 7 heures du soir d’arroser', '2026-11-16 19:00:00', 'reminder', 'Arroser'),
            ('Rappelle-moi à 18 h de sortir le 2 roues', '2026-11-16 18:00:00', 'reminder', 'Sortir le 2 roues'),
            ('Rappelle-moi dans 2 jours à 9 h de relancer le devis', '2026-11-18 09:00:00', 'reminder',
             'Relancer le devis'),
            ('rappelle-moi dans trois semaines de renouveler l’abonnement', '2026-12-07 16:00:00', 'reminder',
             "Renouveler l'abonnement"),
            ('rappelle-moi dans vingt-cinq minutes de couper le feu', '2026-11-16 16:25:00', 'reminder', 'Couper le feu'),
            ('fais-moi penser à acheter du pain à 17 h', '2026-11-16 17:00:00', 'reminder', 'Acheter du pain'),
            ('Peux-tu me rappeler dans 5 min de rappeler le garage s’il te plaît ?', '2026-11-16 16:05:00', 'reminder',
             'Rappeler le garage'),
            ('Préviens-moi dans un quart d’heure', '2026-11-16 16:15:00', 'reminder', 'Rappel'),
            ('Minuteur de 10 minutes', '2026-11-16 16:10:00', 'timer', 'Minuteur de 10 minutes'),
            ('Mets un minuteur de 2 minutes 30 pour les œufs', '2026-11-16 16:02:30', 'timer',
             'Minuteur de 2 minutes 30 — Les œufs'),
            ('lance un minuteur d’une demi-heure', '2026-11-16 16:30:00', 'timer', 'Minuteur d’une demi-heure'),
            ('minuteur 45 secondes', '2026-11-16 16:00:45', 'timer', 'Minuteur de 45 secondes'),
            ('Réveille-moi à 7 h', '2026-11-17 07:00:00', 'alarm', 'Réveil'),
            ('Réveille-moi demain à 6h45', '2026-11-17 06:45:00', 'alarm', 'Réveil'),
            ('Mets une alarme à 7 h 30', '2026-11-17 07:30:00', 'alarm', 'Alarme'),
        ]:
            with self.subTest(said):
                found = reminders.understand(said, NOW)
                self.assertEqual((found['due'], found['kind'], found['text'], found['why']), (due, kind, text, None))

    def test_what_cannot_be_read_is_said_and_gives_no_moment(self):
        for said, why in [('Rappelle-moi d’appeler Paul', 'when'), ('Rappelle-moi demain d’appeler Paul', 'hour'),
                          ('Rappelle-moi ce soir à 15 h de dormir', 'past'),        # 15 h « ce soir »: gone
                          ('Rappelle-moi le 31 février à 9 h de rire', 'when'),     # a date that does not exist
                          ('Rappelle-moi dans 500 jours de fêter ça', 'far')]:
            with self.subTest(said):
                found = reminders.understand(said, NOW)
                self.assertEqual((found['due'], found['why']), (None, why))
        self.assertEqual(reminders.understand('Rappelle-moi demain d’appeler Paul', NOW)['text'], 'Appeler Paul')

    def test_listing_and_cancelling_are_asked_at_the_start_of_the_sentence(self):
        for said in ('Quels sont mes rappels ?', 'mes rappels', 'Liste mes minuteurs',
                     'Combien de temps reste-t-il au minuteur ?', 'Est-ce que j’ai des rappels ?'):
            self.assertTrue(reminders.understand(said, NOW).get('show'), said)
        for said, kind, every in [('Annule le minuteur', 'timer', False), ('Arrête l’alarme', 'alarm', False),
                                  ('supprime tous mes rappels', 'reminder', True), ('Annule mes rappels', 'reminder', True),
                                  ('Aurora, arrête le compte à rebours', 'timer', False)]:
            found = reminders.understand(said, NOW)
            self.assertEqual((found.get('cancel'), found.get('every')), (kind, every), said)
        kept = reminders.understand('Rappelle-moi à 18 h d’annuler le rappel du dentiste', NOW)     # an order to remember
        self.assertEqual((kept.get('cancel'), kept['due']), (None, '2026-11-16 18:00:00'))

    def test_the_slots_of_the_intent_are_this_reading(self):
        with at():
            self.assertEqual(extract_slots('reminder.set', 'Minuteur de 10 minutes'),
                             reminders.understand('Minuteur de 10 minutes', NOW))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def test_kept_rung_once_snoozed_then_done(self):
        first = reminders.add('Appeler Paul', NOW + timedelta(hours=2), 'reminder', NOW)
        timer = reminders.add('Minuteur de 10 minutes', NOW + timedelta(minutes=10), 'timer', NOW)
        self.assertEqual((first['id'], first['state'], first['due']), (1, 'waiting', '2026-11-16 18:00:00'))
        self.assertEqual([r['id'] for r in reminders.list_reminders()], [timer['id'], first['id']])       # by time
        self.assertEqual(reminders.fire_due(NOW + timedelta(minutes=9, seconds=59)), [])
        rung = reminders.fire_due(NOW + timedelta(minutes=10))
        self.assertEqual([(r['id'], r['state'], r['fired_at']) for r in rung], [(timer['id'], 'due', '2026-11-16 16:10:00')])
        self.assertEqual(reminders.fire_due(NOW + timedelta(minutes=11)), [])                              # said once
        self.assertEqual([r['state'] for r in reminders.list_reminders()], ['due', 'waiting'])            # the due one first
        later = reminders.snooze(timer['id'], 10, NOW + timedelta(minutes=12))
        self.assertEqual((later['state'], later['due'], later['fired_at']), ('waiting', '2026-11-16 16:22:00', None))
        self.assertEqual([r['id'] for r in reminders.fire_due(NOW + timedelta(minutes=22))], [timer['id']])
        self.assertEqual(reminders.remove(timer['id'])['id'], timer['id'])                                 # « Fait »
        self.assertIsNone(reminders.remove(timer['id']))
        self.assertIsNone(reminders.snooze(99, 10, NOW))
        self.assertEqual([r['id'] for r in reminders.list_reminders()], [first['id']])

    def test_one_whose_time_passed_while_the_service_was_off_is_found_late(self):
        reminders.add('Appeler Paul', NOW + timedelta(hours=2), 'reminder', NOW)
        rung = reminders.fire_due(NOW + timedelta(days=1))          # AURORA started the day after
        self.assertEqual([(r['due'], r['fired_at']) for r in rung], [('2026-11-16 18:00:00', '2026-11-17 16:00:00')])

    def test_refusals_in_french_and_the_limit(self):
        for due, why in [(NOW, 'déjà passé'), (NOW - timedelta(minutes=1), 'déjà passé'),
                         (NOW + timedelta(days=400), 'un an')]:
            with self.assertRaisesRegex(ValueError, why):
                reminders.add('x', due, 'reminder', NOW)
        with self.assertRaisesRegex(ValueError, 'Type'):
            reminders.add('x', NOW + timedelta(hours=1), 'agenda', NOW)
        for i in range(reminders.MAX_PENDING):
            reminders.add(f'n° {i}', NOW + timedelta(minutes=i + 1), 'reminder', NOW)
        with self.assertRaisesRegex(ValueError, 'au plus'):
            reminders.add('de trop', NOW + timedelta(hours=5), 'reminder', NOW)

    def test_cancelling_removes_only_what_was_named(self):
        timer = reminders.add('Minuteur de 10 minutes', NOW + timedelta(minutes=10), 'timer', NOW)
        one = reminders.add('Appeler Paul', NOW + timedelta(hours=1), 'reminder', NOW)
        two = reminders.add('Payer le loyer', NOW + timedelta(hours=2), 'reminder', NOW)
        gone, kept = reminders.cancel('reminder', every=False)      # « annule le rappel »: which one? none
        self.assertEqual((gone, [r['id'] for r in kept]), ([], [one['id'], two['id']]))
        gone, kept = reminders.cancel('timer', every=False)         # the only timer
        self.assertEqual(([r['id'] for r in gone], kept), ([timer['id']], []))
        gone, _kept = reminders.cancel('reminder', every=True)
        self.assertEqual(sorted(r['id'] for r in gone), [one['id'], two['id']])
        self.assertEqual(reminders.cancel('alarm', every=True), ([], []))
        self.assertEqual(reminders.list_reminders(), [])


class ToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    async def ask(self, said: str, clock: datetime = NOW) -> dict:
        """The tool, with the slots read in the sentence — as the planner hands them."""
        with at(clock):
            slots = {k: v for k, v in reminders.understand(said, clock).items() if v not in (None, '')}
            return (await run_tool('reminder.set', slots)).to_dict()

    async def test_set_says_the_full_date_and_keeps_it(self):
        result = await self.ask('Rappelle-moi à 18 h d’appeler Paul')
        self.assertTrue(result['ok'], result)
        self.assertIn('Rappel réglé pour aujourd’hui à 18:00 (dans 2 h 00 min) — « Appeler Paul »', result['summary'])
        self.assertIn('lu à voix haute si la voix est activée', result['summary'])
        self.assertEqual([(r['text'], r['due'], r['kind']) for r in reminders.list_reminders()],
                         [('Appeler Paul', '2026-11-16 18:00:00', 'reminder')])
        late = await self.ask('Rappelle-moi à 15 h d’aller chercher le colis')
        self.assertIn('pour demain à 15:00', late['summary'])
        timer = await self.ask('Mets un minuteur de 2 minutes 30 pour les œufs')
        self.assertIn('Minuteur de 2 minutes 30 — Les œufs lancé : fin aujourd’hui à 16:02:30', timer['summary'])
        alarm = await self.ask('Réveille-moi à 7 h')
        self.assertIn('Réveil réglé pour demain à 07:00 (dans 15 h 00 min)', alarm['summary'])
        far = await self.ask('Rappelle-moi le 1er décembre à 9h d’ouvrir le calendrier')
        self.assertIn('mardi 1er décembre à 09:00', far['summary'])

    async def test_nothing_is_kept_when_the_moment_is_not_understood(self):
        for said, words in [('Rappelle-moi d’appeler Paul', 'Je n’ai pas compris à quel moment'),
                            ('Rappelle-moi demain d’appeler Paul', 'Il manque l’heure'),
                            ('Rappelle-moi ce soir à 15 h de dormir', 'déjà passée')]:
            result = await self.ask(said)
            self.assertIn(words, result['summary'], said)
            self.assertIn('Rien n’a été programmé', result['summary'], said)
        self.assertEqual(reminders.list_reminders(), [])

    async def test_list_and_cancel_by_the_sentence(self):
        self.assertIn('Aucun rappel ni minuteur à venir', (await self.ask('Quels sont mes rappels ?'))['summary'])
        self.assertIn('rien à annuler', (await self.ask('Annule le minuteur'))['summary'])
        await self.ask('Minuteur de 10 minutes')
        await self.ask('Rappelle-moi à 18 h d’appeler Paul')
        await self.ask('Rappelle-moi à 19 h de payer le loyer')
        listed = (await self.ask('Quels sont mes rappels ?', NOW + timedelta(minutes=4)))['summary']
        self.assertIn('3 rappels ou minuteurs', listed)
        self.assertIn('1. aujourd’hui à 16:10 (dans 6 min) — « Minuteur de 10 minutes »', listed)
        unsure = (await self.ask('Annule le rappel'))['summary']
        self.assertIn('je ne sais pas lequel annuler : rien n’a été annulé', unsure)
        self.assertEqual(len(reminders.list_reminders()), 3)
        self.assertIn('1 minuteur annulé', (await self.ask('Arrête le minuteur'))['summary'])
        self.assertIn('2 rappels annulés', (await self.ask('Supprime tous mes rappels'))['summary'])
        self.assertEqual(reminders.list_reminders(), [])


class RequestTests(unittest.IsolatedAsyncioTestCase):
    """The whole path of a request: the real rules, the real tool, the answer and its control."""

    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def test_the_rules_send_these_sentences_to_the_reminders(self):
        from aurora.core.router import get_engine
        engine = get_engine()
        for said in ('Rappelle-moi à 18 h d’appeler Paul', 'rappelle moi dans 10 minutes d éteindre le four',
                     'Mets un minuteur de 12 minutes', 'Minuteur de 3 minutes', 'Réveille-moi à 7 h',
                     'Mets une alarme à 6 h 30', 'Fais-moi penser à acheter du pain à 17 h',
                     'Préviens-moi dans un quart d’heure', 'Quels sont mes rappels ?', 'Annule le minuteur',
                     'Supprime tous mes rappels'):
            self.assertEqual(engine.decide_fast(said).intent, 'reminder.set', said)
        for said, other in (('Rappelle-moi ce qu’on avait décidé pour le logo', 'memory.search'),
                            ('Rappelle-moi le nom de mon fichier de thèse', 'memory.search'),
                            ('Quel est le code de mon alarme ?', 'memory.search'),
                            ('Quelle heure est-il ?', 'time.now'), ('Arrête la musique', 'chat.general')):
            self.assertNotEqual(engine.decide_fast(said).intent, 'reminder.set', said)
            self.assertEqual(engine.decide_fast(said).intent, other, said) if other != 'chat.general' else None
        self.assertNotEqual(engine.decide_fast(
            'Invente une phrase courte où tu dis que tu as programmé un rappel pour demain.').intent, 'reminder.set')

    async def test_a_request_keeps_the_reminder_and_its_answer_is_true(self):
        from aurora.core.orchestrator import Deps, Orchestrator
        said = 'Rappelle-moi à 18 h d’appeler Paul'
        with at():
            decision = FakeDecision('reminder.set', method='rule', slots=reminders.understand(said, NOW))
            orch = Orchestrator(Deps(decision_engine=FakeEngine(decision), router=FakeRouter, tools=RealTools(),
                                     ollama=FakeOllama(), claude=FakeClaude(configured=False), capabilities=make_caps()))
            result = await orch.handle_request(said)
        self.assertEqual(result['status'], 'done', result.get('error'))
        self.assertIn('Rappel réglé pour aujourd’hui à 18:00', result['answer'])
        self.assertTrue(result['verification']['ok'], result['verification'])
        tools = [(s['tool'], s['requires_permission']) for s in result['steps'] if s['stage'] == 'tool']
        self.assertEqual(tools, [('reminder.set', False)])                    # words kept in AURORA: no card
        self.assertEqual([r['text'] for r in reminders.list_reminders()], ['Appeler Paul'])

    def test_a_promise_is_true_only_with_the_tool(self):
        said = 'C’est noté, le rappel est programmé pour 18 h.'
        alone = verify.check(answer=said, steps=[], caps={}, mode='equilibre', permissions={}, llm_text_used=True)
        self.assertFalse(next(c for c in alone['checks'] if c['name'] == 'actions')['ok'])
        self.assertIn('reminder.set', verify.ACTION_CLAIMS)
        self.assertTrue(verify.ACTION_CLAIMS['reminder'].search('Je vais vous envoyer un message.'))   # still impossible
        for mode in policy.MODES:
            decision = policy.evaluate('reminder.set', mode=mode)
            self.assertEqual((decision.allowed, decision.requires_permission), (True, False), mode)

    def test_the_agent_cannot_set_a_reminder(self):
        from aurora.core import agent
        from aurora.tools.base import REGISTRY
        self.assertIn('reminder.set', REGISTRY)
        self.assertNotIn('reminder.set', agent.actions(REGISTRY, shell=True))    # a model never gives the moment


if ApiTestCase is not None:
    class ServiceLoopTests(unittest.IsolatedAsyncioTestCase):
        def setUp(self):
            self.env = TempEnv().__enter__()
            self.addCleanup(self.env.__exit__, None, None, None)

        async def test_the_service_says_a_reminder_when_its_time_comes_and_once(self):
            from aurora import main
            reminders.add('Appeler Paul', NOW + timedelta(minutes=5), 'reminder', NOW)
            reminders.add('Payer le loyer', NOW + timedelta(hours=3), 'reminder', NOW)
            with at(NOW + timedelta(minutes=5, seconds=1)):
                task = asyncio.create_task(main._reminder_loop(0.01, 0))
                await asyncio.sleep(0.3)          # many rounds of the loop
                task.cancel()
            said = [e for e in events.list_events(limit=50) if e['kind'] == 'reminder.due']
            self.assertEqual([(e['label'], e['payload']['due'], e['payload']['state']) for e in said],
                             [('Rappel : Appeler Paul', '2026-11-16 16:05:00', 'due')])
            self.assertEqual([r['state'] for r in reminders.list_reminders()], ['due', 'waiting'])

    class ReminderApiTests(ApiTestCase):
        def test_routes_require_the_token(self):
            for method, path in (('get', '/api/reminders'), ('delete', '/api/reminders/1'),
                                 ('post', '/api/reminders/1/snooze')):
                res = getattr(self.client, method)(path, headers=NO_TOKEN, **({'json': {}} if method == 'post' else {}))
                self.assertEqual(res.status_code, 401, path)

        def test_list_snooze_and_done_over_http(self):
            now = datetime.now().replace(microsecond=0)
            kept = reminders.add('Appeler Paul', now + timedelta(hours=1), 'reminder', now)
            listed = self.client.get('/api/reminders').json()
            self.assertRegex(listed['now'], r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$')
            self.assertEqual([(r['id'], r['text'], r['state']) for r in listed['items']],
                             [(kept['id'], 'Appeler Paul', 'waiting')])
            later = self.client.post(f"/api/reminders/{kept['id']}/snooze", json={'minutes': 15})
            self.assertEqual((later.status_code, later.json()['state']), (200, 'waiting'))
            self.assertEqual(self.client.post(f"/api/reminders/{kept['id']}/snooze", json={'minutes': 0}).status_code, 422)
            self.assertEqual(self.client.post('/api/reminders/99/snooze', json={}).status_code, 404)
            self.assertEqual(self.client.delete(f"/api/reminders/{kept['id']}").status_code, 204)
            self.assertEqual(self.client.delete(f"/api/reminders/{kept['id']}").status_code, 404)
            self.assertEqual(self.client.get('/api/reminders').json()['items'], [])


if __name__ == '__main__':
    unittest.main()
