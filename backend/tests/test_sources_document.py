"""The « sources » check under an answer about a joined document (conversation bench of 22/11/2026,
reviews/banc-modeles, article sections 6.2 and 7.1, fourth track). No network, no real LLM.

The check only counted the numbered sources of a web search. Asked in which cases the pump of a joined sheet does not
start, models cited the sheet they had been given: « [6] » and « [7] » for its points 6 and 7, or « [1] » for the
sheet itself, twice with « [1] fiche-pompe-halo.txt » written under the answer. AURORA then added « Référence(s) [6],
[7] sans source correspondante » under a right answer: 10 of the 17 answers the check reported on the bench.

Written before the correction, with another document than the bench's. What is held here:
- a reference to a point the joined document numbers itself, or to the document by its rank, has its source;
- what the document does not carry stays reported, and so does any [n] of an answer with nothing joined (the four
  « [1] » of the bench written with no source at all were reported rightly);
- the numbered sources of a web search are counted as before;
- only what a model read to write the answer counts.
"""
import unittest

from tests.doubles_core import FakeDecision, FakeOllama, build
from tests.test_documents import long_text, reader
from tests.test_orchestrator import CoreTestCase
from tests.test_verify_mods import checked

NOTICE = """Notice — chauffe-eau Solis (version du 2 mars 2026)
1. Cuve : 150 litres, en acier émaillé.
2. Résistance : 1 800 W, protégée par un thermostat réglé à 55 °C.
3. Sécurité : la chauffe s'arrête si la température dépasse 85 °C.
4) Entretien : vidanger la cuve tous les 24 mois.
12 V suffisent à la carte de commande.
6 h 30 : début de l'heure creuse du matin.
3,5 m de câble sont fournis."""
PLAIN = 'Compte rendu du 4 mars. La chauffe a été coupée deux fois. Le thermostat sera remplacé.'
CITING = 'Selon Martin [14], la corrosion vient du calcaire. Une autre étude dit le contraire.'
WEB = {'status': 'done', 'stage': 'tool', 'tool': 'web.search', 'idx': 0, 'requires_permission': False,
       'permission_id': None,
       'result': {'ok': True, 'provenance': 'searxng', 'data': {'results': [
           {'title': 'Chauffe-eau', 'url': 'https://exemple.test/a', 'text': 'Un chauffe-eau chauffe l’eau.'},
           {'title': 'Thermostat', 'url': 'https://exemple.test/b', 'text': 'Un thermostat coupe la chauffe.'}]}}}


def sources(answer: str, documents=(), steps=()) -> dict:
    """The « sources » check of an answer written by a model, with the documents it read."""
    return next(c for c in checked(answer, steps, documents=documents)['checks'] if c['name'] == 'sources')


class ReferencesToAJoinedDocument(unittest.TestCase):
    def test_a_point_the_document_numbers_is_a_source(self):
        for answer in ('La chauffe s’arrête si la température dépasse 85 °C [3].',
                       '1.  Si la température **dépasse 85 °C** [3].\n2.  La cuve se vidange tous les 24 mois. [4]',
                       'La cuve fait 150 litres [1] et la résistance 1 800 W [2].'):
            with self.subTest(answer):
                self.assertTrue(sources(answer, [NOTICE])['ok'])

    def test_the_document_itself_is_cited_by_its_rank(self):
        """The bench: « … dans les cas suivants [1] » and, under the answer, « [1] fiche-pompe-halo.txt »."""
        self.assertTrue(sources('La chauffe a été coupée deux fois [1].\n\n[1] compte-rendu.txt', [PLAIN])['ok'])
        self.assertTrue(sources('Le compte rendu [1] et la notice [2] disent la même chose.', [PLAIN, PLAIN])['ok'])

    def test_a_mark_the_document_writes_between_brackets_is_a_source(self):
        self.assertTrue(sources('D’après le document, la corrosion vient du calcaire [14].', [CITING])['ok'])

    def test_what_the_document_does_not_carry_stays_reported(self):
        for answer, documents, orphan in (
                ('La garantie est de cinq ans [7].', [NOTICE], '[7]'),            # the notice has no point 7
                ('La carte de commande est décrite au point [12].', [NOTICE], '[12]'),   # « 12 V » is not a point
                ('L’heure creuse est citée en [6].', [NOTICE], '[6]'),            # « 6 h 30 » neither
                ('Le thermostat sera remplacé [2].', [PLAIN], '[2]'),             # one document, nothing numbered
                ('Une autre étude dit le contraire [15].', [CITING], '[15]')):
            with self.subTest(answer):
                check = sources(answer, documents)
                self.assertFalse(check['ok'])
                self.assertIn(f'{orphan} sans source correspondante', check['detail'])

    def test_one_reference_out_of_two_is_enough_to_report(self):
        check = sources('La chauffe s’arrête à 85 °C [3], et la garantie est de cinq ans [9].', [NOTICE])
        self.assertFalse(check['ok'])
        self.assertIn('[9]', check['detail'])
        self.assertNotIn('[3]', check['detail'])

    def test_with_nothing_joined_nothing_changes(self):
        """The four « [1] » of the bench in an answer with no source at all were reported rightly."""
        said = 'Un panneau ne produit pas la nuit, faute de lumière [1].'
        for documents in ((), [], [''], ['   \n']):          # a document without text gave the model nothing to cite
            with self.subTest(documents=documents):
                self.assertFalse(sources(said, documents)['ok'])
        self.assertEqual(sources('Un panneau ne produit pas la nuit.')['detail'], 'Aucune référence citée.')

    def test_the_sources_of_a_search_are_counted_as_before(self):
        self.assertTrue(sources('Il chauffe l’eau [1], et le thermostat coupe [2].', steps=[WEB])['ok'])
        self.assertEqual(sources('Il chauffe l’eau [1].', steps=[WEB])['detail'],
                         '1 référence(s), toutes vers une source fournie.')
        self.assertFalse(sources('La garantie est de cinq ans [3].', steps=[WEB])['ok'])
        self.assertTrue(sources('La chauffe s’arrête à 85 °C [3], et il chauffe l’eau [1].', [NOTICE], [WEB])['ok'])

    def test_the_detail_says_which_references_are_in_the_document(self):
        detail = sources('La chauffe s’arrête à 85 °C [3], la cuve se vidange [4].', [NOTICE])['detail']
        self.assertIn('2 référence(s)', detail)
        self.assertIn('[3], [4]', detail)
        self.assertIn('document joint', detail)


class AnswersAboutAJoinedDocument(CoreTestCase):
    """Through the orchestrator: the check receives the documents as far as a model read them for this answer."""

    async def ask(self, reply, attachments: list | None = None, question: str = 'Dans quels cas la chauffe s’arrête-t-elle ?') -> dict:
        orch, _tools, _ollama, _claude = build(FakeDecision('chat.general', method='default', confidence=0.2),
                                               ollama=FakeOllama(reply=reply))
        return await orch.handle_request(question, attachments=attachments)

    async def long(self, said: str, fail: tuple[int, ...] = ()) -> str:
        """The answer about a long report whose last line is its point 7, the parts of `fail` not read."""
        report = long_text() + '7. Résiliation : préavis de trois mois.\n'
        result = await self.ask(reader(fail=fail, final=said), [{'name': 'rapport.pdf', 'text': report}],
                                'Quel est le préavis ?')
        return result['answer']

    async def test_a_right_answer_about_a_joined_document_gets_no_warning(self):
        reply = 'La chauffe s’arrête si la température dépasse 85 °C [3].'
        result = await self.ask(reply, [{'name': 'notice-solis.txt', 'text': NOTICE}])
        self.assertEqual(result['decision']['intent'], 'doc.analyze')
        checks = {c['name']: c for c in result['verification']['checks']}
        self.assertTrue(checks['sources']['ok'], checks['sources'])
        self.assertIn('document joint', checks['sources']['detail'])
        self.assertTrue(result['verification']['ok'], result['verification'])
        self.assertEqual(result['answer'], reply)                      # nothing added under the answer

    async def test_a_reference_the_document_does_not_carry_is_still_reported(self):
        result = await self.ask('La garantie est de cinq ans [7].', [{'name': 'notice-solis.txt', 'text': NOTICE}])
        self.assertIn('Référence(s) [7] sans source correspondante', result['answer'])

    async def test_the_same_reference_with_nothing_joined_is_still_reported(self):
        result = await self.ask('La chauffe s’arrête si la température dépasse 85 °C [3].')
        self.assertEqual(result['decision']['intent'], 'chat.general')
        self.assertIn('Référence(s) [3] sans source correspondante', result['answer'])

    async def test_a_long_document_counts_for_the_parts_that_were_read(self):
        said = 'Le préavis est de trois mois [7].'
        self.assertNotIn('sans source correspondante', await self.long(said))   # read whole, part after part
        for fail in ((3,), (1, 2, 3)):                  # its last part not read; then nothing but its beginning
            with self.subTest(fail=fail):
                self.assertIn('Référence(s) [7] sans source correspondante', await self.long(said, fail))

    def test_no_answer_about_the_documents_no_document_counts(self):
        """The agent is told to write no reference between brackets (core/agent.py): its [n] stay reported."""
        from aurora.core.orchestrator import Orchestrator

        def state(purpose: str, status: str = 'done') -> dict:
            return {'attachments': [{'name': 'notice-solis.txt', 'text': NOTICE}],
                    'steps': [{'stage': 'local_ai', 'status': status, 'spec': {'purpose': purpose}, 'result': {}}]}
        self.assertEqual(Orchestrator._documents_read(state('doc')), [NOTICE])
        for other in (state('agent'), state('chat'), state('doc', 'error'), {'attachments': [], 'steps': []}):
            with self.subTest(other=other):
                self.assertEqual(Orchestrator._documents_read(other), [])


if __name__ == '__main__':
    unittest.main()
