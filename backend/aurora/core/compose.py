"""Deterministic French answers built only from tool data (principle 1: no LLM).

Each template reads measured values with .get() and falls back to the tool's own
factual `summary`. Nothing here produces a number that is not in the data.
"""
from __future__ import annotations

import re
from typing import Any, Callable

PAGE_LABELS = {
    'home': 'Accueil', 'chat': 'Conversation', 'search': 'Recherche', 'computer': 'Ordinateur',
    'world': 'Globe', 'simulations': 'Simulations', 'memory': 'Mémoire', 'tasks': 'Tâches',
    'settings': 'Paramètres',
}
WIDGETS = {
    'system.snapshot': 'system', 'system.diagnose': 'system', 'system.processes': 'processes',
    'weather.current': 'weather', 'weather.cities': 'weather', 'sim.orbit': 'orbit',
    'sim.solar_system': 'solar', 'quakes.recent': 'quakes', 'web.search': 'search',
    # files.restore has no widget: its data lists no file to show, and the widget's « Restaurer » button
    # pointed at the restore run itself (seen on 28/10, A5 check). The answer lists the restored files.
    'news.latest': 'search', 'files.scan': 'files', 'files.quarantine': 'files',
}


def fr_num(value: Any, digits: int = 1) -> str:
    """12.34 -> '12,3' ; 12.0 -> '12' ; non-numbers returned as text."""
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int):
        return f'{value:,}'.replace(',', ' ')
    if isinstance(value, float):
        text = f'{round(value, digits):.{digits}f}'
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
        whole, _, frac = text.partition('.')
        sign = '-' if whole.startswith('-') else ''
        whole = whole.lstrip('-')
        whole = f'{int(whole):,}'.replace(',', ' ') if whole.isdigit() else whole
        return sign + whole + (',' + frac if frac else '')
    return str(value)


def fr_size(num_bytes: Any) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return '?'
    for unit in ('octets', 'Ko', 'Mo', 'Go', 'To'):
        if size < 1024 or unit == 'To':
            return f'{fr_num(size if unit != "octets" else int(size), 1)} {unit}'
        size /= 1024
    return f'{fr_num(size)} To'


def _has(data: dict, *keys: str) -> bool:
    return all(isinstance(data.get(k), (int, float)) and not isinstance(data.get(k), bool) for k in keys)


def _summary(result: dict | None, fallback: str = 'Résultat indisponible.') -> str:
    if not result:
        return fallback
    return str(result.get('summary') or fallback)


def failure_text(label: str, result: dict | None) -> str:
    error = (result or {}).get('error') or (result or {}).get('summary') or 'source indisponible'
    return f'{label} : indisponible ({error}). Aucune valeur de remplacement n’est affichée.'


# --- system -----------------------------------------------------------------------

def _cpu(d: dict) -> str | None:
    if not _has(d, 'cpu_percent'):
        return None
    text = f"Processeur : {fr_num(d['cpu_percent'])} % d’utilisation"
    if _has(d, 'cpu_count'):
        text += f" sur {d['cpu_count']} cœurs logiques"
    if _has(d, 'cpu_freq_mhz'):
        text += f" ({fr_num(d['cpu_freq_mhz'], 0)} MHz)"
    return text + '.'


def _ram(d: dict) -> str | None:
    if _has(d, 'ram_used_gb', 'ram_total_gb', 'ram_percent'):
        return (f"Mémoire vive : {fr_num(d['ram_used_gb'])} Go utilisés sur {fr_num(d['ram_total_gb'])} Go "
                f"({fr_num(d['ram_percent'])} %).")
    if _has(d, 'ram_percent'):
        total = f" sur {fr_num(d['ram_total_gb'])} Go" if _has(d, 'ram_total_gb') else ''
        return f"Mémoire vive : {fr_num(d['ram_percent'])} % utilisée{total}."
    return None


def _disk(d: dict) -> str | None:
    if _has(d, 'disk_used_gb', 'disk_total_gb', 'disk_percent'):
        return (f"Disque système : {fr_num(d['disk_used_gb'])} Go utilisés sur {fr_num(d['disk_total_gb'])} Go "
                f"({fr_num(d['disk_percent'])} %).")
    if _has(d, 'disk_percent'):
        return f"Disque système : {fr_num(d['disk_percent'])} % occupé."
    return None


def _net(d: dict) -> str | None:
    if _has(d, 'net_down_kbps', 'net_up_kbps'):
        return (f"Réseau : {fr_num(d['net_down_kbps'])} kbit/s en réception, "
                f"{fr_num(d['net_up_kbps'])} kbit/s en émission.")
    return None


def _gpu(d: dict) -> str:
    gpu = d.get('gpu')
    if not isinstance(gpu, dict):
        return 'GPU : aucune mesure disponible sur ce PC (capteur non pris en charge).'
    text = f"GPU {gpu.get('name') or ''}".rstrip()
    parts = []
    if isinstance(gpu.get('util_percent'), (int, float)):
        parts.append(f"{fr_num(gpu['util_percent'])} % d’utilisation")
    if isinstance(gpu.get('memory_used_mb'), (int, float)) and isinstance(gpu.get('memory_total_mb'), (int, float)):
        parts.append(f"{fr_num(gpu['memory_used_mb'], 0)} Mo de mémoire utilisés sur {fr_num(gpu['memory_total_mb'], 0)} Mo")
    return text + (' : ' + ', '.join(parts) if parts else ' : détecté, sans mesure d’utilisation') + '.'


def _battery(d: dict) -> str:
    if _has(d, 'battery_percent'):
        return f"Batterie : {fr_num(d['battery_percent'], 0)} %."
    return 'Batterie : aucune batterie détectée (PC fixe ou capteur indisponible).'


def _temps(d: dict) -> str | None:
    temps = d.get('temperatures')
    if not isinstance(temps, dict) or not temps:
        return None
    items = [f'{name} {fr_num(value)} °C' for name, value in list(temps.items())[:4]
             if isinstance(value, (int, float))]
    return ('Températures : ' + ', '.join(items) + '.') if items else None


GPU_SOURCES = {'pdh': 'compteurs de performance Windows (PDH)', 'nvidia-smi': 'nvidia-smi',
               'registry': 'registre Windows (modèle et mémoire seulement)'}


def _source_line(metric: str, data: dict) -> str:
    """Where the numbers come from: the GPU is not measured by psutil."""
    gpu = data.get('gpu') if isinstance(data.get('gpu'), dict) else {}
    if metric == 'gpu' and gpu.get('source'):
        return f"Source : mesure locale ({GPU_SOURCES.get(gpu['source'], gpu['source'])}), à l’instant."
    return 'Source : mesure locale (psutil), à l’instant.'


def system_status(slots: dict, results: dict) -> str:
    snap = results.get('system.snapshot')
    if not snap or not snap.get('ok'):
        return failure_text('Mesure système', snap)
    metric = str(slots.get('metric') or 'all').lower()
    if str(snap.get('summary') or '').strip():
        if metric == 'identity':   # 22/11/2026: what no tool reads (a serial number…): no « Source : mesure locale »
            return str(snap['summary'])
        # The tool's own summary is metric-aware and built from the measured data.
        return f"{snap['summary']}\n{_source_line(metric, snap.get('data') or {})}"
    d = snap.get('data') or {}
    pick: dict[str, Callable[[dict], str | None]] = {
        'cpu': _cpu, 'ram': _ram, 'disk': _disk, 'network': _net, 'gpu': _gpu, 'battery': _battery,
    }
    if metric in pick:
        lines = [pick[metric](d)]
    else:
        lines = [_cpu(d), _ram(d), _disk(d), _net(d), _temps(d)]
        if isinstance(d.get('gpu'), dict):
            lines.append(_gpu(d))
        if _has(d, 'battery_percent'):
            lines.append(_battery(d))
    lines = [line for line in lines if line]
    if not lines:
        return _summary(snap)
    return '\n'.join(lines) + '\n' + _source_line(metric, d)


def processes(slots: dict, results: dict, *, limit: int = 8) -> str:
    res = results.get('system.processes')
    if not res or not res.get('ok'):
        return failure_text('Liste des processus', res)
    procs = (res.get('data') or {}).get('processes') or []
    if not procs:
        return _summary(res, 'Aucun processus mesuré.')
    sort = str(slots.get('sort') or 'cpu')
    head = 'Programmes qui consomment le plus de ' + ('mémoire' if sort == 'memory' else 'processeur') + ' :'
    lines = [head]
    for i, p in enumerate(procs[:limit], 1):
        bits = []
        if isinstance(p.get('cpu_percent'), (int, float)):
            bits.append(f"CPU {fr_num(p['cpu_percent'])} %")
        if isinstance(p.get('memory_mb'), (int, float)):
            bits.append(f"{fr_num(p['memory_mb'], 0)} Mo")
        lines.append(f"{i}. {p.get('name') or '?'} (PID {p.get('pid')}) — {', '.join(bits)}")
    lines.append('Source : mesure locale (psutil).')
    return '\n'.join(lines)


def _causes(data: dict) -> list[str]:
    raw = data.get('causes') or data.get('findings') or data.get('issues') or []
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            title = item.get('title') or item.get('label') or item.get('cause') or ''
            detail = item.get('detail') or item.get('evidence') or item.get('description') or ''
            text = f'{title} — {detail}' if title and detail else (title or detail)
            if text and item.get('confidence'):
                text += f" (confiance {item['confidence']})"
            if text:
                out.append(str(text))
    return out


def _suggestions(data: dict) -> list[str]:
    out = []
    for item in data.get('suggestions') or []:
        if not isinstance(item, dict) or not item.get('title'):
            continue
        bits = [str(item['title'])]
        if item.get('benefit'):
            bits.append(f"bénéfice : {item['benefit']}")
        if item.get('risk'):
            bits.append(f"risque : {item['risk']}")
        out.append(' — '.join(bits))
    return out


def main_cause(data: dict) -> str:
    """Short text of the main probable cause (used to build a web query)."""
    for key in ('main_cause', 'primary_cause', 'top_cause'):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            text = value.get('title') or value.get('label') or value.get('cause')
            if text:
                return str(text)
    raw = data.get('causes') or data.get('findings') or []
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict):
            return str(first.get('title') or first.get('label') or first.get('cause') or '')
        return str(first)
    return ''


def diagnose(slots: dict, results: dict) -> str:
    res = results.get('system.diagnose')
    if not res or not res.get('ok'):
        status = system_status({'metric': 'all'}, results)
        return failure_text('Diagnostic', res) + ('\n' + status if results.get('system.snapshot') else '')
    data = res.get('data') or {}
    lines = [_summary(res)]
    causes = _causes(data)
    if causes:
        lines.append('Causes probables :')
        lines += [f'{i}. {c}' for i, c in enumerate(causes[:5], 1)]
    suggestions = _suggestions(data)
    if suggestions:
        lines.append('Pistes proposées (rien n’est exécuté sans votre accord) :')
        lines += [f'- {s}' for s in suggestions[:5]]
    procs = (results.get('system.processes') or {}).get('data', {}).get('processes') or []
    if procs:
        top = ', '.join(f"{p.get('name')} ({fr_num(p.get('cpu_percent'))} % CPU)" for p in procs[:3]
                        if isinstance(p.get('cpu_percent'), (int, float)))
        if top:
            lines.append(f'Processus les plus actifs : {top}.')
    if 'n’a été faite' not in lines[0] and "n'a été faite" not in lines[0]:
        lines.append('Aucune modification n’a été faite sur le PC.')
    return '\n'.join(lines)


# --- lookups and measures from network tools ------------------------------------------

def _list_items(data: dict, *keys: str) -> list[dict]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
    return []


def quakes(slots: dict, results: dict) -> str:
    res = results.get('quakes.recent')
    if not res or not res.get('ok'):
        return failure_text('Séismes récents', res)
    data = res.get('data') or {}
    lines = [_summary(res)]
    items = _list_items(data, 'quakes', 'events', 'features', 'items')
    for q in items[:5]:
        mag = q.get('mag', q.get('magnitude'))
        place = q.get('place') or q.get('location') or ''
        when = _local_moment(q.get('time') or q.get('time_utc') or '')
        if isinstance(mag, (int, float)) and not isinstance(mag, bool):
            lines.append(f"- M {f'{float(mag):.1f}'.replace('.', ',')} — {place}" + (f', {when}' if when else ''))
    lines.append('Source : USGS.')
    return '\n'.join(lines)


_MONTHS_SHORT = ('janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.')


def _local_moment(iso: str) -> str:
    """'2026-10-28T02:21:45Z' -> '28 oct. à 03 h 21 (heure locale)'; unreadable -> the text as is."""
    from datetime import datetime
    if not iso:
        return ''
    try:
        moment = datetime.fromisoformat(str(iso).replace('Z', '+00:00')).astimezone()
    except ValueError:
        return str(iso)
    return f'{moment.day} {_MONTHS_SHORT[moment.month - 1]} à {moment.hour:02d} h {moment.minute:02d} (heure locale)'


def calc(slots: dict, results: dict, tool: str = 'calc.evaluate') -> str:
    res = results.get(tool)
    if not res or not res.get('ok'):
        return failure_text('Calcul' if tool == 'calc.evaluate' else 'Conversion', res)
    return _summary(res)


def sources_list(sources: list[dict], *, limit: int = 6) -> str:
    lines = []
    for i, s in enumerate(sources[:limit], 1):
        title = s.get('title') or 'Source'
        url = s.get('url') or ''
        lines.append(f'[{i}] {title}' + (f' — {url}' if url else ''))
    return '\n'.join(lines)


def search_results(res: dict | None, label: str = 'Recherche') -> str:
    if not res or not res.get('ok'):
        return failure_text(label, res)
    data = res.get('data') or {}
    items = _list_items(data, 'results', 'items', 'articles', 'pages')
    lines = [_summary(res)]
    for i, item in enumerate(items[:6], 1):
        title = item.get('title') or 'Sans titre'
        extract = str(item.get('extract') or item.get('snippet') or item.get('summary') or '').strip()
        if len(extract) > 220:
            extract = extract[:217].rstrip() + '…'
        lines.append(f'[{i}] {title}' + (f' : {extract}' if extract else ''))
    if not items and res.get('sources'):
        lines.append(sources_list(res['sources']))
    return '\n'.join(lines)


def world_news_answer(res: dict | None) -> str:
    """The news of the world (tools/news.py news.world, 8/11/2026): the media's own titles, each linked to its article,
    with the media and how long ago (fr_duration, as for the quakes). Nothing is written by a model."""
    import time
    from ..tools.base import fr_duration
    if not res or not res.get('ok'):
        return failure_text('Actualités du monde', res)
    articles = _list_items(res.get('data') or {}, 'articles')
    lines = [_summary(res)]
    for i, a in enumerate(articles[:6], 1):
        title = re.sub(r'[\[\]]', '', str(a.get('titre') or 'Sans titre')).strip()[:200]
        link = a.get('lien') if isinstance(a.get('lien'), str) and a['lien'].startswith(('https://', 'http://')) else ''
        date = a.get('date')
        ago = f', il y a {fr_duration(max(time.time() - date / 1000, 0))}' \
            if isinstance(date, (int, float)) and not isinstance(date, bool) else ''
        where = f", {a['pays']}" if a.get('pays') else ''
        lines.append(f"[{i}] " + (f'[{title}]({link})' if link else title) + f" — {a.get('source') or 'média'}{where}{ago}")
    if articles:
        lines.append('Titres tels que les médias les écrivent ; le globe de Monde 3D les montre par pays.')
    return '\n'.join(lines)


def files_scan(slots: dict, results: dict) -> str:
    res = results.get('files.scan')
    if not res or not res.get('ok'):
        return failure_text('Analyse de fichiers', res)
    data = res.get('data') or {}
    lines = [_summary(res)]
    for f in _list_items(data, 'files', 'items', 'candidates')[:8]:
        path = f.get('path') or f.get('name') or '?'
        size = file_size_bytes(f)
        lines.append(f'- {path}' + (f' ({fr_size(size)})' if size is not None else ''))
    if 'lecture seule' not in lines[0].lower():   # the tool's summary usually says it already
        lines.append('Analyse en lecture seule : aucun fichier n’a été modifié.')
    return '\n'.join(lines)


def scan_candidates(data: dict) -> list[dict]:
    return _list_items(data, 'files', 'items', 'candidates')


def file_size_bytes(item: dict) -> int | None:
    """Size of a scanned file entry ('size' / 'size_bytes' in bytes, or 'size_mb')."""
    for key in ('size', 'size_bytes'):
        if isinstance(item.get(key), (int, float)):
            return int(item[key])
    if isinstance(item.get('size_mb'), (int, float)):
        return int(round(item['size_mb'] * 1024 ** 2))
    return None


def files_cleanup(slots: dict, results: dict, *, permission_status: str | None, run_id: int) -> str:
    scan = results.get('files.scan')
    if not scan or not scan.get('ok'):
        return failure_text('Analyse des fichiers à nettoyer', scan)
    if not scan_candidates(scan.get('data') or {}):
        return _summary(scan) + '\nAucun fichier ne correspond : rien à mettre en quarantaine.'
    if permission_status in ('denied', 'expired'):
        label = 'Autorisation refusée' if permission_status == 'denied' else 'Autorisation expirée sans décision'
        return _summary(scan) + f'\n{label} : aucun fichier n’a été déplacé.'
    moved = results.get('files.quarantine')
    if not moved:
        return _summary(scan) + '\nAucun fichier n’a été déplacé.'
    if not moved.get('ok'):
        return failure_text('Mise en quarantaine', moved)
    return (_summary(moved) + '\nLes fichiers sont en quarantaine réversible (rien n’est supprimé), dans la '
            f'quarantaine de la tâche n° {run_id} : ils restent restaurables à leur emplacement d’origine '
            '(bouton « Restaurer » affiché avec cette réponse).')


def routine_launch(routine: dict | None, asked: str | None, known: list[dict]) -> str:
    """D1: the steps are run next by the interface, one after the other: this answer never says they are done."""
    if routine:
        steps = '\n'.join(f'{i}. {step}' for i, step in enumerate(routine.get('steps') or [], 1))
        return (f"Routine « {routine.get('name')} » : {len(routine.get('steps') or [])} étape(s), lancées l’une après "
                f"l’autre ci-dessous, chacune avec sa propre réponse.\n{steps}")
    names = ', '.join(f"« {r.get('name')} »" for r in known)
    lead = f'Je ne trouve pas de routine « {asked} ».' if asked else 'Je ne trouve pas cette routine.'
    return (f'{lead} Routines enregistrées : {names}.' if names else
            f'{lead} Aucune routine n’est enregistrée : créez-en une dans Paramètres › Routines.')


def files_restore(results: dict, *, permission_status: str | None) -> str:
    """B4: « restaure les fichiers mis en quarantaine », answered from the files.restore result only."""
    if permission_status in ('denied', 'expired'):
        label = 'Autorisation refusée' if permission_status == 'denied' else 'Autorisation expirée sans décision'
        return f'{label} : aucun fichier n’a été remis en place, ils restent en quarantaine.'
    res = results.get('files.restore')
    if res is None:
        return ('Aucun fichier en quarantaine à restaurer : AURORA n’en a mis aucun de côté, '
                'ou ils ont déjà tous été remis en place.')
    if not res.get('ok'):
        return failure_text('Restauration des fichiers', res)
    restored = (res.get('data') or {}).get('restored') or []
    lines = [_summary(res)] + [f"- {item.get('original')}" for item in restored[:10]]
    if len(restored) > 10:
        lines.append(f'… et {len(restored) - 10} autre(s)')
    return '\n'.join(lines)


def kill_process(slots: dict, dry_run: dict | None, final: dict | None, *, permission_status: str | None) -> str:
    """dry_run: result of the read-only resolution; final: result of the real stop (if run)."""
    name = slots.get('name') or (f"PID {slots['pid']}" if slots.get('pid') else 'demandé')
    if not dry_run or not dry_run.get('ok'):
        return failure_text(f'Recherche du processus « {name} »', dry_run) + ' Rien n’a été arrêté.'
    if permission_status in ('denied', 'expired'):
        label = 'Autorisation refusée' if permission_status == 'denied' else 'Autorisation expirée sans décision'
        return f'{label} : « {name} » n’a pas été arrêté.'
    if not final:
        return f'« {name} » n’a pas été arrêté.'
    if not final.get('ok'):
        return failure_text(f'Arrêt de « {name} »', final)
    data = final.get('data') or {}
    lines = [_summary(final)]
    still = data.get('still_running') or []
    if still:
        lines.append(f'{len(still)} processus toujours actif(s) : vous pouvez réessayer ou le fermer vous-même.')
    return '\n'.join(lines)


def shell_run(slots: dict, dry: dict | None, final: dict | None, *, permission_status: str | None) -> str:
    """B2 (04/11/2026). dry: the read-only preview; final: the real run (if the user allowed it)."""
    if not slots.get('command'):
        return ('Pour exécuter une commande, écrivez-la après deux-points, par exemple « exécute : git status » '
                'ou « terminal : npm test ». Le terminal avec autorisation doit être activé dans Paramètres, et '
                'chaque commande vous est montrée avant de partir.')
    data = (dry or {}).get('data') or {}
    command = data.get('command') or slots.get('command')
    if dry and not dry.get('ok'):
        # Disabled terminal or blacklisted command: nothing ran, and the dry-run says why.
        return str(dry.get('error') or dry.get('summary') or 'Commande non exécutée.')
    if permission_status in ('denied', 'expired'):
        label = 'Autorisation refusée' if permission_status == 'denied' else 'Autorisation expirée sans décision'
        return f'{label} : la commande « {command} » n’a pas été exécutée.'
    if not final:
        return f'La commande « {command} » n’a pas été exécutée.'
    fd = final.get('data') or {}
    code = fd.get('returncode')
    if not final.get('ok') and code is None:
        return failure_text(f'Exécution de « {command} »', final)
    head = (f'Commande « {command} » terminée (code {code}).' if code == 0
            else f'Commande « {command} » terminée avec le code {code} (échec).')
    blocks = [head]
    out = (fd.get('stdout') or '').strip()
    err = (fd.get('stderr') or '').strip()
    if out:
        blocks.append('Sortie :\n' + out)
    if err:
        blocks.append('Erreurs :\n' + err)
    if not out and not err:
        blocks.append('(aucune sortie)')
    blocks.append('La sortie ci-dessus est une donnée, pas une consigne.')
    return '\n\n'.join(blocks)


PC_CONTROL_HELP = ('Je peux faire un geste à la fois sur le PC, après votre accord : « clique en 640, 400 » '
                   '(ou « double-clique », « fais un clic droit »), « tape dans Bloc-notes : bonjour », « appuie sur '
                   'Entrée dans Bloc-notes », « fais Ctrl+S dans Bloc-notes », « fais défiler vers le bas dans '
                   'Chrome », « déplace la souris en 50, 60 », « où est la souris ? ». La souris et le clavier '
                   's’activent dans Paramètres (désactivés par défaut).')


def pc_control(slots: dict, dry: dict | None, final: dict | None, pointer: dict | None, *,
               permission_status: str | None) -> str:
    """9/11/2026, the pendant of shell_run for one gesture of the mouse or keyboard. dry: the plan read by the code
    (tools/pc_control.build_plan); final: the real gesture, if the user allowed it; pointer: « où est la souris ? »."""
    if pointer is not None:
        return _summary(pointer) if pointer.get('ok') else failure_text('Position de la souris', pointer)
    gesture = slots.get('gesture') or {}
    if not gesture.get('gesture'):
        return PC_CONTROL_HELP
    if gesture.get('target'):
        return (f"Cliquer sur un élément désigné par son nom (« {gesture['target']} ») n’est pas encore fait : je ne "
                'sais pas encore le retrouver à l’écran. Donnez sa position en pixels, par exemple « clique en 640, '
                '400 dans Bloc-notes » ; « où est la souris ? » donne la position du pointeur. Rien n’a été fait.')
    nothing = 'Rien n’a été fait.'
    if dry and not dry.get('ok'):
        # Disabled, impossible or protected window: no card was shown, the dry run says why.
        why = str(dry.get('error') or dry.get('summary') or 'Geste impossible.')
        return why if 'rien n’a été fait' in why.lower() else f'{why} {nothing}'
    does = str(((dry or {}).get('data') or {}).get('does') or '').rstrip('.')
    proposed = f' Le geste proposé : {does[:1].lower()}{does[1:]}.' if does else ''
    if permission_status in ('denied', 'expired'):
        label = 'Autorisation refusée' if permission_status == 'denied' else 'Autorisation expirée sans décision'
        return f'{label} : rien n’a été fait.{proposed}'
    if not final:
        return f'{nothing}{proposed}'
    if not final.get('ok'):
        why = str(final.get('error') or final.get('summary') or 'Geste impossible.')
        return why if 'rien' in why.lower() else f'{why} {nothing}'
    after = (final.get('data') or {}).get('window_after')
    lines = [_summary(final)]
    if after:
        exe = f" ({after['exe']})" if after.get('exe') else ''
        lines.append(f"Juste après, la fenêtre est « {after.get('title') or 'sans titre'} »{exe}. Ctrl+Z annule une "
                     'saisie dans la plupart des applications.')
    return '\n'.join(lines)


APPS_WHERE = 'La liste se règle dans Paramètres › Applications autorisées.'
YOUTUBE_COMMENT_HELP = ('dites « mets un commentaire sous ma dernière vidéo » (je le rédige avec l’IA locale) ou « mets '
                        'un commentaire sous ma dernière vidéo : votre texte ». Il vous est montré avant d’être publié, '
                        'sauf si « Publier sans redemander » est activé ; la chaîne se relie dans Paramètres › Studio '
                        '› Connexion Google.')
YOUTUBE_UNDO = ('Pour le retirer : sur YouTube, menu ⋮ du commentaire › Supprimer (le retrait par AURORA n’est pas '
                'encore fait).')


def youtube_comment(slots: dict, dry: dict | None, final: dict | None, *, permission_status: str | None,
                    mode: str | None, last: dict | None = None) -> str:
    """9/11/2026: a comment under the newest video of the connected channel (tools/youtube_comments.py). dry: the
    card (video, text); final: the post, if allowed (click or « Publier sans redemander »); last: the last comment
    AURORA posted, for « retire le commentaire »."""
    nothing = 'Rien n’a été publié.'
    if slots.get('remove'):
        said = (f" Le dernier que j’ai publié, sous « {last.get('video_title')} » : « {last.get('text')} » "
                f"({last.get('url')}).") if last else ''
        return f'Retirer un commentaire publié n’est pas encore fait par AURORA.{said} {YOUTUBE_UNDO}'
    if mode == 'prive':
        return f'Mode Privé : AURORA ne publie rien sur Internet. {nothing} Passez en mode Équilibré ou Avancé.'
    if slots.get('video') != 'latest':
        return ('Je ne sais publier que sous votre dernière vidéo, la plus récente de la chaîne reliée : choisir une '
                f'autre vidéo n’est pas encore fait. {nothing} Pour la dernière, {YOUTUBE_COMMENT_HELP}')
    if dry is None:
        return nothing
    if not dry.get('ok'):
        why = str(dry.get('error') or dry.get('summary') or 'Commentaire impossible.')
        return why if 'rien' in why.lower() else f'{why} {nothing}'
    card = dry.get('data') or {}
    shown = f"Le commentaire proposé, sous « {card.get('video_title')} » : « {card.get('text')} »."
    if permission_status in ('denied', 'expired'):
        label = 'Autorisation refusée' if permission_status == 'denied' else 'Autorisation expirée sans décision'
        return f'{label} : rien n’a été publié. {shown}'
    if not final:
        return f'{nothing} {shown}'
    if not final.get('ok'):
        why = str(final.get('error') or final.get('summary') or 'Publication impossible.')
        return why if re.search(r'rien|je ne sais pas', why, re.I) else f'{why} {nothing}'
    done = final.get('data') or {}
    lines = [f"Publié en votre nom sous votre dernière vidéo « {done.get('video_title') or card.get('video_title')} » : "
             f"« {done.get('text')} »."]
    if done.get('verified'):
        lines.append(f"Relu sur YouTube : il y est, à l’identique. Lien : {done.get('url')}")
    else:
        lines.append(f"Attention : {done.get('verify_note') or 'non relu sur YouTube'}. Lien : {done.get('url')}")
    if card.get('drafted_by') == 'local_model':
        lines.append(f"Texte rédigé par l’IA locale ({card.get('model')}).")
    if card.get('auto'):
        lines.append('Publié sans redemander : réglage « Publier sans redemander » de Paramètres › Studio.')
    lines.append(YOUTUBE_UNDO)
    return '\n'.join(lines)


def app_task(name: str, task: str) -> str:
    """9/11/2026, conversation 144 (« … utiliser l'application YouTube Manager » pour un commentaire): AURORA sait
    ouvrir l'application, pas encore s'en servir à la place de la personne. Elle le dit, puis ce qu'elle sait faire
    (PC_CONTROL_HELP, réutilisé)."""
    task = task if len(task) <= 120 else task[:119].rstrip() + '…'
    if re.search(r'commentaire|commenter', task, re.I):   # 9/11/2026: a YouTube comment, AURORA posts it herself
        # The gestures in the application stay as the fallback (CLAUDE.md: the old way is kept, linked to the new).
        return (f'Je sais ouvrir « {name} », mais pas encore m’en servir à votre place pour « {task} ». Un commentaire '
                f'YouTube, en revanche, je peux le publier moi-même : {YOUTUBE_COMMENT_HELP}\nPour passer plutôt par '
                f'l’application, voici ce que je sais y faire. {PC_CONTROL_HELP}')
    return (f'Je sais ouvrir « {name} », mais pas encore m’en servir à votre place pour « {task} » : vous pouvez le '
            f'faire dans l’application, et je peux vous en rédiger le texte. {PC_CONTROL_HELP}')


def app_launch(slots: dict, result: dict | None, names: list[str]) -> str:
    """9/11/2026: one app of the allowed list opened by tools/apps.py (or why not). names: the list, for the help.
    slots['refused']: the name was said with a negation (nothing opened); slots['task']: what the person wants to do
    in it, said after the result (app_task)."""
    listed = ', '.join(f'« {n} »' for n in names)
    if slots.get('refused'):
        return f"D’accord : je n’ouvre pas « {slots['refused']} ». Rien n’a été lancé."
    if slots.get('command_syntax'):
        return ('« lance : … » est la forme d’une commande de terminal : écrivez « exécute la commande … » (terminal '
                'avec autorisation, Paramètres). Pour ouvrir une application de votre liste : « ouvre <son nom> ». '
                'Rien n’a été lancé.')
    if not slots.get('app'):
        if not names:
            return ('Votre liste d’applications autorisées est vide : ajoutez-y une application dans Paramètres › '
                    'Applications autorisées (son nom et le chemin de son programme), puis dites « ouvre <son nom> ».')
        return f'Dites quelle application ouvrir, par son nom : {listed}. {APPS_WHERE}'
    if result is None:
        return 'Rien n’a été lancé.'
    if not result.get('ok'):
        why = str(result.get('error') or result.get('summary') or 'Ouverture impossible.')
        if (result.get('data') or {}).get('unknown'):
            why = f'{why} {APPS_WHERE}'
        return why if 'rien' in why.lower() else f'{why} Rien n’a été lancé.'
    if slots.get('task'):
        name = (result.get('data') or {}).get('name') or slots['app']
        return f"{_summary(result)}\n{app_task(name, str(slots['task']))}"
    return _summary(result)


def project_sizing(slots: dict, result: dict | None) -> str:
    """13/11/2026, « les calculs en premier »: what the project's own calculation program answers to a requirement
    said in one sentence (tools/projets.py). Every figure is the program's: nothing is added or rounded here."""
    if not slots.get('autonomie_min'):
        mass = fr_num(slots.get('masse_kg'))
        return (f'Pour calculer un robot de {mass} kg, dites aussi l’autonomie voulue : par exemple « conçois-moi un '
                f'robot quadrupède de {mass} kg avec une heure d’autonomie ». Aucun calcul n’a été fait.')
    if result is None:
        return 'Aucun calcul n’a été fait.'
    if not result.get('ok'):
        return str(result.get('error') or result.get('summary') or 'Calcul impossible : aucun chiffre n’est donné.')
    data = result.get('data') or {}
    lines = [f"D’après le programme de calcul du projet « {data.get('titre')} » ({data.get('programme')}) : "
             f"{data.get('resume') or data.get('verdict')}"]
    lines += [f"- {'Tenu' if line.get('etat') == 'tenue' else 'Non tenu'} : {line.get('quoi')}. {line.get('detail')}"
              for line in data.get('lignes') or []]
    lines.append('Ces chiffres viennent du programme du projet, jamais d’une IA ; rien n’a été fabriqué ni mesuré. Le '
                 'détail (couples, masse, ce que le calcul suppose) est dans la page « Projets ».')
    return '\n'.join(lines)


# --- memory, meta, navigation ----------------------------------------------------------

def memory_search(hits: list[dict], query: str) -> str:
    """query '' = listing of what is known about the user (not a full-text search)."""
    if not hits:
        if not query.strip():
            return 'Je ne sais encore rien de vous : aucun souvenir n’est enregistré (dites « retiens que … »).'
        return f'Je n’ai trouvé aucun souvenir correspondant à « {query} ».'
    lines = [f'J’ai retrouvé {len(hits)} souvenir(s) pour « {query} » :' if query.strip() else
             f'Voici ce que je sais de vous ({len(hits)} souvenir(s) enregistré(s), épinglés d’abord) :']
    for i, hit in enumerate(hits[:6], 1):
        mem = hit.get('memory') or {}
        date = str(mem.get('created_at') or '')[:10]
        meta = ', '.join(x for x in (mem.get('category'), date, _verif(mem.get('verification'))) if x)
        snippet = str(hit.get('snippet') or mem.get('detail') or '').strip()
        if len(snippet) > 160:
            snippet = snippet[:157].rstrip() + '…'
        lines.append(f"{i}. {mem.get('title') or 'Sans titre'} ({meta})" + (f' — {snippet}' if snippet else ''))
    return '\n'.join(lines)


def _verif(value: Any) -> str:
    return {'verified': 'vérifié', 'unverified': 'non vérifié', 'user_confirmed': 'confirmé',
            'contradicted': 'contredit'}.get(str(value or ''), '')


def memory_saved(memory: dict | None, *, permission_status: str | None = None, error: str = '') -> str:
    if permission_status in ('denied', 'expired'):
        return ('Autorisation refusée' if permission_status == 'denied' else 'Autorisation expirée sans décision') \
            + ' : rien n’a été mémorisé.'
    if not memory:
        return 'Je n’ai pas pu enregistrer ce souvenir' + (f' : {error}' if error else '.')
    category = memory.get('category') or 'general'
    if memory.get('confidentiality') == 'sensitive':
        # A secret is never repeated in clear: the answer is displayed, kept in the history and may be read aloud.
        from ..security.redaction import SENSITIVE_TYPES, redact
        masked, findings = redact(str(memory.get('title') or ''))
        note = ('Souvenir sensible : il n’est pas répété en clair ici, et il n’est jamais joint au contexte '
                'envoyé à Claude.')
        if any(f['type'] in SENSITIVE_TYPES for f in findings):
            return f'C’est noté : « {masked} » (catégorie {category}). {note}'
        return f'C’est noté (catégorie {category}). {note}'
    return f"C’est noté : « {memory.get('title')} » (catégorie {category})."


MODE_NAMES = {'prive': 'privé', 'equilibre': 'équilibré', 'avance': 'avancé'}
# CLAUDE.md: « Si une fonction n'est pas encore faite, l'interface le dit clairement. »
NOT_YET_AVAILABLE = ('Pas encore disponible : cliquer sur un bouton désigné par son nom (la souris et le clavier se '
                     'commandent pour l’instant par une position en pixels et une fenêtre nommée), regarder l’écran '
                     'de moi-même, agenda et rappels qui se répètent, application de bureau (AURORA s’ouvre pour l’instant '
                     'dans le navigateur). Une image jointe ou une capture d’écran faite par ton clic est lue '
                     'sur ce PC par un modèle de vision local.')
_CAPS_TOPICS = (
    ('reminder', r'\b(?:rappelle moi|rappelez moi|reveille moi|reveillez moi|rappel|rappels|alarme|alarmes|reveil|'
                 r'minuteur)\b'),
    ('pc', r'\b(?:souris|clavier)\b'),   # 9/11/2026: tools/pc_control.py, answered with the real setting
    ('screen', r'\b(?:mon ecran|l ecran|capture d ecran|captures d ecran|screenshot|vision de l ecran)\b'),
    ('terminal', r'\b(?:terminal|terminaux|lignes? de commande|invite de commandes?|powershell|console|shell|cmd)\b'),
    ('internet', r'\b(?:internet|web|en ligne|online|offline|connecte|connectee|connexion|reseau|hors ligne|net)\b'),
    ('models', r'\b(?:modele|modeles|ia|llm|ollama|claude|anthropic|qwen|cloud|moteur|moteurs|cerveau)\b'),
    ('mode', r'\bmode\b'),
)


def _caps_topic(question: str) -> str:
    """Which part of the capability state the question is about (it is answered first)."""
    import re
    try:
        from .normalize import normalize
        norm = normalize(question)
    except Exception:
        norm = (question or '').lower()
    from .rules import asks_answer_author
    if asks_answer_author(norm):   # « c'est toi ou Claude qui a écrit ce code ? »: read on the run, before 'models'
        return 'author'
    for topic, pattern in _CAPS_TOPICS:
        if re.search(pattern, norm):
            return topic
    return 'general'


def answer_author_line(author: dict | None) -> str:
    """Who wrote an earlier answer, as its run recorded it (orchestrator._answer_author), never a model's guess.
    None: this conversation has no earlier answer."""
    if not author:
        return ('Je ne trouve aucune réponse précédente dans cette conversation : il n’y a donc rien dont je puisse '
                'dire qui l’a rédigé.')
    from ..memory.recall import shorten, when   # lazy: the memory module imports the core one
    excerpt = shorten(' '.join(str(author.get('excerpt') or '').split()), 70)
    which = 'La dernière réponse contenant du code' if author.get('code') else 'La réponse précédente'
    which += f" (« {excerpt} », {when(author.get('at'))})" if excerpt else ''
    model = f" ({author['model']})" if author.get('model') else ''
    writer = author.get('written_by')
    if writer == 'cloud':
        return f'{which} a été rédigée par Claude{model}, le modèle d’Anthropic dans le cloud, et non par l’IA locale.'
    if writer == 'local':
        return f'{which} a été rédigée par l’IA locale{model}, sur ce PC, et non par Claude.'
    if author.get('tools'):
        return (f'{which} n’a été rédigée par aucune IA : AURORA l’a composée à partir de ses outils '
                f"({', '.join(author['tools'])}).")
    if author.get('deterministic'):
        return f'{which} n’a été rédigée par aucune IA : c’est un texte qu’AURORA compose elle-même.'
    return f'{which} ne garde aucune trace de qui l’a rédigée : je ne peux pas le dire.'


def capabilities_answer(caps: dict, question: str = '', *, author: dict | None = None) -> str:
    """Real capability state (measured just before), with the part the question is about first.
    ``author``: the earlier answer asked about (topic 'author'), None when the conversation has none."""
    ollama = caps.get('ollama') or {}
    cloud = caps.get('cloud') or {}
    topic = _caps_topic(question)
    if topic == 'reminder':
        # 16/11/2026 (C3): reminders exist (tools/reminders.py). A question about them is answered here; the order
        # itself (« rappelle-moi à 18 h de… ») goes to the tool, which says what it kept.
        return ('Oui : je sais programmer un rappel, un minuteur ou un réveil. Dites par exemple « rappelle-moi à 18 h '
                'd’appeler Paul », « rappelle-moi dans 20 minutes de sortir le plat », « minuteur de 10 minutes » ou '
                '« réveille-moi à 7 h ». Le moment venu, il s’affiche dans AURORA et il est lu à voix haute si la voix '
                'est activée ; AURORA doit être démarrée, sinon il est dit à son prochain démarrage. « Quels sont mes '
                'rappels ? » les liste, « annule le minuteur » en retire un. Rien n’a été programmé par cette réponse. '
                'Pas encore : un rappel qui se répète, ni l’agenda.')
    from .capabilities import capability_lines, pc_control_state, terminal_state
    if topic == 'screen':   # E2 (9/11/2026): the screen is read on a click only; acting on it: the real setting
        return ('Je ne regarde pas ton écran de moi-même. Clique sur le bouton « Capture d’écran », à côté du '
                'trombone : l’écran est capturé et lu sur ce PC par le modèle de vision local (rien ne part '
                'ailleurs), tu vois ce qui a été lu, puis tu poses ta question. Une image se joint avec le '
                'trombone. ' + pc_control_state(caps))
    if caps.get('internet'):
        tools = 'via mes outils (recherche web SearXNG et Wikipedia, Open-Meteo, USGS, flux RSS) ; je ne navigue pas librement.'
        internet = (f'Oui, j’ai accès à Internet {tools}' if topic == 'internet'
                    else f'Internet : accessible {tools}')
        if caps.get('mode') == 'prive':
            internet += ' En mode privé, chaque requête web demande votre autorisation.'
    elif caps.get('internet') is None and caps.get('mode') == 'prive':
        internet = ('Internet : non vérifié (mode privé, aucune connexion n’est testée) ; chaque requête web de mes '
                    'outils demande votre autorisation.')
    else:
        internet = 'Internet est indisponible en ce moment.'
    lines = {
        # Every capability, one by one, from the registry (29/10/2026: the old sentence named only six of them).
        'general': ('Je suis AURORA, votre assistant local. Voici ce que je sais faire :\n'
                    + '\n'.join(f'- {line}' for line in capability_lines())
                    + '\nToute action sensible demande votre autorisation.'),
        'internet': internet,
        'models': ((f"IA locale : Ollama actif, modèle {ollama.get('chat_model')} "
                    f"({len(ollama.get('models') or [])} modèle(s) installé(s)).") if ollama.get('up')
                   else 'IA locale : Ollama ne répond pas.')
                  + ((' Claude (Anthropic) : configuré, mais jamais utilisé en mode privé. Les réponses rédigées viennent '
                      'de l’IA locale.' if caps.get('mode') == 'prive' else
                      # CLAUDE.md (04/11/2026): the local model answers first; a tier chosen in the bar does not
                      # put Claude first.
                      ' Claude (Anthropic) : configuré ; il rédige la réponse quand vous choisissez le moteur « Claude » '
                      '(ou dites « utilise Claude »), ou en secours quand l’IA locale échoue ou ne répond pas, toujours '
                      'après votre accord pour l’envoi. Sinon, la réponse est rédigée par l’IA locale.')
                     if cloud.get('configured')
                     else ' Claude (Anthropic) : non configuré. Les réponses rédigées viennent de l’IA locale.'),
        'memory': ('Recherche sémantique en mémoire : prête.' if ollama.get('embed_ready')
                   else 'Recherche sémantique en mémoire : indisponible (recherche plein texte seulement).'),
        'mode': f"Mode actif : {MODE_NAMES.get(caps.get('mode'), caps.get('mode'))}.",
        'terminal': terminal_state(caps),
        'pc': pc_control_state(caps),
        'later': NOT_YET_AVAILABLE,
    }
    if topic == 'author':   # the answer asked about, then who writes in general
        return answer_author_line(author) + '\nEn général : ' + lines['models']
    if topic != 'general':   # a precise question (model, Internet, mode): its answer and the short state, no list
        order = [topic] + [k for k in ('internet', 'models', 'mode') if k != topic]
        return '\n'.join(lines[k] for k in order) + '\nPour tout ce que je sais faire, demandez « que sais-tu faire ? ».'
    order = ['general', 'internet', 'models', 'memory', 'terminal', 'pc', 'mode', 'later']
    return '\n'.join(lines[k] for k in order)


def unhandled_note(intents: list[str]) -> str:
    """Honest line for the parts of a compound request that were not performed."""
    try:
        from .intents import intent_label
    except Exception:
        intent_label = str
    labels = ', '.join(f'« {intent_label(i)} »' for i in intents)
    return (f'Non traité dans cette demande : {labels}. Rien n’a été fait pour cette partie : '
            'reformulez-la dans une demande séparée.')


_GLOBE_LAYER_LABELS = {'meteo': 'la météo', 'evenements': 'les catastrophes en cours',
                       'actualites': 'les actualités du monde', 'avions': 'les avions', 'satellites': 'les satellites',
                       'guide': 'le guide des pays', 'routes': 'la carte routière'}
_GLOBE_SATELLITE_NAMES = {25544: 'l’ISS', 48274: 'la station Tiangong', 20580: 'Hubble', 49260: 'Landsat 9'}


def navigate(page: str | None, globe: dict | None = None) -> str:
    if page == 'world' and globe:
        # 08/11/2026: « montre-moi Paris sur le globe ». The place is looked up by the globe in its own lists.
        text = 'J’ouvre le globe'
        if globe.get('satellite') in _GLOBE_SATELLITE_NAMES:
            text += f' sur {_GLOBE_SATELLITE_NAMES[globe["satellite"]]}'
        elif globe.get('couche') in _GLOBE_LAYER_LABELS:
            text += f' avec {_GLOBE_LAYER_LABELS[globe["couche"]]}'
        if isinstance(globe.get('lieu'), str) and globe['lieu']:
            from .normalize import globe_site
            site = globe_site(globe['lieu'])
            if site:   # « où est Pétra ? » : the country comes from the globe's own list (Wikidata), not a model
                where = f' ({site["pays"]})' if site.get('pays') else ''   # « au Pérou », « en Jordanie » : no guess
                return f'{text}, centré sur {site["nom"]}{where}, un lieu du patrimoine mondial de l’UNESCO.'
            text += (f', centré sur « {globe["lieu"].title()} » s’il est dans ses pays, ses villes ou ses lieux du '
                     'patrimoine mondial')
        return text + '.'
    if page in PAGE_LABELS:
        return f'J’ouvre la page « {PAGE_LABELS[page]} ».'
    return 'Je n’ai pas reconnu la page demandée : rien n’a été ouvert.'


# --- 3D lab of the home page (08/11/2026, reviews/accueil-holographique.md § 3) -------------------
# Answers read from the validated description given by lab.scene (never from a model). An illustration is always
# named « illustration pédagogique » and never presented as a simulation (cahier § 8).

def _scene_part(scene: dict, kind: str, item_id: str | None) -> dict | None:
    items = scene.get('pieces' if kind == 'piece' else 'groupes') or []
    return next((i for i in items if i.get('id') == item_id), None) if item_id else None


def _scene_explodes(scene: dict) -> bool:
    """A piece of the scene moves away in the exploded view (false for a model imported as one block)."""
    return any(p.get('eclate') for p in scene.get('pieces') or [])


def _scene_examples(scene: dict) -> str:
    """Three commands to try: take the scene apart, show a group, explain it. A scene of one block (an imported
    model) has nothing to take apart."""
    if not _scene_explodes(scene):
        if scene.get('origine') in ('importe', 'reconstruit'):   # a kept model: it can be stood up and named
            return 'Essayez : « redresse-le », « tourne-le », « renomme-le en … », « reviens au noyau ».'
        return 'Essayez : « recentre la vue », « explique son fonctionnement », « reviens au noyau ».'
    groups = [g for g in scene.get('groupes') or [] if g.get('nom')]
    plural = next((g for g in groups if str(g['nom']).endswith('s')), None)
    show = (f'montre les {plural["nom"][0].lower()}{plural["nom"][1:]}' if plural
            else f'montre « {groups[0]["nom"]} »' if groups else 'recentre la vue')
    return f'Essayez : « démonte-le », « {show} », « explique son fonctionnement ».'


def scene_view(order: dict, result: dict | None) -> tuple[str, dict | None]:
    """Answer and interface order of a scene command (slots['scene'] of ui.navigate) from the lab.scene result:
    (answer, {'navigate': 'home', 'scene': {scene, action, piece?, groupe?, suite?}}) ; no order when nothing changes
    (unknown piece, nothing selected, a fixed piece to animate, a scene that cannot be read). Several commands in
    one sentence (order['suite']): one answer each, in the order said, and the orders that change something."""
    from ..tools.base import fr_list            # lazy: tools are owned by another module
    from ..tools.lab import MODEL_ORIGINS, kind_label, rename_plan
    if not result or not result.get('ok') or not isinstance(result.get('data'), dict):
        reason = str((result or {}).get('error') or 'description illisible.')
        return f'Scène 3D indisponible : {reason}', None
    scene = result['data']
    # « illustration pédagogique », or « … générée automatiquement » for a scene built on request (phase 4)
    titre, nature = scene.get('titre') or scene.get('id'), kind_label(scene)
    if order.get('suite'):
        tag, texts, uis = f' ({nature})', [], []
        for one in ({k: v for k, v in order.items() if k != 'suite'}, *order['suite']):
            text, ui = scene_view(one, result)
            texts.append(text.replace(tag, '') if any(tag in t for t in texts) else text)   # said once
            if ui:
                uis.append(ui['scene'])
                if one.get('action') == 'renommer':   # the next commands of the sentence speak of its new name
                    result = {**result, 'data': {**result['data'], 'titre': ui['scene']['titre']}}
        joined = ('\n\n' if any('\n' in t for t in texts) else ' ').join(texts)
        if not uis:
            return joined, None
        first = {**uis[0], 'suite': [{k: v for k, v in u.items() if k != 'scene'} for u in uis[1:]]}
        return joined, {'navigate': 'home', 'scene': first if first['suite'] else uis[0]}
    illustration = scene.get('nature') == 'illustration'
    action = order.get('action') or 'ouvrir'
    piece = _scene_part(scene, 'piece', order.get('piece'))
    groupe = _scene_part(scene, 'groupe', order.get('groupe'))
    target = piece or groupe
    name = f'« {target["nom"]} »' if target else f'« {titre} »'
    ui_order = {'scene': scene.get('id'), 'action': action}
    if piece:
        ui_order['piece'] = piece['id']
    elif groupe:
        ui_order['groupe'] = groupe['id']
    ui = {'navigate': 'home', 'scene': ui_order}

    if order.get('sans_selection'):
        return (f'Aucune pièce n’est sélectionnée dans « {titre} » : sélectionnez-en une dans la scène, ou nommez-la '
                '(par exemple « agrandis le coude »).'), None
    if 'inconnu' in order:
        wanted = f'« {order["inconnu"]} »' if order.get('inconnu') else 'cette pièce'
        pieces = fr_list([str(p.get('nom')) for p in scene.get('pieces') or []])
        groups = fr_list([str(g.get('nom')) for g in scene.get('groupes') or []])
        text = f'Je ne trouve pas {wanted} dans « {titre} ». Ses pièces : {pieces}.'
        return text + (f' Ses groupes : {groups}.' if groups else ''), None

    if action in ('redresser', 'tourner', 'renommer'):
        # 10/11/2026 (ID Studio IA : « redresser et renommer »): only a kept model takes these; the interface applies the
        # change through the service (tools/lab.adjust_model) and shows the model again.
        if scene.get('origine') not in MODEL_ORIGINS:
            return (f'« {titre} » n’est pas un modèle que vous avez donné : seul un modèle importé, ou une forme '
                    'reconstruite depuis une photo, se redresse, se tourne ou se renomme.'), None
        if action == 'renommer':
            if not order.get('titre'):
                return f'Quel nom donner à « {titre} » ? Dites par exemple « renomme-le en Veste VITA ».', None
            named, _mots, why = rename_plan(scene, order['titre'])
            if why:
                return f'« {titre} » garde son nom : {why}.', None
            if named == titre:
                return f'« {titre} » porte déjà ce nom.', None
            return (f'Je renomme « {titre} » en « {named} ». Pour le rouvrir plus tard : « montre-moi '
                    f'{named[0].lower()}{named[1:]} ».'), {'navigate': 'home', 'scene': {**ui_order, 'titre': named}}
        if action == 'redresser':
            return (f'Je redresse « {titre} » : je le pose dans une autre position (il y en a six). Si ce n’est pas '
                    'encore la bonne, redites « redresse-le ». La position est gardée pour la prochaine fois.'), ui
        return (f'Je tourne « {titre} » d’un quart de tour sur lui-même. La position est gardée pour la prochaine '
                'fois.'), ui

    tag = f' ({nature})'
    if action == 'ouvrir':
        parts = [f'J’affiche « {titre} » sur l’accueil{tag}.', str(scene.get('resume') or '')]
        if scene.get('avertissement'):
            parts.append(str(scene['avertissement']))
        parts.append(_scene_examples(scene))
        return '\n\n'.join(p for p in parts if p), ui
    if action == 'expliquer':
        note = f'{nature[0].upper()}{nature[1:]}' + (', pas une simulation.' if illustration else '.')
        if piece:
            props = [f"- {p.get('nom')} : {p.get('valeur')}" for p in piece.get('proprietes') or []]
            text = f'{piece["nom"]} : {piece.get("fonction") or ""}'
            text += ('\n' + '\n'.join(props)) if props else ''
        elif groupe:
            members = [p for p in scene.get('pieces') or [] if p.get('groupe') == groupe['id']]
            text = f'{groupe["nom"]} ({len(members)} pièce{"s" if len(members) > 1 else ""}) :'
            text += ''.join(f'\n- {p.get("nom")} : {p.get("fonction") or ""}' for p in members)
        else:
            text = f'{titre} : {scene.get("fonctionnement") or scene.get("resume") or ""}'
        return f'{text}\n\n{note}', ui
    if action == 'animer':
        moving = [p for p in (scene.get('pieces') or [])
                  if p.get('articulation') and (piece is None or p is piece)
                  and (groupe is None or p.get('groupe') == groupe['id'])]
        if not moving:
            # 11/11/2026 (ID Studio IA : « mets-moi des animations dans les modèles que tu peux »): a kept model is one block,
            # without any joint; « anime-le » turns it on its stand (frontend labo/scene.ts), and the answer says so.
            if not target and any((p.get('forme') or {}).get('type') == 'maillage' for p in scene.get('pieces') or []):
                return (f'Je fais tourner « {titre} » sur son socle, comme sur un présentoir. Ce modèle est en un seul '
                        'bloc : ce n’est pas le mouvement de l’objet.'), ui
            return (f'{name} est fixe : aucune articulation à animer. Demandez « fais bouger le bras » pour animer '
                    'toute la scène.' if target else f'« {titre} » n’a aucune articulation à animer.'), None
        what = name if piece else (f'les {len(moving)} articulation{"s" if len(moving) > 1 else ""} de {name}')
        animation = f'{nature} : le principe du mouvement, pas une simulation' if illustration else nature
        return f'J’anime {what} ({animation}).', ui
    sentences = {
        'eclater': f'Je démonte {name} en vue éclatée : chaque pièce s’écarte de son support{tag}.',
        'assembler': f'Je remonte {name} : chaque pièce reprend sa place{tag}.',
        'arreter': f'J’arrête l’animation de « {titre} »{tag}.',
        'recentrer': f'Je recentre la vue sur « {titre} »{tag}.',
        'noyau': f'Je ferme « {titre} » et je reviens au noyau.',
        'montrer': f'Je mets en évidence {name}{tag}.',
        'masquer': f'Je masque {name}{tag}.',
        'isoler': f'J’isole {name} : les autres pièces sont masquées{tag}.',
        'tout_montrer': f'J’affiche à nouveau toutes les pièces de « {titre} »{tag}.',
        'agrandir': f'J’agrandis {name}{tag}.',
        'interieur': f'Je montre l’intérieur de {name} en transparence{tag}.',
    }
    if action in ('montrer', 'masquer', 'isoler', 'agrandir') and target is None:
        return f'Quelle pièce de « {titre} » ? Nommez-la (par exemple « le coude » ou « la pince »).', None
    if action in ('eclater', 'assembler') and not _scene_explodes(scene):   # nothing would move: said, not promised
        return f'« {titre} » est en un seul bloc : il n’y a aucune pièce à écarter{tag}.', None
    text = sentences.get(action)
    return (text, ui) if text else (f'Commande de scène non reconnue : rien n’a changé dans « {titre} ».', None)


def scene_absent(subject: str, catalogue: dict | None, raison: str | None = None) -> str:
    """An object asked in 3D that has no scene, and none could be built (phase 4): said honestly, with why
    (``raison``) and the scenes that exist (lab.catalogue). No subject: the user is asked to name the object."""
    from ..tools.lab import available_text   # lazy: tools are owned by another module
    scenes = ((catalogue or {}).get('data') or {}).get('scenes') if (catalogue or {}).get('ok') else None
    listed = available_text(scenes) if isinstance(scenes, list) else 'Catalogue des scènes 3D illisible.'
    example = ''
    if isinstance(scenes, list) and scenes and scenes[0].get('mots'):
        example = f' Par exemple : « montre-moi un {scenes[0]["mots"][0]} ».'
    if not subject:
        return (f'Quel objet voulez-vous voir en 3D ? Nommez-le (« montre-moi un moteur électrique en 3D ») : '
                f'AURORA en construit une maquette simplifiée. {listed}{example}')
    why = str(raison or '').strip().rstrip('.')
    why = f' : {why}' if why else ''
    return (f'Je n’ai pas pu construire de scène 3D pour « {subject} »{why}. Aucune scène ne s’ouvre. '
            f'{listed}{example}')


# --- dispatcher ------------------------------------------------------------------------

def deterministic(intent: str, slots: dict, results: dict) -> str | None:
    """Template answer for measure / lookup intents answered by a single tool."""
    if intent == 'system.status':
        return system_status(slots, results)
    if intent == 'system.processes':
        return processes(slots, results)
    if intent == 'system.diagnose':
        return diagnose(slots, results)
    if intent == 'weather.current':
        res = results.get('weather.current')
        return _summary(res) + '\nSource : Open-Meteo.' if res and res.get('ok') else failure_text('Météo', res)
    if intent == 'quakes.recent':
        return quakes(slots, results)
    if intent == 'calc.evaluate':
        return calc(slots, results, 'calc.evaluate')
    if intent == 'calc.convert':
        return calc(slots, results, 'calc.convert')
    if intent == 'time.now':
        res = results.get('time.now')
        if res and not res.get('ok') and (res.get('data') or {}).get('local'):
            return str(res.get('error') or res.get('summary'))   # the tool's own honest message
        return _summary(res) if res and res.get('ok') else failure_text('Heure', res)
    if intent == 'reminder.set':   # 16/11/2026: the tool's own sentence says exactly what was kept, or that nothing was
        res = results.get('reminder.set')
        return _summary(res) if res and res.get('ok') else failure_text('Rappel', res)
    if intent == 'sim.orbit':
        res = results.get('sim.orbit')
        return (_summary(res) + '\nCalcul : intégration numérique SciPy.') if res and res.get('ok') \
            else failure_text('Simulation orbitale', res)
    if intent == 'sim.solar_system':
        res = results.get('sim.solar_system')
        return _summary(res) if res and res.get('ok') else failure_text('Système solaire', res)
    if intent == 'news.latest':
        if 'news.world' in results:
            return world_news_answer(results.get('news.world'))
        return search_results(results.get('news.latest'), 'Actualités')
    if intent == 'web.search':
        return search_results(results.get('web.search'), 'Recherche web')
    if intent == 'files.scan':
        return files_scan(slots, results)
    return None


def widget_for(results_in_order: list[tuple[str, dict]]) -> dict | None:
    """UI widget of the last successful widget-bearing tool."""
    for tool, res in reversed(results_in_order):
        if tool in WIDGETS and res.get('ok'):
            return {'widget': WIDGETS[tool], 'data': res.get('data')}
    return None


# --------------------------------------------------------------------------- the tab of the subject (08/11/2026)

# The page that shows a tool's subject. After the answer AURORA opens it by itself: « ui.auto » marks it, and the
# interface follows it only while the setting auto_open_tab is on (ID Studio IA : « toujours, avec un réglage pour couper »).
# Memory, files, the terminal and web searches stay where the user is: their result, or their permission card, is there.
AUTO_PAGE_TOOLS = (
    (re.compile(r'^(?:weather|quakes|events|sky)\.|^news\.world$'), 'world'),
    (re.compile(r'^system\.(?:snapshot|diagnose|processes|history)$'), 'computer'),
    (re.compile(r'^studio\.'), 'studio'),
)


def auto_page(results_in_order: list[tuple[str, dict]]) -> str | None:
    """Page of the last successful tool that has one; None: the answer stays where it is."""
    for tool, res in reversed(results_in_order):
        if not res.get('ok'):
            continue
        for pattern, page in AUTO_PAGE_TOOLS:
            if pattern.search(tool):
                return page
    return None


def _focus(lat: Any, lon: Any, nom: Any = None) -> dict | None:
    """A point of the globe from a tool's measured coordinates (never from a model)."""
    if isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (int, float)) \
            or not isinstance(lon, (int, float)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    point: dict = {'lat': float(lat), 'lon': float(lon)}
    if isinstance(nom, str) and nom.strip():
        point['nom'] = nom.strip()[:80]
    return point


def _order(couche: str, focus: dict | None = None, **extra: Any) -> dict:
    order: dict = {'couche': couche}
    if focus:
        order['focus'] = focus
    order.update({k: v for k, v in extra.items() if v not in (None, '')})
    return order


def globe_order(results_in_order: list[tuple[str, dict]]) -> dict | None:
    """What the globe of Monde 3D shows after the request (lib/types.ts GlobeOrder): the layer of the last
    successful world tool and, when its data has one, the place it is about."""
    for tool, res in reversed(results_in_order):
        if not res.get('ok'):
            continue
        data = res.get('data') if isinstance(res.get('data'), dict) else {}
        if tool == 'weather.current':
            return _order('meteo', _focus(data.get('latitude'), data.get('longitude'), data.get('city')))
        if tool.startswith('weather.'):
            return _order('meteo')
        if tool == 'quakes.recent':
            strongest = data.get('strongest') if isinstance(data.get('strongest'), dict) else {}
            return _order('evenements', _focus(strongest.get('lat'), strongest.get('lon'), strongest.get('place')))
        if tool == 'events.active':
            items = [e for e in data.get('evenements') or [] if isinstance(e, dict)]
            first = items[0] if items else {}
            return _order('evenements', _focus(first.get('lat'), first.get('lon'), first.get('titre')))
        if tool == 'news.world':
            asked = data.get('demande') if isinstance(data.get('demande'), dict) else {}
            return _order('actualites', theme=asked.get('theme'), pays=asked.get('pays'))
        if tool == 'sky.planes':
            lieu = data.get('lieu') if isinstance(data.get('lieu'), dict) else {}
            return _order('avions', _focus(lieu.get('lat'), lieu.get('lon'), lieu.get('nom')))
        if tool == 'sky.traffic':
            return _order('avions')
        if tool == 'sky.satellites':
            return _order('satellites')
    return None


# --------------------------------------------------------------------------- P3: analysis of a table

DATA_NO_TABLE = ('Aucun tableau à analyser : joins un fichier CSV au message, ou nomme un fichier .csv de tes '
                 'dossiers autorisés (Documents, Téléchargements, Bureau), par exemple « Analyse '
                 'Documents\\depenses.csv ».')
ROUTINE_STEP_MAX = 300   # tasks/routines.MAX_STEP


def _data(ordered: list[tuple[str, dict]], tool: str) -> list[dict]:
    """The data of every successful run of an analysis tool, in the order of the run."""
    return [r.get('data') or {} for t, r in ordered if t == tool and r.get('ok')]


def _signed(value: Any, digits: int = 1) -> str:
    return ('+' if isinstance(value, (int, float)) and value > 0 else '') + fr_num(value, digits)


def _period_words(period: dict) -> str:
    """« septembre 2026 » for the month « 2026-09 » (core/prompts.MONTHS); any other period keeps its label."""
    from .prompts import MONTHS
    key = str(period.get('period') or '')
    if len(key) == 7 and key[4] == '-' and key[:4].isdigit() and key[5:].isdigit() and 1 <= int(key[5:]) <= 12:
        return f'{MONTHS[int(key[5:]) - 1]} {key[:4]}'
    return str(period.get('label') or key)


def data_summary(ordered: list[tuple[str, dict]]) -> str:
    """The answer when no model wrote one: each calculation in one sentence, read on its result."""
    lines = [r.get('summary') for t, r in ordered if t.startswith('data.') and r.get('ok') and r.get('summary')]
    failed = [f"{t} : {r.get('error')}" for t, r in ordered if t.startswith('data.') and not r.get('ok')]
    text = 'Analyse faite par AURORA (calculs du code, sans texte d’IA) :\n' + '\n'.join(f'- {x}' for x in lines)
    if failed:
        text += '\nCalculs impossibles : ' + ' ; '.join(failed)
    return text


def _data_actions(ordered: list[tuple[str, dict]], source: str, table: dict) -> list[dict]:
    """Proposed actions, each with its proof (the figure and the calculation) and what it would do. Rules only:
    nothing is guessed by an AI, and nothing runs without the user's click (and its confirmation)."""
    actions: list[dict] = []
    name = table.get('name') or 'le fichier'
    for out in _data(ordered, 'data.outliers')[:1]:
        if out.get('count'):
            shown = out['outliers'][:5]
            lines = ', '.join(f"ligne {o['line']} ({fr_num(o['value'], 2)})" for o in shown)
            actions.append({
                'id': 'outliers', 'kind': 'info',
                'title': f"Vérifier {out['count']} valeur(s) inhabituelle(s) de « {out['column']} »",
                'proof': f"{lines}{' …' if out['count'] > len(shown) else ''}. {out['calc']}",
                'confidence': 'élevée' if any(o['level'] == 'fort' for o in out['outliers']) else 'moyenne',
                'effect': 'Rien n’est modifié : vérifie ces lignes dans ton fichier (erreur de saisie, dépense '
                          'exceptionnelle…).'})
    for cmp in _data(ordered, 'data.compare')[:1]:
        a, b = cmp['first'], cmp['second']
        # A rise in size of a same-sign amount: an expense written −600 then −155 went DOWN (−74 % in size), it is
        # not « +74 % »; an income turned into an expense compares nothing.
        rising = [c for c in cmp.get('changes') or []
                  if isinstance(c.get('first'), (int, float)) and isinstance(c.get('second'), (int, float))
                  and c['first'] * c['second'] > 0 and abs(c['second']) >= 1.2 * abs(c['first'])]
        if rising and not cmp.get('incomplete'):
            c = {**rising[0], 'diff_pct': (abs(rising[0]['second']) - abs(rising[0]['first'])) / abs(rising[0]['first']) * 100}
            # Sent as « Retiens que … »: no abbreviation (« sept. » would end its first sentence, the title of the
            # memory) and no « .csv » (a file name in the sentence would also ask for an analysis).
            fact = (f"« {c['group']} » ({cmp['value'] or 'nombre de lignes'}) est passé de {fr_num(c['first'], 2)} à "
                    f"{fr_num(c['second'], 2)} entre {_period_words(a)} et {_period_words(b)} "
                    f"({_signed(c['diff_pct'])} %), d’après le fichier « {name.rsplit('.', 1)[0]} »")
            actions.append({
                'id': 'compare', 'kind': 'memory', 'memory_text': fact,
                'title': f"Surveiller « {c['group']} » : {_signed(c['diff_pct'])} % en {b['label']}",
                'proof': f"{fr_num(c['first'], 2)} en {a['label']}, {fr_num(c['second'], 2)} en {b['label']}. {cmp['calc']}",
                'confidence': 'moyenne',
                'effect': 'Retenir ce constat dans ta mémoire (une phrase envoyée comme si tu la tapais), pour le '
                          'comparer le mois prochain. Deux périodes seulement : ce n’est pas encore une tendance.'})
    for tr in _data(ordered, 'data.trend')[:1]:
        if tr.get('direction') in ('hausse', 'baisse') and (tr.get('r2') or 0) >= 0.5 and \
                abs(tr.get('slope_pct') or 0) >= 10:
            actions.append({
                'id': 'trend', 'kind': 'info',
                'title': f"Tendance à la {tr['direction']} : {_signed(tr['slope_pct'])} % par {tr['period']} en moyenne",
                'proof': f"Pente {fr_num(tr['slope_per_period'], 2)} par {tr['period']}, R² = {fr_num(tr['r2'], 2)}, "
                         f"de {tr['first_complete']} à {tr['last_complete']}. {tr['calc']}",
                'confidence': 'élevée' if (tr.get('r2') or 0) >= 0.8 else 'moyenne',
                'effect': 'Rien n’est modifié : une tendance passée ne dit pas la suite avec certitude.'})
    gaps = [c for c in table.get('columns') or [] if table.get('rows') and c.get('missing', 0) / table['rows'] >= 0.1]
    if gaps:
        c = max(gaps, key=lambda g: g['missing'])
        actions.append({
            'id': 'missing', 'kind': 'info',
            'title': f"Compléter la colonne « {c['name']} » : {c['missing']} case(s) vide(s)",
            'proof': f"{c['missing']} sur {table['rows']} lignes ({fr_num(c['missing'] / table['rows'] * 100, 0)} %) ; "
                     'les calculs ignorent ces cases.', 'confidence': 'élevée',
            'effect': 'Rien n’est modifié : les résultats seront plus sûrs une fois le fichier complété.'})
    if table.get('duplicates'):
        actions.append({
            'id': 'duplicates', 'kind': 'info', 'title': f"Vérifier {table['duplicates']} ligne(s) en double",
            'proof': f"{table['duplicates']} ligne(s) identique(s) à une autre, colonne par colonne.",
            'confidence': 'élevée',
            'effect': 'Rien n’est modifié : un doublon gonfle les sommes et les comptes s’il n’est pas voulu.'})
    kind, _sep, path = source.partition(':')
    step = f'Analyse le fichier {path}'
    if kind == 'fichier' and len(step) <= ROUTINE_STEP_MAX:
        stem = name.rsplit('.', 1)[0]
        actions.append({
            'id': 'routine', 'kind': 'routine', 'routine': {'name': f'Analyse {stem}'[:60], 'steps': [step]},
            'title': 'Refaire cette analyse plus tard, en une phrase',
            'proof': f"« {name} » est dans un dossier autorisé : AURORA peut le relire quand il change.",
            'confidence': 'élevée',
            'effect': f"Crée la routine « {f'Analyse {stem}'[:60]} » (une étape : « {step} »). Elle ne se lance que "
                      'quand tu le demandes.'})
    return actions


def data_view(ordered: list[tuple[str, dict]], source: str) -> dict | None:
    """RunResult.ui.data of a P3 analysis: the table, up to six key figures and the proposed actions."""
    table = next(iter(_data(ordered, 'data.describe')), None)
    if not table:
        return None
    missing = sum(c.get('missing') or 0 for c in table.get('columns') or [])
    highlights = [{'label': 'Lignes', 'value': table.get('rows'), 'unit': '', 'digits': 0,
                   'note': f"{len(table.get('columns') or [])} colonnes"}]
    if missing:
        highlights.append({'label': 'Cases vides', 'value': missing, 'unit': '', 'digits': 0, 'note': 'toutes colonnes'})
    for tr in _data(ordered, 'data.trend')[:1]:
        if tr.get('change_pct') is not None:
            highlights.append({'label': f"Évolution ({tr['period']})", 'value': tr['change_pct'], 'unit': '%',
                               'digits': 1, 'note': f"de {tr['first_complete']} à {tr['last_complete']}"})
    for cmp in _data(ordered, 'data.compare')[:1]:
        if cmp.get('diff_pct') is not None:
            highlights.append({'label': f"{cmp['second']['label']} / {cmp['first']['label']}", 'value': cmp['diff_pct'],
                               'unit': '%', 'digits': 1,
                               'note': f"{fr_num(cmp['second']['value'], 2)} contre {fr_num(cmp['first']['value'], 2)}"})
    for grp in _data(ordered, 'data.group')[:1]:
        if grp.get('groups'):
            top = grp['groups'][0]
            highlights.append({'label': f"En tête : {top['group']}"[:60], 'value': top['value'], 'unit': '',
                               'digits': 2, 'note': f"{fr_num(top.get('share'), 1)} % du total"
                               if top.get('share') is not None else grp['calc'][:120]})
    for out in _data(ordered, 'data.outliers')[:1]:
        highlights.append({'label': 'Valeurs anormales', 'value': out.get('count'), 'unit': '', 'digits': 0,
                           'note': f"« {out['column']} »"})
    kind, _sep, path = source.partition(':')
    return {'dataset': {'name': table.get('name'), 'source': 'fichier' if kind == 'fichier' else 'jointe',
                        'path': path if kind == 'fichier' else None, 'rows': table.get('rows'),
                        'columns': len(table.get('columns') or []), 'skipped': table.get('skipped') or 0},
            'highlights': highlights[:6], 'actions': _data_actions(ordered, source, table)}
