"""Local date and time from the system clock.

``datetime.now().astimezone()`` uses the Windows time zone directly; the IANA database
(tzdata) is often missing on Windows, so a named zone is optional and fails honestly.
"""
from __future__ import annotations

import time
import unicodedata
from datetime import datetime, timedelta, timezone

from pydantic import Field

from .base import Params, Source, ToolError, ToolResult, now_iso, tool

WEEKDAYS_FR = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
MONTHS_FR = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre',
             'octobre', 'novembre', 'décembre']

# City / country (accent-free, lower case) -> IANA zone, for « quelle heure est-il à Tokyo ? ».
_ZONE_NAMES = {
    'Europe/Paris': ('paris', 'lyon', 'marseille', 'toulouse', 'nice', 'nantes', 'strasbourg', 'montpellier',
                     'bordeaux', 'lille', 'rennes', 'grenoble', 'france'),
    'Europe/London': ('londres', 'london', 'royaume uni', 'angleterre'), 'Europe/Berlin': ('berlin', 'allemagne'),
    'Europe/Madrid': ('madrid', 'barcelone', 'espagne'), 'Europe/Rome': ('rome', 'milan', 'italie'),
    'Europe/Brussels': ('bruxelles', 'belgique'), 'Europe/Zurich': ('geneve', 'zurich', 'lausanne', 'suisse'),
    'Europe/Lisbon': ('lisbonne', 'portugal'), 'Europe/Amsterdam': ('amsterdam', 'pays bas'),
    'Europe/Athens': ('athenes', 'grece'), 'Europe/Moscow': ('moscou', 'russie'),
    'Europe/Istanbul': ('istanbul', 'turquie'),
    'America/New_York': ('new york', 'miami', 'washington', 'boston'), 'America/Chicago': ('chicago',),
    'America/Los_Angeles': ('los angeles', 'san francisco'), 'America/Toronto': ('montreal', 'toronto', 'quebec'),
    'America/Vancouver': ('vancouver',), 'America/Mexico_City': ('mexico',),
    'America/Sao_Paulo': ('rio de janeiro', 'sao paulo', 'bresil'),
    'America/Argentina/Buenos_Aires': ('buenos aires', 'argentine'),
    'Asia/Tokyo': ('tokyo', 'japon'), 'Asia/Shanghai': ('pekin', 'shanghai', 'chine'),
    'Asia/Hong_Kong': ('hong kong',), 'Asia/Seoul': ('seoul', 'coree du sud'),
    'Asia/Bangkok': ('bangkok', 'thailande'), 'Asia/Singapore': ('singapour',), 'Asia/Dubai': ('dubai',),
    'Asia/Kolkata': ('mumbai', 'bombay', 'new delhi', 'inde'), 'Australia/Sydney': ('sydney',),
    'Africa/Algiers': ('alger', 'oran', 'constantine', 'annaba', 'algerie'),
    'Africa/Tunis': ('tunis', 'sfax', 'tunisie'), 'Africa/Casablanca': ('casablanca', 'rabat', 'marrakech', 'maroc'),
    'Africa/Dakar': ('dakar', 'senegal'), 'Africa/Abidjan': ('abidjan',), 'Africa/Bamako': ('bamako', 'mali'),
    'Africa/Nouakchott': ('nouakchott',), 'Africa/Cairo': ('le caire', 'caire', 'egypte'),
}
CITY_ZONES = {name: zone for zone, names in _ZONE_NAMES.items() for name in names}
# Zones without daylight saving time (for years): exact even when the tzdata base is missing.
FIXED_OFFSETS_MIN = {
    'Asia/Tokyo': 540, 'Asia/Seoul': 540, 'Asia/Shanghai': 480, 'Asia/Hong_Kong': 480, 'Asia/Singapore': 480,
    'Asia/Bangkok': 420, 'Asia/Kolkata': 330, 'Asia/Dubai': 240, 'Europe/Moscow': 180, 'Europe/Istanbul': 180,
    'Africa/Algiers': 60, 'Africa/Tunis': 60, 'Africa/Dakar': 0, 'Africa/Abidjan': 0, 'Africa/Bamako': 0,
    'Africa/Nouakchott': 0, 'America/Sao_Paulo': -180, 'America/Argentina/Buenos_Aires': -180,
    'America/Mexico_City': -360,
}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize('NFKD', (text or '').lower())
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in decomposed if not unicodedata.combining(c)).split())


def zone_for_place(place: str | None) -> str | None:
    key = _fold(place or '')
    return CITY_ZONES.get(key) or CITY_ZONES.get(key.removeprefix('le ').removeprefix('la '))


def _offset_text(offset: timedelta | None) -> str:
    if offset is None:
        return 'UTC'
    minutes = int(offset.total_seconds() // 60)
    sign = '+' if minutes >= 0 else '−'
    h, m = divmod(abs(minutes), 60)
    return f'UTC{sign}{h:02d}:{m:02d}'


def describe(now: datetime) -> dict:
    day = '1er' if now.day == 1 else str(now.day)
    iso_year, iso_week, _ = now.isocalendar()
    return {
        'iso': now.isoformat(timespec='seconds'),
        'utc': now.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'date': now.date().isoformat(),
        'time': now.strftime('%H:%M:%S'),
        'weekday': WEEKDAYS_FR[now.weekday()],
        'day': now.day,
        'month': now.month,
        'month_name': MONTHS_FR[now.month - 1],
        'year': now.year,
        'iso_week': iso_week,
        'day_of_year': now.timetuple().tm_yday,
        'utc_offset': _offset_text(now.utcoffset()),
        'tz_name': now.tzname() or '',
        'is_dst': bool(time.localtime().tm_isdst) if now.tzinfo is not None else None,
        'timestamp': round(now.timestamp(), 3),
        'date_text': f'{WEEKDAYS_FR[now.weekday()]} {day} {MONTHS_FR[now.month - 1]} {now.year}',
        'time_text': f'{now.hour} h {now.minute:02d}',
    }


def _local_hint() -> tuple[dict, str]:
    local = describe(datetime.now().astimezone())
    return local, f"seule l'heure locale du PC est connue ({local['time_text']}, {local['utc_offset']})"


def now_info(tz: str | None = None, place: str | None = None) -> dict:
    """Local time, or the time of a named zone / place. A zone that cannot be computed exactly
    fails honestly with the local time in the message, never presented as the requested one."""
    if place and not tz:
        tz = zone_for_place(place)
        if tz is None:
            local, hint = _local_hint()
            raise ToolError(f"Heure à {place} indisponible : fuseau horaire de ce lieu inconnu d'AURORA ; {hint}.",
                            data={'local': local, 'place': place, 'tz_requested': None})
    if tz:
        zone = None
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                zone = ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError):
                zone = None
        except ImportError:
            zone = None
        fixed = FIXED_OFFSETS_MIN.get(tz)
        if zone is None and fixed is not None:   # no tzdata on this PC, but this zone has no DST
            zone = timezone(timedelta(minutes=fixed), tz)
        if zone is None:
            # Windows names its zone after cities (« Paris, Madrid »): the requested place is the PC's own zone.
            local = datetime.now().astimezone()
            local_name = f' {_fold(local.tzname() or "")} '
            if any(f' {name} ' in local_name for name in _ZONE_NAMES.get(tz, ())):
                data = describe(local)
                data.update(tz_requested=tz, place=place, fixed_offset=False, local_zone=True)
                return data
        if zone is None:
            local, hint = _local_hint()
            label = place or tz
            raise ToolError(f"Heure à {label} indisponible : base des fuseaux horaires (tzdata) absente sur ce PC "
                            f"ou fuseau « {tz} » inconnu ; {hint}.",
                            data={'local': local, 'place': place, 'tz_requested': tz})
        data = describe(datetime.now(zone))
        data.update(tz_requested=tz, place=place, fixed_offset=zone is not None and fixed is not None
                    and not hasattr(zone, 'key'))
        return data
    data = describe(datetime.now().astimezone())
    data['tz_requested'] = None
    return data


class TimeParams(Params):
    tz: str | None = Field(default=None, max_length=64)
    place: str | None = Field(default=None, max_length=80)


@tool('time.now', description="Date et heure locales du PC (ou d'une grande ville connue).", action='system.read',
      params=TimeParams, provenance='clock', timeout_s=5)
def time_tool(p: TimeParams) -> ToolResult:
    data = now_info(p.tz, p.place)
    if data.get('tz_requested'):
        where = f"À {p.place} ({data['tz_requested']}), il" if p.place else f"Dans le fuseau {data['tz_requested']}, il"
        note = ", sans heure d'été" if data.get('fixed_offset') else (
            ', fuseau du PC' if data.get('local_zone') else '')
        summary = f"{where} est {data['time_text']}, {data['date_text']} ({data['utc_offset']}{note})."
    else:
        summary = f"Il est {data['time_text']}, {data['date_text']} ({data['utc_offset']})."
    return ToolResult(ok=True, data=data, summary=summary, provenance='clock',
                      sources=[Source(title='Horloge système du PC', kind='local', retrieved_at=now_iso())])
