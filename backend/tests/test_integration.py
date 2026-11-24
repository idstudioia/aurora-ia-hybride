"""Cross-module fixes found by the live integration smoke test (B6). No network, no real LLM.

Each test pins one behaviour observed on the real server:
- identity questions are answered by the language model, not by the capability template;
- a trailing "Aurora" that names a project is kept in the query, a vocative one is removed;
- memory.search is labelled 'tool' (its answer is a template, no LLM step);
- capability answers lead with the part the question asks about;
- an explicit Claude request that cannot be honoured is answered locally, with a note;
- a document's memory recall uses the document, not « Analyse ce texte »;
- the web_contact setting is validated;
- every httpx client reuses one TLS context.
"""
import asyncio
import logging
import unittest

from aurora import config
from aurora.core import compose
from aurora.core.engine_select import select_engine
from aurora.core.normalize import extract_query, normalize, strip_wake_word
from aurora.core.router import DecisionEngine
from tests.doubles_core import FakeDecision, FakeOllama, FakeTools, TempEnv, build

ENGINE = DecisionEngine(use_semantic=False)
_MODULE_ENV: TempEnv | None = None


def setUpModule():
    """Module-wide temporary home/database (user_agent() reads a setting): the real ~/.aurora is never opened."""
    global _MODULE_ENV
    logging.getLogger('aurora').setLevel(logging.CRITICAL)
    _MODULE_ENV = TempEnv().__enter__()


def tearDownModule():
    _MODULE_ENV.__exit__(None, None, None)


CAPS = {'internet': True, 'mode': 'equilibre',
        'ollama': {'up': True, 'models': ['qwen3.5:9b', 'bge-m3'], 'chat_model': 'qwen3.5:9b',
                   'embed_model': 'bge-m3', 'embed_ready': True},
        'cloud': {'configured': False, 'provider': 'anthropic', 'tiers': {}}}


class RoutingFixes(unittest.TestCase):
    def test_identity_goes_to_the_language_model(self):
        for text in ('Bonjour, qui es-tu ?', 'Présente-toi', 'Comment tu t’appelles ?', 'Tu es qui ?'):
            self.assertEqual(ENGINE.decide_fast(text).intent, 'chat.general', text)
        for text in ('Que sais-tu faire ?', 'As-tu accès à Internet ?', 'Quel modèle utilises-tu ?'):
            self.assertEqual(ENGINE.decide_fast(text).intent, 'meta.capabilities', text)

    def test_explain_what_is_x_is_a_knowledge_question(self):
        """« Explique en une phrase ce qu'est une orbite » used to run a 400 km Earth-orbit simulation."""
        for text in ('Explique en une phrase ce qu’est une orbite', 'Explique ce qu’est un séisme',
                     'Explique-moi ce qu’est la RAM'):
            self.assertEqual(ENGINE.decide_fast(text).intent, 'chat.general', text)
        self.assertEqual(ENGINE.decide_fast('Explique-moi pourquoi mon PC ralentit').intent, 'system.diagnose')
        self.assertEqual(ENGINE.decide_fast('Simule une orbite autour de Mars').intent, 'sim.orbit')

    def test_trailing_wake_word(self):
        self.assertEqual(strip_wake_word('Retrouve notre projet AURORA'), 'Retrouve notre projet AURORA')
        self.assertEqual(extract_query('Retrouve notre projet AURORA', kind='memory'), 'projet AURORA')
        self.assertEqual(ENGINE.decide_fast('Retrouve notre projet AURORA').slots.get('query'), 'projet AURORA')
        self.assertEqual(normalize('ouvre mon projet aurora'), 'ouvre mon projet aurora')
        # Vocative: removed, with or without the comma.
        self.assertEqual(normalize('Quelle heure est-il, Aurora ?'), 'quelle heure est il')
        self.assertEqual(normalize('quelle heure est il aurora'), 'quelle heure est il')
        self.assertEqual(strip_wake_word('Quelle heure est-il, Aurora ?'), 'Quelle heure est-il?')
        self.assertEqual(strip_wake_word('Merci Aurora'), 'Merci')
        self.assertEqual(ENGINE.decide_fast('Merci Aurora').intent, 'chat.general')

    def test_memory_search_engine_label(self):
        decision = ENGINE.decide_fast('Retrouve notre projet sur Mars')
        choice = select_engine(decision, mode='equilibre', text='Retrouve notre projet sur Mars', caps=CAPS)
        self.assertEqual(choice.engine, 'tool')
        web = ENGINE.decide_fast('Recherche les dernières découvertes spatiales')
        self.assertEqual(select_engine(web, mode='equilibre', text='x', caps=CAPS).engine, 'tool+local')


class CapabilitiesAnswer(unittest.TestCase):
    def test_question_part_comes_first(self):
        internet = compose.capabilities_answer(CAPS, 'As-tu accès à Internet ?')
        self.assertTrue(internet.startswith('Oui, j’ai accès à Internet'), internet)
        models = compose.capabilities_answer(CAPS, 'Quel modèle utilises-tu ?')
        self.assertTrue(models.startswith('IA locale : Ollama actif, modèle qwen3.5:9b'), models)
        self.assertIn('Claude (Anthropic) : non configuré.', models)
        self.assertNotIn('Voici ce que je sais faire', models)   # a precise question gets its answer, not the list
        general = compose.capabilities_answer(CAPS, 'Que sais-tu faire ?')
        self.assertTrue(general.startswith('Je suis AURORA'), general)
        self.assertNotIn('Oui,', general)
        # every capability, from the tool registry (29/10/2026: the old sentence named six of them)
        for line in ('- Recherche web :', '- Analyse de données :', '- Studio :', '- Routines :', '- Agent :'):
            self.assertIn(line, general)
        prive = compose.capabilities_answer({**CAPS, 'mode': 'prive'}, 'Es-tu connectée à Internet ?')
        self.assertIn('chaque requête web demande votre autorisation', prive)
        offline = compose.capabilities_answer({**CAPS, 'internet': False}, 'As-tu Internet ?')
        self.assertTrue(offline.startswith('Internet est indisponible'), offline)

    def test_who_wrote_the_answer_is_read_on_its_run(self):
        """08/11/2026 (run 28): « toi qui fais le travail ou c'est Claude ? » is answered from the record."""
        question = "C'est toi ou Claude qui a écrit ce code ?"
        game = {'written_by': 'cloud', 'model': 'claude-sonnet-5-5', 'tools': [], 'deterministic': False, 'code': True,
                'at': '2026-10-29T14:59:00Z', 'excerpt': 'JavaScript + Three.js : un simple fichier HTML ouvert dans '
                                                         'le navigateur, sans installation.'}
        answer = compose.capabilities_answer(CAPS, question, author=game)
        self.assertTrue(answer.startswith('La dernière réponse contenant du code (« JavaScript + Three.js'), answer)
        self.assertIn('a été rédigée par Claude (claude-sonnet-5-5), le modèle d’Anthropic dans le cloud, et non par '
                      'l’IA locale.', answer)
        self.assertIn('\nEn général : IA locale : Ollama actif, modèle qwen3.5:9b', answer)
        local = compose.capabilities_answer(CAPS, 'Quel modèle a répondu ?',
                                            author={**game, 'written_by': 'local', 'model': 'gemma4:e4b', 'code': False})
        self.assertTrue(local.startswith('La réponse précédente (« JavaScript'), local)
        self.assertIn('a été rédigée par l’IA locale (gemma4:e4b), sur ce PC, et non par Claude.', local)
        tools = compose.capabilities_answer(CAPS, 'Qui a répondu ?', author={
            'written_by': None, 'model': None, 'tools': ['time.now'], 'deterministic': True, 'excerpt': 'Il est 10 h.'})
        self.assertIn('n’a été rédigée par aucune IA : AURORA l’a composée à partir de ses outils (time.now).', tools)
        fixed = compose.capabilities_answer(CAPS, 'Qui a répondu ?', author={'deterministic': True, 'excerpt': ''})
        self.assertTrue(fixed.startswith('La réponse précédente n’a été rédigée par aucune IA'), fixed)
        unknown = compose.capabilities_answer(CAPS, 'Qui a répondu ?', author={'excerpt': 'Bonjour.'})
        self.assertIn('ne garde aucune trace de qui l’a rédigée : je ne peux pas le dire.', unknown)
        nothing = compose.capabilities_answer(CAPS, 'Qui a répondu ?', author=None)
        self.assertTrue(nothing.startswith('Je ne trouve aucune réponse précédente dans cette conversation'), nothing)
        # Claude configured: the general line says when Claude writes (04/11/2026: never because of a tier in the bar)
        cloud = compose.capabilities_answer({**CAPS, 'cloud': {'configured': True}}, question, author=game)
        self.assertIn('quand vous choisissez le moteur « Claude » (ou dites « utilise Claude »), ou en secours', cloud)
        self.assertNotIn('palier', cloud)

    def test_the_terminal_question_reads_the_real_setting(self):
        """08/11/2026 (run 47 of the real AURORA, by voice): « tu as le terminal dans tes applications ou pas ? » was
        answered « je ne peux pas » by the local AI while the terminal with permission was on."""
        from aurora.core import capabilities
        question = 'Est-ce que tu as le terminal dans tes applications ou pas ?'
        on = compose.capabilities_answer({**CAPS, 'shell': True}, question)
        self.assertTrue(on.startswith('Le terminal avec autorisation est activé (Paramètres) : une commande Windows à '
                                      'la fois'), on)
        self.assertNotIn('Voici ce que je sais faire', on)
        off = compose.capabilities_answer({**CAPS, 'shell': False}, 'Le terminal est-il activé ?')
        self.assertTrue(off.startswith('Le terminal avec autorisation est désactivé : aucune commande ne peut être '
                                       'lancée.'), off)
        self.assertIn('Le terminal avec autorisation est activé', compose.capabilities_answer({**CAPS, 'shell': True},
                                                                                             'Que sais-tu faire ?'))
        # What the AI is told in its prompt follows the same setting; unknown (no measure): the general rule only.
        self.assertIn('Le terminal avec autorisation est activé', capabilities.describe({**CAPS, 'shell': True}))
        self.assertIn('Le terminal avec autorisation est désactivé', capabilities.describe({**CAPS, 'shell': False}))
        self.assertIn('peut être activé dans les Paramètres (désactivé par défaut)',
                      capabilities.describe({k: v for k, v in CAPS.items() if k != 'shell'}))
        for text in (question, 'Tu as accès au terminal ?', 'Le terminal est-il activé ?', 'As-tu accès à PowerShell ?',
                     'Peux-tu utiliser le terminal ?', 'Tu peux lancer des commandes ou pas ?'):
            self.assertEqual(ENGINE.decide_fast(text).intent, 'meta.capabilities', text)
        for text in ('Lance la commande dir', 'Dans le terminal : dir'):   # an order stays an order
            self.assertEqual(ENGINE.decide_fast(text).intent, 'shell.run', text)

    def test_the_ai_knows_it_writes_code_and_says_no_monsieur(self):
        """08/11/2026 (runs 46-47 of the real AURORA): asked for a little game, the local AI answered « je ne peux pas
        programmer de mini-jeux », and it greeted « Bonjour, Monsieur » although nothing asks for it."""
        from aurora.core import prompts
        system = prompts.system_prompt(CAPS)
        self.assertIn('Ne l’appelle jamais « Monsieur » ni « Madame »', system)
        self.assertIn('- Écrire : tu rédiges toi-même du texte et du code complet', system)
        self.assertIn('ne réponds jamais que tu ne sais pas programmer', system)
        self.assertIn('tu ne l’exécutes pas et ne crées aucun fichier', system)
        stable, _volatile = prompts.system_prompt_parts(CAPS)   # Claude's prompt says the same
        self.assertIn('Ne l’appelle jamais « Monsieur »', stable)
        general = compose.capabilities_answer(CAPS, 'Que sais-tu faire ?')
        self.assertIn('- Rédaction : écrire des textes, des explications et du code, par exemple un petit jeu', general)
        self.assertIn('ce code n’est ni lancé ni enregistré sur le PC par AURORA', general)


class OrchestratorFixes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def tearDown(self):
        self.env.__exit__(None, None, None)

    async def test_explicit_cloud_unavailable_answers_locally_with_note(self):
        ollama = FakeOllama(reply='La relativité restreinte relie espace et temps.')
        orch, _tools, ollama, claude = build(FakeDecision('chat.general', method='default', confidence=0.2),
                                             ollama=ollama)
        result = await orch.handle_request('Utilise Claude pour m’expliquer la relativité',
                                           options={'engine': 'cloud'})
        self.assertEqual(result['status'], 'done')
        self.assertEqual(claude.calls, [])
        self.assertTrue(result['answer'].startswith('Claude n’est pas utilisé : Claude n’est pas configuré'),
                        result['answer'])
        self.assertIn('La relativité restreinte', result['answer'])
        prompt = ollama.calls[0]['messages'][0]['content']
        self.assertIn('Réponds directement à sa demande', prompt)

    async def test_no_note_without_explicit_request(self):
        orch, *_ = build(FakeDecision('chat.general'), ollama=FakeOllama(reply='Bonjour !'))
        result = await orch.handle_request('Bonjour, qui es-tu ?')
        self.assertEqual(result['answer'], 'Bonjour !')

    async def test_web_refused_in_private_mode_is_a_decision_not_an_error(self):
        config.update_settings({'mode': 'prive'})
        orch, tools, *_ = build(FakeDecision('web.search', slots={'query': 'Mars'}), tools=FakeTools())
        result = await orch.handle_request('Recherche les dernières découvertes sur Mars')
        self.assertEqual(result['status'], 'waiting_permission')
        self.assertEqual(result['permission']['action'], 'web.fetch')
        done = await orch.decide_permission(result['permission']['id'], 'deny')
        self.assertEqual(done['status'], 'done')
        self.assertIn('l’accès à Internet a été refusé', done['answer'])
        self.assertEqual(tools.calls, [])

    async def test_greeting_does_not_recall_a_summary_that_says_bonjour(self):
        from aurora.memory import store
        store.add_memory('Résumé : mon PC est lent', 'Bonjour. Votre disque C est presque plein.', 'diagnostic',
                         kind='summary', auto_link=False)
        orch, _tools, ollama, _claude = build(FakeDecision('chat.general'), ollama=FakeOllama(reply='Bonjour !'))
        result = await orch.handle_request('Bonjour, qui es-tu ?')
        self.assertEqual(result['memories_used'], [])
        self.assertNotIn('disque C', ollama.calls[0]['messages'][0]['content'])

    async def test_document_recall_uses_the_document(self):
        from aurora.memory import store
        store.add_memory('Résumé : mon PC est lent. Analyse le problème', 'Disque C presque plein.',
                         'diagnostic', kind='summary', auto_link=False)
        store.add_memory('Migration PostgreSQL', 'Projet de migration de la base vers PostgreSQL.', 'projet',
                         kind='project', auto_link=False)
        orch, _tools, ollama, _claude = build(FakeDecision('doc.analyze'), ollama=FakeOllama(reply='Résumé.'))
        doc = {'name': 'note.txt', 'text': "L'équipe migre la base de données vers PostgreSQL d'ici la fin du "
                                           "trimestre."}
        result = await orch.handle_request('Analyse ce texte', attachments=[doc])
        titles = [m['title'] for m in result['memories_used']]
        self.assertIn('Migration PostgreSQL', titles)
        self.assertFalse(any(t.startswith('Résumé : mon PC') for t in titles), titles)
        self.assertIn('ne les utilise que s’ils portent sur le même sujet', ollama.calls[0]['messages'][0]['content'])


class SettingsAndHttp(unittest.TestCase):
    def test_web_contact_setting(self):
        with TempEnv():
            self.assertEqual(config.load_settings()['web_contact'], '')
            self.assertEqual(config.update_settings({'web_contact': 'moi@example.org'})['web_contact'],
                             'moi@example.org')
            from aurora.tools.base import user_agent
            self.assertIn('moi@example.org', user_agent())
            for bad in ('pas un contact', 'x' * 201 + '@a.fr', 'a@b.fr; DROP'):
                with self.assertRaises(ValueError):
                    config.update_settings({'web_contact': bad})
            self.assertEqual(config.update_settings({'web_contact': ''})['web_contact'], '')

    def test_small_files_have_an_exact_size_in_the_preview(self):
        import tempfile
        from pathlib import Path
        from aurora import db
        from aurora.tools import files
        with TempEnv(), tempfile.TemporaryDirectory(dir=Path.home() / 'AppData' / 'Local' / 'Temp') as tmp:
            for name in ('a.txt', 'b.txt'):
                (Path(tmp) / name).write_text('x' * 400, encoding='utf-8')
            db.set_setting('allowed_roots', [tmp])
            scan = files.scan(tmp, older_than_days=0)
            sizes = [compose.file_size_bytes(f) for f in scan['files']]
            self.assertEqual(sorted(sizes), [400, 400])
            self.assertEqual(compose.fr_size(sum(sizes)), '800 octets')

    def test_shared_tls_context(self):
        """httpx builds a TLS context per client (~190 ms here) unless one is passed: creating
        clients must now be nearly free."""
        import time
        from aurora.netutil import ssl_context
        from aurora.providers.ollama import OllamaProvider
        from aurora.tools.base import http_client
        self.assertIs(ssl_context(), ssl_context())
        provider = OllamaProvider()

        async def make(n: int) -> float:
            start = time.perf_counter()
            for _ in range(n):
                async with provider._client(5.0), http_client(5.0):
                    pass
            return (time.perf_counter() - start) * 1000
        asyncio.run(make(1))   # warm-up: the shared context is built once
        self.assertLess(asyncio.run(make(10)), 300.0)   # 20 clients; was ~3.8 s without the shared context


if __name__ == '__main__':
    unittest.main()
