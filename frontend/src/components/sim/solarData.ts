// Reads the sim.solar_system ToolResult (/api/sim/solar) into propagatable bodies, then checks the
// client-side Kepler solution against the server's own positions at the reference date.
import { getPath, isObj, num, pickArr, pickStr, vec3, type Json } from '../../lib/tolerant';
import { AU_KM, J2000_JD, dist, elementsAt, jdFromMs, position, type Elements, type Rates, type Vec3 } from './kepler';

export interface PlanetStyle { fr: string; color: string; size: number; ring?: boolean }

/** Display style only (colour, drawn size — not to scale). */
export const PLANET_STYLES: Record<string, PlanetStyle> = {
  mercury: { fr: 'Mercure', color: '#b9b2a8', size: 3.2 },
  venus: { fr: 'Vénus', color: '#f2cf8a', size: 5 },
  earth: { fr: 'Terre', color: '#4fa8ff', size: 5.4 },
  mars: { fr: 'Mars', color: '#ff7a4d', size: 4.2 },
  jupiter: { fr: 'Jupiter', color: '#e3b98a', size: 9.5 },
  saturn: { fr: 'Saturne', color: '#f0d9a0', size: 8.2, ring: true },
  uranus: { fr: 'Uranus', color: '#8fe3f0', size: 6.6 },
  neptune: { fr: 'Neptune', color: '#6b86ff', size: 6.4 },
  pluto: { fr: 'Pluton', color: '#c9b39b', size: 2.6 },
};
const ALIASES: Record<string, string> = {
  mercure: 'mercury', terre: 'earth', saturne: 'saturn', pluton: 'pluto', earthmoonbarycenter: 'earth', emb: 'earth',
};
const SKIP = new Set(['sun', 'soleil', 'moon', 'lune']);

export const bodyKey = (name: string) => {
  const k = name.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z]/g, '');
  return ALIASES[k] ?? k;
};

export interface SolarBody {
  key: string;
  name: string;
  style: PlanetStyle;
  el: Elements;
  rates: Rates | null;
  epochJd: number;
  serverPos: Vec3 | null;
}

export interface SolarModel {
  bodies: SolarBody[];
  refJd: number;
  refDeclared: boolean;
  /** Client vs server comparison at refJd (only when the server sent positions). */
  check: { maxErrAu: number; compared: number; interpretation: string } | null;
  incomplete: string[];
}

const RAD = Math.PI / 180;
const EL_NAMES: Record<keyof Elements | 'w' | 'M', string[]> = {
  a: ['a', 'a_au', 'semi_major_axis_au', 'semi_major_axis', 'sma_au'],
  e: ['e', 'ecc', 'eccentricity'],
  i: ['i', 'I', 'i_deg', 'inclination_deg', 'inclination', 'incl_deg', 'incl'],
  L: ['L', 'L_deg', 'mean_longitude_deg', 'mean_longitude', 'mean_lon_deg'],
  varpi: ['varpi', 'varpi_deg', 'long_peri', 'long_peri_deg', 'longitude_of_perihelion_deg', 'longitude_of_perihelion',
    'lon_peri_deg', 'perihelion_longitude_deg', 'w_bar', 'long_perihelion_deg'],
  Omega: ['Omega', 'Omega_deg', 'long_node', 'long_node_deg', 'longitude_of_ascending_node_deg', 'longitude_of_ascending_node',
    'node_deg', 'lan_deg', 'ascending_node_deg', 'raan_deg', 'node'],
  w: ['w', 'w_deg', 'omega', 'omega_deg', 'arg_peri', 'arg_peri_deg', 'argument_of_perihelion_deg', 'argument_of_periapsis_deg',
    'arg_perihelion_deg'],
  M: ['M', 'M_deg', 'mean_anomaly_deg', 'mean_anomaly', 'ma_deg'],
};
const RATE_SUFFIXES = ['_rate', '_dot', '_per_cy', '_per_century', '_rate_per_century', '_cy'];

function readAngleOrValue(o: Json, names: string[], suffixes: string[] = ['']): number | null {
  for (const n of names) {
    for (const s of suffixes) {
      const v = num(o[`${n}${s}`]);
      if (v !== null) return v;
      const r = num(o[`${n.replace(/_deg$/, '')}_rad${s}`]);
      if (r !== null && n.endsWith('_deg')) return r / RAD;
    }
  }
  return null;
}

type Partial6 = { [K in keyof Elements | 'w' | 'M']?: number | null };

function readSet(o: Json, suffixes?: string[]): Partial6 {
  const out: Partial6 = {};
  (Object.keys(EL_NAMES) as (keyof Partial6)[]).forEach(k => { out[k] = readAngleOrValue(o, EL_NAMES[k], suffixes); });
  return out;
}

function complete(set: Partial6): Elements | null {
  let { varpi, L } = set;
  const { a, e, i, Omega, w, M } = set;
  if (varpi == null && w != null && Omega != null) varpi = w + Omega;
  if (L == null && M != null && varpi != null) L = M + varpi;
  if (a == null || e == null || i == null || L == null || varpi == null || Omega == null) return null;
  if (!(a > 0) || e < 0 || e >= 1) return null;
  return { a, e, i, L, varpi, Omega };
}

function readRates(item: Json, el: Json): Rates | null {
  let perDay = false;
  let set: Partial6 | null = null;
  for (const key of ['rates', 'rates_per_century', 'element_rates', 'elements_rates', 'rates_per_day', 'dot']) {
    const holder = isObj(item[key]) ? item[key] as Json : isObj(el[key]) ? el[key] as Json : null;
    if (holder) { set = readSet(holder); perDay = key.includes('day'); break; }
  }
  if (!set) set = readSet(el, RATE_SUFFIXES);
  if (set.varpi == null && set.w != null && set.Omega != null) set.varpi = set.w + set.Omega;
  const rates: Rates = {};
  (['a', 'e', 'i', 'L', 'varpi', 'Omega'] as (keyof Elements)[]).forEach(k => {
    const v = set?.[k];
    if (v != null) rates[k] = perDay ? v * 36525 : v;
  });
  return Object.keys(rates).length ? rates : null;
}

function readJd(v: unknown): number | null {
  const n = num(v);
  if (n !== null) return n > 1e6 && n < 1e7 ? n : null;
  if (typeof v === 'string') {
    if (/^j\s*2000/i.test(v.trim())) return J2000_JD;
    const ms = Date.parse(v);
    if (Number.isFinite(ms)) return jdFromMs(ms);
  }
  return null;
}

function readServerPos(item: Json): Vec3 | null {
  const direct = (['x_au', 'y_au', 'z_au'] as const).map(k => num(item[k]));
  if (direct.every(v => v !== null)) return direct as Vec3;
  for (const k of ['position_au', 'xyz_au', 'heliocentric_au', 'position', 'pos', 'xyz', 'heliocentric', 'helio', 'ecliptic', 'r']) {
    const v = vec3(item[k]);
    if (v) return Math.hypot(...v) > 1000 ? v.map(c => c / AU_KM) as Vec3 : v;
  }
  const plain = (['x', 'y', 'z'] as const).map(k => num(item[k]));
  if (plain.every(v => v !== null)) return plain as Vec3;
  return null;
}

/** Parse the ToolResult data. Returns null when no body carries a complete set of elements. */
export function parseSolar(data: unknown, fallbackMs: number): SolarModel | null {
  const list = Array.isArray(data) ? data : pickArr(data, ['planets', 'bodies', 'objects', 'items', 'system.planets']);
  if (!list) return null;
  const declaredRef = readJd(getPath(data, 'jd')) ?? readJd(getPath(data, 'julian_day'))
    ?? readJd(pickStr(data, ['date', 'datetime', 'utc', 'time', 'at', 'requested_date', 'date_utc']));
  const refJd = declaredRef ?? jdFromMs(fallbackMs);
  const dataEpoch = readJd(getPath(data, 'epoch_jd')) ?? readJd(getPath(data, 'epoch'));

  const bodies: (SolarBody & { declaredEpoch: number | null })[] = [];
  const incomplete: string[] = [];
  for (const raw of list) {
    if (!isObj(raw)) continue;
    const name = pickStr(raw, ['name_fr', 'nom', 'name', 'label', 'id', 'key']) ?? '';
    const key = bodyKey(name);
    const parent = pickStr(raw, ['parent', 'center', 'central_body', 'orbits']);
    if (SKIP.has(key) || (parent && !['sun', 'soleil'].includes(bodyKey(parent)))) continue;
    const elObj = (['elements', 'orbital_elements', 'orbit', 'kepler', 'keplerian'] as const)
      .map(k => raw[k]).find(isObj) as Json | undefined ?? raw;
    const el = complete(readSet(elObj));
    if (!el) { if (name) incomplete.push(name); continue; }
    const style = PLANET_STYLES[key] ?? { fr: name || key, color: '#9fb8d8', size: 4 };
    bodies.push({
      key, name: PLANET_STYLES[key]?.fr ?? name, style, el,
      rates: readRates(raw, elObj),
      epochJd: refJd,
      declaredEpoch: readJd(elObj.epoch_jd) ?? readJd(raw.epoch_jd) ?? readJd(elObj.epoch) ?? readJd(raw.epoch) ?? dataEpoch,
      serverPos: readServerPos(raw) ?? readServerPos(elObj),
    });
  }
  if (!bodies.length) return null;

  // Which epoch do the elements refer to? Try each plausible reading and keep the one that
  // reproduces the server's positions; without positions, fall back to the declared epoch.
  const candidates: { label: string; epochOf: (b: (typeof bodies)[number]) => number }[] = [];
  if (bodies.some(b => b.declaredEpoch !== null)) candidates.push({ label: 'époque déclarée par le serveur', epochOf: b => b.declaredEpoch ?? refJd });
  candidates.push({ label: 'éléments valables à la date de référence', epochOf: () => refJd });
  if (bodies.some(b => b.rates)) candidates.push({ label: 'éléments J2000 + taux séculaires', epochOf: () => J2000_JD });

  const compared = bodies.filter(b => b.serverPos);
  let chosen = candidates[0];
  let check: SolarModel['check'] = null;
  if (compared.length) {
    let best = Infinity;
    for (const c of candidates) {
      const err = Math.max(...compared.map(b => dist(position(elementsAt(b.el, c.epochOf(b), refJd, b.rates)), b.serverPos as Vec3)));
      if (err < best) { best = err; chosen = c; }
    }
    check = { maxErrAu: best, compared: compared.length, interpretation: chosen.label };
  } else if (!bodies.some(b => b.declaredEpoch !== null) && bodies.some(b => b.rates)) {
    chosen = candidates.find(c => c.label.startsWith('éléments J2000')) ?? chosen;
  }
  const finalBodies: SolarBody[] = bodies.map(({ declaredEpoch: _d, ...b }) => ({ ...b, epochJd: chosen.epochOf({ ...b, declaredEpoch: _d }) }));
  finalBodies.sort((x, y) => x.el.a - y.el.a);
  return { bodies: finalBodies, refJd, refDeclared: declaredRef !== null, check, incomplete };
}

/** Position of a body at any Julian date, from its own elements. */
export const bodyPosition = (b: SolarBody, jd: number): Vec3 => position(elementsAt(b.el, b.epochJd, jd, b.rates));

/** Elements at a date (for drawing the osculating orbit). */
export const bodyElements = (b: SolarBody, jd: number): Elements => elementsAt(b.el, b.epochJd, jd, b.rates);
