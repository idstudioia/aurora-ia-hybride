"""Real capability snapshot (Internet, Ollama, Claude, mode).

Injected into every system prompt BEFORE generation, so the model never claims
"I have no Internet" when the tools can reach it (or the reverse).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import config

log = logging.getLogger(__name__)

_internet: dict[str, Any] = {'value': None, 'at': 0.0}

MODE_LABELS = {
    'prive': 'privé (outils locaux et Ollama uniquement, aucun appel à Claude)',
    'equilibre': 'équilibré (local d’abord ; Claude seulement avec consentement à chaque requête)',
    'avance': 'avancé (Claude possible ; chaque envoi est montré avant et demande un consentement)',
}
# Same modes when Claude is not configured: never announce a cloud use that cannot happen.
MODE_LABELS_NO_CLOUD = {
    'prive': MODE_LABELS['prive'],
    'equilibre': 'équilibré (outils locaux et Ollama ; Claude indisponible : non configuré)',
    'avance': 'avancé (outils locaux et Ollama ; Claude indisponible : non configuré)',
}
# The only voice instruction, shown by the interface (ChatDrawer). Where the audio goes depends on the recognition
# mode chosen in the interface, which says it itself next to this text (B7, rapport §4.6: two texts before).
VOICE_HINT = 'Cliquez sur le micro (barre du bas) puis dites votre demande ; le mot « Aurora » en tête est facultatif.'
# Embedding-only models (never usable for a conversation): name heuristic when Ollama does not say.
_EMBED_NAME = ('embed', 'nomic-embed', 'mxbai-embed')


def invalidate() -> None:
    """Forget cached probes (settings changed): the next snapshot measures again."""
    _internet.update(value=None, at=0.0)
    try:
        from ..providers.ollama import ollama as provider  # lazy
        provider.invalidate()
    except Exception:
        pass


def is_embedding_name(name: str) -> bool:
    base = str(name or '').lower().split(':')[0]
    return any(part in base for part in _EMBED_NAME) or base.startswith('bge')


async def internet_available(*, force: bool = False) -> bool:
    """Quick HEAD probe, cached 60 s. Any HTTP answer (even 4xx/405) proves connectivity."""
    if config.offline():
        return False
    now = time.monotonic()
    if not force and _internet['value'] is not None and now - _internet['at'] < config.INTERNET_CHECK_TTL_S:
        return bool(_internet['value'])
    ok = False
    try:
        from ..netutil import ssl_context
        async with httpx.AsyncClient(timeout=config.INTERNET_CHECK_TIMEOUT_S, follow_redirects=False,
                                     verify=ssl_context()) as client:
            for url in config.INTERNET_CHECK_URLS:
                try:
                    await client.head(url)
                    ok = True
                    break
                except httpx.HTTPError:
                    continue
    except Exception:
        ok = False
    _internet.update(value=ok, at=time.monotonic())
    return ok


def _model_names(models: list) -> list[str]:
    names = []
    for item in models or []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get('name') or item.get('model')
            if name:
                names.append(str(name))
    return names


def _has_model(names: list[str], wanted: str) -> bool:
    base = wanted.split(':')[0]
    for name in names:
        if name == wanted or name == f'{wanted}:latest' or (':' not in wanted and name.split(':')[0] == base):
            return True
    return False


async def _chat_models(provider, names: list[str], embed_model: str) -> list[str]:
    """Installed models usable for a conversation: Ollama's declared capabilities when it gives
    them (/api/show), otherwise a name heuristic; the embedding model is always excluded."""
    declared: dict[str, list[str]] = {}
    reader = getattr(provider, 'model_capabilities', None)
    if reader is not None:
        try:
            declared = await reader(names) or {}
        except Exception:
            declared = {}
    embed_base = embed_model.split(':')[0]
    out = []
    for name in names:
        caps = declared.get(name)
        if caps:
            if 'completion' not in caps:
                continue
        elif is_embedding_name(name):
            continue
        if name.split(':')[0] == embed_base:
            continue
        out.append(name)
    return out


async def ollama_state(*, provider=None, settings: dict | None = None) -> dict:
    settings = settings or {}
    chat_model = settings.get('local_model') or config.local_model()
    state = {'up': False, 'models': [], 'chat_models': [], 'chat_model': chat_model,
             'embed_model': config.embed_model(), 'embed_ready': False,
             # 13/11/2026: the model chosen for the 3D maquettes ('' = the conversation model writes them too)
             'scene_model': str(settings.get('local_scene_model') or '')}
    try:
        if provider is None:
            from ..providers.ollama import ollama as provider  # lazy: written by another module owner
        up = bool(await provider.available())
        names = _model_names(await provider.models()) if up else []
    except Exception:
        return state
    state.update(up=up, models=names, embed_ready=up and _has_model(names, state['embed_model']))
    state['chat_models'] = await _chat_models(provider, names, state['embed_model']) if up else []
    if up:
        # 23/11/2026: on a PC where only the lighter model is installed (a card under 10 GB), it is the starting
        # model as long as the person has chosen none (config.note_installed_models). Said here at once, so that the
        # screen names the model that will really answer.
        start = config.note_installed_models(names)
        if chat_model != start and chat_model == config.DEFAULT_LOCAL_MODEL and not config.local_model_chosen():
            chat_model = state['chat_model'] = start
    state['chat_model_installed'] = up and _has_model(names, chat_model)
    return state


def _tier_prices(model: str) -> dict:
    """Base list price in USD per million tokens (contract §8 « prices »)."""
    try:
        from ..providers import pricing  # lazy
        card = pricing.prices_for(model)
    except Exception:
        return {}
    return {k: float(card[k]) for k in ('input', 'output', 'cache_read', 'cache_write')}


def cloud_state(*, provider=None) -> dict:
    fallback_tiers = {tier: {'model': model, 'effort': '', 'prices': _tier_prices(model)}
                      for tier, model in config.cloud_models().items()}
    try:
        if provider is None:
            from ..providers.anthropic_provider import claude as provider  # lazy
        configured = bool(provider.configured())
        tiers = provider.tiers() or fallback_tiers
    except Exception:
        return {'configured': False, 'provider': 'anthropic', 'tiers': fallback_tiers}
    clean = {}
    for name, info in tiers.items():
        info = info if isinstance(info, dict) else {}
        model = str(info.get('model', ''))
        clean[name] = {'model': model, 'effort': str(info.get('effort', '')), 'prices': _tier_prices(model)}
    return {'configured': configured, 'provider': 'anthropic', 'tiers': clean}


async def snapshot(*, ollama_provider=None, claude_provider=None, settings: dict | None = None,
                   force: bool = False) -> dict:
    """Capabilities (contract §8), measured now (Internet cached 60 s, Ollama cached by its provider).

    In private mode the Internet is not probed at all (each access needs the user's consent):
    ``internet`` is then None, shown as « non vérifié (mode privé) »."""
    if settings is None:
        try:
            settings = config.load_settings()
        except Exception:
            settings = config.default_settings()
    private = settings.get('mode') == 'prive'
    caps = {
        'internet': None if private else await internet_available(force=force),
        'ollama': await ollama_state(provider=ollama_provider, settings=settings),
        'cloud': cloud_state(provider=claude_provider),
        'mode': settings.get('mode', 'equilibre'),
        # 08/11/2026 (run 47 of the real AURORA): « as-tu le terminal ? » is answered with the real setting.
        'shell': bool(settings.get('shell_enabled')),
        'pc_control': bool(settings.get('pc_control_enabled')),   # 9/11/2026: the mouse and keyboard, same way
        'voice_hint': VOICE_HINT,
        'checked_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
    }
    return caps


def tool_names() -> list[str]:
    try:
        from ..tools.base import REGISTRY  # lazy
        return sorted(REGISTRY.keys())
    except Exception:
        return []


# What AURORA can do, in the user's words, one line per capability (like Athéna's « outil par outil » list,
# 29/10/2026: the local model listed only part of its tools). A line tied to tools is shown only when one of them
# is really registered; the others are built into AURORA itself (memory, conversations, pages, routines, agent).
CAPABILITY_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ('Ordinateur', 'l’état du PC mesuré (processeur, mémoire, disques, réseau, carte graphique), son historique, '
                   'les programmes gourmands, un diagnostic, et l’arrêt d’un programme avec votre accord',
     ('system.', 'process.')),
    ('Fichiers', 'les gros ou vieux fichiers d’un dossier autorisé, leur mise en quarantaine réversible avec votre '
                 'accord, et leur remise en place', ('files.',)),
    ('Calculs', 'calculs exacts et conversions d’unités', ('calc.',)),
    ('Heure et date', 'la date et l’heure du PC ou d’une grande ville', ('time.',)),
    ('Météo', 'la météo actuelle et la prévision sur 7 jours (Open-Meteo) ; sur le globe de Monde 3D, le vent, la '
              'pluie, la chaleur et la qualité de l’air du monde entier', ('weather.',)),
    ('Recherche web', 'chercher sur Internet (SearXNG, Wikipédia, DuckDuckGo) et lire les premières pages publiques',
     ('web.',)),
    ('Actualités', 'les actualités du monde ou d’un pays (27 flux : France 24, RFI, Euronews, BBC Afrique…), '
                   'placées par pays sur le globe, et les actualités scientifiques (5 flux RSS)', ('news.',)),
    ('Séismes et catastrophes', 'les séismes des dernières 24 heures (USGS) et, sur le globe, les catastrophes en '
                                'cours : séismes, tsunamis, volcans, cyclones, tornades', ('quakes.', 'events.')),
    ('Ciel', 'les avions en vol autour d’un lieu (adsb.lol) et la position des satellites (ISS, Tiangong, Hubble…), '
             'montrés sur le globe de Monde 3D ; le trafic aérien mondial seulement si vous avez allumé OpenSky '
             'dans Paramètres', ('sky.',)),
    ('Simulations', 'des orbites autour de la Terre, de Mars, de la Lune… et la position des planètes', ('sim.',)),
    # 08/11/2026: one predefined scene in data/scenes for now (tests/test_lab.py checks this line against the
    # catalogue); phase 4: an object asked in 3D is built on request (tools/lab.build_generated). 10/11/2026: a
    # model file of the user (tools/lab.import_model) and a shape rebuilt from a photo (providers/pixal3d_local.py,
    # with providers/triposr_local.py as the way out) open there too, by the buttons of the message bar.
    # 24/11/2026 (ID Studio IA : « au modèle 3D ajoute 2 modèles », la fusée à deux étages et le X-SWING).
    ('Laboratoire 3D', 'sur l’accueil, trois scènes prédéfinies (le bras robotique à 4 axes, la fusée à deux étages '
                       'et le X-SWING), et sur demande (« montre-moi '
                       'un moteur électrique en 3D ») une maquette construite automatiquement par l’IA avec des '
                       'formes simples, vérifiée par le code : des illustrations pédagogiques (jamais des '
                       'simulations) que l’on démonte, anime et explique pièce par pièce ; s’y ouvrent aussi, en un '
                       'seul bloc, un modèle 3D que vous avez déjà (fichier STL, GLB ou OBJ, bouton « Joindre un '
                       'fichier ») et la forme d’un objet reconstruite sur ce PC par une IA locale à partir d’une '
                       'photo (bouton « Photo vers 3D », avec un choix à côté : « Détaillé », 3 minutes environ avec '
                       'Pixal3D, ou « Rapide », 35 secondes environ avec TripoSR ; l’autre moteur sert de secours ; '
                       'les côtés que la photo ne montre pas sont inventés) ; le bouton « Modèles 3D » de l’accueil '
                       'liste les scènes gardées, pour en rouvrir une ou en retirer une (vous seul le faites, après '
                       'confirmation : elle est mise de côté, jamais effacée)', ('lab.',)),
    ('Analyse de données', 'un tableau CSV joint ou dans un dossier : totaux, évolution, valeurs anormales, '
                           'comparaison de périodes', ('data.',)),
    ('Studio', 'votre activité de créateur : GitHub, téléchargements, Discord, YouTube (vidéos, abonnés, '
               'commentaires) et site', ('studio.',)),
    # 08/11/2026 (run 46 of the real AURORA): asked for a little game, the local AI said it could not program.
    ('Rédaction', 'écrire des textes, des explications et du code, par exemple un petit jeu complet en HTML et '
                  'JavaScript à copier dans un fichier .html puis à ouvrir dans le navigateur ; ce code n’est ni lancé '
                  'ni enregistré sur le PC par AURORA', ()),
    # 9/11/2026: tools/pc_control.py (off by default, one approved gesture at a time).
    ('Souris et clavier', 'un geste à la fois sur le PC (cliquer à une position, taper un texte ou une touche dans '
                          'une fenêtre nommée, faire défiler), montré puis fait seulement après votre accord ; '
                          'désactivé par défaut (Paramètres), jamais dans un terminal, une fenêtre de sécurité ni '
                          'AURORA', ('pc.',)),
    # 9/11/2026: tools/apps.py (empty list by default).
    ('Applications', 'ouvrir par son nom une application de la liste que vous tenez dans Paramètres › Applications '
                     'autorisées (« ouvre YouTube Manager ») ; rien d’autre ne peut être lancé', ('app.',)),
    # 9/11/2026: tools/youtube_comments.py (the channel connected in Paramètres › Studio).
    ('YouTube', 'publier un commentaire sous la dernière vidéo de votre chaîne reliée (« mets un commentaire sous ma '
                'dernière vidéo »), votre texte ou rédigé par l’IA locale, montré avant d’être publié sauf si « Publier '
                'sans redemander » est activé ; le retirer n’est pas encore fait', ('youtube.',)),
    ('Mémoire', 'retenir ce que vous m’apprenez, le retrouver, et l’oublier sur demande', ()),
    ('Conversations passées', 'retrouver ce dont nous avons parlé', ()),
    ('Documents joints', 'lire et résumer un texte, un PDF ou un fichier Word joint ; un long document est lu en '
                         'entier, partie par partie, par l’IA locale ; un PDF fait d’images (scanné) est lu page par '
                         'page par le modèle de vision local, 25 s par page environ, et cette lecture peut contenir '
                         'des erreurs', ()),
    # 23/11/2026, PC du garage : à « Robot quadrupède », le modèle local a nié la page Projets, qu'aucune ligne ne
    # nommait. Elle existe dans toute AURORA, même sans projet : le bouton « Créer un projet » en commence un.
    ('Projets', 'la page Projets tient le dossier de vos projets de conception (cahier des charges, fichiers, '
                'vérifications avant fabrication, fiche technique) ; un projet vide s’y crée par le bouton « Créer un '
                'projet », je ne le crée pas moi-même', ()),
    ('Pages', 'faire une page lisible d’une réponse, d’un diagnostic ou d’une analyse', ()),
    ('Routines', 'lancer par son nom, ou toute seule (à une heure, ou au démarrage d’AURORA, réglé dans '
                 'Paramètres), une suite de demandes enregistrée', ()),
    # 16/11/2026 (C3, tools/reminders.py)
    ('Rappels', 'vous prévenir à une heure dite ou après une durée (« rappelle-moi à 18 h de… », « minuteur de 10 '
                'minutes », « réveille-moi à 7 h ») : le rappel s’affiche dans AURORA et est lu à voix haute si la voix '
                'est activée ; les lister et les annuler. Pas encore de rappel qui se répète', ('reminder.',)),
    ('Agent', 'enchaîner plusieurs outils, tour après tour, pour une tâche en plusieurs étapes', ()),
)


def capability_lines(tools: list[str] | None = None) -> list[str]:
    """« Titre : ce que ça fait », for each capability AURORA really has now (tools read from the registry)."""
    names = tool_names() if tools is None else tools
    return [f'{title} : {what}' for title, what, prefixes in CAPABILITY_GROUPS
            if not prefixes or any(n.startswith(prefixes) for n in names)]


def terminal_state(caps: dict) -> str:
    """The terminal with permission (B2) as the settings say now: caps['shell'] (snapshot). 08/11/2026, run 47 of
    the real AURORA: asked « as-tu le terminal ? », the local AI answered « je ne peux pas » while it was on."""
    if caps.get('shell') is True:
        return ('Le terminal avec autorisation est activé (Paramètres) : une commande Windows à la fois, montrée avec ce '
                'qu’elle fait, et lancée seulement après accord dans l’interface ; certaines commandes restent '
                'interdites même avec accord.')
    if caps.get('shell') is False:
        # The same place as the shell tool's refusal (tools/shell.py: « Activez-le dans Paramètres › Terminal »).
        return ('Le terminal avec autorisation est désactivé : aucune commande ne peut être lancée. Il s’active dans '
                'Paramètres › Terminal ; chaque commande est alors montrée et attend l’accord avant de partir.')
    return ('Un terminal avec autorisation peut être activé dans les Paramètres (désactivé par défaut) ; même activé, '
            'chaque commande est montrée et attend l’accord avant de partir.')


def pc_control_state(caps: dict) -> str:
    """The mouse and keyboard (tools/pc_control.py) as the settings say now: caps['pc_control'] (snapshot), the
    pendant of terminal_state, so that « peux-tu cliquer ? » is answered with the real setting."""
    if caps.get('pc_control') is True:
        return ('La souris et le clavier sont activés (Paramètres) : un geste à la fois (cliquer à une position, taper '
                'dans une fenêtre nommée, une touche), montré puis fait seulement après accord dans l’interface. Je ne '
                'sais pas encore cliquer sur un bouton désigné par son nom.')
    if caps.get('pc_control') is False:
        return ('La souris et le clavier sont désactivés : je ne peux pas agir sur l’écran. Ils s’activent dans '
                'Paramètres › Souris et clavier ; chaque geste est alors montré et attend l’accord.')
    return ('La souris et le clavier peuvent être activés dans les Paramètres (désactivés par défaut) ; même activés, '
            'chaque geste est montré et attend l’accord.')


def describe(caps: dict, *, engine_label: str | None = None) -> str:
    """French bullet list of the real state, for system prompts and meta.capabilities."""
    ollama = caps.get('ollama') or {}
    cloud = caps.get('cloud') or {}
    lines = []
    if caps.get('internet'):
        lines.append('- Internet : disponible, uniquement via les outils d’AURORA (recherche web SearXNG et Wikipedia, '
                     'météo Open-Meteo, séismes USGS, flux RSS du monde et scientifiques, avions et satellites). '
                     'Tu ne navigues pas toi-même : '
                     'tu utilises seulement les résultats fournis dans ce message.')
    elif caps.get('internet') is None and caps.get('mode') == 'prive':
        lines.append('- Internet : non vérifié (mode privé) ; chaque accès des outils d’AURORA demande l’accord '
                     'de l’utilisateur. Tu ne navigues pas toi-même.')
    else:
        lines.append('- Internet : indisponible en ce moment (aucune recherche en ligne possible).')
    if ollama.get('up'):
        lines.append(f"- IA locale (Ollama) : active, modèle de conversation {ollama.get('chat_model')}.")
    else:
        lines.append('- IA locale (Ollama) : ne répond pas.')
    if cloud.get('configured'):
        tiers = ', '.join(f"{name} = {info.get('model')}" for name, info in (cloud.get('tiers') or {}).items())
        lines.append(f'- Claude (Anthropic) : configuré ({tiers}) ; utilisé seulement selon le mode.')
    else:
        lines.append('- Claude (Anthropic) : non configuré (aucune clé API) : indisponible, ne propose pas de '
                     'l’utiliser.')
    labels = MODE_LABELS if cloud.get('configured') else MODE_LABELS_NO_CLOUD
    lines.append(f"- Mode : {labels.get(caps.get('mode', 'equilibre'), caps.get('mode'))}.")
    tools = tool_names()
    if tools:
        # In words, not identifiers: the local model could not tell what « data.trend » does (29/10/2026).
        lines.append('- Ce que tu sais faire, avec les outils réels d’AURORA (seulement quand AURORA les exécute ; '
                     'cite-les tous si on te demande tes capacités) :\n'
                     + '\n'.join(f'  - {line}' for line in capability_lines(tools)))
    lines.append('- Écrire : tu rédiges toi-même du texte et du code complet (un petit jeu en HTML et JavaScript, un '
                 'script…) ; ne réponds jamais que tu ne sais pas programmer. L’utilisateur copie le code dans un '
                 'fichier et l’ouvre lui-même : tu ne l’exécutes pas et ne crées aucun fichier.')
    # « seules les actions du registre » made the local AI list « registres » among its tools (B7, rapport §4.6).
    # B2 (04/11/2026): a terminal exists, but never free — off by default, one approved command at a time.
    lines.append('- Pas de terminal libre ni d’accès libre aux fichiers : uniquement les capacités listées '
                 'ci-dessus, et l’accord de l’utilisateur pour toute action sensible. ' + terminal_state(caps)
                 + ' ' + pc_control_state(caps))
    if engine_label:
        lines.append(f'- Cette réponse est produite par : {engine_label}.')
    return '\n'.join(lines)


# --------------------------------------------------------------------------- the tool map (page Tâches)

# 11/11/2026 (ID Studio IA : « une carte des outils… elle ne peut pas mentir »). The families are the lines of
# CAPABILITY_GROUPS; the terminal has no line there (terminal_state says it apart), so its family is added here.
TOOL_MAP_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = CAPABILITY_GROUPS + (
    ('Terminal', 'une commande Windows à la fois, que vous donnez mot pour mot (« exécute : git status »), montrée '
                 'avec ce qu’elle fait puis lancée seulement après votre accord ; désactivé par défaut (Paramètres), '
                 'et certaines commandes restent interdites même avec accord', ('shell.',)),
)
# Where a tool of the registry goes when no family has its prefix: shown, never dropped (tests/test_tool_map.py
# fails as long as a real tool lands here, so that a new prefix gets its family).
OTHER_FAMILY = ('autres', 'Autres outils', 'des outils du registre qui ne sont rangés dans aucune famille')
# 11/11/2026 (ID Studio IA : « Oui, regroupe ») : seventeen families for 45 tools read badly on a ring. They are gathered in
# seven large ones, in his words (« PC, web, mémoire, 3D, Studio, agir… ») : id, title, what it gathers, and the
# titles of the families above. This grouping is written by hand, the only part of the map that is ; so it is checked :
# tests/test_tool_map.py fails when a family of TOOL_MAP_GROUPS is in no large family, in two, or misspelt here.
TOOL_MAP_LARGE: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ('pc', 'Votre PC', 'ce qu’AURORA mesure et range sur cet ordinateur', ('Ordinateur', 'Fichiers')),
    ('monde', 'Web et monde', 'ce qu’elle va chercher sur Internet et ce qui se passe dans le monde',
     ('Recherche web', 'Actualités', 'Météo', 'Séismes et catastrophes', 'Ciel')),
    ('calcul', 'Calculs et données', 'des résultats calculés par des outils, pas devinés',
     ('Calculs', 'Heure et date', 'Analyse de données')),
    ('3d', '3D', 'les maquettes du laboratoire, les simulations et vos projets de conception',
     ('Laboratoire 3D', 'Simulations', 'Projets')),
    ('studio', 'Studio', 'votre activité de créateur', ('Studio', 'YouTube')),
    ('agir', 'Agir', 'ce qu’elle fait sur le PC ou enchaîne pour vous ; les gestes sensibles demandent votre accord',
     ('Souris et clavier', 'Applications', 'Terminal', 'Routines', 'Rappels', 'Agent')),
    ('memoire', 'Mémoire et écriture', 'ce qu’elle retient, relit et rédige',
     ('Mémoire', 'Conversations passées', 'Documents joints', 'Pages', 'Rédaction')),
)
# A switch of Paramètres that turns a whole action off: action of security.policy -> (key of the snapshot, the
# function that says its state in words).
_SWITCHES = {'shell.run': ('shell', terminal_state), 'pc.control': ('pc_control', pc_control_state)}
TOOL_USES_NOTE = ('Nombre de fois où une demande a exécuté l’outil, compté dans le journal (aperçus et échecs '
                  'compris). Les lectures faites par les pages (Ordinateur, Monde 3D, Studio…) ne sont pas comptées, '
                  'et vider le journal remet les compteurs à zéro.')


def _family_id(title: str, prefixes: tuple[str, ...]) -> str:
    """'system' for ('system.', 'process.') ; 'conversations-passees' for a capability built into AURORA."""
    if prefixes:
        return prefixes[0].rstrip('.')
    from .normalize import strip_accents   # lazy
    return re.sub(r'[^a-z0-9]+', '-', strip_accents(title).lower()).strip('-')


def _tool_examples(names: list[str]) -> dict[str, str]:
    """A request to try, for each tool that has one in the code: a training sentence of the router
    (data/router_examples.jsonl) of the intent tied to the tool — the one the planner answers with this tool
    (SIMPLE_TOOL_INTENTS) or that bears its name. Never a sentence made up here, and never one taken on trust:
    the first sentence is kept that AURORA's own rules send to that intent (alone: no similarity, no model) AND
    whose plan, built now, holds the tool. « Quelles sont les actualités du jour ? » is a news.latest sentence that
    the planner answers with news.world: it is not given for news.latest. None found: no example (None on the map)."""
    try:
        from . import router
        from .intents import INTENTS
        from .planner import SIMPLE_TOOL_INTENTS, Planner
        from .semantic import load_examples
        by_intent: dict[str, list[str]] = {}
        for item in load_examples():
            by_intent.setdefault(item['intent'], []).append(item['text'])
        settings = config.load_settings()
        engine, planner = router.DecisionEngine(use_semantic=False), Planner()
    except Exception:
        log.exception('Carte des outils : phrases d’exemple illisibles')
        return {}
    intent_of = {tool: intent for intent, tool in SIMPLE_TOOL_INTENTS.items()}
    found = {}
    for name in names:
        intent = intent_of.get(name) or (name if name in INTENTS else None)
        for text in by_intent.get(intent, ()):
            try:
                decision = engine.decide_fast(text)
                if (decision.intent, decision.method) != (intent, 'rule') or decision.confidence < router.RULE_CONFIDENT:
                    continue
                steps = planner.build(decision.to_dict(), {'engine': 'tool'}, text=text, settings=settings)
            except Exception:
                log.exception('Carte des outils : phrase d’exemple non vérifiée pour %s', name)
                continue
            if any(step.get('stage') == 'tool' and step.get('tool') == name for step in steps):
                found[name] = text
                break
    return found


def _tool_state(spec: Any, caps: dict, decision: Any) -> tuple[str, str | None]:
    """('available' | 'disabled' | 'absent', why in French or None) for one tool, as it is now. In this order: the
    switch of Paramètres on its action, the mode (security.policy), what the tool says it lacks (ToolSpec.missing),
    then Internet for a network tool. 'available': none of these stands in the way — the tool itself is not run."""
    switch = _SWITCHES.get(getattr(spec, 'action', ''))
    if switch and caps.get(switch[0]) is not True:
        return 'disabled', switch[1](caps)
    if not decision.allowed:
        return 'disabled', decision.reason
    check = getattr(spec, 'missing', None)
    if check is not None:
        try:
            lack = check()
        except Exception:
            log.exception('Carte des outils : état de %s illisible', getattr(spec, 'name', '?'))
            return 'absent', 'Ce qu’il lui faut n’a pas pu être vérifié : il n’est pas donné pour disponible.'
        if lack:
            return 'absent', str(lack)
    if getattr(spec, 'network', False):
        if config.offline():
            return 'absent', 'Mode hors ligne : cet outil a besoin d’Internet.'
        if caps.get('internet') is False:
            return 'absent', 'Internet ne répond pas en ce moment : cet outil en a besoin.'
        if caps.get('internet') is None:   # private mode: never probed (snapshot)
            private = caps.get('mode') == 'prive'
            return 'available', 'Internet non vérifié' + (' (mode privé) : chaque accès demande votre accord.'
                                                         if private else '.')
    return 'available', None


def tool_map(caps: dict, *, uses: dict[str, dict] | None = None, since: str | None = None,
             registry: dict | None = None) -> dict:
    """The map of the tools (page Tâches): the families, and every tool of the REGISTRY as it is at the time of
    the call — never a list of tools written by hand.

    caps: snapshot() (mode, Internet, switches of Paramètres). uses: tasks.events.tool_uses(), None when there is
    no counter (« uses » is then None, never 0). since: date of the oldest line still in the journal.
    A tool: name, title (TOOL_TITLES, else its name), family, description (its own), state and reason (_tool_state),
    needs (Internet: its « network » flag ; the user's permission: security.policy in the current mode, None when
    the mode forbids the action), example (_tool_examples, or None), uses and last_used_at.
    A family without prefix is a capability built into AURORA (memory, pages…): no tool, no state.
    groups: the seven large families of TOOL_MAP_LARGE that gather the families (each family and each tool bears the
    id of its large family in « group »).
    absent_modules: the tool modules that could not be loaded, whose tools are therefore not in the registry."""
    from ..security import policy          # lazy, like the registry
    from ..tools import base as tools_base
    if registry is None:
        registry = tools_base.REGISTRY
    try:
        from .planner import TOOL_TITLES as titles
    except Exception:
        titles = {}
    mode = caps.get('mode', 'equilibre')
    names = sorted(registry.keys())
    examples = _tool_examples(names)
    groups = [(_family_id(title, prefixes), title, what, prefixes) for title, what, prefixes in TOOL_MAP_GROUPS]
    large_of_title = {title: gid for gid, _title, _what, members in TOOL_MAP_LARGE for title in members}
    large_of = {fid: large_of_title.get(title, OTHER_FAMILY[0]) for fid, title, _what, _prefixes in groups}
    tools, count = [], {}
    for name in names:
        spec = registry[name]
        family = next((fid for fid, _title, _what, prefixes in groups if prefixes and name.startswith(prefixes)),
                      OTHER_FAMILY[0])
        count[family] = count.get(family, 0) + 1
        decision = policy.evaluate(spec.action, mode=mode)
        state, reason = _tool_state(spec, caps, decision)
        used = (uses or {}).get(name) or {}
        tools.append({
            'name': name, 'title': titles.get(name) or name, 'family': family,
            'group': large_of.get(family, OTHER_FAMILY[0]), 'description': spec.description,
            'state': state, 'reason': reason,
            'needs': {'internet': bool(spec.network),
                      'permission': bool(decision.requires_permission) if decision.allowed else None,
                      'permission_reason': decision.reason, 'risk': decision.risk},
            'example': examples.get(name),
            'uses': None if uses is None else int(used.get('uses', 0)),
            'last_used_at': used.get('last_used_at')})
    families = [{'id': fid, 'title': title, 'group': large_of[fid], 'description': what, 'builtin': not prefixes,
                 'tools': count.get(fid, 0)}
                for fid, title, what, prefixes in groups if not prefixes or count.get(fid)]
    if count.get(OTHER_FAMILY[0]):
        families.append({'id': OTHER_FAMILY[0], 'title': OTHER_FAMILY[1], 'group': OTHER_FAMILY[0],
                         'description': OTHER_FAMILY[2], 'builtin': False, 'tools': count[OTHER_FAMILY[0]]})
    # The large families, in the order of TOOL_MAP_LARGE ; one without any listed family is not announced, and a
    # family that none of them names (a new one, not yet grouped) is shown apart, never dropped.
    large = [{'id': gid, 'title': title, 'description': what, 'families': [f['id'] for f in families if f['group'] == gid],
              'tools': sum(f['tools'] for f in families if f['group'] == gid)}
             for gid, title, what, _members in TOOL_MAP_LARGE + ((OTHER_FAMILY[0], OTHER_FAMILY[1], OTHER_FAMILY[2], ()),)]
    large = [g for g in large if g['families']]
    absent = [{'module': module, 'reason': f'Le module d’outils « {module} » n’a pas pu être chargé ({error}).'}
              for module, error in sorted(tools_base.LOAD_ERRORS.items())]
    return {'checked_at': caps.get('checked_at'), 'mode': mode, 'internet': caps.get('internet'),
            'groups': large, 'families': families, 'tools': tools, 'absent_modules': absent,
            'usage': None if uses is None else {'source': 'journal', 'since': since, 'note': TOOL_USES_NOTE}}
