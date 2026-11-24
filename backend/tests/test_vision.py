"""E2, local vision (tools/vision.py, /api/vision): an image or a screen capture read on this PC by Ollama.

No real model is called: Ollama is a fake one behind httpx.MockTransport (as in test_providers.py), and the real
screen is never captured here (the capture is replaced by fixed bytes), so nothing of the PC's screen is read.
"""
import asyncio
import base64
import json
import os
import struct
import zlib
import unittest
from unittest import mock

import httpx
import numpy as np

from aurora.providers.ollama import OllamaProvider
from aurora.tools import vision
from tests.test_api import NO_TOKEN, ApiTestCase
from tests.test_providers import ndjson

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 32
OCTETS = {'Content-Type': 'application/octet-stream'}


def run(coro):
    return asyncio.run(coro)


class FakeOllama:
    """Answers /api/tags, /api/show and /api/chat like Ollama. `chat_answers`: what each chat call gets, in order
    (an HTTP status, or an exception to raise); a plain reply once the list is used up."""

    def __init__(self, names, caps=None, reply='Un globe terrestre sur fond noir.', chat_answers=()):
        self.names, self.caps, self.reply = names, caps or {}, reply
        self.chat_answers = list(chat_answers)
        self.chats: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': n} for n in self.names]})
        if path == '/api/show':
            name = json.loads(request.content)['model']
            return httpx.Response(200, json={'capabilities': self.caps.get(name, ['completion'])})
        if path == '/api/chat':
            body = json.loads(request.content)
            self.chats.append(body)
            answer = self.chat_answers.pop(0) if self.chat_answers else 200
            if isinstance(answer, Exception):
                raise answer
            if answer != 200:
                return httpx.Response(answer, json={'error': 'model not found' if answer == 404 else 'device lost'})
            return httpx.Response(200, content=ndjson({'message': {'content': self.reply}},
                                                      {'done': True, 'model': body['model'], 'done_reason': 'stop',
                                                       'eval_count': 7}))
        return httpx.Response(404)


class VisionToolTests(unittest.TestCase):
    def use(self, fake: FakeOllama) -> FakeOllama:
        patch = mock.patch('aurora.providers.ollama.ollama', OllamaProvider(transport=httpx.MockTransport(fake)))
        patch.start()
        self.addCleanup(patch.stop)
        return fake

    def setUp(self):
        env = mock.patch.dict(os.environ, {'AURORA_OLLAMA_URL': 'http://ollama.test'})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('AURORA_VISION_MODEL', None)

    # -- which model sees the image (Athéna's order)
    def test_ornith_first_then_the_order_of_athena(self):
        self.use(FakeOllama(['qwen3.5:9b', 'gemma3:4b', 'qwen2.5vl:7b', 'ornith-1.5:9b']))
        self.assertEqual(run(vision.vision_model()), 'ornith-1.5:9b')

    def test_gemma_comes_after_qwen_vl(self):
        self.use(FakeOllama(['gemma3:4b', 'qwen2.5vl:7b', 'qwen3.5:9b']))
        self.assertEqual(run(vision.vision_model()), 'qwen2.5vl:7b')

    def test_any_model_that_declares_vision_otherwise(self):
        self.use(FakeOllama(['qwen3.5:9b', 'inconnu:2b'], caps={'inconnu:2b': ['completion', 'vision']}))
        self.assertEqual(run(vision.vision_model()), 'inconnu:2b')

    def test_a_forced_model_only_if_installed(self):
        self.use(FakeOllama(['ornith-1.5:9b', 'llava:7b']))
        with mock.patch.dict(os.environ, {'AURORA_VISION_MODEL': 'llava:7b'}):
            self.assertEqual(run(vision.vision_model()), 'llava:7b')
        with mock.patch.dict(os.environ, {'AURORA_VISION_MODEL': 'absent:1b'}):
            self.assertEqual(run(vision.vision_model()), 'ornith-1.5:9b')

    def test_no_vision_model_is_said_and_nothing_is_described(self):
        fake = self.use(FakeOllama(['qwen3.5:9b', 'bge-m3:latest']))
        status = run(vision.status())
        self.assertFalse(status['available'])
        self.assertIn('aucun modèle de vision installé', status['reason'])
        with self.assertRaises(vision.VisionError) as err:
            run(vision.describe(PNG))
        self.assertIn('ne décrit pas une image qu’elle ne voit pas', str(err.exception))
        self.assertEqual(fake.chats, [])

    # -- what is sent, with Athéna's settings
    def test_the_image_goes_to_the_vision_model_with_athenas_settings(self):
        fake = self.use(FakeOllama(['ornith-1.5:9b'], reply='<think>brouillon</think>Un globe terrestre.'))
        seen = run(vision.describe(JPEG))
        self.assertEqual(seen['text'], 'Un globe terrestre.')
        self.assertEqual((seen['model'], seen['processor'], seen['truncated']), ('ornith-1.5:9b', 'carte graphique', False))
        body = fake.chats[0]
        self.assertEqual(body['model'], 'ornith-1.5:9b')
        self.assertEqual(base64.b64decode(body['messages'][-1]['images'][0]), JPEG)
        self.assertIn(vision.IMAGE_QUESTION, body['messages'][-1]['content'])
        self.assertIn('Décris UNIQUEMENT ce que tu vois réellement', body['messages'][-1]['content'])
        self.assertEqual(body['options']['temperature'], 0.2)
        self.assertEqual(body['options']['num_ctx'], 8192)
        self.assertNotIn('num_gpu', body['options'])
        self.assertIs(body['think'], False)
        self.assertEqual(body['keep_alive'], '30m')

    def test_the_screen_question_is_asked_for_a_capture(self):
        fake = self.use(FakeOllama(['ornith-1.5:9b']))
        seen = run(vision.describe(PNG, vision.SCREEN_QUESTION))
        self.assertIn('capture de l’écran', fake.chats[0]['messages'][-1]['content'])
        # 22/11/2026: a description of twenty lines at most (writing it was the longest part of a screen reading),
        # and the three parts of the time kept with the answer.
        self.assertIn('en vingt lignes au plus', fake.chats[0]['messages'][-1]['content'])
        self.assertEqual(sorted(seen['timings']), ['image', 'load', 'writing'])
        self.assertTrue(all(isinstance(value, float) and value >= 0 for value in seen['timings'].values()))

    def test_one_more_try_on_the_processor_when_the_card_fails(self):
        fake = self.use(FakeOllama(['ornith-1.5:9b'], chat_answers=[500]))
        seen = run(vision.describe(PNG))
        self.assertEqual(seen['processor'], 'processeur')
        self.assertEqual(len(fake.chats), 2)
        self.assertNotIn('num_gpu', fake.chats[0]['options'])
        self.assertEqual(fake.chats[1]['options']['num_gpu'], 0)

    def test_a_timeout_on_the_card_is_also_retried_on_the_processor(self):
        fake = self.use(FakeOllama(['ornith-1.5:9b'], chat_answers=[httpx.ReadTimeout('lent')]))
        self.assertEqual(run(vision.describe(PNG))['processor'], 'processeur')
        self.assertEqual(len(fake.chats), 2)

    def test_a_missing_model_is_not_retried(self):
        fake = self.use(FakeOllama(['ornith-1.5:9b'], chat_answers=[404]))
        with self.assertRaises(vision.VisionError) as err:
            run(vision.describe(PNG))
        self.assertIn('non installé', str(err.exception))
        self.assertEqual(len(fake.chats), 1)

    def test_both_tries_failing_is_said(self):
        self.use(FakeOllama(['ornith-1.5:9b'], chat_answers=[500, 500]))
        with self.assertRaises(vision.VisionError) as err:
            run(vision.describe(PNG))
        self.assertIn('Lecture de l’image impossible', str(err.exception))

    def test_an_empty_answer_is_not_a_description(self):
        self.use(FakeOllama(['ornith-1.5:9b'], reply='<think>rien vu</think>'))
        with self.assertRaises(vision.VisionError) as err:
            run(vision.describe(PNG))
        self.assertIn('n’a rien répondu', str(err.exception))

    def test_images_refused_before_any_model(self):
        fake = self.use(FakeOllama(['ornith-1.5:9b']))
        for image, said in ((b'', 'Image vide'), (b'GIF89a' + b'\x00' * 20, 'PNG ou JPEG seulement')):
            with self.assertRaises(vision.VisionError) as err:
                run(vision.describe(image))
            self.assertIn(said, str(err.exception))
        with mock.patch.object(vision, 'MAX_IMAGE_BYTES', 10), self.assertRaises(vision.VisionError) as err:
            run(vision.describe(PNG))
        self.assertIn('trop lourde', str(err.exception))
        self.assertEqual(fake.chats, [])

    def test_ollama_stopped_is_said(self):
        def down(request):
            raise httpx.ConnectError('refusé')
        patch = mock.patch('aurora.providers.ollama.ollama', OllamaProvider(transport=httpx.MockTransport(down)))
        patch.start()
        self.addCleanup(patch.stop)
        self.assertEqual(run(vision.status())['reason'], 'Ollama local arrêté')
        with self.assertRaises(vision.VisionError) as err:
            run(vision.describe(PNG))
        self.assertIn('rien n’est envoyé ailleurs', str(err.exception))

    # -- small pieces
    def test_without_reasoning(self):
        self.assertEqual(vision.without_reasoning('<think>a</think> Réponse'), 'Réponse')
        self.assertEqual(vision.without_reasoning('brouillon sans début</think>\nRéponse'), 'Réponse')   # Ornith 1.5
        self.assertEqual(vision.without_reasoning('Réponse seule'), 'Réponse seule')

    def test_image_kind(self):
        self.assertEqual(vision.image_kind(PNG), 'png')
        self.assertEqual(vision.image_kind(JPEG), 'jpeg')
        self.assertIsNone(vision.image_kind(b'RIFF....WEBP'))


def png_chunks(data: bytes) -> dict[bytes, bytes]:
    """Chunks of a PNG, each CRC checked."""
    assert data.startswith(b'\x89PNG\r\n\x1a\n')
    pos, out = 8, {}
    while pos < len(data):
        size = struct.unpack('>I', data[pos:pos + 4])[0]
        tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + size]
        assert struct.unpack('>I', data[pos + 8 + size:pos + 12 + size])[0] == zlib.crc32(tag + body) & 0xFFFFFFFF
        out[tag] = body
        pos += 12 + size
    return out


class CaptureImageTests(unittest.TestCase):
    def test_png_holds_exactly_the_pixels(self):
        rgb = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
        chunks = png_chunks(vision.png(rgb))
        self.assertEqual(struct.unpack('>IIBBBBB', chunks[b'IHDR']), (3, 2, 8, 2, 0, 0, 0))
        rows = zlib.decompress(chunks[b'IDAT'])
        self.assertEqual(rows, b'\x00' + rgb[0].tobytes() + b'\x00' + rgb[1].tobytes())
        self.assertIn(b'IEND', chunks)

    def test_reduce_averages_whole_blocks(self):
        rgb = np.zeros((4, 3200, 3), dtype=np.uint8)
        rgb[:, 1::2] = 100                                  # columns 0, 100, 0, 100…: each pair averages to 50
        small = vision.reduce(rgb)
        self.assertEqual(small.shape, (2, 1600, 3))
        self.assertTrue((small == 50).all())

    def test_a_4k_screen_becomes_1280_wide_and_a_small_one_is_kept(self):
        self.assertEqual(vision.reduce(np.zeros((6, 3840, 3), dtype=np.uint8)).shape, (2, 1280, 3))
        small = np.zeros((10, 800, 3), dtype=np.uint8)
        self.assertIs(vision.reduce(small), small)

    def test_the_capture_is_only_made_on_windows(self):
        with mock.patch.object(vision.sys, 'platform', 'linux'), self.assertRaises(vision.VisionError) as err:
            vision.capture_screen()
        self.assertIn('Windows', str(err.exception))


class VisionRouteTests(ApiTestCase):
    SEEN = {'text': 'Un globe.', 'model': 'ornith-1.5:9b', 'seconds': 2.5, 'processor': 'carte graphique',
            'truncated': False}

    def patch(self, name, **kw):
        p = mock.patch.object(vision, name, **kw)
        started = p.start()
        self.addCleanup(p.stop)
        return started

    def test_a_joined_image_is_read_and_described(self):
        describe = self.patch('describe', new=mock.AsyncMock(return_value=self.SEEN))
        res = self.client.post('/api/vision/describe', content=JPEG, headers=OCTETS)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), self.SEEN)
        describe.assert_awaited_once_with(JPEG)

    def test_what_cannot_be_read_is_said(self):
        self.patch('describe', new=mock.AsyncMock(side_effect=vision.VisionError('Format d’image non lu : PNG ou JPEG seulement.')))
        res = self.client.post('/api/vision/describe', content=b'GIF89a', headers=OCTETS)
        self.assertEqual(res.status_code, 422)
        self.assertIn('PNG ou JPEG seulement', res.json()['detail'])

    def test_a_too_heavy_image_is_refused_before_the_model(self):
        describe = self.patch('describe', new=mock.AsyncMock(return_value=self.SEEN))
        self.patch('MAX_IMAGE_BYTES', new=10)
        res = self.client.post('/api/vision/describe', content=PNG, headers=OCTETS)
        self.assertEqual(res.status_code, 413)
        describe.assert_not_awaited()

    def test_the_screen_is_captured_read_and_shown_back(self):
        capture = self.patch('capture_screen', return_value=(PNG, 3840, 2160))
        describe = self.patch('describe', new=mock.AsyncMock(return_value=self.SEEN))
        res = self.client.post('/api/vision/screen')
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual((data['width'], data['height'], data['text']), (3840, 2160, 'Un globe.'))
        self.assertTrue(data['image'].startswith('data:image/png;base64,'))
        self.assertEqual(base64.b64decode(data['image'].split(',', 1)[1]), PNG)   # exactly what was read
        capture.assert_called_once_with()
        describe.assert_awaited_once_with(PNG, vision.SCREEN_QUESTION)

    def test_a_refused_capture_is_said(self):
        self.patch('capture_screen', side_effect=vision.VisionError('Capture de l’écran refusée par Windows.'))
        res = self.client.post('/api/vision/screen')
        self.assertEqual(res.status_code, 422)
        self.assertIn('refusée par Windows', res.json()['detail'])

    def test_no_link_or_image_tag_can_capture_the_screen(self):
        capture = self.patch('capture_screen', return_value=(PNG, 1, 1))
        self.assertEqual(self.client.get('/api/vision/screen').status_code, 405)    # <img src> and links are GET
        self.assertIn(self.client.post('/api/vision/screen', headers=NO_TOKEN).status_code, (401, 403))
        capture.assert_not_called()

    def test_the_token_is_required(self):
        self.patch('status', new=mock.AsyncMock(return_value={'available': True, 'model': 'ornith-1.5:9b', 'reason': ''}))
        self.assertEqual(self.client.get('/api/vision').json()['model'], 'ornith-1.5:9b')
        self.assertIn(self.client.get('/api/vision', headers=NO_TOKEN).status_code, (401, 403))
        self.assertIn(self.client.post('/api/vision/describe', content=PNG, headers={**OCTETS, **NO_TOKEN}).status_code,
                      (401, 403))


if __name__ == '__main__':
    unittest.main()
