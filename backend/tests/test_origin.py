"""Step 2 of 2026-10-28 (reviews/mod-verification/00-propositions.md): the origin badge of each answer.

Athéna's three registers (docs/JOURNAL_ORIGINE.md: Mesuré, Source, Modèle seul) plus memory (article
« Ce qui se mesure ne se génère pas », 14/10/2026), read on the steps that REALLY ran, never guessed from
the text. « rédigé par l'IA » marks an AI rewording measured or sourced data: on 28/10, a real search on
« Le Comte de Monte-Cristo » had right sources and a wrong sentence (« 2024 avec Jim »).
Offline, temporary AURORA_HOME (tests.doubles_core.TempEnv).
"""
import json
import logging
import unittest

from aurora import db
from aurora.core import verify
from tests.doubles_core import FakeDecision, FakeTools, build
from tests.test_review_fixes import CoreCase


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def step(stage, tool='', status='done', **result):
    """A step in check()'s shape (the shape verify.origin reads), not a database row (runs.step_spec)."""
    return {'stage': stage, 'tool': tool, 'status': status, 'result': result}


LINK = [{'title': 'Le Comte de Monte-Cristo — fr.wikipedia.org', 'url': 'https://fr.wikipedia.org/wiki/x', 'kind': 'web'}]
LLM = step('local_ai', 'llm')
ANSWER = step('response')


class OriginTests(unittest.TestCase):
    """verify.origin() alone: the path, not the words."""

    def test_each_register(self):
        cases = [
            ('local tool, no AI', [step('tool', 'system.snapshot', ok=True)], False, 0, ['mesure'], None),
            ('network tool with links', [step('tool', 'web.search', ok=True, sources=LINK)], False, 0, ['source'], None),
            ('memory hit', [step('memory', 'memory.search', hits=[{'memory': {'id': 1}}])], False, 0, ['memoire'], None),
            ('memory saved', [step('memory', 'memory.save', memory={'id': 2})], False, 0, ['memoire'], None),
            ('the AI alone', [LLM, ANSWER], True, 0, ['modele'], 'local'),
        ]
        for name, steps, llm, memories, kinds, writer in cases:
            with self.subTest(name):
                got = verify.origin(steps, llm_text_used=llm, memories_used=memories)
                self.assertEqual((got['kinds'], got['written_by']), (kinds, writer))

    def test_ai_rewording_sourced_data_is_said(self):
        got = verify.origin([step('tool', 'web.search', ok=True, sources=LINK), LLM, ANSWER], llm_text_used=True)
        self.assertEqual(got['kinds'], ['source'])
        self.assertEqual(got['written_by'], 'local')
        self.assertEqual((got['tools'], got['sources']), (['web.search'], 1))

    def test_mixed_path_keeps_every_register_in_a_fixed_order(self):
        steps = [step('tool', 'web.search', ok=True, sources=LINK), step('tool', 'system.snapshot', ok=True),
                 step('cloud_ai', 'llm'), ANSWER]
        got = verify.origin(steps, llm_text_used=True, memories_used=2)
        self.assertEqual(got['kinds'], ['mesure', 'source', 'memoire'])
        self.assertEqual(got['written_by'], 'cloud')

    def test_what_did_not_bring_anything_does_not_count(self):
        failed = step('tool', 'web.search', status='error', ok=False)
        no_link = step('tool', 'web.search', ok=True, sources=[{'title': 'Réponse instantanée', 'url': ''}])
        refused = step('tool', 'system.snapshot', ok=False)
        waiting = step('tool', 'files.quarantine', status='waiting_permission')
        empty_memory = step('memory', 'memory.search', hits=[])
        for s in (failed, no_link, refused, waiting, empty_memory):
            with self.subTest(s):
                # Nothing measured nor sourced: the AI wrote alone, and the badge says so.
                self.assertEqual(verify.origin([s, LLM, ANSWER], llm_text_used=True)['kinds'], ['modele'])
                self.assertIsNone(verify.origin([s], llm_text_used=False))

    def test_memories_given_to_the_model_count_only_if_its_text_is_used(self):
        self.assertEqual(verify.origin([LLM, ANSWER], llm_text_used=True, memories_used=1)['kinds'], ['memoire'])
        self.assertIsNone(verify.origin([LLM], llm_text_used=False, memories_used=1))

    def test_nothing_answered_has_no_badge(self):
        self.assertIsNone(verify.origin([], llm_text_used=False))


WEB = {'ok': True, 'provenance': 'searxng', 'summary': '1 résultat web',
       'data': {'results': [{'title': 'Le Comte de Monte-Cristo', 'url': 'https://fr.wikipedia.org/wiki/x',
                             'extract': 'Roman d’Alexandre Dumas paru de 1844 à 1846.'}]},
       'sources': LINK}
CLOCK = {'ok': True, 'provenance': 'horloge', 'summary': 'Il est 10 h.', 'data': {'time': '10:00'}}


class OriginFlowTests(CoreCase):
    """The badge on a real run, live (RunResult.origin) and in the history (message meta.origin)."""

    def stored_origin(self, run_id: int):
        rows = db.rows('SELECT meta_json FROM messages WHERE run_id=? AND role=?', (run_id, 'assistant'))
        return json.loads(rows[-1]['meta_json'] or '{}').get('origin')

    async def ask(self, intent: str, text: str, **slots) -> dict:
        orch, *_ = build(FakeDecision(intent, slots=slots), tools=FakeTools({'web.search': WEB, 'time.now': CLOCK}))
        return await orch.handle_request(text)

    async def test_clock_is_measured(self):
        r = await self.ask('time.now', 'quelle heure est-il ?')
        self.assertEqual(r['origin']['kinds'], ['mesure'])
        self.assertIsNone(r['origin']['written_by'])
        self.assertEqual(self.stored_origin(r['task_id']), r['origin'])

    async def test_search_is_sourced_and_written_by_the_local_ai(self):
        r = await self.ask('web.search', 'cherche sur le web Le Comte de Monte-Cristo', query='Le Comte de Monte-Cristo')
        self.assertEqual(r['status'], 'done')
        self.assertEqual(r['origin']['kinds'], ['source'])
        self.assertEqual(r['origin']['written_by'], 'local')
        self.assertEqual(self.stored_origin(r['task_id']), r['origin'])

    async def test_chat_alone_is_model_only(self):
        r = await self.ask('chat.general', 'raconte-moi une histoire courte')
        self.assertEqual(r['origin'], {'kinds': ['modele'], 'written_by': 'local', 'tools': [], 'sources': 0})

    async def test_who_wrote_an_earlier_answer_is_read_on_its_run(self):
        """08/11/2026 (run 28 of the real AURORA): asked « toi qui le fait de travail ou c'est Claude », the local
        model claimed the game Claude had written. The answer now comes from the record, without any model."""
        from aurora.memory import store
        from tests.doubles_core import FakeOllama
        ollama = FakeOllama(reply='Voici le jeu :\n```html\n<canvas id="jeu"></canvas>\n```')
        orch, *_ = build(lambda text: FakeDecision('chat.general' if text.startswith('Écris') else 'meta.capabilities'),
                         ollama=ollama)
        game = await orch.handle_request('Écris un petit jeu en HTML')
        conv = game['conversation_id']
        self.assertEqual(game['origin']['written_by'], 'local')
        # An answer written by Claude afterwards (no code), as the history stores it.
        store.add_message(conv, 'assistant', 'Une explication rédigée par Claude.',
                          meta={'origin': {'kinds': ['modele'], 'written_by': 'cloud', 'tools': [], 'sources': 0},
                                'context': {'provider': 'anthropic', 'model': 'claude-sonnet-5-5'}})
        calls = len(ollama.calls)
        last = await orch.handle_request('Quel modèle a répondu ?', conversation_id=conv)
        self.assertTrue(last['answer'].startswith('La réponse précédente (« Une explication rédigée par Claude. »'),
                        last['answer'])
        self.assertIn('a été rédigée par Claude (claude-sonnet-5-5)', last['answer'])
        code = await orch.handle_request("C'est toi ou Claude qui a écrit ce code ?", conversation_id=conv)
        self.assertTrue(code['answer'].startswith('La dernière réponse contenant du code (« Voici le jeu'), code['answer'])
        self.assertIn('a été rédigée par l’IA locale (qwen3.5:9b), sur ce PC, et non par Claude.', code['answer'])
        self.assertEqual(len(ollama.calls), calls)   # read on the record: no model was asked
        alone = await orch.handle_request('Qui a répondu ?')   # a new conversation
        self.assertTrue(alone['answer'].startswith('Je ne trouve aucune réponse précédente'), alone['answer'])


class UnverifiedFiguresTests(unittest.TestCase):
    """A3 (reviews/etapes-restantes.md): the dates and figures of a « modèle seul » answer are marked."""

    def test_dates_and_figures_written_from_memory(self):
        for answer, question, expected in [
            ('Le Comte de Monte-Cristo est paru en 1838. Il compte 117 chapitres [1].', '', ['1838', '117 chapitres']),
            ('La tour Eiffel mesure 324 mètres et attire 7 millions de visiteurs, soit 19 % du total.', '',
             ['324 mètres', '7 millions', '19 %']),
            ('Il y a 8 milliards d’humains et la Terre a 4,5 milliards d’années.', '', ['8 milliards', '4,5 milliards']),
            ('Le prix est de 8,99 € chez GoTronic.', '', ['8,99 €']),
        ]:
            with self.subTest(answer[:30]):
                self.assertEqual(verify.unverified_figures(answer, question), expected)

    def test_what_is_not_a_claim_stays_unmarked(self):
        self.assertEqual(verify.unverified_figures('Voici 3 conseils :\n1. Dormez bien.\n2. Buvez de l’eau.'), [])
        self.assertEqual(verify.unverified_figures('Bonjour ! Comment puis-je vous aider ?'), [])
        # The year asked by the user is his, not the model's claim; AURORA's own note is not the model's either.
        self.assertEqual(verify.unverified_figures('Le film sort en 2024 avec un budget de 42,9 millions.',
                                                   'Quel film sort en 2024 ?'), ['42,9 millions'])
        self.assertEqual(verify.unverified_figures('Rien à signaler.\n\nVérification AURORA : 1 référence [3].'), [])

    def test_the_measures_check_reads_numbers_as_before(self):
        # extended=False (the « mesures » check) is unchanged: no year, no bare large number.
        self.assertEqual([raw for raw, _, _ in verify.answer_numbers('Le CPU est à 23,5 % en 1838, pour 450 personnes.')],
                         ['23,5 %'])

    def test_an_amount_with_thousands_is_one_number(self):
        # P3 (29/10/2026): « 1 298,00 € » was read « 298,00 » and flagged as absent from the measures.
        found = verify.answer_numbers('Hausse de 1 298,00 € : de 70,50 € à 1 320,50 €, dont 1 250,00 €.')
        self.assertEqual([value for _, value, _ in found], [1298.0, 70.5, 1320.5, 1250.0])


class UnverifiedFlowTests(CoreCase):
    async def ask(self, intent: str, reply: str, text: str, **slots) -> dict:
        from tests.doubles_core import FakeOllama
        orch, *_ = build(FakeDecision(intent, slots=slots), tools=FakeTools({'web.search': WEB}),
                         ollama=FakeOllama(reply=reply))
        return await orch.handle_request(text)

    async def test_a_model_only_answer_marks_its_figures_live_and_in_history(self):
        r = await self.ask('chat.general', 'Il a été publié en 1838, en 18 volumes.', 'raconte-moi Monte-Cristo')
        self.assertEqual(r['origin']['kinds'], ['modele'])
        self.assertEqual(r['origin']['unverified'], ['1838', '18 volumes'])
        stored = OriginFlowTests.stored_origin(self, r['task_id'])      # same reading as the history
        self.assertEqual(stored['unverified'], ['1838', '18 volumes'])

    async def test_a_sourced_answer_is_not_marked(self):
        r = await self.ask('web.search', 'Publié de 1844 à 1846 [1].', 'cherche Monte-Cristo', query='Monte-Cristo')
        self.assertEqual(r['origin']['kinds'], ['source'])
        self.assertNotIn('unverified', r['origin'])


if __name__ == '__main__':
    unittest.main()
