// Reads the sim.orbit ToolResult (/api/sim/orbit, or run.ui.data of a sim.orbit run).
// Units are taken from the key suffix (_s, _min, _h, _km, _m, _km_s…); a value whose unit cannot
// be inferred is shown as such, never converted by guess.
import { getPath, isObj, num, numericLeaves, pickArr, pickStr, vec3, humanizeKey } from '../../lib/tolerant';
import type { Vec3 } from './kepler';

export const ORBIT_BODIES = [
  { id: 'earth', fr: 'Terre' },
  { id: 'mars', fr: 'Mars' },
  { id: 'moon', fr: 'Lune' },
  { id: 'venus', fr: 'Vénus' },
  { id: 'jupiter', fr: 'Jupiter' },
] as const;
export type OrbitBodyId = (typeof ORBIT_BODIES)[number]['id'];

/** IAU mean radii (km), used only when the server does not return the body radius. */
const MEAN_RADIUS_KM: Record<string, number> = { earth: 6371.0, mars: 3389.5, moon: 1737.4, venus: 6051.8, jupiter: 69911 };
const BODY_ALIASES: Record<string, string> = {
  terre: 'earth', laterre: 'earth', lune: 'moon', lalune: 'moon', venus: 'venus', mars: 'mars', jupiter: 'jupiter', earth: 'earth', moon: 'moon',
};

export const orbitBodyKey = (name: string | null | undefined) => {
  if (!name) return null;
  const k = name.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z]/g, '');
  return BODY_ALIASES[k] ?? null;
};
export const orbitBodyFr = (key: string | null) => ORBIT_BODIES.find(b => b.id === key)?.fr ?? key ?? '—';

type Unit = 's' | 'km' | 'kms' | 'rel' | 'deg' | 'num' | 'none';
export interface Metric { path: string; label: string; value: number; unit: Unit; known: boolean; suffix?: string }

/** Convert by key suffix to s / km / km·s⁻¹. `known` = the unit was explicit. */
function normalizeUnit(key: string, value: number): { value: number; unit: Unit; known: boolean; suffix?: string } {
  const k = key.toLowerCase();
  if (/_km3_s2$/.test(k)) return { value, unit: 'num', known: true, suffix: ' km³/s²' };
  if (/_km2_s2$/.test(k)) return { value, unit: 'num', known: true, suffix: ' km²/s²' };
  if (/_km2_s$/.test(k)) return { value, unit: 'num', known: true, suffix: ' km²/s' };
  if (/_ms$/.test(k)) return { value: value / 1000, unit: 's', known: true };
  if (/^(rtol|atol)$/.test(k)) return { value, unit: 'rel', known: true };
  if (/(_km_s|_kms|_km_per_s|_km_sec)$/.test(k)) return { value, unit: 'kms', known: true };
  if (/(_m_s|_mps|_m_per_s)$/.test(k)) return { value: value / 1000, unit: 'kms', known: true };
  if (/(_km)$/.test(k)) return { value, unit: 'km', known: true };
  if (/(_m)$/.test(k)) return { value: value / 1000, unit: 'km', known: true };
  if (/(_s|_sec|_seconds)$/.test(k)) return { value, unit: 's', known: true };
  if (/(_min|_minutes)$/.test(k)) return { value: value * 60, unit: 's', known: true };
  if (/(_h|_hr|_hours)$/.test(k)) return { value: value * 3600, unit: 's', known: true };
  if (/(_days?|_d)$/.test(k)) return { value: value * 86400, unit: 's', known: true };
  if (/(_deg)$/.test(k)) return { value, unit: 'deg', known: true };
  if (k === 'hours' || k === 'duration_hours') return { value: value * 3600, unit: 's', known: true };
  if (/(drift|rel|error|err)/.test(k)) return { value, unit: 'rel', known: true };
  if (/(ecc|ratio|count|steps|^n_|nfev|orbits|revolutions|samples|^j2$)/.test(k)) return { value, unit: 'num', known: true };
  return { value, unit: 'none', known: false };
}

/** Scientific notation with a French decimal comma: 4,33e-9. */
export const expFr = (v: number, d = 2) => v.toExponential(d).replace('.', ',').replace('e+', 'e');

export function fmtMetric(m: Pick<Metric, 'value' | 'unit' | 'known' | 'suffix'>): string {
  const v = m.value;
  const n = (x: number, d = 2) => x.toLocaleString('fr-FR', { maximumFractionDigits: d });
  switch (m.unit) {
    case 's': {
      if (Math.abs(v) < 120) return `${n(v, 2)} s`;
      if (Math.abs(v) < 3600) return `${n(v / 60, 2)} min`;
      if (Math.abs(v) < 172800) { const tm = Math.round(v / 60); return `${Math.floor(tm / 60)} h ${String(tm % 60).padStart(2, '0')} min`; }
      return `${n(v / 86400, 2)} j`;
    }
    case 'km': return `${n(v, Math.abs(v) < 100 ? 2 : 0)} km`;
    case 'kms': return `${n(v, 3)} km/s`;
    case 'deg': return `${n(v, 2)}°`;
    case 'num': return Math.abs(v) >= 1e6 || (v !== 0 && Math.abs(v) < 1e-3) ? `${expFr(v, 4)}${m.suffix ?? ''}` : `${n(v, 4)}${m.suffix ?? ''}`;
    case 'rel': return v === 0 ? '0' : Math.abs(v) < 1e-3 || Math.abs(v) >= 1e4 ? expFr(v, 2) : n(v, 6);
    default: return `${n(v, 4)} (unité non précisée)`;
  }
}

export interface OrbitModel {
  points: Vec3[];
  bodyKey: string;
  bodyName: string;
  radiusKm: number;
  radiusFromServer: boolean;
  periodAnalytic: Metric | null;
  periodNumeric: Metric | null;
  speeds: Metric[];
  periapsis: Metric | null;
  apoapsis: Metric | null;
  periapsisNumeric: Metric | null;
  apoapsisNumeric: Metric | null;
  energyDrift: Metric | null;
  integrator: string | null;
  checks: { name: string; ok: boolean; detail: string }[];
  impacted: boolean;
  others: Metric[];
}

const LABELS: [RegExp, string][] = [
  [/specific_energy/i, 'Énergie orbitale spécifique'], [/angular_momentum/i, 'Moment cinétique spécifique'], [/impact_time/i, 'Instant de l’impact'],
  [/^rtol$/i, 'Tolérance relative'], [/^atol$/i, 'Tolérance absolue'], [/compute_ms/i, 'Temps de calcul'], [/^j2$/i, 'Coefficient J2'],
  [/inclination/i, 'Inclinaison'], [/nfev/i, 'Évaluations de la dynamique'], [/duration_s/i, 'Durée simulée'],
  [/alt/i, 'Altitude'], [/ecc/i, 'Excentricité'], [/hours|duration|duree/i, 'Durée simulée'], [/^(mu|gm)(_|$)/i, 'Paramètre gravitationnel'],
  [/semi|sma|^a_/i, 'Demi-grand axe'], [/steps|n_points|samples|nfev/i, 'Points / évaluations'], [/orbits|revolutions/i, 'Révolutions'],
];

function readPoints(data: unknown): Vec3[] {
  let scale = 1;
  const unit = (pickStr(data, ['units.distance', 'units', 'unit', 'length_unit', 'trajectory_units', 'trajectory_unit']) ?? '').toLowerCase().trim();
  if (unit === 'm' || unit === 'meters' || unit === 'metres') scale = 1 / 1000;
  let pts: Vec3[] = [];
  const arr = pickArr(data, ['trajectory', 'trajectory_km', 'points', 'positions', 'path', 'states', 'xyz', 'samples', 'trajectory.points', 'trajectory_m']);
  if (arr) {
    if (Array.isArray(getPath(data, 'trajectory_m')) && arr === getPath(data, 'trajectory_m')) scale = 1 / 1000;
    pts = arr.map(vec3).filter((p): p is Vec3 => p !== null);
  } else {
    const cols = ['trajectory', 'positions', 'path'].map(k => getPath(data, k)).find(isObj);
    if (cols && Array.isArray(cols.x) && Array.isArray(cols.y) && Array.isArray(cols.z)) {
      const xs = cols.x as unknown[], ys = cols.y as unknown[], zs = cols.z as unknown[];
      for (let k = 0; k < Math.min(xs.length, ys.length, zs.length); k++) {
        const x = num(xs[k]), y = num(ys[k]), z = num(zs[k]);
        if (x !== null && y !== null && z !== null) pts.push([x, y, z]);
      }
    }
  }
  if (scale !== 1) pts = pts.map(p => [p[0] * scale, p[1] * scale, p[2] * scale]);
  if (pts.length > 2000) { const step = pts.length / 2000; pts = Array.from({ length: 2000 }, (_, k) => pts[Math.floor(k * step)]).concat([pts[pts.length - 1]]); }
  return pts;
}

/** Null when the payload carries neither a trajectory nor any metric. */
export function parseOrbit(data: unknown, requestedBody: string | null): OrbitModel | null {
  if (!isObj(data)) return null;
  const bodyKey = orbitBodyKey(pickStr(data, ['body.id', 'body_id', 'body', 'body.name', 'body_name', 'central_body', 'planet']))
    ?? orbitBodyKey(requestedBody) ?? 'earth';
  const points = readPoints(data);

  const leaves = numericLeaves(data, 2);
  const used = new Set<string>();
  const metric = (l: { path: string; key: string; value: number }, label: string): Metric => {
    used.add(l.path);
    const u = normalizeUnit(l.key, l.value);
    return { path: l.path, label, ...u };
  };
  const find = (test: (k: string) => boolean) => leaves.find(l => !used.has(l.path) && test(l.key.toLowerCase()));

  const radiusLeaf = find(k => /radius/.test(k) && !/orbit|peri|apo/.test(k));
  let radiusKm = MEAN_RADIUS_KM[bodyKey] ?? 6371;
  let radiusFromServer = false;
  if (radiusLeaf) { const u = metric(radiusLeaf, 'Rayon'); if (u.unit === 'km') { radiusKm = u.value; radiusFromServer = true; } }

  const pA = find(k => /period/.test(k) && /(analyt|kepler|theor|expected)/.test(k));
  const pN = find(k => /period/.test(k) && /(numer|integr|simul|measur|observ|computed)/.test(k));
  const periodAnalytic = pA ? metric(pA, 'Période analytique (Kepler)') : null;
  let periodNumeric = pN ? metric(pN, 'Période numérique (intégration)') : null;
  if (!periodAnalytic && !periodNumeric) { const p = find(k => /period/.test(k)); if (p) periodNumeric = metric(p, 'Période'); }

  const energy = find(k => /energ/.test(k) && /(drift|err|delta|rel|change|var)/.test(k)) ?? find(k => /drift/.test(k));
  const energyDrift = energy ? metric(energy, "Dérive relative d'énergie") : null;

  const speeds: Metric[] = [];
  for (const [re, label] of [[/peri/, 'Vitesse au périapside'], [/apo/, "Vitesse à l'apoapside"], [/circ/, 'Vitesse circulaire'],
    [/(initial|v0|start)/, 'Vitesse initiale'], [/(mean|avg|moy)/, 'Vitesse moyenne'], [/(esc|liber)/, "Vitesse de libération"],
    [/min/, 'Vitesse minimale (intégration)'], [/max/, 'Vitesse maximale (intégration)']] as [RegExp, string][]) {
    const l = find(k => /(speed|velocity|^v_|^v$|vitesse)/.test(k) && re.test(k));
    if (l) speeds.push(metric(l, label));
  }
  const anySpeed = speeds.length ? null : find(k => /(speed|velocity|^v_|vitesse)/.test(k));
  if (anySpeed) speeds.push(metric(anySpeed, 'Vitesse'));

  const notSpeed = (k: string) => !/(speed|velocity|^v_|period)/.test(k);
  const peri = find(k => /(peri|perigee|perihel)/.test(k) && notSpeed(k) && !/numer/.test(k));
  const apo = find(k => /(apo|apogee|aphel)/.test(k) && notSpeed(k) && !/numer/.test(k));
  const periapsis = peri ? metric(peri, /alt/.test(peri.key) ? 'Périapside (altitude)' : 'Périapside') : null;
  const apoapsis = apo ? metric(apo, /alt/.test(apo.key) ? 'Apoapside (altitude)' : 'Apoapside') : null;

  const periN = find(k => /(peri|perigee|perihel)/.test(k) && notSpeed(k));
  const apoN = find(k => /(apo|apogee|aphel)/.test(k) && notSpeed(k));
  const periapsisNumeric = periN ? metric(periN, 'Périapside mesurée sur la trajectoire') : null;
  const apoapsisNumeric = apoN ? metric(apoN, 'Apoapside mesurée sur la trajectoire') : null;

  const rank = (p: string) => (p.startsWith('metrics.') ? 0 : p.startsWith('initial.') ? 1 : p.startsWith('integration.') ? 2 : 3);
  const others = leaves.filter(l => !used.has(l.path)).sort((a, b) => rank(a.path) - rank(b.path)).slice(0, 16).map(l => {
    const label = LABELS.find(([re]) => re.test(l.key))?.[1] ?? humanizeKey(l.key);
    return { path: l.path, label, ...normalizeUnit(l.key, l.value) };
  });

  if (!points.length && !periodAnalytic && !periodNumeric && !speeds.length && !energyDrift) return null;
  return {
    points, bodyKey, bodyName: orbitBodyFr(bodyKey), radiusKm, radiusFromServer,
    periodAnalytic, periodNumeric, speeds, periapsis, apoapsis, periapsisNumeric, apoapsisNumeric, energyDrift,
    integrator: pickStr(data, ['integration.method', 'integrator', 'method', 'solver', 'scheme', 'numerics.method']),
    checks: (pickArr(data, ['checks', 'verification.checks']) ?? []).filter(isObj).map(c => ({
      name: String(c.name ?? ''), ok: c.ok === true, detail: typeof c.detail === 'string' ? c.detail : '',
    })).filter(c => c.name),
    impacted: getPath(data, 'metrics.impacted') === true || getPath(data, 'impacted') === true,
    others,
  };
}
