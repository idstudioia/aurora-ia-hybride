"""Stopping a user process (action ``process.terminate``, permission required).

The orchestrator only calls this tool after the user granted the permission; this module
adds its own guards on top: critical/system processes, AURORA itself (this process, its
parents, the Vite dev server, the venv's Python), Ollama and other users' processes are
always refused. ``dry_run=True`` lists what would be stopped, for the permission preview.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psutil
from pydantic import Field, model_validator

from .base import Params, Source, ToolError, ToolResult, fr_list, now_iso, tool

# Lower-case names without '.exe'.
PROTECTED_NAMES = frozenset({
    'system', 'idle', 'system idle process', 'registry', 'memory compression', 'memcompression',
    'secure system', 'smss', 'csrss', 'wininit', 'services', 'lsass', 'lsaiso', 'winlogon',
    'explorer', 'svchost', 'dwm', 'fontdrvhost', 'sihost', 'ctfmon', 'spoolsv', 'audiodg',
    'msmpeng', 'nissrv', 'securityhealthservice', 'mpdefendercoreservice', 'wudfhost',
    'conhost', 'runtimebroker', 'taskhostw', 'dllhost', 'wmiprvse', 'searchindexer', 'searchhost',
    'startmenuexperiencehost', 'shellexperiencehost', 'textinputhost', 'applicationframehost',
    'lockapp', 'logonui', 'userinit',
    'ollama', 'ollama app', 'ollama_llama_server',
})
PROTECTED_PIDS = frozenset({0, 4})
MAX_TARGETS = 64
WAIT_S = 3.0

# Common French / product names -> executable name.
ALIASES = {
    'google chrome': 'chrome', 'chrome': 'chrome', 'microsoft edge': 'msedge', 'edge': 'msedge',
    'mozilla firefox': 'firefox', 'firefox': 'firefox', 'word': 'winword', 'microsoft word': 'winword',
    'excel': 'excel', 'powerpoint': 'powerpnt', 'outlook': 'outlook', 'teams': 'ms-teams',
    'visual studio code': 'code', 'vs code': 'code', 'vscode': 'code', 'bloc-notes': 'notepad',
    'bloc notes': 'notepad', 'blocnotes': 'notepad', 'spotify': 'spotify', 'discord': 'discord',
    'steam': 'steam', 'vlc': 'vlc', 'opera': 'opera', 'brave': 'brave', 'zoom': 'zoom',
}


def norm_name(name: str) -> str:
    n = (name or '').strip().lower()
    return n[:-4] if n.endswith('.exe') else n


def _current_user() -> str | None:
    try:
        return psutil.Process().username()
    except (psutil.Error, OSError):
        return None


def aurora_pids() -> set[int]:
    """This process and all its ancestors (service, launcher, terminal...)."""
    pids = {os.getpid()}
    try:
        pids.update(p.pid for p in psutil.Process().parents())
    except psutil.Error:
        pass
    return pids


def _venv_root() -> Path:
    return Path(sys.prefix).resolve()


def _is_aurora_runtime(proc: psutil.Process, name: str) -> bool:
    """Vite / node dev server, or a Python running from AURORA's venv."""
    try:
        if name == 'node':
            cmd = ' '.join(proc.cmdline()).lower()
            return 'vite' in cmd or 'aurora' in cmd
        if name in ('python', 'pythonw', 'py'):
            venv = _venv_root()
            # On Windows the venv's python.exe is a launcher that starts the base interpreter
            # as its child: check the process and its parent.
            for candidate in (proc, proc.parent()):
                if candidate is None:
                    continue
                try:
                    exe = candidate.exe()
                except (psutil.Error, OSError):
                    continue
                if exe and Path(exe).resolve().is_relative_to(venv):
                    return True
            return 'aurora' in ' '.join(proc.cmdline()).lower()
    except (psutil.Error, OSError, ValueError):
        return name == 'node'  # unreadable node command line: assume it may be Vite
    return False


def refusal_reason(proc: psutil.Process, *, user: str | None, protected_pids: set[int]) -> str | None:
    """French reason why ``proc`` must not be stopped, or None if allowed."""
    try:
        raw_name = proc.name()
    except psutil.NoSuchProcess:
        return 'processus déjà terminé'
    except psutil.Error:
        return 'processus inaccessible (droits insuffisants)'
    name = norm_name(raw_name)
    if proc.pid in PROTECTED_PIDS or name in PROTECTED_NAMES:
        return f'{raw_name} est un processus système ou critique protégé'
    if proc.pid in protected_pids:
        return f'{raw_name} fait partie d\'AURORA ou de son lanceur'
    if _is_aurora_runtime(proc, name):
        return f'{raw_name} fait partie de l\'environnement d\'AURORA'
    try:
        owner = proc.username()
    except (psutil.Error, OSError):
        owner = None
    if owner is None or user is None:
        return f'propriétaire de {raw_name} inconnu : arrêt refusé par prudence'
    if owner.lower() != user.lower():
        return f'{raw_name} appartient à un autre utilisateur ({owner})'
    return None


def _describe(proc: psutil.Process) -> dict:
    info = {'pid': proc.pid, 'name': None, 'memory_mb': None, 'create_time': None}
    try:
        info['name'] = proc.name()
        info['create_time'] = round(proc.create_time(), 3)   # with the PID: identifies THIS process
        info['memory_mb'] = round(proc.memory_info().rss / 1024 ** 2, 1)
    except psutil.Error:
        pass
    return info


def _same_process(proc: psutil.Process, approved: list[dict]) -> bool:
    """True if ``proc`` is one of the processes shown in the approved preview (pid, name, start time)."""
    try:
        name, started = norm_name(proc.name()), proc.create_time()
    except psutil.Error:
        return False
    for item in approved:
        if int(item.get('pid') or -1) != proc.pid or norm_name(str(item.get('name') or '')) != name:
            continue
        if item.get('create_time') is None or abs(float(item['create_time']) - started) < 0.01:
            return True
    return False


def plan(pid: int | None = None, name: str | None = None) -> dict:
    """Resolve the request into allowed targets and refusals, without touching anything."""
    user = _current_user()
    protected = aurora_pids()
    candidates: list[psutil.Process] = []
    wanted = None
    if pid is not None:
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            raise ToolError(f'Aucun processus avec le PID {pid}.') from None
        except psutil.Error:
            raise ToolError(f'Processus {pid} inaccessible.') from None
        if name:
            wanted = norm_name(ALIASES.get(name.strip().lower(), name))
            try:
                actual = norm_name(proc.name())
            except psutil.Error:
                actual = ''
            if actual != wanted:
                raise ToolError(f'Le PID {pid} ne correspond pas à « {name} » (processus actuel : {actual or "inconnu"}). '
                                'Rien n\'a été arrêté.')
        candidates.append(proc)
    else:
        wanted = norm_name(ALIASES.get(name.strip().lower(), name)) if name else ''
        if not wanted:
            raise ToolError('Indiquez un nom de programme ou un PID.')
        for proc in psutil.process_iter(['name']):
            pname = proc.info.get('name')
            if pname and norm_name(pname) == wanted:
                candidates.append(proc)
        if not candidates:
            raise ToolError(f'Aucun processus nommé « {name} » en cours d\'exécution.')
    targets, refused = [], []
    for proc in candidates:
        reason = refusal_reason(proc, user=user, protected_pids=protected)
        if reason:
            refused.append({**_describe(proc), 'reason': reason})
        else:
            targets.append(proc)
    if len(targets) > MAX_TARGETS:
        raise ToolError(f'{len(targets)} processus correspondent : au-delà de {MAX_TARGETS}, l\'arrêt groupé est refusé.')
    return {'targets': targets, 'refused': refused, 'user': user}


def _permission_granted(permission_id: int, action: str = 'process.terminate') -> bool:
    """``action``: 9/11/2026, also the gestures of tools/pc_control.py ('pc.control'), which add their own check
    that the granted card is the gesture done (same fingerprint)."""
    try:
        from ..security import consent  # lazy: owned by another module
        perm = consent.get(permission_id)
    except Exception:
        return False
    return bool(perm) and perm.get('status') == 'granted' and perm.get('action') == action


def terminate(pid: int | None = None, name: str | None = None, *, dry_run: bool = False,
              permission_id: int | None = None, wait_s: float = WAIT_S,
              approved: list[dict] | None = None) -> ToolResult:
    """approved: targets of the preview the user validated ({pid, name, create_time}); only those
    are stopped, a process of the same name started since then is refused."""
    sources = [Source(title='Gestion des processus (psutil)', kind='local', retrieved_at=now_iso())]
    if permission_id is not None and not dry_run and not _permission_granted(permission_id):
        raise ToolError('Autorisation absente ou non accordée pour arrêter ce processus : rien n\'a été fait.')
    resolved = plan(pid, name)
    targets, refused = resolved['targets'], resolved['refused']
    if approved is not None and not dry_run:
        kept = []
        for proc in targets:
            if _same_process(proc, approved):
                kept.append(proc)
            else:
                refused.append({**_describe(proc), 'reason': "absent de l'aperçu validé (non autorisé)"})
        targets = kept
    planned = [_describe(p) for p in targets]
    data = {'requested': {'pid': pid, 'name': name}, 'dry_run': dry_run, 'targets': planned,
            'refused': refused, 'terminated': [], 'still_running': []}
    label = name or (planned[0]['name'] if planned else f'PID {pid}')
    if not targets:
        reasons = fr_list(sorted({r['reason'] for r in refused}))
        msg = f'Arrêt refusé : {reasons}. Rien n\'a été arrêté.'
        return ToolResult(ok=False, data=data, summary=msg, provenance='psutil', sources=sources, error=msg)
    if dry_run:
        summary = f'{len(targets)} processus « {label} » seraient arrêtés'
        if refused:
            summary += f' ({len(refused)} protégé(s) seraient épargnés)'
        return ToolResult(ok=True, data=data, summary=summary + '.', provenance='psutil', sources=sources)
    for proc in targets:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            refused.append({**_describe(proc), 'reason': 'accès refusé par Windows'})
    alive_targets = [p for p in targets if not any(r['pid'] == p.pid for r in refused)]
    gone, alive = psutil.wait_procs(alive_targets, timeout=wait_s)
    data['terminated'] = [{'pid': p.pid, 'name': next((t['name'] for t in planned if t['pid'] == p.pid), None)}
                          for p in gone]
    data['still_running'] = [_describe(p) for p in alive]
    data['refused'] = refused
    parts = []
    if gone:
        parts.append(f'{len(gone)} processus « {label} » arrêté(s)')
    if alive:
        parts.append(f'{len(alive)} toujours actif(s) après {wait_s:g} s')
    if refused:
        parts.append(f'{len(refused)} épargné(s) par protection')
    summary = fr_list(parts) + '.'
    ok = bool(gone)
    return ToolResult(ok=ok, data=data, summary=summary[0].upper() + summary[1:], provenance='psutil',
                      sources=sources, error=None if ok else summary)


class TerminateParams(Params):
    pid: int | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, max_length=120)
    dry_run: bool = False
    permission_id: int | None = None
    approved: list[dict] | None = Field(default=None, max_length=MAX_TARGETS)   # targets of the validated preview

    @model_validator(mode='after')
    def _need_target(self):
        if self.pid is None and not (self.name or '').strip():
            raise ValueError('indiquez un nom de programme ou un PID')
        return self


@tool('process.terminate', description="Arrête un programme de l'utilisateur (permission obligatoire, "
      "processus système/AURORA/autres utilisateurs refusés).", action='process.terminate',
      params=TerminateParams, provenance='psutil', timeout_s=20)
def terminate_tool(p: TerminateParams) -> ToolResult:
    return terminate(p.pid, p.name, dry_run=p.dry_run, permission_id=p.permission_id, approved=p.approved)
