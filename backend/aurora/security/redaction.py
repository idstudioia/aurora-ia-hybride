"""PII and secret masking applied before anything leaves the machine (CONTRACT.md §0.4).

`redact()` is idempotent: masks such as [EMAIL] never match a detector again. Detectors run
from the most specific (secrets, structured identifiers) to the most generic (phone numbers),
so a card number is never half-masked as a phone number.
"""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from typing import Callable

LABELS_FR = {
    'api_key': 'clé API', 'token': 'jeton', 'password': 'mot de passe', 'email': 'adresse e-mail',
    'iban': 'IBAN', 'card': 'carte bancaire', 'nir': 'numéro de sécurité sociale', 'ip': 'adresse IP',
    'user_path': "chemin contenant un nom d'utilisateur", 'phone': 'numéro de téléphone',
}
MASKS = {
    'api_key': '[CLÉ_API]', 'token': '[JETON]', 'password': '[MOT_DE_PASSE]', 'email': '[EMAIL]',
    'iban': '[IBAN]', 'card': '[CARTE]', 'nir': '[NIR]', 'ip': '[IP]', 'user_path': '[UTILISATEUR]',
    'phone': '[TÉLÉPHONE]',
}
# Finding types that make a text sensitive on their own (credentials, financial or state identifiers).
SENSITIVE_TYPES = frozenset({'api_key', 'token', 'password', 'iban', 'card', 'nir'})

_API_KEY = re.compile(
    r'(?<![\w-])(?:'
    r'sk-ant-[A-Za-z0-9_-]{8,}'
    r'|sk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{16,}'
    r'|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}'
    r'|hf_[A-Za-z0-9]{20,}'
    r'|glpat-[A-Za-z0-9_-]{20,}'
    r'|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}'
    r'|github_pat_[A-Za-z0-9_]{20,}'
    r'|(?:AKIA|ASIA)[0-9A-Z]{16}'
    r'|xox[abposr]-[A-Za-z0-9-]{10,}'
    r'|AIza[0-9A-Za-z_-]{35}'
    r')')
_JWT = re.compile(r'(?<![\w-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}')
_BEARER = re.compile(r'(?i)(\bBearer\s+)([A-Za-z0-9._~+/-]{20,}=*)')
# Configuration-style secrets: NAME_API_KEY=value, client_secret: value, DB_PASSWORD = value…
_SECRET_ASSIGN = re.compile(
    # bounded name without '.' / '-' : linear time even on long dotted strings
    r'(?i)((?<![\w.-])[A-Za-z0-9_]{0,40}?(?:secret|api[_-]?key|apikey|token|password|passwd)[A-Za-z0-9_]{0,40}'
    r'\s*[=:]\s*)'
    r'("[^"\n]{8,256}"|\'[^\'\n]{8,256}\'|[^\s"\',;]{8,256})')
_SECRET_VERB = r"(?:est(?![\w'’-])|c[’']est(?![\w'’-])|is(?![\w'’-])|:=|=|:)"
# A few words between the keyword and the verb: « mot de passe Wi-Fi est », « code de l'alarme de la maison est ».
_GAP = r"(?:\s+(?!(?:est|c[’']est|is)(?![\w'’-]))[\w'’-]+){0,6}?"
_PASSWORD = re.compile(
    r'(?i)(\b(?:mot\s+de\s+passe|mdp|password|passwd|pwd|passphrase|code\s+secret|code\s+pin|code\s+d[’\']acc[eè]s)\b'
    + _GAP + r'\s*' + _SECRET_VERB + r'\s*)'
    r'("[^"\n]{1,128}"|\'[^\'\n]{1,128}\'|[^\s"\',;]{1,128})')
# Weaker words (« le code de l'alarme est 4821-7730 », « mon PIN : 1234 »): the value must hold a digit.
_SECRET_CODE = re.compile(
    r'(?i)(\b(?:code|codes|pin|cl[ée]|key|jeton|token|secret)\b(?!\s+(?:postal|source|couleur|de\s+la\s+route))'
    + _GAP + r'\s*' + _SECRET_VERB + r'\s*)'
    r'("[^"\n]*\d[^"\n]*"|\'[^\'\n]*\d[^\'\n]*\'|(?=[^\s"\',;]*\d)[^\s"\',;.]{3,128})')
_EMAIL = re.compile(r'(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b')
_IBAN = re.compile(r'(?i)\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b')
_CARD = re.compile(r'(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])')
_NIR = re.compile(r'(?<!\d)[12][ ]?\d{2}[ ]?(?:0[1-9]|1[0-2]|[2-9]\d)[ ]?(?:\d{2}|2[AB])[ ]?\d{3}[ ]?\d{3}[ ]?(\d{2})(?!\d)')
_IPV4 = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
_IPV6 = re.compile(r'(?<![\w:.])(?=[0-9A-Fa-f:]*:[0-9A-Fa-f:]*:)[0-9A-Fa-f:]{3,39}(?:%\w+)?(?![\w:])')
_USER_PATH = re.compile(
    r'(?i)((?:\b[A-Z]:|\\\\\?\\[A-Z]:)[\\/]+(?:Users|Utilisateurs|Documents and Settings)[\\/]+)'
    r'(?!\[UTILISATEUR\])([^\\/\s:*?"<>|]{1,32}(?: [^\\/\s:*?"<>|]{1,32}){0,2}(?=[\\/])|[^\\/\s:*?"<>|]{1,64})')
_UNIX_HOME = re.compile(r'(?<![\w/])(/(?:home|Users)/)(?!\[UTILISATEUR\])([A-Za-z0-9._-]{1,64})')
_PUBLIC_PROFILES = {'public', 'default', 'default user', 'all users'}
_PHONE_FR = re.compile(r'(?<![\w+])0[1-9](?:[ .-]?\d{2}){4}(?![\w])')
_PHONE_INTL = re.compile(r'(?<![\w+])(?:\+|00)\d{1,3}[ .-]?(?:\(0\)[ .-]?)?\(?\d{1,4}\)?(?:[ .-]?\d{2,4}){2,5}(?![\w])')


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _iban_ok(candidate: str) -> bool:
    compact = candidate.replace(' ', '')
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    try:
        number = int(''.join(str(int(ch, 36)) for ch in rearranged))
    except ValueError:
        return False
    return number % 97 == 1


def _nir_ok(match: re.Match) -> bool:
    compact = match.group(0).replace(' ', '').upper()
    body, key = compact[:13], int(compact[13:])
    body = body.replace('2A', '19').replace('2B', '18')
    try:
        return 97 - (int(body) % 97) == key
    except ValueError:
        return False


def _ip_ok(candidate: str) -> bool:
    try:
        address = ipaddress.ip_address(candidate.split('%')[0])
    except ValueError:
        return False
    return not (address.is_loopback or address.is_unspecified)


def _apply(text: str, pattern: re.Pattern, kind: str, counts: dict[str, int],
           *, keep_group: int | None = None, check: Callable[[re.Match], bool] | None = None) -> str:
    def replace(match: re.Match) -> str:
        if check is not None and not check(match):
            return match.group(0)
        counts[kind] = counts.get(kind, 0) + 1
        prefix = match.group(keep_group) if keep_group else ''
        return prefix + MASKS[kind]
    return pattern.sub(replace, text)


def redact(text: str) -> tuple[str, list[dict]]:
    """Mask PII/secrets. Returns (masked text, findings [{type, count, label}])."""
    if not text:
        return text or '', []
    counts: dict[str, int] = {}
    out = text
    out = _apply(out, _API_KEY, 'api_key', counts)
    out = _apply(out, _JWT, 'token', counts)
    out = _apply(out, _BEARER, 'token', counts, keep_group=1)
    not_masked = lambda m: not m.group(2).startswith('[') or not m.group(2).endswith(']')  # noqa: E731
    out = _apply(out, _SECRET_ASSIGN, 'api_key', counts, keep_group=1, check=not_masked)
    out = _apply(out, _PASSWORD, 'password', counts, keep_group=1, check=not_masked)
    out = _apply(out, _SECRET_CODE, 'password', counts, keep_group=1, check=not_masked)
    out = _apply(out, _EMAIL, 'email', counts)
    out = _apply(out, _IBAN, 'iban', counts, check=lambda m: _iban_ok(m.group(0).upper()))
    out = _apply(out, _NIR, 'nir', counts, check=_nir_ok)
    out = _apply(out, _CARD, 'card', counts,
                 check=lambda m: 13 <= len(re.sub(r'\D', '', m.group(0))) <= 19
                 and _luhn_ok(re.sub(r'\D', '', m.group(0))))
    out = _apply(out, _IPV4, 'ip', counts, check=lambda m: _ip_ok(m.group(0)))
    out = _apply(out, _IPV6, 'ip', counts, check=lambda m: _ip_ok(m.group(0)))
    out = _apply(out, _USER_PATH, 'user_path', counts, keep_group=1,
                 check=lambda m: m.group(2).strip().lower() not in _PUBLIC_PROFILES)
    out = _apply(out, _UNIX_HOME, 'user_path', counts, keep_group=1)
    out = _apply(out, _PHONE_INTL, 'phone', counts,
                 check=lambda m: 8 <= len(re.sub(r'\D', '', m.group(0))) <= 15)
    out = _apply(out, _PHONE_FR, 'phone', counts)
    findings = [{'type': kind, 'count': count, 'label': LABELS_FR[kind]} for kind, count in counts.items()]
    return out, findings


_GLUED_TAIL = re.compile(r'[^\w\s"\',;]+')


def restore_secret_tail(content: str, original: str) -> str:
    """Put back the end punctuation an extractor stripped from ``content`` when, in ``original``, it is
    glued to a secret value: « … est Soleil2024! » keeps its « ! ». A sentence end that is not part of
    a secret (« … est le bleu. », « … est Soleil2024 ! ») stays stripped. Where a secret ends is decided
    by the detectors of redact(), not by a second rule."""
    core = (content or '').rstrip()
    index = (original or '').rfind(core) if core else -1
    if index < 0:
        return content
    tail = _GLUED_TAIL.match(original, index + len(core))
    if not tail:
        return content
    masked, findings = redact(core + tail.group(0))
    if any(f['type'] in SENSITIVE_TYPES for f in findings) and masked.endswith(
            tuple(MASKS[kind] for kind in SENSITIVE_TYPES)):
        return core + tail.group(0)   # the detector took the punctuation as part of the value
    return content


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize('NFKD', text.lower())
    return ''.join(ch for ch in decomposed if not unicodedata.combining(ch))


# Topics that make a request sensitive even without identifiers (health, finances, legal, intimacy).
# Matched on accent-folded lowercase text, word-bounded. Kept specific to avoid flagging
# everyday AURORA requests ("diagnostic du PC", "traitement de texte").
_SENSITIVE_TOPICS = re.compile(r'\b(?:' + '|'.join([
    r'dossier medical', r'diagnostic medical', r'ordonnance', r'mon medecin', r'ma maladie', r'mes symptomes',
    r'ma sante', r'mon traitement medical', r'ma depression', r'je suis enceinte', r'grossesse',
    r'seropositi\w*',
    r'medical record', r'my diagnosis', r'my illness', r'prescription',
    r'mon salaire', r'mes revenus', r'ma declaration d.impots', r'avis d.imposition', r'mes dettes',
    r'releve bancaire', r'releve de compte', r'code confidentiel', r'numero de securite sociale',
    r'carte vitale', r'passeport', r'carte d.identite', r'bank statement', r'social security',
    r'casier judiciaire', r'mon avocat', r'plainte contre', r'mon divorce',
    r'orientation sexuelle', r'mes opinions politiques', r'ma religion',
    r'confidentiel', r'strictement prive', r'top secret',
]) + r')\b')
# A secret is being talked about even when its value is not recognised (« mon mot de passe Wi-Fi… »).
_SECRET_WORDS = re.compile(r'\b(?:' + '|'.join([
    r'mots? de passe', r'mdp', r'passwords?', r'passwd', r'passphrase', r'code secret', r'code pin',
    r'code d.acces', r'codes? (?:de l.|d.)alarme', r'cle api', r'api ?key', r'cles? secretes?', r'cle privee',
    r'private key', r'secret key', r'access token', r'jeton d.acces', r'identifiants? de connexion',
]) + r')\b')


def sensitivity_of(text: str) -> str:
    """'sensitive' if the text holds credentials/financial identifiers, a sensitive topic or talks
    about a secret (password, PIN, API key…) even when the value itself is not recognised."""
    if not text:
        return 'normal'
    _, findings = redact(text)
    if any(f['type'] in SENSITIVE_TYPES for f in findings):
        return 'sensitive'
    folded = _fold(text)
    return 'sensitive' if _SENSITIVE_TOPICS.search(folded) or _SECRET_WORDS.search(folded) else 'normal'


def summarize_findings(findings: list[dict]) -> str:
    """French one-liner for the consent preview, e.g. « 2 adresses e-mail, 1 IBAN masqués »."""
    if not findings:
        return 'Aucune donnée personnelle détectée.'
    parts = [f"{f['count']} × {f.get('label') or LABELS_FR.get(f['type'], f['type'])}" for f in findings]
    return 'Masqué avant envoi : ' + ', '.join(parts) + '.'
