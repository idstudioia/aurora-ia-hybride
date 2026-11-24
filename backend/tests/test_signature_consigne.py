"""The line of the instructions that small models copied at the end of their answers (conversation bench of
22/11/2026, reviews/banc-modeles): « Cette réponse est produite par : IA locale Ollama, modèle gemma4:e2b. »
39 written answers out of 126 carried it or recited a rule. No network, no real LLM.

The first tests written here expected a new wording of the line to stop the copy. The wording trial of the same day
(other questions than the bench, four models, six wordings) said otherwise, and they were rewritten after it:
- gemma4:e2b copies the line whatever it says and wherever it stands (8 answers out of 8);
- « ne signe pas » made granite4.2:8b recite it (1 → 7 answers out of 8);
- saying it inside another line stops most copies, but gemma4:e2b then forgets it speaks as AURORA.
So the line stays as it is, which keeps its copy easy to recognise, and the code takes the copy off.

What is held here:
- the model still learns which engine it is (rule 8, 08/11/2026: the local model claimed code written by Claude);
- rule 8 names a line that really exists in the measured state;
- the line of the state, copied under or above an answer, is taken off by the code, and nothing else is.
"""
import re
import unittest

from aurora.core import capabilities, prompts

CAPS = {'internet': True, 'mode': 'equilibre',
        'ollama': {'up': True, 'models': ['gemma4:e2b', 'bge-m3'], 'chat_model': 'gemma4:e2b',
                   'embed_model': 'bge-m3', 'embed_ready': True},
        'cloud': {'configured': False, 'provider': 'anthropic', 'tiers': {}}}
LOCAL = 'IA locale Ollama, modèle gemma4:e2b'
CLAUDE = 'Claude (Anthropic), palier standard, modèle claude-sonnet-5-5'
COPIED = 'Cette réponse est produite par'


class EngineLine(unittest.TestCase):
    @staticmethod
    def line(label):
        """The line of the measured state that names the engine, as the model reads it."""
        return next((l for l in capabilities.describe(CAPS, engine_label=label).splitlines() if label in l), '')

    def test_the_model_still_learns_which_engine_it_is(self):
        for label in (LOCAL, CLAUDE):
            self.assertTrue(self.line(label), label)
            for text in (prompts.system_prompt(CAPS, engine_label=label),
                         '\n'.join(prompts.system_prompt_parts(CAPS, engine_label=label))):
                self.assertEqual(text.count(label), 1, label)

    def test_without_an_engine_there_is_no_such_line(self):
        self.assertNotIn(COPIED, capabilities.describe(CAPS))   # the page of the capabilities: nobody is writing

    def test_rule_8_names_a_line_that_exists(self):
        rule = next(l for l in prompts.RULES.splitlines() if l.startswith('8.'))
        named = re.findall(r'« ([^»]+) »', rule)
        self.assertTrue(named, rule)
        for name in named:
            self.assertIn(name, self.line(LOCAL), name)
        self.assertIn('ne t’attribue jamais une réponse rédigée par un autre', rule)   # 08/11/2026, kept

    def test_the_line_copied_around_an_answer_is_taken_off(self):
        """The line and its removal go together: reword the line, and this fails until the code follows."""
        for label in (LOCAL, CLAUDE):
            copied = self.line(label).lstrip('- ').strip()
            self.assertIn(COPIED, copied)
            for text in (f'{CONTENT}\n\n{copied}', f'{copied}\n\n{CONTENT}', f'{CONTENT} {copied}',
                         f'{CONTENT}\n\n{copied.rstrip(".")}', f'{CONTENT}\n\n*{copied}*'):
                self.assertEqual(prompts.unsigned(text), (CONTENT, 1), text)

    def test_the_instructions_do_not_ask_not_to_sign(self):
        """Wording trial of 22/11/2026: with « Ne signe pas ta réponse… » in rule 8, granite4.2:8b put the line on
        top of 7 answers out of 8 (1 without it). What a small model is told not to write, it writes."""
        for label in (LOCAL, CLAUDE):
            self.assertNotRegex(prompts.system_prompt(CAPS, engine_label=label), r'(?i)\bne signe')


CONTENT = 'Le vent fait tourner les pales, qui entraînent un générateur.'
# Signatures as the models really wrote them (reviews/banc-modeles, and the wording trial of the same day).
SIGNED = (
    f'{CONTENT}\n\nCette réponse est produite par : IA locale Ollama, modèle gemma4:e2b.',
    f'Cette réponse est produite par : IA locale Ollama, modèle granite4.2:8b.\n\n{CONTENT}',
    f'{CONTENT} Cette réponse est produite par : IA locale Ollama, modèle granite4.2:8b.',
    f'{CONTENT}\n\n*(Réponse produite par l’IA locale Ollama, modèle granite4.2:8b.)*',
    f'{CONTENT}\n\n> Cette réponse est rédigée par l’IA locale (Ollama, modèle granite4.2:8b).',
    f'{CONTENT}\n\n**Cette réponse est produite par :** IA locale Ollama, modèle qwen3.5:9b',
    f'{CONTENT}\n\nCette réponse est produite par l\'IA locale (Ollama), modèle qwen3.5:9b.',
    f'Réponse rédigée par l’IA locale, modèle granite4.2:8b.\n\n{CONTENT}',
    f'{CONTENT} La réponse est produite par l’IA locale Ollama, modèle granite4.2:8b.',
    f'{CONTENT}\n\n***\n\nCette réponse est produite par : IA locale Ollama, modèle gemma4:e4b.',
    f'{CONTENT}\n\nCette réponse est produite par : Claude (Anthropic), palier standard, modèle claude-sonnet-5-5.',
    f'{CONTENT}\n\nCes réponses sont produites par : IA locale Ollama, modèle gemma4:e4b.',
    f'{CONTENT}\n\n*Ce résumé est produit par l’IA locale Ollama, modèle granite4.2:8b.*',
)


class SignatureRemovedByTheCode(unittest.TestCase):
    """Whatever the wording of the instructions, gemma4:e2b copied the line (8 answers out of 8 in the trial): the
    code takes the signature off, and only it."""

    def test_a_signature_is_taken_off_and_the_answer_kept(self):
        for text in SIGNED:
            cleaned, removed = prompts.unsigned(text)
            self.assertEqual((cleaned, removed), (CONTENT, 1), text)

    def test_an_engine_cited_as_a_source_is_taken_off(self):
        text = ('Les cellules captent l’énergie des photons du soleil [IA locale Ollama, modèle gemma4:e4b]. '
                'Cette absorption libère des électrons.')
        cleaned, removed = prompts.unsigned(text)
        self.assertEqual(cleaned, 'Les cellules captent l’énergie des photons du soleil. Cette absorption libère des électrons.')
        self.assertEqual(removed, 1)

    def test_two_signatures_are_counted(self):
        text = f'Cette réponse est produite par : IA locale Ollama, modèle gemma4:e2b.\n\n{CONTENT}\n\n{SIGNED[3].split(chr(10))[-1]}'
        self.assertEqual(prompts.unsigned(text), (CONTENT, 2))

    def test_what_is_not_a_signature_stays(self):
        for text in (
                CONTENT,
                # the signature is the whole answer: it is what was asked
                'Cette réponse est produite par l’IA locale Ollama, modèle qwen3.5:9b.',
                # who AURORA is, in a sentence: an answer, not a footer
                'Je suis AURORA, votre assistant personnel, qui utilise des outils réels, l’IA locale Ollama (modèle '
                'granite4.2:8b) et, si vous l’autorisez, Claude (Anthropic).',
                # other people write answers too
                f'{CONTENT}\n\nCette réponse a été rédigée par le service client du fournisseur.',
                f'{CONTENT}\n\nCe résumé a été produit par le cabinet d’audit en mars.',
                'La lettre a été rédigée par Claude Bernard en 1865.',
                'Cette réponse est produite par un onduleur : il transforme le courant continu en courant alternatif.',
                # a sentence that goes on after the engine is content
                'Cette réponse est rédigée par l’IA locale à partir des seules mesures de vos outils : le processeur est à '
                '12 % et la mémoire à 41 %, rien d’anormal.',
                # code is never touched
                f'{CONTENT}\n\n```python\n# Cette réponse est produite par : IA locale Ollama, modèle gemma4:e2b.\nprint(1)\n```',
        ):
            self.assertEqual(prompts.unsigned(text), (text, 0), text)

    def test_nothing_is_taken_off_when_the_question_is_who_answers(self):
        text = f'Je suis AURORA.\n\nCette réponse est produite par : IA locale Ollama, modèle gemma4:e2b.'
        for question in ('Quel modèle d’IA es-tu ?', 'C’est quel modèle qui me répond en ce moment ?', 'Qui es-tu ?',
                         'Qui a rédigé ta dernière réponse ?', 'Tu tournes sur Ollama ou sur Claude ?'):
            self.assertEqual(prompts.unsigned(text, question), (text, 0), question)
        for question in ('Comment fonctionne une éolienne ?', 'Résume ce document en cinq points.', ''):
            self.assertEqual(prompts.unsigned(text, question), ('Je suis AURORA.', 1), question)


class SignatureWhileTheAnswerIsWritten(unittest.TestCase):
    """The answer is shown, and may be said aloud, while the model writes it (`llm.delta`): there, what may become a
    signature is held back until it is one (taken off) or is not (shown)."""

    def test_a_signature_being_written_is_held_back(self):
        for begun in ('Cette réponse est produite par', 'Cette réponse est produite par : IA',
                      'Cette réponse est produite par : IA locale Ollama, modèle qw', '*(Réponse produite par l’IA'):
            self.assertEqual(prompts.unsigned(f'{CONTENT}\n\n{begun}', partial=True)[0], CONTENT, begun)
            self.assertEqual(prompts.unsigned(f'{CONTENT} {begun}', partial=True)[0], CONTENT, begun)
        # on top of the answer: nothing is shown yet, rather than the signature
        self.assertEqual(prompts.unsigned('Cette réponse est produite par : IA locale Ollama, modèle qwen3.5:9b.',
                                          partial=True)[0], '')

    def test_a_sentence_that_is_not_a_signature_comes_back(self):
        text = (f'{CONTENT}\n\nCette réponse est produite par un onduleur : il transforme le courant continu des '
                'panneaux en courant alternatif')
        self.assertEqual(prompts.unsigned(text, partial=True), (text, 0))

    def test_a_finished_answer_is_never_cut_this_way(self):
        text = f'{CONTENT}\n\nCette réponse est produite par un onduleur.'
        self.assertEqual(prompts.unsigned(text), (text, 0))
        self.assertEqual(prompts.unsigned(f'{CONTENT}\n\nCette réponse est produite par : IA'), (f'{CONTENT}\n\nCette réponse est produite par : IA', 0))


class SignatureInTheOrchestrator(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.doubles_core import TempEnv
        self.env = TempEnv().__enter__()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    async def test_the_answer_shown_and_kept_has_no_signature(self):
        from tests.doubles_core import FakeDecision, FakeOllama, build
        reply = f'{CONTENT}\n\nCette réponse est produite par : IA locale Ollama, modèle qwen3.5:9b.'
        orch, _tools, ollama, _claude = build(FakeDecision('chat.general', method='default', confidence=0.2),
                                              ollama=FakeOllama(reply=reply))
        result = await orch.handle_request('Comment fonctionne une éolienne ?')
        self.assertEqual(len(ollama.calls), 1)
        self.assertIn(CONTENT, result['answer'])
        self.assertNotIn('produite par', result['answer'])
        from aurora.tasks import runs
        step = next(s for s in result['steps'] if s['stage'] == 'local_ai')
        self.assertEqual(runs.step_result(step).get('signature_removed'), 1)   # said in the journal of the request
        # the next turn of the conversation does not show it to the model either
        await orch.handle_request('Et quand il n’y a pas de vent ?', conversation_id=result['conversation_id'])
        self.assertNotIn('Cette réponse est produite par', ollama.calls[-1]['messages'][0]['content'])

    async def test_asked_who_answers_the_model_s_words_stay(self):
        from tests.doubles_core import FakeDecision, FakeOllama, build
        reply = 'Je suis AURORA.\n\nCette réponse est produite par : IA locale Ollama, modèle qwen3.5:9b.'
        orch, _tools, _ollama, _claude = build(FakeDecision('chat.general', method='default', confidence=0.2),
                                               ollama=FakeOllama(reply=reply))
        result = await orch.handle_request('Quel modèle d’IA es-tu ?')
        self.assertIn('modèle qwen3.5:9b', result['answer'])


if __name__ == '__main__':
    unittest.main()
