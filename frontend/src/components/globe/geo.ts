// Spherical and astronomical helpers for the globe. Pure functions, no DOM, no network.

export const RAD = Math.PI / 180;
export const DEG = 180 / Math.PI;

export interface LatLon { lat: number; lon: number }

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
export const norm360 = (x: number) => ((x % 360) + 360) % 360;
export const norm180 = (x: number) => norm360(x + 180) - 180;

/**
 * Sub-solar point (where the Sun is at the zenith) for an instant.
 * Low-precision solar ephemeris from the Astronomical Almanac (≈0.01° between 1950 and 2050):
 * mean longitude / anomaly → ecliptic longitude → right ascension + declination, then GMST.
 */
export function subsolarPoint(date: Date): LatLon {
  const n = date.getTime() / 86_400_000 + 2_440_587.5 - 2_451_545.0; // days since J2000.0
  const L = norm360(280.460 + 0.9856474 * n);
  const g = norm360(357.528 + 0.9856003 * n) * RAD;
  const lambda = (L + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * RAD;
  const eps = (23.439 - 0.0000004 * n) * RAD;
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lambda), Math.cos(lambda)) * DEG;
  const dec = Math.asin(Math.sin(eps) * Math.sin(lambda)) * DEG;
  const gmst = norm360(280.46061837 + 360.98564736629 * n);
  return { lat: dec, lon: norm180(ra - gmst) };
}

export const antipode = (p: LatLon): LatLon => ({ lat: -p.lat, lon: norm180(p.lon + 180) });

/** Great-circle distance in degrees. */
export function angularDistanceDeg(a: LatLon, b: LatLon): number {
  const dLat = (b.lat - a.lat) * RAD;
  const dLon = (b.lon - a.lon) * RAD;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * RAD) * Math.cos(b.lat * RAD) * Math.sin(dLon / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(h))) * DEG;
}

/** True when the Sun is below the horizon at this point (geometric, no refraction). */
export const isNight = (p: LatLon, sun: LatLon) => angularDistanceDeg(p, sun) > 90;

const frNum = (v: number, digits: number) => v.toFixed(digits).replace('.', ',');

/** « 48,86° N » / « 12,05° S » */
export const formatLat = (lat: number, digits = 2) => `${frNum(Math.abs(lat), digits)}° ${lat >= 0 ? 'N' : 'S'}`;
/** « 2,35° E » / « 73,99° O » (ouest) */
export const formatLon = (lon: number, digits = 2) => `${frNum(Math.abs(norm180(lon)), digits)}° ${norm180(lon) >= 0 ? 'E' : 'O'}`;
export const formatLatLon = (lat: number, lon: number, digits = 2) => `${formatLat(lat, digits)} · ${formatLon(lon, digits)}`;

export const easeInOutCubic = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

/** Stable pseudo-random phase in [0, 1) from a string id (used to desynchronise pulses). */
export function phaseOf(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 1000) / 1000;
}
