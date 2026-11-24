"""French normalisation (accents, typos, voice transcription, wake words) and slot extraction.

Everything here is pure and deterministic: the same input always gives the same output.
`normalize()` is what the rule and similarity routers see; slots are extracted from the
ORIGINAL text whenever case or accents matter (cities, search queries, memory content).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

__all__ = [
    'normalize', 'strip_accents', 'strip_wake_word', 'split_clauses', 'extract_slots',
    'extract_city', 'extract_expression', 'extract_conversion', 'extract_folder',
    'extract_older_than_days', 'extract_query', 'extract_metric', 'extract_sort',
    'extract_orbit', 'extract_page', 'extract_process', 'extract_topic',
    'extract_memory_content', 'extract_date', 'words_to_numbers',
    'scene_subject', 'scene_3d_asked', 'scene_request_3d', 'extract_scene_order',
]

# --------------------------------------------------------------------------- characters

_SYMBOLS = {
    ' ': ' ', ' ': ' ', '×': ' x ', '✕': ' x ', '÷': ' / ', '−': '-', '–': '-', '—': ' ',
    '²': ' au carre ', '³': ' au cube ', '√': ' racine de ', '℃': ' degres c ', '℉': ' degres f ',
    '°': ' degres ', '€': ' euros ', '$': ' dollars ', '&': ' et ', '’': "'", '‘': "'", '`': "'",
    '´': "'", 'ʼ': "'", '«': ' ', '»': ' ', 'œ': 'oe', 'Œ': 'oe', 'æ': 'ae', 'Æ': 'ae', 'ß': 'ss',
}
_SUPERSCRIPT_AFTER_LETTER = re.compile(r'(?<=[A-Za-zÀ-ÿ])([²³])')


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(ch for ch in decomposed if not unicodedata.combining(ch))


def _replace_symbols(text: str) -> str:
    s = _SUPERSCRIPT_AFTER_LETTER.sub(lambda m: '2' if m.group(1) == '²' else '3', text)
    for key, value in _SYMBOLS.items():
        if key in s:
            s = s.replace(key, value)
    return s


def _clean_chars(s: str) -> str:
    """Lower-case ascii text -> words, numbers and math operators separated by spaces."""
    out: list[str] = []
    n = len(s)
    for i, ch in enumerate(s):
        if ch.isalnum():
            out.append(ch)
        elif ch in '.,':
            if 0 < i < n - 1 and s[i - 1].isdigit() and s[i + 1].isdigit():
                out.append('.')
            else:
                out.append(' ')
        elif ch == '-':
            j = i - 1
            while j >= 0 and s[j] == ' ':
                j -= 1
            k = i + 1
            while k < n and s[k] == ' ':
                k += 1
            prev = s[j] if j >= 0 else ''
            nxt = s[k] if k < n else ''
            if (prev.isdigit() or prev == ')') and (nxt.isdigit() or nxt == '('):
                out.append(' - ')
            elif nxt.isdigit() and k == i + 1 and (i == 0 or s[i - 1] == ' ' or prev in '+*/^=(%'):
                out.append(' -')
            else:
                out.append(' ')
        elif ch in '+*/^=%()':
            out.append(f' {ch} ')
        else:
            out.append(' ')
    return ''.join(out)


# --------------------------------------------------------------------------- typos & voice

# « temp » followed by one of these (within a few words) is a hardware temperature, never the weather.
_HARDWARE = frozenset('cpu gpu processeur processeurs proco proc pc ordinateur ordi machine composants graphique '
                      'ssd hdd disque coeurs'.split())
_HW_AHEAD = r'(?! (?:\S+ ){0,4}(?:' + '|'.join(sorted(_HARDWARE)) + r') )'

# Applied on the padded, cleaned string (order matters).
_PRE_FIXES: list[tuple[re.Pattern, str]] = [(re.compile(p), r) for p, r in [
    (r' s il (?:te|vous) plait ', ' '),
    (r' (?:stp|svp|steuplait|stplait|silteplait) ', ' '),
    (r' (?:c p u|ce pe u|ce pe hu|se pe u) ', ' cpu '),
    (r' (?:g p u|ge pe u) ', ' gpu '),
    (r' (?:p c|pe ce|pece|pecee) ', ' pc '),
    # « quelle temp fait le CPU » keeps « temp » (-> temperature in _fix_tokens); « quel temp fait-il » = weather
    (r' (?:quel|quelle|quels|quelles|kel|kelle) (?:temp' + _HW_AHEAD + r'|tant|tan|tps|tems|temps) ', ' quel temps '),
    (r' (?:wi fi|oui fi|ouifi|wii fi|wifii) ', ' wifi '),
    (r' (?:e mails?|imels?|e mel) ', ' email '),
    (r' tele charg\w* ', ' telechargements '),
    (r' aujourdhui ', ' aujourd hui '),
    (r' (?:keske|kesk|keskeu) ', ' qu est ce que '),
    (r' ya t il ', ' y a t il '),
    (r' ya ', ' y a '),
    (r' (?:o|oh|au|aux|ho) rora ', ' aurora '),
    (r' (?:au|o) rore ', ' aurore '),
]]

_TOKEN_FIXES: dict[str, str] = {
    # SMS / spoken shortcuts
    'kel': 'quel', 'kelle': 'quelle', 'kels': 'quels', 'koi': 'quoi', 'pk': 'pourquoi', 'pq': 'pourquoi',
    'pourkoi': 'pourquoi', 'pourqoi': 'pourquoi', 'cmb': 'combien', 'cb': 'combien', 'conbien': 'combien',
    'jsuis': 'je suis', 'chui': 'je suis', 'jai': 'j ai', 'cest': 'c est', 'ct': 'c etait', 'bcp': 'beaucoup',
    'tjrs': 'toujours', 'auj': 'aujourd hui', 'ajd': 'aujourd hui', 'mtn': 'maintenant', 'qd': 'quand',
    'pr': 'pour', 'ds': 'dans', 'tt': 'tout', 'vrmt': 'vraiment', 'pcq': 'parce que', 'dsl': 'desole',
    'ptit': 'petit', 'qqch': 'quelque chose', 'qqn': 'quelqu un', 'tps': 'temps',
    # spoken fillers (voice transcription)
    'euh': '', 'euhh': '', 'heu': '', 'heuu': '', 'hum': '', 'hmm': '', 'bah': '', 'beh': '', 'mmh': '',
    # vocabulary of the assistant
    'ordi': 'ordinateur', 'ordis': 'ordinateurs', 'ordinatuer': 'ordinateur', 'ordinnateur': 'ordinateur',
    'ordinateure': 'ordinateur', 'pcs': 'pc', 'proco': 'processeur', 'proc': 'processeur',
    'metteo': 'meteo', 'meteoo': 'meteo', 'meteos': 'meteo',
    'telechargement': 'telechargements', 'telechargment': 'telechargements', 'telechargments': 'telechargements',
    'telechargemens': 'telechargements', 'telechargemnt': 'telechargements', 'telechargemnts': 'telechargements',
    'downloads': 'telechargements',
    'actu': 'actualite', 'actus': 'actualites', 'news': 'actualites',
    'appli': 'application', 'applis': 'applications', 'app': 'application', 'apps': 'applications',
    'params': 'parametres', 'parametre': 'parametres', 'reglage': 'reglages', 'config': 'configuration',
    'seime': 'seisme', 'seimes': 'seismes', 'seisme': 'seisme', 'sisme': 'seisme', 'sismes': 'seismes',
    'fichies': 'fichiers', 'fichers': 'fichiers', 'fichie': 'fichier', 'ficher': 'fichier',
    'memoir': 'memoire', 'memore': 'memoire',
    'netoie': 'nettoie', 'nettoi': 'nettoie', 'netoyer': 'nettoyer', 'netoyage': 'nettoyage',
    'suprime': 'supprime', 'suprimer': 'supprimer', 'supprimme': 'supprime', 'suprimme': 'supprime',
    'recherhe': 'recherche', 'recheche': 'recherche', 'recherce': 'recherche', 'reserche': 'recherche',
    'interner': 'internet', 'internette': 'internet',
    'baterie': 'batterie', 'battrie': 'batterie', 'raiseau': 'reseau', 'rezo': 'reseau', 'reso': 'reseau',
    'planette': 'planete', 'planettes': 'planetes', 'orbitte': 'orbite', 'trajectoir': 'trajectoire',
    'analise': 'analyse', 'annalyse': 'analyse', 'analize': 'analyse', 'analyze': 'analyse',
    'simmule': 'simule', 'simul': 'simule', 'heur': 'heure', 'retien': 'retiens', 'souvien': 'souviens',
    'crome': 'chrome', 'chrom': 'chrome', 'spotifi': 'spotify', 'diskord': 'discord',
    'kilometre': 'kilometres', 'kilometer': 'kilometres', 'kilometers': 'kilometres',
    'farenheit': 'fahrenheit', 'fahreneit': 'fahrenheit', 'celcius': 'celsius', 'celsus': 'celsius',
    'gigas': 'giga', 'diagnostik': 'diagnostic', 'ralentit': 'ralentit', 'ralenti': 'ralentit',
    'ralentie': 'ralentit', 'ralentis': 'ralentit',
}

# Words before / after "rame" that mean the RAM (otherwise "ça rame" = it lags).
_RAM_LEFT = {'la', 'de', 'ma', 'ta', 'sa', 'en', 'quelle', 'combien', 'utilisation', 'usage', 'du', 'cette',
             'barrette', 'barrettes', 'go', 'giga', 'gb', 'mo', 'memoire', 'quantite', 'niveau', 'votre', 'notre'}
_RAM_RIGHT = {'utilisee', 'utilise', 'libre', 'disponible', 'occupee', 'consommee', 'installee', 'totale',
              'restante', 'vive', 'est', 'utilisees'}

# Fuzzy correction vocabulary: trigger words of the routers (>= 6 letters, normalised).
_VOCAB = frozenset('''
ordinateur ordinateurs processeur processus programme programmes application applications logiciel logiciels
memoire utilisation utilisee consomme consomment disponible batterie stockage reseau connexion temperature
graphique diagnostic diagnostique ralentit ralentir lenteur plante chauffe surchauffe bloque optimise
optimiser optimisation probleme pourquoi recherche rechercher cherche chercher internet wikipedia
decouvertes decouverte actualite actualites nouvelles scientifique scientifiques spatiale spatiales
retrouve retrouver souviens rappelle memorise retiens souvenirs conversation discussion projets
pleuvoir previsions parapluie seisme seismes tremblement tremblements sismique magnitude calcule
calculer combien multiplie divise puissance racine convertis convertir conversion simule simuler
simulation trajectoire orbite orbitale satellite satellites planetes planete systeme solaire projet
dossier document souvenir conversations discussions programme logiciel application processus
fichiers fichier dossier dossiers telechargements documents document supprime supprimer efface effacer
nettoie nettoyer nettoyage quarantaine analyse analyser resume resumer rapport organise organiser
planifie planifier planning semaine affiche afficher montre montrer parametres reglages accueil
simulations taches capacites fonctionnalites connecte cyclones ouragan aujourd maintenant actuellement
'''.split())


def _inflections(word: str) -> set[str]:
    forms = {word, word + 's', word + 'e', word + 'es', word + 'r', word + 'nt'}
    for suffix in ('s', 'e', 'es', 'ee', 'ees', 'r', 'nt'):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            forms.add(word[:-len(suffix)])
    return forms


# Inflected forms of trigger words are valid words: never "correct" them into another form.
_KNOWN = frozenset(form for word in _VOCAB for form in _inflections(word))
# Real words that are one edit away from a trigger word and must never be "corrected".
_NO_FUZZY = frozenset('''
chercheur chercheurs organisme organismes combine reviens revient soutiens soutient salaire simple espece
especes titres planter plantes affaire montres mettre lettres position positions recherches analyste analystes
'''.split())


def _edit_kind(a: str, b: str) -> str | None:
    """Return the kind of a single Damerau edit turning a into b, or None if distance != 1."""
    la, lb = len(a), len(b)
    if a == b or abs(la - lb) > 1:
        return None
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return 'sub'
        if len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]:
            return 'trans'
        return None
    short, long_ = (a, b) if la < lb else (b, a)
    for i in range(len(long_)):
        if long_[:i] + long_[i + 1:] == short:
            return 'ins' if la < lb else 'del'
    return None


@lru_cache(maxsize=8192)
def _fuzzy(token: str) -> str:
    if len(token) < 6 or not token.isalpha() or token in _KNOWN or token in _NO_FUZZY:
        return token
    found: set[str] = set()
    for word in _VOCAB:
        if word[0] != token[0] or abs(len(word) - len(token)) > 1:
            continue
        kind = _edit_kind(token, word)
        if kind is None:
            continue
        if kind == 'sub' and len(token) < 9:
            continue  # substitutions on short words create too many false friends
        found.add(word)
    if len(found) == 1:
        return next(iter(found))
    return token


def _fix_tokens(tokens: list[str]) -> list[str]:
    out: list[str] = []
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok in ('rame', 'rames', 'ramme', 'rammes'):
            left = tokens[i - 1] if i > 0 else ''
            right = tokens[i + 1] if i + 1 < n else ''
            out.append('ram' if (left in _RAM_LEFT or right in _RAM_RIGHT) else 'rame')
            continue
        if tok == 'temp':
            left = tokens[i - 1] if i > 0 else ''
            if left in ('fichier', 'fichiers'):
                out.append('temporaires')      # « fichiers temp » = temporary files
            elif left in ('dossier', 'repertoire'):
                out.append('temp')             # the folder named Temp
            elif _HARDWARE.intersection(tokens[i + 1:i + 6]):
                out.append('temperature')      # « quelle temp fait le CPU » : hardware, never the weather
            else:
                out.append('temps' if left in ('quel', 'quelle', 'le', 'du', 'beau', 'mauvais') else 'temperature')
            continue
        fixed = _TOKEN_FIXES.get(tok)
        if fixed is None:
            fixed = _fuzzy(tok)
        out.extend(fixed.split())
    return out


_WAKE = {'aurora', 'astra', 'aurore'}
_WAKE_PREFIX = {'ok', 'okay', 'hey', 'he', 'eh', 'dis', 'dites', 'salut', 'bonjour', 'coucou', 'allo', 'yo',
                'oh', 'ho', 'bonsoir', 'hello', 'hi'}


# A trailing "aurora" after one of these words is a name ("notre projet AURORA"), not a vocative.
_WAKE_KEEP_BEFORE = {
    'le', 'la', 'les', 'l', 'un', 'une', 'mon', 'ma', 'mes', 'ton', 'ta', 'tes', 'notre', 'nos', 'votre', 'vos',
    'son', 'sa', 'ses', 'de', 'd', 'du', 'des', 'sur', 'avec', 'pour', 'sans', 'projet', 'projets', 'application',
    'appli', 'app', 'logiciel', 'programme', 'code', 'version', 'dossier', 'fichier', 'assistant', 'systeme',
    'interface', 'depot', 'repo', 'nomme', 'appele', 'appelle'}


def _strip_wake_tokens(tokens: list[str]) -> list[str]:
    for _ in range(2):
        if len(tokens) >= 2 and tokens[0] in _WAKE_PREFIX and tokens[1] in _WAKE:
            tokens = tokens[2:]
        elif tokens and tokens[0] in _WAKE:
            tokens = tokens[1:]
    if tokens and tokens[-1] in _WAKE and not (len(tokens) >= 2 and tokens[-2] in _WAKE_KEEP_BEFORE):
        tokens = tokens[:-1]
    return tokens


def _mask_paths(text: str) -> str:
    """Windows paths -> one neutral token, so that « C:/Windows/Temp » is never read as words (« windows » = PC,
    « temp » = température). The path itself is taken verbatim from the original text by extract_folder."""
    spans = _path_spans(text)
    if not spans:
        return text
    out, last = [], 0
    for start, end, path in spans:
        ext = _FILE_EXT.search(path.rstrip('\\/'))
        out.append(text[last:start])
        out.append(f' cheminfichier {ext.group(1).lower()} ' if ext else ' cheminlocal ')
        last = end
    out.append(text[last:])
    return ''.join(out)


@lru_cache(maxsize=8192)
def _normalize_cached(text: str, strip_wake: bool = True) -> str:
    s = _clean_chars(strip_accents(_replace_symbols(_mask_paths(text))).lower())
    s = ' ' + ' '.join(s.split()) + ' '
    for pattern, repl in _PRE_FIXES:
        s = pattern.sub(repl, s)
        s = ' ' + ' '.join(s.split()) + ' '
    tokens = _fix_tokens(s.split())
    if strip_wake:
        tokens = _strip_wake_tokens(tokens)
    return ' '.join(tokens)


def normalize(text: str) -> str:
    """Lower case, no accents, punctuation -> spaces, common typos and voice errors fixed,
    wake word ("aurora", "ok astra"...) removed at the start, numbers kept."""
    if not text:
        return ''
    return _normalize_cached(str(text)[:4000])


# --------------------------------------------------------------------------- original text helpers

_WAKE_ORIG = re.compile(
    r"^\s*(?:(?:ok|okay|hey|h[ée]|eh|dis|dites|salut|bonjour|bonsoir|coucou|all[ôo]|oh|hello)\s*[,!]?\s+)?"
    r"(?:aurora|astra|aurore)\b[\s,:;.!?\-]*", re.I)
_WAKE_ORIG_TAIL = re.compile(r"([\s,]+)(?:aurora|astra|aurore)\s*([.!?]*)\s*$", re.I)
_LAST_WORD = re.compile(r"(\w+)\W*$")
_POLITE_ORIG = re.compile(r"[\s,]*\b(?:s'il (?:te|vous) pla[iî]t|stp|svp)\b[\s,]*", re.I)


def _strip_wake_tail(s: str) -> str:
    """Trailing wake word: removed when vocative (", Aurora ?" or "quelle heure est-il aurora"),
    kept when it names something ("notre projet AURORA")."""
    m = _WAKE_ORIG_TAIL.search(s)
    if not m:
        return s
    if ',' not in m.group(1):
        prev = _LAST_WORD.search(s[:m.start()])
        if prev and strip_accents(prev.group(1).lower()) in _WAKE_KEEP_BEFORE:
            return s
    return s[:m.start()] + m.group(2)


def strip_wake_word(text: str) -> str:
    """Remove the wake word and politeness from the ORIGINAL text (case and accents kept)."""
    s = (text or '').replace('’', "'").strip()
    for _ in range(2):
        s = _WAKE_ORIG.sub('', s, count=1)
    s = _strip_wake_tail(s)
    s = _POLITE_ORIG.sub(' ', s)
    return ' '.join(s.split())


_CLAUSE_SPLIT = re.compile(
    r"[,;:!?.]+(?=\s|$)|\s+(?:et puis|et ensuite|et aussi|et enfin|puis|ensuite|apr[eè]s quoi|enfin|ainsi que|et)\s+",
    re.I)


def split_clauses(text: str) -> list[str]:
    """Split a compound request into clauses (original text)."""
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(strip_wake_word(text)) if p and p.strip()]
    return parts


_ORIG_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _orig_tokens(text: str) -> list[tuple[int, int, str]]:
    """(start, end, normalised token) for each word of the original text."""
    out = []
    for m in _ORIG_WORD.finditer(text):
        norm = _normalize_cached(m.group(0), False)
        for part in norm.split() or ['']:
            out.append((m.start(), m.end(), part))
    return out


def as_written(text: str, part: str) -> str:
    """The words of the original text whose normalised form is `part` (accents and case kept: « mettre un commentaire
    sur une vidéo »), via _orig_tokens; `part` itself when they are not found as they are."""
    wanted = part.split()
    toks = _orig_tokens(text or '')
    for i in range(len(toks) - len(wanted) + 1):
        if wanted and [t[2] for t in toks[i:i + len(wanted)]] == wanted:
            return text[toks[i][0]:toks[i + len(wanted) - 1][1]]
    return part


def _trim_tokens(text: str, lead: set[str], trail: set[str], lead_phrases: tuple[str, ...] = ()) -> str:
    """Drop leading / trailing filler words, return the original substring in between."""
    toks = _orig_tokens(text)
    i, j = 0, len(toks)
    changed = True
    while changed and i < j:
        changed = False
        for phrase in lead_phrases:
            words = phrase.split()
            if [t[2] for t in toks[i:i + len(words)]] == words:
                i += len(words)
                changed = True
        while i < j and toks[i][2] in lead:
            i += 1
            changed = True
    while j > i and toks[j - 1][2] in trail:
        j -= 1
    if i >= j:
        return ''
    return text[toks[i][0]:toks[j - 1][1]].strip(" '\"-")


# --------------------------------------------------------------------------- numbers

_NUM_UNITS = {'zero': 0, 'un': 1, 'une': 1, 'deux': 2, 'trois': 3, 'quatre': 4, 'cinq': 5, 'six': 6, 'sept': 7,
              'huit': 8, 'neuf': 9, 'dix': 10, 'onze': 11, 'douze': 12, 'treize': 13, 'quatorze': 14,
              'quinze': 15, 'seize': 16}
_NUM_TENS = {'vingt': 20, 'vingts': 20, 'trente': 30, 'quarante': 40, 'cinquante': 50, 'soixante': 60,
             'septante': 70, 'huitante': 80, 'octante': 80, 'nonante': 90}
_NUM_WORDS = set(_NUM_UNITS) | set(_NUM_TENS) | {'cent', 'cents', 'mille'}


def words_to_numbers(text: str) -> str:
    """'douze fois trois' -> '12 fois 3' on normalised text (simple French numerals up to millions)."""
    tokens = text.split()
    out: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        if tokens[i] not in _NUM_WORDS:
            out.append(tokens[i])
            i += 1
            continue
        total, current, j = 0, 0, i
        while j < n:
            t = tokens[j]
            if t in _NUM_UNITS:
                current += _NUM_UNITS[t]
            elif t in _NUM_TENS:
                current = 80 if (current == 4 and t.startswith('vingt')) else current + _NUM_TENS[t]
            elif t in ('cent', 'cents'):
                current = (current or 1) * 100
            elif t == 'mille':
                total += (current or 1) * 1000
                current = 0
            elif t == 'et' and j + 1 < n and tokens[j + 1] in ('un', 'une', 'onze') and current >= 20:
                pass
            else:
                break
            j += 1
        out.append(str(total + current))
        i = j
    return ' '.join(out)


_NUM_RE = r'-?\d+(?:\.\d+)?'
_THOUSANDS = re.compile('(\\d)[   ](\\d{3})(?!\\d)')


def join_thousands(text: str) -> str:
    """'2 000 km', '35 786 km', '1 500,5' (space, U+00A0 or U+202F as thousands separator) -> '2000 km'…
    Applied to the ORIGINAL text before normalize(), which would otherwise split the number."""
    s = (text or '').replace('−', '-')
    for _ in range(4):
        joined = _THOUSANDS.sub(r'\1\2', s)
        if joined == s:
            break
        s = joined
    return s

# --------------------------------------------------------------------------- math expression

_MATH_TOKEN = re.compile(r'\d+(?:\.\d+)?|\*\*|[-+*/%()]|sqrt|cbrt|[a-z]+')


def _prep_math(text: str) -> str:
    s = strip_wake_word(text)
    s = s.replace('×', ' * ').replace('✕', ' * ').replace('÷', ' / ').replace('−', '-').replace('–', '-')
    s = re.sub(r'(?<=[\d)])\s*²', ' ** 2 ', s)
    s = re.sub(r'(?<=[\d)])\s*³', ' ** 3 ', s)
    s = s.replace('√', ' sqrt ').replace('^', ' ** ')
    s = strip_accents(s).lower().replace('’', "'")
    s = re.sub(r'(\d),(\d)', r'\1.\2', s)
    s = join_thousands(s)
    s = re.sub(r"[^a-z0-9.+\-*/%() ]", ' ', s)
    s = re.sub(r'(?<=\d)\s*x\s*(?=[\d(])', ' * ', s)
    s = words_to_numbers(' '.join(s.split()))
    rules = [
        (r'\b(?:multipliee?s?|multiplier|multiplie) par\b', ' * '), (r'\bfois\b', ' * '),
        (r'\b(?:divisee?s?|diviser|divise) par\b', ' / '), (r'(\d)\s+sur\s+(?=\d)', r'\1 / '),
        (r'\bplus\b', ' + '), (r'\bmoins\b', ' - '),
        (r'\b(?:a la )?puissance\b', ' ** '), (r'\bexposant\b', ' ** '),
        (r'\bau carre\b', ' ** 2 '), (r'\bau cube\b', ' ** 3 '),
        (r'\bracine cubique (?:de |d )?', ' cbrt '), (r'\bracine (?:carree )?(?:de |d )?', ' sqrt '),
        (r'(\d)\s*(?:%|pour ?cents?|pourcents?)\s*(?:de|d|du|des)\b', r'\1 / 100 * '),
        (r'\bmodulo\b', ' % '),
    ]
    for pattern, repl in rules:
        s = re.sub(pattern, repl, s)
    return ' '.join(s.split())


def _is_num(tok: str) -> bool:
    return bool(re.fullmatch(r'\d+(?:\.\d+)?', tok))


def extract_expression(text: str) -> str | None:
    """Arithmetic expression in Python-like syntax ('17 * 23', 'sqrt(144)', '2 ** 10') or None."""
    tokens = _MATH_TOKEN.findall(_prep_math(text))
    math_tokens = {'**', '+', '-', '*', '/', '%', '(', ')', 'sqrt', 'cbrt'}
    best: list[str] = []
    run: list[str] = []
    for tok in tokens + ['<end>']:
        if _is_num(tok) or tok in math_tokens:
            run.append(tok)
            continue
        if run:
            numbers = sum(1 for t in run if _is_num(t))
            has_op = any(t in math_tokens - {'(', ')'} for t in run)
            if numbers and has_op and numbers > sum(1 for t in best if _is_num(t)):
                best = run
        run = []
    if not best:
        return None
    while best and best[0] in {'**', '+', '*', '/', '%', ')'}:
        best = best[1:]
    while best and best[-1] in {'**', '+', '-', '*', '/', '%', '(', 'sqrt', 'cbrt'}:
        best = best[:-1]
    if not best or not any(_is_num(t) for t in best):
        return None
    # sqrt N / cbrt N -> function call
    out: list[str] = []
    i = 0
    while i < len(best):
        tok = best[i]
        if tok in ('sqrt', 'cbrt') and i + 1 < len(best) and _is_num(best[i + 1]):
            out.append(f'sqrt({best[i + 1]})' if tok == 'sqrt' else f'({best[i + 1]}) ** (1/3)')
            i += 2
            continue
        if tok == 'sqrt' and i + 1 < len(best) and best[i + 1] == '(':
            out.append('sqrt')
            i += 1
            continue
        if tok == 'cbrt':
            return None
        out.append(tok)
        i += 1
    binary = {'**', '+', '*', '/', '%'}
    for a, b in zip(out, out[1:]):
        if (a in binary or a == '-') and b in binary:
            return None
    expr = ' '.join(out).replace('( ', '(').replace(' )', ')').replace('sqrt (', 'sqrt(')
    if expr.count('(') != expr.count(')'):
        return None
    if re.fullmatch(r'-?\d+(?:\.\d+)?', expr):
        return None
    return expr


# --------------------------------------------------------------------------- unit conversion

_UNIT_ALIASES: dict[str, str] = {}
for canonical, aliases in {
    'km': 'kilometres kilometre km kms', 'm': 'metres metre m', 'cm': 'centimetres centimetre cm',
    'mm': 'millimetres millimetre mm', 'mi': 'miles mile mi', 'ft': 'pieds pied ft feet foot',
    'in': 'pouces pouce inches inch', 'yd': 'yards yard yd',
    'nmi': 'milles marins|mille marin|milles nautiques|mille nautique|nmi',
    'kg': 'kilogrammes kilogramme kilos kilo kg', 'g': 'grammes gramme g gr', 'mg': 'milligrammes milligramme mg',
    't': 'tonnes tonne', 'lb': 'livres livre lbs lb', 'oz': 'onces once oz',
    'l': 'litres litre l', 'ml': 'millilitres millilitre ml', 'cl': 'centilitres centilitre cl',
    'gal': 'gallons gallon gal',
    'celsius': 'degres celsius|degre celsius|celsius|degres c|degre c|degres',
    'fahrenheit': 'degres fahrenheit|degre fahrenheit|fahrenheit|degres f|degre f',
    'kelvin': 'kelvins kelvin',
    'km/h': 'kilometres par heure|kilometres heure|km par heure|km / h|km h|kmh',
    'mph': 'miles par heure|miles heure|mph', 'm/s': 'metres par seconde|metre par seconde|m / s',
    'kn': 'noeuds noeud',
    'o': 'octets octet', 'kb': 'kilooctets kilooctet ko kb', 'mb': 'megaoctets megaoctet mo mb',
    'gb': 'gigaoctets gigaoctet go gb giga', 'tb': 'teraoctets teraoctet to tb',
    's': 'secondes seconde sec', 'min': 'minutes minute min', 'h': 'heures heure h',
    'day': 'jours jour', 'week': 'semaines semaine', 'month': 'mois', 'year': 'annees annee ans an',
    'm2': 'metres carres|metre carre|m2|m au carre', 'km2': 'kilometres carres|kilometre carre|km2|km au carre',
    'ha': 'hectares hectare ha', 'acre': 'acres acre',
    'kwh': 'kilowattheures kilowattheure kwh', 'kcal': 'kilocalories kilocalorie kcal',
    'cal': 'calories calorie cal', 'kj': 'kilojoules kilojoule kj', 'j': 'joules joule',
    'EUR': 'euros euro eur', 'USD': 'dollars dollar usd', 'GBP': 'livres sterling|livre sterling|gbp',
    'DZD': 'dinars algeriens|dinar algerien|dinars|dinar|dzd', 'CHF': 'francs suisses|franc suisse|chf',
    'CAD': 'dollars canadiens|dollar canadien|cad', 'JPY': 'yens yen jpy',
}.items():
    for alias in (aliases.split('|') if '|' in aliases else aliases.split()):
        _UNIT_ALIASES[alias] = canonical

UNIT_PATTERN = '|'.join(re.escape(a) for a in sorted(_UNIT_ALIASES, key=len, reverse=True))
_U = rf'(?<![a-z0-9])(?:{UNIT_PATTERN})(?![a-z0-9])'

_CONV_PATTERNS = [
    re.compile(rf'(?P<v>{_NUM_RE})\s*(?P<u1>{_U})\s+(?:en|vers|=|a combien de|font combien de|fait combien de|'
               rf'ca fait combien de|ca fait combien en|c est combien en|c est combien de|combien de|combien en|'
               rf'font combien en|fait combien en|equivaut a combien de|equivalent en|equivaut en|egal combien de)\s+'
               rf'(?P<u2>{_U})'),
    re.compile(rf'combien (?:de |d |en )?(?P<u2>{_U})\s+(?:dans|pour|font|fait|y a t il dans|y a t il en|'
               rf'equivaut a|equivalent a|valent|vaut|represente|representent|en|d|de|a|pour faire)\s+'
               rf'(?:(?P<v>{_NUM_RE})\s*)?(?P<u1>{_U})'),
    re.compile(rf'(?P<v>{_NUM_RE})\s*(?P<u1>{_U})\s+(?:c est|ca fait|font|fait|ca represente)\s+combien\s+'
               rf'(?:de |d |en )?(?P<u2>{_U})'),
    re.compile(rf'(?:convertis|convertir|converti|convertit|conversion de|conversion|convertissez|transforme|'
               rf'passe)\s+(?:moi\s+)?(?P<v>{_NUM_RE})\s*(?P<u1>{_U})(?:\s+(?:en|vers|a)\s+(?P<u2>{_U}))?'),
    re.compile(rf'(?:convertis|convertir|converti|convertit|conversion|convertissez)\s+(?:moi\s+)?'
               rf'(?:des |les |du |de la |d )?(?P<u1>{_U})\s+(?:en|vers|a)\s+(?P<u2>{_U})'),
]


def extract_conversion(text: str) -> dict | None:
    s = words_to_numbers(normalize(join_thousands(text)))
    for pattern in _CONV_PATTERNS:
        m = pattern.search(s)
        if not m:
            continue
        u1 = m.group('u1')
        u2 = m.groupdict().get('u2')
        value_txt = m.groupdict().get('v')
        value = float(value_txt) if value_txt else 1.0
        return {
            'value': value,
            'from_unit': _UNIT_ALIASES.get(u1),
            'to_unit': _UNIT_ALIASES.get(u2) if u2 else None,
            'from_unit_text': u1,
            'to_unit_text': u2 or '',
        }
    return None


# --------------------------------------------------------------------------- city

KNOWN_CITIES: dict[str, str] = {strip_accents(c).lower().replace('-', ' ').replace("'", ' '): c for c in [
    'Paris', 'Lyon', 'Marseille', 'Toulouse', 'Nice', 'Nantes', 'Strasbourg', 'Montpellier', 'Bordeaux', 'Lille',
    'Rennes', 'Reims', 'Toulon', 'Grenoble', 'Dijon', 'Angers', 'Nîmes', 'Brest', 'Tours', 'Limoges',
    'Clermont-Ferrand', 'Le Havre', 'Saint-Étienne', 'Metz', 'Rouen', 'Caen', 'Orléans', 'Mulhouse', 'Perpignan',
    'Besançon', 'Ajaccio', 'Bastia', 'Avignon', 'Annecy', 'La Rochelle', 'Poitiers', 'Pau', 'Bayonne', 'Nancy',
    'Amiens', 'Le Mans', 'Brest', 'Lorient', 'Calais', 'Dunkerque', 'Cannes', 'Antibes', 'Versailles',
    'Bruxelles', 'Liège', 'Genève', 'Lausanne', 'Zurich', 'Luxembourg', 'Montréal', 'Québec',
    'Alger', 'Oran', 'Constantine', 'Annaba', 'Sétif', 'Béjaïa', 'Tizi Ouzou', 'Blida', 'Batna', 'Tlemcen',
    'Tunis', 'Sfax', 'Casablanca', 'Rabat', 'Marrakech', 'Fès', 'Tanger', 'Agadir', 'Dakar', 'Abidjan',
    'Londres', 'Berlin', 'Madrid', 'Barcelone', 'Rome', 'Milan', 'Lisbonne', 'Amsterdam', 'Vienne', 'Prague',
    'Varsovie', 'Athènes', 'Istanbul', 'Moscou', 'New York', 'Los Angeles', 'San Francisco', 'Chicago',
    'Miami', 'Tokyo', 'Pékin', 'Shanghai', 'Séoul', 'Bangkok', 'Singapour', 'Dubaï', 'Le Caire', 'Sydney',
    'Rio de Janeiro', 'Buenos Aires', 'Mexico', 'Toronto', 'Vancouver', 'Bamako', 'Nouakchott',
]}
_NOT_CITY = {'aurora', 'astra', 'claude', 'ollama', 'quel', 'quelle', 'quelles', 'quels', 'meteo', 'météo',
             'il', 'elle', 'est', 'mars', 'venus', 'vénus', 'jupiter', 'saturne', 'lune', 'la lune', 'terre',
             'mercure', 'neptune', 'uranus', 'pluton', 'soleil', 'internet', 'windows', 'linux'}
_TEMPORAL_TAIL = re.compile(
    r"\s+(?:demain(?: matin| soir| apres midi)?|apres demain|aujourd hui|ce soir|ce matin|cet apres midi|"
    r"cette nuit|maintenant|en ce moment|actuellement|cette semaine|ce week end|ce weekend|la semaine prochaine|"
    r"dans \d+ jours?|pour demain|pour aujourd hui|dehors|exactement|precisement|stp|svp)$")
_CITY_STOP = {'le', 'la', 'les', 'l', 'il', 'fait', 'temps', 'quel', 'quelle', 'moi', 'ici', 'chez', 'maison',
              'dehors', 'semaine', 'journee', 'jour', 'matin', 'soir', 'nuit', 'week', 'end', 'ma', 'mon', 'ville',
              'region', 'coin', 'exterieur', 'demain', 'aujourd', 'hui', 'meteo', 'pluie', 'vent', 'neige',
              'quoi', 'combien', 'degres', 'prevision', 'previsions', 'temperature', 'y', 'a', 't', 'va',
              'pleuvoir', 'neiger', 'beau', 'chaud', 'froid', 'un', 'une', 'des', 'du', 'de', 'en', 'au', 'aux',
              'sur', 'pour', 'dans', 'vers', 'ce', 'cette', 'mes', 'nos', 'notre'}
_CAP = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'\-]*"
_CITY_ORIG = re.compile(
    rf"(?:^|\s)(?:à|a|au|aux|en|sur|pour|de|du|d'|dans|vers)\s*({_CAP}(?:[\s\-](?:(?:sur|en|de|la|le|les|du|d')[\s\-]?)?{_CAP})*)")


def extract_city(text: str) -> str | None:
    original = strip_wake_word(text).replace('’', "'")
    for m in _CITY_ORIG.finditer(original):
        candidate = m.group(1).strip(" '-")
        if candidate.lower() in _NOT_CITY or strip_accents(candidate).lower() in _NOT_CITY:
            continue
        words = candidate.split()
        while words and strip_accents(words[-1]).lower() in _NOT_CITY:
            words.pop()
        if words:
            key = strip_accents(' '.join(words)).lower().replace('-', ' ').replace("'", ' ')
            for prefix in ('', 'le ', 'la ', 'les '):
                if prefix + key in KNOWN_CITIES:
                    return KNOWN_CITIES[prefix + key]
            return ' '.join(words)
    norm = normalize(text)
    padded = f' {norm} '
    best = None
    for key, display in KNOWN_CITIES.items():
        pos = padded.find(f' {key} ')
        if pos >= 0 and (best is None or len(key) > len(best[1])):
            best = (pos, key, display)
    if best:
        return best[2]
    # lower-case voice transcription: last words after a preposition
    tail = norm
    for _ in range(3):
        tail = _TEMPORAL_TAIL.sub('', tail)
    m = re.search(r'(?:^| )(?:a|au|aux|en|sur|pour|de|dans|vers) ((?:[a-z]+ ?){1,3})$', tail)
    if m:
        words = m.group(1).split()
        if words and not any(w in _CITY_STOP for w in words) and all(len(w) > 1 for w in words):
            if ' '.join(words) not in _NOT_CITY:
                return ' '.join(w.capitalize() for w in words)
    return None


_WEEKDAYS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')


def extract_forecast_days(text: str, *, today: date | None = None) -> list[int] | None:
    """Day offsets of a weather forecast question (0 = today): « demain » -> [1], « ce week-end » ->
    [samedi, dimanche], « cette semaine » -> [0..6]. None = current conditions."""
    s = words_to_numbers(normalize(text))
    weekday = (today or date.today()).weekday()
    if re.search(r'\bapres demain\b', s):
        return [2]
    if re.search(r'\bdemain\b', s):
        return [1]
    m = re.search(r'\bdans (\d+) jours?\b', s)
    if m:
        return [int(m.group(1))]   # beyond the forecast horizon the tool says so
    if re.search(r'\b(?:week end|weekend)\b', s):
        return [0] if weekday == 6 else [5 - weekday, 6 - weekday]
    if re.search(r'\b(?:cette semaine|de la semaine|la semaine prochaine|prochains jours|jours a venir)\b', s):
        return list(range(7))
    m = re.search(r'\b(' + '|'.join(_WEEKDAYS) + r')\b', s)
    if m and re.search(r'\b(?:fera|sera|va|prevision|previsions|prevu|prevue|pour|ce|cette)\b', s):
        return [(_WEEKDAYS.index(m.group(1)) - weekday) % 7]
    return None


# --------------------------------------------------------------------------- folders & files

_FOLDERS = [
    ('telechargements', 'downloads'), ('telecharger', 'downloads'), ('downloads', 'downloads'),
    ('mes documents', 'documents'), ('dossier documents', 'documents'), ('documents', 'documents'),
    ('bureau', 'desktop'), ('desktop', 'desktop'), ('images', 'pictures'), ('photos', 'pictures'),
    ('videos', 'videos'), ('musique', 'music'), ('musiques', 'music'),
]
# « C:/Windows/Temp », « C:\Users\x », « C:\ »; not preceded by a letter or digit (« https://… » is no path).
_WIN_PATH = re.compile(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s"\'<>|?*«»“”]*')
# Quoted path, spaces allowed: « C:\Users\Jean Dupont\Documents », "C:\Program Files\X".
_QUOTED_PATH = re.compile(r'["«“]\s*([A-Za-z]:[\\/][^"<>|?*«»“”]*?)\s*["»”]')
_FILE_EXT = re.compile(r'\.([A-Za-z][A-Za-z0-9]{0,4})$')


def _path_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, path) of the Windows paths of the ORIGINAL text, case and separators kept."""
    spans = [(m.start(), m.end(), m.group(1).rstrip()) for m in _QUOTED_PATH.finditer(text or '')]
    for m in _WIN_PATH.finditer(text or ''):
        if any(start <= m.start() < end for start, end, _ in spans):
            continue
        path = m.group(0).rstrip('.,;:)')
        spans.append((m.start(), m.start() + len(path), path))
    return sorted(spans)


# « du dossier scanme »: a folder named but unknown here is passed on (the tool matches the folders
# added in the settings, or says it does not know it) instead of silently becoming Téléchargements.
_NAMED_FOLDER = re.compile(r'\b(?:dossier|repertoire|dans le dossier|du dossier)\s+(?:(?:de|du|des|d|le|la|les|l|'
                           r'mon|ma|mes|nomme|appele)\s+)*([a-z0-9][a-z0-9_.-]*)')
_NOT_FOLDER_NAME = {'qui', 'que', 'plus', 'avec', 'sans', 'pour', 'en', 'et', 'ou', 'a', 'dans', 'sur', 'par',
                    'telechargements', 'documents', 'bureau', 'fichiers', 'fichier', 'entier', 'complet', 'courant'}


def extract_folder(text: str) -> str | None:
    spans = _path_spans(text)
    if spans:          # verbatim (« C:\Windows\Temp »): the files tool accepts it or refuses it honestly
        return spans[0][2]
    norm = normalize(text)
    padded = f' {norm} '
    for word, folder in _FOLDERS:
        if f' {word} ' in padded:
            return folder
    named = _NAMED_FOLDER.search(norm)
    if named and named.group(1) not in _NOT_FOLDER_NAME and not named.group(1).isdigit():
        return named.group(1)
    return None


_SIZE_UNITS_MB = {'ko': 1 / 1024, 'kb': 1 / 1024, 'kilooctets': 1 / 1024, 'mo': 1.0, 'mb': 1.0, 'mega': 1.0,
                  'megas': 1.0, 'megaoctets': 1.0, 'go': 1024.0, 'gb': 1024.0, 'giga': 1024.0, 'gigas': 1024.0,
                  'gigaoctets': 1024.0, 'to': 1024.0 ** 2, 'tb': 1024.0 ** 2, 'teraoctets': 1024.0 ** 2}


def extract_min_size_mb(text: str) -> float | None:
    """« plus de 1 Go », « d'au moins 500 Mo », « 2 Go ou plus », « > 2 Go » -> minimum size in MB (None if not
    asked). 1 Mo = 2**20 octets and 1 Go = 1024 Mo, as the files tool and Windows Explorer display sizes."""
    units = '(' + '|'.join(_SIZE_UNITS_MB) + r')\b'
    s = words_to_numbers(normalize(join_thousands(text)))
    m = (re.search(r'(?:plus de|superieurs? a|superieure?s? a|au moins|au dela de|depassant|>|plus gros que|'
                   r'plus lourds? que|plus grands? que|minimum)\s*(\d+(?:\.\d+)?)\s*' + units, s)
         or re.search(r'(\d+(?:\.\d+)?)\s*' + units + r'\s+(?:ou plus|et plus|minimum|au minimum|ou davantage)\b', s)
         # « > 2 Go », « >= 500 Mo »: the sign is dropped by normalize(), read it on the original text
         or re.search(r'(?:>|≥)=?\s*(\d+(?:[.,]\d+)?)\s*' + units,
                      strip_accents(join_thousands(text or '')).lower().replace(',', '.')))
    if not m:
        return None
    return round(float(m.group(1)) * _SIZE_UNITS_MB[m.group(2)], 4)


# « gros fichiers » without a number: a default threshold, flagged as such and stated in the answer.
DEFAULT_LARGE_FILE_MB = 100.0
_LARGE_FILES = re.compile(r'\b(?:gros|grosses?|grands|grandes|volumineux|volumineuses?|lourds|lourdes|enormes|'
                          r'massifs)\b|'
                          r'\b(?:prennent|prend|occupent|occupe|bouffent|mangent) (?:le plus |beaucoup |autant |tant )?'
                          r'(?:de )?(?:place|d espace|de la place|de l espace)\b')
# « Où je peux récupérer de l'espace ? » (files.scan only): where space can be won = the biggest files.
_FREE_SPACE = re.compile(r'\b(?:recupere|recuperer|recuperez|recuperes|liberer|libere|gagner|gagne|faire)(?: \w+)? '
                         r'(?:de l |de la |du |un peu d |un peu de |plus d |plus de )?(?:espace|place)\b')
# Size of a whole folder (« combien de place prend le dossier C:/x ? »): no age criterion.
_FOLDER_SIZE = re.compile(r'\b(?:combien de place|combien d espace|quelle taille|taille du dossier|taille totale|'
                          r'pese combien|combien pese|poids du dossier)\b|'
                          # « le dossier Bureau occupe combien ? » (28/10, A2): the folder first, then the verb
                          r'\b(?:dossiers?|repertoires?|documents|telechargements|bureau|images|photos|videos|musique)\b'
                          r' (?:\w+ )?(?:prend|prennent|occupe|occupent|pese|pesent)\b')
# Age words: an age criterion asked without a number keeps the tool's default (180 days).
_AGE_WORDS = re.compile(r'\b(?:vieux|vieilles?|anciens|anciennes?|ages|perimes|obsoletes|oublies|inutilises|'
                        r'pas (?:ouverts?|utilises?|modifies?))\b')


_DURATION_UNIT_DAYS = {'jour': 1, 'jours': 1, 'semaine': 7, 'semaines': 7, 'mois': 30, 'an': 365, 'ans': 365,
                       'annee': 365, 'annees': 365}


def extract_older_than_days(text: str) -> int | None:
    s = words_to_numbers(normalize(join_thousands(text)))
    m = re.search(r'(\d+) (jours?|semaines?|mois|ans?|annees?)\b', s)
    if m and re.search(r'\b(?:plus de|depuis|vieux|vieilles|anciens|anciennes|ages|datant|il y a|de plus de|'
                        r'anterieurs?|avant|older|pas (?:ouverts?|utilises?|modifies?))\b', s):
        return int(m.group(1)) * _DURATION_UNIT_DAYS[m.group(2)]
    if m:
        return int(m.group(1)) * _DURATION_UNIT_DAYS[m.group(2)]
    return None


# --------------------------------------------------------------------------- search queries

_COMMON_LEAD = set('''
peux pourrais pourriez pouvez tu vous est ce que qu je j voudrais veux aimerais souhaite me moi m merci de d bien
vouloir s il te plait alors bon ok dis dites aurora astra aurore stp svp please
'''.split())
_WEB_LEAD = _COMMON_LEAD | set('''
recherche rechercher recherches cherche chercher cherches trouve trouver trouves retrouve google googler
googlise fais faire fait lance lancer effectue effectuer une un des les la le l du sur internet web en ligne
wikipedia wiki informations information infos info renseigne renseigner toi renseignements quelques donne
donner dans a au aux propos concernant article articles sources source quoi net toile parle
fasses fasse fassiez faites petite petites rapide rapides breve mene mener realise realiser sujet
'''.split())
# Speech recognition writes « d'IA » as « dia » after these words (« le dernier modèle dia vocale »).
_DIA = re.compile(r"\b(mod[eè]les?|outils?|assistants?|agents?|logiciels?|voix|images?|vid[eé]os?|applis?|"
                  r"applications?)\s+dia\b", re.I)
_MEMORY_LEAD = _COMMON_LEAD | set('''
retrouve retrouver retrouves rappelle rappelles souviens souvient souvenez te t cherche chercher recherche dans
ma ta la memoire mes souvenirs notre nos mon on a avait avais dit dis parle sur de du des les le quoi propos
concernant qui ai sais connais reprends reprend reprendre reprenons ou nous en etions etait etais montre donne
informations infos sait as retenu memorise note notes ouvre affiche mets
quel quelle quels quelles sont
'''.split())   # « quel est mon mot de passe Wi-Fi » looked for « quel est mon mot de passe Wi-Fi » (B6, 28/10)
_TRAIL = set('''
stp svp merci plait s il te vous internet web ligne wikipedia sur en le la dans ma ta memoire mes souvenirs
please net toile moi
'''.split())


def extract_query(text: str, *, kind: str = 'web') -> str:
    """Search text without trigger words, taken from the original text (accents and case kept).

    For the memory, a request that only designates the user (« que sais-tu de moi ? ») gives ''
    (the caller lists what is known), never a leftover verb such as « sais »."""
    original = strip_wake_word(text).strip(' ?!.')
    lead = _MEMORY_LEAD if kind == 'memory' else _WEB_LEAD
    query = _trim_tokens(original, lead, _TRAIL)
    if not query and kind == 'memory':
        return ''
    if not query:
        query = _trim_tokens(original, _COMMON_LEAD, _TRAIL) or original
    if kind == 'web':
        query = _DIA.sub(r"\1 d'IA", query)
    return query.strip(' ?!.,;:')


# --------------------------------------------------------------------------- system

_METRIC_WORDS = [
    ('cpu', r'\b(?:cpu|processeur|processeurs|coeurs)\b'),
    ('ram', r'\b(?:ram|memoire vive|memoire ram|memoire)\b'),
    ('disk', r'\b(?:disque|disques|ssd|hdd|stockage|espace disque|espace libre|partition|place)\b'),
    ('network', r'\b(?:reseau|debit|bande passante|connexion|wifi|ping|upload|download|internet)\b'),
    ('gpu', r'\b(?:gpu|carte graphique|carte video|vram)\b'),
    ('battery', r'\b(?:batterie|autonomie)\b'),
]


_UPTIME = re.compile(r'\b(?:allume depuis|demarre depuis|uptime|temps de fonctionnement|'
                     r'depuis (?:combien de temps|quand)\b.*\b(?:allume|demarre|tourne|en marche|redemarre))\b')
_TEMPERATURE = re.compile(r'\b(?:temperature|temperatures|chauffe a combien|combien de degres)\b')
# 22/11/2026 (banc de conversation, tour 12) : « quel est le numéro de série de ma carte graphique ? » recevait la
# charge de la carte. Ce qu'aucun outil ne lit sur un composant est dit non lu, comme la température (rules.py s'en
# sert aussi pour reconnaître la question).
# « n° de série » arrives as « n degres de serie »: the degree sign is read as « degrés » by normalize().
HARDWARE_IDENTITY = (r'(?:numeros? de serie|n ?(?:o|degres)? de serie|serial(?: number)?|date (?:de fabrication|d achat|'
                     r'de mise en service)|garantie)')
_IDENTITY = re.compile(rf'\b{HARDWARE_IDENTITY}\b')


def extract_metric(text: str) -> str:
    s = normalize(text)
    if _IDENTITY.search(s):
        return 'identity'        # never answered by a load: said unread (tools/system.py)
    if _UPTIME.search(s):
        return 'uptime'
    if _TEMPERATURE.search(s):
        return 'temperature'     # never answered by the CPU load: the temperature is said unavailable
    found = [name for name, pattern in _METRIC_WORDS if re.search(pattern, s)]
    if 'disk' in found and 'ram' in found and re.search(r'\bespace\b', s) and not re.search(r'\bram\b|\bvive\b', s):
        found.remove('ram')
    if len(found) == 1:
        return found[0]
    return 'all'


def extract_sort(text: str) -> str:
    s = normalize(text)
    if re.search(r'\b(?:ram|memoire)\b', s) and not re.search(r'\b(?:cpu|processeur)\b', s):
        return 'memory'
    return 'cpu'


_APP_ALIASES = [
    ('google chrome', 'chrome'), ('chrome', 'chrome'), ('mozilla firefox', 'firefox'), ('firefox', 'firefox'),
    ('microsoft edge', 'edge'), ('edge', 'edge'), ('opera', 'opera'), ('brave', 'brave'), ('spotify', 'spotify'),
    ('discord', 'discord'), ('steam', 'steam'), ('microsoft teams', 'teams'), ('teams', 'teams'), ('zoom', 'zoom'),
    ('skype', 'skype'), ('slack', 'slack'), ('word', 'winword'), ('excel', 'excel'), ('powerpoint', 'powerpnt'),
    ('outlook', 'outlook'), ('onenote', 'onenote'), ('bloc notes', 'notepad'), ('notepad', 'notepad'),
    ('vlc', 'vlc'), ('obs', 'obs64'), ('photoshop', 'photoshop'), ('blender', 'blender'),
    ('visual studio code', 'code'), ('vs code', 'code'), ('vscode', 'code'), ('whatsapp', 'whatsapp'),
    ('telegram', 'telegram'), ('explorateur de fichiers', 'explorer'), ('explorateur', 'explorer'),
    ('onedrive', 'onedrive'), ('dropbox', 'dropbox'), ('minecraft', 'javaw'), ('thunderbird', 'thunderbird'),
    ('acrobat', 'acrobat'), ('paint', 'mspaint'), ('calculatrice', 'calculatorapp'), ('python', 'python'),
    ('node', 'node'), ('java', 'java'), ('epic games', 'epicgameslauncher'), ('netflix', 'netflix'),
    ('qbittorrent', 'qbittorrent'), ('utorrent', 'utorrent'), ('teamviewer', 'teamviewer'), ('anydesk', 'anydesk'),
]
_KILL_PREFIX = re.compile(
    r'^(?:peux tu |tu peux |pourrais tu |tu pourrais |je veux que tu |merci de )?'
    r'(?:ferme|fermer|fermes|arrete|arreter|arretes|tue|tuer|termine|terminer|quitte|quitter|kill|stoppe|stopper|'
    r'coupe|couper|force la fermeture d|force la fermeture de|force la fermeture du|forcer la fermeture de|'
    r'mets fin a|met fin a)\s+(?:moi\s+)?(?:le |la |l |les |du |de |des )?'
    r'(?:programme |processus |application |logiciel |jeu |tache |appli )?(?P<rest>.+)$')


def extract_process(text: str) -> dict:
    s = normalize(text)
    slots: dict = {'name': None, 'pid': None}
    m = re.search(r'\b(?:pid|processus|process|numero)\s+(\d{1,7})\b', s)
    if m:
        slots['pid'] = int(m.group(1))
    padded = f' {s} '
    for alias, image in _APP_ALIASES:
        if f' {alias} ' in padded:
            slots['name'] = image
            break
    if slots['name'] is None and slots['pid'] is None:
        km = _KILL_PREFIX.search(s)
        if km:
            rest = km.group('rest').split()
            if rest and not rest[0].isdigit():
                slots['name'] = rest[0]
    return slots


# 9/11/2026 (tools/apps.py): « ouvre YouTube Manager », « lance-moi l'application Spotify », « tu peux démarrer
# mon logiciel de montage ». The name is then compared with the allowed list by tools/apps.find (exact name only).
_APP_OPEN = re.compile(
    r'^(?:aurora )?(?:peux tu |tu peux |pourrais tu |tu pourrais |pouvez vous |je veux que tu |merci de |'
    r'j aimerais que tu |je voudrais que tu |est ce que tu peux )?'
    r'(?:ouvre|ouvrir|ouvres|ouvrez|lance|lancer|lances|lancez|demarre|demarrer|demarres|demarrez|allume|allumer|'
    r'allumes|allumez|utilise|utiliser|utilises|utilisez|sers toi de|servez vous de)(?: moi| nous)? '
    r'(?:l |le |la |les |mon |ma |mes |notre )?'
    r'(?:application |appli |app |logiciel |programme )?(?:de |d |du )?(?P<name>.+?)'
    r'(?: s il te plait| s il vous plait| stp| svp| merci| please)?$')
# 9/11/2026, conversation 144 d’ID Studio IA : « tu peux mettre un petit commentaire sur une vidéo […] de ta part utiliser
# l'application Youtube manager ». « Utiliser l'application X » au milieu de la phrase : le mot « application » (ou
# appli, logiciel…) est exigé, et un auxiliaire avant le verbe est un récit (« j'ai utilisé l'appli OBS hier »).
_APP_USE = re.compile(
    r'(?<!\bai )(?<!\bas )(?<!\ba )(?<!avons )(?<!avez )(?<!\bont )(?<!avais )(?<!avait )(?<!\bete )'
    r'\b(?:utilise|utiliser|utilises|utilisez|utilisant|sers toi de|servez vous de|te servir de|passe par|'
    r'passer par|passant par|avec|via|que tu (?:ouvres|lances|demarres|allumes)) (?:l |le |la |mon |ma |notre |ton |'
    r'ta )?'
    r'(?:application|appli|app|logiciel|programme) (?:de |d |du )?(?P<name>.+?)'
    r'(?: s il te plait| s il vous plait| stp| svp| merci| please)?$')
# Ce que la personne veut y faire, après le nom : « ouvre YouTube Manager pour mettre un commentaire ».
_APP_TASK = re.compile(r' (?:pour|afin de|afin d|et (?:puis |ensuite )?(?=(?:mets|mettre|ecris|ecrire|poste|poster|'
                       r'publie|publier|ajoute|ajouter|tape|taper|commente|commenter|reponds|repondre|envoie|envoyer|'
                       r'fais|faire)\b)) ?(?P<task>.+)$')
# « je veux pas utiliser l'application X », « n'ouvre pas X », « pas besoin de lancer X » : rien n'est ouvert.
_APP_VERB = r'(?:ouvr|lanc|demarr|utilis|allum)\w*'
_APP_NOT = re.compile(
    rf'\b(?:(?:veux|veut|voulons|voulez|voudrais|voulais|souhaite|faut) (?:pas|plus|jamais)(?: que tu| qu on)? '
    rf'{_APP_VERB}|n {_APP_VERB} (?:pas|plus|jamais|rien)|{_APP_VERB} (?:pas|plus|jamais)\b|pas besoin d {_APP_VERB}'
    rf'|surtout pas)')
_APP_NOT_START = re.compile(rf'^(?:ne |n )?({_APP_VERB}) (?:pas|plus|jamais) ')   # « n'ouvre pas X »: X is read
_POLITE_START = re.compile(r'^(?:peux tu |tu peux |pourrais tu |tu pourrais |pouvez vous |je veux que tu |merci de |'
                           r'j aimerais que tu |je voudrais que tu |est ce que tu peux )')


def extract_app(text: str) -> dict:
    """{'app': <name asked, as written if it ends the sentence, else normalised> or None}. « lance : notepad » is the
    terminal's syntax (shell.run), not an app.

    9/11/2026 (conversation 144) : aussi « utilise l'application X » au milieu de la phrase ; 'task' = ce que la
    personne veut y faire (« pour mettre un commentaire », ou ce qui précède « utiliser l'application X ») ;
    'refused' = le nom dit avec une négation (« je veux pas utiliser l'application X ») : 'app' reste None, rien
    n'est ouvert."""
    raw = text or ''
    if _SHELL_MARKER.match(raw.strip()):
        return {'app': None, 'command_syntax': True}
    norm = normalize(raw).strip()
    m = _APP_OPEN.match(_APP_NOT_START.sub(r'\1 ', norm))
    before = ''
    if not m:
        m = _APP_USE.search(norm)
        before = _POLITE_START.sub('', norm[:m.start()]).strip() if m else ''
    name = m.group('name').strip() if m else ''
    task = ''
    cut = _APP_TASK.search(name)
    if cut:
        name, task = name[:cut.start()].strip(), cut.group('task').strip()
    task = task or re.sub(r'\b(?:en|et|pour|puis|de ta part|de votre part)$', '', before).strip()
    if re.fullmatch(r'(?:une |un )?(?:application|appli|app|logiciel|programme)s?', name):
        name = ''   # « ouvre une application »: no name, the answer lists the allowed ones
    extra = {'task': as_written(raw, task)} if task and name else {}
    if name and _APP_NOT.search(norm):
        return {'app': None, 'refused': name}
    words = raw.split()
    while words and not re.search(r'\w', words[-1]):
        words.pop()   # a lone « ? » or « ! »
    # The name as written (« Spotify »), when it ends the sentence; never longer than the name itself, so a
    # politeness that normalize() drops (« s'il te plaît ») is not taken with it.
    for k in range(1, min(len(words), len(name.split())) + 1):
        tail = ' '.join(words[-k:]).strip(' ?!.,;:«»"\'')
        if name and normalize(tail).strip() == name:
            return {'app': tail, **extra}
    return {'app': name or None, **extra}


# --------------------------------------------------------------------------- shell command (B2, 04/11/2026)

# The marker that means « run this literal command », matched on the RAW text so the command keeps its
# exact case, slashes and punctuation (the model never rewrites it, CLAUDE.md « le vrai danger »).
# Two safe forms only, to avoid catching « lance une recherche » / « exécute le plan » :
#   A: exécute / lance (+ « la/cette commande ») followed by « : » or « > », then the command;
#   B: terminal / shell / cmd / (ligne de) commande followed by « : » or « > », then the command.
_SHELL_MARKER = re.compile(
    r'^(?:aurora[\s,]+)?(?:'
    r'(?:ex[ée]cute(?:r|s)?|lance(?:r|s)?)\s+(?:moi\s+)?(?:la\s+|une\s+|cette\s+)?'
    r'(?:ligne\s+de\s+)?commande\s*[:>]?\s*|'
    r'(?:ex[ée]cute(?:r|s)?|lance(?:r|s)?)\s*[:>]\s*|'
    r'(?:dans\s+le\s+)?(?:terminal|shell|cmd|invite\s+de\s+commande|ligne\s+de\s+commande)\s*[:>]\s*'
    r')', re.I)
# An optional trailing « dans <chemin> » naming the working folder (a drive path, ~ or / path).
_SHELL_CWD_TAIL = re.compile(r'\s+(?:dans|depuis)\s+(?:le\s+dossier\s+)?(?P<cwd>(?:[A-Za-z]:[\\/]|~|/)[^\n]+?)\s*$', re.I)


def extract_command(text: str) -> dict:
    """{'command': <literal command or None>, 'cwd': <folder or None>} from the RAW text."""
    raw = (text or '').strip()
    m = _SHELL_MARKER.match(raw)
    if not m:
        return {'command': None, 'cwd': None}
    command = raw[m.end():].strip()
    cwd = None
    tail = _SHELL_CWD_TAIL.search(command)
    if tail:
        cwd = tail.group('cwd').strip().strip('`"\'' + '«»').strip()
        command = command[:tail.start()].strip()
    command = command.strip('`').strip()
    if (command.startswith('"') and command.endswith('"')) or (command.startswith('«') and command.endswith('»')):
        command = command[1:-1].strip()
    return {'command': command or None, 'cwd': cwd or None}


# --------------------------------------------------------------------------- mouse and keyboard (9/11/2026)

# Read on the RAW text, like the shell command: the typed text keeps its case and punctuation, never rewritten.
_PC_LEAD = (r'^(?:aurora[\s,]+)?(?:(?:est-ce\s+que\s+)?(?:tu\s+peux|peux-tu|peux\s+tu|pourrais-tu|tu\s+pourrais|'
            r'merci\s+de|je\s+veux\s+que\s+tu|il\s+faut)\s+)?')
_PC_WINDOW = r'(?:la\s+fen[eê]tre\s+|l[’\']application\s+|le\s+programme\s+|l[’\']appli\s+)?'
_PC_POINT = re.compile(r'(?:\bx\s*[=:]?\s*(?P<x1>\d{1,5})\s*(?:,|;|et)?\s*y\s*[=:]?\s*(?P<y1>\d{1,5}))|'
                       r'(?P<x2>\d{1,5})\s*(?:,|;|x|×|/)\s*(?P<y2>\d{1,5})', re.I)
# The LAST « dans <fenêtre> » of the rest (« clique sur le bouton OK dans Chrome »: the window is Chrome).
_PC_IN = re.compile(rf'^(?P<before>.*)\s+dans\s+{_PC_WINDOW}(?P<w>.+?)\s*[.!]?\s*$', re.I | re.S)
_PC_TYPE = re.compile(_PC_LEAD + r'(?:tape|tapes|taper|tapez|[ée]cris|[ée]crire|[ée]crivez|saisis|saisir|saisissez)'
                      rf'(?:\s+(?:moi|le\s+texte|ce\s+texte|ceci))?(?:\s+(?:dans|sur)\s+{_PC_WINDOW}(?P<w>[^:\n]+?))?'
                      r'\s*:\s*(?P<t>.+)$', re.I | re.S)
_PC_KEY = re.compile(_PC_LEAD + r'(?:appuie|appuies|appuyer|appuyez|presse|presser|pressez|enfonce|enfoncer|'
                     r'tape|taper)\s+sur\s+(?P<k>.+)$', re.I)
_PC_SHORTCUT = re.compile(_PC_LEAD + r'(?:fais|faire|fait|faites)\s+(?:un\s+)?(?P<k>(?:ctrl|control|contr[oô]le|alt|maj|'
                          r'shift)\b.+)$', re.I)
_PC_CLICK = re.compile(_PC_LEAD + r'(?:fais\s+|faire\s+)?(?:un\s+)?(?P<kind>double[\s-]?cli(?:c|que|quer|quez|ques)|'
                       r'clic\s+droit|cli(?:c|que|quer|quez|ques)(?:\s+(?:avec\s+le\s+)?(?:bouton\s+)?droit)?)\b'
                       r'(?P<rest>.*)$', re.I)
_PC_SCROLL = re.compile(_PC_LEAD + r'(?:fais\s+d[ée]filer|faire\s+d[ée]filer|d[ée]file|d[ée]filer|scrolle|scroller|'
                        r'scroll)\b(?P<rest>.*)$', re.I)
_PC_MOVE = re.compile(_PC_LEAD + r'(?:d[ée]place|d[ée]placer|bouge|bouger|mets|mettre|place|placer|am[eè]ne|amener)\s+'
                      r'(?:la\s+souris|le\s+curseur|le\s+pointeur)\b(?P<rest>.*)$', re.I)
_PC_WHERE = re.compile(r'\b(?:o[uù]\s+(?:est|se\s+trouve|en\s+est)|position|coordonn[ée]es)\b.*'
                       r'\b(?:souris|curseur|pointeur)\b', re.I)
_PC_TARGET = re.compile(r'^\s*(?:sur\s+)?(?:le\s+|la\s+|l[’\']\s*|les\s+)?(?:bouton|lien|onglet|menu|ic[oô]ne|case|'
                        r'champ|mot|texte|option|élément|element)?\s*(?P<t>.+?)\s*$', re.I)


def _pc_window(raw: str | None) -> str | None:
    name = (raw or '').strip().strip('`"\'«» ').rstrip('.!?').strip()
    return name or None


_COMMENT_REMOVE = re.compile(r'^(?:aurora )?(?:peux tu |tu peux |pourrais tu |pouvez vous )?(?:retire|retirer|'
                             r'retirez|supprime|supprimer|supprimez|efface|effacer|effacez|enleve|enlever|enlevez)\b')
_COMMENT_WORDS = re.compile(r'[:：](?!//)\s*(.+)$|[«“"]\s*(.+?)\s*[»”"]|\b(?:qui dit|disant|avec le texte)\s+(.+)$',
                            re.S | re.I)
_COMMENT_OTHER = re.compile(r'\bavant derniere\b|\bpenultieme\b|\bvideos? (?:sur|de|d|du|des|qui|intitulee|appelee|'
                            r'nommee|ou|avec|que|a propos)\b')


def extract_comment(text: str) -> dict:
    """9/11/2026 (youtube.comment), from the RAW text: {'text': the comment's own words (after « : », in quotes, or
    after « qui dit ») or None (the local model writes it), 'video': 'latest' (the newest video: « ma dernière
    vidéo », « ma vidéo », none named) or 'other' (another one named, a link: not done yet), 'remove': a request to
    withdraw the comment AURORA posted}. The video is read before the comment's words, never in them."""
    raw = re.sub(r'^\s*aurora\s*[,:!]?\s*', '', text or '', flags=re.I).strip()
    if _COMMENT_REMOVE.match(normalize(raw)):
        return {'text': None, 'video': 'latest', 'remove': True}
    found = _COMMENT_WORDS.search(raw)
    head = raw[:found.start()] if found else raw
    words = next((g for g in found.groups() if g), None) if found else None
    if words:
        words = words.strip().strip('«»“”" ').strip()[:1000] or None
    h = normalize(head)
    other = bool(re.search(r'youtu\.be/|youtube\.com/(?:watch|shorts)', raw, re.I)) or bool(_COMMENT_OTHER.search(h))
    if re.search(r'\b(?:derniere|nouvelle|plus recente)\b', h) and not re.search(r'\bavant derniere\b', h):
        other = False
    return {'text': words, 'video': 'other' if other else 'latest', 'remove': False}


def extract_gesture(text: str) -> dict:
    """{'gesture': {...} | None} from the RAW text: one gesture of the closed list (tools/pc_control.py), with its
    window, position, text or keys as written. A gesture the code cannot read is None (the answer gives the syntax);
    a click on a NAMED thing (« clique sur Enregistrer ») keeps that name in 'target'."""
    raw = (text or '').strip()
    if _PC_WHERE.search(raw) and not _PC_CLICK.match(raw):
        return {'gesture': {'gesture': 'position'}}
    m = _PC_TYPE.match(raw)
    if m:
        typed = m.group('t').strip()
        if (typed[:1], typed[-1:]) in (('"', '"'), ('«', '»'), ('“', '”')) and len(typed) > 1:
            typed = typed[1:-1].strip()
        return {'gesture': {'gesture': 'type', 'text': typed or None, 'window': _pc_window(m.group('w'))}}
    m = _PC_SHORTCUT.match(raw) or _PC_KEY.match(raw)
    if m:
        keys, window = m.group('k').strip(), None
        inside = _PC_IN.match(' ' + keys)
        if inside:
            window = _pc_window(inside.group('w'))
            keys = inside.group('before').strip()
        from ..tools.pc_control import parse_keys   # lazy: tools are owned by another module
        names, why = parse_keys(keys)
        if names:
            return {'gesture': {'gesture': 'key', 'keys': keys, 'window': window}}
        if not re.search(r'\b(?:ctrl|control|contr[oô]le|alt|maj|shift|touche|f\d{1,2})\b', keys, re.I) \
                and not why.startswith(('La touche Windows', 'Raccourci interdit')):
            # « appuie sur le bouton Enregistrer »: a click on a named thing, not a key
            target = _PC_TARGET.match(keys)
            return {'gesture': {'gesture': 'click', 'target': target.group('t') if target else keys, 'window': window}}
        return {'gesture': {'gesture': 'key', 'keys': keys, 'window': window}}   # the tool says why it is refused
    for pattern, kind in ((_PC_CLICK, None), (_PC_SCROLL, 'scroll'), (_PC_MOVE, 'move')):
        m = pattern.match(raw)
        if not m:
            continue
        if kind is None:
            word = re.sub(r'\s+', ' ', m.group('kind').lower())
            kind = ('double_click' if word.startswith('double') else
                    'right_click' if 'droit' in word else 'click')
        rest = m.group('rest')
        gesture: dict = {'gesture': kind}
        inside = _PC_IN.match(rest)
        if inside:
            gesture['window'] = _pc_window(inside.group('w'))
            rest = inside.group('before')
        point = _PC_POINT.search(rest)
        if point:
            gesture['x'] = int(point.group('x1') or point.group('x2'))
            gesture['y'] = int(point.group('y1') or point.group('y2'))
        if kind == 'scroll':
            gesture['direction'] = 'haut' if re.search(r'\b(?:haut|monte|remonte)\b', rest, re.I) else 'bas'
            amount = re.search(r'(\d{1,2})\s*(?:crans?|fois|clics?|coups?)', rest, re.I)
            if amount:
                gesture['amount'] = max(1, min(20, int(amount.group(1))))
        elif not point and kind != 'move':
            cleaned = re.sub(r'^\s*(?:en|à|a|au)\b', '', rest.strip(), flags=re.I).strip()
            target = _PC_TARGET.match(cleaned) if cleaned else None
            if target and target.group('t'):
                gesture['target'] = target.group('t').strip('`"\'«» ')
        return {'gesture': gesture}
    return {'gesture': None}


# --------------------------------------------------------------------------- simulations

# Ids of tools/orbit.py (French); unsupported bodies are passed as-is and refused honestly by the tool.
_BODIES = [('la lune', 'lune'), ('lune', 'lune'), ('terre', 'terre'), ('mars', 'mars'), ('venus', 'venus'),
           ('mercure', 'mercure'), ('jupiter', 'jupiter'), ('saturne', 'saturne'), ('uranus', 'uranus'),
           ('neptune', 'neptune'), ('soleil', 'soleil'), ('pluton', 'pluton')]


def extract_orbit(text: str) -> dict:
    s = words_to_numbers(normalize(join_thousands(text)))
    slots: dict = {'body': None, 'altitude_km': None}
    # prefer the body introduced by "autour de", then any mention
    m = re.search(r'\bautour (?:de |du |d )?(?:la |l )?(\w+)', s)
    if m:
        for word, body in _BODIES:
            if m.group(1) == word.split()[-1]:
                slots['body'] = body
                break
    if slots['body'] is None:
        padded = f' {s} '
        for word, body in _BODIES:
            if f' {word} ' in padded:
                slots['body'] = body
                break
    # The sign is kept: a negative altitude is refused by the tool, never silently made positive.
    m = re.search(r'(-?\d+(?:\.\d+)?)\s*(km|kilometres|kilometre|m|metres)\b(?:\s+(?:d altitude|de haut|au dessus))?', s)
    if m and re.search(r'\b(?:altitude|haut|au dessus|orbite|a -?\d|de -?\d)', s):
        value = float(m.group(1))
        if m.group(2) in ('m', 'metres'):
            value /= 1000.0
        slots['altitude_km'] = value
    elif re.search(r'\bgeostationnaire|geosynchrone\b', s):
        slots['altitude_km'] = 35786.0
        slots['body'] = slots['body'] or 'terre'
    elif re.search(r'\b(?:iss|station spatiale internationale)\b', s):
        slots['altitude_km'] = 420.0
        slots['body'] = slots['body'] or 'terre'
    return slots


_MONTHS = {'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8,
           'septembre': 9, 'octobre': 10, 'novembre': 11, 'decembre': 12}


def extract_date(text: str, *, today: date | None = None) -> str | None:
    """ISO date (YYYY-MM-DD) mentioned in the text, or None for 'now'."""
    raw = text or ''
    m = re.search(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b', raw)
    if m:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', raw)
    if m:
        return _iso(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    s = normalize(text)
    m = re.search(r'\b(\d{1,2}|1er|premier) (' + '|'.join(_MONTHS) + r') (\d{4})\b', s)
    if m:
        day = 1 if m.group(1) in ('1er', 'premier') else int(m.group(1))
        return _iso(int(m.group(3)), _MONTHS[m.group(2)], day)
    m = re.search(r'\b(?:en|pour|de|le) (?:(' + '|'.join(_MONTHS) + r') )?(\d{4})\b', s)
    if m and 1600 <= int(m.group(2)) <= 2500:
        return _iso(int(m.group(2)), _MONTHS.get(m.group(1) or '', 1), 1)
    base = today or date.today()
    if re.search(r'\bapres demain\b', s):
        return (base + timedelta(days=2)).isoformat()
    if re.search(r'\bdemain\b', s):
        return (base + timedelta(days=1)).isoformat()
    if re.search(r'\bhier\b', s):
        return (base - timedelta(days=1)).isoformat()
    return None


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------- interface

_PAGE_WORDS = [
    ('tableau de bord', 'home'), ('page d accueil', 'home'), ('accueil', 'home'), ('dashboard', 'home'),
    ('page principale', 'home'),
    ('graphe de memoire', 'memory'), ('memoire', 'memory'), ('souvenirs', 'memory'), ('graphe', 'memory'),
    ('carte du monde', 'world'), ('globe', 'world'), ('planisphere', 'world'), ('monde', 'world'),
    ('carte', 'world'),
    ('simulations', 'simulations'), ('simulation', 'simulations'), ('simulateur', 'simulations'),
    ('systeme solaire', 'simulations'),
    ('taches', 'tasks'), ('activite', 'tasks'), ('historique', 'tasks'), ('journal', 'tasks'),
    ('chronologie', 'tasks'), ('timeline', 'tasks'),
    ('parametres', 'settings'), ('reglages', 'settings'), ('configuration', 'settings'), ('options', 'settings'),
    ('preferences', 'settings'),
    ('ordinateur', 'computer'), ('systeme', 'computer'), ('moniteur', 'computer'), ('surveillance', 'computer'),
    ('pc', 'computer'), ('performances', 'computer'),
    ('recherche', 'search'), ('chat', 'chat'), ('conversation', 'chat'), ('discussion', 'chat'),
]


def extract_page(text: str) -> str | None:
    padded = f' {normalize(text)} '
    best: tuple[int, str] | None = None
    for word, page in _PAGE_WORDS:
        pos = padded.find(f' {word} ')
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, page)
    return best[1] if best else None


# 08/11/2026 (globe hologramme de Monde 3D) : what to show on the globe, read from the request. The place is a
# name the globe looks up in its own lists (capitals, 7 342 cities, 197 countries), with no network call.
_GLOBE_LAYERS = [
    ('evenements', r'\b(?:cyclones?|ouragans?|typhons?|tsunamis?|volcans?|eruptions?|seismes?|tremblements? de terre|'
                   r'tornades?|catastrophes?|evenements?)\b'),
    ('satellites', r'\b(?:satellites?|iss|station spatiale|tiangong|hubble|landsat)\b'),
    ('avions', r'\b(?:avions?|vols? en cours|trafic aerien|aeronefs?)\b'),
    ('actualites', r'\b(?:actualites?|actus?|infos|informations|nouvelles|news)\b'),
    ('meteo', r'\b(?:meteo|vents?|pluies?|precipitations?|temperatures?|qualite de l air|pollution)\b'),
    ('routes', r'\b(?:routes?|autoroutes?|carte routiere)\b'),
    ('guide', r'\b(?:guide|sites? unesco|patrimoine mondial|monuments?)\b'),
]
_GLOBE_SATELLITES = [(r'\b(?:iss|station spatiale internationale|station spatiale)\b', 25544),
                     (r'\btiangong\b', 48274), (r'\bhubble\b', 20580), (r'\blandsat\b', 49260)]
_GLOBE_TAIL = r'(?: (?:sur|dans) (?:le |la |l )?(?:globe|carte du monde|planisphere|carte|monde 3d))'
_GLOBE_VERB = (r'(?:montre|montrer|affiche|afficher|zoome|zoomer|zoom|centre|centrer|localise|localiser|situe|situer|'
               r'va|aller|emmene|amene)')
_GLOBE_WHERE = r'(?:ou (?:est|sont|se trouve|se trouvent|se situe|se situent))'
_GLOBE_ARTICLE = r'(?:le |la |l |les )?'
_GLOBE_LIEU = r'(?P<lieu>[a-z][a-z \'-]{1,40}?)'
_GLOBE_PLACE = [
    # 08/11/2026 (runs 44-45 of the real AURORA, by voice): « où est la Jordanie sur le globe ».
    re.compile(rf'\b{_GLOBE_WHERE} {_GLOBE_ARTICLE}{_GLOBE_LIEU}(?:{_GLOBE_TAIL}|\s*$)'),
    re.compile(rf'\b(?:au dessus d[eu]?|autour d[eu]?|pres d[eu]?|vers|a|au|aux|en) (?:l )?{_GLOBE_LIEU}{_GLOBE_TAIL}'),
    re.compile(rf'\b(?:{_GLOBE_VERB[3:-1]}|place|placer)(?: moi| nous)? (?:sur )?(?:le |la |l |les )?'
               rf'{_GLOBE_LIEU}{_GLOBE_TAIL}'),
    # « les avions au-dessus de Lyon » : without « sur le globe », only these words name the place, at the end.
    re.compile(rf'\b(?:au dessus d[eu]?|autour d[eu]?|pres d[eu]?) (?:l )?{_GLOBE_LIEU}\s*$'),
    # « montre sur le globe où est la Jordanie », « … sur le globe où la Jordanie » (voice drops « est »).
    re.compile(rf'{_GLOBE_TAIL} (?:ou (?:est |sont |se trouve |se trouvent |se situe |se situent )?)?'
               rf'{_GLOBE_ARTICLE}{_GLOBE_LIEU}\s*$'),
    # « Petra sur le globe » : the place alone.
    re.compile(rf'^{_GLOBE_ARTICLE}{_GLOBE_LIEU}{_GLOBE_TAIL}\s*$'),
    # « zoome sur la Jordanie », « situe Petra » : the router sent them to the globe (globe_place_named).
    re.compile(rf'^{_GLOBE_VERB}(?: moi| nous)? (?:sur |vers |a |au |aux |en )?{_GLOBE_ARTICLE}{_GLOBE_LIEU}\s*$'),
]
_GLOBE_NOT_PLACE = re.compile(r'\b(?:globe|carte|monde|planete|terre|page|onglet|ecran|tout|toutes?|tous|ca|cela|'
                              r'actifs?|actives?|en cours|direct|moi|quoi|qui|quel|quelle|quels|quelles|comment|'
                              r'pourquoi|combien|rien|mon|ma|mes|ton|ta|tes|son|sa|ses|notre|nos|votre|vos|'
                              rf'{_GLOBE_VERB[3:-1]}|ouvre|ouvrir|regarde|voir)\b|^{_GLOBE_WHERE}\b')
# « … ou pas », « … s'il te plaît » (ID Studio IA's spoken questions end so): not part of the place.
_GLOBE_FILLER = re.compile(r'(?: (?:ou pas|ou non|s il te plait|s il vous plait|stp|svp|merci|maintenant|'
                           r'tout de suite))+$')


def extract_globe_order(text: str) -> dict:
    """{couche?, satellite?, lieu?} for « montre-moi Paris sur le globe », « affiche les cyclones sur le globe »."""
    norm = normalize(text)
    order: dict = {}
    for layer, pattern in _GLOBE_LAYERS:
        if re.search(pattern, norm):
            order['couche'] = layer
            break
    for pattern, norad in _GLOBE_SATELLITES:
        if re.search(pattern, norm):
            order['couche'], order['satellite'] = 'satellites', norad
            break
    for pattern in _GLOBE_PLACE:
        m = pattern.search(norm)
        lieu = _globe_place(m.group('lieu')) if m else None
        if lieu:
            order['lieu'] = lieu
            break
    return order


def _globe_place(raw: str) -> str | None:
    """The place read in a globe request, or None when the words are a layer, a page, a verb or a question."""
    lieu = _GLOBE_FILLER.sub('', raw.strip(" '-")).strip(" '-")
    if not lieu or any(re.search(p, lieu) for _, p in _GLOBE_LAYERS) or _GLOBE_NOT_PLACE.search(lieu):
        return None
    return lieu


_GLOBE_ALONE = re.compile(rf'^{_GLOBE_ARTICLE}{_GLOBE_LIEU} (?:sur|dans) (?:le |la |l )?(?:globe|carte du monde|'
                          rf'planisphere|monde 3d)(?: ou pas| ou non)?$')
_GLOBE_ORDER = re.compile(rf'^{_GLOBE_VERB}(?: moi| nous)? (?:sur |vers |a |au |aux |en )?{_GLOBE_ARTICLE}'
                          rf'{_GLOBE_LIEU}(?: ou pas| ou non)?$')
# « où est Pétra ? », « mondial où est Petra » (run 49, the start lost by voice): the place ends the sentence.
_GLOBE_ASKED_WHERE = re.compile(rf'\b{_GLOBE_WHERE} {_GLOBE_ARTICLE}{_GLOBE_LIEU}(?: ou pas| ou non)?$')
# The 1 381 World Heritage sites the globe already shows (Wikidata CC0): read where the globe reads them, not copied.
GUIDE_SITES = Path(__file__).resolve().parents[3] / 'frontend' / 'public' / 'terre' / 'guide-sites.json'
_known_places: frozenset[str] | None = None
_heritage_sites: dict[str, dict] = {}
# Every row of GUIDE_SITES, the best known first (read with the names): the photos of place_photos.
_heritage_rows: list[dict] = []


def globe_place_named(text: str, *, normalized: bool = False) -> str | None:
    """08/11/2026 (runs 44-49 of the real AURORA, by voice): a sentence that asks for a place on the globe by
    itself. « Petra sur le globe » (a place alone, at most 4 words); « zoome sur la Jordanie », « situe Pétra »,
    « où est Pétra ? » only for a place the globe knows: a country or a capital (tools/news.country_index) or a
    World Heritage site (GUIDE_SITES). « zoome sur l'image », « où est mon fichier » are not places. The globe looks
    the name up itself (frontend trouverLieu)."""
    norm = text if normalized else normalize(text)
    m = _GLOBE_ALONE.match(norm)
    if m:
        lieu = _globe_place(m.group('lieu'))
        return lieu if lieu and len(lieu.split()) <= 4 else None
    m = _GLOBE_ORDER.match(norm) or _GLOBE_ASKED_WHERE.search(norm)
    lieu = _globe_place(m.group('lieu')) if m else None
    return lieu if lieu and lieu in _place_names() else None


def globe_site(lieu: str) -> dict | None:
    """{nom, pays} of the World Heritage site of that (normalised) name, the best known one first: « petra » ->
    {nom: 'Pétra', pays: 'Jordanie'}. None for anything else (a country, a city, an unknown name)."""
    _place_names()
    return _heritage_sites.get(lieu or '')


def _place_names() -> frozenset[str]:
    """The places the globe knows, normalised once: the 197 countries, their capitals and short names
    (tools/news.country_index) and the World Heritage sites (GUIDE_SITES). A list unreadable is left out."""
    global _known_places
    if _known_places is None:
        names: set[str] = set()
        try:
            from ..tools.news import country_index   # lazy: the tools import core modules
            names.update(normalize(word) for word, _country, _pattern in country_index().words)
        except Exception:      # the list unreadable: no country is known, the sentence goes on its usual way
            pass
        try:
            sites = json.loads(GUIDE_SITES.read_text(encoding='utf-8'))['sites']
        except (OSError, ValueError, KeyError, TypeError):
            sites = []
        for site in sorted((s for s in sites if isinstance(s, dict) and isinstance(s.get('n'), str)),
                           key=lambda s: -(s.get('l') or 0)):
            _heritage_rows.append(site)
            key = normalize(site['n'])
            if key and key not in _heritage_sites:
                _heritage_sites[key] = {'nom': site['n'], 'pays': site.get('p') or None}
        names.update(_heritage_sites)
        _known_places = frozenset(names)
    return _known_places


# 08/11/2026 (the owner: « histoire du Maroc », « c'était bien d'avoir des images du sujet »): the photos of the World
# Heritage sites of the place a question names, read in the globe's own list (GUIDE_SITES: Wikidata CC0, photos from
# Wikimedia Commons). Only a country named by its name, or a site named by its full name: a capital is often a common
# word too (Sucre, Roseau, Malé).
PHOTOS_MAX = 6
# Site names that are common words: « médina » is a whole site (Tétouan), « acre » a unit, « bikini » a swimsuit.
_PHOTO_NOT_SITES = frozenset({'medina', 'acre', 'bikini', 'salt', 'bath', 'kuk', 'ani', 'bam', 'nisa', 'henderson'})
# Countries that are also first names or things: only with the words that name the country.
_PHOTO_AMBIGUOUS = {
    'dominique': re.compile(r'\b(?:la|de la) dominique\b'),
    'maurice': re.compile(r'\b(?:ile|a) maurice\b'),
    'salvador': re.compile(r'\b(?:le|au|du) salvador\b'),
    'chili': re.compile(r'\b(?:le|au|du) chili\b(?! con carne)'),
    'panama': re.compile(r'\b(?:le|au|du) panama\b(?! papers)'),
}
# 11/11/2026 (ID Studio IA : « les images des sujets ne fonctionnent pas tout le temps »): 14 everyday questions out of 41 had
# photos, because a site had to be named by its whole name (« Médina de Marrakech ») and a city never counted. The
# place that a site's own name names (« Marrakech », « Paris », « Machu Picchu ») now finds it, a few nicknames too
# (« tour Eiffel »), and a capital gives the sites of its country. Words that are also things, first names or
# directions are left out: no photo is better than the photo of something else.
_PHOTO_SEGMENT = re.compile(r'[,;:()«»–—/]| - ')
_PHOTO_TOKEN = re.compile(r'[^\W\d_]+(?:-[^\W\d_]+)*')
_PHOTO_BEFORE = frozenset({'de', 'd', 'du', 'des', 'à', 'a'})
_PHOTO_ARTICLES = frozenset({'la', 'le', 'les', 'l'})
_PHOTO_ENGLISH = frozenset({'of', 'and', 'the'})
# Read one by one in the 951 one-word places of the list (11/11/2026): common words, first names, directions, whole
# countries and continents (a country is found by its own name, below).
_PHOTO_NOT_PLACES = frozenset((
    'afrique alliance along altar apocalypse apollon armada ascension assise atoll basse canal canyon capela carriere '
    'castel cavernas centenaire centre chang chantier chaussee chevaliers chine christ cites coeur collegiale colonia '
    'confucius coree cote coteaux cour debarquement ecosysteme eglise empire escuela este etablissements europe fermes '
    'fernando fjords fortaleza fountains geants genes germanie golfe grands habitations hadrien hail hauts hercule '
    'hypogee iraq ivoire jarres jesuites khan khans lagon lagons leon lignes lune manu margraves mecanica memoire midi '
    'minaret nativite nord noyau ouest paix palestine palmeraie parc paseo petroglyphes peuplement pico plaine plan '
    'pleistocene pueblo quebrada refuge residence residences roca roland round ruinas ruta sainte sana sculptures serra '
    'serrania sierra song split sucre sultanats systemes tara tell tels temples terrains terres theatres trinational '
    'triomphe trois tropiques tumulus universite vall vallee vega victoire vierge virginie vladimir xive xvie yuan'
).split()) | {'san francisco', 'historical monuments', 'village eglise'}
_PHOTO_SHORT_PLACES = frozenset({'fes'})   # three letters, and yet a city people ask about
# Places that are also a verb or a fruit: only with the words that name the city.
_PHOTO_PLACE_ONLY_AS = {
    'vienne': re.compile(r'\b(?:a|de) vienne\b'),
    'orange': re.compile(r'\b(?:a orange|ville d orange|theatre (?:antique )?d orange)\b'),
}
# Capitals that are common words or first names (Sucre, Roseau, Malé: the first reading of 08/11/2026), or the name of
# several cities.
_PHOTO_NOT_CAPITALS = frozenset({'sucre', 'roseau', 'male', 'victoria', 'sofia', 'georgetown', 'kingston', 'kingstown',
                                 'santiago', 'san jose', 'saint georges', 'saint john s'})
# What people call a monument or a city -> the place of the list that holds it (a place of _photo_place_index, or the
# whole name of a site).
_PHOTO_ALIASES = {
    'tour eiffel': 'paris', 'louvre': 'paris', 'notre dame de paris': 'paris', 'colisee': 'rome',
    'forum romain': 'rome', 'pyramides de gizeh': 'guizeh', 'pyramide de kheops': 'guizeh', 'gizeh': 'guizeh',
    'sphinx': 'guizeh', 'louxor': 'thebes', 'karnak': 'thebes', 'vallee des rois': 'thebes',
    'palais des papes': 'avignon', 'sagrada familia': 'antoni gaudi', 'gaudi': 'antoni gaudi',
    'parc guell': 'antoni gaudi', 'mont fuji': 'fujisan', 'statue de la liberte': 'liberty island',
    'new york': 'liberty island', 'big ben': 'westminster', 'kremlin': 'moscou', 'place rouge': 'moscou',
    'cite interdite': 'pekin', 'volubilis': 'oualili', 'kyoto': 'monuments historiques de l ancienne kyoto',
    'lyon': 'vieux lyon', 'bordeaux': 'port de la lune', 'christ redempteur': 'janeiro', 'corcovado': 'janeiro',
}
# Countries that the list knows under a longer name.
_PHOTO_COUNTRY_ALIASES = {'chine': 'république populaire de Chine', 'angleterre': 'Royaume-Uni',
                          'grande bretagne': 'Royaume-Uni', 'hollande': 'Pays-Bas', 'palestine': 'État de Palestine'}
_photo_places: dict[str, dict] | None = None


def _site_places(name: str) -> list[tuple[bool, str]]:
    """The places that the name of a site names: [(at the head of the name?, the words as written)]. A run of
    capitalised words at the head (« Venise et sa lagune »), after « de », « du », « à »… (« rives de la Seine à
    Paris »), or alone after a comma (« …, Moscou »). A name in English is left out."""
    if _PHOTO_ENGLISH & {t.lower() for t in _PHOTO_TOKEN.findall(name)}:
        return []
    found: list[tuple[bool, str]] = []
    for k, segment in enumerate(_PHOTO_SEGMENT.split(name)):
        tokens = _PHOTO_TOKEN.findall(segment.replace('’', ' ').replace("'", ' '))
        alone = k > 0 and bool(tokens) and all(t[:1].isupper() for t in tokens)
        i = 0
        while i < len(tokens):
            head = i == 0 and (k == 0 or alone)
            after = i > 0 and (tokens[i - 1].lower() in _PHOTO_BEFORE or (
                i > 1 and tokens[i - 1].lower() in _PHOTO_ARTICLES and tokens[i - 2].lower() in _PHOTO_BEFORE))
            if tokens[i][:1].isupper() and (head or after):
                j = i
                while j < len(tokens) and tokens[j][:1].isupper():
                    j += 1
                found.append((head and k == 0, ' '.join(tokens[i:j])))
                # « Rio de Janeiro » : the head, « de », and the name after it are one place too.
                if head and k == 0 and j + 1 < len(tokens) and tokens[j].lower() in ('de', 'du', 'des') \
                        and tokens[j + 1][:1].isupper():
                    end = j + 1
                    while end < len(tokens) and tokens[end][:1].isupper():
                        end += 1
                    found.append((True, ' '.join(tokens[i:end])))
                i = j
            else:
                i += 1
    return found


def _photo_place_index() -> dict[str, dict]:
    """normalised place -> {nom: the photographed site, pays, lieu: the place as written}, the best known site first
    (« marrakech » -> Médina de Marrakech). Built once from the globe's list."""
    global _photo_places
    if _photo_places is None:
        _place_names()
        try:
            from ..tools.news import country_index   # lazy: the tools import core modules
            countries = {normalize(name) for _word, name, _pattern in country_index().words}
        except Exception:      # the list unreadable: no country to set aside
            countries = set()
        lower = {normalize(t) for row in _heritage_rows for t in _PHOTO_TOKEN.findall(row['n']) if t[:1].islower()}
        places: dict[str, dict] = {}
        for row in _heritage_rows:
            if not (row.get('img') and isinstance(row.get('la'), (int, float)) and isinstance(row.get('lo'), (int, float))):
                continue
            for head, run in _site_places(row['n']):
                key = normalize(run)
                words = key.split()
                if not words or key in places or key in countries or key in _PHOTO_NOT_PLACES \
                        or key in _PHOTO_NOT_SITES:
                    continue
                if max(map(len, words)) < 4 and key not in _PHOTO_SHORT_PLACES:
                    continue
                if head and normalize(run.split(' ')[0]) in lower:   # « Centre historique… », « Vieille ville… »
                    continue
                places[key] = {'nom': row['n'], 'pays': row.get('p') or None, 'lieu': run}
        _photo_places = places
    return _photo_places


def _photo_place_named(norm: str) -> dict | None:
    """{nom, pays, lieu} of the photographed site that a (normalised) sentence names by a nickname (« la tour
    Eiffel ») or by the place in its name (« Marrakech »): the first one named, the longest name at the same spot."""
    places = _photo_place_index()
    whole = r'(?<![a-z0-9]){}(?![a-z0-9])'
    for alias in sorted(_PHOTO_ALIASES, key=len, reverse=True):
        if alias in norm and re.search(whole.format(re.escape(alias)), norm):
            target = _PHOTO_ALIASES[alias]
            site = places.get(target)
            if site is None and target in _heritage_sites:
                site = {**_heritage_sites[target], 'lieu': alias.title()}   # « Bordeaux », not « port de la Lune »
            if site:
                return site
    best: tuple[int, int, dict] | None = None
    for key, place in places.items():
        if key not in norm:
            continue
        found = (_PHOTO_PLACE_ONLY_AS[key].search(norm) if key in _PHOTO_PLACE_ONLY_AS
                 else re.search(whole.format(re.escape(key)), norm))
        if found and (best is None or (found.start(), -len(key)) < best[:2]):
            best = (found.start(), -len(key), place)
    return best[2] if best else None


def place_photos(text: str) -> dict | None:
    """{titre, pays, photos: [{nom, img, la, lo, pays}]} for a question that names a World Heritage site (that site
    first, then the best known of its country) or a country (its best known sites); at most PHOTOS_MAX, only sites
    with a photo. None when no place is named, or when the place has no photographed site. 11/11/2026: the site is
    also found by the city or the monument in its name (« Marrakech », « la tour Eiffel »), and a capital gives the
    sites of its country (« Tokyo »)."""
    norm = normalize(text)
    if not norm:
        return None
    _place_names()
    site = None
    for key in sorted(_heritage_sites, key=len, reverse=True):   # « medine de fes » before « fes »
        if key in norm and key not in _PHOTO_NOT_SITES and len(key) > 2 \
                and re.search(rf'(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])', norm):
            site = _heritage_sites[key]
            break
    if not site:
        site = _photo_place_named(norm)
    country = site.get('pays') if site else None
    if not site:
        try:
            from ..tools.news import country_index   # lazy: the tools import core modules
            index = country_index()
        except Exception:      # the list unreadable: no country is known, no photo
            return None
        best: tuple[int, int, str] | None = None
        for word, name, _pattern in index.words:
            if normalize(name) != normalize(word):   # a capital or a short name, not the country's own name
                continue
            key = normalize(word)
            if key not in norm:
                continue
            found = (_PHOTO_AMBIGUOUS[key].search(norm) if key in _PHOTO_AMBIGUOUS
                     else re.search(rf'(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])', norm))
            if found and (best is None or found.start() < best[0] or (found.start() == best[0] and len(key) > best[1])):
                best = (found.start(), len(key), name)
        whole = r'(?<![a-z0-9]){}(?![a-z0-9])'
        if best is None:      # « la Chine », « l'Angleterre »: the list knows them under a longer name
            for alias, name in _PHOTO_COUNTRY_ALIASES.items():
                found = re.search(whole.format(re.escape(alias)), norm) if alias in norm else None
                if found and (best is None or found.start() < best[0]):
                    best = (found.start(), len(alias), name)
        if best is None:      # a capital names its country: « Tokyo » -> the best known sites of Japan
            for word, name, _pattern in index.words:
                key = normalize(word)
                if normalize(name) == key or len(key) < 4 or key in _PHOTO_NOT_CAPITALS or key not in norm:
                    continue
                found = (_PHOTO_PLACE_ONLY_AS[key].search(norm) if key in _PHOTO_PLACE_ONLY_AS
                         else re.search(whole.format(re.escape(key)), norm))
                if found and (best is None or found.start() < best[0]
                              or (found.start() == best[0] and len(key) > best[1])):
                    best = (found.start(), len(key), name)
        country = best[2] if best else None
        if not country:
            return None
    rows = [r for r in _heritage_rows if r.get('img') and isinstance(r.get('la'), (int, float))
            and isinstance(r.get('lo'), (int, float))]
    first = [r for r in rows if site and r['n'] == site['nom']][:1]
    rest = [r for r in rows if country and r.get('p') == country and r not in first]
    photos = [{'nom': r['n'], 'img': r['img'], 'la': r['la'], 'lo': r['lo'], 'pays': r.get('p') or None}
              for r in (first + rest)[:PHOTOS_MAX]]
    if not photos:
        return None
    return {'titre': (site.get('lieu') or site['nom']) if site else country, 'pays': country, 'photos': photos}


# 08/11/2026 (laboratoire 3D de l'accueil, reviews/accueil-holographique.md § 3) : open a scene of the catalogue
# (tools/lab.py), say honestly that a subject asked in 3D has no scene yet, and read the commands on the scene shown.
SCENE_SHOW = (r'(?:montre|montrer|montres|montrez|fais voir|fait voir|faire voir|affiche|afficher|affiches|affichez|'
              r'visualise|visualiser|visualisez|ouvre|ouvrir|ouvrez|construis|construire|construisez|modelise|'
              r'modeliser|modelisez|represente|representer|je veux voir|j aimerais voir|je voudrais voir)')
SCENE_3D = (r'(?:en (?:3d|3 d|trois d|trois dimensions|3 dimensions|hologramme|holographie)|holographique|'
            r'hologramme d(?:e|u|es|un|une)?|(?:modele|maquette) (?:en )?3d)')
# Asking for a new object also says « fais-moi », « crée », « dessine »… (only with a 3D marker: too broad alone).
SCENE_MAKE = (rf'(?:{SCENE_SHOW}|fais moi|fais nous|fais|fait|faire|cree|creer|creez|genere|generer|generez|'
              r'dessine|dessiner|dessinez|fabrique|fabriquer)')
# A page, the sky or the Earth asked in 3D stay with their own routing (Monde 3D, Simulations…).
SCENE_NOT_SUBJECT = (r'(?:page|onglet|accueil|memoire|globe|monde|carte du monde|planisphere|simulations?|parametres|'
                     r'reglages|taches|terre|planetes?|systeme solaire|lune|soleil|orbites?|satellites?|iss|etoiles?|'
                     r'galaxies?|ciel|noyau)')
# Published content or the user's own things are looked for elsewhere (web, memory, files), never shown as a scene.
_SCENE_ELSEWHERE = re.compile(r'\b(?:recherches?|decouvertes?|actualites?|articles?|prix|internet|en ligne|wikipedia|'
                              r'videos?|photos?|images?|publications?|etudes?|projets?|notes?|fichiers?|dossiers?|'
                              r'documents?)\b')
_SCENE_SHOW_RE = re.compile(rf'\b{SCENE_SHOW}\b')
_SCENE_3D_REQUEST = re.compile(rf'^(?!.*\b{SCENE_NOT_SUBJECT}\b).*\b{SCENE_MAKE}\b.*\b{SCENE_3D}\b')
# 11/11/2026 (ID Studio IA, à la voix : « … modèle 3D d'une voiture », reçu par la dictée sans son verbe ; AURORA a bavardé au
# lieu de construire la maquette). La chose demandée est aussi nommée telle quelle : « un modèle 3D de… », « une
# maquette 3D de… », avec un souhait (« je veux », « il me faut ») ou sans aucun verbe. Jamais quand la personne parle
# d'un modèle qu'elle a déjà (« j'ai un modèle 3D de… », « le modèle 3D de la voiture »).
_MODEL_3D_NAMED = re.compile(r'\b(?P<det>(?:un|une|des|le|la|les|ce|cet|cette|mon|ma|mes|ton|ta|tes|son|sa|ses|notre|'
                             r'votre) )?(?:modele|maquette) (?:en )?(?:3d|3 d|trois dimensions) d(?:e|u|es)? '
                             r'(?:(?:un|une|la|le|l|les) )?\w')
_MODEL_3D_WISH = re.compile(r'\b(?:je veux|je voudrais|j aimerais|il me faut|il nous faut|j ai besoin|on a besoin|donne|'
                            r'donnez|propose|proposez|prepare|preparez)\b')
_MODEL_3D_OWNED = re.compile(r'\b(?:j ai|j avais|on a|nous avons|tu as|vous avez|il y a|voici|voila|c est|ce sont|avec)'
                             r'(?: deja)? $')
_MODEL_3D_DEFINITE = frozenset({'le', 'la', 'les', 'ce', 'cet', 'cette', 'mon', 'ma', 'mes', 'ton', 'ta', 'tes', 'son',
                                'sa', 'ses', 'notre', 'votre'})
_SCENE_NOT_SUBJECT_RE = re.compile(rf'\b{SCENE_NOT_SUBJECT}\b')
_SCENE_WORDS: tuple = ((), ())   # (catalogue signature, [(scene id, compiled words)]), rebuilt when it changes


def _scene_word(mot: str) -> str:
    """Regex of a scene word, each word also in the plural (« bras robotiques »)."""
    parts = []
    for token in normalize(mot).split():
        parts.append(re.escape(token) + ('' if token[-1] in 'sx' or token.isdigit() else 's?'))
    return ' '.join(parts)


def _scene_catalogue() -> list[tuple[str, re.Pattern]]:
    """[(scene id, words)] of the valid scenes; [] when they cannot be read (safe fallback: no scene opens and the
    usual routing goes on)."""
    global _SCENE_WORDS
    try:
        from ..tools.lab import catalogue   # lazy: tools are owned by another module
        scenes = catalogue()
    except Exception:
        logging.getLogger('aurora.core').exception('Catalogue des scènes 3D illisible')
        return []
    key = tuple((s['id'], tuple(s['mots'])) for s in scenes)
    if _SCENE_WORDS[0] != key:
        built = []
        for scene_id, mots in key:
            words = sorted({_scene_word(m) for m in mots if normalize(m)}, key=len, reverse=True)
            if words:
                built.append((scene_id, re.compile(r'\b(?:' + '|'.join(words) + r')\b')))
        _SCENE_WORDS = (key, built)
    return _SCENE_WORDS[1]


def scene_subject(text: str, *, normalized: bool = False) -> str | None:
    """Id of the catalogue scene a request asks to show (« montre-moi comment fonctionne un bras robotique »): a
    display verb, then the words of the scene. None otherwise (also for the web, the memory or the files)."""
    norm = text if normalized else normalize(text)
    verb = _SCENE_SHOW_RE.search(norm)
    if verb is None or _SCENE_ELSEWHERE.search(norm):
        return None
    # The scene named with the most words wins (« la gourde 2 » is not « la gourde »), the first one otherwise.
    found, length = None, 0
    for scene_id, words in _scene_catalogue():
        match = words.search(norm, verb.end())
        if match and len(match.group()) > length:
            found, length = scene_id, len(match.group())
    return found


def _fold(text: str) -> str:
    """Lower case without accents, ONE character for each character of ``text`` (its indices stay valid), so that
    words found in it are taken from the original with their accents."""
    out = []
    for ch in text:
        folded = strip_accents(ch).lower()
        out.append(folded if len(folded) == 1 else ' ')
    return ''.join(out)


_VERB_3D = (r"\b(?:montre|affiche|fais voir|fait voir|fais|fait|visualise|ouvre|construis|modelise|represente|"
            r"dessine|cree|genere|fabrique)\w*(?:[\s-]+(?:moi|nous))?")
_MARK_3D = (r"(?:en\s+(?:3\s*-?\s*d|trois\s+d(?:imensions)?|3\s+dimensions|hologramme|holographie)\b|"
            r"sous\s+forme\s+d.?hologramme|holographique)")
_SUBJECT_3D = [
    # « un modèle 3D d'une voiture », « la maquette en 3D du pont » : first, or « montre-moi un modèle en 3D d'une
    # voiture » would give « un modèle » as its subject (11/11/2026).
    re.compile(r"\b(?:modele|maquette)\s+(?:en\s+)?(?:3\s*-?\s*d|trois\s+dimensions)\s+(?:d['’]|de\s+|du\s+|des\s+)"
               r"(?P<sujet>[^.?!;]{2,80})"),
    re.compile(_VERB_3D + r"[\s,:]+" + _MARK_3D + r"[\s,:]+(?P<sujet>[^.?!;]{2,80})"),   # « montre en 3D un … »
    re.compile(_VERB_3D + r"[\s,:]+(?P<sujet>[^.?!;]{2,80}?)[\s,]+" + _MARK_3D),        # « montre un … en 3D »
    re.compile(r"\bhologramme\s+(?:d'|de\s+|du\s+|des\s+)(?P<sujet>[^.?!;]{2,80})"),
]
_SUBJECT_LEAD = re.compile(r"^(?:comment (?:fonctionne(?:nt)?|marche(?:nt)?|est fait|sont faits?|est faite|"
                           r"sont faites)\s+|a quoi ressemble(?:nt)?\s+|l'interieur (?:d'|de |du |des )|ce qu'est\s+)")
_SUBJECT_POLITE = re.compile(r"[\s,]+(?:s['’ ]il (?:te|vous) plait|stp|svp|merci(?: beaucoup| d['’ ]avance)?)[\s,]*$")


def _model_3d_named(norm: str) -> bool:
    """« un modèle 3D de… », « maquette 3D d'un… » names the thing to build, with a wish or with no verb at all; not a
    model the person already has (a definite article or a possessive without a wish, « j'ai un… », « voici un… »)."""
    if _SCENE_NOT_SUBJECT_RE.search(norm):
        return False
    match = _MODEL_3D_NAMED.search(norm)
    if match is None:
        return False
    before = norm[:match.start()]
    if _MODEL_3D_WISH.search(before):
        return True
    return (match.group('det') or '').strip() not in _MODEL_3D_DEFINITE and not _MODEL_3D_OWNED.search(before)


def scene_3d_asked(text: str, *, normalized: bool = False) -> bool:
    """An object is asked in 3D (« montre-moi un moteur en 3D », « fais-moi un hologramme d'un cœur », « un modèle 3D
    d'une voiture »), not a page, the sky, the Earth, nor something published (web) or the user's own (memory, files)."""
    norm = text if normalized else normalize(text)
    if _SCENE_ELSEWHERE.search(norm):
        return False
    return bool(_SCENE_3D_REQUEST.search(norm)) or _model_3d_named(norm)


def scene_request_3d(text: str) -> dict | None:
    """{sujet} when an object is asked in 3D (« montre-moi un moteur électrique en trois dimensions »); the subject is
    taken from the original text ('' when it cannot be isolated). None for a page, the sky or the Earth."""
    if not scene_3d_asked(text):
        return None
    raw = strip_wake_word(text or '').strip()
    folded = _fold(raw)
    for pattern in _SUBJECT_3D:
        match = pattern.search(folded)
        if not match:
            continue
        start, end = match.span('sujet')
        lead = _SUBJECT_LEAD.match(folded[start:end])
        if lead:
            start += lead.end()
        polite = _SUBJECT_POLITE.search(folded[start:end])   # « … s'il te plaît », « … merci » : not the object
        if polite:
            end = start + polite.start()
        sujet = raw[start:end].strip(" '’-,:").strip()
        return {'sujet': sujet[:80]}
    return {'sujet': ''}


# Commands on the scene shown (options.scene), first match wins. The table is the contract's.
_SCENE_POINTER = (r'(?:cette piece|cette partie|ce composant|cet element|ce morceau|celle ci|celui ci|celle la|'
                  r'celui la|la piece selectionnee|l element selectionne|la selection|ca|cela)')
_SCENE_ACTIONS: list[tuple[str, re.Pattern]] = [(name, re.compile(pattern)) for name, pattern in [
    # 10/11/2026 (ID Studio IA : « redresser et renommer »), on a kept model only (SCENE_MODEL_ONLY): « redresse-le » stands
    # it another way, « tourne-le » turns it a quarter turn on itself. « fais-le tourner » stays an animation.
    ('redresser', r'\b(?:redresse|redresser|redressez)\b|'
                  r'\b(?:mets|met|mettre|mettez|remets|remet|remettre|pose|poser|posez)(?: (?:le|la|les))?'
                  r'(?: (?:modele|objet))? (?:debout|droit|droite|a l endroit|d aplomb|sur ses pieds|sur pied)\b|'
                  r'\b(?:il|elle|c) est (?:couche|couchee|a l envers|penche|penchee|de travers|sur le cote|'
                  r'sur le dos)\b|\bretourne(?:r|z)? (?:le|la)$'),
    ('tourner', r'\bquart de tour\b|'
                r'^(?:tourne|tournez|pivote|pivotez)(?: (?:le|la|les))?(?: (?:modele|objet))?(?: (?:sur lui meme|'
                r'sur elle meme|a gauche|a droite|vers la gauche|vers la droite|de cote|de profil|de face))?$|'
                r'\b(?:montre|voir)(?: moi)? (?:l autre cote|l autre face|son autre cote)\b'),
    ('noyau',r'\b(?:reviens|revenir|revenez|retourne|retourner|retournez|retour|ramene moi|ramene nous|repasse)\b.*'
              r'\bnoyau\b|\bnoyau central\b|\b(?:ferme|fermer|fermez|quitte|quitter|quittez|sors de|sortir de)'
              r'(?: moi)? (?:la |cette |le |l )?(?:scene|vue 3d|hologramme|laboratoire|labo|maquette)\b'),
    ('assembler', r'\b(?:remonte|remonter|remontez|rassemble|rassembler|rassemblez|reassemble|reassembler|assemble|'
                  r'assembler|assemblez|recompose|recomposer|reconstitue|reconstituer)\b|'
                  r'\bremets? (?:\w+ ){0,3}en place\b'),
    ('tout_montrer', r'\b(?:montre|montrer|affiche|afficher|reaffiche|reafficher|remets|remettre|remet|'
                     r'fais reapparaitre|fait reapparaitre)(?: moi)?(?: a nouveau| de nouveau)? (?:toutes (?:les )?'
                     r'(?:pieces|parties)|tous les (?:composants|elements|morceaux)|tout)\b|\btout (?:montrer|'
                     r'afficher|reafficher)\b'),
    ('isoler', r'\b(?:isole|isoler|isolez)\b|\b(?:montre|montrer|affiche|afficher|garde|garder|laisse|laisser)'
               r'(?: moi)? (?:seulement|uniquement|juste)\b|\bne (?:montre|garde|laisse|affiche)(?: moi)? que\b'),
    ('interieur', r'\b(?:l interieur|interieur|dedans|vue en coupe|en coupe|coupe transversale|transparence|'
                  r'transparent|transparente|transparents|a travers|rayons x)\b'),
    ('arreter', r'^(?:stop|stoppe|pause|arrete|arrete tout|arrete toi|fige|fige le|fige la|fige tout)$|'
                r'\b(?:arrete|arreter|arretez|stoppe|stopper|coupe|couper|mets en pause|met en pause|suspends|'
                r'suspendre)(?: moi)? (?:l |le |la |les |son |sa |ses |cette |ce )?(?:animation|mouvement|'
                r'mouvements|rotation|simulation|demonstration)\b|\b(?:fige|figer|figez|immobilise|immobiliser)\b|'
                r'\barrete (?:le|la|les|ca|cela)$|\barrete de (?:bouger|tourner|l animer)\b|\bne bouge plus\b'),
    ('eclater', r'\b(?:demonte|demonter|demontez|demontes|eclate|eclater|eclatez|eclatee|decompose|decomposer|'
                r'decomposez|desassemble|desassembler|separe|separer|separez|disseque|dissequer)\b'),
    ('animer', r'\b(?:fais|fait|faire|faites)(?: le| la| les| moi)? (?:bouger|tourner|fonctionner|marcher|pivoter)\b|'
               r'\b(?:anime|animer|animez|simule|simuler|simulez|mets en mouvement|met en mouvement|'
               r'mettre en mouvement)\b|\b(?:lance|lancer|demarre|demarrer|relance|relancer|reprends|reprendre)'
               r'(?: moi)? (?:l |la |le |une )?(?:animation|simulation|mouvement|demonstration)\b|'
               r'\bcomment (?:il|elle|ils|elles|ca) (?:bouge|bougent)\b'),
    ('recentrer', r'\b(?:recentre|recentrer|recentrez|recadre|recadrer|reinitialise la vue|reinitialise la camera|'
                  r'vue initiale|vue de depart|vue d origine|vue par defaut|vue d ensemble|camera initiale|'
                  r'remets la vue|reviens a la vue)\b'),
    ('agrandir', r'\b(?:agrandis|agrandir|agrandissez|agrandit|grossis|grossir|grossissez|en plus grand|plus gros|'
                 r'plus grosse)\b'),
    ('masquer', r'\b(?:cache|cacher|cachez|masque|masquer|masquez|retire|retirer|retirez|enleve|enlever|enlevez|ote|'
                r'oter|otez|supprime|supprimer|efface|effacer|fais disparaitre|fait disparaitre|faire disparaitre)\b'),
    ('expliquer', r'\b(?:explique|expliquer|expliquez|decris|decrire|decrivez|detaille|detailler|presente|presenter|'
                  r'c est quoi|qu est ce que|qu est ce qu|a quoi sert|a quoi servent|sert a quoi|servent a quoi|'
                  r'comment fonctionne|comment fonctionnent|comment marche|comment marchent|role d|role du|role de|'
                  r'comment (?:ca|cela|il|elle) (?:marche|fonctionne)|'
                  r'fonction d|fonction du|fonction de)\b|\b(?:montre|montrer)(?: moi| nous)? (?:son|le|leur) '
                  r'fonctionnement\b'),
    ('montrer', r'\b(?:montre|montrer|montrez|affiche|afficher|affichez|fais voir|fait voir|zoome|zoomer|zoomez|zoom|'
                r'focalise|focaliser|concentre toi|centre la vue sur|centre sur|cible|selectionne|selectionner|'
                r'mets en evidence|mettre en evidence|surligne|surligner|eclaire|eclairer|ou est|ou sont|'
                r'ou se trouve|ou se trouvent|ou se situe|situe|localise|localiser|pointe|indique moi)\b'),
]]
# Actions that need a piece or a group; the others act on the whole scene (a piece found is kept for animer,
# interieur and expliquer).
SCENE_TARGETED = ('montrer', 'masquer', 'isoler', 'agrandir')
_SCENE_KEEPS_TARGET = SCENE_TARGETED + ('animer', 'interieur', 'expliquer')
# Commands that only a kept model takes (tools/lab.MODEL_ORIGINS: a file of the user, a shape rebuilt from a photo):
# on any other scene these words keep their usual meaning.
SCENE_MODEL_ONLY = ('redresser', 'tourner', 'renommer')
_SCENE_KEPT = ('importe', 'reconstruit')
_RENAME = re.compile(r'\b(?:renomme|renommer|renommez|rebaptise|rebaptiser|rebaptisez)\b|'
                     r'\b(?:appelle|appeler|appelez|nomme|nommer|nommez) (?:le|la|les|ce modele|cet objet|cette forme)\b|'
                     r'\bchange(?:r|z)? (?:son|le|de) (?:nom|titre)\b')
# The same verbs in the text as written (« renomme-le », accents folded): where the new name starts.
_RENAME_RAW = re.compile(r'\b(?:renomm\w+|rebaptis\w+|appell?\w*|nomm\w+)\b|\bchange\w* (?:son|le|de) (?:nom|titre)\b')
_RENAME_LEAD = re.compile(r"^[\s-]*(?:(?:le|la|les|l['’]|ce|cet|cette|son|sa)\b\s*"
                          r"(?:(?:modele|objet|forme|scene|maquette|nom|titre)\b(?:\s+3d\b)?)?)?"
                          r"[\s,:;-]*(?:(?:en|par|comme)\b)?[\s,:;-]*")
# « explique » with no piece is about the scene only when the sentence points at it (otherwise: a conversation).
_SCENE_ITSELF = re.compile(r'\b(?:son|sa|ses|leur) (?:fonctionnement|role|principe|mecanisme)\b|'
                           r'\b(?:le|ce) (?:fonctionnement|principe|mecanisme)\b|'
                           r'\bcomment (?:il|elle|ils|elles|ca|cela) (?:fonctionne|fonctionnent|marche|marchent)\b|'
                           r'\b(?:explique|expliquez|decris|presente)(?: moi| nous)?(?: le| la| les| ca| cela| tout)?$|'
                           r'\b(?:la|cette) scene\b|\bce (?:modele|robot|bras|mecanisme|montage|objet|systeme)\b')
_INDEFINITE_BEFORE = re.compile(r'\b(?:un|une)\s*$')
# « retire le boîtier et montre les bobines », « démonte-le, puis fais-le tourner » : one order per clause (cahier § 4).
_SCENE_CLAUSES = re.compile(r'\s*[,;]\s*(?:(?:et|puis|ensuite)\s+)*|\s+(?:et\s+puis|et\s+ensuite|puis|ensuite|et)\s+',
                            re.IGNORECASE)
SCENE_MAX_ORDERS = 6


def _scene_target(norm: str, scene: dict) -> tuple[str, str] | None:
    """('piece' | 'groupe', id) named in the sentence by its words: the longest word wins, the piece before the
    group when equal. A word after « un » / « une » is a new subject (« un moteur »), not this scene's part."""
    best: tuple[int, int, str, str] | None = None
    for rank, (kind, items) in enumerate((('piece', scene.get('pieces') or []), ('groupe', scene.get('groupes') or []))):
        for item in items:
            for mot in item.get('mots') or []:
                pattern = re.compile(r'\b' + _scene_word(mot) + r'\b')
                for match in pattern.finditer(norm):
                    if _INDEFINITE_BEFORE.search(norm[:match.start()]):
                        continue
                    candidate = (len(match.group(0)), -rank, kind, item['id'])
                    if best is None or candidate[:2] > best[:2]:
                        best = candidate
                    break
    return (best[2], best[3]) if best else None


def _scene_object(raw: str, verb_end: int) -> str:
    """The words after the verb in the original text (« le boîtier » -> « boîtier »), for an honest « not found »."""
    tail = raw[verb_end:]
    folded = _fold(tail)
    match = re.match(r"[\s,-]*(?:(?:moi|nous)\b[\s,-]*)?(?:(?:le|la|les|l'|l’|un|une|des|du|de la|sur|seulement|"
                     r"uniquement|juste)\b\s*)*(?:(?:le|la|les|l'|l’)\s*)?", folded)
    start = match.end() if match else 0
    words = re.split(r'[.?!;,]', tail[start:])[0].strip(" '’-")
    return words[:40]


def _scene_suite(text: str, scene: dict, selected: str | None, order: dict) -> dict:
    """``order`` (the whole sentence's), or for several commands in one sentence (« retire le boîtier et montre les
    bobines ») the first clause's order with the next ones in ``suite``, in the order said. A clause that only names
    a part (« montre les bobines et le rotor ») takes the verb before it. When a clause is not a command on this
    scene, or there are more than SCENE_MAX_ORDERS, the sentence stays one order, as before."""
    clauses = [c for c in _SCENE_CLAUSES.split(strip_wake_word(text or '')) if normalize(c)]
    if len(clauses) < 2 or len(clauses) > SCENE_MAX_ORDERS:
        return order
    orders: list[dict] = []
    for clause in clauses:
        one = extract_scene_order(clause, scene, selected, whole=False)
        norm = normalize(clause)
        if one is None and orders and orders[-1]['action'] in _SCENE_KEEPS_TARGET \
                and not any(pattern.search(norm) for _, pattern in _SCENE_ACTIONS):
            target = _scene_target(norm, scene)
            one = {'action': orders[-1]['action'], target[0]: target[1]} if target else None
        if one is None:
            return order
        if one not in orders:
            orders.append(one)
    return {**orders[0], 'suite': orders[1:]} if len(orders) > 1 else orders[0]


def _scene_new_name(raw: str, scene: dict) -> str:
    """The name asked for in « renomme-le en Veste VITA », as written (« Veste VITA »), or '' when none is given.
    « renomme la gourde en bidon » : the current name said before « en » is not part of the new one."""
    verb = _RENAME_RAW.search(_fold(raw))
    if not verb:
        return ''
    tail = raw[verb.end():]
    rest = tail[_RENAME_LEAD.match(_fold(tail)).end():]
    said = re.match(r'^(.+?)\s+en\s+(.+)$', rest, re.IGNORECASE)
    if said:
        known = {normalize(str(scene.get('titre') or ''))} | {str(m) for m in scene.get('mots') or []}
        if normalize(said.group(1)) in known:
            rest = said.group(2)
    return ' '.join(rest.split()).strip(' «»"\'’“”.,;:!?')[:80].strip()


def extract_scene_order(text: str, scene: dict | None, selected: str | None = None, *, whole: bool = True) -> dict | None:
    """Command on the scene shown (« démonte-le », « montre les servomoteurs », « agrandis cette pièce ») ->
    {action, piece?, groupe?} ; with « inconnu » (the words naming no piece) or « sans_selection » (« cette pièce »
    with nothing selected) when its target is missing, and ``suite`` (the next orders) for a sentence of several
    commands. None: not a command on this scene (usual routing).
    « cette pièce », « celle-ci » and « ça » designate ``selected`` (options.scene.piece)."""
    if not isinstance(scene, dict):
        return None
    if whole:
        order = extract_scene_order(text, scene, selected, whole=False)
        return _scene_suite(text, scene, selected, order) if order else None
    norm = normalize(text)
    kept = scene.get('origine') in _SCENE_KEPT
    if kept and norm and _RENAME.search(norm):
        # Read before anything else: a word of the new name (« … en Montre 3D ») is not another command.
        titre = _scene_new_name(strip_wake_word(text or ''), scene)
        return {'action': 'renommer', 'titre': titre} if titre else {'action': 'renommer', 'sans_titre': True}
    if not norm or _SCENE_3D_REQUEST.search(norm):
        return None   # a new object asked in 3D: a new scene, not a command on this one
    other = scene_subject(norm, normalized=True)
    if other and other != scene.get('id'):
        return None
    action = next((name for name, pattern in _SCENE_ACTIONS
                   if (kept or name not in SCENE_MODEL_ONLY) and pattern.search(norm)), None)
    if action is None:
        return None
    order: dict = {'action': action}
    target = _scene_target(norm, scene)
    pieces = {p.get('id') for p in scene.get('pieces') or []}
    pointer = re.search(rf'\b{_SCENE_POINTER}\b', norm)
    if target is None and pointer:
        if selected in pieces:
            target = ('piece', selected)
        elif action in SCENE_TARGETED:
            order['sans_selection'] = True
            return order
    if action in _SCENE_KEEPS_TARGET and target:
        order[target[0]] = target[1]
    if action in SCENE_TARGETED and not target:
        if scene_subject(norm, normalized=True):
            return None   # « montre-moi un bras robotique » : the scene itself, opened again by the router
        raw = strip_wake_word(text or '')
        verb_raw = dict(_SCENE_ACTIONS)[action].search(_fold(raw))
        order['inconnu'] = _scene_object(raw, verb_raw.end()) if verb_raw else ''
    if action == 'expliquer' and not target and not pointer and not _SCENE_ITSELF.search(norm) \
            and not scene_subject(f'montre {norm}', normalized=True):
        return None   # « explique-moi la relativité » : a conversation, not this scene
    return order


_TOPICS = [
    ('espace', r'\b(?:spatial|spatiale|spatiales|spatiaux|espace|astronomie|astrophysique|nasa|esa|fusee|fusees|'
               r'planetes?|exoplanetes?|cosmos|univers|galaxies?|mars|lune|astronautes?)\b'),
    ('ia', r'\b(?:ia|intelligence artificielle|apprentissage automatique|machine learning|robotique|robots?)\b'),
    ('technologie', r'\b(?:tech|technologie|technologies|technologiques?|informatique|numerique|high tech)\b'),
    ('climat', r'\b(?:climat|climatiques?|rechauffement)\b'),
    ('environnement', r'\b(?:environnement|ecologie|biodiversite|pollution)\b'),
    ('sante', r'\b(?:sante|medecine|medical|medicale|medicaments?|maladies?|vaccins?)\b'),
    ('physique', r'\b(?:physique|quantique|particules?)\b'),
    ('biologie', r'\b(?:biologie|genetique|adn)\b'),
    ('archeologie', r'\b(?:archeologie|archeologique|fossiles?|dinosaures?)\b'),
]


def extract_topic(text: str) -> str | None:
    s = normalize(text)
    for topic, pattern in _TOPICS:
        if re.search(pattern, s):
            return topic
    return None


# 08/11/2026 (globe hologramme) : the news of the world (27 feeds, tools/news.py news.world, placed by country on the
# globe) unless the request is about science; the 5 science feeds (news.latest) keep the science subjects.
_SCIENCE_NEWS = re.compile(r'\b(?:sciences?|scientifiques?|recherches?|decouvertes?|chercheurs?|etudes?)\b')
_WORLD_NEWS_THEMES = [
    ('economie', r'\b(?:economie|economiques?|finances?|financieres?|bourses?|marches financiers|entreprises)\b'),
    ('sport', r'\b(?:sports?|sportives?|football|foot|rugby|tennis|basket|cyclisme|jeux olympiques)\b'),
    ('culture', r'\b(?:culture|culturelles?|cinema|musique|litterature|livres|expositions?|festivals?)\b'),
    ('voyage', r'\b(?:voyages?|tourisme|touristiques?)\b'),
]


def extract_world_news(text: str, topic: str | None = None) -> dict | None:
    """{pays?, theme?} when the news asked for are those of the world (or of a country), None for science news.
    The country is the first one the request names (tools/news.country_of, the same as for the articles)."""
    norm = normalize(text)
    if topic or _SCIENCE_NEWS.search(norm):
        return None
    order: dict = {}
    theme = next((t for t, p in _WORLD_NEWS_THEMES if re.search(p, norm)), None)
    if theme:
        order['theme'] = theme
    try:
        from ..tools.base import ToolError
        from ..tools.news import country_of   # lazy: tools are owned by another module
        try:
            pays = country_of(text)
        except ToolError:
            pays = None   # list of countries unreadable: the news of the whole world
    except ImportError:
        pays = None
    if pays:
        order['pays'] = pays
    return order


# --------------------------------------------------------------------------- memory

_SAVE_PREFIX = re.compile(
    r"^(?:(?:est-ce que |peux[- ]tu |tu peux |pourrais[- ]tu |tu pourrais |je veux que tu |j'aimerais que tu |"
    r"merci de |il faut que tu )\s*)?"
    r"(?:retiens(?: bien)?|retenir|souviens[- ]toi(?: bien)?|rappelle[- ]toi|m[ée]morise[rs]?|note[rs]?(?: bien)?|"
    r"n'oublie(?:s)? pas|garde (?:bien )?en (?:m[ée]moire|t[êe]te)|garde [àa] l'esprit|"
    r"enregistre[rs]?(?: (?:dans|en) (?:ta|la) m[ée]moire)?|sauvegarde[rs]?(?: (?:dans|en) (?:ta|la) m[ée]moire)?|"
    r"ajoute[rs]? (?:[àa]|dans) (?:ta|ma|la|notre) m[ée]moire|m[ée]morise)\s*"
    r"(?:(?:bien|donc|ceci|cela|ça|ca)\s*)?(?::|que |qu'|de |d'|le fait que |le fait qu')?\s*", re.I)


def extract_memory_content(text: str) -> dict:
    original = strip_wake_word(text).strip()
    content = _SAVE_PREFIX.sub('', original, count=1).strip(' .!')
    content = re.sub(r"\s+(?:dans|en) (?:ta|ma|la) m[ée]moire\s*$", '', content, flags=re.I).strip(' .!')
    if not content:
        content = original.strip(' .!')
    s = normalize(content)
    if re.search(r'\b(?:je prefere|j aime|j adore|je deteste|je n aime pas|ma preferee?|mon prefere|prefere|'
                 r'preference|toujours en|jamais de)\b', s):
        category, kind = 'preference', 'preference'
    elif re.search(r'\b(?:projet|projets)\b', s):
        category, kind = 'projet', 'project'
    elif re.search(r'\b(?:j ai decide|nous avons decide|on a decide|decision)\b', s):
        category, kind = 'decision', 'decision'
    elif re.search(r'\b(?:je m appelle|mon nom|j habite|ma femme|mon mari|mon fils|ma fille|mon anniversaire|'
                   r'ma date de naissance|mes enfants|mon frere|ma soeur|ma mere|mon pere)\b', s):
        category, kind = 'personnel', 'fact'
    else:
        category, kind = 'general', 'fact'
    return {'content': content, 'category': category, 'kind': kind}


# --------------------------------------------------------------------------- dispatcher

def extract_slots(intent: str, text: str, normalized: str | None = None) -> dict:
    """Slots for an intent (contract §5). Missing values are None, never invented (the one default, the
    « gros fichiers » size threshold, is flagged size_default=True and stated in the answer)."""
    text = text or ''
    if intent == 'system.status':
        return {'metric': extract_metric(text)}
    if intent == 'system.processes':
        return {'sort': extract_sort(text)}
    if intent == 'system.kill_process':
        return extract_process(text)
    if intent == 'shell.run':
        return extract_command(text)
    if intent == 'pc.control':
        return extract_gesture(text)
    if intent == 'app.launch':
        return extract_app(text)
    if intent == 'youtube.comment':
        return extract_comment(text)
    if intent == 'memory.search':
        return {'query': extract_query(text, kind='memory')}
    if intent == 'memory.save':
        return extract_memory_content(text)
    if intent == 'web.search':
        return {'query': extract_query(text, kind='web')}
    if intent == 'news.latest':
        topic = extract_topic(text)
        slots = {'topic': topic}
        world = extract_world_news(text, topic)
        if world is not None:
            slots['monde'] = world   # planner: news.world rather than the science feeds
        return slots
    if intent == 'weather.current':
        slots = {'city': extract_city(text)}
        days = extract_forecast_days(text)
        if days is not None:          # « demain », « ce week-end »: forecast, not current conditions
            slots['days'] = days
        return slots
    if intent == 'time.now':
        # « quelle heure est-il à Tokyo ? »: the place is resolved to a time zone by the tool.
        place = extract_city(text) if re.search(r'\b(?:a|au|aux|en)\b', normalize(text)) else None
        return {'place': place} if place else {}
    if intent == 'reminder.set':
        # 16/11/2026: the one slot read against the clock (« dans 20 minutes » is a moment only from now) — by
        # tasks/reminders.understand, in the person's own words; never by a model.
        from ..tasks.reminders import understand   # lazy: owned by the tasks module
        return understand(text)
    if intent == 'calc.evaluate':
        return {'expression': extract_expression(text)}
    if intent == 'calc.convert':
        conv = extract_conversion(text)
        return conv or {'value': None, 'from_unit': None, 'to_unit': None}
    if intent == 'sim.orbit':
        return extract_orbit(text)
    if intent == 'sim.solar_system':
        return {'date': extract_date(text)}
    if intent in ('files.scan', 'files.cleanup'):
        slots = {'folder': extract_folder(text), 'older_than_days': extract_older_than_days(text)}
        size = extract_min_size_mb(text)
        norm = normalize(text)
        if size is None and (_LARGE_FILES.search(norm) or (intent == 'files.scan' and _FREE_SPACE.search(norm))):
            size = DEFAULT_LARGE_FILE_MB   # « gros fichiers »: default threshold, said so in the answer
            slots['size_default'] = True
        if size is not None:          # size criterion asked: no age criterion unless also asked
            slots['min_size_mb'] = size
            if slots['older_than_days'] is None and not _AGE_WORDS.search(norm):
                slots['older_than_days'] = 0
        elif slots['older_than_days'] is None and _FOLDER_SIZE.search(norm):
            slots['older_than_days'] = 0   # « combien de place prend le dossier … » : every file counts
        return slots
    if intent == 'task.plan':
        return {'goal': strip_wake_word(text).strip() or None}
    if intent == 'ui.navigate':
        # The 3D lab of the home page first (a catalogue scene, or an object asked in 3D that has none yet).
        scene_id = scene_subject(text)
        if scene_id:
            return {'page': 'home', 'scene': {'scene': scene_id, 'action': 'ouvrir'}}
        asked = scene_request_3d(text)
        if asked is not None:
            return {'page': None, 'sujet_3d': asked['sujet']}
        page = extract_page(text)
        globe = extract_globe_order(text) if page in (None, 'world') else {}
        if page is None and (globe.get('couche') in ('satellites', 'avions', 'evenements') or globe_place_named(text)):
            page = 'world'   # « où est l'ISS ? », « montre les avions », « zoome sur la Jordanie » : on the globe
        slots = {'page': page}
        if page == 'world' and globe:
            slots['globe'] = globe
        return slots
    return {}
