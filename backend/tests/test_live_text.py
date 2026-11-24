"""B4 (reviews/etapes-restantes.md, M3 of the report): the answer shown word by word while the model writes it.

The orchestrator passes an `on_token` callback to the provider; the text written so far goes to the interface as
`llm.delta` events sent live and never stored (events.broadcast_only). Off when a check is set to « bloquer »
(A4: such an answer is shown only once verified) and for a plan (JSON). Temporary AURORA_HOME (TempEnv).
"""
import logging
from unittest import mock

from aurora import config
from aurora.core import orchestrator
from aurora.tasks import events
from tests.doubles_core import FakeDecision, FakeOllama, build
from tests.test_orchestrator import CoreTestCase

REPLY = 'Il était une fois un petit robot qui comptait les étoiles.'


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


class LiveTextTests(CoreTestCase):
    async def deltas(self, intent: str = 'chat.general', text: str = 'Raconte-moi une histoire',
                     reply: str = REPLY) -> tuple[dict, list]:
        queue = events.bus.subscribe()
        self.addCleanup(events.bus.unsubscribe, queue)
        orch, *_ = build(FakeDecision(intent), ollama=FakeOllama(reply=reply))
        with mock.patch.object(orchestrator, 'LIVE_TEXT_EVERY_S', 0):   # every piece, not every 100 ms
            result = await orch.handle_request(text)
        seen = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        return result, [e for e in seen if e['kind'] == 'llm.delta']

    async def test_the_text_arrives_piece_by_piece_and_is_never_stored(self):
        result, deltas = await self.deltas()
        self.assertEqual(result['answer'], REPLY)
        texts = [e['payload']['text'] for e in deltas]
        self.assertGreater(len(texts), 5)
        self.assertEqual(texts[-1], REPLY)
        self.assertTrue(all(REPLY.startswith(t) for t in texts))                 # always the text so far
        self.assertEqual(texts, sorted(texts, key=len))
        self.assertTrue(all(e['transient'] and e['run_id'] == result['task_id'] for e in deltas))
        stored = events.list_events(limit=500, run_id=result['task_id'])
        self.assertNotIn('llm.delta', {e['kind'] for e in stored})              # no journal line per word

    async def test_no_live_text_when_a_check_blocks_or_for_a_plan(self):
        config.update_settings({'verify_mods': {'sources': 'bloquer'}})
        _, deltas = await self.deltas()
        self.assertEqual(deltas, [])                                            # shown only once verified
        config.update_settings({'verify_mods': {'sources': 'alerter'}})
        _, plan = await self.deltas('task.plan', 'Prépare un plan pour ranger mon bureau')
        self.assertEqual(plan, [])                                              # a plan is JSON, not prose

    async def test_the_text_may_be_spoken_while_written_unless_it_may_show_a_secret(self):
        # 11/11/2026 (ID Studio IA : la voix d'Athéna « au fil de l'écriture »): the interface may say the text aloud before
        # the answer is finished, only when the service says so; never for an answer that may show a sensitive memory.
        _, deltas = await self.deltas()
        self.assertTrue(deltas and all(e['payload']['speak'] is True for e in deltas))
        with mock.patch.object(orchestrator.Orchestrator, '_shows_sensitive', return_value=True):
            _, deltas = await self.deltas(text='Raconte-moi une autre histoire')
        self.assertTrue(deltas and all(e['payload']['speak'] is False for e in deltas))

    async def test_a_copied_signature_is_neither_shown_nor_said_while_written(self):
        # 22/11/2026 (reviews/banc-modeles): small models copy the line of their instructions that names the engine.
        # The code takes it off the answer (core/prompts.unsigned, tests/test_signature_consigne.py), and off the
        # text shown while it is written, which the voice may say aloud.
        signature = 'Cette réponse est produite par : IA locale Ollama, modèle qwen3.5:9b.'
        for reply in (f'{REPLY}\n\n{signature}', f'{signature}\n\n{REPLY}', f'{REPLY} {signature}'):
            result, deltas = await self.deltas(reply=reply)
            texts = [e['payload']['text'] for e in deltas]
            self.assertEqual(result['answer'], REPLY, reply)
            self.assertGreater(len(texts), 5)
            self.assertEqual(texts[-1], REPLY, reply)
            for text in texts:
                self.assertNotIn('produite par', text, reply)

    async def test_simple_mathematical_notation_is_shown_plain_while_written(self):
        # 22/11/2026 (reviews/banc-modeles): « $11,5 \text{ V}$ » is written « 11,5 V » by the code
        # (core/prompts.plain_notation, tests/test_notation_reponse.py), in the answer and in the text shown while it
        # is written, which the voice may say aloud: a piece that is not finished is not shown yet.
        reply = 'La pompe reste arrêtée au-delà de $35\\%$ d’humidité, ou sous $11,5 \\text{ V}$ de batterie.'
        plain = 'La pompe reste arrêtée au-delà de 35% d’humidité, ou sous 11,5 V de batterie.'
        result, deltas = await self.deltas(reply=reply)
        texts = [e['payload']['text'] for e in deltas]
        self.assertEqual(result['answer'], plain)
        self.assertGreater(len(texts), 5)
        self.assertEqual(texts[-1], plain)
        for text in texts:
            self.assertNotIn('$', text)
            self.assertNotIn('\\', text)

    def test_a_live_frame_has_no_id(self):
        from aurora import main
        frame = main._sse_frame({'id': 0, 'transient': True, 'kind': 'llm.delta', 'payload': {'text': 'Il'}})
        self.assertNotIn('\nid:', '\n' + frame)                                  # Last-Event-ID keeps the real one
        self.assertIn('id: 42', main._sse_frame({'id': 42, 'kind': 'run.done', 'payload': {}}))
