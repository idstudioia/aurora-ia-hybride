"""E5 (29/10/2026): recall of past conversations, read in the base (memory/recall.py, from Athéna's
rappelConversations.ts and its tests rappel-conversations.test.ts, with neutral sentences)."""
import logging
import unittest

from aurora.memory import recall, store
from aurora.tasks import events
from tests.doubles_core import FakeDecision, TempEnv, build
from tests.test_orchestrator import CoreTestCase


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def conversation(*pairs, title='', meta=None, at=None):
    """A conversation of (question, answer) pairs; `at` dates the messages (ISO UTC)."""
    conv = store.create_conversation(title, created_at=at)
    for question, answer in pairs:
        store.add_message(conv['id'], 'user', question, meta=meta, created_at=at)
        if answer is not None:
            store.add_message(conv['id'], 'assistant', answer, meta=meta, created_at=at)
    return conv


class RequestTests(unittest.TestCase):
    def test_without_a_subject_the_last_conversations_are_listed(self):
        for text in ('dis-moi quelle est la dernière conversation que tu te souviens',
                     "quels sont les derniers sujets dont tu te souviens qu'on a abordé ensemble ?",
                     'tu devrais pouvoir voir les conversations précédentes',
                     "tu peux écouter d'autres conversations", 'Quelles sont nos dernières conversations ?'):
            self.assertEqual(recall.recall_request(text), {'mode': 'list'}, text)

    def test_a_subject_is_found(self):
        expected = [("on avait parlé de Tesla tout à l'heure", 'Tesla'),
                    ("tu te souviens de ce qu'on a dit sur les modules Peltier", 'les modules Peltier'),
                    ("qu'est-ce qu'on a dit hier sur la batterie DALY", 'la batterie DALY'),
                    ('retrouve notre conversation sur le pack de batterie', 'le pack de batterie'),
                    ('On avait parlé des modules Peltier la semaine dernière', 'modules Peltier')]
        for text, subject in expected:
            self.assertEqual(recall.recall_request(text), {'mode': 'subject', 'subject': subject, 'careful': False}, text)

    def test_my_x_is_careful_and_a_following_question_too(self):
        self.assertEqual(recall.recall_request('tu te souviens de mon banc d’essai ?'),
                         {'mode': 'subject', 'subject': 'mon banc d’essai', 'careful': True})
        self.assertEqual(recall.recall_request("on avait parlé de mon brevet tout à l'heure, tu te souviens de la date ?"),
                         {'mode': 'subject', 'subject': 'mon brevet', 'careful': True})

    def test_counter_examples(self):
        for text in ('non en fait on avait vu tu avais mal compris je parlais de GLB',
                     "Tu te souviens de ce qu'on a changé ?", 'de quoi on a parlé ?', "tu peux résumer ce qu'on a dit",
                     'premier message que tu te souviens dans la conversation', 'cherche TEC1-12706 dans le code de vita',
                     'fais-moi une recherche web sur les modules Peltier', "tu te souviens de la loi d'Ohm ?"):
            self.assertIsNone(recall.recall_request(text), text)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        events.bus.detach_loop()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_a_subject_comes_out_with_its_date_and_answer(self):
        conversation(('parle-moi de Nikola Tesla', 'Tesla a inventé le moteur à induction.'), at='2026-09-10T08:00:00.000Z')
        conversation(('quel temps fait-il à Paris ?', 'Il fait 18 °C à Paris.'), at='2026-09-12T08:00:00.000Z')
        current = conversation(('bonjour', 'Bonjour !'))
        found = recall.recall_for('on avait parlé de Tesla', conversation_id=current['id'], memories=[])
        self.assertEqual((found['count'], found['searched']), (1, 2))   # the current conversation is left out
        text = recall.answer(found)
        self.assertIn('**1 échange(s)** sur « Tesla »', text)
        self.assertIn('« parle-moi de Nikola Tesla »', text)
        self.assertIn('J’avais répondu : « Tesla a inventé le moteur à induction. »', text)
        self.assertIn('Lus dans la base, pas reconstitués', text)

    def test_an_absent_subject_is_said_absent(self):
        conversation(('parle-moi de Nikola Tesla', 'Tesla…'))
        text = recall.answer(recall.recall_for('on avait parlé de Marie Curie', conversation_id=None, memories=[]))
        self.assertIn('aucune** conversation passée qui parle de « Marie Curie »', text)
        self.assertIn('Je n’en invente pas', text)

    def test_careful_needs_the_subject_in_the_question_and_gives_way_to_a_memory(self):
        conversation(('mets de la musique', 'Voici votre playlist du soir.'))   # « playlist » only in the answer
        self.assertIsNone(recall.recall_for('tu te souviens de ma playlist ?', conversation_id=None, memories=[]))
        conversation(('ajoute ce titre à ma playlist', 'C’est fait.'))
        found = recall.recall_for('tu te souviens de ma playlist ?', conversation_id=None, memories=[])
        self.assertEqual(found['count'], 1)
        memory = {'title': 'ma playlist', 'detail': 'Jazz et lo-fi pour travailler.'}
        self.assertIsNone(recall.recall_for('tu te souviens de ma playlist ?', conversation_id=None, memories=[memory]))

    def test_a_past_recall_request_is_not_an_exchange_and_repeats_collapse(self):
        conversation(('on avait parlé de Tesla', 'Je n’ai rien trouvé.'))
        conversation(('parle-moi de Nikola Tesla', 'Première réponse.'), at='2026-09-10T08:00:00.000Z')
        conversation(('parle-moi de Nikola Tesla', 'Seconde réponse.'), at='2026-09-11T08:00:00.000Z')
        found = recall.recall_for('retrouve notre conversation sur Tesla', conversation_id=None, memories=[])
        self.assertEqual(found['count'], 1)
        self.assertEqual(found['found'][0]['reply']['content'], 'Seconde réponse.')

    def test_a_subject_already_in_this_conversation_is_read_not_recalled(self):
        conversation(('parle-moi de Nikola Tesla', 'Tesla…'))
        current = conversation(('et Tesla, il est né où ?', 'À Smiljan.'))
        self.assertIsNone(recall.recall_for('on avait parlé de Tesla', conversation_id=current['id'], memories=[]))

    def test_athena_answers_are_named_as_hers_and_sensitive_is_carried(self):
        conversation(('combien coûte le module Peltier ?', 'Environ 8 euros.'),
                     meta={'imported_from': 'athena', 'athena_id': 1, 'sensitive': True}, at='2026-09-03T08:00:00.000Z')
        found = recall.recall_for('on avait parlé du module Peltier', conversation_id=None, memories=[])
        text = recall.answer(found)
        self.assertIn('vous aviez demandé à Athéna', text)
        self.assertIn('réponse d’Athéna, non vérifiée par AURORA', text)
        self.assertTrue(found['sensitive'])

    def test_the_list_of_the_last_conversations(self):
        conversation(('parle-moi de Nikola Tesla', 'Tesla…'), at='2026-09-10T08:00:00.000Z')
        conversation(('quel temps fait-il ?', 'Beau.'), meta={'imported_from': 'athena', 'athena_id': 9},
                     at='2026-09-12T08:00:00.000Z')
        current = conversation(('bonjour', 'Bonjour !'))
        listed = recall.recall_for('quelles sont nos dernières conversations ?', conversation_id=current['id'],
                                   memories=[])
        self.assertEqual([c['subjects'][0] for c in listed['conversations']], ['quel temps fait-il ?',
                                                                              'parle-moi de Nikola Tesla'])
        text = recall.answer(listed)
        self.assertIn('Vos 2 dernières conversations', text)
        self.assertIn('échange avec Athéna', text)


class OrchestratorRecallTests(CoreTestCase):
    async def test_on_avait_parle_answers_from_the_base_without_any_model(self):
        conversation(('parle-moi des modules Peltier', 'Un module Peltier refroidit une face et chauffe l’autre.'),
                     at='2026-09-10T08:00:00.000Z')
        orch, _, ollama, _ = build(FakeDecision('memory.search', slots={'query': 'modules Peltier'}))
        result = await orch.handle_request('On avait parlé des modules Peltier la semaine dernière')
        self.assertEqual(result['status'], 'done')
        self.assertIn('**1 échange(s)** sur « modules Peltier »', result['answer'])
        self.assertIn('Un module Peltier refroidit une face', result['answer'])
        self.assertEqual(ollama.calls, [])            # read in the base, never generated
        self.assertTrue(result['verification']['ok'])

    async def test_a_sensitive_exchange_marks_the_answer(self):
        conversation(('le code du portail est 4512', 'Noté.'), meta={'sensitive': True})
        orch, *_ = build(FakeDecision('memory.search', slots={'query': 'code du portail'}))
        result = await orch.handle_request('on avait parlé du code du portail')
        self.assertTrue(result['shows_sensitive'])


if __name__ == '__main__':
    unittest.main()
