"""Cadre Contexte du Chat (29/10/2026): the model's working memory used by the last AI call of a run.

The total is measured by the provider (Ollama prompt_eval_count, Claude usage), the size of the model's context
comes from the provider (/api/ps, Anthropic models API), the parts are pro rata of their length.
"""
import asyncio
import json
import logging
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import httpx

from aurora.memory import store
from aurora.providers.anthropic_provider import AnthropicProvider
from aurora.providers.base import ProviderResult
from aurora.providers.ollama import OllamaProvider
from tests.doubles_core import FakeClaude, FakeDecision, FakeOllama, FakeTools, build, snapshot_response
from tests.test_orchestrator import CoreTestCase


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def part(context: dict, key: str) -> dict | None:
    return next((p for p in context['parts'] if p['key'] == key), None)


class CachedClaude(FakeClaude):
    async def chat(self, messages, **kwargs):
        result = await super().chat(messages, **kwargs)
        return ProviderResult(**{**result.__dict__, 'cache_read_tokens': 900, 'cache_write_tokens': 30})


class UnloadedOllama(FakeOllama):
    async def context_window(self, model):
        return None


class ContextUsageTests(CoreTestCase):
    async def test_a_local_answer_carries_its_measured_context(self):
        store.add_memory('Relativité restreinte', 'Notes sur Einstein et la relativité.', 'science', auto_link=False)
        orch, *_ = build(FakeDecision('chat.general'))
        first = await orch.handle_request('Explique la relativité')
        result = await orch.handle_request('Et la relativité générale ?', conversation_id=first['conversation_id'])
        ctx = result['context']
        self.assertEqual((ctx['provider'], ctx['model'], ctx['input_tokens']), ('ollama', 'qwen3.5:9b', 11))
        self.assertEqual((ctx['window'], ctx['window_source']), (8192, 'ollama_ps'))
        self.assertEqual(sum(p['tokens'] for p in ctx['parts']), 11)   # the parts share the measured total
        self.assertEqual(part(ctx, 'question')['chars'], len('Et la relativité générale ?'))
        self.assertEqual(part(ctx, 'question')['label'], 'Votre demande')
        self.assertTrue(part(ctx, 'system')['chars'] > 0)
        self.assertTrue(part(ctx, 'memory') and part(ctx, 'conversation'))   # memories, then the earlier turn
        self.assertIsNone(part(ctx, 'tools'))
        # The same frame after a reload: kept in the meta of the answer.
        messages = store.recent_messages(result['conversation_id'], 10)
        meta = json.loads([m for m in messages if m['run_id'] == result['task_id'] and m['role'] == 'assistant'][0]
                          ['meta_json'])
        self.assertEqual(meta['context'], ctx)

    async def test_no_model_no_context(self):
        tools = FakeTools({'system.snapshot': snapshot_response})
        orch, *_ = build(FakeDecision('system.status', slots={'metric': 'ram'}), tools=tools)
        result = await orch.handle_request('Quelle quantité de RAM est utilisée ?')
        self.assertIsNone(result['context'])

    async def test_an_unknown_size_is_said_not_guessed(self):
        orch, *_ = build(FakeDecision('chat.general'), ollama=UnloadedOllama())
        ctx = (await orch.handle_request('Bonjour'))['context']
        self.assertEqual((ctx['window'], ctx['window_source'], ctx['input_tokens']), (None, None, 11))
        self.assertIsNone(part(ctx, 'results'))   # no tool ran: AURORA's headings count with the instructions

    async def test_the_agent_counts_its_tool_list(self):
        reply = json.dumps({'todo': [], 'reason': '', 'action': 'answer', 'params': {}, 'answer': 'Bonjour.'})
        orch, *_ = build(FakeDecision('agent.run'), ollama=FakeOllama(reply=reply))
        ctx = (await orch.handle_request('Agent : dis bonjour'))['context']
        self.assertTrue(part(ctx, 'tools')['chars'] > 500)

    async def test_claude_counts_the_cache_and_gives_its_size(self):
        self.mode('equilibre')
        orch, *_ = build(FakeDecision('chat.general'), claude=CachedClaude(configured=True))
        waiting = await orch.handle_request('Explique la relativité', options={'engine': 'cloud'})
        self.assertIsNone(waiting['context'])   # nothing sent yet
        done = await orch.decide_permission(waiting['permission']['id'], 'grant')
        ctx = done['context']
        self.assertEqual((ctx['provider'], ctx['input_tokens']), ('anthropic', 120 + 900 + 30))
        self.assertEqual((ctx['window'], ctx['window_source']), (1_000_000, 'anthropic_models'))


class ProviderWindowTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict('os.environ', {'AURORA_OFFLINE': '1'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_ollama_reads_the_size_of_the_loaded_model(self):
        seen = []

        def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={'models': [{'name': 'qwen3.5:4b', 'model': 'qwen3.5:4b',
                                                         'context_length': 8192}]})
        provider = OllamaProvider(transport=httpx.MockTransport(handler))
        self.assertEqual(asyncio.run(provider.context_window('qwen3.5:4b')), 8192)
        self.assertEqual(seen, ['/api/ps'])
        self.assertIsNone(asyncio.run(provider.context_window('gemma4:e4b')))   # not loaded: unknown
        broken = OllamaProvider(transport=httpx.MockTransport(lambda r: httpx.Response(500, text='erreur')))
        self.assertIsNone(asyncio.run(broken.context_window('qwen3.5:4b')))
        silent = OllamaProvider(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={'models': [{'name': 'qwen3.5:4b'}]})))
        self.assertIsNone(asyncio.run(silent.context_window('qwen3.5:4b')))   # an older Ollama says nothing

    def test_claude_asks_the_models_api_once_per_model(self):
        calls = []

        async def retrieve(model):
            calls.append(model)
            return NS(id=model, max_input_tokens=1_000_000)
        provider = AnthropicProvider(NS(models=NS(retrieve=retrieve)), budget_guard=False)
        self.assertEqual(asyncio.run(provider.context_window('claude-sonnet-5-5')), 1_000_000)
        self.assertEqual(asyncio.run(provider.context_window('claude-sonnet-5-5')), 1_000_000)
        self.assertEqual(calls, ['claude-sonnet-5-5'])

        async def failing(model):
            raise RuntimeError('réseau')
        down = AnthropicProvider(NS(models=NS(retrieve=failing)), budget_guard=False)
        self.assertIsNone(asyncio.run(down.context_window('claude-sonnet-5-5')))


if __name__ == '__main__':
    unittest.main()
