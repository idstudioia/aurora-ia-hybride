"""Decision (+ secondary intents + slots) -> ordered list of steps.

A step is a dict {title, stage, tool, params, requires_permission, action, ...}:
  stage 'tool'      tool from the registry, params validated by the tool itself
  stage 'local_ai'  / 'cloud_ai'   LLM step, `purpose` selects the prompt
  stage 'memory'    memory.search / memory.save (op: search | save | summary)
  stage 'response'  deterministic composition / final answer (tool 'compose')
  stage 'verify'    verification checks (tool 'verify')
`derive` names a resolver applied at run time from earlier step results
(e.g. the web query built from the diagnosis' main cause).
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import factcheck

# Tools a task.plan may use: read-only ones run directly, sensitive ones need permission.
PLAN_READ_TOOLS = (
    'system.snapshot', 'system.history', 'system.processes', 'system.diagnose', 'calc.evaluate',
    'calc.convert', 'time.now', 'weather.current', 'weather.cities', 'web.search', 'news.latest',
    'quakes.recent', 'sim.orbit', 'sim.solar_system', 'files.scan',
)
PLAN_SENSITIVE_TOOLS = ('files.quarantine', 'process.terminate')
DEFAULT_ACTIONS = {
    'system.snapshot': 'system.read', 'system.history': 'system.read', 'system.processes': 'system.read',
    'system.diagnose': 'system.read', 'calc.evaluate': 'system.read', 'calc.convert': 'system.read',
    'time.now': 'system.read', 'sim.orbit': 'system.read', 'sim.solar_system': 'system.read',
    'weather.current': 'web.fetch', 'weather.cities': 'web.fetch', 'web.search': 'web.fetch',
    'news.latest': 'web.fetch', 'news.world': 'web.fetch', 'quakes.recent': 'web.fetch', 'files.scan': 'files.scan',
    'files.quarantine': 'files.quarantine', 'files.restore': 'files.quarantine',
    'process.terminate': 'process.terminate', 'shell.run': 'shell.run',
    # P3: reading a table (joined, or in an allowed folder) is a read-only file access, like files.scan
    'data.describe': 'files.scan', 'data.group': 'files.scan', 'data.trend': 'files.scan',
    'data.outliers': 'files.scan', 'data.compare': 'files.scan',
    'lab.catalogue': 'system.read', 'lab.scene': 'system.read',   # 08/11/2026: 3D lab, read from data/scenes
    'pc.gesture': 'pc.control', 'pc.pointer': 'system.read',      # 9/11/2026: souris et clavier
    'app.launch': 'app.launch',                                   # 9/11/2026: applications autorisées
    'youtube.comment': 'youtube.comment',                         # 9/11/2026: commentaire YouTube
    'calc.projet': 'system.read',                                 # 13/11/2026: les calculs d'un projet, lus et calculés ici
    'reminder.set': 'reminder.set',                               # 16/11/2026: rappels, minuteurs, réveils
}
TOOL_TITLES = {
    'system.snapshot': 'Mesure système', 'system.history': 'Historique système',
    'system.processes': 'Liste des processus', 'system.diagnose': 'Diagnostic par règles',
    'calc.evaluate': 'Calcul', 'calc.convert': 'Conversion d’unités', 'time.now': 'Date et heure',
    'weather.current': 'Météo (Open-Meteo)', 'weather.cities': 'Météo des villes',
    'web.search': 'Recherche web (SearXNG, Wikipedia)', 'news.latest': 'Actualités (RSS)',
    'news.world': 'Actualités du monde (27 flux RSS)',
    'quakes.recent': 'Séismes récents (USGS)', 'sim.orbit': 'Simulation orbitale',
    'sim.solar_system': 'Positions des planètes', 'files.scan': 'Analyse des fichiers (lecture seule)',
    'files.quarantine': 'Mise en quarantaine réversible', 'process.terminate': 'Arrêt du processus',
    'shell.run': 'Commande de terminal',
    'data.describe': 'Lecture du tableau', 'data.group': 'Regroupement', 'data.trend': 'Évolution dans le temps',
    'data.outliers': 'Valeurs anormales', 'data.compare': 'Comparaison de deux périodes',
    'lab.catalogue': 'Catalogue des scènes 3D', 'lab.scene': 'Description de la scène 3D',
    'pc.gesture': 'Geste de la souris ou du clavier', 'pc.pointer': 'Position de la souris',
    'app.launch': 'Ouverture d’une application autorisée',
    'youtube.comment': 'Publication du commentaire YouTube',
    'calc.projet': 'Calculs du projet pour ce cahier des charges',
    'reminder.set': 'Rappel ou minuteur',
}
# The parameters of one gesture that come from the person's words (normalize.extract_gesture) — never the model's.
GESTURE_KEYS = ('gesture', 'x', 'y', 'text', 'keys', 'direction', 'amount', 'window')
AI_TITLES = {
    'chat': 'Réponse IA', 'diagnose': 'Interprétation du diagnostic', 'web': 'Synthèse des sources',
    'fact': 'Réponse appuyée sur la source',
    'optimize': 'Synthèse des solutions (actions proposées)', 'doc': 'Analyse du document',
    'plan': 'Plan d’étapes', 'agent': 'Agent', 'scene': 'Construction de la scène 3D',
    'doc_part': 'Lecture du document',
}
# 16/11/2026 (suite of E4): a joined document longer than what the local model reads at once is read whole, part
# after part (tools/documents.parts), each part by its own local step; at most DOC_PARTS_MAX parts for a request.
DOC_PART_CHARS, DOC_PARTS_MAX = 12000, 12
# Single-tool intents answered deterministically (also usable as secondary intents).
SIMPLE_TOOL_INTENTS = {
    'system.status': 'system.snapshot', 'system.processes': 'system.processes',
    'weather.current': 'weather.current', 'quakes.recent': 'quakes.recent',
    'calc.evaluate': 'calc.evaluate', 'calc.convert': 'calc.convert', 'time.now': 'time.now',
    'sim.orbit': 'sim.orbit', 'sim.solar_system': 'sim.solar_system', 'files.scan': 'files.scan',
    'news.latest': 'news.latest',
    'reminder.set': 'reminder.set',   # 16/11/2026: the moment is read by the code (tasks/reminders.understand)
}
OPTIMIZE_WORDS = re.compile(r'optimis|solution|corrig|amélior|ameliorer|répar|repar|accélér|acceler', re.I)
# 21/11/2026: a request of the agent that asks to keep its result — « … puis enregistre ce résumé dans ta mémoire »,
# « garde-le en mémoire », « retiens ce bilan ». « Retrouve dans ta mémoire… » is not one: no verb of keeping.
# 22/11/2026: the part of a request of the agent that asks the memory for something — « retrouve dans ta mémoire
# les décisions du projet Orion ». « La mémoire vive de mon PC » is a measure, « enregistre-le dans ta mémoire » a
# keeping: neither is a search.
_MEMORY_PART = re.compile(r'[^.;:!?\n]*\b(?:m[ée]moire|souvenirs?)\b[^.;:!?\n]*', re.I)
_MEMORY_SEARCHED = re.compile(
    r'\b(?:retrouv\w*|cherch\w*|recherch\w*|rappel\w*|consult\w*|regard\w*|fouill\w*)\b[^.;:!?\n]{0,60}'
    r'\b(?:ta|tes|ma|mes|la|notre|nos)\s+(?:m[ée]moire(?!\s+(?:vive|vid[ée]o|graphique))|souvenirs?)\b', re.I)


def memory_clause(text: str) -> str:
    """The words of an agent request that ask AURORA's memory, '' when it asks nothing of it."""
    for part in _MEMORY_PART.finditer(text or ''):
        clause = re.split(r',?\s+puis\b', part.group(0), maxsplit=1)[0].strip()
        if _MEMORY_SEARCHED.search(clause) and not KEEP_RESULT.search(clause):
            return clause
    return ''


_KEPT = r'(?:r[ée]sum[ée]|synth[èe]se|bilan|compte rendu)'
KEEP_RESULT = re.compile(
    r'\b(?:enregistr\w+|m[ée]moris\w+|sauvegard\w+|retiens|retenir|gardes?|garder)\b[^.?!\n]{0,80}'
    r'\b(?:m[ée]moire|souvenirs?)\b'                                                    # « garde-le en mémoire »
    rf'|\b(?:enregistr\w+|retiens|retenir|m[ée]moris\w+)\b[^.?!\n]{{0,30}}\b{_KEPT}\b'   # « enregistre ce résumé »
    rf'|\b{_KEPT}\b[^.?!\n]{{0,80}}\bl[’\']enregistr\w+',                                # « un résumé, … avant de l'enregistrer »
    re.I)


def ai_stage(engine: str) -> str | None:
    if engine in ('cloud', 'tool+cloud'):
        return 'cloud_ai'
    if engine in ('local', 'tool+local'):
        return 'local_ai'
    return None


def tool_params(tool: str, slots: dict, *, text: str, settings: dict) -> dict:
    """Map router slots to tool parameters (names from the contract slot table)."""
    def pick(*names: str) -> dict:
        return {n: slots[n] for n in names if slots.get(n) not in (None, '')}

    if tool == 'weather.current':
        params = {'city': slots.get('city') or settings.get('default_city') or 'Paris'}
        if slots.get('days'):
            params['days'] = list(slots['days'])   # forecast horizon (« demain », « ce week-end »)
        return params
    if tool == 'time.now':
        return pick('place', 'tz')
    if tool == 'reminder.set':
        return pick('due', 'text', 'kind', 'why', 'show', 'cancel', 'every')
    if tool == 'web.search':
        return {'query': str(slots.get('query') or text)[:300]}
    if tool == 'news.latest':
        return pick('topic')
    if tool == 'news.world':   # 08/11/2026: « les actualités en Ukraine » (normalize.extract_world_news)
        world = slots.get('monde') if isinstance(slots.get('monde'), dict) else {}
        return {**{k: world[k] for k in ('pays', 'theme') if world.get(k)}, 'limit': 8}
    if tool == 'calc.evaluate':
        return {'expression': str(slots.get('expression') or text)[:500]}
    if tool == 'calc.convert':
        return pick('value', 'from_unit', 'to_unit')
    if tool == 'sim.orbit':
        return pick('body', 'altitude_km', 'eccentricity', 'hours')
    if tool == 'sim.solar_system':
        return pick('date')
    if tool in ('files.scan', 'files.quarantine'):
        return pick('folder', 'older_than_days', 'min_size_mb', 'size_default')
    if tool == 'system.processes':
        return {'limit': 10, 'sort': slots.get('sort') if slots.get('sort') in ('cpu', 'memory') else 'cpu'}
    if tool == 'system.snapshot':
        return pick('metric')
    return {}


class Planner:
    """Builds plans. `action_of(tool)` and `needs_permission(action)` come from the
    tool registry and security.policy (injected so the planner stays pure)."""

    def __init__(self, *, action_of: Callable[[str], str] | None = None,
                 needs_permission: Callable[[str], bool] | None = None) -> None:
        self.action_of = action_of or (lambda tool: DEFAULT_ACTIONS.get(tool, 'system.read'))
        self.needs_permission = needs_permission or (lambda action: action in (
            'files.quarantine', 'process.terminate', 'cloud.send'))

    # --- step factories ----------------------------------------------------------

    def tool(self, tool: str, params: dict | None = None, *, title: str | None = None,
             derive: str | None = None, force_permission: bool = False, action: str | None = None) -> dict:
        action = action or self.action_of(tool)
        step = {'title': title or TOOL_TITLES.get(tool, tool), 'stage': 'tool', 'tool': tool,
                'params': params or {}, 'action': action,
                'requires_permission': bool(force_permission or self.needs_permission(action))}
        if derive:
            step['derive'] = derive
        return step

    def kill_steps(self, target: dict, *, title: str | None = None) -> list[dict]:
        """Dry run first (read-only: resolves targets, refuses protected processes), then the
        real stop behind a process.terminate permission whose preview is the dry-run result."""
        return [
            self.tool('process.terminate', {**target, 'dry_run': True}, title='Recherche du processus',
                      action='system.read'),
            self.tool('process.terminate', dict(target), title=title, derive='targets_from_dry_run',
                      force_permission=True),
        ]

    def shell_steps(self, command: str, cwd: str | None = None, *, origin: str = 'user',
                    title: str | None = None) -> list[dict]:
        """B2 (04/11/2026): a read-only preview (dry run: command, folder, risk, origin — built by the code),
        then the real run behind a shell.run permission whose card is that preview. The executed command is
        bound to the one shown (derive 'shell_from_dry_run' + the tool's approved_command check)."""
        base = {'command': command, 'origin': origin}
        if cwd:
            base['cwd'] = cwd
        return [
            self.tool('shell.run', {**base, 'dry_run': True}, title='Préparation de la commande',
                      action='system.read'),
            self.tool('shell.run', dict(base), title=title or 'Exécution de la commande',
                      derive='shell_from_dry_run', force_permission=True),
        ]

    def gesture_steps(self, gesture: dict, *, origin: str = 'user', title: str | None = None) -> list[dict]:
        """9/11/2026 (souris et clavier), like shell_steps: a read-only preview (dry run: the gesture, the window,
        the position, by the code), then the real gesture behind a pc.control permission whose card is that
        preview. The gesture done is bound to the one shown (derive 'gesture_from_dry_run' + its fingerprint)."""
        base = {k: gesture[k] for k in GESTURE_KEYS if gesture.get(k) not in (None, '')}
        base['origin'] = origin
        return [
            self.tool('pc.gesture', {**base, 'dry_run': True}, title='Préparation du geste', action='system.read'),
            self.tool('pc.gesture', dict(base), title=title or 'Geste de la souris ou du clavier',
                      derive='gesture_from_dry_run', force_permission=True),
        ]

    def comment_steps(self, text: str | None, request: str, *, origin: str = 'user') -> list[dict]:
        """9/11/2026 (commentaire YouTube), like gesture_steps: a preview (dry run: the newest video of the connected
        channel and the text, given or written by the local model), then the real post behind a youtube.comment
        permission whose card is that preview (derive 'comment_from_dry_run' + its fingerprint). « Publier sans
        redemander » (Paramètres) grants that card at once; it is still made and recorded."""
        base = {'request': request[:500], 'origin': origin}
        if text:
            base['text'] = text
        return [
            self.tool('youtube.comment', {**base, 'dry_run': True}, title='Préparation du commentaire YouTube',
                      action='web.fetch'),
            self.tool('youtube.comment', dict(base), title='Publication du commentaire YouTube',
                      derive='comment_from_dry_run', force_permission=True),
        ]

    def ai(self, purpose: str, choice: dict) -> dict | None:
        stage = ai_stage(choice.get('engine', ''))
        if stage is None:
            return None
        cloud = stage == 'cloud_ai'
        title = AI_TITLES.get(purpose, 'Réponse IA') + (' — Claude' if cloud else ' — IA locale')
        return {'title': title, 'stage': stage, 'tool': 'llm', 'params': {}, 'purpose': purpose,
                'action': 'cloud.send' if cloud else 'system.read',
                'requires_permission': bool(cloud and choice.get('needs_consent'))}

    def reading_steps(self, attachments: list[dict]) -> list[dict]:
        """One local step per part of each long document, in the order of the documents: the model notes what, in
        this part, serves the request; the analysis step then answers from the notes of all the parts. Always the
        local model: the document stays on the PC. A document whose parts no longer fit in DOC_PARTS_MAX keeps
        the reading of before (its beginning, said by the answer)."""
        from ..tools import documents   # lazy: tools are owned by another module
        steps: list[dict] = []
        for rank, doc in enumerate(attachments):
            text = str(doc.get('text') or '')
            if len(text) <= DOC_PART_CHARS:
                continue
            count = len(documents.parts(text, DOC_PART_CHARS))
            if len(steps) + count > DOC_PARTS_MAX:
                continue
            name = str(doc.get('name') or 'document')[:60]
            for part in range(1, count + 1):
                step = self.ai('doc_part', {'engine': 'local'})
                step['title'] = f'Lecture de « {name} » : partie {part} sur {count} — IA locale'
                step['params'] = {'doc': rank, 'part': part, 'parts': count}
                steps.append(step)
        return steps

    def agent_turn(self, choice: dict, turn: int, **params: Any) -> dict | None:
        """P1: one turn of the agent (core/agent.py), where the model chooses ONE next action or answers.
        Same stage, consent and engine as any AI step of the plan."""
        step = self.ai('agent', choice)
        if step is None:
            return None
        engine = ' — Claude' if step['stage'] == 'cloud_ai' else ' — IA locale'
        step['title'] = f"Agent · tour {int(turn)}{' (correction)' if params.get('correction') else ''}{engine}"
        step['params'] = {'turn': int(turn), **params}
        return step

    def data_steps(self, source: str) -> list[dict]:
        """P3: the reading of the table, then the base set of tools/data.standard_calls (tagged turn 0 for the
        agent). An unreadable table keeps only the reading step: its error is the answer."""
        from ..tools import data   # lazy: tools are owned by another module
        steps = [self.tool('data.describe', {'source': source})]
        try:
            calls = data.standard_calls(data.load(source))
        except Exception:   # unreadable (ToolError, a file locked…): the reading step says why
            calls = []
        steps += [self.tool(name, {**params, 'source': source}) for name, params in calls]
        for step in steps:
            step['agent_turn'] = 0
        return steps

    def memory(self, op: str, params: dict) -> dict:
        if op == 'search':
            return {'title': 'Recherche en mémoire', 'stage': 'memory', 'tool': 'memory.search',
                    'params': params, 'op': op, 'action': 'system.read', 'requires_permission': False}
        title = 'Mémorisation du résumé' if op == 'summary' else 'Mémorisation'
        return {'title': title, 'stage': 'memory', 'tool': 'memory.save', 'params': params, 'op': op,
                'action': 'memory.write', 'requires_permission': bool(self.needs_permission('memory.write'))}

    @staticmethod
    def respond(intent: str, secondary: list[str] | None = None, unhandled: list[str] | None = None) -> dict:
        """unhandled: secondary intents this plan does not perform (said so in the answer)."""
        params = {'intent': intent, 'secondary': secondary or []}
        if unhandled:
            params['unhandled'] = list(unhandled)
        return {'title': 'Composition de la réponse', 'stage': 'response', 'tool': 'compose',
                'params': params, 'action': '', 'requires_permission': False}

    @staticmethod
    def check() -> dict:
        return {'title': 'Vérification', 'stage': 'verify', 'tool': 'verify', 'params': {}, 'action': '',
                'requires_permission': False}

    # --- plans -------------------------------------------------------------------

    def build(self, decision: dict, choice: dict, *, text: str, settings: dict,
              attachments: list[dict] | None = None, run_id: int | None = None) -> list[dict]:
        intent = decision.get('intent') or 'chat.general'
        slots = dict(decision.get('slots') or {})
        secondary = [s for s in (decision.get('secondary_intents') or []) if s and s != intent]
        if intent == 'agent.run':
            secondary = []   # the agent chooses every tool of the request itself, turn after turn
        steps: list[dict] = []
        tail: list[dict] = []
        handled_secondary: list[str] = []

        def add_ai(purpose: str) -> None:
            step = self.ai(purpose, choice)
            if step:
                steps.append(step)

        if intent == 'system.diagnose':
            steps += [self.tool('system.snapshot'), self.tool('system.processes', {'limit': 10, 'sort': 'cpu'}),
                      self.tool('system.diagnose')]
            composed = 'web.search' in secondary or ('chat.general' in secondary and OPTIMIZE_WORDS.search(text))
            if composed:
                handled_secondary.append('web.search')
                steps.append(self.tool('web.search', {'query': ''}, title='Recherche de solutions (web)',
                                       derive='query_from_diagnosis'))
                add_ai('optimize')
                tail.append(self.memory('summary', {'category': 'diagnostic', 'kind': 'summary',
                                                    'source_ref': str(run_id or '')}))
            else:
                add_ai('diagnose')
        elif intent == 'calc.evaluate' and slots.get('cahier'):
            # 13/11/2026, « les calculs en premier »: a requirement said in one sentence (« conçois-moi un robot
            # quadrupède de cinq kilos… »). Its numbers, read by projets.demande_de_calcul, go to the project's own
            # program. No autonomy said: no step, the answer asks for it.
            if slots.get('projet') and slots.get('masse_kg') and slots.get('autonomie_min'):
                steps.append(self.tool('calc.projet', {
                    'projet': str(slots['projet']), 'masse_kg': float(slots['masse_kg']),
                    'autonomie_min': float(slots['autonomie_min']), 'articulations': int(slots.get('articulations') or 12)}))
        elif intent in SIMPLE_TOOL_INTENTS:
            tool = SIMPLE_TOOL_INTENTS[intent]
            if intent == 'news.latest' and isinstance(slots.get('monde'), dict):
                tool = 'news.world'   # the news of the world, placed on the globe (not the science feeds)
            steps.append(self.tool(tool, tool_params(tool, slots, text=text, settings=settings)))
        elif intent == 'web.search' and slots.get('clarify'):
            pass   # subject too vague (core/vague.py): the answer asks for a precision, nothing is searched
        elif intent == 'web.search':
            steps.append(self.tool('web.search', tool_params('web.search', slots, text=text, settings=settings)))
            add_ai('web')
        elif intent == 'files.cleanup':
            params = tool_params('files.scan', slots, text=text, settings=settings)
            steps.append(self.tool('files.scan', params, title='Recherche des fichiers à nettoyer'))
            steps.append(self.tool('files.quarantine', {'run_id': run_id}, derive='paths_from_scan',
                                   force_permission=True))
        elif intent == 'files.restore':
            # B4: the most recent quarantine still holding files, put back after the user's permission (the same
            # tool as the « Restaurer » button). None: no step, the answer says there is nothing to restore.
            from ..tools.files import latest_quarantine   # lazy: tools are owned by another module
            found = latest_quarantine()
            if found:
                steps.append(self.tool('files.restore', {'run_id': found}, force_permission=True,
                                       title='Restauration des fichiers mis en quarantaine'))
        elif intent == 'system.kill_process':
            steps += self.kill_steps({k: slots[k] for k in ('name', 'pid') if slots.get(k) not in (None, '')})
        elif intent == 'shell.run':
            # B2: the literal command (and optional folder) come from the user's text, not the model.
            # No command found: no step, the answer says the exact syntax (orchestrator / compose.shell_run).
            if slots.get('command'):
                steps += self.shell_steps(str(slots['command']), slots.get('cwd'), origin='user')
        elif intent == 'pc.control':
            # 9/11/2026: one gesture read from the person's words. Not understood, or a click on a NAMED thing
            # (« clique sur Enregistrer », 'target'): no step here, the answer says what to write.
            gesture = slots.get('gesture') if isinstance(slots.get('gesture'), dict) else None
            if gesture and gesture.get('gesture') == 'position':
                steps.append(self.tool('pc.pointer'))
            elif gesture and gesture.get('gesture') and not gesture.get('target'):
                steps += self.gesture_steps(gesture, origin='user')
        elif intent == 'app.launch':
            # 9/11/2026: the name asked; tools/apps.py opens it only if it is in the allowed list (Paramètres).
            # No name: no step, the answer lists the allowed apps.
            if slots.get('app'):
                # 13/11/2026: « ouvre le tibia dans Creality Print »: the id of the model goes with the name.
                # The same day, for FreeCAD and KiCad: « ouvre le tibia du quadrupède dans FreeCAD », a project file.
                handed = {'model': str(slots['model'])} if slots.get('model') else {}
                if slots.get('projet') and slots.get('fichier'):
                    handed = {'projet': str(slots['projet']), 'fichier': str(slots['fichier'])}
                with_what = ', avec un fichier de projet' if 'projet' in handed else ', avec un modèle 3D' if handed else ''
                steps.append(self.tool('app.launch', {'name': str(slots['app']), **handed},
                                       title=f"Ouverture de « {slots['app']} »{with_what}"))
        elif intent == 'youtube.comment':
            # 9/11/2026: under the newest video only. Another video named, a withdrawal (not done yet), or the Privé
            # mode: no step (nothing read on the Internet), the answer says why (compose.youtube_comment).
            if slots.get('video') == 'latest' and not slots.get('remove') and settings.get('mode') != 'prive':
                steps += self.comment_steps(slots.get('text'), text, origin='user')
        elif intent == 'memory.search':
            # query '' = the request only designates the user (« que sais-tu de moi ? »): list memories.
            query = slots.get('query')
            # 'covering' (22/11/2026): the date of something kept — only the memories holding the whole subject answer
            steps.append(self.memory('search', {'query': str(text if query is None else query)[:300],
                                                **({'covering': True} if slots.get('covering') else {})}))
        elif intent == 'memory.save':
            steps.append(self.memory('save', {'content': str(slots.get('content') or text)[:4000],
                                              'category': slots.get('category') or 'general',
                                              'kind': slots.get('kind')}))
        elif intent == 'doc.analyze':
            if attachments:
                # 16/11/2026: when the local model answers, a long document is first read whole, part after part.
                # With Claude, nothing changes here: no more of the document leaves the PC than before.
                if ai_stage(choice.get('engine', '')) == 'local_ai':
                    steps += self.reading_steps(attachments)
                add_ai('doc')
        elif intent == 'task.plan':
            add_ai('plan')
        elif intent == 'agent.run':   # P1: the first turn; each turn inserts the next one (core/agent.py)
            turn = self.agent_turn(choice, 1)
            if turn:
                asked = memory_clause(text)
                if asked:
                    # 22/11/2026: « retrouve dans ta mémoire les décisions du projet Orion… ». Sur cinq tirages
                    # réels, le modèle local oubliait cette recherche une fois et n'en citait pas le résultat une
                    # autre. AURORA la fait donc elle-même avant le premier tour, qui en lit le résultat.
                    found = self.memory('search', {'query': asked[:300]})
                    found['agent_turn'] = 0
                    steps.append(found)
                steps.append(turn)
                if KEEP_RESULT.search(text):
                    # 21/11/2026 (mission du concours : « prépare un résumé, puis demande mon autorisation avant
                    # de l'enregistrer ») : the request itself asks to keep the result. AURORA, not the model,
                    # proposes it once the answer is given and checked, and it always waits for the agreement.
                    keep = self.memory('summary', {'category': 'agent', 'kind': 'summary',
                                                   'source_ref': str(run_id or '')})
                    keep['requires_permission'] = True
                    tail.append(keep)
        elif intent == 'data.analyze':
            # P3: the table read, the base calculations chosen by the code from its columns (turn 0), then the
            # agent restricted to the analysis tools, which may add 1 to 3 targeted ones before answering.
            # No table found: no step, the answer says why (orchestrator, slots['source_error']).
            if slots.get('source'):
                steps += self.data_steps(str(slots['source']))
                turn = self.agent_turn(choice, 1, toolset='data')
                if turn:
                    steps.append(turn)
        elif intent == 'ui.navigate' and isinstance(slots.get('scene'), dict) and slots['scene'].get('scene'):
            # 08/11/2026 (laboratoire 3D) : the answer is read from the validated description, a real step
            steps.append(self.tool('lab.scene', {'id': str(slots['scene']['scene'])}))
        elif intent == 'ui.navigate' and 'sujet_3d' in slots:
            steps.append(self.tool('lab.catalogue', {}))   # no scene yet: the answer cites the scenes that exist
            if str(slots.get('sujet_3d') or '').strip():
                # 12/11/2026 (ID Studio IA : « vérifier la forme avec des images internet ») : when Claude writes the maquette,
                # photos of the object are looked for first (the local SearXNG) ; they go with the request, shown in
                # its preview. The local model gets none: on the bench it placed the pieces no better with them.
                if ai_stage(choice.get('engine', '')) == 'cloud_ai':
                    from ..tools import lab   # lazy: tools are owned by another module
                    steps.append(self.tool('web.reference_images',
                                           {'sujet': lab.subject_name(slots.get('sujet_3d'))[:160]},
                                           title='Recherche de photos de référence'))
                # Phase 4 (08/11/2026): the model describes the object, the code repairs, checks and keeps it
                # (tools/lab.build_generated). No AI available: no step, the answer says why.
                add_ai('scene')
        elif intent in ('ui.navigate', 'meta.capabilities', 'routine.run'):   # routine: steps run by the interface
            pass
        else:  # chat.general and any unknown intent
            # Check before answering (core/factcheck.py, Athéna's verificationService): a factual question
            # gets its source first, through the web search (SearXNG first). Not where every Internet access
            # asks for consent (private mode): there the model answers alone, and the origin badge says so.
            web_needs_consent = self.needs_permission(self.action_of('web.search'))
            subject = factcheck.subject_to_verify(text) \
                if intent == 'chat.general' and not web_needs_consent else None
            if subject:
                steps.append(self.tool('web.search', {'query': subject[:300], 'limit': 3},
                                       title='Vérification de la source (web)'))
                add_ai('fact')
            else:
                add_ai('chat')

        # Secondary measure/lookup intents answered by one tool each (e.g. "météo et heure"), plus
        # the local memory and encyclopaedic lookups. Actions with their own permission flow
        # (cleanup, stopping a program…) are never chained: the answer says they were not done.
        unhandled: list[str] = []
        query_owner = intent in ('web.search', 'memory.search')   # slots['query'] is the main one's
        for extra in secondary:
            if extra in handled_secondary or extra == intent:
                continue
            if extra in SIMPLE_TOOL_INTENTS or (extra == 'web.search' and not query_owner):
                tool = SIMPLE_TOOL_INTENTS.get(extra, extra)
                if any(s.get('tool') == tool for s in steps):
                    continue   # already covered by the main plan
                steps.insert(self._first_non_tool(steps),
                             self.tool(tool, tool_params(tool, slots, text=text, settings=settings)))
                handled_secondary.append(extra)
            elif extra == 'memory.save' and str(slots.get('content') or '').strip() \
                    and not any(s.get('tool') == 'memory.save' for s in steps):
                steps.append(self.memory('save', {'content': str(slots['content'])[:4000],
                                                  'category': slots.get('category') or 'general',
                                                  'kind': slots.get('kind')}))
                handled_secondary.append(extra)
            elif extra == 'memory.search' and not query_owner and str(slots.get('query') or '').strip() \
                    and not any(s.get('tool') == 'memory.search' for s in steps):
                steps.append(self.memory('search', {'query': str(slots['query'])[:300]}))
                handled_secondary.append(extra)
            else:
                unhandled.append(extra)

        steps.append(self.respond(intent, handled_secondary, unhandled))
        steps.append(self.check())
        steps += tail
        return steps

    @staticmethod
    def _first_non_tool(steps: list[dict]) -> int:
        for i, step in enumerate(steps):
            if step['stage'] != 'tool':
                return i
        return len(steps)

    # --- task.plan: LLM JSON -> validated tool steps -------------------------------

    def expand_llm_plan(self, raw_text: str, *, registry: dict | None, slots: dict, text: str,
                        settings: dict, run_id: int | None) -> tuple[dict | None, list[dict], list[str]]:
        """Parse + validate the LLM plan. Returns (plan, executable steps, notes)."""
        notes: list[str] = []
        data = extract_json(raw_text)
        if data is None:
            return None, [], ['Le modèle n’a pas produit de plan JSON valide.']
        try:
            plan = LLMPlan.model_validate(data)
        except ValidationError:
            return None, [], ['Le plan produit par le modèle ne respecte pas le format attendu.']
        steps: list[dict] = []
        has_scan = False
        for item in plan.steps:
            if not item.tool:
                continue
            new, note = self.llm_tool_steps(item.tool, item.params, title=item.title, registry=registry,
                                            run_id=run_id, has_scan=has_scan)
            if note:
                notes.append(note)
                item.tool = None
                continue
            has_scan = has_scan or item.tool == 'files.scan'
            steps += new
        return plan.model_dump(), steps, notes

    def llm_tool_steps(self, tool: str, params: dict, *, title: str, registry: dict | None, run_id: int | None,
                       has_scan: bool, extra_allowed: tuple[str, ...] = (), shell: bool = False
                       ) -> tuple[list[dict], str | None]:
        """One tool chosen by a model (task.plan, or the agent of core/agent.py) -> the steps that run it,
        or ([], why it is refused). The same guards for both: allowed list, params validated by the tool.
        ``extra_allowed``: the analysis tools of a P3 run (their table is set by AURORA, not by the model).
        ``shell``: allow shell.run (agent only, and only when the terminal is enabled — B2, 04/11/2026)."""
        allowed = set(PLAN_READ_TOOLS) | set(PLAN_SENSITIVE_TOOLS) | set(extra_allowed)
        if shell:
            allowed.add('shell.run')
        title = str(title or TOOL_TITLES.get(tool, tool))[:120]
        if tool not in allowed or (registry is not None and tool not in registry):
            return [], f'Étape « {title} » ignorée : outil « {tool} » non autorisé.'
        params = dict(params or {})
        if registry is not None:
            model = getattr(registry[tool], 'params', None)
            if model is not None and hasattr(model, 'model_validate'):
                try:
                    params = model.model_validate(params).model_dump(exclude_none=True)
                except ValidationError:
                    return [], f'Étape « {title} » ignorée : paramètres invalides.'
        if tool == 'files.quarantine':
            if not has_scan:
                return [], f'Étape « {title} » ignorée : la quarantaine exige une analyse préalable.'
            return [self.tool('files.quarantine', {'run_id': run_id}, title=title, derive='paths_from_scan',
                              force_permission=True)], None
        if tool == 'process.terminate':
            return self.kill_steps({k: v for k, v in params.items() if k in ('name', 'pid')}, title=title), None
        if tool == 'shell.run':
            # B2: the agent proposed a command. Same card and guards as a user command; the origin is marked
            # 'agent' (the model may have read external content) and the card warns it comes from the model.
            command = str(params.get('command') or '').strip()
            if not command:
                return [], f'Étape « {title} » ignorée : commande manquante.'
            try:
                from ..tools.shell import blacklisted   # lazy: tools are owned by another module
                blocked = blacklisted(command)
            except Exception:
                blocked = ''
            if blocked:
                return [], f'Étape « {title} » refusée : {blocked} (interdit même avec autorisation).'
            steps = self.shell_steps(command, params.get('cwd'), origin='agent', title=title)
            steps[-1]['from_llm_plan'] = True   # the card warns: command proposed by the model
            return steps, None
        # A model output never reaches the Internet by itself: every network step it chooses asks the user
        # (exact request in the preview), whatever the mode; a request carrying personal or secret data
        # (memory, documents) is not even proposed.
        network = self.action_of(tool) == 'web.fetch'
        if network and _leaks(params):
            return [], (f'Étape « {title} » ignorée : la requête Internet contiendrait des données personnelles '
                        'ou secrètes.')
        step = self.tool(tool, params, title=title, force_permission=network)
        if network:
            step['from_llm_plan'] = True
        return [step], None


def _leaks(params: dict) -> bool:
    """True if a parameter value holds personal / secret data (fail closed if redaction is unavailable)."""
    try:
        from ..security.redaction import redact, sensitivity_of
    except Exception:
        return True
    for value in params.values():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        if redact(text)[1] or sensitivity_of(text) == 'sensitive':
            return True
    return False


class LLMPlanStep(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    tool: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    note: str = Field(default='', max_length=500)

    @field_validator('tool', mode='before')
    @classmethod
    def _blank_tool(cls, value: Any) -> Any:
        if value in ('', 'null', 'none', 'None'):
            return None
        return value


class LLMPlan(BaseModel):
    goal: str = Field(default='', max_length=500)
    steps: list[LLMPlanStep] = Field(min_length=1, max_length=8)


def extract_json(text: str) -> dict | None:
    """First JSON object in a model answer (tolerates ```json fences and surrounding text)."""
    if not text:
        return None
    cleaned = re.sub(r'```(?:json)?', '', text)
    start = cleaned.find('{')
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(cleaned[start:i + 1])
                    except ValueError:
                        # 12/11/2026: one comma left before a « ] » (« [0.5, 0.1, 0, ] ») made a whole maquette
                        # written by Claude unreadable ; the next « { » was then a small inner object, read for
                        # the answer. The same text without such commas is tried before giving this object up.
                        try:
                            value = json.loads(_without_trailing_commas(cleaned[start:i + 1]))
                        except ValueError:
                            break
                    return value if isinstance(value, dict) else None
        start = cleaned.find('{', start + 1)
    return None


def _without_trailing_commas(text: str) -> str:
    """``text`` (JSON written by a model) without the commas that stand just before a « ] » or a « } », outside the
    strings: nothing else is touched."""
    out: list[str] = []
    in_str = escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == ',':
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in ']}':
                continue
        out.append(ch)
    return ''.join(out)


def plan_tool_descriptions(registry: dict | None) -> dict[str, str]:
    names = list(PLAN_READ_TOOLS) + list(PLAN_SENSITIVE_TOOLS)
    out = {}
    for name in names:
        if registry is not None and name not in registry:
            continue
        spec = registry.get(name) if registry else None
        out[name] = str(getattr(spec, 'description', '') or TOOL_TITLES.get(name, name))
    return out
