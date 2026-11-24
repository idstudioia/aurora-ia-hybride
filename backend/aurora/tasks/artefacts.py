"""P2 « Les artefacts locaux » (reviews/proposition-travail-comme-claude-code.md): an answer becomes a page.

A page is built by code from the data of a finished request, never written by an AI:
- the figures, tables and curves come from the tool results stored with the run, each with its origin
  (the tool, its provenance, the time of the measure);
- the AI's text appears only in « note » blocks, marked with who wrote it and what the checks said;
- the page is made of fixed blocks (stats, table, line, bars, list, note, sources), validated here, then
  rendered as HTML with every text escaped (tasks/artefact_html.py) and shown in an isolated frame.

Stored on the PC, outside the base (no migration): <AURORA_HOME>/artefacts/<id>/meta.json plus one file per
version (v1.json, v2.json…), never overwritten. A page has a key (the diagnosis, a routine, the bench, or one
request): built again from new data, it gets a new version; from the same data, the same version comes back.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .. import config, db
from ..tools.base import fr_num
from . import events, runs

log = logging.getLogger(__name__)

Kind = Literal['diagnostic', 'recherche', 'routine', 'banc', 'reponse', 'analyse']
KIND_LABELS: dict[str, str] = {'diagnostic': 'Diagnostic du PC', 'recherche': 'Recherche web',
                               'routine': 'Bilan de routine', 'banc': 'Banc de routage', 'reponse': 'Réponse',
                               'analyse': 'Analyse de données'}
Tone = Literal['', 'ok', 'warn', 'bad', 'info']
MAX_ROWS, MAX_POINTS, MAX_NOTE = 300, 400, 20_000
_ID_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
_VERSION_FILE = re.compile(r'v(\d{1,6})\.json')
# Stricter than tools/news._http (http(s) prefix only): no space, quote or angle bracket either.
_URL_RE = re.compile(r'^https?://[^\s"<>\\]+$', re.I)
_LOCK = threading.Lock()


class NotFinished(RuntimeError):
    """The request is still running or waiting for an agreement: its page would be incomplete."""


class NothingToShow(ValueError):
    """The request has nothing to put in a page (the message is French, shown as is)."""


# --------------------------------------------------------------------------- the fixed blocks

class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FigureOrigin(_Model):
    """Where figures come from: the tool that measured them, its provenance, the time of the measure.
    (Not verify.origin, which says where a whole answer comes from: the page keeps that one in Page.origin.)"""
    tool: str = Field(max_length=60)
    provenance: str = Field(default='', max_length=60)
    at: str = Field(default='', max_length=40)


class Stat(_Model):
    label: str = Field(max_length=60)
    value: float | None
    unit: str = Field(default='', max_length=20)
    digits: int = Field(default=0, ge=0, le=3)
    tone: Tone = ''
    note: str = Field(default='', max_length=160)


class StatsBlock(_Model):
    type: Literal['stats'] = 'stats'
    title: str = Field(default='', max_length=120)
    items: list[Stat] = Field(min_length=1, max_length=12)
    origin: FigureOrigin


class Column(_Model):
    label: str = Field(max_length=60)
    align: Literal['left', 'right'] = 'left'
    digits: int = Field(default=0, ge=0, le=3)
    unit: str = Field(default='', max_length=20)
    mono: bool = False


Cell = Union[int, float, Annotated[str, Field(max_length=600)], None]


class TableBlock(_Model):
    type: Literal['table'] = 'table'
    title: str = Field(max_length=120)
    columns: list[Column] = Field(min_length=1, max_length=8)
    rows: list[list[Cell]] = Field(max_length=MAX_ROWS)
    note: str = Field(default='', max_length=300)
    origin: FigureOrigin | None = None

    @model_validator(mode='after')
    def _same_width(self) -> 'TableBlock':
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError('chaque ligne doit avoir autant de cellules que de colonnes')
        return self


class Series(_Model):
    label: str = Field(max_length=60)
    points: list[tuple[float, float]] = Field(min_length=2, max_length=MAX_POINTS)


class LineBlock(_Model):
    """A curve. x is a Unix time in seconds (the axis shows local hours); y_max fixes the top (100 for %)."""
    type: Literal['line'] = 'line'
    title: str = Field(max_length=120)
    unit: str = Field(default='', max_length=20)
    y_max: float | None = None
    series: list[Series] = Field(min_length=1, max_length=4)
    origin: FigureOrigin


class Bar(_Model):
    label: str = Field(max_length=200)
    value: float
    note: str = Field(default='', max_length=120)


class BarsBlock(_Model):
    type: Literal['bars'] = 'bars'
    title: str = Field(max_length=120)
    unit: str = Field(default='', max_length=20)
    digits: int = Field(default=0, ge=0, le=3)
    max: float | None = None
    items: list[Bar] = Field(min_length=1, max_length=30)
    origin: FigureOrigin | None = None


class Item(_Model):
    title: str = Field(max_length=400)
    detail: str = Field(default='', max_length=1500)
    tone: Tone = ''
    meta: str = Field(default='', max_length=200)


class ListBlock(_Model):
    type: Literal['list'] = 'list'
    title: str = Field(max_length=120)
    items: list[Item] = Field(min_length=1, max_length=40)
    origin: FigureOrigin | None = None


class NoteBlock(_Model):
    """Text. written_by « aurora »: composed by code from the data; « local » / « cloud »: written by an AI."""
    type: Literal['note'] = 'note'
    title: str = Field(default='', max_length=120)
    text: str = Field(min_length=1, max_length=MAX_NOTE)
    written_by: Literal['aurora', 'local', 'cloud']
    verified: bool | None = None
    warnings: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=8)


class SourceItem(_Model):
    title: str = Field(max_length=300)
    url: str = Field(default='', max_length=2000)
    domain: str = Field(default='', max_length=200)
    excerpt: str = Field(default='', max_length=700)
    read: bool = False

    @field_validator('url', mode='before')
    @classmethod
    def _web_only(cls, value: Any) -> str:
        """Only an http(s) address becomes a link: javascript:, data:, file: are dropped, and so is an address
        too long to be kept (before max_length, so that one link never refuses the whole page)."""
        value = str(value or '').strip()
        return value if len(value) <= 2000 and _URL_RE.fullmatch(value) else ''


class SourcesBlock(_Model):
    type: Literal['sources'] = 'sources'
    title: str = Field(default='Sources', max_length=120)
    items: list[SourceItem] = Field(min_length=1, max_length=30)
    at: str = Field(default='', max_length=40)
    note: str = Field(default='', max_length=300)


Block = Annotated[Union[StatsBlock, TableBlock, LineBlock, BarsBlock, ListBlock, NoteBlock, SourcesBlock],
                  Field(discriminator='type')]


class PageSource(_Model):
    run_id: int | None = None
    conversation_id: int | None = None
    bench_generated_at: str | None = None
    request: str = Field(default='', max_length=500)


class Page(_Model):
    """A report page (not tools/webpage.Page, a web page read from the Internet)."""
    kind: Kind
    title: str = Field(min_length=1, max_length=160)
    subtitle: str = Field(default='', max_length=300)
    status: Literal['', 'ok', 'attention', 'critique'] = ''
    sensitive: bool = False
    origin: dict | None = None          # RunResult.origin (the badges of the answer), as the chat shows them
    source: PageSource = Field(default_factory=PageSource)
    blocks: list[Block] = Field(min_length=1, max_length=40)


# --------------------------------------------------------------------------- reading a run

def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and value not in (float('inf'), float('-inf')) else None


def _tool_label(tool: str) -> str:
    from ..core.planner import TOOL_TITLES   # lazy: core imports tasks
    return TOOL_TITLES.get(tool, tool)


def tool_results(result: dict) -> list[tuple[dict, dict]]:
    """(step, tool result) of every tool step that ran to the end, in the order of the run."""
    found = []
    for step in result.get('steps') or []:
        if step.get('stage') == 'tool' and step.get('status') == 'done' and step.get('tool'):
            res = runs.step_result(step)
            if isinstance(res, dict) and res.get('ok'):
                found.append((step, res))
    return found


def _origin(step: dict, res: dict) -> FigureOrigin:
    at = next((s.get('retrieved_at') for s in res.get('sources') or [] if isinstance(s, dict)
               and s.get('retrieved_at')), '') or step.get('finished_at') or ''
    return FigureOrigin(tool=str(step.get('tool') or ''), provenance=str(res.get('provenance') or '')[:60],
                        at=str(at)[:40])


def _request(result: dict) -> tuple[str, bool]:
    """The user's sentence, masked when it holds a secret, and whether something was masked."""
    from ..security import redaction
    run = runs.get_run(int(result['task_id'])) or {}
    masked, found = redaction.redact(str(run.get('input_text') or '')[:2000])
    return masked[:500], bool(found)


def _answer_note(result: dict, title: str = 'Réponse') -> NoteBlock | None:
    text = str(result.get('answer') or '').strip()
    if not text:
        return None
    writer = (result.get('origin') or {}).get('written_by')
    verification = result.get('verification') or {}
    warnings = [f"{c.get('label') or c.get('name')}{' — ' + str(c.get('detail')) if c.get('detail') else ''}"[:300]
                for c in verification.get('checks') or [] if isinstance(c, dict) and not c.get('ok')]
    return NoteBlock(title=title, text=text[:MAX_NOTE], written_by=writer if writer in ('local', 'cloud') else 'aurora',
                     verified=bool(verification.get('ok')) if verification else None, warnings=warnings[:8])


def _base(result: dict, kind: str, title: str, subtitle: str, blocks: list, *, status: str = '') -> Page:
    request, masked = _request(result)
    return Page(kind=kind, title=title[:160], subtitle=subtitle[:300], status=status,
                sensitive=bool(result.get('shows_sensitive') or masked), origin=result.get('origin') or None,
                source=PageSource(run_id=int(result['task_id']), conversation_id=result.get('conversation_id'),
                                  request=request),
                blocks=[b for b in blocks if b is not None])


# --------------------------------------------------------------------------- the pages, one per kind of request

SEVERITY_TONE = {'critical': 'bad', 'warning': 'warn', 'info': 'info'}
SEVERITY_LABEL = {'critical': 'critique', 'warning': 'attention', 'info': 'remarque'}
DIAG_TITLES = {'ok': 'aucun problème majeur', 'attention': 'points d’attention', 'critique': 'situation critique'}


def _tone_of(findings: list[dict], prefix: str) -> str:
    severities = {f.get('severity') for f in findings if str(f.get('id') or '').startswith(prefix)}
    return 'bad' if 'critical' in severities else 'warn' if 'warning' in severities else ''


def diagnostic_page(result: dict, step: dict, res: dict) -> Page:
    data = res.get('data') or {}
    snap, metrics = data.get('snapshot') or {}, data.get('metrics') or {}
    findings = [f for f in data.get('findings') or [] if isinstance(f, dict)]
    origin = _origin(step, res)
    uptime = _num(metrics.get('uptime_s') if metrics.get('uptime_s') is not None else snap.get('uptime_s'))
    stats = StatsBlock(title='Mesures', origin=origin, items=[
        Stat(label='Processeur', value=_num(metrics.get('cpu_percent')), unit='%', tone=_tone_of(findings, 'cpu.')),
        Stat(label='Mémoire vive', value=_num(metrics.get('ram_percent')), unit='%',
             tone=_tone_of(findings, 'ram.') or _tone_of(findings, 'swap.'),
             note=(f"{fr_num(snap['ram_used_gb'])} / {fr_num(snap['ram_total_gb'])} Go"
                   if _num(snap.get('ram_used_gb')) is not None and _num(snap.get('ram_total_gb')) is not None else '')),
        Stat(label='Disque', value=_num(metrics.get('disk_percent')), unit='%', tone=_tone_of(findings, 'disk.')),
        Stat(label='Espace libre', value=_num(metrics.get('disk_free_gb')), unit='Go', digits=1,
             tone=_tone_of(findings, 'disk.')),
        Stat(label='Processus', value=_num(metrics.get('process_count')), tone=_tone_of(findings, 'proc.count')),
        Stat(label='Allumé depuis', value=None if uptime is None else uptime / 3600, unit='h', digits=1,
             tone=_tone_of(findings, 'uptime.')),
    ])
    blocks: list = [stats]
    series: dict[str, list[tuple[float, float]]] = {'Processeur': [], 'Mémoire vive': []}
    for pt in data.get('history') or []:
        moment = runs.parse_iso(str(pt.get('t') or '')) if isinstance(pt, dict) else None
        if moment is None:
            continue
        for label, key in (('Processeur', 'cpu'), ('Mémoire vive', 'ram')):
            if _num(pt.get(key)) is not None:
                series[label].append((moment.timestamp(), float(pt[key])))
    drawn = [Series(label=k, points=v[-MAX_POINTS:]) for k, v in series.items() if len(v) >= 2]
    if drawn:
        blocks.append(LineBlock(title='Processeur et mémoire avant le diagnostic', unit='%', y_max=100, series=drawn,
                                origin=FigureOrigin(tool='system.history', provenance='psutil', at=origin.at)))
    if findings:
        blocks.append(ListBlock(title='Constats', origin=origin, items=[
            Item(title=str(f.get('title') or '—')[:400], detail=str(f.get('detail') or '')[:1500],
                 tone=SEVERITY_TONE.get(str(f.get('severity')), ''), meta=SEVERITY_LABEL.get(str(f.get('severity')), ''))
            for f in findings[:40]]))
    causes = [c for c in data.get('causes') or [] if isinstance(c, dict)]
    if causes:
        blocks.append(ListBlock(title='Causes probables', origin=origin, items=[
            Item(title=str(c.get('title') or '—')[:400],
                 meta=f"confiance {c['confidence']}"[:200] if c.get('confidence') else '')
            for c in causes[:20]]))
    suggestions = [s for s in data.get('suggestions') or [] if isinstance(s, dict)]
    if suggestions:
        blocks.append(ListBlock(title='Suggestions (rien n’a été modifié)', origin=origin, items=[
            Item(title=str(s.get('title') or '—')[:400],
                 detail=' · '.join(x for x in (f"Bénéfice : {s['benefit']}" if s.get('benefit') else '',
                                               f"Risque : {s['risk']}" if s.get('risk') else '') if x)[:1500],
                 meta=(f"action possible : {(s.get('action') or {}).get('label')}"[:200]
                       if isinstance(s.get('action'), dict) and (s.get('action') or {}).get('label') else ''))
            for s in suggestions[:20]]))
    procs = [p for p in data.get('top_processes') or [] if isinstance(p, dict)]
    by_cpu = sorted((p for p in procs if _num(p.get('cpu_percent')) is not None), key=lambda p: -p['cpu_percent'])
    if by_cpu:
        blocks.append(BarsBlock(title='Processus les plus gourmands en processeur', unit='%', digits=1, origin=origin,
                                items=[Bar(label=str(p.get('name') or '—')[:200], value=float(p['cpu_percent']),
                                           note=f"PID {p.get('pid')}"[:120] if p.get('pid') is not None else '')
                                       for p in by_cpu[:8]]))
    if procs:
        blocks.append(TableBlock(title='Processus relevés', origin=origin, columns=[
            Column(label='Nom'), Column(label='PID', align='right', mono=True),
            Column(label='Processeur', align='right', digits=1, unit='%'),
            Column(label='Mémoire', align='right', digits=0, unit='Mo')],
            rows=[[str(p.get('name') or '—')[:600], int(p['pid']) if _num(p.get('pid')) is not None else None,
                   _num(p.get('cpu_percent')), _num(p.get('memory_mb'))] for p in procs[:30]]))
    blocks.append(_answer_note(result, 'Ce que dit le diagnostic'))
    status = str(data.get('status') or '')
    title = f"Diagnostic du PC · {DIAG_TITLES.get(status, 'résultat')}"
    return _base(result, 'diagnostic', title, 'Mesures locales (psutil) et règles de diagnostic d’AURORA, en lecture seule.',
                 blocks, status=status if status in DIAG_TITLES else '')


def research_page(result: dict, searches: list[tuple[dict, dict]]) -> Page:
    seen: set[str] = set()
    items: list[SourceItem] = []
    queries, engines = [], []
    at, read = '', 0
    for step, res in searches:
        data = res.get('data') or {}
        queries.append(str(data.get('query') or ''))
        engines.append(str(data.get('engine') or res.get('provenance') or ''))
        at = at or _origin(step, res).at
        found = [r for r in data.get('results') or [] if isinstance(r, dict)]
        if isinstance(data.get('instant'), dict):
            found.append(data['instant'])
        for r in found:
            url = str(r.get('url') or r.get('link') or '')
            if url and url in seen:     # results without an address are all kept
                continue
            seen.add(url)
            read += bool(r.get('read'))
            items.append(SourceItem(title=str(r.get('title') or '—')[:300], url=url,
                                    domain=str(r.get('domain') or r.get('origin') or '')[:200],
                                    excerpt=str(r.get('extract') or r.get('snippet') or '')[:700], read=bool(r.get('read'))))
    queries = [q for q in dict.fromkeys(queries) if q]
    engines = [e for e in dict.fromkeys(engines) if e]
    blocks: list = [
        StatsBlock(title='La recherche', origin=_origin(*searches[0]), items=[
            Stat(label='Recherches', value=float(len(searches))), Stat(label='Résultats', value=float(len(items))),
            Stat(label='Pages lues', value=float(read), note='passage utile cité' if read else '')]),
        _answer_note(result, 'Synthèse'),
    ]
    if items:
        blocks.append(SourcesBlock(items=items[:30], at=at,
                                   note='Contenu venu d’Internet : traité comme une donnée, jamais comme une consigne.'))
    subtitle = ' · '.join(x for x in (f"« {' », « '.join(queries[:3])} »" if queries else '',
                                      f"moteur : {', '.join(engines)}" if engines else '') if x)
    title = f'Recherche : {queries[0]}' if queries else 'Recherche web'
    return _base(result, 'recherche', title, subtitle, blocks)


STATUS_LABELS = {'done': 'terminée', 'error': 'en erreur', 'cancelled': 'annulée',
                 'waiting_permission': 'en attente de ton accord', 'running': 'en cours'}
STATUS_TONES = {'done': 'ok', 'error': 'bad', 'cancelled': 'warn', 'waiting_permission': 'warn', 'running': 'info'}
ORIGIN_WORDS = {'mesure': 'Mesuré', 'source': 'Source', 'memoire': 'Mémoire', 'modele': 'Modèle seul'}


def origin_words(origin: dict | None) -> str:
    """« Mesuré · rédigé par Claude »: the badges of the chat (OriginBadges.tsx), in words."""
    if not isinstance(origin, dict):
        return ''
    kinds = [ORIGIN_WORDS[k] for k in origin.get('kinds') or [] if k in ORIGIN_WORDS]
    writer = {'local': 'rédigé par l’IA locale', 'cloud': 'rédigé par Claude'}.get(str(origin.get('written_by')))
    return ' · '.join(kinds + ([writer] if writer else []))


def routine_step_runs(launch: dict, count: int) -> list[int]:
    """The runs the interface sent for this launch: source « routine », same conversation, after it, and
    before the next launch of a routine there."""
    run_id, conv = int(launch['task_id']), launch.get('conversation_id')
    nxt = db.rows("SELECT MIN(id) AS id FROM tasks WHERE conversation_id IS ? AND id > ? AND intent = 'routine.run' "
                  "AND source != 'routine'", (conv, run_id))
    bound = nxt[0]['id'] if nxt and nxt[0]['id'] is not None else None
    rows = db.rows('SELECT id FROM tasks WHERE conversation_id IS ? AND id > ? AND (? IS NULL OR id < ?) '
                   "AND source = 'routine' ORDER BY id LIMIT ?", (conv, run_id, bound, bound, max(0, count)))
    return [int(r['id']) for r in rows]


def routine_page(result: dict, step_results: list[dict | None]) -> Page:
    routine = (result.get('ui') or {}).get('routine') or {}
    planned = [str(s) for s in routine.get('steps') or []]
    rows, answers, durations = [], [], []
    for i, text in enumerate(planned):
        res = step_results[i] if i < len(step_results) else None
        if res is None:
            rows.append([i + 1, text[:600], 'pas encore lancée', '—', None])
            continue
        status = str(res.get('status') or '')
        tools = list(dict.fromkeys(_tool_label(str(s.get('tool'))) for s, _r in tool_results(res)))
        # A step not over has no duration yet (RunResult gives the time elapsed so far, which grows at each
        # reading): « — », out of the total, so the same journal still gives the same page.
        seconds = (_num(res.get('duration_ms')) or 0) / 1000 if status in ('done', 'error', 'cancelled') else None
        rows.append([i + 1, text[:600], STATUS_LABELS.get(status, status), ', '.join(tools)[:600] or '—',
                     None if seconds is None else round(seconds, 1)])
        if seconds is not None:
            durations.append(Bar(label=f'{i + 1}. {text}'[:200], value=round(seconds, 2)))
        answer = str(res.get('answer') or '').strip()
        if answer:
            answers.append(Item(title=f'{i + 1}. {text}'[:400], detail=answer[:1500],
                                tone=STATUS_TONES.get(status, ''), meta=origin_words(res.get('origin'))))
    done = [r for r in step_results if r and r.get('status') == 'done']
    failed = [r for r in step_results if r and r.get('status') in ('error', 'cancelled')]
    waiting = [r for r in step_results if r and r.get('status') == 'waiting_permission']
    cost = sum(_num(r.get('cost_usd')) or 0 for r in step_results if r)
    # Dated by the end of its last step, never « now »: the same journal gives the same page (and version).
    ends = [str((runs.get_run(int(r['task_id'])) or {}).get('finished_at') or '') for r in step_results if r]
    origin = FigureOrigin(tool='journal', provenance='AURORA', at=max(ends, default='')[:40])
    stats = [Stat(label='Étapes prévues', value=float(len(planned))),
             Stat(label='Terminées', value=float(len(done)), tone='ok' if len(done) == len(planned) else ''),
             Stat(label='En erreur ou annulées', value=float(len(failed)), tone='bad' if failed else ''),
             Stat(label='En attente d’accord', value=float(len(waiting)), tone='warn' if waiting else ''),
             Stat(label='Durée totale', value=round(sum(b.value for b in durations), 1), unit='s', digits=1)]
    if cost > 0:
        stats.append(Stat(label='Coût cloud', value=cost, unit='$', digits=3))
    blocks: list = [StatsBlock(title='La routine', items=stats, origin=origin),
                    TableBlock(title='Les étapes', origin=origin, columns=[
                        Column(label='N°', align='right'), Column(label='Demande'), Column(label='État'),
                        Column(label='Outils'), Column(label='Durée', align='right', digits=1, unit='s')], rows=rows)]
    if len(durations) >= 2:
        blocks.append(BarsBlock(title='Durée de chaque étape', unit='s', digits=1, items=durations, origin=origin))
    if answers:
        blocks.append(ListBlock(title='Les réponses (chacune avec son origine)', items=answers))
    name = str(routine.get('name') or 'routine')
    return _base(result, 'routine', f'Bilan de la routine « {name} »',
                 f'{len(done)} étape(s) terminée(s) sur {len(planned)}, lues dans le journal des demandes.', blocks)


def answer_page(result: dict) -> Page:
    from ..memory.recall import when   # lazy: memory imports core
    steps = []
    for step, res in tool_results(result):
        origin = _origin(step, res)
        steps.append(Item(title=_tool_label(origin.tool), detail=str(res.get('summary') or '')[:1500], tone='ok',
                          meta=' · '.join(x for x in (origin.provenance, when(origin.at) if origin.at else '') if x)))
    blocks: list = [_answer_note(result)]
    if steps:
        blocks.append(ListBlock(title='Ce qui a été mesuré ou consulté', items=steps))
    memories = [m for m in result.get('memories_used') or [] if isinstance(m, dict)]
    if memories:
        blocks.append(ListBlock(title='Souvenirs utilisés', items=[
            Item(title=str(m.get('title') or '—')[:400], meta=f"pertinence {round(float(m['score']), 2)}"
                 if _num(m.get('score')) is not None else '') for m in memories[:20]]))
    sources = [SourceItem(title=str(s.get('title') or '—')[:300], url=str(s.get('url') or ''))
               for s in result.get('sources') or [] if isinstance(s, dict) and (s.get('title') or s.get('url'))]
    if sources:
        blocks.append(SourcesBlock(items=sources[:30]))
    request, _masked = _request(result)
    title = request.replace('\n', ' ')[:120] or 'Réponse'
    return _base(result, 'reponse', title, origin_words(result.get('origin')), blocks)


def bench_page(report: dict) -> Page:
    rules = report.get('rules') or {}
    at = str(report.get('generated_at') or '')[:40]
    origin = FigureOrigin(tool='bench.routing', provenance='tests/bench_routing.py', at=at)
    accuracy = _num(rules.get('accuracy'))
    semantic = report.get('rules_semantic') or {}
    latency = report.get('latency_rules') or {}
    second = report.get('secondary_intents') or {}
    determinism = report.get('determinism') or {}
    stats = [Stat(label='Précision (règles)', value=None if accuracy is None else accuracy * 100, unit='%', digits=1,
                  note=f"{rules.get('correct')} / {rules.get('n')} phrases" if rules.get('n') else ''),
             Stat(label='Phrases du banc', value=_num((report.get('corpus') or {}).get('n'))),
             Stat(label='Intentions', value=_num((report.get('corpus') or {}).get('intents'))),
             Stat(label='Latence p50', value=_num(latency.get('p50_ms')), unit='ms', digits=2),
             Stat(label='Latence p95', value=_num(latency.get('p95_ms')), unit='ms', digits=2),
             Stat(label='Écarts entre 2 passages', value=_num(determinism.get('mismatches')),
                  tone='ok' if determinism.get('mismatches') == 0 else 'bad' if determinism.get('mismatches') else '')]
    if _num(semantic.get('accuracy')) is not None:
        stats.insert(1, Stat(label='Avec la similarité', value=semantic['accuracy'] * 100, unit='%', digits=1))
    if _num(second.get('recall')) is not None:
        stats.append(Stat(label='2es intentions trouvées', value=second['recall'] * 100, unit='%', digits=1,
                          note=f"{second.get('found')} / {second.get('expected')}"))
    blocks: list = [StatsBlock(title='Le banc', items=stats, origin=origin)]
    tags = [(k, v) for k, v in (rules.get('by_tag') or {}).items()
            if isinstance(v, dict) and _num(v.get('accuracy')) is not None]
    if tags:
        blocks.append(BarsBlock(title='Précision par type de phrase', unit='%', digits=1, max=100, origin=origin,
                                items=[Bar(label=str(k), value=float(v['accuracy']) * 100,
                                           note=f"{v.get('correct')} / {v.get('n')}") for k, v in tags]))
    intents = [(name, (v.get('correct') or 0) / v['n'] * 100, v) for name, v in (rules.get('by_intent') or {}).items()
               if isinstance(v, dict) and _num(v.get('n'))]
    worst = sorted(intents, key=lambda x: x[1])[:8]
    if worst and worst[0][1] < 100:
        blocks.append(BarsBlock(title='Intentions les moins bien reconnues', unit='%', digits=0, max=100, origin=origin,
                                items=[Bar(label=n, value=round(a, 1), note=f"{v.get('correct')} / {v.get('n')}")
                                       for n, a, v in worst]))
    confusions = [c for c in rules.get('confusions') or [] if isinstance(c, dict)]
    if confusions:
        blocks.append(TableBlock(title='Confusions les plus fréquentes', origin=origin, columns=[
            Column(label='Attendu', mono=True), Column(label='Compris', mono=True), Column(label='Fois', align='right'),
            Column(label='Exemple')], rows=[[str(c.get('expected')), str(c.get('got')), _num(c.get('count')),
                                             str((c.get('examples') or [''])[0])[:600]] for c in confusions[:20]]))
    failures = [f for f in rules.get('failures') or [] if isinstance(f, dict)]
    if failures:
        blocks.append(TableBlock(title='Phrases mal comprises', origin=origin, columns=[
            Column(label='Phrase'), Column(label='Attendu', mono=True), Column(label='Compris', mono=True),
            Column(label='Type')], rows=[[str(f.get('text'))[:600], str(f.get('expected')), str(f.get('got')),
                                          str(f.get('tag') or '')] for f in failures[:60]]))
    replay = report.get('reports')
    if isinstance(replay, dict) and _num(replay.get('n')):
        score = replay.get('rules') or {}
        blocks.append(StatsBlock(title='Tes signalements rejoués (hors score du banc)', origin=origin, items=[
            Stat(label='Signalements', value=_num(replay.get('n'))),
            Stat(label='Bien compris maintenant', value=_num(score.get('correct')),
                 tone='ok' if score.get('correct') == replay.get('n') else 'warn')]))
    reason = str((report.get('recommendation') or {}).get('reason') or '').strip()
    if reason:
        blocks.append(NoteBlock(title='Recommandation du banc', text=reason, written_by='aurora'))
    from ..memory.recall import when
    return Page(kind='banc', title='Banc de routage', subtitle=f'Résultat du {when(at)}' if at else '',
                source=PageSource(bench_generated_at=at or None), blocks=blocks)


def _seconds(moment: datetime) -> float:
    """Seconds since 1970 of a local date; Windows refuses .timestamp() before 1970 (a birth date, a stray
    « 01/01/1900 »): counted from 1970 instead, which the axis of the page reads back the same way."""
    try:
        return moment.timestamp()
    except (OSError, OverflowError, ValueError):
        return (moment - datetime(1970, 1, 1)).total_seconds()


def _what(d: dict) -> str:
    """« somme de « Montant » », the calculation of an analysis result in words (tools/data.AGG_LABELS)."""
    from ..tools.data import AGG_LABELS   # lazy: tools are owned by another module
    agg = AGG_LABELS.get(str(d.get('agg')), str(d.get('agg') or ''))
    return f"{agg} de « {d['value']} »" if d.get('value') else agg


def analysis_page(result: dict, done: list[tuple[dict, dict]]) -> Page:
    """P3: the page of a data.analyze request, from the results of tools/data.py (the file itself never shown)."""
    blocks: list = []
    table: dict = {}
    for step, res in done:
        tool, d, origin = step.get('tool'), res.get('data') or {}, _origin(step, res)
        if tool == 'data.describe':
            table = d
            cols = d.get('columns') or []
            missing = sum(c.get('missing') or 0 for c in cols)
            blocks.append(StatsBlock(title='Le tableau', origin=origin, items=[
                Stat(label='Lignes', value=_num(d.get('rows'))), Stat(label='Colonnes', value=float(len(cols))),
                Stat(label='Cases vides', value=float(missing), tone='warn' if missing else ''),
                Stat(label='Lignes en double', value=_num(d.get('duplicates')), tone='warn' if d.get('duplicates') else ''),
                Stat(label='Lignes ignorées', value=_num(d.get('skipped')), tone='warn' if d.get('skipped') else '',
                     note='mal formées' if d.get('skipped') else '')]))
            blocks.append(TableBlock(title='Les colonnes', origin=origin, note=str(d.get('calc') or '')[:300], columns=[
                Column(label='Colonne'), Column(label='Type'), Column(label='Remplies', align='right'),
                Column(label='Vides', align='right'), Column(label='Minimum', align='right', digits=2),
                Column(label='Maximum', align='right', digits=2), Column(label='Moyenne', align='right', digits=2)],
                rows=[[str(c.get('name'))[:600], str(c.get('kind') or ''), _num(c.get('filled')), _num(c.get('missing')),
                       _num(c.get('min')), _num(c.get('max')), _num(c.get('mean'))] for c in cols[:60]]))
        elif tool == 'data.trend':
            points = [p for p in d.get('points') or [] if _num(p.get('value')) is not None]
            series = [(_seconds(datetime.fromisoformat(p['start'])), float(p['value'])) for p in points]
            if len(series) >= 2:
                blocks.append(LineBlock(title=f"Évolution par {d.get('period')} : {_what(d)}"[:120], origin=origin,
                                        series=[Series(label=str(d.get('value') or 'Lignes')[:60],
                                                       points=series[-MAX_POINTS:])]))
            blocks.append(TableBlock(title=f"Par {d.get('period')}"[:120], origin=origin, note=str(d.get('calc') or '')[:300],
                                     columns=[Column(label='Période'), Column(label='Valeur', align='right', digits=2),
                                              Column(label='Lignes', align='right'), Column(label='Complète')],
                                     rows=[[str(p.get('label')), _num(p.get('value')), _num(p.get('count')),
                                            'non' if p.get('partial') else 'oui'] for p in (d.get('points') or [])[-60:]]))
        elif tool == 'data.group':
            bars = [Bar(label=str(g.get('group'))[:200], value=float(g['value']),
                        note=f"{fr_num(g['share'], 1)} %" if _num(g.get('share')) is not None else f"{g.get('count')} ligne(s)")
                    for g in d.get('groups') or [] if _num(g.get('value')) is not None]
            if bars:
                blocks.append(BarsBlock(title=f"{_what(d)[0].upper()}{_what(d)[1:]} par « {d.get('by')} »"[:120], digits=2,
                                        items=bars[:30], origin=origin))
        elif tool == 'data.outliers':
            context = list(((d.get('outliers') or [{}])[0].get('context') or {}).keys())[:4]
            blocks.append(TableBlock(
                title=f"Valeurs anormales de « {d.get('column')} » : {d.get('count', 0)}"[:120], origin=origin,
                note=str(d.get('calc') or '')[:300],
                columns=[Column(label='Ligne', align='right'), Column(label='Valeur', align='right', digits=2),
                         Column(label='Écart')] + [Column(label=str(c)[:60]) for c in context],
                rows=[[_num(o.get('line')), _num(o.get('value')), f"{o.get('level')} ({o.get('side')})"]
                      + [str((o.get('context') or {}).get(c, ''))[:600] for c in context] for o in d.get('outliers') or []]))
        elif tool == 'data.compare':
            a, b = d.get('first') or {}, d.get('second') or {}
            blocks.append(StatsBlock(title=f"{b.get('label')} comparé à {a.get('label')}"[:120], origin=origin, items=[
                Stat(label=str(a.get('label') or '—')[:60], value=_num(a.get('value')), digits=2),
                Stat(label=str(b.get('label') or '—')[:60], value=_num(b.get('value')), digits=2),
                Stat(label='Écart', value=_num(d.get('diff')), digits=2),
                Stat(label='Écart en %', value=_num(d.get('diff_pct')), unit='%', digits=1,
                     note=str(d.get('calc') or '')[:160])]))
            if d.get('changes'):
                blocks.append(TableBlock(title=f"Écarts par « {d.get('by')} »"[:120], origin=origin, columns=[
                    Column(label=str(d.get('by') or 'Groupe')[:60]),
                    Column(label=str(a.get('label') or '—')[:60], align='right', digits=2),
                    Column(label=str(b.get('label') or '—')[:60], align='right', digits=2),
                    Column(label='Écart', align='right', digits=2), Column(label='Écart', align='right', digits=1, unit='%')],
                    rows=[[str(c.get('group'))[:600], _num(c.get('first')), _num(c.get('second')), _num(c.get('diff')),
                           _num(c.get('diff_pct'))] for c in d['changes'][:30]]))
    actions = ((result.get('ui') or {}).get('data') or {}).get('actions') or []
    if actions:
        blocks.append(ListBlock(title='Actions proposées (à lancer depuis le Chat)', items=[
            Item(title=str(a.get('title') or '—')[:400], meta=f"confiance {a.get('confidence')}"[:200],
                 tone='info' if a.get('kind') == 'info' else 'ok',
                 detail=f"Preuve : {a.get('proof')}\nCe qu’elle ferait : {a.get('effect')}"[:1500]) for a in actions[:20]]))
    blocks.append(_answer_note(result, 'Ce que dit l’analyse'))
    name = str(table.get('name') or 'tableau')
    where = 'fichier joint au message' if table.get('source') != 'fichier' else str(table.get('path') or '')
    return _base(result, 'analyse', f'Analyse de « {name} »',
                 f"{table.get('rows', '—')} lignes, {len(table.get('columns') or [])} colonnes · {where}", blocks)


def build_page(result: dict, result_of: Callable[[int], dict | None]) -> tuple[Page, str]:
    """(page, key) of a finished request: a routine launch, a diagnosis, a web search, or any answer."""
    routine = (result.get('ui') or {}).get('routine')
    if isinstance(routine, dict) and routine.get('steps'):
        ids = routine_step_runs(result, len(routine['steps']))
        return routine_page(result, [result_of(i) for i in ids]), f"routine:{routine.get('id')}"
    done = tool_results(result)
    if any(s.get('tool') == 'data.describe' for s, _r in done):
        return analysis_page(result, done), f"run:{result['task_id']}"
    diagnosis = [(s, r) for s, r in done if s.get('tool') == 'system.diagnose']
    if diagnosis:
        return diagnostic_page(result, *diagnosis[-1]), 'diagnostic'
    searches = [(s, r) for s, r in done if s.get('tool') == 'web.search']
    if searches:
        return research_page(result, searches), f"run:{result['task_id']}"
    return answer_page(result), f"run:{result['task_id']}"


# --------------------------------------------------------------------------- storage: one folder per page

def valid_id(page_id: str) -> bool:
    return bool(_ID_RE.fullmatch(str(page_id or '')))


def _folder(page_id: str) -> Path:
    if not valid_id(page_id):
        raise ValueError('Identifiant de page invalide.')
    return config.artefacts_dir() / page_id


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_meta(folder: Path, meta: dict) -> None:
    tmp = folder / 'meta.json.tmp'
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, folder / 'meta.json')


def _metas() -> list[dict]:
    root = config.artefacts_dir()
    if not root.is_dir():
        return []
    found = []
    for folder in root.iterdir():
        meta = _read(folder / 'meta.json') if folder.is_dir() and valid_id(folder.name) else None
        if meta and meta.get('id') == folder.name and meta.get('versions'):
            found.append(meta)
    return found


def _summary(meta: dict) -> dict:
    last = meta['versions'][-1]
    return {'id': meta['id'], 'kind': meta['kind'], 'title': last['title'], 'subtitle': last.get('subtitle', ''),
            'created_at': meta['created_at'], 'updated_at': last['created_at'], 'versions': len(meta['versions']),
            'latest_version': last['version'], 'sensitive': any(v.get('sensitive') for v in meta['versions'])}


def list_pages() -> list[dict]:
    """Every page, the one changed last first."""
    return sorted((_summary(m) for m in _metas()), key=lambda s: (s['updated_at'], s['id']), reverse=True)


def get_page(page_id: str) -> dict | None:
    """The page with its versions, the newest first. None if unknown."""
    meta = _read(_folder(page_id) / 'meta.json') if valid_id(page_id) else None
    if not meta or not meta.get('versions'):
        return None
    history = [{'version': v['version'], 'created_at': v['created_at'], 'title': v['title'],
                'source': {'run_id': v['source'].get('run_id'), 'conversation_id': v['source'].get('conversation_id'),
                           'bench_generated_at': v['source'].get('bench_generated_at')}}
               for v in reversed(meta['versions'])]
    return {**_summary(meta), 'history': history}


def read_version(page_id: str, version: int | None = None) -> dict | None:
    """{'version', 'versions', 'created_at', 'page'} of one version (the last one by default), or None."""
    meta = _read(_folder(page_id) / 'meta.json') if valid_id(page_id) else None
    if not meta or not meta.get('versions'):
        return None
    wanted = meta['versions'][-1] if version is None else next(
        (v for v in meta['versions'] if v['version'] == int(version)), None)
    stored = _read(_folder(page_id) / f"v{wanted['version']}.json") if wanted else None
    if not stored or not isinstance(stored.get('page'), dict):
        return None
    return {'version': wanted['version'], 'versions': len(meta['versions']), 'created_at': wanted['created_at'],
            'page': stored['page']}


def _same_source(stored: dict, source: PageSource) -> bool:
    """Built from the same request (or the same bench result): its data never change, so neither does its
    version, even if AURORA lays it out differently since. Only a routine's report grows as its steps end."""
    if source.run_id is None and source.bench_generated_at is None:
        return False
    return (stored.get('run_id'), stored.get('bench_generated_at')) == (source.run_id, source.bench_generated_at)


def fingerprint(page: Page) -> str:
    return hashlib.sha256(json.dumps(page.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
                          .encode('utf-8')).hexdigest()[:32]


def save(page: Page, key: str) -> dict:
    """Store a page under its key: a new page, a new version, or the version that already holds these data.
    Returns {'artefact': summary, 'version': n, 'created': bool}."""
    page = Page.model_validate(page.model_dump(mode='json'))   # what is written is what the renderer reads
    print_ = fingerprint(page)
    with _LOCK:
        meta = next((m for m in _metas() if m.get('key') == key), None)
        if meta is not None:
            same = next((v for v in meta['versions'] if v.get('fingerprint') == print_
                         or (page.kind != 'routine' and _same_source(v.get('source') or {}, page.source))), None)
            if same is not None:
                return {'artefact': _summary(meta), 'version': same['version'], 'created': False}
            folder = _folder(meta['id'])
        else:
            page_id = f"{page.kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
            folder = _folder(page_id)
            folder.mkdir(parents=True, exist_ok=False)
            meta = {'id': page_id, 'key': key, 'kind': page.kind, 'created_at': runs.now_iso(), 'versions': []}
        # The numbers of meta.json and of the files: a vN.json left without its line (service stopped between
        # the two writes) is skipped, never overwritten, and never blocks the page.
        on_disk = [int(m.group(1)) for p in folder.glob('v*.json') if (m := _VERSION_FILE.fullmatch(p.name))]
        number = max([v['version'] for v in meta['versions']] + on_disk, default=0) + 1
        with open(folder / f'v{number}.json', 'x', encoding='utf-8') as fh:   # a version is never overwritten
            json.dump({'version': number, 'page': page.model_dump(mode='json')}, fh, ensure_ascii=False)
        meta['versions'].append({'version': number, 'created_at': runs.now_iso(), 'title': page.title,
                                 'subtitle': page.subtitle, 'sensitive': page.sensitive, 'fingerprint': print_,
                                 'source': page.source.model_dump(mode='json')})
        _write_meta(folder, meta)
    events.emit('artefact.saved', f'Page « {page.title[:120]} » (version {number})', subject_type='system',
                stage='response', payload={'id': meta['id'], 'version': number, 'kind': page.kind,
                                           'run_id': page.source.run_id})
    return {'artefact': _summary(meta), 'version': number, 'created': True}


def delete_page(page_id: str) -> bool:
    """Remove a page and all its versions (the interface asks first). False if unknown."""
    if not valid_id(page_id):
        return False
    with _LOCK:
        folder = _folder(page_id)
        meta = _read(folder / 'meta.json')
        if not meta:
            return False
        shutil.rmtree(folder)
    events.emit('artefact.deleted', f"Page supprimée ({len(meta.get('versions') or [])} version(s))",
                subject_type='system', payload={'id': page_id})
    return True


# --------------------------------------------------------------------------- entry points of the API

def make_from_run(run_id: int, result_of: Callable[[int], dict | None]) -> dict | None:
    """The page of a finished request (None if the request does not exist)."""
    result = result_of(int(run_id))
    if result is None:
        return None
    status = result.get('status')
    if status in ('running', 'waiting_permission'):
        raise NotFinished('Cette demande n’est pas terminée : sa page serait incomplète.')
    if status != 'done' or not str(result.get('answer') or '').strip():
        raise NothingToShow('Cette demande n’a pas abouti : il n’y a rien à mettre en page.')
    page, key = build_page(result, result_of)
    return save(page, key)


def make_from_bench() -> dict | None:
    """The page of the last routing bench (None if it never ran). Same file as /api/quality/latest."""
    report = _read(config.bench_latest_path())
    if not report:
        return None
    return save(bench_page(report), 'banc')
