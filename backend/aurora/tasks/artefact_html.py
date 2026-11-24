"""HTML of a P2 page (tasks/artefacts.py): one standalone file, no script, no request to the network.

- every text goes through html.escape; links are http(s) only (checked by the model) and open outside the frame;
- numbers are formatted in French here (tools/base.fr_num) from the values of the page: nothing is written by
  an AI at this stage, and a missing value is shown as « — »;
- the Content-Security-Policy forbids every load (scripts, images, fonts, frames): only the inline style applies.
The interface shows it in an <iframe sandbox> without scripts; the same file can be downloaded and opened alone.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from typing import Any

from ..tools.base import fr_num

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
SERIES_CLASSES = ('s0', 's1', 's2', 's3')
ORIGIN_BADGES = {'mesure': ('Mesuré', 'green'), 'source': ('Source', 'cyan'), 'memoire': ('Mémoire', 'violet'),
                 'modele': ('Modèle seul', 'amber')}
WRITERS = {'local': 'l’IA locale', 'cloud': 'Claude'}
STATUS = {'ok': ('aucun problème majeur', 'green'), 'attention': ('points d’attention', 'amber'),
          'critique': ('situation critique', 'red')}
SOURCE_WORDS = {'journal': 'Lu dans le journal des demandes d’AURORA',
                'bench.routing': 'Calculé par le banc de routage'}

STYLE = """
:root{color-scheme:dark;--bg:#030914;--panel:#081830;--panel-2:#0b2142;--border:#1b4677;--soft:rgba(60,130,220,.22);
--text:#dcebff;--strong:#f3f8ff;--muted:#7f9cc0;--cyan:#22c8ff;--violet:#8a5cff;--green:#2ee6a8;--amber:#ffb547;
--red:#ff6b7a;--font:"Inter","Segoe UI Variable","Segoe UI",system-ui,sans-serif;--mono:"Cascadia Code",Consolas,monospace}
*{box-sizing:border-box}html{background:var(--bg)}
body{margin:0;background:radial-gradient(1200px 500px at 10% -10%,#0a2a55 0,transparent 60%),var(--bg);color:var(--text);
font:15px/1.55 var(--font);padding:28px clamp(16px,4vw,48px) 40px}
main{max-width:1080px;margin:0 auto}
.eyebrow{color:var(--cyan);font-size:12px;letter-spacing:.14em;text-transform:uppercase}
h1{color:var(--strong);font-size:clamp(22px,3vw,30px);line-height:1.2;margin:6px 0 4px;overflow-wrap:anywhere}
.subtitle{color:var(--muted);margin:0 0 12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 4px}
.chip{border:1px solid var(--soft);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--text);background:rgba(8,24,48,.7)}
.chip.green{border-color:rgba(46,230,168,.5);color:var(--green)}.chip.cyan{border-color:rgba(34,200,255,.5);color:var(--cyan)}
.chip.violet{border-color:rgba(138,92,255,.6);color:#b99bff}.chip.amber{border-color:rgba(255,181,71,.55);color:var(--amber)}
.chip.red{border-color:rgba(255,107,122,.6);color:var(--red)}
.request{margin:8px 0 0;color:var(--muted);font-size:13px;overflow-wrap:anywhere}.request q{color:var(--text)}
section{background:linear-gradient(180deg,var(--panel-2),var(--panel));border:1px solid var(--soft);border-radius:14px;
padding:16px 18px;margin:16px 0;break-inside:avoid}
h2{font-size:16px;color:var(--strong);margin:0 0 10px}
.origin{color:var(--muted);font-size:12px;margin:10px 0 0}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.stat{border:1px solid var(--soft);border-radius:10px;padding:10px 12px;background:rgba(3,9,20,.35)}
.stat .label{color:var(--muted);font-size:12px}.stat .value{display:block;font-size:24px;color:var(--strong);font-weight:600}
.stat .unit{font-size:14px;color:var(--muted);margin-left:3px}.stat small{color:var(--muted);font-size:12px}
.stat.ok .value{color:var(--green)}.stat.warn .value{color:var(--amber)}.stat.bad .value{color:var(--red)}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{color:var(--muted);font-weight:600;text-align:left;border-bottom:1px solid var(--border);padding:6px 8px;white-space:nowrap}
td{border-bottom:1px solid var(--soft);padding:6px 8px;vertical-align:top;overflow-wrap:anywhere}
.r{text-align:right;font-variant-numeric:tabular-nums}.mono{font-family:var(--mono);font-size:12.5px}
.items{list-style:none;margin:0;padding:0}
.items li{display:grid;grid-template-columns:10px 1fr;gap:4px 10px;padding:8px 0;border-bottom:1px solid var(--soft)}
.items li:last-child{border-bottom:0}
.dot{width:8px;height:8px;border-radius:50%;margin-top:7px;background:var(--muted)}
.dot.ok{background:var(--green)}.dot.warn{background:var(--amber)}.dot.bad{background:var(--red)}.dot.info{background:var(--cyan)}
.items strong{color:var(--strong)}.items .meta{color:var(--muted);font-size:12px;margin-left:6px}
.items p{grid-column:2;margin:2px 0 0;color:var(--text);white-space:pre-wrap;overflow-wrap:anywhere}
.bars{display:grid;gap:7px}
.bar{display:grid;grid-template-columns:minmax(110px,30%) 1fr 10.5em;gap:10px;align-items:center;font-size:13.5px}
.bar .val{text-align:right}
.bar .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.track{height:10px;border-radius:6px;background:rgba(60,130,220,.15);overflow:hidden}
.fill{display:block;height:100%;border-radius:6px;background:linear-gradient(90deg,#2f7bff,var(--cyan))}
.bar .val{font-variant-numeric:tabular-nums;color:var(--strong);white-space:nowrap}.bar small{color:var(--muted);margin-left:6px}
svg{width:100%;height:auto;display:block}
.grid{stroke:rgba(60,130,220,.22);stroke-width:1}.axis{fill:var(--muted);font-size:11px;font-family:var(--font)}
.line{fill:none;stroke-width:2;stroke-linejoin:round}.s0{stroke:var(--cyan)}.s1{stroke:var(--violet)}
.s2{stroke:var(--green)}.s3{stroke:var(--amber)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:8px;font-size:13px}
.sw{display:inline-block;width:12px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.sw.s0{background:var(--cyan)}.sw.s1{background:var(--violet)}.sw.s2{background:var(--green)}.sw.s3{background:var(--amber)}
.legend small{color:var(--muted)}
.note-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:8px}
.note-head h2{margin:0 8px 0 0}.md p{margin:0 0 8px;overflow-wrap:anywhere}.md ul,.md ol{margin:0 0 8px;padding-left:22px}
.md .md-h{color:var(--strong);font-weight:600}.md code{font-family:var(--mono);font-size:13px;background:rgba(60,130,220,.15);
padding:1px 4px;border-radius:4px}.md pre{background:rgba(3,9,20,.55);border:1px solid var(--soft);border-radius:8px;
padding:10px;overflow-x:auto}.md pre code{background:none;padding:0}
.md blockquote{margin:0 0 8px;padding-left:12px;border-left:3px solid var(--border);color:var(--muted)}
.md table{margin:0 0 8px}
.warnings{margin:8px 0 0;padding-left:20px;color:var(--amber);font-size:13px}
a{color:var(--cyan)}a:hover{color:#6fe3ff}
.sources{margin:0;padding-left:22px}.sources li{margin:0 0 10px;overflow-wrap:anywhere}.sources small{color:var(--muted)}
.sources p{margin:3px 0 0;color:var(--text);font-size:13.5px}
.muted{color:var(--muted)}
footer{color:var(--muted);font-size:12.5px;border-top:1px solid var(--soft);margin-top:24px;padding-top:12px}
@media (max-width:560px){.bar{grid-template-columns:1fr auto}.bar .track{grid-column:1/-1;order:3}}
@media print{:root{color-scheme:light;--bg:#fff;--panel:#fff;--panel-2:#fff;--text:#111;--strong:#000;--muted:#555;
--soft:#ccc;--border:#999}body{background:#fff;padding:0}section{border-color:#ccc}}
"""


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ''), quote=True)


def _when(iso: str | None) -> str:
    from ..memory.recall import when   # lazy: memory imports core
    return when(iso) if iso else ''


def _number(value: Any, digits: int = 0, unit: str = '') -> str:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return '—'
    text = fr_num(int(value), 0) if isinstance(value, int) and digits == 0 else fr_num(float(value), digits)
    return f'{text} {unit}' if unit and text != '—' else text


# --------------------------------------------------------------------------- the AI's text: safe Markdown
# Same subset as the chat (frontend/src/components/chat/Markdown.tsx, React elements only), minus italics
# and bare links: paragraphs, headings, lists, quotes, simple tables, fenced code, inline code, bold, links.

_BOLD = re.compile(r'\*\*([^*\n]+?)\*\*|__([^_\n]+?)__')
_CODE = re.compile(r'`([^`\n]+)`')
_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://[^)\s]+)\)')
_LIST = re.compile(r'^\s*([-*+•]|\d{1,3}[.)])\s+(.*)$')
_HEADING = re.compile(r'^\s*#{1,6}\s+(.*)$')
_QUOTE = re.compile(r'^\s*>\s?(.*)$')
_FENCE = re.compile(r'^\s*```')
_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')


def _inline(text: str) -> str:
    """Escaped first, then `code`, [text](http…) links and bold: no markup of the text itself survives."""
    out = _e(text)
    out = _CODE.sub(r'<code>\1</code>', out)
    out = _LINK.sub(lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener noreferrer nofollow">'
                              f'{m.group(1)}</a>', out)
    return _BOLD.sub(lambda m: f'<strong>{m.group(1) or m.group(2)}</strong>', out)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip('|').split('|')]


def markdown(text: str) -> str:
    lines = str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    parts: list[str] = []
    para: list[str] = []
    items: list[str] = []
    ordered, start = False, 1

    def flush() -> None:
        if para:
            parts.append('<p>' + '<br>'.join(_inline(line) for line in para) + '</p>')
            para.clear()
        if items:
            # « 2. » after a sub-list keeps its number (the AI's « 1. … - … 2. … »)
            tag = f'ol start="{start}"' if ordered and start != 1 else 'ol' if ordered else 'ul'
            parts.append(f'<{tag}>' + ''.join(f'<li>{_inline(i)}</li>' for i in items) + f'</{tag.split()[0]}>')
            items.clear()

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if _FENCE.match(line):
            flush()
            code, i = [], i + 1
            while i < len(lines) and not _FENCE.match(lines[i]):
                code.append(lines[i])
                i += 1
            parts.append(f'<pre><code>{_e(chr(10).join(code))}</code></pre>')
            i += 1
            continue
        if '|' in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush()
            head, rows, i = _cells(line), [], i + 2
            while i < len(lines) and '|' in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            body = ''.join('<tr>' + ''.join(f'<td>{_inline(c)}</td>' for c in r) + '</tr>' for r in rows)
            parts.append('<div class="table-wrap"><table><thead><tr>' + ''.join(f'<th>{_inline(c)}</th>' for c in head)
                         + f'</tr></thead><tbody>{body}</tbody></table></div>')
            continue
        heading, listed, quote = _HEADING.match(line), _LIST.match(line), _QUOTE.match(line)
        if not line.strip():
            flush()
        elif heading:
            flush()
            parts.append(f'<p class="md-h">{_inline(heading.group(1))}</p>')
        elif quote:
            flush()
            parts.append(f'<blockquote>{_inline(quote.group(1))}</blockquote>')
        elif listed:
            numbered = listed.group(1)[0].isdigit()
            if para or (items and ordered != numbered):
                flush()
            if not items:
                start = int(listed.group(1)[:-1]) if numbered else 1
            ordered = numbered
            items.append(listed.group(2))
        else:
            if items:
                flush()
            para.append(line.strip())
        i += 1
    flush()
    return ''.join(parts)


# --------------------------------------------------------------------------- the blocks

def _figure_origin(origin: dict | None) -> str:
    if not origin:
        return ''
    from ..core.planner import TOOL_TITLES   # lazy: core imports tasks
    tool = str(origin.get('tool') or '')
    head = SOURCE_WORDS.get(tool) or (f"Calculé par AURORA sur le fichier (« {TOOL_TITLES.get(tool, tool)} »)"
                                      if tool.startswith('data.') else f"Mesuré par l’outil « {TOOL_TITLES.get(tool, tool)} »")
    parts = [head] + [x for x in (str(origin.get('provenance') or ''), _when(origin.get('at'))) if x]
    return f'<p class="origin">{_e(" · ".join(parts))}</p>'


def _stats(block: dict) -> str:
    cards = []
    for item in block['items']:
        value = _number(item.get('value'), int(item.get('digits') or 0))
        unit = f'<span class="unit">{_e(item["unit"])}</span>' if item.get('unit') and value != '—' else ''
        note = f'<small>{_e(item["note"])}</small>' if item.get('note') else ''
        cards.append(f'<div class="stat {_e(item.get("tone") or "")}"><span class="label">{_e(item["label"])}</span>'
                     f'<span class="value">{value}{unit}</span>{note}</div>')
    return f'<div class="stats">{"".join(cards)}</div>'


def _table(block: dict) -> str:
    cols = block['columns']
    head = ''.join(f'<th class="{"r" if c.get("align") == "right" else ""}">{_e(c["label"])}</th>' for c in cols)
    body = []
    for row in block['rows']:
        cells = []
        for col, cell in zip(cols, row):
            classes = ' '.join(x for x in ('r' if col.get('align') == 'right' else '', 'mono' if col.get('mono') else '') if x)
            text = (_number(cell, int(col.get('digits') or 0), col.get('unit') or '')
                    if isinstance(cell, (int, float)) and not isinstance(cell, bool)
                    else '—' if cell is None or cell == '' else _e(cell))
            cells.append(f'<td class="{classes}">{text}</td>')
        body.append(f'<tr>{"".join(cells)}</tr>')
    empty = '' if body else f'<tr><td colspan="{len(cols)}" class="muted">Aucune ligne.</td></tr>'
    note = f'<p class="origin">{_e(block["note"])}</p>' if block.get('note') else ''
    return (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}{empty}</tbody>'
            f'</table></div>{note}')


def _clock(t: float, span: float) -> str:
    """Axis label of a point of the curve, in local time: the hour (with seconds over half an hour or less), the
    day beyond two days (P3 trends by week), the month beyond 300 days."""
    try:
        moment = datetime.fromtimestamp(t)
    except (OSError, OverflowError, ValueError):   # before 1970 on Windows (artefacts._seconds)
        moment = datetime(1970, 1, 1) + timedelta(seconds=t)
    if span > 300 * 86400:
        return moment.strftime('%m/%Y')
    if span > 2 * 86400:
        return moment.strftime('%d/%m')
    return moment.strftime('%H:%M:%S' if span <= 1800 else '%H:%M')


def _line(block: dict) -> str:
    width, height, left, right, top, bottom = 720, 230, 46, 14, 12, 28
    series = [s for s in block['series'] if len(s['points']) >= 2]
    xs = [p[0] for s in series for p in s['points']]
    ys = [p[1] for s in series for p in s['points']]
    x0, x1 = min(xs), max(xs)
    x1 = x1 if x1 > x0 else x0 + 1
    y0 = min(0.0, min(ys))
    y1 = float(block['y_max']) if block.get('y_max') else (max(ys) * 1.1 or 1.0)
    y1 = y1 if y1 > y0 else y0 + 1
    unit = str(block.get('unit') or '')

    def px(x: float) -> float:
        return left + (x - x0) / (x1 - x0) * (width - left - right)

    def py(y: float) -> float:
        return top + (1 - (min(max(y, y0), y1) - y0) / (y1 - y0)) * (height - top - bottom)

    grid = []
    for k in range(5):
        value = y0 + (y1 - y0) * k / 4
        y = py(value)
        grid.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>'
                    f'<text class="axis" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{_e(_number(value, 0))}</text>')
    span = x1 - x0
    for k in range(5):
        t = x0 + span * k / 4
        anchor = 'start' if k == 0 else 'end' if k == 4 else 'middle'
        grid.append(f'<text class="axis" x="{px(t):.1f}" y="{height - 8}" text-anchor="{anchor}">{_e(_clock(t, span))}</text>')
    lines, legend = [], []
    for i, s in enumerate(series):
        cls = SERIES_CLASSES[i % len(SERIES_CLASSES)]
        pts = ' '.join(f'{px(x):.1f},{py(y):.1f}' for x, y in s['points'])
        lines.append(f'<polyline class="line {cls}" points="{pts}"/>')
        values = [p[1] for p in s['points']]
        stats = (f'min {_number(min(values), 1, unit)} · moyenne {_number(sum(values) / len(values), 1, unit)} · '
                 f'max {_number(max(values), 1, unit)}')
        legend.append(f'<span><i class="sw {cls}"></i>{_e(s["label"])} <small>{_e(stats)}</small></span>')
    label = f'{block["title"]} : ' + ' ; '.join(f'{s["label"]} ({len(s["points"])} points)' for s in series)
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_e(label)}">{"".join(grid)}{"".join(lines)}'
            f'</svg><div class="legend">{"".join(legend)}</div>')


def _bars(block: dict) -> str:
    values = [float(b['value']) for b in block['items']]
    top = float(block['max']) if block.get('max') else max([v for v in values if v > 0], default=1.0)
    rows = []
    for bar in block['items']:
        share = 0.0 if top <= 0 else min(100.0, max(0.0, float(bar['value']) / top * 100))
        note = f'<small>{_e(bar["note"])}</small>' if bar.get('note') else ''
        rows.append(f'<div class="bar"><span class="name" title="{_e(bar["label"])}">{_e(bar["label"])}</span>'
                    f'<span class="track"><span class="fill" style="width:{share:.1f}%"></span></span>'
                    f'<span class="val">{_number(bar["value"], int(block.get("digits") or 0), block.get("unit") or "")}'
                    f'{note}</span></div>')
    return f'<div class="bars">{"".join(rows)}</div>'


def _list(block: dict) -> str:
    rows = []
    for item in block['items']:
        meta = f'<span class="meta">{_e(item["meta"])}</span>' if item.get('meta') else ''
        detail = f'<p>{_e(item["detail"])}</p>' if item.get('detail') else ''
        rows.append(f'<li><span class="dot {_e(item.get("tone") or "")}"></span><div><strong>{_e(item["title"])}</strong>'
                    f'{meta}</div>{detail}</li>')
    return f'<ul class="items">{"".join(rows)}</ul>'


def _note_badges(block: dict) -> str:
    writer = block.get('written_by')
    badges = [('chip amber', f'rédigé par {WRITERS[writer]}') if writer in WRITERS
              else ('chip green', 'écrit par AURORA à partir des données')]
    if writer in WRITERS:
        if block.get('verified') is True:
            badges.append(('chip green', 'contrôles passés'))
        elif block.get('verified') is False:
            badges.append(('chip amber', 'points à revoir'))
    return ''.join(f'<span class="{cls}">{_e(text)}</span>' for cls, text in badges)


def _note(block: dict) -> str:
    warnings = ''.join(f'<li>{_e(w)}</li>' for w in block.get('warnings') or [])
    caution = ('<p class="origin">Texte reformulé par l’IA à partir des données ci-dessus : une phrase peut être '
               'inexacte même si les données sont justes.</p>') if block.get('written_by') in WRITERS else ''
    return (f'<div class="md">{markdown(block["text"])}</div>'
            f'{f"<ul class=warnings>{warnings}</ul>" if warnings else ""}{caution}')


def _sources(block: dict) -> str:
    rows = []
    for item in block['items']:
        title = _e(item['title'])
        link = (f'<a href="{_e(item["url"])}" target="_blank" rel="noopener noreferrer nofollow">{title}</a>'
                if item.get('url') else title)
        info = ' · '.join(x for x in (item.get('domain') or '', 'page lue' if item.get('read') else '') if x)
        excerpt = f'<p>{_e(item["excerpt"])}</p>' if item.get('excerpt') else ''
        rows.append(f'<li>{link}{f" <small>{_e(info)}</small>" if info else ""}{excerpt}</li>')
    when = _when(block.get('at'))
    foot = ' · '.join(x for x in (f'Consultées le {when}' if when else '', block.get('note') or '') if x)
    return f'<ol class="sources">{"".join(rows)}</ol>{f"<p class=origin>{_e(foot)}</p>" if foot else ""}'


RENDERERS = {'stats': _stats, 'table': _table, 'line': _line, 'bars': _bars, 'list': _list, 'note': _note,
             'sources': _sources}


def _block(block: dict) -> str:
    body = RENDERERS[block['type']](block)
    title = str(block.get('title') or '')
    if block['type'] == 'note':
        head = f'<div class="note-head">{f"<h2>{_e(title)}</h2>" if title else ""}{_note_badges(block)}</div>'
    else:
        head = f'<h2>{_e(title)}</h2>' if title else ''
    return f'<section class="block-{block["type"]}">{head}{body}{_figure_origin(block.get("origin"))}</section>'


def _origin_chips(origin: dict | None) -> str:
    """The badges of the answer (frontend OriginBadges.tsx), from RunResult.origin."""
    if not isinstance(origin, dict):
        return ''
    kinds = [k for k in origin.get('kinds') or [] if k in ORIGIN_BADGES]
    chips = [f'<span class="chip {ORIGIN_BADGES[k][1]}">{_e(ORIGIN_BADGES[k][0])}</span>' for k in kinds]
    writer = origin.get('written_by')
    if writer in WRITERS:
        chips.append(f'<span class="chip amber">rédigé par {_e(WRITERS[writer])}</span>')
    unverified = [str(u) for u in origin.get('unverified') or []][:3]
    if kinds == ['modele'] and unverified:
        chips.append(f'<span class="chip amber">non vérifié : {_e(", ".join(unverified))}</span>')
    return ('<span class="chip">Origine de la réponse</span>' + ''.join(chips)) if chips else ''


def render(page: dict, *, page_id: str, version: int, versions: int, created_at: str) -> str:
    """The standalone HTML of one version of a page (a dict validated by artefacts.Page)."""
    from .artefacts import KIND_LABELS
    status = STATUS.get(str(page.get('status') or ''))
    chips = [f'<span class="chip cyan">{_e(KIND_LABELS.get(page["kind"], page["kind"]))}</span>',
             f'<span class="chip">version {int(version)} sur {int(versions)}</span>']
    if created_at:
        chips.append(f'<span class="chip">créée {_e(_when(created_at))}</span>')
    if status:
        chips.append(f'<span class="chip {status[1]}">{_e(status[0])}</span>')
    if page.get('sensitive'):
        chips.append('<span class="chip red">contient des données sensibles</span>')
    source = page.get('source') or {}
    request = (f'<p class="request">Demande n° {_e(source["run_id"])} : <q>{_e(source.get("request") or "")}</q></p>'
               if source.get('run_id') else '')
    origin = _origin_chips(page.get('origin'))
    subtitle = f'<p class="subtitle">{_e(page["subtitle"])}</p>' if page.get('subtitle') else ''
    origin_row = f'<div class="chips">{origin}</div>' if origin else ''
    blocks = ''.join(_block(b) for b in page['blocks'])
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="referrer" content="no-referrer">
<title>{_e(page['title'])} · AURORA</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<header>
<div class="eyebrow">AURORA · page locale</div>
<h1>{_e(page['title'])}</h1>
{subtitle}
<div class="chips">{''.join(chips)}</div>
{origin_row}
{request}
</header>
{blocks}
<footer>Page « {_e(page_id)} », construite par AURORA sur ce PC. Les chiffres, tableaux et courbes sont calculés par
le code à partir des résultats des outils, chacun avec son origine ; seul un texte marqué « rédigé par… » vient d’une
IA. Construire cette page n’a rien envoyé sur Internet, et elle ne charge rien.</footer>
</main>
</body>
</html>
"""
