// Keplerian propagation in the browser, from the SAME elements the server returned (/api/sim/solar).
// Heliocentric ecliptic J2000 frame, distances in AU, angles in degrees.

export const J2000_JD = 2451545.0;
export const DAY_MS = 86_400_000;
/** Gaussian gravitational constant expressed as mean motion at a = 1 AU, in degrees per day. */
export const GAUSS_DEG_PER_DAY = 0.9856076686;
export const AU_KM = 149_597_870.7;

export type Vec3 = [number, number, number];

export interface Elements {
  a: number;      // semi-major axis (AU)
  e: number;      // eccentricity
  i: number;      // inclination (deg)
  L: number;      // mean longitude (deg)
  varpi: number;  // longitude of perihelion (deg)
  Omega: number;  // longitude of ascending node (deg)
}
export type Rates = Partial<Elements>; // per Julian century

const RAD = Math.PI / 180;

export const jdFromMs = (ms: number) => ms / DAY_MS + 2440587.5;
export const msFromJd = (jd: number) => (jd - 2440587.5) * DAY_MS;

/** Normalise an angle to (-180, 180]. */
export function wrapDeg(d: number): number {
  const r = ((d + 180) % 360 + 360) % 360 - 180;
  return r === -180 ? 180 : r;
}

/** Solve E - e sin E = M (radians) by Newton iterations. */
export function solveKepler(M: number, e: number): number {
  let E = e < 0.8 ? M : Math.PI * Math.sign(M || 1);
  for (let k = 0; k < 30; k++) {
    const f = E - e * Math.sin(E) - M;
    const dE = f / (1 - e * Math.cos(E));
    E -= dE;
    if (Math.abs(dE) < 1e-12) break;
  }
  return E;
}

/** Elements at `jd`, given elements valid at `epochJd`. Rates (per century) when supplied, else
 *  Kepler's third law for the mean longitude (other elements held fixed). */
export function elementsAt(el: Elements, epochJd: number, jd: number, rates?: Rates | null): Elements {
  const days = jd - epochJd;
  if (rates && Object.keys(rates).length) {
    const T = days / 36525;
    return {
      a: el.a + (rates.a ?? 0) * T,
      e: el.e + (rates.e ?? 0) * T,
      i: el.i + (rates.i ?? 0) * T,
      L: el.L + (rates.L ?? meanMotionDegPerDay(el.a) * 36525) * T,
      varpi: el.varpi + (rates.varpi ?? 0) * T,
      Omega: el.Omega + (rates.Omega ?? 0) * T,
    };
  }
  return { ...el, L: el.L + meanMotionDegPerDay(el.a) * days };
}

export const meanMotionDegPerDay = (a: number) => GAUSS_DEG_PER_DAY / Math.pow(a, 1.5);
/** Sidereal period in days from the semi-major axis (heliocentric, planet mass neglected). */
export const periodDays = (a: number) => 360 / meanMotionDegPerDay(a);

/** Position in the orbital plane for eccentric anomaly E, then rotated to the ecliptic. */
function toEcliptic(el: Elements, E: number): Vec3 {
  const xp = el.a * (Math.cos(E) - el.e);
  const yp = el.a * Math.sqrt(Math.max(0, 1 - el.e * el.e)) * Math.sin(E);
  const w = (el.varpi - el.Omega) * RAD;
  const O = el.Omega * RAD;
  const I = el.i * RAD;
  const cw = Math.cos(w), sw = Math.sin(w), cO = Math.cos(O), sO = Math.sin(O), cI = Math.cos(I), sI = Math.sin(I);
  return [
    (cw * cO - sw * sO * cI) * xp + (-sw * cO - cw * sO * cI) * yp,
    (cw * sO + sw * cO * cI) * xp + (-sw * sO + cw * cO * cI) * yp,
    sw * sI * xp + cw * sI * yp,
  ];
}

/** Heliocentric ecliptic position (AU) for the given elements. */
export function position(el: Elements): Vec3 {
  const M = wrapDeg(el.L - el.varpi) * RAD;
  return toEcliptic(el, solveKepler(M, el.e));
}

/** Closed orbit sampled uniformly in eccentric anomaly. */
export function orbitPath(el: Elements, samples = 180): Vec3[] {
  const pts: Vec3[] = [];
  for (let k = 0; k <= samples; k++) pts.push(toEcliptic(el, (k / samples) * 2 * Math.PI));
  return pts;
}

export const norm = (v: Vec3) => Math.hypot(v[0], v[1], v[2]);
export const dist = (a: Vec3, b: Vec3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
