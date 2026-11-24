"""AURORA's own Google connection (tools/youtube_comments.py, /api/youtube/*), to post comments later.
Google sends the browser back to the interface (/youtube-retour), which gives the code to the service with its
session token: every route stays locked (tests.test_api.SessionTests).

No network and no real vault: Google's token address and the YouTube API go through httpx.MockTransport
(tests.test_tools.MockHttp), the Windows vault is a dict, the database is temporary.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient

from aurora import config
from aurora.tools import studio
from aurora.tools import youtube_comments as yt
from aurora.tools.base import ToolError
from tests.doubles_core import FakeDecision, TempEnv, build
from tests.test_tools import MockHttp, no_network

ORIGIN = 'http://127.0.0.1:5173'
BASE = 'http://127.0.0.1:8765'
CLIENT_ID = '123456789012-abcdefghijklmnop0123456789abcdef.apps.googleusercontent.com'
CLIENT_SECRET = 'GOCSPX-' + 's' * 28
REFRESH = '1//0g' + 'r' * 60
ACCESS = 'ya29.acces-de-test'
CHANNEL = 'UCexemple0000000000000AA'
RETURN = 'http://127.0.0.1:5173/youtube-retour'
CODE = '4/0AbCdEf-code-de-test'
REFRESH_TARGET = studio.SECRETS['youtube_refresh'][0]

# 23/11/2026: the Studio no longer ships with ID Studio IA's own channel as its default. These tests are about a person whose
# Studio names the channel they connect (an invented one): it is given here as the default of this file.
_studio_channel = mock.patch.dict(studio.DEFAULTS, {'youtube_channel': CHANNEL})


def setUpModule():
    _studio_channel.start()


def tearDownModule():
    _studio_channel.stop()


def run(coro):
    return asyncio.run(coro)


class FakeVault:
    """The Windows vault as a dict (AURORA_VAULT=1, so studio.secret reads it)."""

    def __init__(self, stored: dict | None = None):
        self.stored = dict(stored or {})

    def __enter__(self):
        from aurora.security import vault
        self._patches = [
            mock.patch.dict(os.environ, {'AURORA_VAULT': '1', 'AURORA_OFFLINE': '0'}),
            mock.patch.object(vault, 'write', side_effect=lambda secret, target, user='aurora': self.stored.update({target: secret})),
            mock.patch.object(vault, 'read', side_effect=lambda target: self.stored.get(target)),
            mock.patch.object(vault, 'delete', side_effect=lambda target: self.stored.pop(target, None) is not None),
            mock.patch.object(vault, 'available', return_value=True)]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()


def with_client(**extra) -> FakeVault:
    return FakeVault({studio.SECRETS['youtube_client_id'][0]: CLIENT_ID,
                      studio.SECRETS['youtube_client_secret'][0]: CLIENT_SECRET, **extra})


class Google:
    """Google's token and revoke addresses and YouTube's channels.list; keeps the forms it received."""

    def __init__(self, *, scope: str = yt.SCOPE, refresh: str | None = REFRESH, channel: str | None = CHANNEL,
                 token_status: int = 200, error: str | None = None, revoke_status: int = 200):
        self.scope, self.refresh, self.channel = scope, refresh, channel
        self.token_status, self.error, self.revoke_status = token_status, error, revoke_status
        self.forms: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL) or url.startswith(yt.REVOKE_URL):
            assert request.method == 'POST'
            form = {k: v[0] for k, v in parse_qs(request.read().decode()).items()}
            self.forms.append((url, form))
            if url.startswith(yt.REVOKE_URL):
                return httpx.Response(self.revoke_status, json={})
            if self.token_status >= 400:
                return httpx.Response(self.token_status, json={'error': self.error})
            body = {'access_token': ACCESS, 'expires_in': 3599, 'scope': self.scope, 'token_type': 'Bearer'}
            if self.refresh:
                body['refresh_token'] = self.refresh
            return httpx.Response(200, json=body)
        if request.url.path == '/youtube/v3/channels':
            assert request.headers.get('authorization') == f'Bearer {ACCESS}'
            assert 'x-goog-api-key' not in request.headers   # the Google access, not the API key
            assert request.url.params.get('mine') == 'true'
            items = [{'id': self.channel, 'snippet': {'title': '<b>ID Studio</b>'}}] if self.channel else []
            return httpx.Response(200, json={'items': items})
        raise AssertionError(f'adresse inattendue : {url}')


def state_of(link: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}


class YoutubeConnectionTests(unittest.TestCase):
    def setUp(self):
        logging.getLogger('aurora').setLevel(logging.CRITICAL)
        self.env = TempEnv().__enter__()
        yt._pending.clear()

    def tearDown(self):
        yt._pending.clear()
        self.env.__exit__(None, None, None)

    def test_no_oauth_client_no_link(self):
        with FakeVault(), MockHttp(no_network):
            with self.assertRaises(ToolError) as caught:
                yt.start_connection(ORIGIN)
            self.assertIn('ID client', caught.exception.message_fr)
            self.assertFalse(yt.status(ORIGIN)['client'])

    def test_only_the_interface_may_ask_for_a_link(self):
        with with_client(), MockHttp(no_network):
            for origin in (None, '', 'null', 'https://exemple.com', 'http://127.0.0.1:9999'):
                with self.subTest(origin=origin), self.assertRaises(ToolError) as caught:
                    yt.start_connection(origin)
                self.assertIn('interface', caught.exception.message_fr)
            self.assertEqual(yt._pending, {})
            self.assertIsNone(yt.status(None)['redirect_uri'])
            self.assertEqual(yt.status('http://127.0.0.1:5173/')['redirect_uri'], RETURN)

    def test_the_link_asks_one_scope_with_a_single_use_state(self):
        with with_client(), MockHttp(no_network):
            got = yt.start_connection(ORIGIN)
        parts = urlsplit(got['url'])
        query = state_of(got['url'])
        self.assertEqual((parts.scheme, parts.netloc), ('https', 'accounts.google.com'))
        self.assertEqual(query['scope'], 'https://www.googleapis.com/auth/youtube.force-ssl')
        self.assertEqual(query['redirect_uri'], RETURN)
        self.assertEqual(got['redirect_uri'], query['redirect_uri'])
        self.assertEqual((query['access_type'], query['prompt'], query['code_challenge_method']),
                         ('offline', 'select_account consent', 'S256'))
        self.assertEqual(query['client_id'], CLIENT_ID)
        self.assertNotIn(CLIENT_SECRET, got['url'])   # the secret only goes to Google's token address
        self.assertGreaterEqual(len(query['state']), 40)
        self.assertIn(query['state'], yt._pending)

    def test_the_return_keeps_the_access_in_the_vault_and_the_channel(self):
        google = Google()
        with with_client() as vault, MockHttp(google):
            query = state_of(yt.start_connection(ORIGIN)['url'])
            done = run(yt.finish_connection(query['state'], CODE))
            self.assertTrue(done['connected'])
            self.assertEqual(done['channel_id'], CHANNEL)
            self.assertEqual(done['channel_title'], '<b>ID Studio</b>')   # kept as text; the page escapes it
            self.assertIs(done['same_channel'], True)                      # the channel of Paramètres › Studio
            self.assertEqual(vault.stored[REFRESH_TARGET], REFRESH)
            self.assertNotIn(REFRESH, str(done))
            (url, form), = google.forms
            self.assertEqual(url, yt.TOKEN_URL)
            self.assertEqual((form['code'], form['grant_type'], form['client_secret']),
                             (CODE, 'authorization_code', CLIENT_SECRET))
            self.assertEqual(form['redirect_uri'], RETURN)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'].encode()).digest()).rstrip(b'=')
            self.assertEqual(challenge.decode(), query['code_challenge'])   # PKCE: the verifier of this link
            with self.assertRaises(ToolError) as again:   # single use
                run(yt.finish_connection(query['state'], CODE))
            self.assertIn('rien n’a été enregistré', again.exception.message_fr)
            self.assertEqual(len(google.forms), 1)

    def test_nothing_is_kept_when_google_or_the_person_says_no(self):
        # (Google, call, said, access withdrawn at once): an access AURORA will not keep never stays alive.
        cases = [
            ({}, {'error': 'access_denied'}, 'refusé', None),
            ({'scope': 'https://www.googleapis.com/auth/youtube.readonly'}, {}, 'permission YouTube demandée', REFRESH),
            ({'refresh': None}, {}, 'accès durable', ACCESS),
            ({'token_status': 401, 'error': 'invalid_client'}, {}, 'ID client ou le code secret', None),
            ({'token_status': 400, 'error': 'invalid_grant'}, {}, 'expiré', None),
            ({'channel': None}, {}, 'pas de chaîne YouTube', REFRESH),
        ]
        for google_args, call_args, said, withdrawn in cases:
            google = Google(**google_args)
            with self.subTest(said=said), with_client() as vault, MockHttp(google):
                query = state_of(yt.start_connection(ORIGIN)['url'])
                with self.assertRaises(ToolError) as caught:
                    run(yt.finish_connection(query['state'], CODE, **call_args))
                self.assertIn(said, caught.exception.message_fr)
                self.assertNotIn(REFRESH_TARGET, vault.stored)
                self.assertFalse(yt.status(ORIGIN)['connected'])
                revokes = [form for url, form in google.forms if url == yt.REVOKE_URL]
                self.assertEqual(revokes, [{'token': withdrawn}] if withdrawn else [])

    def test_vault_and_settings_change_together(self):
        google = Google()
        old = 'ancien-acces-' + 'a' * 30
        with with_client(**{REFRESH_TARGET: old}) as vault, MockHttp(google), \
                mock.patch.object(yt, '_save_account', side_effect=OSError('base verrouillée')):
            query = state_of(yt.start_connection(ORIGIN)['url'])
            with self.assertRaises(ToolError) as caught:
                run(yt.finish_connection(query['state'], CODE))
            self.assertIn('réglages', caught.exception.message_fr)
            self.assertEqual(vault.stored[REFRESH_TARGET], old)                        # put back as it was
            self.assertEqual(google.forms[-1], (yt.REVOKE_URL, {'token': REFRESH}))    # the new one withdrawn

    def test_a_changed_client_id_is_said(self):
        with with_client() as vault, MockHttp(no_network):
            query = state_of(yt.start_connection(ORIGIN)['url'])
            vault.stored[studio.SECRETS['youtube_client_id'][0]] = '999999999999-zzzzzzzzzzzzzzzz.apps.googleusercontent.com'
            with self.assertRaises(ToolError) as caught:
                run(yt.finish_connection(query['state'], CODE))
            self.assertIn('ID client a changé', caught.exception.message_fr)

    def test_an_unknown_or_expired_state_calls_nobody(self):
        with with_client(), MockHttp(no_network):
            with self.assertRaises(ToolError):
                run(yt.finish_connection('inconnu', CODE))
            with self.assertRaises(ToolError):
                run(yt.finish_connection('', CODE))
            query = state_of(yt.start_connection(ORIGIN)['url'])
            yt._pending[query['state']]['expires'] = 0.0
            with self.assertRaises(ToolError) as caught:
                run(yt.finish_connection(query['state'], CODE))
            self.assertIn('10 minutes', caught.exception.message_fr)

    def test_disconnect_forgets_the_access_then_asks_google(self):
        for revoke_status, revoked in ((200, True), (500, False)):
            google = Google(revoke_status=revoke_status)
            with self.subTest(revoke=revoke_status), with_client() as vault, MockHttp(google):
                query = state_of(yt.start_connection(ORIGIN)['url'])
                run(yt.finish_connection(query['state'], CODE))
                done = run(yt.disconnect())
                self.assertIs(done['revoked'], revoked)
                self.assertEqual(done['note'] is None, revoked)   # « retirez AURORA vous-même » when not confirmed
                self.assertFalse(yt.status(ORIGIN)['connected'])
                self.assertEqual(google.forms[-1], (yt.REVOKE_URL, {'token': REFRESH}))
                self.assertNotIn(REFRESH_TARGET, vault.stored)
                self.assertTrue(yt.status(ORIGIN)['client'])   # the OAuth client stays, to reconnect in one click


class YoutubeConnectionApiTests(unittest.TestCase):
    def setUp(self):
        logging.getLogger('aurora').setLevel(logging.CRITICAL)
        self.env = TempEnv().__enter__()
        yt._pending.clear()
        from aurora import main
        from aurora.core import orchestrator as orch_module
        self.orch_module = orch_module
        orch, *_ = build(FakeDecision('chat.general'))
        orch_module.set_orchestrator(orch)
        self.client = TestClient(main.app, base_url=BASE, raise_server_exceptions=False)
        self.client.__enter__()
        token = self.client.get('/api/bootstrap', headers={'Origin': ORIGIN}).json()['token']
        self.client.headers.update({'X-Aurora-Token': token, 'Origin': ORIGIN})
        config.update_settings({'mode': 'equilibre'})

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.orch_module.set_orchestrator(None)
        yt._pending.clear()
        self.env.__exit__(None, None, None)

    def test_the_oauth_client_goes_to_the_vault_and_is_never_sent_back(self):
        with FakeVault() as vault, MockHttp(no_network):
            self.assertFalse(self.client.get('/api/secrets/youtube_client_id').json()['configured'])
            bad = self.client.put('/api/secrets/youtube_client_id', json={'token': 'pas-un-identifiant-google'})
            self.assertEqual(bad.status_code, 422)
            self.assertIn('googleusercontent', bad.json()['detail'])
            saved = self.client.put('/api/secrets/youtube_client_id', json={'token': CLIENT_ID})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertNotIn(CLIENT_ID, saved.text)
            saved = self.client.put('/api/secrets/youtube_client_secret', json={'token': CLIENT_SECRET})
            self.assertTrue(saved.json()['configured'])
            self.assertNotIn(CLIENT_SECRET, saved.text)
            self.assertEqual(set(vault.stored), {studio.SECRETS['youtube_client_id'][0],
                                                 studio.SECRETS['youtube_client_secret'][0]})
            # The lasting access is written by the service only, never through these routes.
            self.assertEqual(self.client.put('/api/secrets/youtube_refresh', json={'token': REFRESH}).status_code, 422)
            self.assertEqual(self.client.get('/api/secrets/autre').status_code, 422)
            self.assertTrue(self.client.get('/api/youtube/etat').json()['client'])
            self.assertFalse(self.client.delete('/api/secrets/youtube_client_secret').json()['configured'])

    def test_connection_through_the_routes(self):
        google = Google()
        back = {'state': '', 'code': CODE, 'error': ''}
        with with_client(), MockHttp(google):
            for method, path in (('GET', '/api/youtube/etat'), ('POST', '/api/youtube/connexion'),
                                 ('POST', '/api/youtube/retour'), ('DELETE', '/api/youtube/connexion')):
                refused = self.client.request(method, path, json=back if path.endswith('retour') else None,
                                              headers={'X-Aurora-Token': ''})
                self.assertEqual(refused.status_code, 401, path)   # every route needs the session token
            before = self.client.get('/api/youtube/etat').json()
            self.assertEqual((before['client'], before['connected']), (True, False))
            self.assertEqual(before['redirect_uri'], RETURN)
            link = self.client.post('/api/youtube/connexion')
            self.assertEqual(link.status_code, 200, link.text)
            query = state_of(link.json()['url'])
            self.assertEqual(query['redirect_uri'], RETURN)
            self.assertEqual(self.client.post('/api/youtube/connexion', headers={'Origin': 'https://exemple.com'}
                                              ).status_code, 409)
            extra = self.client.post('/api/youtube/retour', json={**back, 'state': query['state'], 'scope': 'x'})
            self.assertEqual(extra.status_code, 422)   # only state, code and error are taken
            done = self.client.post('/api/youtube/retour', json={**back, 'state': query['state']})
            self.assertEqual(done.status_code, 200, done.text)
            self.assertEqual((done.json()['connected'], done.json()['channel_id']), (True, CHANNEL))
            self.assertNotIn(REFRESH, done.text)
            replay = self.client.post('/api/youtube/retour', json={**back, 'state': query['state']})
            self.assertEqual(replay.status_code, 400)
            self.assertIn('rien n’a été enregistré', replay.json()['detail'])
            self.assertTrue(self.client.get('/api/youtube/etat').json()['connected'])
            gone = self.client.delete('/api/youtube/connexion')
            self.assertEqual(gone.status_code, 200, gone.text)
            self.assertEqual((gone.json()['connected'], gone.json()['revoked']), (False, True))

    def test_a_silent_google_is_not_a_refusal(self):
        def unreachable(request):
            raise httpx.ConnectError('injoignable', request=request)
        with with_client(), MockHttp(unreachable):
            query = state_of(self.client.post('/api/youtube/connexion').json()['url'])
            got = self.client.post('/api/youtube/retour', json={'state': query['state'], 'code': CODE, 'error': ''})
            self.assertEqual(got.status_code, 503)
            self.assertIn('injoignable', got.json()['detail'])

    def test_private_mode_does_not_connect(self):
        config.update_settings({'mode': 'prive'})
        with with_client(**{REFRESH_TARGET: REFRESH}) as vault, MockHttp(no_network):
            gone = self.client.delete('/api/youtube/connexion')   # forgets the access without calling Google
            self.assertEqual(gone.status_code, 200, gone.text)
            self.assertEqual((gone.json()['connected'], gone.json()['revoked']), (False, False))
            self.assertIn('myaccount.google.com', gone.json()['note'])
            self.assertNotIn(REFRESH_TARGET, vault.stored)
        with with_client(), MockHttp(no_network):
            refused = self.client.post('/api/youtube/connexion')
            self.assertEqual(refused.status_code, 409)
            self.assertIn('Mode Privé', refused.json()['detail'])
            back = self.client.post('/api/youtube/retour', json={'state': 'x', 'code': CODE, 'error': ''})
            self.assertEqual(back.status_code, 409)
            self.assertIn('Mode Privé', back.json()['detail'])


# --------------------------------------------------------------------------- posting a comment (9/11/2026)

THREAD = 'UgxCommentaireDeTest123'
NEWEST = 'zyxwvutsrqp'   # « Deuxième vidéo », the newest of tests.test_studio.FEED


class YouTube(Google):
    """Google (token refresh) and YouTube: the public feed, commentThreads.insert and .list. Keeps what was posted."""

    def __init__(self, *, refresh_error: str | None = None, insert_status: int = 200, reason: str | None = None,
                 shown_text: str | None = None, hidden: bool = False, hidden_reads: int = 0, **google):
        super().__init__(**google)
        self.refresh_error, self.insert_status, self.reason = refresh_error, insert_status, reason
        self.shown_text, self.hidden, self.hidden_reads = shown_text, hidden, hidden_reads
        self.posted: list[dict] = []
        self.refreshes = 0
        self.reads = 0   # commentThreads.list?id= : the first ``hidden_reads`` find nothing (shown a bit later)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        from tests.test_studio import FEED
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            form = {k: v[0] for k, v in parse_qs(request.read().decode()).items()}
            if form.get('grant_type') == 'refresh_token':
                self.forms.append((url, form))
                self.refreshes += 1
                if self.refresh_error:
                    return httpx.Response(400, json={'error': self.refresh_error})
                return httpx.Response(200, json={'access_token': ACCESS, 'expires_in': 3599, 'token_type': 'Bearer'})
        if url.startswith(studio.YOUTUBE_FEED):
            assert request.url.params.get('channel_id') == CHANNEL
            return httpx.Response(200, content=FEED, headers={'Content-Type': 'application/atom+xml'})
        if request.url.path == '/youtube/v3/commentThreads':
            assert request.headers.get('authorization') == f'Bearer {ACCESS}'
            assert 'x-goog-api-key' not in request.headers
            if request.method == 'POST':
                assert request.url.params.get('part') == 'snippet'
                body = json.loads(request.read().decode())
                self.posted.append(body)
                if self.insert_status >= 400:
                    return httpx.Response(self.insert_status, json={'error': {
                        'code': self.insert_status, 'message': 'refus', 'errors': [{'reason': self.reason}]}})
                return httpx.Response(200, json={'id': THREAD, 'snippet': body['snippet']})
            assert request.url.params.get('id') == THREAD
            self.reads += 1
            if self.hidden or self.reads <= self.hidden_reads:
                return httpx.Response(200, json={'items': []})
            text = self.shown_text or self.posted[-1]['snippet']['topLevelComment']['snippet']['textOriginal']
            return httpx.Response(200, json={'items': [{'id': THREAD, 'snippet': {
                'videoId': self.posted[-1]['snippet']['videoId'],
                'topLevelComment': {'id': THREAD, 'snippet': {'textOriginal': text, 'textDisplay': text,
                                                              'authorChannelId': {'value': CHANNEL}}}}}]})
        return super().__call__(request)


def connected(**extra) -> FakeVault:
    """The OAuth client and a lasting access in the vault, the channel remembered in the settings."""
    yt._save_account({'channel_id': CHANNEL, 'title': 'ID Studio', 'connected_at': '2026-11-09T10:00:00+01:00'})
    return with_client(**{REFRESH_TARGET: REFRESH, **extra})


def grant(fingerprint: str) -> int:
    from aurora.security import consent
    item = consent.create_request(task_id=None, step_id=None, action='youtube.comment', title='Commentaire',
                                  preview={'fingerprint': fingerprint})
    consent.decide(int(item['id']), 'grant')
    return int(item['id'])


class YoutubeCommentToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        logging.getLogger('aurora').setLevel(logging.CRITICAL)
        self.env = TempEnv().__enter__()
        yt._forget_access()
        config.update_settings({'mode': 'equilibre'})

    def tearDown(self):
        yt._forget_access()
        self.env.__exit__(None, None, None)

    async def comment(self, **params):
        from aurora.tools.base import run_tool
        return await run_tool('youtube.comment', params)

    async def test_the_card_is_the_newest_video_and_the_words_given(self):
        youtube = YouTube()
        with connected(), MockHttp(youtube):
            dry = await self.comment(text='  Merci   à tous !  ', dry_run=True)
        self.assertTrue(dry.ok, dry.summary)
        card = dry.data
        self.assertEqual((card['video_id'], card['video_title']), (NEWEST, 'Deuxième vidéo'))
        self.assertEqual((card['channel_id'], card['channel_title'], card['same_channel']), (CHANNEL, 'ID Studio', True))
        self.assertEqual((card['text'], card['drafted_by'], card['auto']), ('Merci à tous !', 'user', False))
        self.assertEqual(len(card['fingerprint']), 24)
        self.assertIn('Publier ce commentaire public sous la vidéo « Deuxième vidéo »', card['does'])
        self.assertIn('pas encore fait', card['undo'])   # the withdrawal by AURORA is said as not done
        self.assertEqual((youtube.posted, youtube.refreshes), ([], 0))   # a card posts nothing, needs no access

    async def test_the_local_model_writes_it_when_no_words_are_given(self):
        from aurora.providers import ollama as ollama_module
        from aurora.providers.base import ProviderResult
        reply = ProviderResult(text='<think>brouillon</think>Commentaire : « Merci d’être là, à très vite ! »',
                               provider='ollama', model='qwen3.5:9b')
        chat = mock.AsyncMock(return_value=reply)
        with connected(), MockHttp(YouTube()), \
                mock.patch.object(ollama_module.ollama, 'available', mock.AsyncMock(return_value=True)), \
                mock.patch.object(ollama_module.ollama, 'chat', chat):
            dry = await self.comment(request='mets un commentaire sympa sous ma dernière vidéo', dry_run=True)
        self.assertTrue(dry.ok, dry.summary)
        self.assertEqual(dry.data['text'], 'Merci d’être là, à très vite !')
        self.assertEqual((dry.data['drafted_by'], dry.data['model']), ('local_model', 'qwen3.5:9b'))
        asked = chat.call_args
        self.assertIn('Deuxième vidéo', asked.args[0][0]['content'])
        self.assertIn('sympa', asked.args[0][0]['content'])
        self.assertIn('des données', asked.kwargs['system'])   # the title and the request are data, not orders
        self.assertIn('à la place de l’auteur de la vidéo', asked.kwargs['system'])   # the author's voice
        self.assertEqual(chat.await_count, 1)   # a short draft in the author's voice is kept as it is

    async def test_a_viewers_or_too_long_draft_is_asked_again_once(self):
        """9/11/2026, first real draft: « merci pour cette vidéo… j'ai apprécié… » under the author's own video."""
        from aurora.providers import ollama as ollama_module
        from aurora.providers.base import ProviderResult
        viewer = 'Merci pour cette vidéo, j’ai apprécié découvrir l’IA locale !'
        author = 'Dans cette vidéo, je vous parle de l’IA locale : posez vos questions en commentaire !'
        long = 'Bonjour à tous, dans cette vidéo je vous explique tout. ' * 6
        for first, second, reminder, kept in ((viewer, author, 'spectateur', author),
                                              (long, author, 'trop long', author),
                                              (viewer, viewer, 'spectateur', viewer)):   # once only: the card shows it
            replies = [ProviderResult(text=t, provider='ollama', model='granite4.2:8b') for t in (first, second)]
            chat = mock.AsyncMock(side_effect=replies)
            with self.subTest(first=first[:30], second=second[:30]), connected(), MockHttp(YouTube()), \
                    mock.patch.object(ollama_module.ollama, 'available', mock.AsyncMock(return_value=True)), \
                    mock.patch.object(ollama_module.ollama, 'chat', chat):
                dry = await self.comment(dry_run=True)
                self.assertTrue(dry.ok, dry.summary)
                self.assertEqual(dry.data['text'], kept)
                self.assertEqual(chat.await_count, 2)
                again = chat.await_args_list[1].args[0]
                self.assertEqual([m['role'] for m in again], ['user', 'assistant', 'user'])
                self.assertEqual(again[1]['content'], yt.clean_comment(first))
                self.assertIn(reminder, again[2]['content'])

    async def test_no_draft_without_the_local_model(self):
        from aurora.providers import ollama as ollama_module
        with connected(), MockHttp(YouTube()), \
                mock.patch.object(ollama_module.ollama, 'available', mock.AsyncMock(return_value=False)):
            dry = await self.comment(dry_run=True)
        self.assertFalse(dry.ok)
        self.assertIn('IA locale (Ollama) est arrêtée', dry.summary)

    async def test_nothing_without_a_connected_channel_or_in_private_mode(self):
        with with_client(), MockHttp(no_network):
            dry = await self.comment(text='Bonjour', dry_run=True)
        self.assertFalse(dry.ok)
        self.assertIn('Aucune chaîne YouTube n’est reliée', dry.summary)
        config.update_settings({'mode': 'prive'})
        with connected(), MockHttp(no_network):
            for params in ({'text': 'Bonjour', 'dry_run': True}, {'text': 'Bonjour', 'video_id': NEWEST}):
                res = await self.comment(**params)
                self.assertFalse(res.ok)
                self.assertIn('Mode Privé', res.summary)

    async def test_only_the_comment_of_the_granted_card_is_posted(self):
        youtube = YouTube()
        with connected(), MockHttp(youtube):
            fp = (await self.comment(text='Merci !', dry_run=True)).data['fingerprint']
            card = {'text': 'Merci !', 'video_id': NEWEST, 'video_title': 'Deuxième vidéo'}
            no_card = await self.comment(**card, approved_fingerprint=fp)
            self.assertIn('Autorisation absente', no_card.summary)
            other = await self.comment(**card, approved_fingerprint=fp, permission_id=grant('0' * 24))
            self.assertIn('Autorisation absente', other.summary)
            changed = await self.comment(**{**card, 'text': 'Merci beaucoup !'}, approved_fingerprint=fp,
                                         permission_id=grant(fp))
            self.assertIn('changé', changed.summary)
            moved = await self.comment(**{**card, 'video_id': 'abcdefghijk'}, approved_fingerprint=fp,
                                       permission_id=grant(fp))
            self.assertIn('changé', moved.summary)
            unbound = await self.comment(**card, permission_id=grant(fp))   # no fingerprint: not from a card
            self.assertIn('changé', unbound.summary)
            trial = await self.comment(**card, approved_fingerprint=fp, permission_id=grant(fp))
            self.assertIn('Instance d’essai : rien n’a été publié', trial.summary)   # a temporary AURORA folder
        self.assertEqual(youtube.posted, [])

    async def test_a_granted_comment_is_posted_then_read_back(self):
        from aurora.tools import pc_control
        youtube = YouTube()
        with connected(), MockHttp(youtube), mock.patch.object(pc_control, 'trial_instance', return_value=False):
            fp = (await self.comment(text='Merci à tous !', dry_run=True)).data['fingerprint']
            card = {'text': 'Merci à tous !', 'video_id': NEWEST, 'video_title': 'Deuxième vidéo',
                    'approved_fingerprint': fp}
            done = await self.comment(**card, permission_id=grant(fp))
            self.assertTrue(done.ok, done.summary)
            again = await self.comment(**card, permission_id=grant(fp))
        self.assertEqual(youtube.posted[0], {'snippet': {'videoId': NEWEST, 'topLevelComment': {
            'snippet': {'textOriginal': 'Merci à tous !'}}}})
        self.assertTrue(done.data['verified'])
        self.assertEqual(done.data['url'], f'https://www.youtube.com/watch?v={NEWEST}&lc={THREAD}')
        self.assertIn('relu sur YouTube', done.summary)
        (url, form), = youtube.forms   # one refresh of the access for both posts, kept in memory
        self.assertEqual((form['grant_type'], form['refresh_token'], form['client_secret']),
                         ('refresh_token', REFRESH, CLIENT_SECRET))
        self.assertTrue(again.ok)
        last = yt.last_comment()
        self.assertEqual((last['thread_id'], last['video_id'], last['text']), (THREAD, NEWEST, 'Merci à tous !'))
        self.assertNotIn(ACCESS, str(last))

    async def test_what_youtube_refuses_is_said(self):
        from aurora.tools import pc_control
        cases = [({'insert_status': 403, 'reason': 'commentsDisabled'}, 'désactivés', 'Rien n’a été publié'),
                 ({'insert_status': 403, 'reason': 'quotaExceeded'}, 'quota YouTube du jour', 'Rien n’a été publié'),
                 ({'insert_status': 401}, 'reconnectez-vous', 'Rien n’a été publié'),
                 ({'insert_status': 503}, 'erreur HTTP 503', 'Je ne sais pas si le commentaire est parti'),
                 ({'refresh_error': 'invalid_grant'}, '7 jours', 'reconnectez-vous')]
        for args, said, then in cases:
            yt._forget_access()
            youtube = YouTube(**args)
            with self.subTest(args=args), connected(), MockHttp(youtube), \
                    mock.patch.object(pc_control, 'trial_instance', return_value=False):
                fp = (await self.comment(text='Merci !', dry_run=True)).data['fingerprint']
                res = await self.comment(text='Merci !', video_id=NEWEST, approved_fingerprint=fp,
                                         permission_id=grant(fp))
                self.assertFalse(res.ok)
                self.assertIn(said, res.summary)
                self.assertIn(then, res.summary)
                self.assertIsNone(yt.last_comment())

    async def test_a_comment_not_found_again_is_said(self):
        from aurora.tools import pc_control
        waits = mock.AsyncMock()   # asyncio.sleep: the waits are counted, not spent
        for args, said, reads in (({'hidden': True}, 'ne le montrait toujours pas', 4),
                                  ({'shown_text': 'Autre texte'}, 'pas identique', 1)):   # shown: not read again
            youtube = YouTube(**args)
            waits.reset_mock()
            with self.subTest(args=args), connected(), MockHttp(youtube), \
                    mock.patch.object(pc_control, 'trial_instance', return_value=False), \
                    mock.patch.object(yt.asyncio, 'sleep', waits):
                fp = (await self.comment(text='Merci !', dry_run=True)).data['fingerprint']
                res = await self.comment(text='Merci !', video_id=NEWEST, approved_fingerprint=fp,
                                         permission_id=grant(fp))
                self.assertTrue(res.ok)   # YouTube took it: it is said, not hidden
                self.assertFalse(res.data['verified'])
                self.assertIn(said, res.summary)
                self.assertEqual(youtube.reads, reads)
                self.assertEqual([c.args[0] for c in waits.await_args_list], list(yt.READ_BACK_WAITS_S[1:reads]))

    async def test_a_comment_shown_a_few_seconds_later_is_verified(self):
        """9/11/2026, first real post: not shown at once, public a few seconds later."""
        from aurora.tools import pc_control
        youtube = YouTube(hidden_reads=2)
        waits = mock.AsyncMock()
        with connected(), MockHttp(youtube), mock.patch.object(pc_control, 'trial_instance', return_value=False), \
                mock.patch.object(yt.asyncio, 'sleep', waits):
            fp = (await self.comment(text='Merci !', dry_run=True)).data['fingerprint']
            res = await self.comment(text='Merci !', video_id=NEWEST, approved_fingerprint=fp, permission_id=grant(fp))
        self.assertTrue(res.ok, res.summary)
        self.assertTrue(res.data['verified'])
        self.assertIsNone(res.data['verify_note'])
        self.assertIn('relu sur YouTube', res.summary)
        self.assertEqual((youtube.reads, len(youtube.posted)), (3, 1))   # read three times, posted once
        self.assertEqual([c.args[0] for c in waits.await_args_list], [3, 5])

    def test_the_text_is_cleaned_as_youtube_shows_it(self):
        self.assertEqual(yt.clean_comment('  Bonjour​   à\ttous‮ !\r\n\r\n\r\n\r\nFin  '), 'Bonjour à tous !\n\nFin')
        self.assertEqual(yt.clean_comment('x' * 1500), 'x' * 1000)
        for raw, kept in (('« Merci ! »', 'Merci !'), ('"Merci !"', 'Merci !'), ('Commentaire : Merci !', 'Merci !'),
                          ('<think>…</think>Merci !', 'Merci !'), ('Aujourd’hui', 'Aujourd’hui')):
            self.assertEqual(yt._from_model(raw), kept, raw)

    def test_the_fingerprint_is_the_channel_the_video_and_the_text(self):
        plan = {'channel_id': CHANNEL, 'video_id': NEWEST, 'text': 'Merci !', 'video_title': 'A'}
        same = yt.comment_fingerprint({**plan, 'video_title': 'B', 'model': 'x'})
        self.assertEqual(yt.comment_fingerprint(plan), same)
        for key, value in (('channel_id', 'UC' + 'x' * 22), ('video_id', 'abcdefghijk'), ('text', 'Merci')):
            self.assertNotEqual(yt.comment_fingerprint({**plan, key: value}), same, key)


class YoutubeCommentWordsTests(unittest.TestCase):
    def test_the_words_and_the_video_are_read_from_the_raw_text(self):
        from aurora.core.normalize import extract_comment
        cases = {
            'Mets un commentaire sous ma dernière vidéo': (None, 'latest', False),
            'Aurora : mets un commentaire sous ma dernière vidéo : Merci à tous !': ('Merci à tous !', 'latest', False),
            'Publie un commentaire « Merci pour les 1000 abonnés ! » sous ma vidéo': (
                'Merci pour les 1000 abonnés !', 'latest', False),
            'Mets un commentaire qui dit bravo à tous sous ma vidéo': ('bravo à tous sous ma vidéo', 'latest', False),
            'Laisse un commentaire sous la vidéo sur les trous noirs': (None, 'other', False),
            'Mets un commentaire sous mon avant-dernière vidéo : super': ('super', 'other', False),
            'Mets un commentaire sous https://youtu.be/abcdefghijk': (None, 'other', False),
            'Mets un commentaire sous ma dernière vidéo : regardez la vidéo sur Mars': (
                'regardez la vidéo sur Mars', 'latest', False),
            'Retire le commentaire que tu as publié': (None, 'latest', True),
        }
        for text, (words, video, remove) in cases.items():
            got = extract_comment(text)
            self.assertEqual((got['text'], got['video'], got['remove']), (words, video, remove), text)

    def test_router(self):
        from aurora.core.router import DecisionEngine
        engine = DecisionEngine(use_semantic=False)
        for text in ('Mets un commentaire sous ma dernière vidéo', 'Peux-tu poster un commentaire sur ma nouvelle '
                     'vidéo YouTube ?', 'Commente ma dernière vidéo', 'Écris un commentaire sous ma dernière vidéo',
                     'Retire le commentaire que tu as publié'):
            self.assertEqual(engine.decide_fast(text).intent, 'youtube.comment', text)
        # asking for a text only, or a comment in code: nothing is posted
        for text in ('Écris-moi un commentaire pour ma vidéo', 'Ajoute un commentaire dans ce code Python',
                     'Ajoute un commentaire dans la chaîne de caractères'):
            self.assertNotEqual(engine.decide_fast(text).intent, 'youtube.comment', text)


class YoutubeCommentOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    """The whole path: the sentence, the card, the accord (a click or « Publier sans redemander »), the post."""

    def setUp(self):
        from aurora.tasks import events
        logging.getLogger('aurora').setLevel(logging.CRITICAL)
        self.env = TempEnv().__enter__()
        events.bus.detach_loop()
        yt._forget_access()
        config.update_settings({'mode': 'equilibre'})

    def tearDown(self):
        yt._forget_access()
        self.env.__exit__(None, None, None)

    def orch(self, text: str):
        from aurora.core.normalize import extract_comment
        from aurora.core.orchestrator import Deps, Orchestrator
        from tests.doubles_core import FakeClaude, FakeEngine, FakeOllama, FakeRouter, make_caps
        from tests.test_pc_control import RealTools   # the real registry and run_tool, already written there
        decision = FakeDecision('youtube.comment', method='rule', slots=extract_comment(text))
        return Orchestrator(Deps(decision_engine=FakeEngine(decision), router=FakeRouter, tools=RealTools(),
                                 ollama=FakeOllama(), claude=FakeClaude(configured=False), capabilities=make_caps()))

    async def test_the_card_then_the_post_after_the_click(self):
        from aurora.tools import pc_control
        text = 'mets un commentaire sous ma dernière vidéo : Merci à tous !'
        youtube = YouTube()
        with connected(), MockHttp(youtube):
            orch = self.orch(text)
            waiting = await orch.handle_request(text)
            self.assertEqual(waiting['status'], 'waiting_permission', waiting.get('answer'))
            perm = waiting['permission']
            self.assertEqual(perm['action'], 'youtube.comment')
            self.assertEqual(perm['title'], 'Publier ce commentaire sous « Deuxième vidéo » ?')
            preview = perm['preview']
            self.assertEqual((preview['text'], preview['video_id'], preview['channel_title']),
                             ('Merci à tous !', NEWEST, 'ID Studio'))
            self.assertEqual(len(preview['fingerprint']), 24)
            self.assertEqual(youtube.posted, [])   # nothing before the accord
            with mock.patch.object(pc_control, 'trial_instance', return_value=False):
                done = await orch.decide_permission(perm['id'], 'grant')
        self.assertEqual(done['status'], 'done', done.get('answer'))
        self.assertEqual(len(youtube.posted), 1)
        self.assertIn('Publié en votre nom sous votre dernière vidéo « Deuxième vidéo » : « Merci à tous ! »',
                      done['answer'])
        self.assertIn('Relu sur YouTube', done['answer'])
        self.assertNotIn('sans redemander', done['answer'])

    async def test_publish_without_asking_again(self):
        from aurora.security import consent
        from aurora.tools import pc_control
        config.update_settings({'youtube_comment_auto': True})
        text = 'poste un commentaire sur ma dernière vidéo : Bon visionnage !'
        youtube = YouTube()
        with connected(), MockHttp(youtube), mock.patch.object(pc_control, 'trial_instance', return_value=False):
            done = await self.orch(text).handle_request(text)
        self.assertEqual(done['status'], 'done', done.get('answer'))
        self.assertEqual(len(youtube.posted), 1)
        self.assertIn('Publié sans redemander', done['answer'])
        (perm,) = consent.for_task(done['task_id'])   # the card is still made and kept, granted by the setting
        self.assertEqual((perm['action'], perm['status']), ('youtube.comment', 'granted'))
        self.assertEqual(perm['preview']['text'], 'Bon visionnage !')
        self.assertEqual(perm['preview']['consent_scope'], 'reglage')

    async def test_refused_card_posts_nothing(self):
        text = 'mets un commentaire sous ma dernière vidéo : Merci !'
        youtube = YouTube()
        with connected(), MockHttp(youtube):
            orch = self.orch(text)
            waiting = await orch.handle_request(text)
            done = await orch.decide_permission(waiting['permission']['id'], 'deny')
        self.assertIn('Autorisation refusée : rien n’a été publié', done['answer'])
        self.assertIn('« Merci ! »', done['answer'])
        self.assertEqual(youtube.posted, [])

    async def test_no_card_when_it_cannot_be_posted(self):
        config.update_settings({'youtube_comment_auto': True})   # even so, nothing leaves
        for text, mode, link, said in (
                ('mets un commentaire sous ma dernière vidéo : Merci !', 'equilibre', False, 'Aucune chaîne YouTube'),
                ('mets un commentaire sous la vidéo sur les trous noirs', 'equilibre', True, 'pas encore fait'),
                ('mets un commentaire sous ma dernière vidéo : Merci !', 'prive', True, 'Mode Privé'),
                ('retire le commentaire que tu as publié', 'equilibre', True, 'pas encore fait')):
            config.update_settings({'mode': mode})
            vault = connected() if link else with_client()
            if not link:
                yt._save_account(None)
            asked: list[str] = []   # no_network's error is caught by the tool: the addresses asked are kept here

            def refuse(request: httpx.Request):
                asked.append(str(request.url))
                return no_network(request)

            with self.subTest(text=text, mode=mode), vault, MockHttp(refuse):
                result = await self.orch(text).handle_request(text)
                self.assertNotEqual(result['status'], 'waiting_permission', text)
                self.assertIsNone(result.get('permission'), text)
                self.assertIn(said, result['answer'], text)
                self.assertIn('publié', result['answer'], text)
                self.assertEqual(asked, [], text)   # not even the channel's feed is read


if __name__ == '__main__':
    unittest.main()
