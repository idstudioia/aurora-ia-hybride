"""Voix D3: local speech recognition through Athéna's Whisper service (providers/whisper_local.py, /api/stt).

Athéna's real service is never started here: its folder is a temporary one with stand-in files, and its address
points to a small fake HTTP server that answers like whisper-service.py (empty POST refused, JSON otherwise).
"""
import base64
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from aurora.providers import whisper_local
from tests.test_api import NO_TOKEN, ApiTestCase


class FakeWhisper:
    """Answers like Athéna's service: 413 for an empty body, its JSON for a real one (or `status` when set)."""

    def __init__(self):
        self.received: list[dict] = []
        self.status = 200
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0:
                    self.send_error(413)
                    return
                fake.received.append(json.loads(self.rfile.read(size)))
                if fake.status != 200:
                    self.send_error(fake.status, 'Transcription impossible')
                    return
                body = json.dumps({'texte': ' Situe Pétra ', 'secondes': 0.4567, 'dureeAudio': 1.2,
                                   'moteur': 'Whisper base CPU int8'}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/transcrire'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def service_folder(root: Path) -> Path:
    """Stand-ins for Athéna's script, venv and model (never run: the fake server answers in their place)."""
    (root / 'whisper-audit-venv' / 'Scripts').mkdir(parents=True)
    (root / 'whisper-audit-venv' / 'Scripts' / 'python.exe').write_bytes(b'')
    (root / 'whisper-service.py').write_text('# stand-in', encoding='utf-8')
    (root / 'whisper-models' / 'models--Systran--faster-whisper-base').mkdir(parents=True)
    return root


class WhisperRouteTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = service_folder(Path(self.tmp.name) / 'outputs')
        self.fake = FakeWhisper()
        self.env_patch = mock.patch.dict(os.environ, {'AURORA_WHISPER_DIR': str(self.folder)})
        self.url_patch = mock.patch.object(whisper_local, 'URL', self.fake.url)
        self.popen = mock.patch.object(whisper_local.subprocess, 'Popen', side_effect=AssertionError('lancé'))
        for p in (self.env_patch, self.url_patch, self.popen):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.fake.close)
        self.addCleanup(self.tmp.cleanup)
        whisper_local._proc = None

    def test_the_audio_goes_to_the_local_service_with_athenas_vocabulary(self):
        audio = b'\x1aE\xdf\xa3 un vrai webm'
        res = self.client.post('/api/stt', content=audio, headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {'text': 'Situe Pétra', 'seconds': 0.46, 'audio_seconds': 1.2,
                                      'engine': whisper_local.ENGINE})
        self.assertEqual(len(self.fake.received), 1)
        self.assertEqual(base64.b64decode(self.fake.received[0]['audio']), audio)
        self.assertIs(self.fake.received[0]['vocabulaire'], True)     # Athéna's setting, its own prompt

    def test_status_says_it_runs_and_who_started_it(self):
        res = self.client.get('/api/stt')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {'available': True, 'running': True, 'started_by_aurora': False,
                                      'engine': whisper_local.ENGINE, 'local': True, 'reason': ''})
        self.assertEqual(self.client.post('/api/stt/prepare').json()['running'], True)

    def test_a_service_that_cannot_read_the_sound_is_said_plainly(self):
        self.fake.status = 422
        res = self.client.post('/api/stt', content=b'abc', headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(res.status_code, 503)
        self.assertIn('n’a pas pu lire ce son (HTTP 422)', res.json()['detail'])

    def test_empty_and_too_long_recordings_are_refused_before_the_service(self):
        empty = self.client.post('/api/stt', content=b'', headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(empty.status_code, 503)
        self.assertIn('Aucun son', empty.json()['detail'])
        with mock.patch.object(whisper_local, 'MAX_AUDIO_BYTES', 10):
            big = self.client.post('/api/stt', content=b'x' * 11, headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(big.status_code, 413)
        self.assertEqual(self.fake.received, [])

    def test_the_routes_need_the_token(self):
        self.assertIn(self.client.get('/api/stt', headers=NO_TOKEN).status_code, (401, 403))
        self.assertIn(self.client.post('/api/stt', content=b'abc', headers=NO_TOKEN).status_code, (401, 403))
        self.assertEqual(self.fake.received, [])

    def test_without_athenas_folder_it_is_unavailable_and_nothing_starts(self):
        with mock.patch.dict(os.environ, {'AURORA_WHISPER_DIR': str(Path(self.tmp.name) / 'absent')}):
            status = self.client.get('/api/stt').json()
            self.assertFalse(status['available'])
            self.assertIn('introuvable', status['reason'])
            res = self.client.post('/api/stt', content=b'abc', headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(res.status_code, 503)
        self.assertIn('Reconnaissance locale indisponible', res.json()['detail'])
        self.assertEqual(self.fake.received, [])

    def test_without_the_model_nothing_is_downloaded(self):
        (self.folder / 'whisper-models' / 'models--Systran--faster-whisper-base').rmdir()
        status = self.client.get('/api/stt').json()
        self.assertFalse(status['available'])
        self.assertIn('rien n’est téléchargé sans ton accord', status['reason'])


class WhisperStartTests(unittest.IsolatedAsyncioTestCase):
    """When nothing answers, AURORA starts Athéna's script with its own venv, hidden, and stops only its own run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = service_folder(Path(self.tmp.name) / 'outputs')
        for p in (mock.patch.dict(os.environ, {'AURORA_WHISPER_DIR': str(self.folder)}),
                  mock.patch.object(whisper_local, 'URL', 'http://127.0.0.1:9/transcrire'),   # nothing listens there
                  mock.patch.object(whisper_local, 'START_TIMEOUT_S', 1.0)):
            p.start()
            self.addCleanup(p.stop)
        whisper_local._proc = None
        self.addCleanup(setattr, whisper_local, '_proc', None)

    async def test_a_service_that_stops_at_once_is_reported(self):
        proc = mock.Mock(pid=1234, returncode=1)
        proc.poll.return_value = 1
        with mock.patch.object(whisper_local.subprocess, 'Popen', return_value=proc) as popen:
            with self.assertRaisesRegex(whisper_local.WhisperError, 'arrêté au démarrage'):
                await whisper_local.ensure_started()
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [str(self.folder / 'whisper-audit-venv' / 'Scripts' / 'python.exe'),
                                   str(self.folder / 'whisper-service.py')])
        self.assertEqual(kwargs['cwd'], str(self.folder))
        self.assertEqual(kwargs['creationflags'], getattr(whisper_local.subprocess, 'CREATE_NO_WINDOW', 0))

    async def test_a_service_that_never_answers_times_out(self):
        proc = mock.Mock(pid=1234, returncode=None)
        proc.poll.return_value = None
        with mock.patch.object(whisper_local.subprocess, 'Popen', return_value=proc):
            with self.assertRaisesRegex(whisper_local.WhisperError, 'ne répond pas'):
                await whisper_local.ensure_started()
        whisper_local.stop()                          # AURORA's own run: stopped
        proc.terminate.assert_called_once()

    def test_stop_leaves_a_service_aurora_did_not_start(self):
        whisper_local._proc = None
        with mock.patch.object(whisper_local.subprocess, 'Popen') as popen:
            whisper_local.stop()
        popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
