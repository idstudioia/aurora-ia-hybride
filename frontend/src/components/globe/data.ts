// Tolerant readers for the weather / quakes / news ToolResult payloads (CONTRACT §7–8).
// The contract fixes the ToolResult envelope but not the inner `data` keys, so each reader
// accepts the usual spellings (Open-Meteo, USGS GeoJSON, RSS) and drops anything it cannot
// read. Nothing is ever filled in: a missing value stays null and the UI shows « — ».
import type { Source, ToolResult } from '../../lib/types';
import { skyKindFromCode, skyKindFromLabel, wmoLabel, type SkyKind } from './sky';

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);

/** First defined value among dotted paths ('current.temperature_2m'). */
function pick(o: Obj, ...paths: string[]): unknown {
  for (const path of paths) {
    let cur: unknown = o;
    for (const part of path.split('.')) {
      if (!isObj(cur)) { cur = undefined; break; }
      cur = cur[part];
    }
    if (cur !== undefined && cur !== null && cur !== '') return cur;
  }
  return undefined;
}

export function num(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v.replace(',', '.'));
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** First path holding a number (skips e.g. `wind_direction: "nord"` when looking for degrees). */
function pickNum(o: Obj, ...paths: string[]): number | null {
  for (const path of paths) {
    const n = num(pick(o, path));
    if (n !== null) return n;
  }
  return null;
}

function text(v: unknown): string {
  if (typeof v === 'string') return v.trim();
  if (typeof v === 'number') return String(v);
  if (isObj(v)) return text(v.title ?? v.name ?? v.label ?? '');
  return '';
}

function bool(v: unknown): boolean | null {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'number') return v !== 0;
  if (typeof v === 'string') return ['1', 'true', 'oui', 'yes'].includes(v.toLowerCase());
  return null;
}

/** ISO string from an ISO / RFC 2822 string or an epoch (s or ms). Null if unparseable. */
export function toIso(v: unknown): string | null {
  if (typeof v === 'number' && Number.isFinite(v)) {
    const ms = v > 1e11 ? v : v * 1000;
    return new Date(ms).toISOString();
  }
  if (typeof v === 'string' && v.trim()) {
    const t = Date.parse(v);
    return Number.isNaN(t) ? null : new Date(t).toISOString();
  }
  return null;
}

/** True when an ISO-like string carries an explicit timezone (Z or ±hh:mm). */
const hasZone = (s: string) => /(Z|[+-]\d{2}:?\d{2})$/i.test(s.trim());

/** Only http(s) links are kept (no javascript:, data:, file: …). */
export function safeHttpUrl(v: unknown): string | null {
  if (typeof v !== 'string' || !v.trim()) return null;
  try {
    const u = new URL(v.trim());
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null;
  } catch { return null; }
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', hellip: '…', rsquo: '’', lsquo: '‘', laquo: '«', raquo: '»', ndash: '–', mdash: '—', eacute: 'é', egrave: 'è', agrave: 'à', ccedil: 'ç' };

/** External text → plain text (tags removed, entities decoded, whitespace collapsed). Never HTML. */
export function plainText(v: unknown, max = 0): string {
  let s = text(v);
  if (!s) return '';
  s = s.replace(/<(script|style)[\s\S]*?<\/\1>/gi, ' ').replace(/<[^>]*>/g, ' ');
  s = s.replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (m, e: string) => {
    if (e[0] === '#') {
      const code = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code < 0x110000 ? String.fromCodePoint(code) : ' ';
    }
    return ENTITIES[e.toLowerCase()] ?? m;
  });
  s = s.replace(/\s+/g, ' ').trim();
  if (max > 0 && s.length > max) s = `${s.slice(0, max - 1).replace(/\s+\S*$/, '')}…`;
  return s;
}

function listFrom(data: unknown, ...keys: string[]): unknown[] {
  if (Array.isArray(data)) return data;
  if (!isObj(data)) return [];
  for (const k of keys) {
    const v = data[k];
    if (Array.isArray(v)) return v;
  }
  return [];
}

/** Retrieval time: first source with retrieved_at, else null. */
export function retrievedAt(result: ToolResult | null | undefined): string | null {
  const s = result?.sources?.find(src => src.retrieved_at);
  return s ? toIso(s.retrieved_at) : null;
}

export function firstSource(result: ToolResult | null | undefined): Source | null {
  return result?.sources?.[0] ?? null;
}

// ---------------------------------------------------------------- weather

export interface WeatherNow {
  id: string;
  city: string;
  country: string;
  lat: number | null;
  lon: number | null;
  temperature: number | null;
  apparent: number | null;
  code: number | null;
  label: string;
  kind: SkyKind;
  isDay: boolean | null;
  windKmh: number | null;
  windDir: number | null;
  humidity: number | null;
  todayMin: number | null;
  todayMax: number | null;
  /** Observation instant (ISO UTC) when it can be determined without guessing the timezone. */
  observedAt: string | null;
}

/** Observation time → ISO UTC. Open-Meteo gives local wall time + `utc_offset_seconds`. */
function observationIso(raw: Obj): string | null {
  const v = pick(raw, 'observed_at', 'current.time', 'time', 'updated_at');
  if (typeof v === 'number') return toIso(v);
  if (typeof v !== 'string' || !v.trim()) return null;
  if (hasZone(v)) return toIso(v);
  const offset = pickNum(raw, 'utc_offset_seconds');
  if (offset === null || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(v.trim())) return null;
  const asUtc = Date.parse(`${v.trim()}Z`);
  return Number.isNaN(asUtc) ? null : new Date(asUtc - offset * 1000).toISOString();
}

const capitalize = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

export function readWeather(raw: unknown, fallbackId = ''): WeatherNow | null {
  if (!isObj(raw)) return null;
  if (raw.ok === false || (raw.error && pickNum(raw, 'temperature_c', 'temperature', 'current.temperature_2m') === null)) return null;
  const code = pickNum(raw, 'weather_code', 'weathercode', 'wmo_code', 'code', 'current.weather_code', 'current.weathercode');
  const label = capitalize(plainText(pick(raw, 'condition', 'label', 'description', 'conditions', 'weather_label', 'sky')) || wmoLabel(code));
  const kind = code !== null ? skyKindFromCode(code) : skyKindFromLabel(label);
  const city = plainText(pick(raw, 'city', 'name', 'location.name', 'place', 'town'), 80);
  const lat = pickNum(raw, 'latitude', 'lat', 'location.latitude', 'location.lat', 'coords.lat');
  const lon = pickNum(raw, 'longitude', 'lon', 'lng', 'location.longitude', 'location.lon', 'coords.lon');
  return {
    id: text(pick(raw, 'id', 'key')) || city || fallbackId,
    city,
    country: plainText(pick(raw, 'country', 'location.country', 'country_code'), 60),
    lat: lat !== null && Math.abs(lat) <= 90 ? lat : null,
    lon: lon !== null && Math.abs(lon) <= 180 ? lon : null,
    temperature: pickNum(raw, 'temperature_c', 'temperature', 'temp_c', 'temp', 'temperature_2m', 'current.temperature_2m', 'current.temperature'),
    apparent: pickNum(raw, 'apparent_temperature_c', 'apparent_c', 'apparent_temperature', 'feels_like_c', 'feels_like', 'current.apparent_temperature'),
    code,
    label,
    kind,
    isDay: bool(pick(raw, 'is_day', 'current.is_day')),
    windKmh: pickNum(raw, 'wind_kmh', 'wind_speed_kmh', 'wind_speed', 'windspeed', 'wind_speed_10m', 'current.wind_speed_10m', 'wind.speed_kmh', 'wind.speed'),
    windDir: pickNum(raw, 'wind_direction_deg', 'wind_direction', 'winddirection', 'wind_direction_10m', 'current.wind_direction_10m', 'wind.direction'),
    humidity: pickNum(raw, 'humidity_pct', 'humidity', 'humidity_percent', 'relative_humidity', 'relative_humidity_2m', 'current.relative_humidity_2m'),
    todayMin: pickNum(raw, 'today_min_c', 'temperature_min_c', 'min_c'),
    todayMax: pickNum(raw, 'today_max_c', 'temperature_max_c', 'max_c'),
    observedAt: observationIso(raw),
  };
}

/** Current weather for one city (`/api/weather`). The payload may be flat or nested under `current`. */
export function readCurrentWeather(result: ToolResult | null): WeatherNow | null {
  if (!result?.ok || !isObj(result.data)) return null;
  const d = result.data;
  const nested = isObj(d.weather) ? { ...d, ...d.weather } : d;
  const w = readWeather(nested, 'current');
  return w && (w.temperature !== null || w.label) ? w : null;
}

/** Weather of the globe cities (`/api/weather/cities`); entries without coordinates are dropped. */
export function readCityWeather(result: ToolResult | null): WeatherNow[] {
  if (!result?.ok) return [];
  return listFrom(result.data, 'cities', 'items', 'results', 'list', 'locations')
    .map((c, i) => readWeather(c, `c${i}`))
    .filter((w): w is WeatherNow => !!w && w.lat !== null && w.lon !== null && (w.temperature !== null || !!w.label));
}

// ---------------------------------------------------------------- earthquakes

export interface Quake {
  id: string;
  mag: number | null;
  magType: string;
  place: string;
  time: string | null;
  lat: number;
  lon: number;
  depthKm: number | null;
  url: string | null;
  tsunami: boolean;
  felt: number | null;
  alert: string;
}

function readQuake(raw: unknown, i: number): Quake | null {
  if (!isObj(raw)) return null;
  // Raw USGS GeoJSON feature, or an already flattened record.
  const props = isObj(raw.properties) ? raw.properties : raw;
  const coords = isObj(raw.geometry) && Array.isArray(raw.geometry.coordinates) ? raw.geometry.coordinates
    : Array.isArray(raw.coordinates) ? raw.coordinates : null;
  const lon = coords ? num(coords[0]) : pickNum(raw, 'lon', 'longitude', 'lng');
  const lat = coords ? num(coords[1]) : pickNum(raw, 'lat', 'latitude');
  if (lat === null || lon === null || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return {
    id: text(raw.id ?? props.id ?? props.code) || `q${i}`,
    mag: pickNum(props, 'mag', 'magnitude'),
    magType: text(pick(props, 'magType', 'mag_type')),
    place: plainText(pick(props, 'place', 'location', 'region', 'title'), 140),
    time: toIso(pick(props, 'time_iso', 'time', 'datetime', 'occurred_at', 'date')),
    lat, lon,
    depthKm: coords && coords.length > 2 ? num(coords[2]) : pickNum(props, 'depth_km', 'depth'),
    url: safeHttpUrl(pick(props, 'url', 'link', 'detail_url')),
    tsunami: bool(pick(props, 'tsunami')) === true,
    felt: pickNum(props, 'felt'),
    alert: text(pick(props, 'alert')),
  };
}

export interface QuakeFeed {
  quakes: Quake[];
  /** Feed title as given by the source (USGS titles are in English). */
  title: string;
  minMag: number | null;
  windowHours: number | null;
}

export function readQuakes(result: ToolResult | null): QuakeFeed {
  if (!result?.ok) return { quakes: [], title: '', minMag: null, windowHours: null };
  const d = result.data;
  const quakes = listFrom(d, 'quakes', 'events', 'earthquakes', 'items', 'features')
    .map(readQuake).filter((q): q is Quake => !!q);
  const o = isObj(d) ? d : {};
  return {
    quakes,
    title: plainText(pick(o, 'feed_title', 'title', 'feed', 'period', 'metadata.title'), 120),
    minMag: pickNum(o, 'min_magnitude', 'min_mag'),
    windowHours: pickNum(o, 'window_hours', 'hours'),
  };
}

/** French name of a USGS PAGER alert level. */
export const PAGER_LEVELS: Record<string, string> = { green: 'vert', yellow: 'jaune', orange: 'orange', red: 'rouge' };

// ---------------------------------------------------------------- news

export interface NewsItem {
  id: string;
  title: string;
  summary: string;
  url: string | null;
  image: string | null;
  published: string | null;
  source: string;
}

function readNewsItem(raw: unknown, i: number): NewsItem | null {
  if (!isObj(raw)) return null;
  const title = plainText(pick(raw, 'title', 'headline'), 220);
  if (!title) return null;
  const imageRaw = pick(raw, 'image', 'image_url', 'thumbnail', 'enclosure.url', 'enclosure', 'media.url', 'media');
  return {
    id: text(pick(raw, 'id', 'guid')) || `${i}-${title.slice(0, 40)}`,
    title,
    summary: plainText(pick(raw, 'summary', 'description', 'snippet', 'excerpt', 'content'), 320),
    url: safeHttpUrl(pick(raw, 'url', 'link', 'href')),
    image: safeHttpUrl(isObj(imageRaw) ? imageRaw.url : imageRaw),
    published: toIso(pick(raw, 'published', 'published_at', 'pubDate', 'date', 'updated', 'time')),
    source: plainText(pick(raw, 'source', 'feed_title', 'feed', 'publisher', 'site'), 80),
  };
}

export function readNews(result: ToolResult | null): NewsItem[] {
  if (!result?.ok) return [];
  return listFrom(result.data, 'items', 'articles', 'entries', 'news', 'results')
    .map(readNewsItem).filter((n): n is NewsItem => !!n);
}

/** Per-feed status when the news tool reports it (`data.feeds: [{title, ok}]`). */
export function readNewsFeeds(result: ToolResult | null): { ok: string[]; failed: string[] } {
  const out = { ok: [] as string[], failed: [] as string[] };
  if (!result?.ok) return out;
  for (const f of listFrom(result.data, 'feeds')) {
    if (!isObj(f)) continue;
    const title = plainText(pick(f, 'title', 'id'), 80);
    if (!title) continue;
    (f.ok === false ? out.failed : out.ok).push(title);
  }
  return out;
}
