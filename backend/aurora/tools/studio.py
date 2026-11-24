"""Studio: the user's creator activity (GitHub, downloads, Discord, YouTube, site visits).

Every figure comes from a real request; a source that is not set up says so (data « not_connected »), never a
made-up number. Public sources need no account: the GitHub REST API (60 requests per hour without a token), the
counts of a Discord invite, the public Atom feed of a YouTube channel (its 15 latest videos only). Site visits
come from Netlify Analytics: a personal token kept in the Windows vault (CLAUDE.md) and the paid Analytics option
on the site. Fetched content is data, never instructions: only counts, dates and names are kept, as text.
AURORA keeps one line of figures per day (settings table), so the curves are its own measurements over time.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Any
from urllib.parse import quote, urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .base import (_PYDANTIC_FR, NoParams, Source, TTLCache, ToolError, ToolResult, fetch, fetch_json, fr_num,
                   get_setting, now_iso, tool)
from .news import _CDATA, _FORBIDDEN, _http, _local, _text, parse_date
from .webpage import public_url

log = logging.getLogger(__name__)

CACHE_S = 600
SETTINGS_KEY = 'studio.settings'
HISTORY_KEY = 'studio.history'
HISTORY_DAYS = 400
HISTORY_FIELDS = ('downloads', 'discord_members', 'youtube_views', 'github_stars', 'youtube_subscribers')
NETLIFY_TARGET = 'AURORA/netlify_token'
YOUTUBE_TARGET = 'AURORA/youtube_api_key'
YOUTUBE_API = 'https://www.googleapis.com/youtube/v3'
MAX_COMMENTS = 50
GITHUB_API = 'https://api.github.com'
DISCORD_API = 'https://discord.com/api/v9'
YOUTUBE_FEED = 'https://www.youtube.com/feeds/videos.xml'
NETLIFY_API = 'https://api.netlify.com/api/v1'
NETLIFY_ANALYTICS = 'https://analytics.services.netlify.com/v2'
NETLIFY_TIMEZONE = 'Europe/Paris'
SITE_DAYS = 30
MAX_FEED_BYTES = 2_000_000
MAX_ACTIVITY = 15
MAX_RELEASES = 30

# A new AURORA starts with an empty Studio: each person gives their own accounts in Paramètres › Studio, and every
# reading says « aucun … indiqué » until then (NOT_INDICATED). 23/11/2026: these defaults held ID Studio IA's own accounts,
# and the AURORA just installed on his garage PC showed them already filled in (« il faut penser à supprimer mes
# données de connexion dans Aurora »). His own AURORA keeps them: they are saved in its settings (SETTINGS_KEY).
DEFAULTS: dict[str, Any] = {
    'github_users': [],
    'release_repos': [],
    'discord_invite': '',
    'youtube_channel': '',
    'site_url': '',
    'netlify_site': '',
    'kickstarter_url': '',
}
SOURCES = {'github': 'studio.github', 'downloads': 'studio.downloads', 'discord': 'studio.discord',
           'youtube': 'studio.youtube', 'comments': 'studio.comments', 'site': 'studio.site'}

NETLIFY_NOT_CONNECTED = ('Netlify n’est pas connecté : ajoutez votre jeton personnel Netlify dans Paramètres › Studio '
                         '(app.netlify.com › User settings › Applications), puis activez Netlify Analytics sur le site.')
ANALYTICS_OFF = ('Netlify Analytics ne répond pas pour ce site : l’option Analytics n’est sans doute pas activée '
                 '(elle se met en route dans app.netlify.com › le site › Analytics).')
YOUTUBE_NOT_CONNECTED = ('Clé API YouTube absente : créez-en une dans Google Cloud Console (API et services › '
                         'Identifiants › Clé API, limitée à « YouTube Data API v3 »), puis collez-la dans Paramètres › Studio.')
# A field of Paramètres › Studio left empty: said by the readings below, and by missing() for the tool map.
NOT_INDICATED = {'github': 'Aucun compte GitHub indiqué dans Paramètres › Studio.',
                 'downloads': 'Aucun dépôt de versions indiqué dans Paramètres › Studio.',
                 'discord': 'Aucune invitation Discord indiquée dans Paramètres › Studio.',
                 'youtube': 'Aucune chaîne YouTube indiquée dans Paramètres › Studio.',
                 'site': 'Aucun site indiqué dans Paramètres › Studio.'}

_cache = TTLCache(CACHE_S, maxsize=32)
_history_lock = threading.Lock()

_LOGIN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})')
_REPO = re.compile(r'[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}')
_INVITE = re.compile(r'[A-Za-z0-9-]{2,32}')
_CHANNEL = re.compile(r'(?<![A-Za-z0-9_-])UC[A-Za-z0-9_-]{22}(?![A-Za-z0-9_-])')
_HOST = re.compile(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?')
_VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')
_SITE_ID = re.compile(r'[A-Za-z0-9-]{8,64}')
_TOKEN = re.compile(r'[A-Za-z0-9_\-]{20,200}')
_API_KEY = re.compile(r'AIza[0-9A-Za-z_\-]{35}')
_YT_ID = re.compile(r'[A-Za-z0-9_\-]{1,100}')
# AURORA's own Google connection, to post comments (tools/youtube_comments.py): the OAuth client ID Studio IA creates in
# Google Cloud Console, and the lasting access Google gives back after the sign-in (written by the service only).
_OAUTH_CLIENT_ID = re.compile(r'[0-9]{5,30}-[a-z0-9]{10,64}\.apps\.googleusercontent\.com')
_OAUTH_CLIENT_SECRET = re.compile(r'(?:GOCSPX-)?[A-Za-z0-9_\-]{20,60}')
_OAUTH_REFRESH = re.compile(r'[A-Za-z0-9/_\-.]{20,512}')
# 13/11/2026 (ID Studio IA : « place 2 clés API dans les paramètres, meshy.ai et huggingface, pour si on a besoin ») : the keys
# of the two online 3D services are kept like the other secrets (same vault, same checks, never shown again).
# Nothing uses them yet: a 3D model asked of one of these services will need ID Studio IA's accord at each sending.
_MESHY_KEY = re.compile(r'msy_[A-Za-z0-9_\-]{16,120}')      # « msy_… » (docs.meshy.ai, Authentication)
_HF_TOKEN = re.compile(r'hf_[A-Za-z0-9]{20,120}')           # « hf_… » (huggingface.co › Settings › Access Tokens)
# Studio secrets in the Windows vault: name -> (vault target, accepted shape)
SECRETS = {'netlify': (NETLIFY_TARGET, _TOKEN), 'youtube': (YOUTUBE_TARGET, _API_KEY),
           'youtube_client_id': ('AURORA/youtube_oauth_client_id', _OAUTH_CLIENT_ID),
           'youtube_client_secret': ('AURORA/youtube_oauth_client_secret', _OAUTH_CLIENT_SECRET),
           'youtube_refresh': ('AURORA/youtube_oauth_refresh_token', _OAUTH_REFRESH),
           'meshy': ('AURORA/meshy_api_key', _MESHY_KEY), 'huggingface': ('AURORA/huggingface_token', _HF_TOKEN)}


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _clip(text: Any, limit: int) -> str:
    out = ' '.join(str(text or '').split())
    return out if len(out) <= limit else out[:limit - 1].rstrip() + '…'


def _after(text: str, marker: str) -> str:
    return text.split(marker, 1)[1] if marker in text else text


def _not_connected(message: str) -> ToolError:
    return ToolError(message, data={'not_connected': True})


# --------------------------------------------------------------------------- settings

_LABELS = {'github_users': 'Comptes GitHub', 'release_repos': 'Dépôts des versions', 'discord_invite': 'Invitation Discord',
           'youtube_channel': 'Chaîne YouTube', 'site_url': 'Adresse du site', 'netlify_site': 'Site Netlify',
           'kickstarter_url': 'Campagne Kickstarter'}


def _items(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(',')
    if not isinstance(value, list):
        raise ValueError('liste attendue')
    out: list[str] = []
    for item in value:
        text = str(item or '').strip().rstrip('/')
        if text and text not in out:
            out.append(text)
    return out


class StudioSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    github_users: list[str] = Field(default_factory=list, max_length=5)
    release_repos: list[str] = Field(default_factory=list, max_length=5)
    discord_invite: str = Field('', max_length=200)
    youtube_channel: str = Field('', max_length=200)
    site_url: str = Field('', max_length=300)
    netlify_site: str = Field('', max_length=253)
    kickstarter_url: str = Field('', max_length=300)

    @field_validator('github_users', mode='before')
    @classmethod
    def _users(cls, value: Any) -> list[str]:
        out: list[str] = []
        for item in _items(value):
            login = _after(item, 'github.com/').split('/')[0].lstrip('@')
            if not _LOGIN.fullmatch(login):
                raise ValueError(f'« {item[:60]} » n’est pas un nom de compte GitHub.')
            if login.lower() not in {o.lower() for o in out}:
                out.append(login)
        return out

    @field_validator('release_repos', mode='before')
    @classmethod
    def _repos(cls, value: Any) -> list[str]:
        out: list[str] = []
        for item in _items(value):
            repo = '/'.join(_after(item, 'github.com/').split('/')[:2]).removesuffix('.git')
            if not _REPO.fullmatch(repo):
                raise ValueError(f'« {item[:60]} » n’est pas un dépôt GitHub (forme compte/dépôt).')
            if repo.lower() not in {o.lower() for o in out}:
                out.append(repo)
        return out

    @field_validator('discord_invite', mode='before')
    @classmethod
    def _invite(cls, value: Any) -> str:
        text = str(value or '').strip()
        for marker in ('discord.gg/', 'discord.com/invite/', 'discordapp.com/invite/'):
            text = _after(text, marker)
        text = text.split('/')[0].split('?')[0]
        if text and not _INVITE.fullmatch(text):
            raise ValueError('donnez le lien d’invitation (discord.gg/…) ou son code.')
        return text

    @field_validator('youtube_channel', mode='before')
    @classmethod
    def _channel(cls, value: Any) -> str:
        text = str(value or '').strip()
        if not text:
            return ''
        found = _CHANNEL.search(text)
        if found is None:
            raise ValueError('donnez l’identifiant de la chaîne, qui commence par « UC » (YouTube Studio › Paramètres '
                             '› Chaîne › Paramètres avancés) ; une adresse en @nom ne suffit pas.')
        return found.group(0)

    @field_validator('site_url', mode='before')
    @classmethod
    def _site(cls, value: Any) -> str:
        text = str(value or '').strip()
        if not text:
            return ''
        try:   # same check as the page reader: public HTTPS address, no credentials, no local name
            return public_url(text if '://' in text else f'https://{text}').rstrip('/')
        except ToolError:
            raise ValueError('adresse web publique attendue (https://…).') from None

    @field_validator('netlify_site', mode='before')
    @classmethod
    def _netlify(cls, value: Any) -> str:
        text = str(value or '').strip().lower()
        if '://' in text:
            text = urlparse(text).hostname or ''
        if text and not _HOST.fullmatch(text):
            raise ValueError('nom du site Netlify attendu (par exemple mon-site.netlify.app).')
        return text

    @field_validator('kickstarter_url', mode='before')
    @classmethod
    def _kickstarter(cls, value: Any) -> str:
        text = str(value or '').strip()
        if not text:
            return ''
        try:
            parsed = urlparse(public_url(text))
        except ToolError:
            parsed = urlparse('')
        if parsed.hostname not in ('www.kickstarter.com', 'kickstarter.com') or not parsed.path.startswith('/projects/'):
            raise ValueError('adresse d’une campagne attendue (https://www.kickstarter.com/projects/…).')
        return f'https://www.kickstarter.com{parsed.path.rstrip("/")}'


def _explain(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        loc = err.get('loc') or ('',)
        label = _LABELS.get(str(loc[0]), 'Réglage')
        kind = err.get('type', '')
        if kind == 'value_error':
            msg = str(err.get('msg', '')).removeprefix('Value error, ')
        elif kind == 'too_long':
            msg = '5 éléments au plus'
        elif kind == 'extra_forbidden':
            msg = 'réglage inconnu'
        else:
            msg = _PYDANTIC_FR.get(kind) or str(err.get('msg') or 'valeur invalide')
        parts.append(f'{label} : {msg}')
    return ' ; '.join(parts)


def load_settings() -> StudioSettings:
    stored = get_setting(SETTINGS_KEY, None)
    try:
        return StudioSettings.model_validate({**DEFAULTS, **(stored if isinstance(stored, dict) else {})})
    except ValidationError:
        log.warning('Réglages Studio illisibles : valeurs par défaut utilisées')
        return StudioSettings.model_validate(DEFAULTS)


def secret(name: str) -> str | None:
    """A Studio secret (SECRETS) from the Windows vault; AURORA_VAULT=0 (the tests) never reads the real vault."""
    if os.getenv('AURORA_VAULT', '1').strip() == '0':
        return None
    target, shape = SECRETS[name]
    from ..security import vault   # lazy: Windows only
    value = vault.read(target)
    return value if value and shape.fullmatch(value) else None


def netlify_token() -> str | None:
    return secret('netlify')


def youtube_key() -> str | None:
    return secret('youtube')


def valid_secret(name: str, value: str) -> bool:
    return bool(SECRETS[name][1].fullmatch(value or ''))


def settings_view() -> dict:
    return {**load_settings().model_dump(), 'netlify_token': netlify_token() is not None,
            'youtube_key': youtube_key() is not None}


def save_settings(payload: dict) -> dict:
    """Validated settings stored in the settings table. Raises ValueError (French) on a bad value."""
    payload = {k: v for k, v in (payload or {}).items() if k not in ('netlify_token', 'youtube_key')}
    try:
        settings = StudioSettings.model_validate({**DEFAULTS, **payload})
    except ValidationError as exc:
        raise ValueError(_explain(exc)) from None
    from .. import db   # lazy: owned by another module
    db.set_setting(SETTINGS_KEY, settings.model_dump())
    _cache.clear()
    return settings_view()


_last_clear = [float('-inf')]


def clear_cache(min_interval_s: float = 0.0) -> bool:
    """Empty the cache (« Actualiser »), at most once per ``min_interval_s``: GitHub allows 60 requests per hour."""
    now = time.monotonic()
    if now - _last_clear[0] < min_interval_s:
        return False
    _last_clear[0] = now
    _cache.clear()
    return True


# --------------------------------------------------------------------------- GitHub

async def _github(path: str, params: dict | None = None) -> Any:
    try:
        return await fetch_json(f'{GITHUB_API}/{path}', params=params, source='GitHub', timeout=10,
                                headers={'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    except ToolError as exc:
        if exc.status in (403, 429):
            raise ToolError('GitHub limite les demandes sans compte (60 par heure) : réessayez un peu plus tard.',
                            status=exc.status) from None
        raise


_ISSUE_ACTIONS = {'opened': 'ouvert', 'closed': 'fermé', 'reopened': 'rouvert'}
_PR_ACTIONS = {'opened': 'ouverte', 'closed': 'fermée', 'reopened': 'rouverte'}


def _event(ev: Any) -> dict | None:
    """A public GitHub event as a short French line; only its type, repository, numbers and tags are read."""
    if not isinstance(ev, dict):
        return None
    kind = str(ev.get('type') or '')
    repo = str((ev.get('repo') or {}).get('name') or '')
    created = parse_date(str(ev.get('created_at') or ''))
    if not kind or not created or not _REPO.fullmatch(repo):
        return None
    payload = ev.get('payload') if isinstance(ev.get('payload'), dict) else {}
    url = f'https://github.com/{repo}'
    if kind == 'PushEvent':
        count = _int(payload.get('size')) or 0
        label = f'Envoi sur {repo}' + (f' ({count} commit{"s" if count > 1 else ""})' if count else '')
    elif kind == 'CreateEvent':
        ref = _clip(payload.get('ref'), 60)
        label = {'repository': f'Nouveau dépôt : {repo}', 'branch': f'Nouvelle branche {ref} sur {repo}',
                 'tag': f'Nouvelle étiquette {ref} sur {repo}'}.get(str(payload.get('ref_type')), f'Création sur {repo}')
    elif kind == 'DeleteEvent':
        label = f'Suppression de {_clip(payload.get("ref"), 60)} sur {repo}'
    elif kind == 'ReleaseEvent':
        release = payload.get('release') if isinstance(payload.get('release'), dict) else {}
        label = f'Nouvelle version {_clip(release.get("tag_name"), 40)} de {repo}'
        url = _http(release.get('html_url')) or url
    elif kind in ('IssuesEvent', 'IssueCommentEvent'):
        issue = payload.get('issue') if isinstance(payload.get('issue'), dict) else {}
        number = _int(issue.get('number'))
        ref = f' #{number}' if number else ''
        label = (f'Commentaire sur le ticket{ref} de {repo}' if kind == 'IssueCommentEvent'
                 else f'Ticket{ref} {_ISSUE_ACTIONS.get(str(payload.get("action")), "modifié")} sur {repo}')
        url = _http(issue.get('html_url')) or url
    elif kind == 'PullRequestEvent':
        pull = payload.get('pull_request') if isinstance(payload.get('pull_request'), dict) else {}
        number = _int(pull.get('number')) or _int(payload.get('number'))
        action = str(payload.get('action'))
        state = 'fusionnée' if action == 'closed' and pull.get('merged') else _PR_ACTIONS.get(action, 'modifiée')
        label = f'Demande de fusion{f" #{number}" if number else ""} {state} sur {repo}'
        url = _http(pull.get('html_url')) or url
    elif kind == 'WatchEvent':
        label = f'Étoile sur {repo}'
    elif kind == 'ForkEvent':
        label = f'Copie (fork) de {repo}'
    elif kind == 'PublicEvent':
        label = f'Dépôt rendu public : {repo}'
    else:
        label = f'{_clip(kind.removesuffix("Event"), 40)} sur {repo}'
    return {'type': kind, 'label': label, 'repo': repo, 'created_at': created, 'url': url}


def _repo(item: Any) -> dict | None:
    if not isinstance(item, dict) or not _REPO.fullmatch(str(item.get('full_name') or '')):
        return None
    full_name = str(item['full_name'])
    return {'name': _clip(item.get('name'), 100), 'full_name': full_name,
            'url': _http(item.get('html_url')) or f'https://github.com/{full_name}',
            'stars': _int(item.get('stargazers_count')) or 0, 'forks': _int(item.get('forks_count')) or 0,
            'open_issues': _int(item.get('open_issues_count')) or 0,
            'pushed_at': parse_date(str(item.get('pushed_at') or '')), 'description': _clip(item.get('description'), 200)}


async def _github_account(login: str) -> tuple[dict, list[dict], list[dict]]:
    try:
        user, repos, events = await asyncio.gather(
            _github(f'users/{login}'),
            _github(f'users/{login}/repos', {'per_page': 100, 'sort': 'pushed'}),
            _github(f'users/{login}/events/public', {'per_page': 30}))
    except ToolError as exc:
        if exc.status == 404:
            raise ToolError(f'Compte GitHub « {login} » introuvable.', status=404) from None
        raise
    if not isinstance(user, dict) or not isinstance(repos, list) or not isinstance(events, list):
        raise ToolError('Réponse inattendue de GitHub.')
    account = {'login': _clip(user.get('login'), 40) or login, 'type': _clip(user.get('type'), 20) or 'User',
               'public_repos': _int(user.get('public_repos')) or 0, 'followers': _int(user.get('followers')) or 0,
               'url': _http(user.get('html_url')) or f'https://github.com/{login}'}
    return account, [r for r in map(_repo, repos) if r], [e for e in map(_event, events) if e]


async def github_overview(users: list[str]) -> dict:
    if not users:
        raise _not_connected(NOT_INDICATED['github'])
    key = ('github', tuple(users))
    hit = _cache.get(key)
    if hit is not None:
        return {**hit, 'cached': True}
    accounts, repos, activity, problems = [], [], [], []
    for got in await asyncio.gather(*(_github_account(u) for u in users), return_exceptions=True):
        if isinstance(got, ToolError):
            problems.append(got.message_fr)
            continue
        if isinstance(got, BaseException):
            raise got
        accounts.append(got[0])
        repos.extend(got[1])
        activity.extend(got[2])
    if not accounts:
        raise ToolError(problems[0])
    repos.sort(key=lambda r: r['pushed_at'] or '', reverse=True)
    activity.sort(key=lambda e: e['created_at'], reverse=True)
    data = {'accounts': accounts, 'repos': repos, 'activity': activity[:MAX_ACTIVITY],
            'totals': {'repos': len(repos), 'stars': sum(r['stars'] for r in repos),
                       'forks': sum(r['forks'] for r in repos), 'followers': sum(a['followers'] for a in accounts)},
            'problems': problems, 'retrieved_at': now_iso()}
    _cache.set(key, data)
    return {**data, 'cached': False}


# --------------------------------------------------------------------------- downloads (GitHub releases)

def os_of(filename: str) -> str:
    """The system a published file is for, from its name only (extension, then words like « linux »)."""
    low = filename.lower()
    words = set(re.split(r'[^a-z0-9]+', low))
    ext = low.rsplit('.', 1)[-1] if '.' in low else ''
    if ext in ('dmg', 'pkg') or words & {'mac', 'macos', 'osx', 'darwin', 'apple'}:
        return 'macOS'
    if ext in ('appimage', 'deb', 'rpm', 'snap', 'flatpak') or words & {'linux', 'ubuntu', 'debian', 'fedora'}:
        return 'Linux'
    if ext in ('exe', 'msi', 'msix', 'appx') or words & {'win', 'windows', 'win32', 'win64'}:
        return 'Windows'
    return 'Autres'


async def _releases(full_name: str) -> tuple[dict, dict[str, int]]:
    try:
        payload = await _github(f'repos/{full_name}/releases', {'per_page': 100})
    except ToolError as exc:
        if exc.status == 404:
            raise ToolError(f'Dépôt GitHub « {full_name} » introuvable.', status=404) from None
        raise
    if not isinstance(payload, list):
        raise ToolError('Réponse inattendue de GitHub (versions).')
    releases, by_os = [], {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        count = 0
        for asset in item.get('assets') or []:
            n = _int((asset or {}).get('download_count')) or 0
            count += n
            system = os_of(str((asset or {}).get('name') or ''))
            by_os[system] = by_os.get(system, 0) + n
        releases.append({'tag': _clip(item.get('tag_name'), 60), 'name': _clip(item.get('name') or item.get('tag_name'), 120),
                         'published_at': parse_date(str(item.get('published_at') or '')), 'downloads': count,
                         'url': _http(item.get('html_url')) or f'https://github.com/{full_name}/releases'})
    repo = {'full_name': full_name, 'url': f'https://github.com/{full_name}/releases',
            'total': sum(r['downloads'] for r in releases), 'releases': releases[:MAX_RELEASES]}
    return repo, by_os


async def downloads_overview(repos: list[str]) -> dict:
    if not repos:
        raise _not_connected(NOT_INDICATED['downloads'])
    key = ('downloads', tuple(repos))
    hit = _cache.get(key)
    if hit is not None:
        return {**hit, 'cached': True}
    found, by_os, problems = [], {}, []
    for got in await asyncio.gather(*(_releases(r) for r in repos), return_exceptions=True):
        if isinstance(got, ToolError):
            problems.append(got.message_fr)
            continue
        if isinstance(got, BaseException):
            raise got
        found.append(got[0])
        for system, n in got[1].items():
            by_os[system] = by_os.get(system, 0) + n
    if not found:
        raise ToolError(problems[0])
    total = sum(r['total'] for r in found)
    shares = [{'os': system, 'downloads': n, 'share': round(n / total, 4) if total else 0.0}
              for system, n in sorted(by_os.items(), key=lambda kv: -kv[1]) if n > 0]
    data = {'total': total, 'repos': found, 'by_os': shares, 'problems': problems, 'retrieved_at': now_iso()}
    _cache.set(key, data)
    return {**data, 'cached': False}


# --------------------------------------------------------------------------- Discord

async def discord_overview(invite: str) -> dict:
    if not invite:
        raise _not_connected(NOT_INDICATED['discord'])
    key = ('discord', invite)
    hit = _cache.get(key)
    if hit is not None:
        return {**hit, 'cached': True}
    try:
        payload = await fetch_json(f'{DISCORD_API}/invites/{quote(invite, safe="")}', params={'with_counts': 'true'},
                                   source='Discord', timeout=10)
    except ToolError as exc:
        if exc.status == 404:
            raise ToolError(f'L’invitation Discord « {invite} » a expiré ou n’existe pas.', status=404) from None
        if exc.status == 429:
            raise ToolError('Discord limite les demandes : réessayez un peu plus tard.', status=429) from None
        raise
    guild = payload.get('guild') if isinstance(payload, dict) else None
    if not isinstance(guild, dict):
        raise ToolError('Réponse inattendue de Discord.')
    guild_id, icon = str(guild.get('id') or ''), str(guild.get('icon') or '')
    icon_url = (f'https://cdn.discordapp.com/icons/{guild_id}/{icon}.png?size=64'
                if guild_id.isdigit() and re.fullmatch(r'(a_)?[0-9a-f]{32}', icon) else None)
    data = {'name': _clip(guild.get('name'), 100), 'members': _int(payload.get('approximate_member_count')),
            'online': _int(payload.get('approximate_presence_count')), 'invite_url': f'https://discord.gg/{invite}',
            'icon_url': icon_url, 'retrieved_at': now_iso()}
    _cache.set(key, data)
    return {**data, 'cached': False}


# --------------------------------------------------------------------------- YouTube (public feed)

def parse_youtube(content: bytes) -> dict:
    """The channel Atom feed -> channel name and videos. Raises ToolError on unsafe or invalid XML."""
    if len(content) > MAX_FEED_BYTES:
        raise ToolError('Flux YouTube trop volumineux.')
    if _FORBIDDEN.search(_CDATA.sub(b'', content)):
        raise ToolError('Flux YouTube refusé : déclaration DOCTYPE/ENTITY interdite.')
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise ToolError('Flux YouTube illisible (XML invalide).') from None
    channel, channel_url, videos = '', None, []
    for child in root:
        name = _local(child.tag)
        if name == 'title' and not channel:
            channel = _clip(_text(child), 100)
        elif name == 'link' and child.get('rel') == 'alternate':
            channel_url = channel_url or _http(child.get('href'))
        elif name == 'entry':
            video = {'id': '', 'title': '', 'url': None, 'published': None, 'views': None, 'likes': None,
                     'comments': None, 'thumbnail': None}
            for el in child.iter():
                part = _local(el.tag)
                if part == 'videoid':
                    video['id'] = _text(el)
                elif part == 'title' and not video['title']:   # the entry title comes before media:title
                    video['title'] = _clip(_text(el), 200)
                elif part == 'link' and video['url'] is None and el.get('rel', 'alternate') == 'alternate':
                    video['url'] = _http(el.get('href'))
                elif part == 'published':
                    video['published'] = parse_date(_text(el))
                elif part == 'thumbnail' and video['thumbnail'] is None:
                    video['thumbnail'] = _http(el.get('url'))
                elif part == 'statistics':
                    video['views'] = _int(el.get('views'))
                elif part == 'starrating':
                    video['likes'] = _int(el.get('count'))
            if _VIDEO_ID.fullmatch(video['id']):
                video['url'] = video['url'] or f'https://www.youtube.com/watch?v={video["id"]}'
                videos.append(video)
    videos.sort(key=lambda v: v['published'] or '', reverse=True)
    return {'channel': channel, 'channel_url': channel_url or '', 'videos': videos}


async def _youtube_api(resource: str, params: dict, api_key: str | None = None, *,
                       access_token: str | None = None) -> dict:
    """GET on the YouTube Data API v3, the key in a header: never in the address, so never in a log.
    ``access_token``: the access of AURORA's Google connection (tools/youtube_comments.py) instead of the key."""
    auth = {'Authorization': f'Bearer {access_token}'} if access_token else {'X-Goog-Api-Key': api_key or ''}
    try:
        payload = await fetch_json(f'{YOUTUBE_API}/{resource}', params=params, source='YouTube', timeout=10,
                                   headers=auth)
    except ToolError as exc:
        if access_token and exc.status == 401:
            raise ToolError('YouTube refuse l’accès de la connexion Google : reconnectez-vous dans Paramètres › '
                            'Studio.', status=401) from None
        if exc.status == 400 and not access_token:
            raise _not_connected('YouTube refuse la clé API (invalide ?) : vérifiez-la dans Paramètres › Studio.') from None
        if exc.status == 403 and access_token:
            raise ToolError('YouTube refuse la demande : quota du jour épuisé, API « YouTube Data v3 » non activée '
                            'pour le projet Google du client OAuth, ou action non permise sur cette vidéo.',
                            status=403) from None
        if exc.status == 403:
            raise ToolError('YouTube refuse la demande : quota du jour épuisé, API « YouTube Data v3 » non activée pour '
                            'ce projet Google, ou clé limitée à d’autres API.', status=403) from None
        raise
    if not isinstance(payload, dict):
        raise ToolError('Réponse inattendue de YouTube.')
    return payload


async def _youtube_stats(data: dict, channel: str, api_key: str) -> None:
    """Subscribers and channel totals (channels.list, 1 unit), then views, likes and comments of the feed's videos
    (videos.list, 1 unit), written into ``data``. Raises ToolError; the caller keeps the feed's figures then."""
    ids = [v['id'] for v in data['videos']][:50]
    calls = [_youtube_api('channels', {'part': 'statistics', 'id': channel}, api_key)]
    if ids:
        calls.append(_youtube_api('videos', {'part': 'statistics', 'id': ','.join(ids), 'maxResults': 50}, api_key))
    got = await asyncio.gather(*calls)
    items = got[0].get('items') if isinstance(got[0].get('items'), list) else []
    if not items or not isinstance(items[0], dict):
        raise ToolError('YouTube ne connaît pas cette chaîne : vérifiez son identifiant dans Paramètres › Studio.')
    stats = items[0].get('statistics') if isinstance(items[0].get('statistics'), dict) else {}
    hidden = bool(stats.get('hiddenSubscriberCount'))
    data.update(subscribers=None if hidden else _int(stats.get('subscriberCount')), subscribers_hidden=hidden,
                channel_views=_int(stats.get('viewCount')), channel_videos=_int(stats.get('videoCount')))
    if len(got) > 1:
        by_id = {v.get('id'): v.get('statistics') for v in got[1].get('items') or []
                 if isinstance(v, dict) and isinstance(v.get('statistics'), dict)}
        for video in data['videos']:
            found = by_id.get(video['id'])
            if found:
                video.update(views=_int(found.get('viewCount')), likes=_int(found.get('likeCount')),
                             comments=_int(found.get('commentCount')))


async def youtube_overview(channel: str, *, fresh: bool = False) -> dict:
    """``fresh``: the feed is read again now, not taken from the 10-minute cache (tools/youtube_comments.py: the
    newest video, under which a comment is posted)."""
    if not channel:
        raise _not_connected(NOT_INDICATED['youtube'])
    api_key = youtube_key()
    key = ('youtube', channel, api_key is not None)
    hit = None if fresh else _cache.get(key)
    if hit is not None:
        return {**hit, 'cached': True}
    try:
        got = await fetch(YOUTUBE_FEED, params={'channel_id': channel}, source='YouTube', timeout=10,
                          max_bytes=MAX_FEED_BYTES, accept='application/atom+xml')
    except ToolError as exc:
        if exc.status == 404:
            raise ToolError(f'Chaîne YouTube « {channel} » introuvable (flux public).', status=404) from None
        raise
    data = parse_youtube(got.content)
    data['channel_url'] = data['channel_url'] or f'https://www.youtube.com/channel/{channel}'
    data.update(subscribers=None, subscribers_hidden=False, channel_views=None, channel_videos=None,
                stats='feed', stats_error=None)
    if api_key:
        try:
            await _youtube_stats(data, channel, api_key)
            data['stats'] = 'api'
        except ToolError as exc:   # the public feed still answers: its figures stay, the reason is shown
            data['stats_error'] = exc.message_fr
    views = [v['views'] for v in data['videos'] if v['views'] is not None]
    data.update(video_count=len(data['videos']), total_views=sum(views), retrieved_at=now_iso())
    _cache.set(key, data)
    return {**data, 'cached': False}


# --------------------------------------------------------------------------- YouTube comments (API key)

def _author_of(snippet: dict) -> str | None:
    found = snippet.get('authorChannelId')
    return found.get('value') if isinstance(found, dict) else None


def read_threads(items: Any, channel_id: str) -> list[dict]:
    """commentThreads.list -> comments: YouTube Manager's lireFils (src/lib/commentaires.ts:26) in Python. The
    channel's own comments are left out; « unanswered » = no reply signed by the channel among those YouTube sent.
    YouTube sends only a few replies per thread: beyond them nothing is claimed (« uncertain »), as there."""
    out: list[dict] = []
    for thread in items if isinstance(items, list) else []:
        if not isinstance(thread, dict) or not isinstance(thread.get('snippet'), dict):
            continue
        snippet = thread['snippet']
        top = (snippet.get('topLevelComment') or {}).get('snippet') if isinstance(snippet.get('topLevelComment'), dict) else None
        if not isinstance(top, dict) or _author_of(top) == channel_id:
            continue
        thread_id, video_id = str(thread.get('id') or ''), str(snippet.get('videoId') or top.get('videoId') or '')
        if not _YT_ID.fullmatch(thread_id) or not _VIDEO_ID.fullmatch(video_id):
            continue
        total = _int(snippet.get('totalReplyCount')) or 0
        replies = thread.get('replies') if isinstance(thread.get('replies'), dict) else {}
        seen = [r.get('snippet') for r in replies.get('comments') or [] if isinstance(r, dict) and isinstance(r.get('snippet'), dict)]
        answered = any(_author_of(r) == channel_id for r in seen)
        uncertain = not answered and total > len(seen)
        out.append({'id': thread_id, 'video_id': video_id, 'video_title': '', 'author': _clip(top.get('authorDisplayName'), 80),
                    'text': _clip(top.get('textDisplay') or top.get('textOriginal'), 600),
                    'published': parse_date(str(top.get('publishedAt') or '')), 'replies': total,
                    'unanswered': not answered and not uncertain, 'uncertain': uncertain,
                    'url': f'https://www.youtube.com/watch?v={video_id}&lc={thread_id}'})
    return out


async def comments_overview(channel: str) -> dict:
    if not channel:
        raise _not_connected(NOT_INDICATED['youtube'])
    api_key = youtube_key()
    if not api_key:
        raise _not_connected(YOUTUBE_NOT_CONNECTED)
    key = ('comments', channel)
    hit = _cache.get(key)
    if hit is not None:
        return {**hit, 'cached': True}
    payload = await _youtube_api('commentThreads', {'part': 'snippet,replies', 'allThreadsRelatedToChannelId': channel,
                                                    'maxResults': MAX_COMMENTS, 'order': 'time', 'textFormat': 'plainText'},
                                 api_key)
    items = payload.get('items') if isinstance(payload.get('items'), list) else []
    threads = read_threads(items, channel)
    own = sum(1 for t in items if isinstance(t, dict) and isinstance(t.get('snippet'), dict)
              and isinstance(t['snippet'].get('topLevelComment'), dict)
              and isinstance(t['snippet']['topLevelComment'].get('snippet'), dict)
              and _author_of(t['snippet']['topLevelComment']['snippet']) == channel)   # left out, but said
    ids = sorted({t['video_id'] for t in threads})[:50]
    if ids:
        try:   # the titles are a comfort: without them the comments stay
            videos = await _youtube_api('videos', {'part': 'snippet', 'id': ','.join(ids), 'maxResults': 50}, api_key)
            titles = {v.get('id'): _clip((v.get('snippet') or {}).get('title'), 120)
                      for v in videos.get('items') or [] if isinstance(v, dict) and isinstance(v.get('snippet'), dict)}
            for t in threads:
                t['video_title'] = titles.get(t['video_id'], '')
        except ToolError:
            pass
    data = {'threads': threads, 'total': len(threads), 'unanswered': sum(t['unanswered'] for t in threads),
            'uncertain': sum(t['uncertain'] for t in threads), 'own': own, 'retrieved_at': now_iso()}
    _cache.set(key, data)
    return {**data, 'cached': False}


# --------------------------------------------------------------------------- site (Netlify Analytics)

def _per_day(payload: Any) -> dict[str, int]:
    """Netlify Analytics answer {data: [[timestamp ms, count], ...]} -> {local day: count}."""
    rows = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ToolError('Réponse inattendue de Netlify Analytics.')
    out: dict[str, int] = {}
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) == 2 and isinstance(row[0], (int, float)):
            count = _int(row[1])
            if count is not None:
                day = datetime.fromtimestamp(row[0] / 1000).date().isoformat()
                out[day] = out.get(day, 0) + count
    return out


def _site_target(site_url: str, netlify_site: str) -> str:
    """The site asked to Netlify: its Netlify name when given, else the host of its address ('' : none)."""
    return netlify_site or (urlparse(site_url).hostname or '' if site_url else '')


async def site_overview(site_url: str, netlify_site: str) -> dict:
    target = _site_target(site_url, netlify_site)
    if not target:
        raise _not_connected(NOT_INDICATED['site'])
    token = netlify_token()
    if not token:
        raise _not_connected(NETLIFY_NOT_CONNECTED)
    key = ('site', target)
    hit = _cache.get(key)
    if hit is not None:
        return {**hit, 'cached': True}
    auth = {'Authorization': f'Bearer {token}'}
    try:
        site = await fetch_json(f'{NETLIFY_API}/sites/{quote(target, safe="")}', source='Netlify', timeout=10, headers=auth)
    except ToolError as exc:
        if exc.status in (401, 403):
            raise _not_connected('Netlify refuse le jeton : vérifiez-le dans Paramètres › Studio.') from None
        if exc.status == 404:
            raise _not_connected(f'Site « {target} » introuvable sur votre compte Netlify : indiquez son nom Netlify '
                                 '(…netlify.app) dans Paramètres › Studio.') from None
        raise
    site_id = str(site.get('id') or '') if isinstance(site, dict) else ''
    if not _SITE_ID.fullmatch(site_id):
        raise ToolError('Réponse inattendue de Netlify (site).')
    now_ms = int(time.time() * 1000)
    params = {'from': now_ms - SITE_DAYS * 86_400_000, 'to': now_ms, 'timezone': NETLIFY_TIMEZONE, 'resolution': 'day'}
    try:
        pageviews, visitors = await asyncio.gather(
            fetch_json(f'{NETLIFY_ANALYTICS}/{site_id}/pageviews', params=params, source='Netlify Analytics',
                       timeout=10, headers=auth),
            fetch_json(f'{NETLIFY_ANALYTICS}/{site_id}/visitors', params=params, source='Netlify Analytics',
                       timeout=10, headers=auth))
    except ToolError as exc:
        if exc.status in (401, 402, 403, 404):
            raise _not_connected(ANALYTICS_OFF) from None
        raise
    views, people = _per_day(pageviews), _per_day(visitors)
    days = sorted(set(views) | set(people))
    data = {'site': _clip(site.get('ssl_url') or site.get('url') or target, 200), 'days': SITE_DAYS,
            'visitors': sum(people.values()) if people else None, 'pageviews': sum(views.values()) if views else None,
            'series': [{'date': d, 'visitors': people.get(d), 'pageviews': views.get(d)} for d in days],
            'retrieved_at': now_iso()}
    _cache.set(key, data)
    return {**data, 'cached': False}


# --------------------------------------------------------------------------- tools

def _sources(title: str, url: str, data: dict) -> list[Source]:
    return [Source(title=title, url=url, kind='api', retrieved_at=data.get('retrieved_at') or now_iso())]


def _problems(data: dict) -> str:
    return (' ' + ' '.join(data['problems'])) if data.get('problems') else ''


def missing(source: str) -> str | None:
    """What a source of the Studio lacks to be read NOW: nothing indicated in Paramètres › Studio, a key or a token
    absent. None when nothing is missing. The first refusals of the *_overview readings above, with their own
    sentences, said without reading anything on the Internet (ToolSpec.missing, for the tool map)."""
    s = load_settings()
    if source == 'github':
        return None if s.github_users else NOT_INDICATED['github']
    if source == 'downloads':
        return None if s.release_repos else NOT_INDICATED['downloads']
    if source == 'discord':
        return None if s.discord_invite else NOT_INDICATED['discord']
    if source == 'youtube':
        return None if s.youtube_channel else NOT_INDICATED['youtube']
    if source == 'comments':
        if not s.youtube_channel:
            return NOT_INDICATED['youtube']
        return None if youtube_key() else YOUTUBE_NOT_CONNECTED
    if source == 'site':
        if not _site_target(s.site_url, s.netlify_site):
            return NOT_INDICATED['site']
        return None if netlify_token() else NETLIFY_NOT_CONNECTED
    raise KeyError(source)


@tool('studio.github', description='Studio : comptes, dépôts et activité publique GitHub (API publique).',
      action='web.fetch', params=NoParams, network=True, provenance='github', timeout_s=25,
      missing=lambda: missing('github'))
async def github_tool(_: NoParams) -> ToolResult:
    data = await github_overview(load_settings().github_users)
    t = data['totals']
    summary = (f"GitHub : {fr_num(t['repos'], 0)} dépôt(s) public(s), {fr_num(t['stars'], 0)} étoile(s), "
               f"{fr_num(t['followers'], 0)} abonné(s) sur {len(data['accounts'])} compte(s).{_problems(data)}")
    src = [Source(title=f'GitHub — {a["login"]}', url=a['url'], kind='api', retrieved_at=data['retrieved_at'])
           for a in data['accounts']]
    return ToolResult(ok=True, data=data, summary=summary, provenance='github', sources=src)


@tool('studio.downloads', description='Studio : téléchargements des fichiers publiés dans les versions GitHub.',
      action='web.fetch', params=NoParams, network=True, provenance='github', timeout_s=25,
      missing=lambda: missing('downloads'))
async def downloads_tool(_: NoParams) -> ToolResult:
    data = await downloads_overview(load_settings().release_repos)
    summary = (f"GitHub : {fr_num(data['total'], 0)} téléchargement(s) des fichiers publiés "
               f"(les archives du code source ne sont pas comptées par GitHub).{_problems(data)}")
    src = [Source(title=f'GitHub — versions de {r["full_name"]}', url=r['url'], kind='api',
                  retrieved_at=data['retrieved_at']) for r in data['repos']]
    return ToolResult(ok=True, data=data, summary=summary, provenance='github', sources=src)


@tool('studio.discord', description='Studio : membres et membres en ligne du serveur Discord (invitation publique).',
      action='web.fetch', params=NoParams, network=True, provenance='discord', timeout_s=15,
      missing=lambda: missing('discord'))
async def discord_tool(_: NoParams) -> ToolResult:
    data = await discord_overview(load_settings().discord_invite)
    summary = (f"Discord « {data['name']} » : {fr_num(data['members'], 0)} membre(s), "
               f"{fr_num(data['online'], 0)} en ligne (chiffres approchés de Discord).")
    return ToolResult(ok=True, data=data, summary=summary, provenance='discord',
                      sources=_sources('Discord — invitation publique', data['invite_url'], data))


@tool('studio.youtube', description='Studio : dernières vidéos de la chaîne YouTube et leurs vues (flux public).',
      action='web.fetch', params=NoParams, network=True, provenance='youtube-rss', timeout_s=15,
      missing=lambda: missing('youtube'))
async def youtube_tool(_: NoParams) -> ToolResult:
    data = await youtube_overview(load_settings().youtube_channel)
    summary = (f"YouTube « {data['channel']} » : {data['video_count']} vidéo(s) dans le flux public, "
               f"{fr_num(data['total_views'], 0)} vue(s) au total (le flux ne donne que les 15 dernières vidéos).")
    src = _sources('YouTube — flux public de la chaîne', data['channel_url'], data)
    if data['stats'] == 'api':
        who = 'abonnés masqués' if data['subscribers_hidden'] else f"{fr_num(data['subscribers'], 0)} abonné(s)"
        summary += f" {who} ; vues et « j'aime » relus par l'API YouTube."
        src.append(Source(title='YouTube Data API — statistiques', url=data['channel_url'], kind='api',
                          retrieved_at=data['retrieved_at']))
    elif data['stats_error']:
        summary += f" Statistiques détaillées indisponibles : {data['stats_error']}"
    return ToolResult(ok=True, data=data, summary=summary, provenance='youtube-rss', sources=src)


@tool('studio.comments', description='Studio : derniers commentaires de la chaîne YouTube et ceux sans réponse (API YouTube).',
      action='web.fetch', params=NoParams, network=True, provenance='youtube-api', timeout_s=20,
      missing=lambda: missing('comments'))
async def comments_tool(_: NoParams) -> ToolResult:
    channel = load_settings().youtube_channel
    data = await comments_overview(channel)
    summary = f"YouTube : {data['total']} commentaire(s) récent(s), dont {data['unanswered']} sans réponse de votre part"
    if data['uncertain']:
        summary += f" ; {data['uncertain']} fil(s) incertain(s) (YouTube n'a pas renvoyé toutes leurs réponses)"
    if data.get('own'):
        summary += f" ; {data['own']} de vos propres commentaires écarté(s)"
    return ToolResult(ok=True, data=data, summary=summary + '.', provenance='youtube-api',
                      sources=_sources('YouTube Data API — commentaires de la chaîne',
                                       f'https://studio.youtube.com/channel/{channel}/comments/inbox', data))


@tool('studio.site', description='Studio : visiteurs et pages vues du site sur 30 jours (Netlify Analytics).',
      action='web.fetch', params=NoParams, network=True, provenance='netlify', timeout_s=25,
      missing=lambda: missing('site'))
async def site_tool(_: NoParams) -> ToolResult:
    s = load_settings()
    data = await site_overview(s.site_url, s.netlify_site)
    summary = (f"Netlify Analytics : {fr_num(data['visitors'], 0)} visiteur(s) (additionnés jour par jour) et "
               f"{fr_num(data['pageviews'], 0)} page(s) vue(s) sur {SITE_DAYS} jours.")
    return ToolResult(ok=True, data=data, summary=summary, provenance='netlify',
                      sources=_sources('Netlify Analytics', 'https://app.netlify.com/', data))


# --------------------------------------------------------------------------- overview for the Studio page

def source_view(result: dict) -> dict:
    """A tool result as one source of the page: state, data only when the measure succeeded, the reason otherwise."""
    data = result.get('data') if isinstance(result.get('data'), dict) else {}
    ok = bool(result.get('ok'))
    if ok:
        state = 'ok'
    elif data.get('private_mode'):
        state = 'private'
    elif data.get('offline'):
        state = 'offline'
    elif data.get('not_connected'):
        state = 'not_connected'
    else:
        state = 'error'
    return {'state': state, 'ok': ok, 'data': data if ok else None,
            'summary': str(result.get('summary') or ''), 'error': result.get('error'),
            'provenance': str(result.get('provenance') or ''), 'fetched_at': data.get('retrieved_at') if ok else None,
            'sources': result.get('sources') or []}


def _kickstarter(settings: StudioSettings) -> dict:
    if settings.kickstarter_url:
        summary = ('Campagne liée. Kickstarter n’ouvre pas ses chiffres aux outils : le suivi des contributions '
                   'n’est pas encore fait, le lien ouvre la campagne.')
    else:
        summary = 'Aucune campagne Kickstarter liée. Quand vous en lancerez une, collez son adresse dans Paramètres › Studio.'
    return {'state': 'not_connected', 'ok': False, 'data': None, 'summary': summary, 'error': None,
            'provenance': 'kickstarter', 'fetched_at': None, 'sources': []}


def links(settings: StudioSettings) -> dict:
    channel, invite = settings.youtube_channel, settings.discord_invite
    return {'site': settings.site_url,
            'youtube': f'https://www.youtube.com/channel/{channel}' if channel else '',
            'youtube_studio': f'https://studio.youtube.com/channel/{channel}' if channel else '',
            'discord': f'https://discord.gg/{invite}' if invite else '',
            'github': [f'https://github.com/{u}' for u in settings.github_users],
            'kickstarter': settings.kickstarter_url or None}


def record_history(values: dict[str, int | None], today: str | None = None) -> list[dict]:
    """Today's measured figures (None = not measured this time) merged into the daily history; written only when a
    figure changed. A failed write keeps the page working: the history is a bonus, never a blocker."""
    today = today or date.today().isoformat()
    with _history_lock:
        stored = get_setting(HISTORY_KEY, [])
        rows = [r for r in (stored if isinstance(stored, list) else [])
                if isinstance(r, dict) and isinstance(r.get('date'), str)]
        measured = {k: v for k, v in values.items() if k in HISTORY_FIELDS and v is not None}
        current = next((r for r in rows if r['date'] == today), None)
        if measured and (current is None or any(current.get(k) != v for k, v in measured.items())):
            if current is None:
                current = {'date': today}
                rows.append(current)
            current.update(measured)
            rows = sorted(rows, key=lambda r: r['date'])[-HISTORY_DAYS:]
            try:
                from .. import db   # lazy: owned by another module
                db.set_setting(HISTORY_KEY, rows)
            except Exception:
                log.warning('Historique Studio non enregistré', exc_info=True)
    return [{'date': r['date'], **{k: r.get(k) for k in HISTORY_FIELDS}} for r in sorted(rows, key=lambda r: r['date'])]


def overview(results: dict[str, dict], *, mode: str) -> dict:
    """The Studio page: one view per source (tool results from main._tool), the links and AURORA's daily history."""
    settings = load_settings()
    views = {key: source_view(results.get(key) or {}) for key in SOURCES}

    def measured(key: str, *path: str) -> int | None:
        value: Any = views[key]['data']
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    history = record_history({'downloads': measured('downloads', 'total'),
                              'discord_members': measured('discord', 'members'),
                              'youtube_views': measured('youtube', 'total_views'),
                              'github_stars': measured('github', 'totals', 'stars'),
                              'youtube_subscribers': measured('youtube', 'subscribers')})
    return {'generated_at': now_iso(), 'mode': mode, 'links': links(settings), **views,
            'kickstarter': _kickstarter(settings), 'history': history}
