"""Read-only machine measurements (psutil).

Extends the ASTRA starter's ``system_status()`` (backend/aurora/_astra_main_reference.py):
same four keys (cpu_percent, ram_percent, disk_percent, ram_total_gb), plus the contract's
extended fields, a 300-point ring buffer fed by ``sample()`` and a process list.
Nothing here modifies the machine.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Literal

import psutil
from pydantic import Field, field_validator

from .base import Params, Source, ToolResult, fr_duration, fr_num, now_iso, tool

_GB = 1024 ** 3
_MB = 1024 ** 2
HISTORY_SIZE = 300
IS_WINDOWS = sys.platform == 'win32'


# --------------------------------------------------------------------------- CPU / network meters

def _busy_fraction(t1, t2) -> float:
    """Same arithmetic as psutil.cpu_percent, on two cpu_times() readings."""
    def total(t):
        tot = sum(t)
        return tot - getattr(t, 'guest', 0) - getattr(t, 'guest_nice', 0)

    def busy(t):
        return total(t) - t.idle - getattr(t, 'iowait', 0)

    dt = total(t2) - total(t1)
    if dt <= 0:
        return 0.0
    return min(max((busy(t2) - busy(t1)) / dt, 0.0), 1.0)


class _CpuMeter:
    """System CPU % from deltas of cpu_times(), shared by every thread.

    psutil.cpu_percent(interval=None) keeps one baseline *per thread*; tools run in a
    thread pool, so a single process-wide baseline is kept here instead. A reading
    covers at least MIN_WINDOW_S; closer calls return the previous value.
    """

    MIN_WINDOW_S = 0.5
    PRIME_S = 0.1

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._t = time.monotonic()
        self._times = psutil.cpu_times()
        self._last: float | None = None

    def percent(self) -> float:
        with self._lock:
            elapsed = time.monotonic() - self._t
            if self._last is not None and elapsed < self.MIN_WINDOW_S:
                return self._last
            if self._last is None and elapsed < self.PRIME_S:
                time.sleep(self.PRIME_S - elapsed)  # one-time priming right after import
            now, times = time.monotonic(), psutil.cpu_times()
            self._last = round(_busy_fraction(self._times, times) * 100, 1)
            self._t, self._times = now, times
            return self._last


class _NetMeter:
    """Network throughput (kbit/s) computed from successive net_io_counters()."""

    MIN_WINDOW_S = 0.5
    PRIME_S = 0.1

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._t = time.monotonic()
        self._io = self._read()
        self._last: tuple[float | None, float | None] | None = None

    @staticmethod
    def _read():
        try:
            return psutil.net_io_counters()
        except Exception:
            return None

    def rates(self) -> tuple[float | None, float | None]:
        with self._lock:
            elapsed = time.monotonic() - self._t
            if self._last is not None and elapsed < self.MIN_WINDOW_S:
                return self._last
            if self._last is None and elapsed < self.PRIME_S:
                time.sleep(self.PRIME_S - elapsed)
            now, io = time.monotonic(), self._read()
            if io is None or self._io is None:
                self._last = (None, None)
            else:
                dt = max(now - self._t, 1e-6)
                down = max(io.bytes_recv - self._io.bytes_recv, 0) * 8 / 1000 / dt
                up = max(io.bytes_sent - self._io.bytes_sent, 0) * 8 / 1000 / dt
                self._last = (round(down, 1), round(up, 1))
            self._t, self._io = now, io
            return self._last


_cpu = _CpuMeter()
_net = _NetMeter()


# --------------------------------------------------------------------------- GPU

_NVSMI_CANDIDATES = (
    r'C:\Windows\System32\nvidia-smi.exe',
    r'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe',
)
_gpu_lock = threading.Lock()
_gpu_state: dict = {'nvsmi': None, 'nvsmi_checked': False, 't': 0.0, 'value': None,
                    'win_static': None, 'win_checked': False, 'pdh': None, 'pdh_failed': False}
GPU_CACHE_S = 2.0


def _nvidia_smi_path() -> str | None:
    if not _gpu_state['nvsmi_checked']:
        found = shutil.which('nvidia-smi')
        if not found:
            found = next((p for p in _NVSMI_CANDIDATES if os.path.isfile(p)), None)
        _gpu_state['nvsmi'] = found
        _gpu_state['nvsmi_checked'] = True
    return _gpu_state['nvsmi']


def _num_or_none(text: str) -> float | None:
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        return None  # '[N/A]', '[Not Supported]'


def _query_nvidia(exe: str) -> list[dict] | None:
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        proc = subprocess.run(
            [exe, '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=2.5, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    gpus = []
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 5:
            continue
        gpus.append({'name': parts[0], 'util_percent': _num_or_none(parts[1]),
                     'memory_used_mb': _num_or_none(parts[2]), 'memory_total_mb': _num_or_none(parts[3]),
                     'temperature_c': _num_or_none(parts[4]), 'source': 'nvidia-smi'})
    return gpus or None


def _windows_gpu_static() -> dict | None:
    """Adapter name + dedicated VRAM from the display-class registry key (read only)."""
    if not IS_WINDOWS:
        return None
    try:
        import winreg
    except ImportError:
        return None
    path = r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
    skip = ('microsoft basic', 'remote', 'virtual', 'indirect', 'parsec', 'citrix')
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except OSError:
        return None
    best = None
    with root:
        index = 0
        while True:
            try:
                sub = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as key:
                    name = str(winreg.QueryValueEx(key, 'DriverDesc')[0])
                    if any(s in name.lower() for s in skip):
                        continue
                    vram = None
                    for value_name in ('HardwareInformation.qwMemorySize', 'HardwareInformation.MemorySize'):
                        try:
                            raw = winreg.QueryValueEx(key, value_name)[0]
                        except OSError:
                            continue
                        if isinstance(raw, bytes):
                            raw = int.from_bytes(raw[:8], 'little')
                        if isinstance(raw, int) and raw > 0:
                            vram = round(raw / _MB)
                            break
                    candidate = {'name': name, 'memory_total_mb': vram}
                    if best is None or (vram or 0) > (best['memory_total_mb'] or 0):
                        best = candidate
            except OSError:
                continue
    return best


class _PdhGpu:
    """GPU utilisation / dedicated memory via Windows performance counters (PDH).

    Mirrors Task Manager: per engine type, sum the utilisation of every instance, then take
    the busiest engine type. Rate counters need two collections, so the first value is None.
    """

    PDH_FMT_DOUBLE = 0x00000200
    PDH_FMT_NOCAP100 = 0x00008000
    PDH_MORE_DATA = 0x800007D2

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes

        class Value(ctypes.Structure):
            _fields_ = [('CStatus', wintypes.DWORD), ('doubleValue', ctypes.c_double)]

        class Item(ctypes.Structure):
            _fields_ = [('szName', wintypes.LPWSTR), ('FmtValue', Value)]

        self.Item = Item
        pdh = ctypes.WinDLL('pdh.dll')
        for fn in ('PdhOpenQueryW', 'PdhAddEnglishCounterW', 'PdhCollectQueryData',
                   'PdhGetFormattedCounterArrayW', 'PdhCloseQuery'):
            getattr(pdh, fn).restype = wintypes.DWORD
        pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        pdh.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t,
                                              ctypes.POINTER(wintypes.HANDLE)]
        pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        pdh.PdhGetFormattedCounterArrayW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                     ctypes.POINTER(wintypes.DWORD),
                                                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.pdh = pdh
        self.query = wintypes.HANDLE()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
            raise OSError('PdhOpenQuery')
        self.engine = wintypes.HANDLE()
        self.memory = wintypes.HANDLE()
        if pdh.PdhAddEnglishCounterW(self.query, r'\GPU Engine(*)\Utilization Percentage', 0,
                                     ctypes.byref(self.engine)) != 0:
            raise OSError('GPU Engine counter')
        if pdh.PdhAddEnglishCounterW(self.query, r'\GPU Adapter Memory(*)\Dedicated Usage', 0,
                                     ctypes.byref(self.memory)) != 0:
            self.memory = None
        if pdh.PdhCollectQueryData(self.query) != 0:
            raise OSError('PdhCollectQueryData')
        self.collected_at = time.monotonic()

    MIN_WINDOW_S = 0.5

    def _array(self, counter) -> list[tuple[str, float]]:
        ctypes, wintypes = self.ctypes, self.wintypes
        fmt = self.PDH_FMT_DOUBLE | self.PDH_FMT_NOCAP100
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = self.pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if status != self.PDH_MORE_DATA or size.value == 0:
            return []
        buf = (ctypes.c_byte * size.value)()
        status = self.pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), buf)
        if status != 0:
            return []
        items = ctypes.cast(buf, ctypes.POINTER(self.Item * count.value)).contents
        return [(it.szName or '', it.FmtValue.doubleValue) for it in items if it.FmtValue.CStatus in (0, 1)]

    def read(self) -> tuple[float | None, float | None]:
        if time.monotonic() - self.collected_at < self.MIN_WINDOW_S:
            # Rate counter: a window this short would read as 0 %. Report memory only.
            usage = [v for _, v in self._array(self.memory)] if self.memory is not None else []
            return None, (round(max(usage) / _MB) if usage else None)
        if self.pdh.PdhCollectQueryData(self.query) != 0:
            return None, None
        self.collected_at = time.monotonic()
        per_engine: dict[tuple[str, str], float] = {}
        for name, value in self._array(self.engine):
            luid = name.split('_phys')[0].split('luid_')[-1] if 'luid_' in name else ''
            engtype = name.split('engtype_')[-1] if 'engtype_' in name else ''
            per_engine[(luid, engtype)] = per_engine.get((luid, engtype), 0.0) + max(value, 0.0)
        util = round(min(max(per_engine.values()), 100.0), 1) if per_engine else None
        mem_mb = None
        if self.memory is not None:
            usage = [v for _, v in self._array(self.memory)]
            if usage:
                mem_mb = round(max(usage) / _MB)
        return util, mem_mb


def gpu_info() -> dict | None:
    """First GPU: nvidia-smi if present, else (Windows) registry + PDH counters, else None."""
    with _gpu_lock:
        now = time.monotonic()
        if _gpu_state['value'] is not None and now - _gpu_state['t'] < GPU_CACHE_S:
            return _gpu_state['value']
        value = None
        exe = _nvidia_smi_path()
        if exe:
            gpus = _query_nvidia(exe)
            if gpus:
                value = dict(gpus[0])
                if len(gpus) > 1:
                    value['others'] = gpus[1:]
        if value is None and IS_WINDOWS:
            if not _gpu_state['win_checked']:
                _gpu_state['win_static'] = _windows_gpu_static()
                _gpu_state['win_checked'] = True
            static = _gpu_state['win_static']
            if static is not None:
                util = mem = None
                if _gpu_state['pdh'] is None and not _gpu_state['pdh_failed']:
                    try:
                        _gpu_state['pdh'] = _PdhGpu()
                    except Exception:
                        _gpu_state['pdh_failed'] = True
                if _gpu_state['pdh'] is not None:
                    try:
                        util, mem = _gpu_state['pdh'].read()
                    except Exception:
                        _gpu_state['pdh'], _gpu_state['pdh_failed'] = None, True
                value = {'name': static['name'], 'util_percent': util, 'memory_used_mb': mem,
                         'memory_total_mb': static['memory_total_mb'], 'temperature_c': None,
                         'source': 'pdh' if _gpu_state['pdh'] is not None else 'registry'}
        _gpu_state['value'], _gpu_state['t'] = value, now
        return value


# --------------------------------------------------------------------------- snapshot

def _temperatures() -> dict | None:
    reader = getattr(psutil, 'sensors_temperatures', None)
    if reader is None:
        return None  # not provided by psutil on Windows
    try:
        sensors = reader() or {}
    except Exception:
        return None
    out = {}
    for chip, entries in sensors.items():
        values = [e.current for e in entries if e.current is not None]
        if values:
            out[chip] = round(max(values), 1)
    return out or None


def _battery() -> dict | None:
    try:
        bat = psutil.sensors_battery()
    except Exception:
        return None
    if bat is None:
        return None
    secs = bat.secsleft if isinstance(bat.secsleft, int) and bat.secsleft >= 0 else None
    return {'percent': round(bat.percent, 1), 'plugged': bat.power_plugged, 'secs_left': secs}


def disks() -> list[dict]:
    """Fixed local partitions with their usage."""
    out = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        return out
    for part in parts:
        opts = (part.opts or '').lower()
        if 'cdrom' in opts or (IS_WINDOWS and 'fixed' not in opts):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (OSError, PermissionError):
            continue
        out.append({'mountpoint': part.mountpoint, 'fstype': part.fstype,
                    'total_gb': round(usage.total / _GB, 1), 'used_gb': round(usage.used / _GB, 1),
                    'free_gb': round(usage.free / _GB, 1), 'percent': round(usage.percent, 1)})
    return out


def snapshot() -> dict:
    """Instant measurement, compatible with the starter's /api/system payload."""
    memory = psutil.virtual_memory()
    disk_path = os.path.abspath(os.sep)
    disk = psutil.disk_usage(disk_path)
    try:
        swap = psutil.swap_memory()
    except Exception:
        swap = None
    try:
        freq = psutil.cpu_freq()
        freq_mhz = round(freq.current) if freq and freq.current else None
    except Exception:
        freq_mhz = None
    boot = psutil.boot_time()
    down, up = _net.rates()
    battery = _battery()
    try:
        process_count = len(psutil.pids())
    except Exception:
        process_count = None
    return {
        # ASTRA starter keys (unchanged semantics)
        'cpu_percent': _cpu.percent(),
        'ram_percent': round(memory.percent, 1),
        'disk_percent': round(disk.percent, 1),
        'ram_total_gb': round(memory.total / _GB, 1),
        # contract extensions
        'ram_used_gb': round((memory.total - memory.available) / _GB, 1),
        'ram_available_gb': round(memory.available / _GB, 1),
        'disk_total_gb': round(disk.total / _GB, 1),
        'disk_used_gb': round(disk.used / _GB, 1),
        'disk_free_gb': round(disk.free / _GB, 1),
        'disk_path': disk_path,
        'net_down_kbps': down,
        'net_up_kbps': up,
        'net_unit': 'kbit/s',
        'cpu_count': psutil.cpu_count(logical=True),
        'cpu_count_physical': psutil.cpu_count(logical=False),
        'cpu_freq_mhz': freq_mhz,
        'uptime_s': int(time.time() - boot),
        'boot_time': datetime.fromtimestamp(boot, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'swap_percent': round(swap.percent, 1) if swap else None,
        'swap_used_gb': round(swap.used / _GB, 1) if swap else None,
        'swap_total_gb': round(swap.total / _GB, 1) if swap else None,
        'process_count': process_count,
        'battery_percent': battery['percent'] if battery else None,
        'battery_plugged': battery['plugged'] if battery else None,
        'gpu': gpu_info(),
        'temperatures': _temperatures(),
        'disks': disks(),
        'measured_at': now_iso(),
    }


# --------------------------------------------------------------------------- history

_history: deque = deque(maxlen=HISTORY_SIZE)
_history_lock = threading.Lock()


def sample() -> dict:
    """Append one point to the ring buffer (the service calls this every ~2 s)."""
    point = {'t': now_iso(), 'cpu': _cpu.percent(), 'ram': round(psutil.virtual_memory().percent, 1)}
    down, up = _net.rates()
    point['net_down_kbps'], point['net_up_kbps'] = down, up
    with _history_lock:
        _history.append(point)
    return point


def history(limit: int | None = None) -> dict:
    with _history_lock:
        points = list(_history)
    if limit is not None:
        points = points[-limit:] if limit > 0 else []
    return {'points': points}


def _clear_history() -> None:  # tests only
    with _history_lock:
        _history.clear()


# --------------------------------------------------------------------------- processes

_proc_lock = threading.Lock()
_proc_cache: dict[int, dict] = {}   # pid -> {'proc': Process, 'username': str|None, 'user_done': bool}
PRIME_SLEEP_S = 0.3


def _entry(pid: int) -> tuple[dict | None, bool]:
    cached = _proc_cache.get(pid)
    if cached is not None:
        try:
            if cached['proc'].is_running():
                return cached, False
        except psutil.Error:
            pass
    try:
        proc = psutil.Process(pid)
        proc.cpu_percent(None)  # prime: the next call returns usage since now
    except psutil.Error:
        return None, False
    except Exception:
        return None, False
    entry = {'proc': proc, 'username': None, 'user_done': False}
    _proc_cache[pid] = entry
    return entry, True


def _read(entry: dict, ncpu: int) -> dict | None:
    proc: psutil.Process = entry['proc']
    try:
        with proc.oneshot():
            name = proc.name()
            try:
                cpu = round(proc.cpu_percent(None) / ncpu, 1)
            except psutil.AccessDenied:
                cpu = None
            try:
                rss = proc.memory_info().rss
            except psutil.AccessDenied:
                rss = None
            if not entry['user_done']:
                try:
                    entry['username'] = proc.username()
                except (psutil.AccessDenied, OSError):
                    entry['username'] = None
                entry['user_done'] = True
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None
    except psutil.AccessDenied:
        return None
    return {'pid': proc.pid, 'name': name, 'cpu_percent': cpu,
            'memory_mb': round(rss / _MB, 1) if rss is not None else None,
            'username': entry['username']}


def processes(limit: int = 12, sort: str = 'cpu') -> list[dict]:
    """Top processes. cpu_percent is a share of the whole machine (all cores = 100 %)."""
    ncpu = psutil.cpu_count(logical=True) or 1
    with _proc_lock:
        pids = [p for p in psutil.pids() if p != 0]  # pid 0 = System Idle Process (idle time)
        entries, fresh = [], 0
        for pid in pids:
            entry, is_new = _entry(pid)
            if entry is not None:
                entries.append(entry)
                fresh += is_new
        live = set(pids)
        for pid in list(_proc_cache):
            if pid not in live:
                _proc_cache.pop(pid, None)
        if entries and fresh > len(entries) // 2:
            time.sleep(PRIME_SLEEP_S)  # first call: give cpu_percent a real measuring window
        rows = [r for r in (_read(e, ncpu) for e in entries) if r is not None]
    if sort == 'memory':
        rows.sort(key=lambda r: (r['memory_mb'] is None, -(r['memory_mb'] or 0)))
    else:
        rows.sort(key=lambda r: (r['cpu_percent'] is None, -(r['cpu_percent'] or 0), -(r['memory_mb'] or 0)))
    return rows[:max(1, limit)]


# --------------------------------------------------------------------------- summaries

def _gb(value: float | None) -> str:
    return f'{fr_num(value, 1)} Go'


def summarize_snapshot(snap: dict, metric: str = 'all') -> str:
    cpu = f"{fr_num(snap['cpu_percent'], 0)} %"
    if metric == 'cpu':
        freq = f", {fr_num(snap['cpu_freq_mhz'], 0)} MHz" if snap.get('cpu_freq_mhz') else ''
        return (f"Processeur : {cpu} d'utilisation sur {snap['cpu_count']} cœurs logiques{freq}.")
    if metric == 'ram':
        return (f"Mémoire vive : {_gb(snap['ram_used_gb'])} utilisés sur {_gb(snap['ram_total_gb'])} "
                f"({fr_num(snap['ram_percent'], 0)} %), {_gb(snap['ram_available_gb'])} disponibles.")
    if metric == 'disk':
        parts = [f"{d['mountpoint']} {fr_num(d['percent'], 0)} % occupé, {_gb(d['free_gb'])} libres sur {_gb(d['total_gb'])}"
                 for d in snap.get('disks') or []]
        if not parts:
            parts = [f"{snap['disk_path']} {fr_num(snap['disk_percent'], 0)} % occupé, {_gb(snap['disk_free_gb'])} libres"]
        return 'Disques : ' + ' ; '.join(parts) + '.'
    if metric == 'network':
        if snap.get('net_down_kbps') is None:
            return 'Débit réseau : mesure indisponible.'
        return (f"Réseau : {fr_num(snap['net_down_kbps'], 0)} kbit/s en réception, "
                f"{fr_num(snap['net_up_kbps'], 0)} kbit/s en émission.")
    if metric == 'gpu':
        gpu = snap.get('gpu')
        if not gpu:
            return "Aucune mesure GPU disponible sur cette machine (nvidia-smi absent, aucun compteur lisible)."
        util = f"{fr_num(gpu['util_percent'], 0)} % d'utilisation" if gpu.get('util_percent') is not None else "utilisation non mesurée"
        mem = ''
        if gpu.get('memory_total_mb'):
            used = f"{fr_num(gpu['memory_used_mb'], 0)} Mo / " if gpu.get('memory_used_mb') is not None else ''
            mem = f", mémoire {used}{fr_num(gpu['memory_total_mb'], 0)} Mo"
        return f"GPU {gpu['name']} : {util}{mem}."
    if metric == 'battery':
        if snap.get('battery_percent') is None:
            return 'Aucune batterie détectée (PC fixe ou information indisponible).'
        plugged = 'sur secteur' if snap.get('battery_plugged') else 'sur batterie'
        return f"Batterie : {fr_num(snap['battery_percent'], 0)} %, {plugged}."
    if metric == 'uptime':
        boot = ''
        if snap.get('boot_time'):
            started = datetime.fromisoformat(str(snap['boot_time']).replace('Z', '+00:00')).astimezone()
            boot = f" (démarré le {started:%d/%m/%Y à %H:%M})"
        return f"PC allumé depuis {fr_duration(snap['uptime_s'])}{boot}."
    if metric == 'temperature':
        temps = [f'{chip} {fr_num(value, 1)} °C' for chip, value in (snap.get('temperatures') or {}).items()]
        gpu = snap.get('gpu') or {}
        if gpu.get('temperature_c') is not None:
            temps.append(f"GPU {fr_num(gpu['temperature_c'], 0)} °C")
        if temps:
            return 'Températures mesurées : ' + ', '.join(temps) + '.'
        text = ('Température du processeur : indisponible (Windows ne l’expose pas à psutil et aucun capteur '
                'lisible n’a été trouvé) : aucune valeur n’est affichée.')
        if snap.get('cpu_percent') is not None:
            text += f" En complément, utilisation actuelle du processeur : {fr_num(snap['cpu_percent'], 0)} %."
        return text
    if metric == 'identity':
        # 22/11/2026 (banc de conversation, tour 12) : « quel est le numéro de série de ma carte graphique ? » recevait
        # la charge de la carte, exacte et à côté de la question. Rien de tout cela n'est lu : c'est dit, sans chiffre.
        text = ('Numéro de série, date de fabrication, date d’achat et garantie : aucun outil d’AURORA ne lit ces '
                'informations, je ne peux donc pas vous les donner et je n’en invente pas. Elles figurent sur '
                'l’étiquette du composant, sur sa facture ou dans le logiciel de son fabricant.')
        name = (snap.get('gpu') or {}).get('name')
        if name:
            text += f' Ce que je lis sur ce PC : le modèle de la carte graphique, {name}.'
        return text
    text = (f"CPU {cpu}, RAM {fr_num(snap['ram_percent'], 0)} % ({fr_num(snap['ram_used_gb'], 1)} / "
            f"{_gb(snap['ram_total_gb'])}), disque {snap['disk_path']} {fr_num(snap['disk_percent'], 0)} % "
            f"({_gb(snap['disk_free_gb'])} libres)")
    if snap.get('net_down_kbps') is not None:
        text += f", réseau ↓ {fr_num(snap['net_down_kbps'], 0)} kbit/s ↑ {fr_num(snap['net_up_kbps'], 0)} kbit/s"
    gpu = snap.get('gpu')
    if gpu and gpu.get('util_percent') is not None:
        text += f", GPU {fr_num(gpu['util_percent'], 0)} %"
    text += f", allumé depuis {fr_duration(snap['uptime_s'])}."
    return text


def _local_source() -> list[Source]:
    return [Source(title='Mesure locale (psutil)', kind='local', retrieved_at=now_iso())]


# --------------------------------------------------------------------------- tools

Metric = Literal['all', 'cpu', 'ram', 'disk', 'network', 'gpu', 'battery', 'uptime', 'temperature', 'identity']
_METRIC_ALIASES = {'memoire': 'ram', 'mémoire': 'ram', 'memory': 'ram', 'processeur': 'cpu', 'disque': 'disk',
                   'stockage': 'disk', 'reseau': 'network', 'réseau': 'network', 'net': 'network',
                   'carte graphique': 'gpu', 'batterie': 'battery', 'tout': 'all', '': 'all',
                   'temperatures': 'temperature', 'température': 'temperature', 'allumage': 'uptime'}


class SnapshotParams(Params):
    metric: Metric = 'all'

    @field_validator('metric', mode='before')
    @classmethod
    def _alias(cls, v):
        if v is None:
            return 'all'
        v = str(v).strip().lower()
        return _METRIC_ALIASES.get(v, v)


@tool('system.snapshot', description="Instantané de la machine : CPU, RAM, disques, réseau, GPU, batterie.",
      action='system.read', params=SnapshotParams, provenance='psutil', timeout_s=15)
def snapshot_tool(p: SnapshotParams) -> ToolResult:
    snap = snapshot()
    return ToolResult(ok=True, data=snap, summary=summarize_snapshot(snap, p.metric), provenance='psutil',
                      sources=_local_source())


class HistoryParams(Params):
    limit: int = Field(default=HISTORY_SIZE, ge=1, le=HISTORY_SIZE)


@tool('system.history', description="Historique récent CPU / RAM / réseau (tampon de 300 points).",
      action='system.read', params=HistoryParams, provenance='psutil')
def history_tool(p: HistoryParams) -> ToolResult:
    data = history(p.limit)
    points = data['points']
    data['count'] = len(points)
    if not points:
        summary = "Aucun historique disponible pour l'instant : l'échantillonnage démarre avec le service."
        return ToolResult(ok=True, data=data, summary=summary, provenance='psutil', sources=_local_source())
    t0 = datetime.fromisoformat(points[0]['t'].replace('Z', '+00:00'))
    t1 = datetime.fromisoformat(points[-1]['t'].replace('Z', '+00:00'))
    span = (t1 - t0).total_seconds()
    cpus = [pt['cpu'] for pt in points if pt.get('cpu') is not None]
    rams = [pt['ram'] for pt in points if pt.get('ram') is not None]
    data['span_s'] = span
    data['cpu_mean'] = round(sum(cpus) / len(cpus), 1) if cpus else None
    data['cpu_max'] = max(cpus) if cpus else None
    data['ram_mean'] = round(sum(rams) / len(rams), 1) if rams else None
    summary = (f"Historique : {len(points)} points sur {fr_duration(span)} ; CPU moyen {fr_num(data['cpu_mean'], 0)} % "
               f"(max {fr_num(data['cpu_max'], 0)} %), RAM moyenne {fr_num(data['ram_mean'], 0)} %.")
    return ToolResult(ok=True, data=data, summary=summary, provenance='psutil', sources=_local_source())


class ProcessesParams(Params):
    limit: int = Field(default=12, ge=1, le=50)
    sort: Literal['cpu', 'memory'] = 'cpu'

    @field_validator('sort', mode='before')
    @classmethod
    def _alias(cls, v):
        if v is None:
            return 'cpu'
        v = str(v).strip().lower()
        return {'ram': 'memory', 'mem': 'memory', 'memoire': 'memory', 'mémoire': 'memory',
                'processeur': 'cpu', '': 'cpu'}.get(v, v)


@tool('system.processes', description="Processus les plus gourmands (CPU ou mémoire).",
      action='system.read', params=ProcessesParams, provenance='psutil', timeout_s=20)
def processes_tool(p: ProcessesParams) -> ToolResult:
    rows = processes(p.limit, p.sort)
    try:
        total = len(psutil.pids())
    except Exception:
        total = None
    data = {'processes': rows, 'sort': p.sort, 'process_count': total,
            'cpu_basis': 'part du processeur entier (tous cœurs = 100 %)'}
    if not rows:
        return ToolResult(ok=False, data=data, summary='Liste des processus indisponible.', provenance='psutil',
                          error='Liste des processus indisponible.')
    if p.sort == 'memory':
        top = [f"{r['name']} {fr_num(r['memory_mb'], 0)} Mo" for r in rows[:5]]
        label = 'mémoire'
    else:
        top = [f"{r['name']} {fr_num(r['cpu_percent'], 1)} %" for r in rows[:5] if r['cpu_percent'] is not None]
        label = 'CPU'
    count = f' sur {total} processus' if total else ''
    summary = f"Processus les plus gourmands ({label}){count} : {', '.join(top)}."
    return ToolResult(ok=True, data=data, summary=summary, provenance='psutil', sources=_local_source())
