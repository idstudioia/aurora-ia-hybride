"""Step 1 of 2026-10-28 (reviews/athena-reprise-et-feuille-de-route.md): run n° 8 « dis-moi une recherche
sur le dernier modèle dia vocale » was answered by the local model, which claimed a search it never made.

- routing: « une recherche sur… » phrasings go to the web search;
- vague subjects (core/vague.py, ported from Athéna's sujetVague.ts with its test cases): a question first,
  completed by the user's short answer on the next turn;
- verification: a search claimed or promised without a search step fails the « capacites » check.
Offline, temporary AURORA_HOME (tests.doubles_core.TempEnv).
"""
import logging
import unittest

from aurora.core import rules, vague, verify
from aurora.core.normalize import extract_query
from tests.doubles_core import FakeDecision, FakeTools, TempEnv, build
from tests.test_review_fixes import CoreCase


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


class VagueSubjectTests(unittest.TestCase):
    """Athéna's tests/sujet-vague.test.ts, same phrases (vous instead of tu in the questions)."""

    def test_real_vague_phrases_ask_instead_of_searching(self):
        # Athéna, 05/10/2026 17:39 — first question of the conversation, « IA » never said.
        s1 = extract_query("bonjour AURORA je voudrais que tu me fasses une petite recherche sur les derniers "
                           "modèles d'hier")
        self.assertRegex(vague.vague_question(s1) or '', 'modèles de quoi')
        # Athéna, 09/09/2026 17:47, confirmed report.
        s2 = extract_query('tu peux me faire une recherche sur internet sur le type')
        self.assertRegex(vague.vague_question(s2) or '', 'type de quoi')

    def test_real_subjects_still_search(self):
        for subject in ["les derniers modèles d'IA", 'les dernier model IA ou systeme en local LLM',
                        'les nouvelle model de robot', 'la derniere version de Ollama', 'les modeles IA locaux',
                        'le prix des modules Peltier', 'les modules Peltier', 'le cuivre', 'thermoelectricite',
                        'effet Seebeck', 'phi4:14b', 'qwen3:8b', "l'histoire avant la colonisation du Maroc",
                        # 28/10, run n° 8: a real subject once « dia » is read as « d'IA »
                        extract_query('dis-moi une recherche sur le dernier modèle dia vocale')]:
            self.assertIsNone(vague.vague_question(subject), subject)

    def test_answer_on_next_turn_completes_the_subject(self):
        pending = "derniers modèles d'hier"
        self.assertEqual(vague.subject_after_precision("d'IA", pending), "derniers modèles d'IA d'hier")
        self.assertEqual(vague.subject_after_precision("c'est de voitures", pending),
                         "derniers modèles de voitures d'hier")
        # An answer that says everything again becomes the subject.
        self.assertEqual(vague.subject_after_precision("les modèles d'intelligence artificielle", pending),
                         "les modèles d'intelligence artificielle")
        self.assertEqual(vague.subject_after_precision('de quoi ? du téléphone.', 'le type'),
                         'le type de quoi ? du téléphone')

    def test_refusal_long_sentence_or_nothing_pending(self):
        pending = "derniers modèles d'hier"
        self.assertIsNone(vague.subject_after_precision('non laisse tomber', pending))
        self.assertIsNone(vague.subject_after_precision(
            'en fait je voulais te parler de mon projet et de la batterie du prototype', pending))
        self.assertIsNone(vague.subject_after_precision("d'IA", ''))


class SearchRoutingTests(unittest.TestCase):
    def test_une_recherche_sur_goes_to_web_search(self):
        for text in ['dis-moi une recherche sur le dernier modèle dia vocale',
                     "je voudrais que tu me fasses une petite recherche sur les derniers modèles d'hier",
                     'tu peux me faire une recherche rapide sur les modules Peltier',
                     'une recherche à propos du télescope James Webb']:
            self.assertEqual(rules.analyze(text).intent, 'web.search', text)

    def test_own_research_and_memory_are_not_web_searches(self):
        for text in ['ma recherche sur le cancer avance bien', 'fais une recherche dans ma mémoire sur Lyon']:
            self.assertNotEqual(rules.analyze(text).intent, 'web.search', text)

    def test_query_without_trigger_words_and_dia_read_as_ia(self):
        self.assertEqual(extract_query('dis-moi une recherche sur le dernier modèle dia vocale'),
                         "dernier modèle d'IA vocale")
        self.assertEqual(extract_query("je voudrais que tu me fasses une petite recherche sur les derniers modèles "
                                       "d'hier"), "derniers modèles d'hier")
        # « dia » alone is left as written.
        self.assertEqual(extract_query('recherche Dia de los muertos'), 'Dia de los muertos')


class ClaimedSearchTests(unittest.TestCase):
    """Each pattern carries its own faults AND its correct sentences (Athéna's lesson, tests/interdits.ts)."""

    WEB_STEP = [{'status': 'done', 'stage': 'tool', 'tool': 'web.search',
                 'result': {'ok': True, 'provenance': 'wikipedia', 'data': {'results': []}}}]

    def capacites(self, answer: str, steps: list | None = None) -> dict:
        result = verify.check(answer=answer, steps=steps or [], caps={'internet': True}, mode='equilibre',
                              permissions={}, llm_text_used=True)
        return next(c for c in result['checks'] if c['name'] == 'capacites')

    def test_claims_and_promises_without_a_search_step_fail(self):
        for answer in [
            # 28/10, run n° 8, word for word
            "Pour vous informer sur le dernier modèle de dialogue vocal, j'ai effectué une recherche via les "
            "outils d'internet disponibles (Wikipedia).",
            "J'ai fait une petite recherche : voici les modèles récents.",
            'Après avoir consulté plusieurs sources en ligne, voici ma réponse.',
            "D'après mes recherches, le modèle le plus récent est sorti hier.",
            'En consultant Wikipédia, on voit que ce modèle est récent.',
            # Athéna, 06/09: a promise with no tool behind it
            'Je peux rechercher maintenant pour vous les informations fiables sur les modules Peltier.',
            'Je lance la recherche pour les modules Peltier selon vos critères.',
        ]:
            self.assertFalse(self.capacites(answer)['ok'], answer)

    def test_correct_sentences_pass(self):
        for answer in [
            "Je n'ai pas effectué de recherche : aucune recherche web n'a été lancée.",
            'Si vous voulez, je peux faire une recherche sur Wikipédia.',
            'Écrivez « recherche modules Peltier » et la recherche partira vers le vrai moteur.',
            "J'ai effectué le calcul : 8,5 A sous 12 V donnent 102 W.",
            # the clarification answer of this step
            "Des modèles de quoi ? « derniers modèles d'hier » est trop vague pour une recherche utile : "
            'précisez-le, et la recherche partira avec votre précision.',
        ]:
            self.assertTrue(self.capacites(answer)['ok'], answer)

    def test_a_real_search_step_allows_the_claim(self):
        self.assertTrue(self.capacites("J'ai effectué une recherche sur Wikipédia [1].", self.WEB_STEP)['ok'])

    def test_denying_a_search_that_ran_fails(self):
        for answer in [
            # 28/10, real run « Cherche sur le web Le Comte de Monte-Cristo » (SearXNG, 3 sources), word for word
            "Pour cette demande, aucune recherche web active n'a été effectuée (mesure du moment : 18:38), les "
            'réponses reposent uniquement sur l\'analyse des textes externes cités ci-dessus.',
            "Je n'ai pas effectué de recherche : aucune recherche web n'a été lancée.",
            "Je n'ai effectué aucune recherche, voici ce que je sais.",
            'Sans avoir fait de recherche, je pense que le roman date de 1838.',
        ]:
            with self.subTest(answer):
                self.assertFalse(self.capacites(answer, self.WEB_STEP)['ok'])
                self.assertTrue(self.capacites(answer)['ok'], 'sans étape de recherche, la phrase est vraie')

    def test_sentences_that_do_not_deny_the_search_pass(self):
        for answer in [
            "Je n'ai pas fait de recherche supplémentaire : les sources ci-dessus suffisent [1].",
            "La recherche n'a rien donné de plus récent que 2024 [1].",
            "Je n'ai pas trouvé la date de sortie exacte dans les sources [2].",
            "Aucune recherche supplémentaire n'est nécessaire.",
        ]:
            with self.subTest(answer):
                self.assertTrue(self.capacites(answer, self.WEB_STEP)['ok'])


WEB = {'ok': True, 'provenance': 'wikipedia', 'summary': '1 résultat',
       'data': {'results': [{'title': 'Modèle de langage', 'url': 'https://fr.wikipedia.org/wiki/x',
                             'text': 'Un modèle de langage est un modèle statistique.'}]},
       'sources': [{'title': 'Modèle de langage', 'url': 'https://fr.wikipedia.org/wiki/x', 'kind': 'web'}]}


class VagueSearchFlowTests(CoreCase):
    async def test_question_first_then_search_with_the_precision(self):
        def route(text: str) -> FakeDecision:
            if 'recherche' in text:
                return FakeDecision('web.search', slots={'query': extract_query(text)})
            return FakeDecision('chat.general')
        orch, tools, *_ = build(route, tools=FakeTools({'web.search': WEB}))
        first = await orch.handle_request("je voudrais que tu me fasses une petite recherche sur les derniers "
                                          "modèles d'hier")
        self.assertEqual(tools.calls, [], 'nothing searched for a vague subject')
        self.assertIn('Des modèles de quoi', first['answer'])
        self.assertNotIn('Vérification AURORA', first['answer'])
        second = await orch.handle_request("d'IA", conversation_id=first['conversation_id'])
        self.assertEqual(tools.called('web.search'), [{'query': "derniers modèles d'IA d'hier"}])
        self.assertEqual(second['decision']['intent'], 'web.search')

    async def test_another_request_after_the_question_is_not_hijacked(self):
        def route(text: str) -> FakeDecision:
            if 'recherche' in text:
                return FakeDecision('web.search', slots={'query': extract_query(text)})
            return FakeDecision('time.now')
        orch, tools, *_ = build(route, tools=FakeTools({'web.search': WEB, 'time.now': {
            'ok': True, 'provenance': 'horloge', 'summary': 'Il est 10 h.', 'data': {'time': '10:00'}}}))
        first = await orch.handle_request('tu peux me faire une recherche sur internet sur le type')
        await orch.handle_request('quelle heure est-il', conversation_id=first['conversation_id'])
        self.assertEqual(tools.called('web.search'), [])


if __name__ == '__main__':
    unittest.main()
