"""Orbital mechanics, computed — never generated.

``simulate_orbit``: two-body problem (optionally with the J2 oblateness term) integrated
with SciPy ``solve_ivp`` (DOP853, rtol 1e-9). Metrics are checked numerically: analytic vs
numerical period and relative energy drift.

``solar_system``: heliocentric ecliptic positions of the eight planets from the JPL
approximate Keplerian elements (E. M. Standish, "Keplerian Elements for Approximate
Positions of the Major Planets", table 1, valid 1800–2050, J2000 ecliptic and equinox).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

from pydantic import Field, field_validator, model_validator

from .base import Params, Source, ToolError, ToolResult, fr_duration, fr_num, fr_sig, now_iso, tool


# --------------------------------------------------------------------------- bodies

@dataclass(frozen=True)
class Body:
    id: str
    name: str
    gm: float        # km^3 / s^2
    radius: float    # km (equatorial)
    j2: float        # dimensionless


BODIES = {
    'terre': Body('terre', 'la Terre', 398_600.4418, 6378.137, 1.08262668e-3),
    'mars': Body('mars', 'Mars', 42_828.37, 3396.19, 1.96045e-3),
    'lune': Body('lune', 'la Lune', 4_902.800066, 1737.4, 2.0330e-4),
    'venus': Body('venus', 'Vénus', 324_858.592, 6051.8, 4.458e-6),
    'jupiter': Body('jupiter', 'Jupiter', 126_686_534.0, 71_492.0, 1.4736e-2),
}
BODY_ALIASES = {'earth': 'terre', 'la terre': 'terre', 'moon': 'lune', 'la lune': 'lune', 'vénus': 'venus',
                'planete mars': 'mars', 'planète mars': 'mars'}

MAX_DURATION_S = 90 * 86400
MAX_PERIODS = 200
RTOL = 1e-9
ATOL = 1e-9


def resolve_body(name: str) -> Body:
    key = (name or '').strip().lower()
    key = BODY_ALIASES.get(key, key)
    if key not in BODIES:
        raise ToolError(f"Corps céleste non pris en charge : « {name} » (disponibles : Terre, Mars, Lune, Vénus, Jupiter).")
    return BODIES[key]


def simulate_orbit(body_name: str = 'terre', altitude_km: float = 400.0, eccentricity: float = 0.0,
                   inclination_deg: float = 0.0, hours: float | None = None, periods: float | None = None,
                   j2: bool = False, points: int = 400) -> dict:
    import numpy as np
    from scipy.integrate import solve_ivp

    body = resolve_body(body_name)
    gm, radius = body.gm, body.radius
    j2_coef = body.j2 if j2 else 0.0
    e = float(eccentricity)
    if not 0 <= e < 0.95:
        raise ToolError('Excentricité hors limites (0 ≤ e < 0,95 pour une orbite fermée).')
    if altitude_km <= 0:
        raise ToolError('L\'altitude au périapside doit être positive.')
    rp = radius + altitude_km
    a = rp / (1 - e)
    ra = a * (1 + e)
    period = 2 * math.pi * math.sqrt(a ** 3 / gm)
    duration = hours * 3600 if hours else (periods or 1.0) * period
    if duration > MAX_DURATION_S:
        raise ToolError(f'Durée simulée trop longue ({fr_duration(duration)}) : limite {fr_duration(MAX_DURATION_S)}.')
    if duration / period > MAX_PERIODS:
        raise ToolError(f'Trop de révolutions ({fr_num(duration / period, 0)}) : limite {MAX_PERIODS} par simulation.')
    v_p = math.sqrt(gm * (1 + e) / rp)
    inc = math.radians(inclination_deg)
    q_hat = (0.0, math.cos(inc), math.sin(inc))          # initial velocity direction (in plane)
    state0 = [rp, 0.0, 0.0, 0.0, v_p * q_hat[1], v_p * q_hat[2]]
    r2_eq = radius * radius

    def rhs(_t, s):
        x, y, z, vx, vy, vz = s
        r2 = x * x + y * y + z * z
        r = math.sqrt(r2)
        k = -gm / (r2 * r)
        if j2_coef:
            f = 1.5 * j2_coef * r2_eq / r2
            z2 = z * z / r2
            kxy, kz = k * (1 + f * (1 - 5 * z2)), k * (1 + f * (3 - 5 * z2))
            return [vx, vy, vz, kxy * x, kxy * y, kz * z]
        return [vx, vy, vz, k * x, k * y, k * z]

    def impact(_t, s):
        return math.sqrt(s[0] ** 2 + s[1] ** 2 + s[2] ** 2) - radius
    impact.terminal = True
    impact.direction = -1

    def revolution(_t, s):
        return s[0] * q_hat[0] + s[1] * q_hat[1] + s[2] * q_hat[2]
    revolution.direction = 1

    n = max(50, min(int(points), 2000))
    t_eval = np.linspace(0.0, duration, n)
    started = time.perf_counter()
    # Integrate a little past one revolution so the numerical period is always detected;
    # the returned trajectory still stops at the requested duration (t_eval).
    t_end = max(duration, 1.05 * period) if duration < 1.05 * period and period <= MAX_DURATION_S else duration
    sol = solve_ivp(rhs, (0.0, t_end), state0, method='DOP853', rtol=RTOL, atol=ATOL, t_eval=t_eval,
                    events=(impact, revolution))
    integration_ms = (time.perf_counter() - started) * 1000
    if sol.status < 0:
        raise ToolError(f"L'intégration numérique a échoué : {sol.message}")

    xs, ys, zs, vxs, vys, vzs = sol.y
    r = np.sqrt(xs ** 2 + ys ** 2 + zs ** 2)
    v = np.sqrt(vxs ** 2 + vys ** 2 + vzs ** 2)
    energy = 0.5 * v ** 2 - gm / r
    if j2_coef:
        energy = energy + gm * j2_coef * r2_eq * (3 * zs ** 2 / r ** 2 - 1) / (2 * r ** 3)
    drift = float(np.max(np.abs(energy - energy[0])) / abs(energy[0]))
    rev_times = [float(t) for t in sol.t_events[1] if t > 0.5 * period]
    period_num = rev_times[0] if rev_times else None
    impacts = [float(t) for t in sol.t_events[0] if t <= duration]
    impacted = bool(impacts)
    h = math.hypot(*np.cross(state0[:3], state0[3:]))

    checks = [{'name': 'conservation de l\'énergie', 'ok': drift < 1e-6,
               'detail': f'dérive relative maximale {drift:.2e}'}]
    if period_num is not None:
        rel = abs(period_num - period) / period
        tol = 1e-6 if not j2_coef else 5e-2
        checks.append({'name': 'période numérique vs analytique', 'ok': rel < tol,
                       'detail': f'écart relatif {rel:.2e}' + (' (J2 actif : écart attendu)' if j2_coef else '')})
    if impacted:
        checks.append({'name': 'trajectoire', 'ok': False, 'detail': 'collision avec la surface'})

    return {
        'body': {'id': body.id, 'name': body.name, 'gm_km3_s2': gm, 'radius_km': radius, 'j2': body.j2},
        'initial': {'altitude_km': altitude_km, 'eccentricity': e, 'inclination_deg': inclination_deg,
                    'j2_enabled': bool(j2_coef), 'position_km': state0[:3], 'velocity_kms': state0[3:]},
        'metrics': {
            'semi_major_axis_km': a,
            'periapsis_alt_km': altitude_km,
            'apoapsis_alt_km': ra - radius,
            'periapsis_alt_numeric_km': float(r.min() - radius),
            'apoapsis_alt_numeric_km': float(r.max() - radius),
            'period_analytic_s': period,
            'period_numeric_s': period_num,
            'speed_periapsis_kms': v_p,
            'speed_apoapsis_kms': math.sqrt(gm * (1 - e) / ra),
            'speed_min_kms': float(v.min()),
            'speed_max_kms': float(v.max()),
            'circular_speed_kms': math.sqrt(gm / rp),
            'escape_speed_kms': math.sqrt(2 * gm / rp),
            'specific_energy_km2_s2': float(energy[0]),
            'angular_momentum_km2_s': h,
            'energy_drift_rel': drift,
            'revolutions': duration / period,
            'impacted': impacted,
            'impact_time_s': impacts[0] if impacted else None,
        },
        'integration': {'method': 'DOP853', 'rtol': RTOL, 'atol': ATOL, 'nfev': int(sol.nfev),
                        'duration_s': duration, 'compute_ms': round(integration_ms, 1), 'message': sol.message},
        'checks': checks,
        'trajectory': [[round(float(x), 1), round(float(y), 1), round(float(z), 1)] for x, y, z in zip(xs, ys, zs)],
        'times_s': [round(float(t), 2) for t in sol.t],
        'units': {'distance': 'km', 'speed': 'km/s', 'time': 's'},
    }


def summarize_orbit(d: dict) -> str:
    m, i, b = d['metrics'], d['initial'], d['body']
    text = (f"Orbite autour de {b['name']} : périapside {fr_num(m['periapsis_alt_km'], 0)} km, "
            f"apoapside {fr_num(m['apoapsis_alt_km'], 0)} km (e = {fr_sig(i['eccentricity'], 3)}), "
            f"inclinaison {fr_num(i['inclination_deg'], 1)}°. Période {fr_duration(m['period_analytic_s'])} "
            f"(analytique {fr_num(m['period_analytic_s'], 1)} s")
    if m['period_numeric_s'] is not None:
        text += f", numérique {fr_num(m['period_numeric_s'], 1)} s"
    drift = f"{m['energy_drift_rel']:.1e}".replace('.', ',')
    text += (f"), vitesse au périapside {fr_num(m['speed_periapsis_kms'], 3)} km/s. "
             f"Dérive relative d'énergie {drift}")
    if m['energy_drift_rel'] < 1e-6:
        text += ' : intégration DOP853 vérifiée.'
    else:
        text += ' : dérive élevée, résultat à prendre avec prudence.'
    if m['impacted']:
        text += f" La trajectoire touche la surface après {fr_duration(m['impact_time_s'])}."
    if i['j2_enabled']:
        text += ' Aplatissement J2 pris en compte.'
    return text


# --------------------------------------------------------------------------- solar system

# a [au], e, I [deg], L [deg], long. perihelion [deg], long. ascending node [deg]; then rates per century.
JPL_ELEMENTS = {
    'mercury': ('Mercure', (0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593),
                (0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081)),
    'venus': ('Vénus', (0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
              (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418)),
    'earth': ('Terre', (1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
              (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0)),
    'mars': ('Mars', (1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
             (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343)),
    'jupiter': ('Jupiter', (5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
                (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106)),
    'saturn': ('Saturne', (9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448),
               (-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794)),
    'uranus': ('Uranus', (19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503),
               (-0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589)),
    'neptune': ('Neptune', (30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574),
                (0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.00508664)),
}
JPL_SOURCE_URL = 'https://ssd.jpl.nasa.gov/planets/approx_pos.html'
J2000_JD = 2451545.0
VALID_YEARS = (1800, 2050)


def julian_day(dt: datetime) -> float:
    return dt.timestamp() / 86400.0 + 2440587.5


def solve_kepler(mean_anomaly_rad: float, e: float) -> float:
    E = mean_anomaly_rad + e * math.sin(mean_anomaly_rad)
    for _ in range(50):
        dE = (E - e * math.sin(E) - mean_anomaly_rad) / (1 - e * math.cos(E))
        E -= dE
        if abs(dE) < 1e-13:
            break
    return E


def _wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def position_from_elements(a: float, e: float, i_deg: float, L_deg: float, varpi_deg: float,
                           node_deg: float) -> tuple[float, float, float]:
    """Heliocentric ecliptic J2000 coordinates (au) from mean elements (Standish algorithm)."""
    omega = math.radians(varpi_deg - node_deg)
    node = math.radians(node_deg)
    inc = math.radians(i_deg)
    M = math.radians(_wrap180(L_deg - varpi_deg))
    E = solve_kepler(M, e)
    xp = a * (math.cos(E) - e)
    yp = a * math.sqrt(1 - e * e) * math.sin(E)
    co, so, cn, sn, ci, si = math.cos(omega), math.sin(omega), math.cos(node), math.sin(node), math.cos(inc), math.sin(inc)
    x = (co * cn - so * sn * ci) * xp + (-so * cn - co * sn * ci) * yp
    y = (co * sn + so * cn * ci) * xp + (-so * sn + co * cn * ci) * yp
    z = (so * si) * xp + (co * si) * yp
    return x, y, z


def parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    text = value.strip()
    try:
        if len(text) == 10:
            d = date.fromisoformat(text)
            return datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        raise ToolError(f'Date invalide : « {value} » (format attendu AAAA-MM-JJ).') from None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def solar_system(when: str | None = None, *, include_paths: bool = True, path_points: int = 120) -> dict:
    dt = parse_date(when)
    if not VALID_YEARS[0] <= dt.year <= VALID_YEARS[1]:
        raise ToolError(f'Les éléments approchés du JPL (table 1) sont valides de {VALID_YEARS[0]} à {VALID_YEARS[1]} ; '
                        f'année demandée : {dt.year}.')
    jd = julian_day(dt)
    T = (jd - J2000_JD) / 36525.0
    planets = []
    for pid, (name, base, rate) in JPL_ELEMENTS.items():
        a, e, inc, L, varpi, node = (b + r * T for b, r in zip(base, rate))
        x, y, z = position_from_elements(a, e, inc, L, varpi, node)
        r_au = math.sqrt(x * x + y * y + z * z)
        planet = {
            'id': pid, 'name': name, 'x': x, 'y': y, 'z': z, 'r_au': r_au,
            'ecliptic_longitude_deg': math.degrees(math.atan2(y, x)) % 360.0,
            'ecliptic_latitude_deg': math.degrees(math.asin(z / r_au)),
            'elements': {'a_au': a, 'e': e, 'i_deg': inc, 'L_deg': L % 360.0, 'varpi_deg': varpi % 360.0,
                         'node_deg': node % 360.0, 'omega_deg': (varpi - node) % 360.0,
                         'M_deg': _wrap180(L - varpi) % 360.0},
            'rates_per_century': dict(zip(('a_au', 'e', 'i_deg', 'L_deg', 'varpi_deg', 'node_deg'), rate)),
            'mean_motion_deg_per_day': rate[3] / 36525.0,
            'period_days': 360.0 / (rate[3] / 36525.0),
        }
        if include_paths:
            n = max(24, min(path_points, 720))
            path = []
            for k in range(n + 1):
                Lk = varpi + 360.0 * k / n  # sweep mean anomaly over a full revolution
                px, py, pz = position_from_elements(a, e, inc, Lk, varpi, node)
                path.append([round(px, 5), round(py, 5), round(pz, 5)])
            planet['path'] = path
        planets.append(planet)
    return {
        'date': dt.isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'jd': jd,
        'centuries_since_j2000': T,
        'frame': 'héliocentrique, écliptique et équinoxe J2000',
        'unit': 'UA',
        'planets': planets,
        'method': 'Éléments képlériens moyens + taux séculaires (JPL, Standish, table 1, 1800–2050)',
        'propagation': 'L(t) = L + n·Δt (n en °/jour) ; M = L − ϖ ; ω = ϖ − Ω ; équation de Kepler',
        'accuracy_note': "Précision de l'ordre de quelques minutes d'arc pour les planètes intérieures, "
                         "moins bonne pour les géantes ; « earth » désigne le barycentre Terre-Lune.",
    }


MONTHS_FR = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre',
             'novembre', 'décembre']


def summarize_solar(d: dict) -> str:
    dt = datetime.fromisoformat(d['date'].replace('Z', '+00:00'))
    when = f"{'1er' if dt.day == 1 else dt.day} {MONTHS_FR[dt.month - 1]} {dt.year}"
    parts = [f"{p['name']} {fr_num(p['r_au'], 2)} UA" for p in d['planets']]
    return (f"Positions héliocentriques au {when} (éléments approchés du JPL) — distance au Soleil : "
            + ', '.join(parts) + '.')


# --------------------------------------------------------------------------- tools

class OrbitParams(Params):
    body: str = Field(default='terre', max_length=40)
    altitude_km: float = Field(default=400.0, gt=0, le=5_000_000)
    eccentricity: float = Field(default=0.0, ge=0, lt=0.95)
    inclination_deg: float = Field(default=0.0, ge=0, le=180)
    hours: float | None = Field(default=None, gt=0, le=MAX_DURATION_S / 3600)
    periods: float | None = Field(default=None, gt=0, le=MAX_PERIODS)
    j2: bool = False
    points: int = Field(default=400, ge=50, le=2000)

    @field_validator('body', mode='before')
    @classmethod
    def _default_body(cls, v):
        return 'terre' if v in (None, '') else v

    @model_validator(mode='after')
    def _one_duration(self):
        if self.hours is not None and self.periods is not None:
            raise ValueError('indiquez hours ou periods, pas les deux')
        return self


@tool('sim.orbit', description="Simulation d'orbite (SciPy DOP853, J2 optionnel) autour de la Terre, Mars, la Lune, "
      "Vénus ou Jupiter, avec vérification numérique.", action='system.read', params=OrbitParams,
      provenance='scipy', timeout_s=60)
def orbit_tool(p: OrbitParams) -> ToolResult:
    data = simulate_orbit(p.body, p.altitude_km, p.eccentricity, p.inclination_deg, p.hours, p.periods, p.j2, p.points)
    return ToolResult(ok=True, data=data, summary=summarize_orbit(data), provenance='scipy',
                      sources=[Source(title='Intégration numérique SciPy solve_ivp (DOP853)', kind='computation',
                                      retrieved_at=now_iso())])


class SolarParams(Params):
    date: str | None = Field(default=None, max_length=40)
    include_paths: bool = True


@tool('sim.solar_system', description='Positions héliocentriques des 8 planètes à une date (éléments képlériens '
      'approchés du JPL) et éléments pour tracer les orbites.', action='system.read', params=SolarParams,
      provenance='kepler-jpl-approx', timeout_s=10)
def solar_tool(p: SolarParams) -> ToolResult:
    data = solar_system(p.date, include_paths=p.include_paths)
    return ToolResult(ok=True, data=data, summary=summarize_solar(data), provenance='kepler-jpl-approx',
                      sources=[Source(title='JPL — Approximate Positions of the Major Planets (Standish)',
                                      url=JPL_SOURCE_URL, kind='reference', retrieved_at=now_iso())])
