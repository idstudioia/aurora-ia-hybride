"""Safe reader of a public web page, and the passage that answers the question.

Port of Athéna's agentWeb.ts (urlPublique, adressePublique, lirePublic, textePage, illisible, the
main/article choice of outilsWeb.lire) and searxngService.ts (passagePertinent), with their tests.

Public HTTPS pages only: no file://, no local network, no other port, no credentials in the address.
Every address the name resolves to must be public, and the connection is pinned to the checked address:
a second DNS answer cannot turn a public page into a request to the local network (DNS rebinding). The
certificate is still checked against the site's name (SNI). Redirects are followed by hand, three at most,
each one checked again. Text only (HTML, plain text, JSON), 1.5 MB at most, never compressed. What is read
is external content: data to quote, never instructions (the prompt wraps it, security/untrusted.py).

Independent security review of 28/10/2026 (reviews/mod-verification): the first version used regular
expressions whose time grew with the square of the page (a trapped page froze the whole service), let
non-ToolError exceptions sink the whole search, accepted compressed answers (590 bytes → 558 MB), sent the
user's contact to every site, and accepted « application/octet-stream; x=text/html ». All fixed and tested.
"""
from __future__ import annotations

import asyncio
import codecs
import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from .base import OFFLINE_MESSAGE, USER_AGENT, ToolError, http_client, is_offline

MAX_BYTES = 1_500_000
MAX_HTML_CHARS = 300_000      # parsed at most: the useful text of an article is far shorter
MAX_REDIRECTS = 3
READ_TIMEOUT_S = 15
TEXT_TYPES = frozenset({'text/html', 'text/plain', 'application/json'})
LOCAL_SUFFIXES = ('.local', '.localhost', '.internal', '.lan', '.home.arpa')
# Not public although Python 3.11.7 says so (CVE-2024-4032): IETF protocol assignments.
NOT_PUBLIC = (ipaddress.ip_network('192.0.0.0/24'),)


def public_ip(address: str) -> bool:
    """A public IPv4 address (Athéna's adressePublique); an IPv4 written as IPv6 (::ffff:…) is judged as IPv4."""
    try:
        ip = ipaddress.ip_address(str(address).split('%')[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.version == 4 and ip.is_global and not ip.is_multicast
            and not any(ip in net for net in NOT_PUBLIC))


def public_url(raw: str) -> str:
    """The address, without its anchor and with an ASCII (IDNA) name, if it is a public HTTPS page; else
    ToolError (Athéna's urlPublique)."""
    text = str(raw or '').strip()
    if re.search(r'[\x00-\x20\x7f]', text):
        raise ToolError('Adresse de page invalide.')
    try:
        parts = urlsplit(text)
        port = parts.port
        host = (parts.hostname or '').rstrip('.').encode('idna').decode('ascii').lower()
    except (ValueError, UnicodeError):
        raise ToolError('Adresse de page invalide.') from None
    if (parts.scheme != 'https' or port not in (None, 443) or parts.username or parts.password or not host
            or ':' in host or host == 'localhost' or host.endswith(LOCAL_SUFFIXES)):
        raise ToolError('Seules les pages HTTPS publiques sont lisibles.')
    if re.fullmatch(r'[\d.]+', host) and not public_ip(host):   # « https://2130706433 » is 127.0.0.1
        raise ToolError('Adresse privée refusée.')
    return parts._replace(netloc=host, fragment='').geturl()


async def resolve(host: str) -> list[str]:
    """IPv4 addresses of a name (the tests replace this function)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


@dataclass
class Page:
    url: str
    body: str
    content_type: str


def _decode(raw: bytes, charset: str | None) -> str:
    """Bytes → text; an unknown or broken charset falls back to UTF-8 (« charset=pas-un-codec »)."""
    try:
        codecs.lookup(charset or 'utf-8')
        return raw.decode(charset or 'utf-8', errors='replace')
    except (LookupError, UnicodeError, TypeError):
        return raw.decode('utf-8', errors='replace')


async def read_public(url: str, *, timeout: float = READ_TIMEOUT_S) -> Page:
    """GET a public page (Athéna's lirePublic): every hop checked, connection pinned to the checked address,
    ``timeout`` for the whole reading (a server sending one byte at a time cannot stretch it)."""
    if is_offline():
        raise ToolError(OFFLINE_MESSAGE, data={'offline': True})
    try:
        async with asyncio.timeout(timeout):
            return await _read(url, timeout)
    except TimeoutError:
        raise ToolError('Délai de lecture dépassé.') from None


async def _read(url: str, timeout: float) -> Page:
    for _hop in range(MAX_REDIRECTS + 1):
        url = public_url(url)
        parts = urlsplit(url)
        host = parts.hostname or ''
        try:
            addresses = await resolve(host)
        except (OSError, UnicodeError, ValueError):
            raise ToolError(f'Site introuvable : {host}.') from None
        if not addresses or not all(public_ip(a) for a in addresses):
            raise ToolError('Adresse réseau non publique : page refusée.')
        pinned = parts._replace(netloc=addresses[0]).geturl()
        headers = {'Host': host, 'Accept': 'text/html,text/plain,application/json', 'Accept-Encoding': 'identity',
                   'User-Agent': USER_AGENT}   # never the user's contact: it is meant for Wikipedia only
        try:
            # trust_env=False: through a system proxy, httpcore ignores the SNI name and every reading fails
            async with http_client(timeout, follow_redirects=False, trust_env=False) as client:
                async with client.stream('GET', pinned, headers=headers, extensions={'sni_hostname': host}) as resp:
                    if 300 <= resp.status_code < 400:
                        location = resp.headers.get('location')
                        if not location or len(location) > 4000:
                            raise ToolError('Redirection sans adresse lisible.')
                        url = urljoin(url, location)
                        continue
                    kind = resp.headers.get('content-type', '').split(';')[0].strip().lower()
                    if resp.status_code != 200 or kind not in TEXT_TYPES:
                        raise ToolError(f"Page non lisible (HTTP {resp.status_code}, {kind or 'type inconnu'}).",
                                        status=resp.status_code)
                    if resp.headers.get('content-encoding', 'identity').strip().lower() not in ('', 'identity'):
                        raise ToolError('Page compressée malgré la demande : refusée (risque de « bombe »).')
                    buf = bytearray()
                    # Any Content-Encoding was refused just above: these bytes are never decompressed.
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > MAX_BYTES:
                            raise ToolError('Page trop volumineuse (limite 1,5 Mo).')
                    return Page(url, _decode(bytes(buf), resp.charset_encoding), kind)
        except httpx.TimeoutException:
            raise ToolError('Délai de lecture dépassé.') from None
        except (httpx.HTTPError, ValueError, UnicodeError, TypeError) as exc:
            raise ToolError(f'Page injoignable ({type(exc).__name__}).') from None
    raise ToolError('Trop de redirections.')


# A price whose cents sit in their own tag: « 8 , 99 € » once the tags are gone, read 899 by the model
# (Athéna, 05/10/2026). « 1 299 € » (thousands) is untouched.
_SPLIT_PRICE = re.compile(r'(\d) ([.,]) (\d{2})(?= ?(?:€|\$|eur\b|euros?\b))', re.I)


# One tag, read with BOUNDED length: a quoted value is at most 1000 characters and a tag at most 1000 parts, so a
# « < » that never closes costs a bounded scan, then counts as text. Neither the first version's expressions nor
# Python 3.11.7's html.parser stay linear on unclosed tags: « <span » repeated took x8 time per doubled page
# (measured 28/10). Comments and declarations (<!…>, <?…>) are tags too: their content is never text.
_TAG = re.compile(r'''<(/?)([a-zA-Z][\w:-]{0,40})?((?:[^'"<>]|"[^"]{0,1000}"|'[^']{0,1000}'){0,1000})>''')
_DECLARATION = re.compile(r'<[!?][^<>]{0,2000}>')
_SKIP_TAGS = frozenset({'script', 'style', 'noscript', 'svg', 'template'})
_MAIN_TAGS = frozenset({'main', 'article'})


def _without_comments(markup: str) -> str:
    """<!-- … --> removed with one forward search each (a regular expression would rescan an unclosed one
    from every « <!-- »); an unclosed comment runs to the end, as in a browser."""
    out, position = [], 0
    while True:
        start = markup.find('<!--', position)
        if start < 0:
            out.append(markup[position:])
            return ' '.join(out)
        out.append(markup[position:start])
        end = markup.find('-->', start + 4)
        if end < 0:
            return ' '.join(out)
        position = end + 3


def page_text(markup: str) -> str:
    """The text of a page, its <main> / <article> part when it has one (Athéna's textePage + outilsWeb.lire):
    scripts, styles and SVG skipped, entities decoded, Amazon's cents (a-price-fraction) glued back."""
    source = _DECLARATION.sub(' ', _without_comments((markup or '')[:MAX_HTML_CHARS]))
    pieces: list[tuple[bool, str]] = []
    skip = main = 0
    cents = False

    def add(raw: str) -> None:
        nonlocal cents
        text = ' '.join(html.unescape(raw).split())
        if not text or skip:
            return
        if cents and len(text) == 2 and text.isdigit() and pieces:
            cents = False
            inside, last = pieces.pop()
            if last in (',', '.') and pieces:
                inside, last = pieces.pop()
            if last[-1:].isdigit():
                pieces.append((inside, f'{last},{text}'))
                return
            pieces.append((inside, last))
        pieces.append((main > 0, text))

    position = 0
    for tag in _TAG.finditer(source):
        add(source[position:tag.start()])
        position = tag.end()
        closing, name, attrs = tag.group(1), (tag.group(2) or '').lower(), tag.group(3)
        if attrs.rstrip().endswith('/'):
            continue                              # <br/>, <svg/>: nothing inside
        step = -1 if closing else 1
        if name in _SKIP_TAGS:
            skip = max(0, skip + step)
        elif name in _MAIN_TAGS:
            main = max(0, main + step)
        elif name == 'span' and not closing and 'a-price-fraction' in attrs:
            cents = True
    add(source[position:])
    chosen = [t for inside, t in pieces if inside] or [t for _, t in pieces]
    return _SPLIT_PRICE.sub(r'\1\2\3', ' '.join(chosen))[:10000]


# « Lis-le-moi » / « Explique-le » on the globe (11/11/2026, ID Studio IA : « sur Athéna la version payante on a un système de
# lecture des articles plus abouti »). Athéna's rule of 21/10/2026, measured on 12 newspapers (texteDeLArticle in
# actualitesService.ts): a whole page starts with the menus and ends with the other titles of the site; the article
# is the longest run of <p> paragraphs that follow one another (less than 1 500 characters of HTML between two).
# Paragraphs left out, by a word of their class: photo captions (wp-caption-text), the message against ad blockers
# (o-em-adblock), the « réservé aux abonnés » banner (fig-premium-paywall), the author's card (author_name). A class
# holding « paywall » anywhere in the page says the article is cut: « partial ». The rule only is taken, never
# Athéna's expressions: this reader goes once through the page with the bounded _TAG (review of 28/10/2026).
_ARTICLE_LEFT_OUT = ('caption', 'paywall', 'adblock', 'author')
_ARTICLE_GAP, _ARTICLE_MIN = 1500, 40
_CLASS = re.compile(r'''\bclass\s*=\s*(?:"([^"]{0,1000})"|'([^']{0,1000})'|([^\s"'<>]{0,200}))''', re.I)


def article_text(markup: str, max_chars: int = 12000) -> tuple[str, bool]:
    """(the text of the article of a newspaper page, one paragraph per line; True when the page says it is cut for
    subscribers). '' when the page has no paragraph (an anti-robot page). Whole paragraphs, ``max_chars`` at most."""
    source = _DECLARATION.sub(' ', _without_comments((markup or '')[:MAX_HTML_CHARS]))
    paragraphs: list[tuple[int, int, str]] = []   # where it starts and ends in the page, its text
    partial = False
    opened: int | None = None
    kept, pieces = True, []
    skip = position = 0
    for tag in _TAG.finditer(source):
        if opened is not None and not skip:
            pieces.append(source[position:tag.start()])
        position = tag.end()
        closing, name, attrs = tag.group(1), (tag.group(2) or '').lower(), tag.group(3)
        found = _CLASS.search(attrs) if 'class' in attrs.lower() else None
        classes = (found.group(1) or found.group(2) or found.group(3) or '').lower() if found else ''
        partial = partial or 'paywall' in classes
        if attrs.rstrip().endswith('/'):
            continue
        if name in _SKIP_TAGS:
            skip = max(0, skip + (-1 if closing else 1))
        elif name == 'br' and opened is not None:
            pieces.append(' ')
        elif name == 'p':
            if opened is not None:   # </p>, or a new <p>: it closes the one before, as in a browser
                text = ' '.join(html.unescape(''.join(pieces)).split())
                if kept and len(text) >= _ARTICLE_MIN:
                    paragraphs.append((opened, tag.end() if closing else tag.start(), text))
                opened = None
            if not closing:
                opened, pieces = tag.start(), []
                kept = not any(word in classes for word in _ARTICLE_LEFT_OUT)
    blocks: list[list] = []   # [texts, end in the page, characters]
    for start, end, text in paragraphs:
        if blocks and start - blocks[-1][1] < _ARTICLE_GAP:
            blocks[-1][0].append(text)
            blocks[-1][1] = end
            blocks[-1][2] += len(text)
        else:
            blocks.append([[text], end, len(text)])
    best = max(blocks, key=lambda block: block[2])[0] if blocks else []
    chosen: list[str] = []
    for text in best:
        if chosen and len('\n'.join(chosen)) + len(text) > max_chars:
            break
        chosen.append(text[:max_chars])
    return '\n'.join(chosen), partial


def unreadable(text: str) -> bool:
    """A scrambled page is not a source (Athéna, 05/10: ~44 % U+FFFD on an Amazon page): over 5 % of
    control or replacement characters, refused."""
    if not text:
        return True
    bad = len(re.findall(r'[\x00-\x08\x0e-\x1f\x7f-\x9f�]', text))
    return bad / len(text) > 0.05


def relevant_passage(text: str, query: str, size: int = 900) -> str:
    """The ~900-character window holding the most words of the question (Athéna's passagePertinent),
    else the start. Lower case only: removing accents would change the length and shift the window."""
    flat = ' '.join((text or '').split())
    if len(flat) <= size:
        return flat
    words = [w for w in re.split(r'[^a-z0-9àâäçéèêëîïôöùûüÿœæ-]+', (query or '').lower()) if len(w) > 3]
    low = flat.lower()
    best, score = 0, -1
    for start in range(0, len(low), 150):
        window = low[start:start + size]
        hits = sum(1 for w in words if w in window)
        if hits > score:
            best, score = start, hits
    return flat[best:best + size]


def passage_of(markup: str, query: str) -> str:
    """The useful passage of a page for the question, or '' when the page is scrambled (run in a thread)."""
    text = page_text(markup)
    return '' if unreadable(text) else relevant_passage(text, query)
