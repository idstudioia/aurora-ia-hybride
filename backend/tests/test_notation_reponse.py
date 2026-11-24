"""Mathematical notation in an answer written by a model (conversation bench of 22/11/2026, reviews/banc-modeles,
article sections 6.4 and 7.1, fifth track). No network, no real LLM.

The interface has no library of formulas: « $11,5 \\text{ V}$ » is shown as it is written, and read aloud the same
way. On the bench, 15 written answers out of 532 held such notation, in 103 pieces: 13 answers of gemma4:e4b and 2 of
granite4.2:8b. Every piece was a simple one — a number, a number and its unit, a percentage, a multiplication — and
11 answers right on the facts failed their turn because of it.

Written before the correction, with the pieces as the models wrote them and with others. What is held here:
- a simple piece is written as plain text by the code, and counted;
- a real formula, an amount in dollars, code and a variable of a shell are left as they are;
- nothing is rewritten when the user asks for the notation itself;
- while the answer is written, a piece that is not finished is neither shown nor said aloud.
"""
import re
import unittest

from aurora.core import prompts
from aurora.tasks import runs
from tests.doubles_core import FakeDecision, FakeOllama, build
from tests.test_orchestrator import CoreTestCase

# What the models wrote on the bench (reviews/banc-modeles), and what must be shown instead.
BENCH = {
    'La pompe ne démarre pas si la batterie descend sous $11,5 \\text{ V}$.':
        'La pompe ne démarre pas si la batterie descend sous 11,5 V.',
    'Humidité : au-delà de $35\\%$, la pompe reste arrêtée.': 'Humidité : au-delà de 35%, la pompe reste arrêtée.',
    'Humidité : au-delà de $35 \\%$, la pompe reste arrêtée.': 'Humidité : au-delà de 35 %, la pompe reste arrêtée.',
    'Une serre de $12 \\text{ m}^2$ et une cuve de $300$ litres.': 'Une serre de 12 m² et une cuve de 300 litres.',
    'Un arrosage à $6 \\text{ h } 30$, pendant $12$ minutes.': 'Un arrosage à 6 h 30, pendant 12 minutes.',
    '**Pompe** : **$12 \\text{ V}$**, débit de $240$ litres par heure.': '**Pompe** : **12 V**, débit de 240 litres par heure.',
    'Sous $11,5\\ \\text{V}$, sur $12\\ \\text{m}^2$.': 'Sous 11,5 V, sur 12 m².',
    'Le calcul $2 500 \\times 3$ a été demandé.': 'Le calcul 2 500 × 3 a été demandé.',
    'Le calcul $2\\,500 \\times 3$ a été demandé.': 'Le calcul 2 500 × 3 a été demandé.',
    'Un calcul, \\(2\\,500 \\times 3 = 7\\,500\\), puis la date.': 'Un calcul, 2 500 × 3 = 7 500, puis la date.',
    'Un calcul, \\(2{,}500 \\times 3 = 7{,}500\\), puis la date.': 'Un calcul, 2,500 × 3 = 7,500, puis la date.',
}
# Not seen on the bench, of the same simple kind.
OTHERS = {
    'La chauffe s’arrête à $85\\,^\\circ\\text{C}$.': 'La chauffe s’arrête à 85 °C.',
    'Environ $10^{6}$ octets, soit $3 \\times 10^{-3}\\,\\mathrm{A}$.': 'Environ 10⁶ octets, soit 3 × 10⁻³ A.',
    'Une résistance de $4{,}7\\,\\text{k}\\Omega$ et du $CO_2$.': 'Une résistance de 4,7 kΩ et du CO₂.',
    'Avec $1 \\leq x \\leq 10$, et $E = mc^2$.': 'Avec 1 ≤ x ≤ 10, et E = mc².',
    'Une marge de $\\pm 5\\%$ sur $20 \\text{ W}$.': 'Une marge de ± 5% sur 20 W.',
    'Un condensateur de $10\\,\\mu\\text{F}$, de $-5\\,^\\circ\\text{C}$ à $+40\\,^{\\circ}\\text{C}$.':
        'Un condensateur de 10 µF, de -5 °C à +40 °C.',
    'Entre $5$ et $10$ volts, soit \\[2\\,500 \\times 3 = 7\\,500\\].': 'Entre 5 et 10 volts, soit 2 500 × 3 = 7 500.',
}
# Left as they are: a real formula cannot be written as plain text without changing it, and the rest is not notation.
KEPT = (
    'La moitié s’écrit $\\frac{1}{2}$, et la racine $\\sqrt{2}$.',
    'La somme $\\sum_{i=1}^{n} i$ et l’angle $\\alpha = 3$.',
    'Soit $x^n + 1$ pour tout n.',
    'Cela coûte entre $5 et $10 par mois.',
    'Cela coûte 5 $ ou 10 $ selon l’offre.',
    'Le prix est de 20 $.',
    'Between $20,000 and $30,000 a year.',
    'De 5$ à 10$ par mois.',
    'Tapez echo $HOME et $USER dans le terminal.',
    'La variable `$HOME` vaut `$PATH` sur ce PC, et `$1$` est du code.',
    'En PowerShell, $a=$b copie la valeur.',
    'Dans un motif, $1$2 remet les deux groupes.',
    'Un dollar protégé : \\$5 et \\$7.',
    '```\ntotal = $2 500 \\times 3$\n```',
    'Aucune notation ici : 11,5 V et 35 %.',
    # added after the correction, from a pass of awkward texts: a shell written without backticks, a sentence
    # caught between two dollars
    'En bash, x=$((1+2)) et y=$(date) donnent un nombre et la date.',
    'Ajoutez PATH=$PATH:$HOME/bin puis ${HOME}/x et $1,$2.',
    'Cela coûte $5 par mois, puis 6$.',
    'Tarifs : $5/mois ou $50/an ; $5-$10 ; ($5) ; 5$-10$ ; USD $1,000.00.',
    'Cela coûte $5 ou 6$, voire $5.$',
    'Une regex : ^\\d+$ puis $ en fin de ligne, et \\d{2}$.',
)


class SimpleNotationIsWrittenPlain(unittest.TestCase):
    def test_the_pieces_the_models_wrote_on_the_bench(self):
        for written, shown in BENCH.items():
            with self.subTest(written):
                self.assertEqual(prompts.plain_notation(written)[0], shown)

    def test_other_pieces_of_the_same_kind(self):
        for written, shown in OTHERS.items():
            with self.subTest(written):
                self.assertEqual(prompts.plain_notation(written)[0], shown)

    def test_the_pieces_rewritten_are_counted(self):
        text = 'Une serre de $12 \\text{ m}^2$, une cuve de $300$ litres, et $\\frac{1}{2}$.'
        self.assertEqual(prompts.plain_notation(text), ('Une serre de 12 m², une cuve de 300 litres, et $\\frac{1}{2}$.', 2))

    def test_what_is_not_simple_notation_stays_as_written(self):
        for text in KEPT:
            with self.subTest(text):
                self.assertEqual(prompts.plain_notation(text), (text, 0))
        self.assertEqual(prompts.plain_notation(''), ('', 0))

    def test_nothing_is_rewritten_when_the_notation_itself_is_asked(self):
        text = 'La formule est $E = mc^2$.'
        for question in ('Écris cette formule en LaTeX', 'Donne-moi le code latex de la formule', 'Comment l’écrire en TeX ?'):
            with self.subTest(question):
                self.assertEqual(prompts.plain_notation(text, question), (text, 0))
        self.assertEqual(prompts.plain_notation(text, 'Quelle est la formule d’Einstein ?')[0], 'La formule est E = mc².')


class NotationWhileTheAnswerIsWritten(unittest.TestCase):
    """The text shown live, which the voice may say aloud: a piece that is not finished is held back."""

    def test_a_piece_being_written_is_held_back(self):
        for so_far, shown in (('L’humidité dépasse $', 'L’humidité dépasse'),
                              ('L’humidité dépasse $35', 'L’humidité dépasse'),
                              ('L’humidité dépasse $35\\', 'L’humidité dépasse'),
                              ('L’humidité dépasse $35\\%$ et', 'L’humidité dépasse 35% et'),
                              ('Sous $11,5 \\text{ V', 'Sous'),
                              ('Le calcul \\', 'Le calcul'),
                              ('Le calcul \\(2\\,500 \\times', 'Le calcul'),
                              ('Le calcul \\(2\\,500 \\times 3\\) donne', 'Le calcul 2 500 × 3 donne'),
                              ('Une cuve de $300$ litres. Une pompe de $12 \\text', 'Une cuve de 300 litres. Une pompe de')):
            with self.subTest(so_far):
                self.assertEqual(prompts.plain_notation(so_far, partial=True)[0], shown)

    def test_what_turns_out_not_to_be_a_piece_comes_back(self):
        for so_far in ('Il coûte 5 $.', 'Il coûte 5 $ et', 'Il coûte $5 par mois.\nEnsuite',
                       'La moitié s’écrit $\\frac{1}{2}$', 'La moitié s’écrit $\\frac{1}{2}$ et',
                       'Il coûte $5 par mois, ce qui reste raisonnable pour un abonnement de ce genre, bien moins que'):
            with self.subTest(so_far):
                self.assertEqual(prompts.plain_notation(so_far, partial=True)[0], so_far)

    def test_a_finished_answer_is_never_cut_this_way(self):
        for text in ('L’humidité dépasse $35\\', 'Le prix est de 20 $', 'Le calcul \\(2\\,500 \\times'):
            with self.subTest(text):
                self.assertEqual(prompts.plain_notation(text), (text, 0))


class NotationInTheOrchestrator(CoreTestCase):
    SHEET = 'Fiche : la pompe ne démarre pas si l’humidité dépasse 35 % ou si la batterie descend sous 11,5 V.'
    REPLY = ('La pompe ne démarre pas dans deux cas :\n\n* si l’humidité dépasse $35\\%$ ;\n'
             '* si la batterie descend sous $11,5 \\text{ V}$.')

    async def test_the_answer_shown_and_kept_is_written_plain(self):
        orch, _tools, ollama, _claude = build(FakeDecision('chat.general', method='default', confidence=0.2),
                                              ollama=FakeOllama(reply=self.REPLY))
        result = await orch.handle_request('Dans quels cas la pompe ne démarre-t-elle pas ?',
                                           attachments=[{'name': 'fiche.txt', 'text': self.SHEET}])
        self.assertEqual(result['answer'], 'La pompe ne démarre pas dans deux cas :\n\n* si l’humidité dépasse 35% ;\n'
                                           '* si la batterie descend sous 11,5 V.')
        self.assertTrue(re.search(r'35\s?%', result['answer']) and re.search(r'11[,.]5\s?V', result['answer']))
        step = next(s for s in result['steps'] if s['stage'] == 'local_ai')
        self.assertEqual(runs.step_result(step).get('notation_rewritten'), 2)   # said in the journal of the request
        self.assertTrue(result['verification']['ok'], result['verification'])
        # the next turn of the conversation does not show the notation to the model either
        await orch.handle_request('Et sinon ?', conversation_id=result['conversation_id'])
        self.assertNotIn('\\text', ollama.calls[-1]['messages'][0]['content'])

    async def test_an_answer_without_notation_is_not_noted(self):
        orch, *_ = build(FakeDecision('chat.general', method='default', confidence=0.2),
                         ollama=FakeOllama(reply='Elle ne démarre pas au-dessus de 35 % d’humidité.'))
        result = await orch.handle_request('Dans quels cas la pompe ne démarre-t-elle pas ?')
        step = next(s for s in result['steps'] if s['stage'] == 'local_ai')
        self.assertNotIn('notation_rewritten', runs.step_result(step))


if __name__ == '__main__':
    unittest.main()
