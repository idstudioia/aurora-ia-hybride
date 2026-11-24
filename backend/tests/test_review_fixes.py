"""Non-regression tests for the defects of the 2026-10-28 review (reviews/correction-backend.md).

Every test runs on a temporary AURORA_HOME / AURORA_DB_PATH (tests.doubles_core.TempEnv), offline,
with doubles for the router, tools and providers when the orchestrator is involved.
"""
import asyncio
import logging
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from aurora import config, db
from aurora.core import capabilities
from aurora.security.redaction import redact, sensitivity_of
from aurora.tasks import events, runs
from tests.doubles_core import (FakeClaude, FakeDecision, FakeOllama, FakeTools, TempEnv, build, make_caps,
                                snapshot_response)

ORIGIN = 'http://127.0.0.1:5173'
BASE = 'http://127.0.0.1:8765'


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def kinds(run_id: int) -> list[str]:
    return [e['kind'] for e in events.list_events(limit=500, run_id=run_id, ascending=True)]


class CoreCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        events.bus.detach_loop()

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def mode(self, mode: str) -> None:
        config.update_settings({'mode': mode})

    async def ask_granting_cloud(self, orch, text: str, **kwargs) -> dict:
        """Ask, then approve the cloud preview as the user would: every send is shown first (CLAUDE.md)."""
        result = await orch.handle_request(text, **kwargs)
        perm = result.get('permission') or {}
        if result.get('status') == 'waiting_permission' and perm.get('action') == 'cloud.send':
            result = await orch.decide_permission(perm['id'], 'grant')
        return result


# --------------------------------------------------------------------------- #1 masking

class RedactionFixTests(unittest.TestCase):
    def assertMasked(self, text: str, secret: str) -> None:
        masked, findings = redact(text)
        self.assertNotIn(secret, masked, text)
        self.assertTrue(findings, text)
        self.assertEqual(sensitivity_of(text), 'sensitive', text)

    def test_everyday_password_phrases(self):
        self.assertMasked('Mon mot de passe Wi-Fi est Soleil2024, est-il assez solide ?', 'Soleil2024')
        self.assertMasked("Mon mot de passe c'est Soleil2024", 'Soleil2024')
        self.assertMasked('le mdp du wifi : Soleil2024', 'Soleil2024')
        self.assertMasked("Le code de l'alarme de la maison est 4821-7730", '4821-7730')
        self.assertMasked('Mon PIN : 1234', '1234')

    def test_lowercase_iban_and_common_api_keys(self):
        self.assertMasked('iban fr7630006000011234567890189', 'fr7630006000011234567890189')
        self.assertMasked('STRIPE_SECRET_KEY=sk_live_51HxAbCdEfGhIjKlMnOp', 'sk_live_51HxAbCdEfGhIjKlMnOp')
        self.assertMasked('token hf_AbCdEfGhIjKlMnOpQrStUvWxYz123456', 'hf_AbCdEfGhIjKlMnOpQrStUvWxYz123456')
        self.assertMasked('OPENWEATHER_API_KEY=5f3c9a7b2e1d4c6f8a0b', '5f3c9a7b2e1d4c6f8a0b')
        self.assertMasked('glpat-AbCdEfGhIjKlMnOpQrSt12', 'glpat-AbCdEfGhIjKlMnOpQrSt12')

    def test_phone_with_00_prefix(self):
        masked, _ = redact('Appelle le 0033 6 12 34 56 78')
        self.assertIn('[TÉLÉPHONE]', masked)

    def test_secret_keyword_alone_is_sensitive_and_everyday_text_is_not(self):
        self.assertEqual(sensitivity_of('Le mot de passe doit être long et il est conseillé'), 'sensitive')
        for text in ('Explique le code Python suivant', 'le code postal est 75001', 'Quel temps fait-il à Paris ?',
                     'Combien de tokens coûte une requête ?', 'le résultat est 42'):
            self.assertEqual(sensitivity_of(text), 'normal', text)
        self.assertEqual(redact('le code postal est 75001')[0], 'le code postal est 75001')

    def test_detectors_stay_linear_on_long_inputs(self):
        import time
        started = time.perf_counter()
        for text in ('x.' * 3000, ('abc-def_ghi.' * 500) + ' = valeur', 'Le code ' + 'mot ' * 3000 + ' est 1234'):
            redact(text)
            sensitivity_of(text)
        self.assertLess(time.perf_counter() - started, 1.0)

    def test_redaction_is_idempotent(self):
        once, _ = redact("Mon mot de passe c'est Soleil2024 ; STRIPE_SECRET_KEY=sk_live_51HxAbCdEfGhIjKlMnOp")
        twice, findings = redact(once)
        self.assertEqual(once, twice)
        self.assertEqual(findings, [])


class AdvancedModePasswordTests(CoreCase):
    async def test_password_in_plain_words_asks_consent_in_advanced_mode(self):
        self.mode('avance')
        orch, _, _, claude = build(FakeDecision('chat.general'), claude=FakeClaude(configured=True))
        result = await orch.handle_request('Mon mot de passe Wi-Fi est Soleil2024, est-il assez solide ?',
                                           options={'engine': 'cloud'})
        self.assertEqual(result['status'], 'waiting_permission')
        self.assertEqual(claude.calls, [])
        self.assertNotIn('Soleil2024', result['permission']['preview']['text'])


# --------------------------------------------------------------------------- #2 sensitive memories in turns

class SensitiveTurnTests(CoreCase):
    async def test_sensitive_memory_shown_earlier_never_reaches_claude(self):
        from aurora.memory import store
        store.add_memory('Nom de jeune fille de ma mère', 'Le nom de jeune fille de ma mère est Durand', 'personnel',
                         confidentiality='sensitive')
        self.mode('avance')

        def route(text):
            if text.startswith('Retrouve'):
                return FakeDecision('memory.search', slots={'query': 'jeune fille'})
            return FakeDecision('chat.general')
        orch, _, _, claude = build(route, claude=FakeClaude(configured=True))
        first = await orch.handle_request('Retrouve le nom de jeune fille')
        self.assertIn('Durand', first['answer'])          # shown locally on explicit request
        second = await self.ask_granting_cloud(orch, 'Écris une courte histoire', conversation_id=first['conversation_id'],
                                           options={'engine': 'cloud'})
        self.assertEqual(len(claude.calls), 1, second['answer'])
        sent = claude.calls[0]['messages'][0]['content']
        self.assertNotIn('Durand', sent)
        self.assertIn('souvenir sensible', sent)


# ------------------------------------------------- SYNTHESE défaut 1: a secret typed by the user (étape 1)

SECRET_PHRASE = 'Retiens que mon mot de passe Wi-Fi est Soleil2024!'
PLAIN_PHRASE = 'retiens que ma couleur préférée est le bleu.'


class SecretTailTests(unittest.TestCase):
    def test_punctuation_glued_to_a_secret_is_kept_and_a_sentence_end_is_not(self):
        from aurora.core.normalize import extract_memory_content
        from aurora.security.redaction import restore_secret_tail
        cases = {
            SECRET_PHRASE: 'mon mot de passe Wi-Fi est Soleil2024!',
            PLAIN_PHRASE: 'ma couleur préférée est le bleu',
            'Retiens que mon mot de passe Wi-Fi est Soleil2024 !': 'mon mot de passe Wi-Fi est Soleil2024',
            # the alarm code detector stops before a final « . »: it stays a sentence end
            "Retiens que le code de l'alarme est 4821-7730.": "le code de l'alarme est 4821-7730",
            'Retiens que je suis content!': 'je suis content',
        }
        for text, expected in cases.items():
            content = extract_memory_content(text)['content']
            self.assertEqual(restore_secret_tail(content, text), expected, text)
        self.assertEqual(restore_secret_tail('autre chose', SECRET_PHRASE), 'autre chose')
        self.assertEqual(restore_secret_tail('', SECRET_PHRASE), '')

    def test_asking_for_ones_password_is_a_memory_lookup_not_a_network_measure(self):
        self.assertEqual(decide('quel est mon mot de passe Wi-Fi ?').intent, 'memory.search')
        self.assertEqual(decide("c'est quoi mon mdp wifi ?").intent, 'memory.search')
        self.assertNotEqual(decide('Quel est le mot de passe du Wi-Fi ?').intent, 'system.status')
        self.assertEqual(decide('quel est mon débit wifi ?').intent, 'system.status')
        self.assertEqual(decide('Comment choisir un bon mot de passe ?').intent, 'chat.general')


class TypedSecretTests(CoreCase):
    """Real router, engine selection, policy, consent, redaction and memory; Ollama/Claude are doubles."""

    def orchestrator(self):
        from aurora.core.orchestrator import Deps, Orchestrator
        config.update_settings({'mode': 'avance', 'semantic_router': False})
        claude = FakeClaude(configured=True)
        deps = Deps(tools=FakeTools(), ollama=FakeOllama(), claude=claude,
                    capabilities=make_caps(ollama_up=True, cloud=True))
        return Orchestrator(deps), claude

    @staticmethod
    def user_meta(conversation_id: int, text: str) -> dict:
        import json
        row = db.row("SELECT meta_json FROM messages WHERE conversation_id=? AND role='user' AND content=?",
                     (conversation_id, text))
        return json.loads(row['meta_json'])

    async def test_secret_is_saved_exactly_masked_in_the_answer_and_its_message_marked(self):
        from aurora.memory import store
        orch, claude = self.orchestrator()
        result = await orch.handle_request(SECRET_PHRASE)
        self.assertEqual(result['decision']['intent'], 'memory.save')
        memory = store.list_memories(limit=5)[0]
        self.assertEqual(memory['detail'], 'mon mot de passe Wi-Fi est Soleil2024!')
        self.assertEqual(memory['confidentiality'], 'sensitive')
        self.assertNotIn('Soleil2024', result['answer'])
        self.assertIn('[MOT_DE_PASSE]', result['answer'])
        self.assertTrue(self.user_meta(result['conversation_id'], SECRET_PHRASE).get('sensitive'))
        self.assertEqual(claude.calls, [])

    async def test_plain_memory_keeps_its_value_and_stays_normal(self):
        from aurora.memory import store
        orch, _ = self.orchestrator()
        result = await orch.handle_request(PLAIN_PHRASE)
        memory = store.list_memories(limit=5)[0]
        self.assertEqual(memory['detail'], 'ma couleur préférée est le bleu')
        self.assertEqual(memory['confidentiality'], 'private')
        self.assertIn('« Ma couleur préférée est le bleu »', result['answer'])
        self.assertNotIn('sensitive', self.user_meta(result['conversation_id'], PLAIN_PHRASE))
        self.assertFalse(result['shows_sensitive'])

    async def test_secret_asked_back_is_shown_locally_and_never_reaches_claude(self):
        from aurora.memory.context import SENSITIVE_USER_TURN
        orch, claude = self.orchestrator()
        saved = await orch.handle_request(SECRET_PHRASE)
        conv = saved['conversation_id']
        asked = await orch.handle_request('quel est mon mot de passe Wi-Fi ?', conversation_id=conv,
                                          options={'engine': 'cloud'})
        self.assertEqual(asked['decision']['intent'], 'memory.search')
        self.assertNotIn('cloud', asked['engine']['engine'])
        self.assertIn('Soleil2024!', asked['answer'])            # the user's own PC, explicit request
        self.assertTrue(asked['shows_sensitive'])                 # shown, never read aloud by itself
        self.assertEqual(claude.calls, [])

        later = await orch.handle_request('Écris une courte histoire', conversation_id=conv,
                                          options={'engine': 'cloud'})
        self.assertEqual(later['status'], 'waiting_permission')
        preview = later['permission']['preview']['text']
        self.assertNotIn('Soleil2024', preview)
        self.assertIn(SENSITIVE_USER_TURN, preview)
        done = await orch.decide_permission(later['permission']['id'], 'grant')
        self.assertEqual(done['status'], 'done')
        self.assertNotIn('Soleil2024', claude.calls[0]['messages'][0]['content'])

    async def test_secret_in_a_cloud_request_needs_a_separate_consent_at_every_send(self):
        orch, claude = self.orchestrator()
        first = await orch.handle_request('Résume la théorie des cordes', options={'engine': 'cloud'})
        conv = first['conversation_id']
        await orch.decide_permission(first['permission']['id'], 'grant', 'conversation')
        text = 'Mon mot de passe Wi-Fi est Soleil2024! Est-il assez solide ?'
        secret = await orch.handle_request(text, conversation_id=conv, options={'engine': 'cloud'})
        self.assertEqual(secret['status'], 'waiting_permission')   # the conversation consent does not cover it
        preview = secret['permission']['preview']
        self.assertEqual(preview['sensitivity'], 'sensitive')
        self.assertNotIn('consent_scope', preview)
        self.assertNotIn('Soleil2024', preview['text'])
        self.assertEqual(len(claude.calls), 1)
        with self.assertRaises(ValueError):
            await orch.decide_permission(secret['permission']['id'], 'grant', 'conversation')
        self.assertTrue(self.user_meta(conv, text).get('sensitive'))

        await orch.decide_permission(secret['permission']['id'], 'grant')
        self.assertNotIn('Soleil2024', claude.calls[1]['messages'][0]['content'])
        # Later sends of the conversation: the turn holding the secret is masked as a whole.
        from aurora.memory.context import SENSITIVE_USER_TURN
        after = await self.ask_granting_cloud(orch, 'Et la gravité quantique ?', conversation_id=conv,
                                              options={'engine': 'cloud'})
        self.assertEqual(after['status'], 'done')
        sent = claude.calls[2]['messages'][0]['content']
        self.assertNotIn('Soleil2024', sent)
        self.assertIn(SENSITIVE_USER_TURN, sent)


# --------------------------------------------------------------------------- #3 secondary intents

WEATHER = {'ok': True, 'data': {'city': 'Lyon'}, 'summary': 'À Lyon : 9 °C.', 'provenance': 'open-meteo'}


class SecondaryIntentTests(CoreCase):
    async def test_secondary_memory_save_is_performed(self):
        decision = FakeDecision('weather.current', secondary_intents=['memory.save'],
                                slots={'city': 'Lyon', 'content': "j'aime le froid", 'category': 'preference',
                                       'kind': 'preference'})
        orch, *_ = build(decision, tools=FakeTools({'weather.current': WEATHER}))
        result = await orch.handle_request("Quel temps fait-il à Lyon et retiens que j'aime le froid")
        self.assertIn('9 °C', result['answer'])
        self.assertIn('C’est noté', result['answer'])
        self.assertEqual(db.row('SELECT detail FROM memories')['detail'], "j'aime le froid")
        self.assertEqual(result['decision']['secondary_intents'], ['memory.save'])

    async def test_secondary_action_not_chained_is_named(self):
        decision = FakeDecision('weather.current', secondary_intents=['files.cleanup'],
                                slots={'city': 'Lyon', 'folder': 'C:/x', 'older_than_days': 0})
        orch, tools, *_ = build(decision, tools=FakeTools({'weather.current': WEATHER}))
        result = await orch.handle_request('Quel temps fait-il à Lyon et supprime les vieux fichiers de C:/x')
        self.assertEqual([t for t, _ in tools.calls], ['weather.current'])
        self.assertIn('Non traité', result['answer'])
        self.assertIn('Nettoyage de fichiers', result['answer'])
        self.assertEqual(result['decision']['secondary_intents'], [])
        self.assertEqual(result['decision']['unhandled_intents'], ['files.cleanup'])

    def test_planner_adds_secondary_web_search(self):
        from aurora.core.planner import Planner
        plan = Planner().build({'intent': 'time.now', 'secondary_intents': ['web.search'],
                                'slots': {'query': 'découvertes sur Mars'}}, {'engine': 'tool'},
                               text='Quelle heure est-il et recherche les découvertes sur Mars', settings={})
        self.assertEqual([s['tool'] for s in plan], ['time.now', 'web.search', 'compose', 'verify'])
        self.assertEqual(plan[1]['params'], {'query': 'découvertes sur Mars'})


# --------------------------------------------------------------------------- #4 #9 #19 #37 honesty

def decide(text: str):
    from aurora.core.router import DecisionEngine
    return DecisionEngine(use_semantic=False).decide_fast(text)


class HonestyTests(CoreCase):
    def test_promises_without_step_fail_verification(self):
        from aurora.core import verify
        answer = 'J’ai bien noté votre demande. Je vous rappellerai d’appeler Paul demain à 9 heures.'
        result = verify.check(answer=answer, steps=[], caps={'internet': True}, mode='equilibre', permissions={},
                              llm_text_used=True)
        actions = next(c for c in result['checks'] if c['name'] == 'actions')
        self.assertFalse(actions['ok'])
        ok = verify.check(answer='Je ne peux pas programmer de rappel.', steps=[], caps={}, mode='equilibre',
                          permissions={}, llm_text_used=True)
        self.assertTrue(next(c for c in ok['checks'] if c['name'] == 'actions')['ok'])

    def test_chat_prompt_lists_what_aurora_cannot_do_and_no_cloud_promise(self):
        from aurora.core import prompts
        caps = {'internet': True, 'ollama': {'up': True, 'chat_model': 'qwen3.5:9b'},
                'cloud': {'configured': False}, 'mode': 'equilibre'}
        system = prompts.system_prompt(caps, purpose='chat')
        # 16/11/2026 (C3): reminders exist, but only through their tool — a model never creates one by writing.
        self.assertIn('n’est programmé que par la demande directe', system)
        self.assertIn('envoyer un e-mail', system)
        self.assertIn('Claude indisponible : non configuré', system)
        self.assertNotIn('Claude seulement avec consentement', system)

    async def test_reminder_request_gets_deterministic_refusal(self):
        # 16/11/2026 (C3): the refusal became a real tool (tests/test_reminders.py); a question about reminders is
        # still answered by a statement, which says that nothing was programmed by it.
        self.assertEqual(decide('Rappelle-moi demain à 9 h d’appeler Paul').intent, 'reminder.set')
        self.assertEqual(decide('Rappelle-moi le nom de mon fichier de thèse').intent, 'memory.search')
        orch, *_ = build(FakeDecision('meta.capabilities'))
        result = await orch.handle_request('Sais-tu programmer un rappel ?')
        self.assertIn('je sais programmer un rappel', result['answer'])
        self.assertIn('Rien n’a été programmé par cette réponse', result['answer'])
        self.assertTrue(result['verification']['ok'])
        # Same honest statement for screen vision: on a click only, never by itself (E2, CLAUDE.md: said clearly).
        for asked in ('Regarde mon écran et dis-moi ce qui ne va pas', 'Fais une capture d’écran',
                      'Qu’est-ce qu’il y a sur mon écran ?'):
            self.assertEqual(decide(asked).intent, 'meta.capabilities', asked)
            result = await orch.handle_request(asked)
            self.assertIn('Je ne regarde pas ton écran de moi-même', result['answer'], asked)
            self.assertIn('Capture d’écran', result['answer'], asked)
            # 9/11/2026: acting on the screen is said from the real setting (core/capabilities.pc_control_state).
            self.assertIn('souris et le clavier', result['answer'], asked)
        result = await orch.handle_request('Que sais-tu faire ?')
        self.assertIn('Pas encore disponible : cliquer sur un bouton désigné par son nom', result['answer'])

    async def test_what_do_you_know_about_me_lists_memories(self):
        from aurora.core.normalize import extract_query
        from aurora.memory import store
        for text in ('Que sais-tu de moi ?', 'Qu’est-ce que tu sais sur moi ?'):
            d = decide(text)
            self.assertEqual(d.intent, 'memory.search', text)
            self.assertEqual(d.slots['query'], '', text)
        self.assertEqual(decide('Que sais-tu faire ?').intent, 'meta.capabilities')
        self.assertEqual(extract_query('Retrouve notre projet sur Mars', kind='memory'), 'projet sur Mars')
        store.add_memory('Mon projet s’appelle Hélios', 'énergie solaire', 'projet', kind='project')
        store.add_memory('Résumé : Mon PC est lent', 'RAM 71,8 %', 'diagnostic', kind='summary')
        orch, *_ = build(FakeDecision('memory.search', slots={'query': ''}))
        result = await orch.handle_request('Que sais-tu de moi ?')
        self.assertIn('Voici ce que je sais de vous', result['answer'])
        self.assertIn('Hélios', result['answer'])
        self.assertNotIn('Résumé : Mon PC', result['answer'])

    async def test_chat_without_memory_says_so_in_prompt(self):
        orch, _, ollama, _ = build(FakeDecision('chat.general'))
        await orch.handle_request('Qui es-tu ?')
        self.assertIn('Aucun souvenir sur l’utilisateur', ollama.calls[0]['messages'][0]['content'])

    def test_mode_label_in_capabilities_answer(self):
        from aurora.core import compose
        caps = {'internet': True, 'ollama': {}, 'cloud': {}, 'mode': 'equilibre'}
        self.assertIn('Mode actif : équilibré.', compose.capabilities_answer(caps, 'Que sais-tu faire ?'))


# --------------------------------------------------------------------------- #5 DuckDuckGo instant answer

class InstantAnswerTests(CoreCase):
    async def test_instant_answer_text_reaches_the_model(self):
        ddg = {'ok': True, 'provenance': 'duckduckgo', 'summary': 'Réponse instantanée DuckDuckGo : Mars.',
               'data': {'results': [], 'instant': {'title': 'Mars', 'url': 'https://en.wikipedia.org/wiki/Mars',
                                                   'extract': 'Mars is the fourth planet from the Sun.'}},
               'sources': [{'title': 'Mars — DuckDuckGo (Wikipedia)', 'url': 'https://en.wikipedia.org/wiki/Mars',
                            'kind': 'web', 'retrieved_at': ''}]}
        orch, _, ollama, _ = build(FakeDecision('web.search', slots={'query': 'Mars'}),
                                   tools=FakeTools({'web.search': ddg}),
                                   ollama=FakeOllama(reply='Mars est la quatrième planète du Soleil [1].'))
        result = await orch.handle_request('Cherche sur internet Mars')
        prompt = ollama.calls[0]['messages'][0]['content']
        self.assertIn('fourth planet from the Sun', prompt)
        checks = {c['name']: c for c in result['verification']['checks']}
        self.assertTrue(checks['sources']['ok'])

    def test_source_without_text_is_not_citable(self):
        from aurora.core import verify
        steps = [{'status': 'done', 'stage': 'tool', 'tool': 'web.search',
                  'result': {'ok': True, 'provenance': 'duckduckgo', 'data': {'results': []},
                             'sources': [{'title': 'Mars', 'url': ''}]}}]
        result = verify.check(answer='Voir [1].', steps=steps, caps={}, mode='equilibre', permissions={},
                              llm_text_used=True)
        self.assertFalse(next(c for c in result['checks'] if c['name'] == 'sources')['ok'])


# --------------------------------------------------------------------------- #6 #7/#27 #38 #42 tools

def run(coro):
    return asyncio.run(coro)


class ToolHonestyTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        from aurora.tools.base import clear_caches
        clear_caches()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_time_in_a_city(self):
        from aurora.tools.base import run_tool
        self.assertEqual(decide('Quelle heure est-il à Tokyo ?').slots, {'place': 'Tokyo'})
        self.assertEqual(decide('Quelle heure est-il ?').slots, {})
        tokyo = run(run_tool('time.now', {'place': 'Tokyo'}))
        self.assertTrue(tokyo.ok, tokyo.summary)
        self.assertEqual(tokyo.data['utc_offset'], 'UTC+09:00')
        self.assertIn('À Tokyo', tokyo.summary)
        unknown = run(run_tool('time.now', {'place': 'Zzyzxville'}))
        self.assertFalse(unknown.ok)
        self.assertIn('Heure à Zzyzxville indisponible', unknown.summary)
        self.assertIn("seule l'heure locale du PC est connue", unknown.summary)

    def test_forecast_days_slot(self):
        from datetime import date
        from aurora.core.normalize import extract_forecast_days
        wednesday = date(2026, 10, 28)
        self.assertEqual(extract_forecast_days('Quel temps fera-t-il demain à Paris ?', today=wednesday), [1])
        self.assertEqual(extract_forecast_days('Météo après-demain', today=wednesday), [2])
        self.assertEqual(extract_forecast_days('Météo ce week-end à Lyon', today=wednesday), [3, 4])
        self.assertIsNone(extract_forecast_days('Quel temps fait-il à Paris ?', today=wednesday))
        self.assertEqual(decide('Quel temps fera-t-il demain à Lyon ?').slots['days'], [1])

    def test_forecast_answer_and_cached_retrieved_at(self):
        import httpx
        from aurora.tools import base
        from aurora.tools.base import run_tool
        calls = []

        def handler(req):
            calls.append(str(req.url))
            if req.url.host == 'geocoding-api.open-meteo.com':
                return httpx.Response(200, json={'results': [{'name': 'Lyon', 'latitude': 45.75, 'longitude': 4.85,
                                                              'country': 'France', 'timezone': 'Europe/Paris'}]})
            self.assertEqual(req.url.params['forecast_days'], '7')
            return httpx.Response(200, json={
                'timezone': 'Europe/Paris', 'current': {'time': '2026-10-28T10:00', 'temperature_2m': 12.2,
                                                        'weather_code': 3, 'is_day': 1},
                'daily': {'time': ['2026-10-28', '2026-10-29'], 'temperature_2m_min': [10.2, 7.5],
                          'temperature_2m_max': [15.3, 13.4], 'weather_code': [3, 61],
                          'precipitation_probability_max': [10, 80]}})
        base.set_http_transport(httpx.MockTransport(handler))
        try:
            with mock.patch.dict(os.environ, {'AURORA_OFFLINE': '0'}):
                first = run(run_tool('weather.current', {'city': 'Lyon', 'days': [1]}))
                self.assertTrue(first.ok, first.summary)
                self.assertIn('Prévision pour demain (jeudi 29 octobre) à Lyon (France) : pluie faible', first.summary)
                self.assertIn('max 13,4 °C', first.summary)
                self.assertIn('Conditions actuelles', first.summary)
                far = run(run_tool('weather.current', {'city': 'Lyon', 'days': [5]}))
                self.assertIn('indisponible', far.summary)
                self.assertEqual(len(calls), 2)                    # second call served by the cache
                self.assertEqual(far.sources[0].retrieved_at, first.sources[0].retrieved_at)
                self.assertTrue(far.data['cached'])
        finally:
            base.set_http_transport(None)

    def test_quake_lines_are_french(self):
        from aurora.core import compose
        res = {'ok': True, 'summary': 'USGS : 2 séisme(s).', 'data': {'quakes': [
            {'mag': 6.0, 'place': 'Vanuatu', 'time': '2026-10-28T02:21:45Z'}]}}
        text = compose.quakes({}, {'quakes.recent': res})
        self.assertIn('M 6,0 — Vanuatu', text)
        self.assertNotIn('T02:21:45Z', text)
        self.assertIn('(heure locale)', text)


# --------------------------------------------------------------------------- #8 #20 #21 #39 #40 measures

class MeasureHonestyTests(unittest.TestCase):
    SNAP = {'cpu_percent': 13.0, 'cpu_count': 16, 'ram_percent': 71.8, 'ram_used_gb': 11.5, 'ram_total_gb': 16.0,
            'disk_percent': 96.6, 'uptime_s': 7 * 86400 + 5 * 3600, 'boot_time': '2026-10-21T03:00:00Z',
            'temperatures': None, 'gpu': {'name': 'AMD Radeon RX 6750 XT', 'util_percent': 1.0,
                                          'memory_used_mb': 9812, 'memory_total_mb': 12272, 'temperature_c': None,
                                          'source': 'pdh'}}

    def test_uptime_and_temperature_are_measured_metrics(self):
        from aurora.core.normalize import extract_metric
        from aurora.tools.system import summarize_snapshot
        d = decide('Depuis combien de temps mon PC est allumé ?')
        self.assertEqual((d.intent, d.slots), ('system.status', {'metric': 'uptime'}))
        self.assertEqual(extract_metric('Quelle est la température du processeur ?'), 'temperature')
        self.assertIn('PC allumé depuis 7 j 5 h', summarize_snapshot(self.SNAP, 'uptime'))
        temp = summarize_snapshot(self.SNAP, 'temperature')
        self.assertIn('Température du processeur : indisponible', temp)
        self.assertIn('utilisation actuelle du processeur : 13 %', temp)

    def test_gpu_source_line_and_single_read_only_mention(self):
        from aurora.core import compose
        res = {'ok': True, 'summary': 'GPU AMD : 1 % d’utilisation.', 'data': self.SNAP}
        text = compose.system_status({'metric': 'gpu'}, {'system.snapshot': res})
        self.assertIn('compteurs de performance Windows (PDH)', text)
        self.assertNotIn('psutil', text)
        scan = {'ok': True, 'data': {'files': []},
                'summary': "Dans Téléchargements : 0 fichier(s). Lecture seule : rien n'a été modifié."}
        self.assertEqual(compose.files_scan({}, {'files.scan': scan}).lower().count('lecture seule'), 1)

    def test_invented_percentages_and_stale_durations_are_flagged(self):
        from aurora.core import verify
        steps = [{'status': 'done', 'stage': 'tool', 'tool': 'system.snapshot',
                  'result': {'ok': True, 'provenance': 'psutil', 'data': dict(self.SNAP, uptime_s=None,
                                                                              swap_percent=12.0),
                             'summary': 'CPU 13 %, RAM 72 %.'}}]
        result = verify.check(answer='Après un redémarrage, votre RAM passera de 71,8 % à 0 % et le processeur '
                                     'tombera à 2 %.', steps=steps, caps={}, mode='equilibre', permissions={},
                              llm_text_used=True)
        mesures = next(c for c in result['checks'] if c['name'] == 'mesures')
        self.assertFalse(mesures['ok'])
        self.assertIn('0 %', mesures['detail'])
        self.assertIn('2 %', mesures['detail'])
        stale = verify.check(answer='Votre ordinateur est allumé depuis 7 jours et 5 heures.', steps=[], caps={},
                             mode='equilibre', permissions={}, llm_text_used=True)
        self.assertFalse(next(c for c in stale['checks'] if c['name'] == 'mesures')['ok'])

    def test_summary_memories_are_marked_stale_in_context(self):
        from aurora.memory.context import _memory_block
        block = _memory_block({'id': 2, 'title': 'Résumé : Mon PC est lent', 'category': 'diagnostic',
                               'kind': 'summary', 'source': 'conversation', 'created_at': '2026-10-21T08:00:00Z',
                               'confidentiality': 'private', 'verification': 'unverified',
                               'detail': 'RAM 71,8 %'}, None)
        self.assertIn('périmées', block)


# --------------------------------------------------------------------------- #10 K1 #25 #26 extraction

class ExtractionTests(unittest.TestCase):
    def test_thousands_separators_and_signs(self):
        for text in ('orbite autour de la Terre à 2 000 km', 'orbite autour de la Terre à 2\u00a0000 km',
                     'orbite autour de la Terre à 2\u202f000 km'):
            self.assertEqual(decide(text).slots['altitude_km'], 2000.0, text)
        self.assertEqual(decide('simule une orbite à 35 786 km autour de la Terre').slots['altitude_km'], 35786.0)
        self.assertEqual(decide('orbite autour de Mars à 1 500,5 km').slots['altitude_km'], 1500.5)
        self.assertEqual(decide('orbite autour de Mars à -300 km').slots['altitude_km'], -300.0)
        self.assertEqual(decide('convertis 2 000 km en miles').slots['value'], 2000.0)
        self.assertEqual(decide('convertis 10 000 pieds en mètres').slots['value'], 10000.0)
        self.assertEqual(decide('convertis 2,5 km en m').slots['value'], 2.5)
        self.assertEqual(decide('convertis -40 °C en fahrenheit').slots['value'], -40.0)
        self.assertEqual(decide('Combien font 12 345 + 1').slots['expression'], '12345 + 1')

    def test_negative_altitude_is_refused_by_the_tool(self):
        from aurora.tools.base import run_tool
        with TempEnv():
            result = run(run_tool('sim.orbit', {'body': 'mars', 'altitude_km': -300.0}))
        self.assertFalse(result.ok)

    def test_folder_with_slashes_or_named_folder_and_size_criterion(self):
        from aurora.core.normalize import extract_folder, extract_slots
        self.assertEqual(extract_folder('analyse les vieux fichiers du dossier C:/Users/x/sb2/vieux'),
                         'C:/Users/x/sb2/vieux')
        self.assertEqual(extract_folder('analyse les vieux fichiers du dossier scanme'), 'scanme')
        self.assertEqual(extract_slots('files.scan', 'liste les fichiers de plus de 1 Go dans le dossier C:\\t\\v'),
                         {'folder': 'C:\\t\\v', 'older_than_days': 0, 'min_size_mb': 1024.0})
        self.assertEqual(extract_slots('files.cleanup', 'Supprime mes anciens fichiers'),
                         {'folder': None, 'older_than_days': None})
        from aurora.core.planner import tool_params
        self.assertEqual(tool_params('files.scan', {'folder': 'x', 'older_than_days': 0, 'min_size_mb': 1024.0},
                                     text='', settings={}), {'folder': 'x', 'older_than_days': 0, 'min_size_mb': 1024.0})

    def test_unknown_named_folder_is_an_honest_error(self):
        from aurora.tools.files import ToolError, resolve_folder
        with self.assertRaises(ToolError) as ctx:
            resolve_folder('scanme', roots=[{'kind': 'Downloads', 'label': 'Téléchargements',
                                             'path': __import__('pathlib').Path.home()}])
        self.assertIn('non reconnu', ctx.exception.message_fr)


# --------------------------------------------------------------------------- #11 #13 process stop bound to the preview

def _terminate_double(gate: asyncio.Event | None = None):
    class Tools(FakeTools):
        async def run_tool(self, name, params=None):
            if name == 'process.terminate' and not (params or {}).get('dry_run') and gate is not None:
                self.calls.append((name, dict(params or {})))
                await gate.wait()
                return await super().run_tool('process.terminate:real', params)
            return await super().run_tool(name, params)

    def terminate(p):
        if p.get('dry_run'):
            return {'ok': True, 'data': {'targets': [{'pid': 508, 'name': 'rvzz.exe', 'create_time': 100.5},
                                                     {'pid': 4820, 'name': 'rvzz.exe', 'create_time': 101.5}],
                                         'refused': []},
                    'summary': '2 processus « rvzz » seraient arrêtés.', 'provenance': 'psutil'}
        return {'ok': True, 'data': {'terminated': [{'pid': 508}, {'pid': 4820}], 'still_running': []},
                'summary': '2 processus « rvzz » arrêté(s).', 'provenance': 'psutil'}
    return Tools({'process.terminate': terminate, 'process.terminate:real': terminate})


class ProcessStopTests(CoreCase):
    async def test_real_stop_carries_the_approved_targets(self):
        orch, tools, *_ = build(FakeDecision('system.kill_process', slots={'name': 'rvzz'}), tools=_terminate_double())
        waiting = await orch.handle_request('arrête le programme rvzz')
        await orch.decide_permission(waiting['permission']['id'], 'grant')
        real = tools.called('process.terminate')[1]
        self.assertEqual(real['approved'], [{'pid': 508, 'name': 'rvzz.exe', 'create_time': 100.5},
                                            {'pid': 4820, 'name': 'rvzz.exe', 'create_time': 101.5}])

    async def test_grant_is_used_once_interrupted_step_asks_again(self):
        gate = asyncio.Event()
        orch, tools, *_ = build(FakeDecision('system.kill_process', slots={'name': 'rvzz'}),
                                tools=_terminate_double(gate))
        waiting = await orch.handle_request('arrête le programme rvzz')
        first_perm = waiting['permission']['id']
        task = asyncio.create_task(orch.decide_permission(first_perm, 'grant'))
        for _ in range(50):
            await asyncio.sleep(0.02)
            if len(tools.called('process.terminate')) == 2:
                break
        await orch.shutdown()
        await asyncio.wait_for(task, 5)
        self.assertEqual(runs.get_run(waiting['task_id'])['status'], 'interrupted')
        resumed = await orch.resume_run(waiting['task_id'])
        self.assertEqual(resumed['status'], 'waiting_permission')
        self.assertNotEqual(resumed['permission']['id'], first_perm)
        gate.set()

    def test_tool_refuses_a_process_started_after_the_preview(self):
        import shutil
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from aurora.tools.processes import terminate
        ping = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'PING.EXE'
        if sys.platform != 'win32' or not ping.exists():
            self.skipTest('PING.EXE indisponible')
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            exe = Path(tmp) / 'aurfixping.exe'
            shutil.copy(ping, exe)
            procs = [subprocess.Popen([str(exe), '-n', '60', '127.0.0.1'], stdout=subprocess.DEVNULL)
                     for _ in range(2)]
            try:
                import time
                time.sleep(0.5)
                preview = terminate(name='aurfixping', dry_run=True)
                approved = preview.data['targets']
                self.assertEqual(len(approved), 2)
                late = subprocess.Popen([str(exe), '-n', '60', '127.0.0.1'], stdout=subprocess.DEVNULL)
                procs.append(late)
                time.sleep(0.5)
                result = terminate(name='aurfixping', approved=approved)
                stopped = sorted(t['pid'] for t in result.data['terminated'])
                self.assertEqual(stopped, sorted(t['pid'] for t in approved))
                self.assertIn(late.pid, [r['pid'] for r in result.data['refused']])
                self.assertIsNone(late.poll())        # the late process is still running
            finally:
                for p in procs:
                    if p.poll() is None:
                        p.kill()
                    p.wait(timeout=10)


# --------------------------------------------------------------------------- #14 network steps of an LLM plan

class LlmPlanNetworkTests(CoreCase):
    async def test_plan_web_step_needs_consent_and_secret_query_is_dropped(self):
        plan_json = ('{"goal": "Organiser Orion", "steps": ['
                     '{"title": "Chercher la doc", "tool": "web.search", "params": {"query": "Hunter2-Orion serveur"}},'
                     '{"title": "Écrire à l’admin", "tool": "web.search", '
                     '"params": {"query": "admin jean.dupont@example.com"}}]}')
        tools = FakeTools({'web.search': {'ok': True, 'data': {'results': []}, 'summary': 'rien',
                                          'provenance': 'wikipedia'}})
        orch, tools, *_ = build(FakeDecision('task.plan'), tools=tools, ollama=FakeOllama(reply=plan_json))
        result = await orch.handle_request('Organise mon projet Orion')
        self.assertEqual(result['status'], 'waiting_permission')
        self.assertEqual(tools.called('web.search'), [])
        perm = result['permission']
        self.assertEqual(perm['action'], 'web.fetch')
        self.assertEqual(perm['preview']['params'], {'query': 'Hunter2-Orion serveur'})
        self.assertIn('proposée par le modèle', perm['preview']['warning'])
        done = await orch.decide_permission(perm['id'], 'deny')
        self.assertEqual(tools.called('web.search'), [])
        self.assertIn('données personnelles ou secrètes', done['answer'])


# --------------------------------------------------------------------------- #16 private mode re-check for tools

class ModeRecheckTests(CoreCase):
    async def test_web_step_planned_in_balanced_mode_asks_after_switch_to_private(self):
        tools = FakeTools({
            'files.scan': {'ok': True, 'data': {'files': [{'path': 'C:/sb/a.txt', 'size': 10}]},
                           'summary': '1 fichier.', 'provenance': 'filesystem'},
            'files.quarantine': {'ok': True, 'data': {'moved': ['C:/sb/a.txt']}, 'summary': '1 fichier déplacé.',
                                 'provenance': 'filesystem'},
            'weather.current': WEATHER})
        decision = FakeDecision('files.cleanup', secondary_intents=['weather.current'],
                                slots={'folder': 'C:/sb', 'older_than_days': 0, 'city': 'Paris'})
        orch, tools, *_ = build(decision, tools=tools)
        first = await orch.handle_request('Supprime les vieux fichiers de C:/sb et donne la météo à Paris')
        self.assertEqual(first['permission']['action'], 'files.quarantine')
        self.mode('prive')
        second = await orch.decide_permission(first['permission']['id'], 'grant')
        self.assertEqual(second['status'], 'waiting_permission')
        self.assertEqual(second['permission']['action'], 'web.fetch')
        self.assertEqual(tools.called('weather.current'), [])


# --------------------------------------------------------------------------- #17 engine after a local fallback

class FallbackEngineTests(CoreCase):
    async def test_denied_cloud_reports_the_local_engine(self):
        self.mode('equilibre')
        orch, _, ollama, claude = build(FakeDecision('chat.general'), claude=FakeClaude(configured=True))
        waiting = await orch.handle_request('Explique la relativité', options={'engine': 'cloud'})
        done = await orch.decide_permission(waiting['permission']['id'], 'deny')
        self.assertEqual(done['status'], 'done')
        self.assertEqual(done['engine']['engine'], 'local')
        self.assertEqual(done['engine']['model'], config.load_settings()['local_model'])
        self.assertIsNone(done['engine']['cloud_tier'])
        ready = [e for e in events.list_events(run_id=done['task_id']) if e['kind'] == 'response.ready'][0]
        self.assertEqual(ready['payload']['engine'], 'local')


# --------------------------------------------------------------------------- #12 #23 #41 #43 #44 Claude

class ScriptedClaude(FakeClaude):
    """FakeClaude returning a given ProviderResult (stop_reason, empty text, meta…)."""

    def __init__(self, result, *, delay: float = 0.0, cost: float = 0.0123):
        super().__init__(configured=True, cost=cost)
        self.result, self.delay = result, delay

    async def chat(self, messages, **kwargs):
        self.calls.append({'messages': messages, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


def claude_result(text: str, stop: str = 'end_turn', meta: dict | None = None, cost: float = 0.0123):
    from aurora.providers.base import ProviderResult
    return ProviderResult(text=text, provider='anthropic', model='claude-fable-5-1', input_tokens=100,
                          output_tokens=2000, cost_usd=cost, duration_ms=5.0, stop_reason=stop, meta=meta or {})


class ClaudeCallTests(CoreCase):
    async def test_tier_max_tokens_and_truncation_is_said(self):
        self.mode('avance')
        result = claude_result('… trois idées :\n1. Les particules sont', 'max_tokens',
                               {'truncated': True, 'warning_fr': 'Réponse tronquée : limite de jetons atteinte.'})
        claude = ScriptedClaude(result)
        orch, *_ = build(FakeDecision('chat.general'), claude=claude)
        done = await self.ask_granting_cloud(orch, 'Explique la physique quantique', options={'engine': 'cloud',
                                                                                    'cloud_tier': 'expert'})
        self.assertEqual(claude.calls[0]['max_tokens'], 16000)
        self.assertIn('Réponse tronquée', done['answer'])

    async def test_request_ceiling_bounds_max_tokens_and_the_stop_is_said(self):
        self.mode('equilibre')
        config.update_settings({'cloud_request_budget_usd': 0.05})
        claude = ScriptedClaude(claude_result('Début de réponse', 'max_tokens', {'truncated': True}))
        orch, *_ = build(FakeDecision('chat.general'), claude=claude)
        waiting = await orch.handle_request('Explique la relativité', options={'engine': 'cloud'})
        preview = waiting['permission']['preview']
        self.assertEqual(preview['request_cap_usd'], 0.05)
        self.assertLessEqual(preview['max_cost_usd'], 0.05)
        done = await orch.decide_permission(waiting['permission']['id'], 'grant')
        self.assertLess(claude.calls[0]['max_tokens'], 5000)          # Sonnet: $10 / MTok of output
        self.assertEqual(claude.calls[0]['max_tokens'], preview['max_tokens'])
        self.assertIn('plafond de dépense', done['answer'])

    async def test_expert_tier_from_the_interface_is_lowered_in_equilibre(self):
        # The orchestrator enforces the mode rule too: a cloud_tier sent by the UI cannot bypass it.
        self.mode('equilibre')
        orch, *_ = build(FakeDecision('chat.general'), claude=FakeClaude(configured=True))
        waiting = await orch.handle_request('Explique la relativité', options={'engine': 'cloud', 'cloud_tier': 'expert'})
        preview = waiting['permission']['preview']
        self.assertEqual((preview['tier'], preview['model']), ('standard', 'claude-sonnet-5-5'))
        self.assertIn('réservé au mode Avancé', waiting['engine']['reason'])
        self.mode('avance')
        waiting = await orch.handle_request('Explique la relativité', options={'engine': 'cloud', 'cloud_tier': 'expert'})
        self.assertEqual(waiting['permission']['preview']['model'], 'claude-opus-5-5')

    async def test_request_ceiling_too_low_sends_nothing(self):
        self.mode('equilibre')
        config.update_settings({'cloud_request_budget_usd': 0.01})
        claude = ScriptedClaude(claude_result('jamais'))
        orch, *_ = build(FakeDecision('chat.general'), claude=claude)
        result = await orch.handle_request('Explique la relativité', options={'engine': 'cloud'})
        self.assertEqual(claude.calls, [])
        self.assertIsNone(result['permission'])
        self.assertIn('Plafond de dépense par requête', result['answer'])

    async def test_empty_claude_text_falls_back_to_local_with_exact_reason(self):
        self.mode('avance')
        claude = ScriptedClaude(claude_result('', 'max_tokens', {'truncated': True}, cost=0.129))
        orch, _, ollama, _ = build(FakeDecision('chat.general'), claude=claude)
        done = await self.ask_granting_cloud(orch, 'Explique la relativité', options={'engine': 'cloud'})
        self.assertEqual(len(ollama.calls), 1)
        self.assertEqual(done['status'], 'done')
        self.assertIn('Claude n’a produit aucun texte', done['answer'])
        self.assertNotIn('Claude n’est pas configuré', done['answer'])
        self.assertAlmostEqual(done['cost_usd'], 0.129, places=6)

    async def test_volatile_state_is_outside_the_cached_system_block(self):
        self.mode('avance')
        claude = ScriptedClaude(claude_result('Réponse.'))
        orch, *_ = build(FakeDecision('chat.general'), claude=claude)
        await self.ask_granting_cloud(orch, 'Résume la théorie des cordes', options={'engine': 'cloud'})
        call = claude.calls[0]
        self.assertNotIn('Date et heure locales', call['system'])
        self.assertIn('Date et heure locales', call['system_dynamic'])
        from aurora.providers.anthropic_provider import _system_blocks
        blocks = _system_blocks(call['system'], call['system_dynamic'])
        self.assertIn('cache_control', blocks[0])
        self.assertNotIn('cache_control', blocks[1])

    async def test_concurrent_cloud_calls_respect_the_daily_budget(self):
        # The worst case of each call (prompt + max_tokens) is reserved: the second call only gets what is
        # left of the day, so both together can never pass the daily ceiling (Sonnet: $10 / MTok output).
        self.mode('avance')
        config.update_settings({'cloud_daily_budget_avance_usd': 0.2})   # each mode has its own ceiling
        claude = ScriptedClaude(claude_result('Réponse.', cost=0.05), delay=0.2)
        orch, *_ = build(FakeDecision('chat.general'), claude=claude)
        await asyncio.gather(self.ask_granting_cloud(orch, 'Explique A', options={'engine': 'cloud'}),
                             self.ask_granting_cloud(orch, 'Explique B', options={'engine': 'cloud'}))
        limits = sorted(call['max_tokens'] for call in claude.calls)
        self.assertEqual(len(limits), 2)
        self.assertEqual(limits[1], 16000)
        self.assertLess(limits[0], 4000)
        self.assertLessEqual(sum(limits) * 10 / 1_000_000, 0.2)

    async def test_daily_budget_too_low_for_a_second_call_falls_back_locally(self):
        self.mode('avance')
        config.update_settings({'cloud_daily_budget_avance_usd': 0.165})
        claude = ScriptedClaude(claude_result('Réponse.', cost=0.05), delay=0.2)
        orch, _, ollama, _ = build(FakeDecision('chat.general'), claude=claude)
        results = await asyncio.gather(
            self.ask_granting_cloud(orch, 'Explique A', options={'engine': 'cloud'}),
            self.ask_granting_cloud(orch, 'Explique B', options={'engine': 'cloud'}))
        self.assertEqual(len(claude.calls), 1)
        self.assertEqual(len(ollama.calls), 1)
        self.assertTrue(any('Budget cloud du jour insuffisant' in r['answer'] for r in results))

    def test_prices_of_other_active_models(self):
        from aurora.providers import pricing
        self.assertEqual(pricing.cost_usd('claude-haiku-4-5', 1_000_000, 0), 1.0)
        self.assertEqual(pricing.cost_usd('claude-sonnet-5', 0, 1_000_000), 10.0)
        self.assertEqual(pricing.cost_usd('claude-opus-4-7', 1_000_000, 0), 5.0)
        self.assertEqual(pricing.cost_usd('claude-fable-5-1', 1_000_000, 0), 10.0)
        self.assertFalse(pricing.is_known('claude-unknown-9'))


# --------------------------------------------------------------------------- #24 K6 expired permission, K7 warm-up

class ExpiryTests(CoreCase):
    async def test_expired_permission_ends_the_run_with_an_event(self):
        tools = FakeTools({
            'files.scan': {'ok': True, 'data': {'files': [{'path': 'C:/sb/old/a.txt', 'size': 10}]},
                           'summary': '1 fichier.', 'provenance': 'filesystem'},
            'files.quarantine': {'ok': True, 'data': {}, 'summary': 'déplacé', 'provenance': 'filesystem'}})
        orch, tools, *_ = build(FakeDecision('files.cleanup', slots={'folder': 'C:/sb/old'}), tools=tools)
        waiting = await orch.handle_request('nettoie les vieux fichiers du dossier C:/sb/old')
        self.assertEqual(waiting['status'], 'waiting_permission')
        with db.connect() as conn:   # simulate the 30 minutes without a decision
            conn.execute("UPDATE permission_requests SET expires_at='2000-01-01T00:00:00.000Z' WHERE id=?",
                         (waiting['permission']['id'],))
        ended = await orch.expire_waiting()
        self.assertEqual(ended, [waiting['task_id']])
        run = runs.get_run(waiting['task_id'])
        self.assertEqual(run['status'], 'done')
        self.assertIn('Autorisation expirée', run['result_text'])
        self.assertEqual(tools.called('files.quarantine'), [])
        denied = [e for e in events.list_events(run_id=waiting['task_id']) if e['kind'] == 'permission.denied']
        self.assertEqual(denied[0]['payload']['reason'], 'expired')
        self.assertIn('run.done', kinds(waiting['task_id']))

    async def test_warmup_is_non_blocking_and_loads_the_model_that_answers(self):
        # 11/11/2026: the model that writes the answers, not the fast one (used only when `llm_router` is on).
        from aurora import main
        called = []

        class Ollama:
            async def warmup(self, model=None):
                called.append(model)
                return True
        models = {'local_model': 'qwen3.5:9b', 'local_fast_model': 'qwen3.5:4b'}
        with mock.patch('aurora.providers.ollama.ollama', Ollama()):
            await main._warmup(models)
            self.assertEqual(called, ['qwen3.5:9b'])
            await main._warmup({**models, 'llm_router': True})
            self.assertEqual(called, ['qwen3.5:9b', 'qwen3.5:9b', 'qwen3.5:4b'])
            await main._warmup({'local_model': 'granite4.2:8b', 'local_fast_model': 'granite4.2:8b', 'llm_router': True})
        self.assertEqual(called[3:], ['granite4.2:8b'])          # the same model for both: loaded once


# --------------------------------------------------------------------------- #18 #22 #29 #30 #33 #34

class MiscFixTests(CoreCase):
    def test_restore_hint_and_web_contact_message(self):
        from aurora.core import compose
        from aurora.tools.base import ToolError
        from aurora.tools.web import _policy_message
        scan = {'ok': True, 'data': {'files': [{'path': 'a'}]}, 'summary': '1 fichier.'}
        moved = {'ok': True, 'data': {}, 'summary': '1 fichier déplacé.'}
        text = compose.files_cleanup({}, {'files.scan': scan, 'files.quarantine': moved}, permission_status='granted',
                                      run_id=9)
        self.assertNotIn('de la tâche n° 9.', text)
        self.assertIn('Restaurer', text)
        self.assertIn('Paramètres › Recherche web', _policy_message(ToolError('x', status=403)))

    def test_embedding_model_cannot_be_the_chat_model(self):
        for name in ('bge-m3:latest', 'bge-m3', 'nomic-embed-text'):
            with self.assertRaises(ValueError):
                config.update_settings({'local_model': name})
        self.assertEqual(config.update_settings({'local_fast_model': 'qwen3.5:4b'})['local_fast_model'], 'qwen3.5:4b')

    def test_quarantine_refuses_a_file_changed_since_the_preview(self):
        from pathlib import Path
        from aurora.tools import files
        sandbox = Path(os.environ['AURORA_HOME']).parent / 'sandbox'
        sandbox.mkdir(parents=True, exist_ok=True)
        target = sandbox / 'vieux_brouillon.txt'
        target.write_text('brouillon', encoding='utf-8')
        old = 1_600_000_000
        os.utime(target, (old, old))
        with mock.patch.object(files, '_extra_roots', return_value=[str(sandbox)]):
            preview = files.scan(str(sandbox), older_than_days=0)['files'][0]
            target.write_bytes(b'x' * 100_000)        # rewritten after the preview
            data = files.quarantine([preview['path']], 'r30', expected=[
                {'path': preview['path'], 'size': preview['size_bytes'], 'modified': preview['modified_at']}])
        self.assertEqual(data['moved'], [])
        self.assertIn("modifié depuis l'aperçu", data['refused'][0]['reason'])
        self.assertTrue(target.exists())

    async def test_web_summary_memory_is_external_and_recall_needs_a_model(self):
        from aurora.memory import store
        from aurora.memory.context import _is_external
        self.assertTrue(_is_external({'source': 'tool:web.search', 'kind': 'summary'}))
        self.assertTrue(_is_external({'source': 'astra-import', 'kind': 'fact'}))
        store.add_memory('Projet sonde Mars', 'Ma sonde vers Mars part en 2030', 'projet', kind='project')
        orch, *_ = build(FakeDecision('chat.general'), ollama=FakeOllama(up=False))
        result = await orch.handle_request('Parle-moi de ma sonde vers Mars')
        self.assertEqual(result['engine']['engine'], 'none')
        self.assertEqual(result['memories_used'], [])
        self.assertEqual(db.row('SELECT recall_count FROM memories')['recall_count'], 0)
        orch2, *_ = build(FakeDecision('chat.general'))
        used = await orch2.handle_request('Parle-moi de ma sonde vers Mars')
        self.assertTrue(used['memories_used'])
        self.assertEqual(db.row('SELECT recall_count FROM memories')['recall_count'], 1)


# --------------------------------------------------------------------------- #15 CORS

class CorsOriginTests(unittest.TestCase):
    def test_star_is_never_an_allowed_origin(self):
        from aurora import main
        with mock.patch.dict(os.environ, {'AURORA_ALLOWED_ORIGINS': '*,http://127.0.0.1:5173'}):
            self.assertNotIn('*', config.allowed_origins())
            self.assertNotIn('*', main._cors_origins())
            self.assertIn('http://127.0.0.1:5173', main._cors_origins())


# --------------------------------------------------------------------------- S1 S2 S3 S5 S6

class SharedShapeTests(CoreCase):
    async def test_s2_tier_prices_and_s5_chat_models(self):
        cloud = capabilities.cloud_state(provider=FakeClaude(configured=False))
        # Expert tier = Claude Opus 5.5 (CLAUDE.md), standard tier = Claude Sonnet 5.5.
        self.assertEqual(cloud['tiers']['expert']['prices'],
                         {'input': 4.0, 'output': 20.0, 'cache_read': 0.2, 'cache_write': 5.0})
        self.assertEqual(cloud['tiers']['rapide']['prices']['input'], 0.10)
        state = await capabilities.ollama_state(provider=FakeOllama(), settings=config.load_settings())
        self.assertEqual(state['models'], ['qwen3.5:9b', 'bge-m3:latest'])
        self.assertEqual(state['chat_models'], ['qwen3.5:9b'])

        class Declaring(FakeOllama):
            async def models(self):
                return [{'name': 'qwen3.5:9b'}, {'name': 'my-embedder:latest'}, {'name': 'llava:7b'}]

            async def model_capabilities(self, names):
                return {'qwen3.5:9b': ['completion', 'tools'], 'my-embedder:latest': ['embedding']}

        state = await capabilities.ollama_state(provider=Declaring(), settings=config.load_settings())
        self.assertEqual(state['chat_models'], ['qwen3.5:9b', 'llava:7b'])

    async def test_the_starting_model_follows_what_is_installed(self):
        """23/11/2026, ID Studio IA's garage PC (a card of 8 GB): the installer takes qwen3.5:4b only, and AURORA started on
        qwen3.5:9b, which is not there. As long as the person has chosen no model, the starting one is installed."""
        class Light(FakeOllama):
            async def models(self):
                return [{'name': 'qwen3.5:4b'}, {'name': 'bge-m3:latest'}]

        self.assertEqual(config.load_settings()['local_model'], 'qwen3.5:9b')     # nothing seen yet: the origin
        state = await capabilities.ollama_state(provider=Light(), settings=config.load_settings())
        self.assertEqual((state['chat_model'], state['chat_model_installed']), ('qwen3.5:4b', True))
        self.assertEqual(config.load_settings()['local_model'], 'qwen3.5:4b')     # what the next request will use
        self.assertFalse(config.local_model_chosen())                             # and nothing was written for it
        with TempEnv():                                                           # another AURORA folder: the origin
            self.assertEqual(config.local_model(), 'qwen3.5:9b')
        with mock.patch.dict(os.environ, {'AURORA_LOCAL_MODEL': 'gemma4:12b'}):   # a model given by the variable
            self.assertEqual(config.local_model(), 'gemma4:12b')
            self.assertTrue(config.local_model_chosen())
        # A model chosen in Paramètres passes first, installed or not: the screen then says it is missing.
        config.update_settings({'local_model': 'qwen3.5:9b'})
        self.assertTrue(config.local_model_chosen())
        state = await capabilities.ollama_state(provider=Light(), settings=config.load_settings())
        self.assertEqual((state['chat_model'], state['chat_model_installed']), ('qwen3.5:9b', False))
        # Both installed, or neither of the two: the origin.
        self.assertEqual(config.note_installed_models(['qwen3.5:9b', 'qwen3.5:4b']), 'qwen3.5:9b')
        self.assertEqual(config.note_installed_models(['gemma4:12b']), 'qwen3.5:9b')
        self.assertEqual(config.note_installed_models(['QWEN3.5:4B']), 'qwen3.5:4b')
        self.assertEqual(config.note_installed_models([]), 'qwen3.5:9b')

    def test_s6_graph_nodes_carry_pinned_and_verification(self):
        from aurora.memory import store
        store.add_memory('Projet Hélios', 'énergie solaire', 'projet', kind='project', pinned=True,
                         verification='user_confirmed')
        node = next(n for n in store.graph()['nodes'] if n['type'] == 'memory')
        self.assertIs(node['pinned'], True)
        self.assertEqual(node['verification'], 'user_confirmed')


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        from aurora import main
        from aurora.core import orchestrator as orch_module
        self.orch_module = orch_module
        orch, *_ = build(FakeDecision('chat.general'))
        orch_module.set_orchestrator(orch)
        self.client = TestClient(main.app, base_url=BASE, raise_server_exceptions=False)
        self.client.__enter__()
        token = self.client.get('/api/bootstrap', headers={'Origin': ORIGIN}).json()['token']
        self.auth = {'X-Aurora-Token': token}
        self.client.headers.update(self.auth)   # CLAUDE.md: the token is required on every request

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.orch_module.set_orchestrator(None)
        self.env.__exit__(None, None, None)

    def test_claude_key_goes_to_the_vault_and_is_never_returned(self):
        from aurora.security import vault
        stored: dict = {}
        with mock.patch.dict(os.environ, {'AURORA_VAULT': '1'}), \
                mock.patch.object(vault, 'write', side_effect=lambda key: stored.update(key=key)), \
                mock.patch.object(vault, 'read', side_effect=lambda: stored.get('key')), \
                mock.patch.object(vault, 'delete', side_effect=lambda: stored.pop('key', None) is not None):
            os.environ.pop('ANTHROPIC_API_KEY', None)
            self.assertFalse(self.client.get('/api/secrets/anthropic').json()['configured'])
            key = 'sk-ant-api03-' + 'x' * 40
            saved = self.client.put('/api/secrets/anthropic', json={'api_key': key})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()['source'], 'vault')
            self.assertNotIn(key, saved.text)                       # never returned
            self.assertEqual(stored['key'], key)
            self.assertTrue(self.client.get('/api/health').json()['capabilities']['cloud']['configured'])
            bad = self.client.put('/api/secrets/anthropic', json={'api_key': 'pas-une-cle-' + 'y' * 20})
            self.assertEqual(bad.status_code, 422)
            self.assertNotIn('y' * 20, bad.text)                    # never echoed either
            self.assertEqual(self.client.put('/api/secrets/anthropic', json={'api_key': key},
                                             headers={'X-Aurora-Token': ''}).status_code, 401)
            self.assertFalse(self.client.delete('/api/secrets/anthropic').json()['configured'])

    def test_s1_db_path_read_only_and_s3_caps_refreshed_after_patch(self):
        got = self.client.get('/api/settings').json()
        self.assertEqual(got['db_path'], str(config.db_path()))
        self.assertIn('web_contact', got)
        self.assertEqual(self.client.get('/api/health').json()['capabilities']['mode'], 'equilibre')
        patched = self.client.patch('/api/settings', json={'mode': 'prive', 'db_path': 'C:/ailleurs.db'},
                                    headers=self.auth)
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()['db_path'], str(config.db_path()))
        caps = self.client.get('/api/health').json()['capabilities']
        self.assertEqual(caps['mode'], 'prive')
        self.assertIsNone(caps['internet'])          # #31: no probe at all in private mode
        self.assertIn('chat_models', caps['ollama'])
        # Ceilings per mode (CLAUDE.md) go through the HTTP model too, and are validated.
        ceilings = {'cloud_request_budget_usd': 0.1, 'cloud_daily_budget_avance_usd': 4.0,
                    'cloud_request_budget_avance_usd': 0.8}
        patched = self.client.patch('/api/settings', json=ceilings, headers=self.auth)
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual({k: patched.json()[k] for k in ceilings}, ceilings)
        self.assertEqual(self.client.patch('/api/settings', json={'cloud_request_budget_usd': 0},
                                           headers=self.auth).status_code, 422)
        # A conversation-wide cloud consent exists in Équilibré and Avancé (04/11/2026): Privé revokes it.
        from aurora.security import consent
        consent.grant_conversation(5, 1)
        url = '/api/conversations/5/cloud-consent'
        self.assertEqual(self.client.get(url, headers={'X-Aurora-Token': ''}).status_code, 401)   # token required
        self.assertTrue(self.client.get(url, headers=self.auth).json()['active'])
        self.client.patch('/api/settings', json={'mode': 'equilibre'}, headers=self.auth)
        self.assertTrue(self.client.get(url, headers=self.auth).json()['active'])
        self.client.patch('/api/settings', json={'mode': 'prive'}, headers=self.auth)
        self.assertFalse(self.client.get(url, headers=self.auth).json()['active'])
        consent.grant_conversation(5, 1)
        self.assertFalse(self.client.delete(url, headers=self.auth).json()['active'])  # the revoke button
        self.assertIn('prices', caps['cloud']['tiers']['standard'])


if __name__ == '__main__':
    unittest.main()
