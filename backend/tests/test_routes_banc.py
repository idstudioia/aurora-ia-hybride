"""Three routes found wrong by the conversation bench of 22/11/2026 (reviews/banc-modeles, turns 8, 12 and 14).
No network, no real LLM, a temporary base.

The sentences here are NOT those of the bench, and each widening has its counter-examples (article of 14/10/2026,
§3: « élargir un déclencheur se paie ailleurs »):
- a question about the date or the hour of something the user asked AURORA to keep is read in the memory by the
  code, only when one memory holds every word of the subject AND a date or an hour; otherwise nothing changes;
- an attribute of the hardware that no tool reads (serial number, date of manufacture, warranty) is said unread,
  instead of the load of the component;
- a question about the CURRENT conversation goes to the model with the whole conversation, never to the search
  of a memory.
"""
import asyncio
import logging
import unittest

from aurora.core.normalize import extract_metric
from aurora.core.router import DecisionEngine
from aurora.memory import recall
from tests.doubles_core import SNAPSHOT, FakeDecision, FakeOllama, TempEnv, build

ENGINE = DecisionEngine(use_semantic=False)
_MODULE_ENV: TempEnv | None = None
VEGA = 'La démonstration du projet Vega aura lieu le 14 janvier à 15 h dans l’atelier'
ORION = 'La démonstration du projet Orion est fixée au 12 décembre 2026'


def setUpModule():
    global _MODULE_ENV
    logging.getLogger('aurora').setLevel(logging.CRITICAL)
    _MODULE_ENV = TempEnv().__enter__()


def tearDownModule():
    _MODULE_ENV.__exit__(None, None, None)


class RetainedDateQuestions(unittest.TestCase):
    def test_the_subject_of_a_question_about_a_date(self):
        cases = {
            'C’est quand, la démonstration du projet Vega ?': 'la démonstration du projet Vega',
            'À quelle heure est mon rendez-vous chez le dentiste ?': 'mon rendez-vous chez le dentiste',
            'C’est quel jour, la réunion avec le fournisseur ?': 'la réunion avec le fournisseur',
            'Elle est quand déjà, la livraison de l’imprimante ?': 'la livraison de l’imprimante',
            'Quand a lieu la démonstration du projet Vega ?': 'la démonstration du projet Vega',
            'la demo du projet vega c est quand': 'la demo du projet vega',
            'Quelle est la date de la réunion avec le fournisseur ?': 'la réunion avec le fournisseur',
            'C’est quand, Noël ?': 'Noël',   # a subject: the memory decides, and it holds nothing about it
        }
        for text, subject in cases.items():
            self.assertEqual(recall.dated_subject(text), subject, text)

    def test_other_questions_have_no_such_subject(self):
        for text in ('Pourquoi la démonstration du projet Vega est-elle en janvier ?', 'Quelle heure est-il ?',
                     'Quel jour sommes-nous ?', 'Rappelle-moi la démonstration à 14 h', 'Quelle est la date ?',
                     'Quel temps fera-t-il le 14 janvier ?', 'Explique-moi comment fonctionne un panneau solaire',
                     'Quelle est la date d’aujourd’hui ?', 'C’est quand ?', ''):
            self.assertIsNone(recall.dated_subject(text), text)

    def test_a_memory_says_a_date_or_not(self):
        for detail in (VEGA, 'Rendez-vous chez le dentiste le 3/12 à 9h30', 'Livraison mardi prochain',
                       'Réunion demain matin', 'La réunion est à 14:30'):
            self.assertTrue(recall.dated({'title': 'x', 'detail': detail}), detail)
        for detail in ('Le projet Lyra est une station météo de jardin', 'Pour le pain : 500 g de farine',
                       'Le budget est plafonné à 450 euros'):
            self.assertFalse(recall.dated({'title': 'x', 'detail': detail}), detail)

    def test_the_date_of_an_event_is_not_the_date_of_today(self):
        for text in ('Quelle est la date de la réunion avec le fournisseur ?', 'Quelle est la date du concert de Lina ?'):
            self.assertNotEqual(ENGINE.decide_fast(text).intent, 'time.now', text)
        for text in ('Quelle est la date d’aujourd’hui ?', 'Quelle est la date ?', 'Quelle est la date du jour ?',
                     'Quel jour sommes-nous ?', 'Quelle heure est-il ?'):
            self.assertEqual(ENGINE.decide_fast(text).intent, 'time.now', text)


class HardwareIdentity(unittest.TestCase):
    def test_what_no_tool_reads_is_its_own_metric(self):
        for text in ('Quel est le numéro de série de ma carte graphique ?', 'Quel est le numéro de série de mon processeur ?',
                     'Donne-moi le numéro de série de mon disque dur', 'Mon PC est encore sous garantie ?',
                     'Quelle est la date de fabrication de ma batterie ?', 'Quel est le n° de série de mon PC ?'):
            decision = ENGINE.decide_fast(text)
            self.assertEqual((decision.intent, decision.slots.get('metric')), ('system.status', 'identity'), text)

    @unittest.expectedFailure
    def test_known_limit_the_same_question_said_with_c_est_quoi(self):
        """Written before the correction and NOT met (22/11/2026): « c'est quoi … » is read as a question of
        definition (« c'est quoi la RAM ? »), which sends it to the conversation. Left as it is, and said."""
        decision = ENGINE.decide_fast('c est quoi le n° de série de mon pc')
        self.assertEqual((decision.intent, decision.slots.get('metric')), ('system.status', 'identity'))

    def test_a_measure_stays_a_measure(self):
        for text, metric in (('Quelle est la charge de ma carte graphique ?', 'gpu'), ('Quelle quantité de RAM est utilisée ?', 'ram'),
                             ('Quel est le modèle de ma carte graphique ?', 'gpu')):
            self.assertEqual(extract_metric(text), metric, text)
        for text in ('Quelle est la garantie de mon assurance ?', 'Quel est le numéro de série de la Joconde ?',
                     'Combien de temps dure la garantie légale en France ?'):
            self.assertNotEqual(ENGINE.decide_fast(text).intent, 'system.status', text)

    def test_the_answer_says_it_is_not_read_and_invents_nothing(self):
        from aurora.tools import system
        gpu = {'name': 'AMD Radeon RX 6750 XT', 'util_percent': 12.0, 'memory_used_mb': 4000, 'memory_total_mb': 12272}
        text = system.summarize_snapshot({**SNAPSHOT, 'gpu': gpu}, 'identity')
        self.assertIn('aucun outil', text)
        self.assertIn('AMD Radeon RX 6750 XT', text)
        self.assertNotIn('12', text.replace('12272', ''))   # the load of the card is not the answer to this question
        bare = system.summarize_snapshot(dict(SNAPSHOT), 'identity')
        self.assertIn('aucun outil', bare)
        self.assertNotRegex(bare, r'\d')   # no figure at all: nothing is read, nothing is invented


class CurrentConversation(unittest.TestCase):
    def test_questions_about_this_conversation(self):
        for text in ('Résume en deux phrases ce dont nous avons parlé.', 'Fais-moi un résumé de ce qu’on s’est dit',
                     'Tu peux me résumer ce dont on a parlé ?', 'Récapitule notre discussion depuis le début',
                     'Résume notre conversation', 'De quoi on a parlé ?', 'Résume ce qu’on vient de dire',
                     'Fais le point sur ce qu’on a dit depuis le début'):
            self.assertTrue(recall.about_current_conversation(text), text)

    def test_other_requests_are_not(self):
        for text in ('On avait parlé de Tesla', 'Tu te souviens de ce qu’on a dit sur le robot ?',
                     'Résume ce document en cinq points.', 'Résume les actualités de l’espace', 'De quoi parle ce livre ?',
                     'Résume notre conversation d’hier', 'Quelles sont nos dernières conversations ?'):
            self.assertFalse(recall.about_current_conversation(text), text)


class OrchestratorRoutes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def tearDown(self):
        self.env.__exit__(None, None, None)

    @staticmethod
    def keep(*details):
        from aurora.memory import store
        for detail in details:
            store.add_memory(detail, detail, 'projet', kind='fact', auto_link=False)

    async def test_a_kept_date_is_read_in_the_memory_without_any_model(self):
        self.keep(VEGA, ORION)
        orch, _tools, ollama, _claude = build(FakeDecision('chat.general', method='default', confidence=0.2))
        result = await orch.handle_request('Quand a lieu la démonstration du projet Vega ?')
        self.assertEqual((result['status'], result['decision']['intent']), ('done', 'memory.search'))
        self.assertEqual(ollama.calls, [])
        self.assertIn('14 janvier à 15 h', result['answer'])
        self.assertNotIn('12 décembre', result['answer'])   # the other project's demonstration is not the answer

    async def test_nothing_changes_without_a_memory_that_answers(self):
        cases = (
            ((), 'Quand a lieu la démonstration du projet Vega ?'),                        # nothing kept
            (('Le projet Vega est un robot de jardin',), 'C’est quand, la démonstration du projet Vega ?'),   # no date
            ((ORION,), 'C’est quand, la démonstration du projet Vega ?'),                    # another project
            ((VEGA,), 'Pourquoi la démonstration du projet Vega est-elle en janvier ?'),     # not a question of date
            ((VEGA,), 'C’est quand, Noël ?'),
        )
        for kept, question in cases:
            with self.subTest(question=question, kept=kept):
                self.tearDown()
                self.setUp()
                self.keep(*kept)
                orch, _tools, ollama, _claude = build(FakeDecision('chat.general', method='default', confidence=0.2),
                                                      ollama=FakeOllama(reply='Réponse du modèle.'))
                result = await orch.handle_request(question)
                self.assertEqual(result['decision']['intent'], 'chat.general')
                self.assertEqual(len(ollama.calls), 1)

    async def test_the_current_conversation_is_read_whole_by_the_model(self):
        def route(text):
            if 'conversation' in text or 'parlé' in text:   # what the rules answer today for these sentences
                return FakeDecision('memory.search', 0.8, slots={'query': text})
            return FakeDecision('chat.general', method='default', confidence=0.2)
        orch, _tools, ollama, _claude = build(route, ollama=FakeOllama(reply='Voici une réponse courte.'))
        subjects = ['les panneaux solaires', 'la recette du pain', 'les orbites de Mars', 'le langage Python', 'les volcans']
        conversation = None
        for subject in subjects:
            result = await orch.handle_request(f'Parle-moi de {subject} en une phrase.', conversation_id=conversation)
            conversation = result['conversation_id']
        for question in ('Résume notre conversation', 'Tu peux me résumer ce dont on a parlé ?'):
            result = await orch.handle_request(question, conversation_id=conversation)
            self.assertEqual(result['decision']['intent'], 'chat.general', question)
            prompt = ollama.calls[-1]['messages'][0]['content']
            for subject in subjects:   # the first exchange too, not only the last three
                self.assertIn(subject, prompt, question)

    async def test_a_past_conversation_is_still_recalled_by_the_code(self):
        orch, _tools, ollama, _claude = build(FakeDecision('memory.search', 0.8, slots={'query': 'Tesla'}))
        result = await orch.handle_request('On avait parlé de Tesla')
        self.assertEqual(result['decision']['intent'], 'memory.search')
        self.assertEqual(ollama.calls, [])
