"""Providers: pricing, Ollama (httpx.MockTransport) and Claude (fake SDK client). No network by default.

Live checks against the local Ollama run only with AURORA_LIVE_TESTS=1.
"""
import asyncio
import json
import logging
import os
import time
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import httpx

from aurora.providers import pricing
from aurora.providers.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from aurora.providers.base import ProviderError, ProviderResult
from aurora.providers.ollama import OllamaProvider

SECRET = 'sk-ant-api03-SECRET-never-shown-0123456789'


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def tearDownModule():
    logging.getLogger('aurora').setLevel(logging.NOTSET)


def run(coro):
    return asyncio.run(coro)


class PricingTests(unittest.TestCase):
    def test_list_prices(self):
        self.assertAlmostEqual(pricing.cost_usd('claude-opus-5-5', 1_000_000, 1_000_000), 24.0)
        self.assertAlmostEqual(pricing.cost_usd('claude-fable-5-1', 1_000_000, 0, 1_000_000, 1_000_000),
                               10 + 0.25 + 12.5)
        self.assertAlmostEqual(pricing.cost_usd('claude-sonnet-5-5', 0, 1_000_000), 10.0)
        self.assertAlmostEqual(pricing.cost_usd('claude-haiku-5-5', 1000, 1000, 1000, 1000),
                               (0.10 + 0.50 + 0.01 + 0.125) / 1000, places=9)

    def test_haiku_long_prompt_rate_card(self):
        short = pricing.cost_breakdown('claude-haiku-5-5', 100_000, 0)
        long = pricing.cost_breakdown('claude-haiku-5-5', 100_001, 1_000_000)
        self.assertAlmostEqual(short['input'], 0.01)
        self.assertAlmostEqual(long['output'], 2.50)
        self.assertAlmostEqual(long['input'], 100_001 * 0.50 / 1e6)
        # cache tokens count toward the prompt length
        mixed = pricing.cost_breakdown('claude-haiku-5-5', 10, 0, cache_read=100_000)
        self.assertAlmostEqual(mixed['cache_read'], 100_000 * 0.05 / 1e6)

    def test_model_resolution_and_unknown_is_conservative(self):
        self.assertEqual(pricing.resolve_model('anthropic.claude-opus-5-5'), 'claude-opus-5-5')
        self.assertEqual(pricing.resolve_model('claude-opus-5-20260301'), 'claude-opus-5')
        self.assertEqual(pricing.resolve_model('claude-opus-5-5'), 'claude-opus-5-5')
        self.assertIsNone(pricing.resolve_model('gpt-4.1'))
        unknown = pricing.cost_usd('claude-unknown-9', 1_000_000, 1_000_000)
        self.assertGreaterEqual(unknown, pricing.cost_usd('claude-fable-5-1', 1_000_000, 1_000_000))
        self.assertFalse(pricing.cost_breakdown('x', 1, 1)['known_model'])

    def test_web_search_and_negative_values(self):
        self.assertAlmostEqual(pricing.cost_breakdown('claude-opus-5-5', 0, 0, web_searches=3)['total'], 0.03)
        self.assertEqual(pricing.cost_usd('claude-opus-5-5', -5, -1), 0.0)


# ---------------------------------------------------------------- Ollama

def ndjson(*chunks):
    return ('\n'.join(json.dumps(c) for c in chunks) + '\n').encode()


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'AURORA_OFFLINE': '1', 'AURORA_OLLAMA_URL': 'http://ollama.test',
                                                'AURORA_LOCAL_MODEL': 'qwen3.5:9b',
                                                'AURORA_LOCAL_FAST_MODEL': 'qwen3.5:4b',
                                                'AURORA_EMBED_MODEL': 'bge-m3'})
        self.env.start()
        self.requests = []

    def tearDown(self):
        self.env.stop()

    def provider(self, handler, **kw):
        def wrapped(request):
            self.requests.append(request)
            return handler(request)
        return OllamaProvider(transport=httpx.MockTransport(wrapped), **kw)

    def body(self, index=-1):
        return json.loads(self.requests[index].content)

    def test_available_is_cached_and_models_listed(self):
        tags = {'models': [{'name': 'qwen3.5:4b', 'size': 3389983735, 'modified_at': '2026-10-01',
                            'details': {'family': 'qwen35', 'parameter_size': '4.7B', 'quantization_level': 'Q4_K_M'}},
                           {'name': 'bge-m3:latest', 'size': 1, 'details': {}}]}
        p = self.provider(lambda r: httpx.Response(200, json=tags))
        self.assertTrue(run(p.available()))
        self.assertTrue(run(p.available()))
        self.assertEqual(len(self.requests), 1, 'available() must be cached')
        models = run(p.models())
        self.assertEqual(models[0]['name'], 'qwen3.5:4b')
        self.assertEqual(models[0]['parameter_size'], '4.7B')
        self.assertTrue(run(p.has_model('bge-m3')))
        self.assertFalse(run(p.has_model('llama3')))
        self.assertEqual(str(self.requests[0].url), 'http://ollama.test/api/tags')

    def test_unavailable_when_connection_refused(self):
        def refuse(request):
            raise httpx.ConnectError('refused', request=request)
        p = self.provider(refuse, cache_ttl=0)
        self.assertFalse(run(p.available()))
        self.assertEqual(run(p.models()), [])
        with self.assertRaises(ProviderError) as ctx:
            run(p.chat([{'role': 'user', 'content': 'Bonjour'}], system='Tu es AURORA.'))
        self.assertEqual(ctx.exception.kind, 'unavailable')

    def test_offline_blocks_real_calls(self):
        p = OllamaProvider()  # no injected transport + AURORA_OFFLINE=1
        self.assertFalse(run(p.available()))
        with self.assertRaises(ProviderError) as ctx:
            run(p.chat([{'role': 'user', 'content': 'x'}], system=''))
        self.assertEqual(ctx.exception.kind, 'unavailable')
        self.assertIsNone(run(p.classify('x', ['a'], instructions='i')))

    def test_chat_streams_tokens_with_think_false(self):
        stream = ndjson(
            {'model': 'qwen3.5:9b', 'message': {'role': 'assistant', 'content': 'Bon'}, 'done': False},
            {'model': 'qwen3.5:9b', 'message': {'role': 'assistant', 'content': 'jour !'}, 'done': False},
            {'model': 'qwen3.5:9b', 'message': {'role': 'assistant', 'content': ''}, 'done': True,
             'done_reason': 'stop', 'prompt_eval_count': 21, 'eval_count': 4, 'eval_duration': 200_000_000,
             'load_duration': 1_000_000, 'prompt_eval_duration': 5_000_000})
        p = self.provider(lambda r: httpx.Response(200, content=stream))
        tokens = []

        async def on_token(t):
            tokens.append(t)
        result = run(p.chat([{'role': 'user', 'content': 'Salut'}], system='Tu es AURORA.', max_tokens=64,
                            on_token=on_token))
        self.assertEqual(tokens, ['Bon', 'jour !'])
        self.assertIsInstance(result, ProviderResult)
        self.assertEqual(result.text, 'Bonjour !')
        self.assertEqual((result.input_tokens, result.output_tokens), (21, 4))
        self.assertEqual(result.stop_reason, 'end_turn')
        self.assertEqual(result.cost_usd, 0.0)
        self.assertEqual(result.meta['tokens_per_s'], 20.0)
        body = self.body()
        self.assertIs(body['think'], False)
        self.assertIs(body['stream'], True)
        self.assertEqual(body['options']['num_predict'], 64)
        self.assertEqual(body['model'], 'qwen3.5:9b')
        self.assertEqual(body['messages'][0], {'role': 'system', 'content': 'Tu es AURORA.'})
        self.assertEqual(str(self.requests[-1].url), 'http://ollama.test/api/chat')
        # 23/11/2026, ID Studio IA's garage PC: without a window asked, Ollama gave the model 4 096 tokens, two thirds of them
        # taken by AURORA's own instructions. 8 192 are asked with every call; AURORA_OLLAMA_NUM_CTX changes it.
        self.assertEqual(body['options']['num_ctx'], 8192)
        for asked, sent in (('16384', 16384), ('512', 8192), ('beaucoup', 8192)):
            with mock.patch.dict(os.environ, {'AURORA_OLLAMA_NUM_CTX': asked}):
                run(p.chat([{'role': 'user', 'content': 'Salut'}], system='', max_tokens=64))
            self.assertEqual(self.body()['options']['num_ctx'], sent, asked)
        # A caller's own window passes first (the 3D lab asks its own when the loaded model has less).
        run(p.chat([{'role': 'user', 'content': 'Salut'}], system='', options={'num_ctx': 4096}))
        self.assertEqual(self.body()['options']['num_ctx'], 4096)

    def test_chat_length_stop_is_flagged(self):
        stream = ndjson({'message': {'content': 'abc'}, 'done': False},
                        {'message': {'content': ''}, 'done': True, 'done_reason': 'length', 'eval_count': 3})
        p = self.provider(lambda r: httpx.Response(200, content=stream))
        result = run(p.chat([{'role': 'user', 'content': 'x'}], system='', model='qwen3.5:4b'))
        self.assertEqual(result.stop_reason, 'max_tokens')
        self.assertTrue(result.meta['truncated'])
        self.assertEqual(self.body()['model'], 'qwen3.5:4b')

    def test_chat_errors_are_typed(self):
        p = self.provider(lambda r: httpx.Response(404, json={'error': 'model "zzz" not found, try pulling it first'}))
        with self.assertRaises(ProviderError) as ctx:
            run(p.chat([{'role': 'user', 'content': 'x'}], system='', model='zzz'))
        self.assertEqual(ctx.exception.kind, 'unavailable')
        self.assertIn('zzz', ctx.exception.message_fr)

        def slow(request):
            raise httpx.ReadTimeout('slow', request=request)
        with self.assertRaises(ProviderError) as ctx:
            run(self.provider(slow).chat([{'role': 'user', 'content': 'x'}], system=''))
        self.assertEqual(ctx.exception.kind, 'timeout')

        truncated = self.provider(lambda r: httpx.Response(200, content=ndjson({'message': {'content': 'a'},
                                                                                'done': False})))
        with self.assertRaises(ProviderError) as ctx:
            run(truncated.chat([{'role': 'user', 'content': 'x'}], system=''))
        self.assertEqual(ctx.exception.kind, 'connection')

        with self.assertRaises(ProviderError) as ctx:
            run(truncated.chat([{'role': 'system', 'content': 'x'}], system=''))
        self.assertEqual(ctx.exception.kind, 'bad_request')
        with self.assertRaises(ProviderError) as ctx:
            run(truncated.chat([{'role': 'assistant', 'content': 'x'}], system=''))
        self.assertEqual(ctx.exception.kind, 'bad_request')

    def test_history_window_is_normalised(self):
        from aurora.providers.base import validate_messages
        clean = validate_messages([{'role': 'assistant', 'content': 'fin du tour précédent'},
                                   {'role': 'note', 'content': 'note interne'},
                                   {'role': 'user', 'content': 'Bonjour'},
                                   {'role': 'assistant', 'content': 'Salut'},
                                   {'role': 'user', 'content': 'Et Mars ?'}], provider='test')
        self.assertEqual([m['role'] for m in clean], ['user', 'assistant', 'user'])
        self.assertEqual(clean[0]['content'], 'Bonjour')

    def test_embed_batches(self):
        def handler(request):
            payload = json.loads(request.content)
            return httpx.Response(200, json={'embeddings': [[0.1, 0.2, 0.3] for _ in payload['input']]})
        p = self.provider(handler)
        vectors = run(p.embed([f'texte {i}' for i in range(40)]))
        self.assertEqual(len(vectors), 40)
        self.assertEqual(len(self.requests), 2)  # batches of 32
        self.assertEqual(self.body(0)['model'], 'bge-m3')
        self.assertEqual(len(self.body(0)['input']), 32)
        self.assertEqual(run(p.embed([])), [])

        bad = self.provider(lambda r: httpx.Response(200, json={'embeddings': []}))
        with self.assertRaises(ProviderError):
            run(bad.embed(['a']))

    def test_classify_uses_schema_enum(self):
        content = json.dumps({'label': 'weather.current', 'confidence': 0.92})
        p = self.provider(lambda r: httpx.Response(200, json={'message': {'content': content}, 'done': True}))
        labels = ['system.status', 'weather.current', 'chat.general']
        result = run(p.classify('il fait quel temps à Lyon', labels, instructions='Classe la demande.'))
        self.assertEqual(result, ('weather.current', 0.92))
        body = self.body()
        self.assertEqual(body['format']['properties']['label']['enum'], labels)
        self.assertIs(body['think'], False)
        self.assertIs(body['stream'], False)
        self.assertEqual(body['model'], 'qwen3.5:4b')
        self.assertEqual(body['options']['num_ctx'], 8192)   # the same window as an answer: no reloading between calls

    def test_classify_never_raises(self):
        cases = [
            lambda r: httpx.Response(200, json={'message': {'content': '{"label": "inconnu", "confidence": 1}'}}),
            lambda r: httpx.Response(200, json={'message': {'content': 'pas du json'}}),
            lambda r: httpx.Response(500, json={'error': 'boom'}),
        ]
        for handler in cases:
            self.assertIsNone(run(self.provider(handler).classify('x', ['a', 'b'], instructions='i')))

        def boom(request):
            raise httpx.ConnectError('down', request=request)
        self.assertIsNone(run(self.provider(boom).classify('x', ['a'], instructions='i')))
        no_conf = self.provider(lambda r: httpx.Response(200, json={'message': {'content': '{"label": "a"}'}}))
        self.assertEqual(run(no_conf.classify('x', ['a'], instructions='i')), ('a', 0.5))
        self.assertIsNone(run(no_conf.classify('', ['a'], instructions='i')))


# ---------------------------------------------------------------- Claude (fake SDK client)

def text_delta(text):
    return NS(type='content_block_delta', index=0, delta=NS(type='text_delta', text=text))


def message(content, *, stop_reason='end_turn', model='claude-opus-5-5', usage=None, stop_details=None):
    usage = usage or NS(input_tokens=100, output_tokens=50, cache_read_input_tokens=1000,
                        cache_creation_input_tokens=200, server_tool_use=None, iterations=None)
    return NS(id='msg_test', type='message', role='assistant', model=model, content=content,
              stop_reason=stop_reason, stop_details=stop_details, usage=usage)


def text_block(text, citations=None):
    return NS(type='text', text=text, citations=citations)


class FakeStream:
    def __init__(self, events, final):
        self._events, self._final = events, final

    def __aiter__(self):
        async def gen():
            for event in self._events:
                yield event
        return gen()

    async def get_final_message(self):
        return self._final


class FakeManager:
    def __init__(self, script):
        self._script = script

    async def __aenter__(self):
        if isinstance(self._script, BaseException):
            raise self._script
        events, final = self._script
        return FakeStream(events, final)

    async def __aexit__(self, *exc):
        return False


class FakeMessages:
    def __init__(self, owner, path):
        self.owner, self.path = owner, path

    def stream(self, **kwargs):
        self.owner.calls.append((self.path, kwargs))
        return FakeManager(self.owner.scripts.pop(0))


class FakeClient:
    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.calls = []
        self.messages = FakeMessages(self, 'messages')
        self.beta = NS(messages=FakeMessages(self, 'beta'))


def sdk_error(cls, status):
    import anthropic
    import httpx2
    request = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages', headers={'x-api-key': SECRET})
    if cls in (anthropic.APIConnectionError,):
        return cls(message=f'connection failed {SECRET}', request=request)
    if cls is anthropic.APITimeoutError:
        return cls(request=request)
    response = httpx2.Response(status, request=request, headers={'request-id': 'req_test'})
    return cls(f'error with {SECRET}', response=response, body={'type': 'error', 'error': {'type': 'x', 'message': SECRET}})


class AnthropicTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'AURORA_OFFLINE': '1'})
        self.env.start()
        for key in ('AURORA_CLOUD_MODEL_RAPIDE', 'AURORA_CLOUD_MODEL_STANDARD', 'AURORA_CLOUD_MODEL_EXPERT',
                    'AURORA_CLOUD_FALLBACKS'):
            os.environ.pop(key, None)

    def tearDown(self):
        self.env.stop()

    def provider(self, *scripts, **kw):
        client = FakeClient(*scripts)
        kw.setdefault('budget_guard', False)
        return AnthropicProvider(client, **kw), client

    def test_configured_from_environment_only(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ANTHROPIC_API_KEY', None)
            os.environ.pop('ANTHROPIC_AUTH_TOKEN', None)
            self.assertFalse(AnthropicProvider().configured())
            with self.assertRaises(ProviderError) as ctx:
                run(AnthropicProvider().chat([{'role': 'user', 'content': 'x'}], system='s'))
            self.assertEqual(ctx.exception.kind, 'unavailable')
            self.assertIn("n'est pas configuré", ctx.exception.message_fr)
            os.environ['ANTHROPIC_AUTH_TOKEN'] = 'token'
            self.assertTrue(AnthropicProvider().configured())
            os.environ.pop('ANTHROPIC_AUTH_TOKEN')
            os.environ['ANTHROPIC_API_KEY'] = SECRET
            self.assertTrue(AnthropicProvider().configured())
            # Offline mode never calls the real SDK.
            with self.assertRaises(ProviderError) as ctx:
                run(AnthropicProvider().chat([{'role': 'user', 'content': 'x'}], system='s'))
            self.assertEqual(ctx.exception.kind, 'unavailable')

    def test_tiers_defaults_and_overrides(self):
        tiers = AnthropicProvider().tiers()
        self.assertEqual(tiers['rapide']['model'], 'claude-haiku-5-5')
        self.assertEqual(tiers['rapide']['effort'], 'low')
        self.assertFalse(tiers['rapide']['fallback'])
        self.assertEqual((tiers['standard']['model'], tiers['standard']['effort']), ('claude-sonnet-5-5', 'medium'))
        self.assertEqual((tiers['expert']['model'], tiers['expert']['effort']), ('claude-opus-5-5', 'high'))
        self.assertTrue(tiers['standard']['fallback'] and tiers['expert']['fallback'])
        with mock.patch.dict(os.environ, {'AURORA_CLOUD_MODEL_STANDARD': 'claude-opus-5-5'}):
            self.assertEqual(AnthropicProvider().tiers()['standard']['model'], 'claude-opus-5-5')
        with self.assertRaises(ProviderError):
            AnthropicProvider().tier('ultra')

    def test_standard_request_shape_and_streaming(self):
        final = message([NS(type='thinking', thinking='', signature='sig'), text_block('Bonjour !')], model='claude-sonnet-5-5')
        p, client = self.provider(([text_delta('Bon'), NS(type='text', text='Bon'), text_delta('jour !')], final))
        tokens = []

        async def on_token(t):
            tokens.append(t)
        result = run(p.chat([{'role': 'user', 'content': 'Salut'}], system='Prompt stable', tier='standard',
                            max_tokens=2048, on_token=on_token, system_dynamic='Internet: oui'))
        self.assertEqual(tokens, ['Bon', 'jour !'])  # only text deltas, no duplicate synthesized events
        self.assertEqual(result.text, 'Bonjour !')
        path, kw = client.calls[0]
        self.assertEqual(path, 'beta')
        self.assertEqual(kw['betas'], [FALLBACK_BETA])
        self.assertEqual(kw['fallbacks'], 'default')
        self.assertEqual(kw['model'], 'claude-sonnet-5-5')
        self.assertEqual(kw['output_config'], {'effort': 'medium'})
        self.assertEqual(kw['max_tokens'], 2048)
        for forbidden in ('thinking', 'temperature', 'top_p', 'top_k', 'budget_tokens'):
            self.assertNotIn(forbidden, kw)
        self.assertEqual(kw['system'][0], {'type': 'text', 'text': 'Prompt stable', 'cache_control': {'type': 'ephemeral'}})
        self.assertEqual(kw['system'][1], {'type': 'text', 'text': 'Internet: oui'})
        self.assertNotIn('tools', kw)
        expected = pricing.cost_usd('claude-sonnet-5-5', 100, 50, 1000, 200)
        self.assertAlmostEqual(result.cost_usd, expected)
        self.assertEqual((result.cache_read_tokens, result.cache_write_tokens), (1000, 200))
        self.assertEqual(result.provider, 'anthropic')
        self.assertEqual(result.stop_reason, 'end_turn')
        self.assertFalse(result.meta['fallback']['used'])

    def test_rapide_uses_plain_endpoint_without_fallbacks(self):
        p, client = self.provider(([text_delta('ok')], message([text_block('ok')], model='claude-haiku-5-5')))
        run(p.chat([{'role': 'user', 'content': 'x'}], system='s', tier='rapide'))
        path, kw = client.calls[0]
        self.assertEqual(path, 'messages')
        self.assertNotIn('fallbacks', kw)
        self.assertNotIn('betas', kw)
        self.assertEqual(kw['output_config'], {'effort': 'low'})

    def test_fallbacks_can_be_disabled(self):
        with mock.patch.dict(os.environ, {'AURORA_CLOUD_FALLBACKS': '0'}):
            p, client = self.provider(([], message([text_block('ok')])))
            run(p.chat([{'role': 'user', 'content': 'x'}], system='s', tier='expert'))
        self.assertEqual(client.calls[0][0], 'messages')

    def test_web_search_sources_and_pause_turn(self):
        citation = NS(type='web_search_result_location', url='https://fr.wikipedia.org/wiki/Mars', title='Mars',
                      cited_text='Mars est la quatrième planète', encrypted_index='x')
        results = NS(type='web_search_tool_result', tool_use_id='srv_1', content=[
            NS(type='web_search_result', url='https://fr.wikipedia.org/wiki/Mars', title='Mars', page_age=None,
               encrypted_content='e'),
            NS(type='web_search_result', url='https://nasa.gov/mars', title='NASA Mars', page_age='2 days',
               encrypted_content='e'),
            NS(type='web_search_result', url='javascript:alert(1)', title='bad', page_age=None, encrypted_content='e')])
        paused = message([NS(type='server_tool_use', id='srv_1', name='web_search', input={'query': 'Mars'}), results,
                          text_block('Selon Wikipédia, ')], stop_reason='pause_turn',
                         usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0, server_tool_use=NS(web_search_requests=1),
                                  iterations=None))
        final = message([text_block('Mars est rouge.', citations=[citation])],
                        usage=NS(input_tokens=20, output_tokens=7, cache_read_input_tokens=0,
                                 cache_creation_input_tokens=0, server_tool_use=NS(web_search_requests=1),
                                 iterations=None))
        p, client = self.provider(([], paused), ([], final))
        result = run(p.chat([{'role': 'user', 'content': 'Parle-moi de Mars'}], system='s', web_search=True))
        self.assertEqual(len(client.calls), 2)
        tools = client.calls[0][1]['tools']
        self.assertEqual(tools, [{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 3}])
        second_messages = client.calls[1][1]['messages']
        self.assertEqual(second_messages[-1]['role'], 'assistant')
        self.assertEqual(len(second_messages), 2)
        self.assertEqual(result.text, 'Selon Wikipédia, Mars est rouge.')
        urls = [s['url'] for s in result.meta['sources']]
        self.assertEqual(urls, ['https://fr.wikipedia.org/wiki/Mars', 'https://nasa.gov/mars'])
        self.assertTrue(result.meta['sources'][0]['cited'])
        self.assertEqual(result.meta['sources'][0]['cited_text'], 'Mars est la quatrième planète')
        self.assertEqual(result.meta['web_search_requests'], 2)
        self.assertEqual(result.meta['continuations'], 1)
        expected = (pricing.cost_usd('claude-opus-5-5', 10, 5) + pricing.cost_usd('claude-opus-5-5', 20, 7)
                    + 2 * pricing.WEB_SEARCH_USD_PER_REQUEST)
        self.assertAlmostEqual(result.cost_usd, expected, places=6)

    def test_refusal_raises_with_category_and_cost(self):
        final = message([text_block('Je commence')], stop_reason='refusal',
                        stop_details=NS(type='refusal', category='cyber', explanation='x'))
        p, _ = self.provider(([text_delta('Je commence')], final))
        with self.assertRaises(ProviderError) as ctx:
            run(p.chat([{'role': 'user', 'content': 'x'}], system='s'))
        error = ctx.exception
        self.assertEqual(error.kind, 'refusal')
        self.assertEqual(error.category, 'cyber')
        self.assertIn('cybersécurité', error.message_fr)
        self.assertIsNotNone(error.result)
        self.assertGreater(error.result.cost_usd, 0)
        self.assertTrue(error.result.meta['partial_discarded'])
        # stop_details may be null on a refusal
        p, _ = self.provider(([], message([], stop_reason='refusal')))
        with self.assertRaises(ProviderError) as ctx:
            run(p.chat([{'role': 'user', 'content': 'x'}], system='s'))
        self.assertIsNone(ctx.exception.category)
        self.assertIn('non précisée', ctx.exception.message_fr)

    def test_max_tokens_is_noted(self):
        p, _ = self.provider(([], message([text_block('Début')], stop_reason='max_tokens')))
        result = run(p.chat([{'role': 'user', 'content': 'x'}], system='s'))
        self.assertEqual(result.stop_reason, 'max_tokens')
        self.assertTrue(result.meta['truncated'])
        self.assertIn('tronquée', result.meta['warning_fr'])

    def test_fallback_iterations_are_billed_per_model(self):
        usage = NS(input_tokens=30, output_tokens=40, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                   server_tool_use=None, iterations=[
                       NS(type='message', model='claude-fable-5-1', input_tokens=30, output_tokens=0,
                          cache_read_input_tokens=0, cache_creation_input_tokens=0),
                       NS(type='fallback_message', model='claude-opus-4-8', input_tokens=30, output_tokens=40,
                          cache_read_input_tokens=0, cache_creation_input_tokens=0)])
        fallback_block = NS(type='fallback', from_=NS(model='claude-fable-5-1'), to=NS(model='claude-opus-4-8'),
                            trigger=None)
        final = message([fallback_block, text_block('Réponse')], model='claude-opus-4-8', usage=usage)
        p, _ = self.provider(([], final))
        result = run(p.chat([{'role': 'user', 'content': 'x'}], system='s', tier='expert'))
        self.assertEqual(result.model, 'claude-opus-4-8')
        self.assertTrue(result.meta['fallback']['used'])
        self.assertEqual(result.meta['fallback']['switches'], [{'from': 'claude-fable-5-1', 'to': 'claude-opus-4-8'}])
        expected = pricing.cost_usd('claude-fable-5-1', 30, 0) + pricing.cost_usd('claude-opus-4-8', 30, 40)
        self.assertAlmostEqual(result.cost_usd, expected)
        self.assertEqual(result.input_tokens, 60)

    def test_outgoing_text_is_redacted(self):
        p, client = self.provider(([], message([text_block('ok')])))
        result = run(p.chat([{'role': 'user', 'content': 'Écris à jean.dupont@example.fr, ma clé ' + SECRET}],
                            system='s'))
        sent = client.calls[0][1]['messages'][0]['content']
        self.assertNotIn('jean.dupont@example.fr', sent)
        self.assertNotIn(SECRET, sent)
        self.assertIn('[EMAIL]', sent)
        self.assertEqual({f['type'] for f in result.meta['redactions']}, {'email', 'api_key'})

    def test_sdk_errors_map_to_safe_kinds(self):
        import anthropic
        cases = [
            (anthropic.AuthenticationError, 401, 'auth'),
            (anthropic.PermissionDeniedError, 403, 'auth'),
            (anthropic.NotFoundError, 404, 'bad_request'),
            (anthropic.RequestTooLargeError, 413, 'bad_request'),
            (anthropic.BadRequestError, 400, 'bad_request'),
            (anthropic.RateLimitError, 429, 'rate_limit'),
            (anthropic.OverloadedError, 529, 'overloaded'),
            (anthropic.ServiceUnavailableError, 503, 'unavailable'),
            (anthropic.DeadlineExceededError, 504, 'timeout'),
            (anthropic.InternalServerError, 500, 'unavailable'),
            (anthropic.APIStatusError, 402, 'budget'),
            (anthropic.APITimeoutError, 0, 'timeout'),
            (anthropic.APIConnectionError, 0, 'connection'),
        ]
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': SECRET}):
            for cls, status, kind in cases:
                with self.subTest(cls=cls.__name__):
                    p, _ = self.provider(sdk_error(cls, status))
                    with self.assertRaises(ProviderError) as ctx:
                        run(p.chat([{'role': 'user', 'content': 'x'}], system='s'))
                    self.assertEqual(ctx.exception.kind, kind)
                    self.assertNotIn(SECRET, ctx.exception.message_fr)
                    self.assertNotIn('x-api-key', ctx.exception.message_fr.lower())
                    self.assertIsNone(ctx.exception.__cause__)
                    self.assertNotIn(SECRET, json.dumps(ctx.exception.to_dict()))

    def test_budget_guard_blocks_when_exhausted(self):
        from aurora.security import budget
        p, client = self.provider(([], message([text_block('ok')])), budget_guard=True)
        with mock.patch.object(budget, 'remaining_usd', return_value=0.0):
            with self.assertRaises(ProviderError) as ctx:
                run(p.chat([{'role': 'user', 'content': 'x'}], system='s'))
        self.assertEqual(ctx.exception.kind, 'budget')
        with mock.patch.object(budget, 'remaining_usd', side_effect=RuntimeError('db down')):
            with self.assertRaises(ProviderError) as ctx:
                run(p.chat([{'role': 'user', 'content': 'x'}], system='s'))
        self.assertEqual(ctx.exception.kind, 'budget')
        self.assertEqual(client.calls, [])
        with mock.patch.object(budget, 'remaining_usd', return_value=5.0):
            self.assertEqual(run(p.chat([{'role': 'user', 'content': 'x'}], system='s')).text, 'ok')

    def test_estimate_cost(self):
        p = AnthropicProvider()
        small, big = p.estimate_cost(300, 'standard'), p.estimate_cost(300_000, 'standard')
        self.assertGreater(small, 0)
        self.assertGreater(big, small)
        self.assertLess(p.estimate_cost(300, 'rapide'), p.estimate_cost(300, 'expert'))


def sse(*events):
    return ''.join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def sse_message(model, text, *, stop_reason='end_turn', stop_details=None):
    return sse(
        {'type': 'message_start', 'message': {
            'id': 'msg_wire', 'type': 'message', 'role': 'assistant', 'model': model, 'content': [],
            'stop_reason': None, 'stop_sequence': None,
            'usage': {'input_tokens': 12, 'output_tokens': 1, 'cache_read_input_tokens': 0,
                      'cache_creation_input_tokens': 30}}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text[:4]}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text[4:]}},
        {'type': 'content_block_stop', 'index': 0},
        {'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None,
                                            'stop_details': stop_details},
         'usage': {'output_tokens': 9}},
        {'type': 'message_stop'})


class AnthropicWireTests(unittest.TestCase):
    """Drives the real AsyncAnthropic streaming helpers through httpx2.MockTransport (no network)."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'AURORA_OFFLINE': '1'})
        self.env.start()
        os.environ.pop('AURORA_CLOUD_FALLBACKS', None)
        self.requests = []

    def tearDown(self):
        self.env.stop()

    def provider(self, body):
        import anthropic
        import httpx2

        def handler(request):
            self.requests.append(request)
            return httpx2.Response(200, headers={'content-type': 'text/event-stream', 'request-id': 'req_wire'},
                                   content=body)
        client = anthropic.AsyncAnthropic(api_key='test-key-not-real', max_retries=0,
                                          http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        return AnthropicProvider(client, budget_guard=False)

    def test_standard_tier_wire_request(self):
        p = self.provider(sse_message('claude-opus-5-5', 'Bonjour AURORA'))
        tokens = []

        async def on_token(t):
            tokens.append(t)
        result = run(p.chat([{'role': 'user', 'content': 'Salut'}], system='Prompt stable', tier='standard',
                            on_token=on_token, web_search=True))
        self.assertEqual(result.text, 'Bonjour AURORA')
        self.assertEqual(tokens, ['Bonj', 'our AURORA'])
        self.assertEqual((result.input_tokens, result.output_tokens, result.cache_write_tokens), (12, 9, 30))
        self.assertAlmostEqual(result.cost_usd, pricing.cost_usd('claude-opus-5-5', 12, 9, 0, 30))
        request = self.requests[0]
        self.assertTrue(request.url.path.endswith('/v1/messages'))
        self.assertIn(FALLBACK_BETA, request.headers.get('anthropic-beta', ''))
        body = json.loads(request.content)
        self.assertEqual(body['fallbacks'], 'default')
        self.assertIs(body['stream'], True)
        self.assertEqual(body['output_config'], {'effort': 'medium'})
        self.assertEqual(body['system'], [{'type': 'text', 'text': 'Prompt stable',
                                           'cache_control': {'type': 'ephemeral'}}])
        self.assertEqual(body['tools'], [{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 3}])
        for forbidden in ('thinking', 'temperature', 'top_p', 'top_k'):
            self.assertNotIn(forbidden, body)

    def test_rapide_tier_wire_request_and_refusal(self):
        p = self.provider(sse_message('claude-haiku-5-5', 'Je ne peux', stop_reason='refusal',
                                      stop_details={'type': 'refusal', 'category': 'bio', 'explanation': None}))
        with self.assertRaises(ProviderError) as ctx:
            run(p.chat([{'role': 'user', 'content': 'x'}], system='s', tier='rapide'))
        self.assertEqual((ctx.exception.kind, ctx.exception.category), ('refusal', 'bio'))
        body = json.loads(self.requests[0].content)
        self.assertNotIn('fallbacks', body)
        self.assertNotIn('anthropic-beta', self.requests[0].headers)
        self.assertEqual(body['model'], 'claude-haiku-5-5')
        self.assertEqual(body['output_config'], {'effort': 'low'})


@unittest.skipUnless(os.getenv('AURORA_LIVE_TESTS') == '1', 'AURORA_LIVE_TESTS=1 pour interroger Ollama')
class FakePiperVoice:
    """Like PiperVoice.synthesize: one chunk of 0.1 s per sentence; records the pace it was asked for."""
    def __init__(self):
        self.configs = []

    def synthesize(self, text, syn_config=None):
        from types import SimpleNamespace
        self.configs.append(syn_config)
        for _sentence in [s for s in text.split('.') if s.strip()]:
            yield SimpleNamespace(sample_rate=22050, sample_width=2, sample_channels=1,
                                  audio_int16_bytes=b'\x00\x00' * 2205)


class PiperVoiceTests(unittest.TestCase):
    """Local neural voice: everything runs on this PC; the browser voices stay as the fallback."""

    def setUp(self):
        import tempfile
        from aurora.providers import piper_voice
        self.pv = piper_voice
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {'AURORA_VOICES_DIR': self.tmp.name, 'AURORA_VOICE': 'fr_FR-siwis-medium'})
        env.start()
        self.addCleanup(env.stop)

    def test_unavailable_without_the_voice_files(self):
        self.assertFalse(self.pv.available())
        self.assertFalse(self.pv.status()['available'])
        with self.assertRaises(RuntimeError):
            self.pv.synthesize('Bonjour')

    def test_synthesize_returns_a_wav_and_checks_the_text(self):
        for name in ('fr_FR-siwis-medium.onnx', 'fr_FR-siwis-medium.onnx.json'):
            open(os.path.join(self.tmp.name, name), 'wb').close()
        fake = FakePiperVoice()
        with mock.patch.object(self.pv, '_engine_installed', return_value=True), \
                mock.patch.dict(self.pv._loaded, {'fr_FR-siwis-medium': fake}):
            self.assertTrue(self.pv.available())
            audio = self.pv.synthesize('Bonjour, je suis AURORA.')
            self.assertEqual(audio[:4], b'RIFF')
            self.assertEqual(audio[8:12], b'WAVE')
            import io
            import wave
            two = self.pv.synthesize('Bonjour. Je suis AURORA.', speed=1.3)
            with wave.open(io.BytesIO(two)) as wav:   # 2 sentences of 0.1 s + the pause between them
                self.assertAlmostEqual(wav.getnframes() / wav.getframerate(), 0.2 + self.pv.SENTENCE_PAUSE_S, places=2)
            self.assertAlmostEqual(fake.configs[0].length_scale, self.pv.LENGTH_SCALE)       # calm default pace
            self.assertAlmostEqual(fake.configs[1].length_scale, self.pv.LENGTH_SCALE / 1.3)  # faster on request
            for bad in ('', '   ', 'x' * (self.pv.MAX_CHARS + 1)):
                with self.assertRaises(ValueError):
                    self.pv.synthesize(bad)

    @unittest.skipUnless(os.path.isfile(os.path.expanduser('~/.aurora/voices/fr_FR-siwis-medium.onnx')),
                         'voix siwis non installée')
    def test_real_local_voice(self):
        # Reads the installed voice files only (never the AURORA database).
        import io
        import wave
        with mock.patch.dict(os.environ, {'AURORA_VOICES_DIR': os.path.expanduser('~/.aurora/voices')}):
            audio = self.pv.synthesize('Bonjour, il fait quatorze degrés à Paris.')
        with wave.open(io.BytesIO(audio)) as wav:
            self.assertGreater(wav.getnframes() / wav.getframerate(), 1.0)


class OllamaLiveTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'AURORA_OFFLINE': '0'})
        self.env.start()
        self.p = OllamaProvider()
        if not run(self.p.available()):
            self.skipTest('Ollama ne répond pas')

    def tearDown(self):
        self.env.stop()

    def test_live_chat_embed_classify(self):
        started = time.perf_counter()
        result = run(self.p.chat([{'role': 'user', 'content': 'Dis bonjour en une phrase.'}],
                                 system='Réponds en français, brièvement.', model=self.p.fast_model, max_tokens=60))
        self.assertTrue(result.text.strip())
        print(f'\n[live] chat {result.model}: {(time.perf_counter() - started) * 1000:.0f} ms, '
              f'{result.output_tokens} jetons, ttft {result.meta["ttft_ms"]} ms')
        vectors = run(self.p.embed(['Bonjour', 'Planète Mars']))
        self.assertEqual(len(vectors), 2)
        print(f'[live] embed dim={len(vectors[0])}')
        label = run(self.p.classify('Quel temps fait-il à Lyon ?', ['weather.current', 'system.status', 'chat.general'],
                                    instructions="Classe la demande de l'utilisateur.", timeout=60))
        self.assertIsNotNone(label)
        self.assertEqual(label[0], 'weather.current')


if __name__ == '__main__':
    unittest.main()
