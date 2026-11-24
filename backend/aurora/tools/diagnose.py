"""Rule-based PC diagnosis: snapshot + recent history + top processes.

Successor of the starter's /api/tools/diagnostic (one CPU/RAM/disk line): same promise,
"mesure ponctuelle, sans réparation", with explicit rules, evidence, probable causes and
suggestions. Nothing is modified; a suggested action (e.g. process.terminate) is only a
proposal that goes through the permission flow.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from .base import Params, Source, ToolResult, fr_duration, fr_list, fr_num, now_iso, tool

SEVERITY_ORDER = {'critical': 0, 'warning': 1, 'info': 2}

CPU_WINDOW_S = 60
CPU_MIN_POINTS = 5
CPU_SUSTAINED_CRIT = 85.0
CPU_SUSTAINED_WARN = 65.0
CPU_INSTANT_WARN = 90.0
RAM_CRIT = 92.0
RAM_WARN = 80.0
SWAP_WARN = 75.0
DISK_FREE_CRIT_GB = 5.0
DISK_FREE_WARN_GB = 15.0
DISK_PCT_CRIT = 98.0
DISK_PCT_WARN = 95.0
DISK_PCT_INFO = 90.0
PROC_CPU_WARN = 25.0          # share of the whole machine
HISTORY_KEPT = 150            # last points of system.history kept with the result (curve of the P2 page)
PROC_CPU_CONTRIB = 8.0
PROC_MEM_WARN_MB = 2048.0
PROC_MEM_WARN_SHARE = 0.15    # of total RAM
UPTIME_WARN_S = 14 * 86400
UPTIME_INFO_S = 5 * 86400
PROC_COUNT_WARN = 400
PROC_COUNT_INFO = 300
TEMP_WARN_C = 90.0
BATTERY_WARN = 15.0

ANTIVIRUS_NAMES = {'msmpeng', 'mpdefendercoreservice', 'nissrv'}


def _parse_t(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        return None


def _recent_cpu(points: list[dict], window_s: float = CPU_WINDOW_S) -> list[float]:
    if not points:
        return []
    last = _parse_t(points[-1].get('t', ''))
    out = []
    for pt in points:
        if pt.get('cpu') is None:
            continue
        t = _parse_t(pt.get('t', ''))
        if last is None or t is None or (last - t).total_seconds() <= window_s:
            out.append(float(pt['cpu']))
    return out


def _norm(name: str | None) -> str:
    n = (name or '').strip().lower()
    return n[:-4] if n.endswith('.exe') else n


def _default_protected(proc: dict) -> bool:
    try:
        from .processes import PROTECTED_NAMES, PROTECTED_PIDS, aurora_pids
    except Exception:
        return True
    return (proc.get('pid') in PROTECTED_PIDS or _norm(proc.get('name')) in PROTECTED_NAMES
            or proc.get('pid') in aurora_pids())


def _finding(fid: str, severity: str, title: str, detail: str, evidence: dict) -> dict:
    return {'id': fid, 'severity': severity, 'title': title, 'detail': detail, 'evidence': evidence}


def analyze(snap: dict, points: list[dict] | None = None, procs: list[dict] | None = None, *,
            current_user: str | None = None,
            is_protected: Callable[[dict], bool] | None = None) -> dict:
    """Pure function: same inputs, same diagnosis (deterministic French text)."""
    points = points or []
    procs = procs or []
    is_protected = is_protected or _default_protected
    findings: list[dict] = []
    causes: list[dict] = []
    suggestions: list[dict] = []
    total_ram_mb = (snap.get('ram_total_gb') or 0) * 1024

    # ---- CPU (sustained if enough history, otherwise instantaneous)
    recent = _recent_cpu(points)
    cpu_high = False
    if len(recent) >= CPU_MIN_POINTS:
        mean, peak = sum(recent) / len(recent), max(recent)
        ev = {'cpu_mean': round(mean, 1), 'cpu_max': round(peak, 1), 'points': len(recent), 'window_s': CPU_WINDOW_S}
        if mean >= CPU_SUSTAINED_CRIT:
            findings.append(_finding('cpu.sustained', 'critical', 'Processeur saturé de façon soutenue',
                                     f'CPU moyen {fr_num(mean, 0)} % sur la dernière minute (max {fr_num(peak, 0)} %).', ev))
            cpu_high = True
        elif mean >= CPU_SUSTAINED_WARN:
            findings.append(_finding('cpu.sustained', 'warning', 'Processeur fortement sollicité',
                                     f'CPU moyen {fr_num(mean, 0)} % sur la dernière minute (max {fr_num(peak, 0)} %).', ev))
            cpu_high = True
    elif (snap.get('cpu_percent') or 0) >= CPU_INSTANT_WARN:
        findings.append(_finding('cpu.instant', 'warning', 'Processeur très sollicité (mesure ponctuelle)',
                                 f"CPU à {fr_num(snap['cpu_percent'], 0)} % au moment de la mesure ; "
                                 "l'historique est trop court pour dire si c'est durable.",
                                 {'cpu_percent': snap['cpu_percent'], 'history_points': len(recent)}))
        cpu_high = True

    # ---- RAM and swap
    ram = snap.get('ram_percent') or 0
    ram_ev = {'ram_percent': ram, 'ram_used_gb': snap.get('ram_used_gb'), 'ram_total_gb': snap.get('ram_total_gb'),
              'ram_available_gb': snap.get('ram_available_gb')}
    ram_high = False
    if ram >= RAM_CRIT:
        findings.append(_finding('ram.pressure', 'critical', 'Mémoire vive presque saturée',
                                 f"RAM utilisée à {fr_num(ram, 0)} % ({fr_num(snap.get('ram_available_gb'), 1)} Go disponibles).", ram_ev))
        ram_high = True
    elif ram >= RAM_WARN:
        findings.append(_finding('ram.pressure', 'warning', 'Mémoire vive sous pression',
                                 f"RAM utilisée à {fr_num(ram, 0)} % ({fr_num(snap.get('ram_available_gb'), 1)} Go disponibles).", ram_ev))
        ram_high = True
    swap = snap.get('swap_percent')
    if swap is not None and swap >= SWAP_WARN and ram >= 70:
        findings.append(_finding('swap.pressure', 'warning', "Fichier d'échange très sollicité",
                                 f"Mémoire virtuelle utilisée à {fr_num(swap, 0)} % alors que la RAM est à {fr_num(ram, 0)} %.",
                                 {'swap_percent': swap, 'swap_used_gb': snap.get('swap_used_gb'), 'ram_percent': ram}))
        causes.append({'title': 'La RAM ne suffit plus : Windows déplace de la mémoire vers le disque, ce qui ralentit tout',
                       'short': 'manque de mémoire vive', 'confidence': 'élevée', 'related': ['swap.pressure']})

    # ---- Disks
    disk_list = snap.get('disks') or [{'mountpoint': snap.get('disk_path', ''), 'percent': snap.get('disk_percent'),
                                       'free_gb': snap.get('disk_free_gb'), 'total_gb': snap.get('disk_total_gb')}]
    disk_problem = False
    for d in disk_list:
        pct, free = d.get('percent'), d.get('free_gb')
        if pct is None or free is None:
            continue
        ev = {'mountpoint': d.get('mountpoint'), 'percent': pct, 'free_gb': free, 'total_gb': d.get('total_gb')}
        detail = f"{d.get('mountpoint')} : {fr_num(free, 1)} Go libres ({fr_num(pct, 0)} % occupé)."
        fid = f"disk.{str(d.get('mountpoint', '')).strip(':/' + chr(92)).lower() or 'root'}"
        if free < DISK_FREE_CRIT_GB or pct >= DISK_PCT_CRIT:
            findings.append(_finding(fid, 'critical', f"Disque {d.get('mountpoint')} presque plein", detail, ev))
            disk_problem = True
        elif free < DISK_FREE_WARN_GB or pct >= DISK_PCT_WARN:
            findings.append(_finding(fid, 'warning', f"Disque {d.get('mountpoint')} presque plein", detail, ev))
            disk_problem = True
        elif pct >= DISK_PCT_INFO:
            findings.append(_finding(fid, 'info', f"Disque {d.get('mountpoint')} bien rempli", detail, ev))

    # ---- Processes
    seen: set[int] = set()
    cpu_hogs, mem_hogs, contributors = [], [], []
    mem_threshold = max(PROC_MEM_WARN_MB, PROC_MEM_WARN_SHARE * total_ram_mb) if total_ram_mb else PROC_MEM_WARN_MB
    for p in procs:
        if p.get('pid') in seen:
            continue
        seen.add(p.get('pid'))
        cpu, mem = p.get('cpu_percent') or 0, p.get('memory_mb') or 0
        if cpu >= PROC_CPU_WARN:
            cpu_hogs.append(p)
        elif cpu_high and cpu >= PROC_CPU_CONTRIB:
            contributors.append(p)
        if mem >= mem_threshold:
            mem_hogs.append(p)
    cpu_hogs.sort(key=lambda p: -(p.get('cpu_percent') or 0))
    mem_hogs.sort(key=lambda p: -(p.get('memory_mb') or 0))
    for p in cpu_hogs[:3]:
        findings.append(_finding(f"proc.cpu.{p['pid']}", 'warning', f"Processus gourmand en CPU : {p['name']}",
                                 f"{p['name']} (PID {p['pid']}) utilise {fr_num(p['cpu_percent'], 0)} % du processeur.",
                                 {'pid': p['pid'], 'name': p['name'], 'cpu_percent': p['cpu_percent'],
                                  'memory_mb': p.get('memory_mb')}))
    for p in mem_hogs[:3]:
        findings.append(_finding(f"proc.mem.{p['pid']}", 'warning' if ram_high else 'info',
                                 f"Processus gourmand en mémoire : {p['name']}",
                                 f"{p['name']} (PID {p['pid']}) occupe {fr_num(p['memory_mb'] / 1024, 1)} Go de RAM.",
                                 {'pid': p['pid'], 'name': p['name'], 'memory_mb': p['memory_mb']}))

    # ---- Uptime, process count, sensors, battery
    uptime = snap.get('uptime_s') or 0
    if uptime >= UPTIME_INFO_S:
        sev = 'warning' if uptime >= UPTIME_WARN_S else 'info'
        findings.append(_finding('uptime.long', sev, 'Ordinateur allumé depuis longtemps',
                                 f'Dernier démarrage il y a {fr_duration(uptime)}.', {'uptime_s': uptime}))
    count = snap.get('process_count') or 0
    if count >= PROC_COUNT_INFO:
        sev = 'warning' if count >= PROC_COUNT_WARN else 'info'
        findings.append(_finding('proc.count', sev, 'Nombre élevé de processus',
                                 f'{count} processus en cours d\'exécution.', {'process_count': count}))
    for chip, temp in (snap.get('temperatures') or {}).items():
        if temp >= TEMP_WARN_C:
            findings.append(_finding(f'temp.{chip}', 'warning', f'Température élevée ({chip})',
                                     f'{fr_num(temp, 0)} °C mesurés.', {'sensor': chip, 'celsius': temp}))
    gpu = snap.get('gpu') or {}
    if gpu.get('util_percent') is not None and gpu['util_percent'] >= 95:
        findings.append(_finding('gpu.busy', 'info', 'Carte graphique à pleine charge',
                                 f"GPU {gpu.get('name', '')} à {fr_num(gpu['util_percent'], 0)} %.",
                                 {'gpu_util_percent': gpu['util_percent']}))
    bat = snap.get('battery_percent')
    if bat is not None and bat <= BATTERY_WARN and not snap.get('battery_plugged'):
        findings.append(_finding('battery.low', 'warning', 'Batterie faible',
                                 f'{fr_num(bat, 0)} % restants, sur batterie.', {'battery_percent': bat}))

    # ---- Probable causes
    by_id = {f['id'] for f in findings}
    av = [p for p in cpu_hogs + contributors if _norm(p.get('name')) in ANTIVIRUS_NAMES]
    if cpu_high or cpu_hogs:
        if av:
            causes.append({'title': f"Analyse antivirus Microsoft Defender en cours ({av[0]['name']}, "
                                    f"{fr_num(av[0]['cpu_percent'], 0)} % du CPU)",
                           'short': 'analyse antivirus en cours', 'confidence': 'élevée',
                           'related': [f"proc.cpu.{av[0]['pid']}"]})
        for p in [p for p in cpu_hogs if p not in av][:2]:
            causes.append({'title': f"{p['name']} consomme {fr_num(p['cpu_percent'], 0)} % du processeur",
                           'short': f"{p['name']} sature le processeur", 'confidence': 'élevée',
                           'related': [f"proc.cpu.{p['pid']}"]})
        if cpu_high and not cpu_hogs:
            names = [p['name'] for p in contributors[:3]]
            title = ('Charge répartie entre plusieurs programmes' + (f' ({fr_list(names)})' if names else '')
                     + f' : aucun ne dépasse {fr_num(PROC_CPU_WARN, 0)} % à lui seul')
            causes.append({'title': title, 'short': 'processeur surchargé', 'confidence': 'moyenne',
                           'related': [f['id'] for f in findings if f['id'].startswith('cpu.')]})
    if ram_high:
        top = sorted([p for p in procs if p.get('memory_mb')], key=lambda p: -p['memory_mb'])
        heaviest, seen_pids = [], set()
        for p in top:  # procs may list a process twice (CPU and memory rankings)
            if p['pid'] not in seen_pids:
                heaviest.append(p)
                seen_pids.add(p['pid'])
            if len(heaviest) == 3:
                break
        if heaviest:
            total = sum(p['memory_mb'] for p in heaviest) / 1024
            used = snap.get('ram_used_gb') or 0
            causes.append({'title': f"{fr_list([p['name'] for p in heaviest])} occupent {fr_num(total, 1)} Go "
                                    "de RAM à eux seuls",
                           'short': 'mémoire vive saturée',
                           'confidence': 'élevée' if used and total >= 0.3 * used else 'moyenne',
                           'related': ['ram.pressure']})
    if disk_problem:
        causes.append({'title': "Espace disque faible : Windows manque de place pour la mémoire virtuelle, "
                                "les mises à jour et les fichiers temporaires",
                       'short': 'disque presque plein', 'confidence': 'moyenne',
                       'related': [f['id'] for f in findings if f['id'].startswith('disk.') and f['severity'] != 'info']})
    if 'uptime.long' in by_id:
        causes.append({'title': f'Session longue ({fr_duration(uptime)}) : programmes et mémoire accumulés '
                                'depuis le dernier démarrage',
                       'short': 'PC non redémarré depuis longtemps', 'confidence': 'faible', 'related': ['uptime.long']})
    if 'proc.count' in by_id:
        causes.append({'title': f'Nombreux programmes en arrière-plan ({count} processus)',
                       'short': 'trop de programmes en arrière-plan', 'confidence': 'faible',
                       'related': ['proc.count']})

    # ---- Suggestions (proposals only; nothing is executed here)
    def terminable(p: dict) -> bool:
        if is_protected(p):
            return False
        owner = p.get('username')
        return bool(owner and current_user and owner.lower() == current_user.lower())

    proposed: set[str] = set()
    # Memory hogs are only worth closing when RAM is actually under pressure.
    for p in cpu_hogs[:2] + (mem_hogs[:2] if ram_high else []):
        key = _norm(p.get('name'))
        if key in proposed:
            continue
        proposed.add(key)
        related = [f"proc.cpu.{p['pid']}"] if p in cpu_hogs else ['ram.pressure', f"proc.mem.{p['pid']}"]
        if _norm(p.get('name')) in ANTIVIRUS_NAMES:
            suggestions.append({'title': "Laisser l'analyse antivirus se terminer",
                                'benefit': 'le processeur redevient disponible à la fin de l\'analyse',
                                'risk': 'aucun', 'action': None, 'related': related})
            continue
        if not terminable(p):
            continue
        gain = []
        if (p.get('cpu_percent') or 0) >= PROC_CPU_CONTRIB:
            gain.append(f"environ {fr_num(p['cpu_percent'], 0)} % du CPU")
        if p.get('memory_mb'):
            gain.append(f"environ {fr_num(p['memory_mb'], 0)} Mo de RAM")
        suggestions.append({
            'title': f"Fermer {p['name']} (PID {p['pid']})",
            'benefit': 'libère ' + fr_list(gain) if gain else 'libère les ressources de ce programme',
            'risk': f"le travail non enregistré dans {p['name']} sera perdu",
            'action': {'tool': 'process.terminate', 'params': {'pid': p['pid'], 'name': p['name']},
                       'requires_permission': True, 'label': f"Arrêter {p['name']}"},
            'related': related,
        })
    if ram_high and not any(s['action'] for s in suggestions):
        suggestions.append({'title': 'Fermer les programmes et onglets de navigateur inutilisés',
                            'benefit': 'libère de la mémoire vive', 'risk': 'enregistrez votre travail avant',
                            'action': None, 'related': ['ram.pressure']})
    if disk_problem:
        disk_ids = [f['id'] for f in findings if f['id'].startswith('disk.') and f['severity'] != 'info']
        suggestions.append({'title': 'Rechercher les vieux fichiers volumineux dans Téléchargements',
                            'benefit': "identifier de l'espace récupérable (analyse en lecture seule)",
                            'risk': 'aucun : rien n\'est déplacé sans votre accord',
                            'action': {'tool': 'files.scan', 'params': {'folder': 'Téléchargements', 'older_than_days': 180,
                                                                        'min_size_mb': 50},
                                       'requires_permission': False, 'label': 'Analyser Téléchargements'},
                            'related': disk_ids})
        suggestions.append({'title': 'Lancer le Nettoyage de disque de Windows',
                            'benefit': 'supprime les fichiers temporaires et anciennes mises à jour',
                            'risk': 'faible (outil Windows, à lancer vous-même)', 'action': None,
                            'related': disk_ids})
    if 'uptime.long' in by_id:
        suggestions.append({'title': "Redémarrer l'ordinateur",
                            'benefit': 'repart avec une mémoire propre et applique les mises à jour en attente',
                            'risk': 'enregistrez et fermez vos documents avant', 'action': None,
                            'related': ['uptime.long']})

    findings.sort(key=lambda f: SEVERITY_ORDER.get(f['severity'], 9))
    severity_of = {f['id']: SEVERITY_ORDER.get(f['severity'], 9) for f in findings}

    def rank(item: dict) -> int:
        return min((severity_of.get(r, 9) for r in item.get('related') or []), default=9)

    causes.sort(key=rank)          # stable sort: rule order kept within a severity
    suggestions.sort(key=rank)
    serious = [f for f in findings if f['severity'] in ('critical', 'warning')]
    status = 'critique' if any(f['severity'] == 'critical' for f in findings) else ('attention' if serious else 'ok')
    metrics = {'cpu_percent': snap.get('cpu_percent'), 'ram_percent': snap.get('ram_percent'),
               'disk_percent': snap.get('disk_percent'), 'disk_free_gb': snap.get('disk_free_gb'),
               'uptime_s': snap.get('uptime_s'), 'process_count': snap.get('process_count'),
               'history_points': len(points), 'cpu_recent_points': len(recent)}
    summary = _summary(status, findings, serious, causes, suggestions, snap)
    main_cause = causes[0]['short'] if causes and serious else ''
    return {'status': status, 'findings': findings, 'causes': causes, 'suggestions': suggestions,
            'main_cause': main_cause, 'metrics': metrics, 'summary': summary, 'modified': False}


def _summary(status, findings, serious, causes, suggestions, snap) -> str:
    base = (f"CPU {fr_num(snap.get('cpu_percent'), 0)} %, RAM {fr_num(snap.get('ram_percent'), 0)} %, "
            f"disque {fr_num(snap.get('disk_percent'), 0)} %")
    if not serious:
        text = f'Diagnostic : aucun problème majeur détecté ({base}).'
        infos = [f['title'] for f in findings if f['severity'] == 'info']
        if infos:
            text += f" Remarques : {fr_list([i[0].lower() + i[1:] for i in infos[:3]])}."
    else:
        n = len(serious)
        label = 'point critique' if status == 'critique' and n == 1 else ("points d'attention" if n > 1 else "point d'attention")
        text = f"Diagnostic : {n} {label} — {fr_list([f['title'] for f in serious[:3]])} ({base})."
        if causes:
            text += f" Cause la plus probable : {causes[0]['title']}."
        if suggestions:
            text += f" Suggestion : {suggestions[0]['title'][0].lower() + suggestions[0]['title'][1:]}."
    return text + " Aucune modification n'a été faite."


class DiagnoseParams(Params):
    include_processes: bool = True


@tool('system.diagnose', description="Diagnostic du PC par règles (CPU soutenu, RAM, disque, processus, "
      "temps de fonctionnement) avec causes probables et suggestions. Lecture seule.",
      action='system.read', params=DiagnoseParams, provenance='psutil', timeout_s=30)
def diagnose_tool(p: DiagnoseParams) -> ToolResult:
    from . import system
    from .processes import _current_user
    snap = system.snapshot()
    points = system.history()['points']
    procs: list[dict] = []
    if p.include_processes:
        every = system.processes(10_000, 'cpu')   # one enumeration, two rankings
        by_mem = sorted(every, key=lambda r: -(r.get('memory_mb') or 0))
        procs = every[:15] + by_mem[:15]
    result = analyze(snap, points, procs, current_user=_current_user())
    result['snapshot'] = snap
    result['top_processes'] = procs[:15]
    # The CPU/RAM curve the rules saw (P2 page of the diagnosis). Never put in the AI's prompt: the
    # orchestrator picks its own keys (_user_content), the agent's observation is cut well before it.
    result['history'] = [{'t': pt.get('t'), 'cpu': pt.get('cpu'), 'ram': pt.get('ram')} for pt in points[-HISTORY_KEPT:]]
    return ToolResult(ok=True, data=result, summary=result['summary'], provenance='psutil',
                      sources=[Source(title='Mesures locales (psutil) et règles de diagnostic AURORA',
                                      kind='local', retrieved_at=now_iso())])
