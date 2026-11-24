// Small cards drawn from run.ui.data. The exact data shapes belong to the tool modules, so every
// accessor is tolerant; a missing value is shown as "—", never invented.
import { useState, type ReactNode } from 'react';
import { Activity, BookmarkPlus, Camera, Check, CloudSun, Cpu, Database, FileText, Globe, ListChecks, Newspaper, Orbit, RotateCcw, Search, Sheet, Sun } from 'lucide-react';
import { api } from '../../lib/api';
import { useAurora } from '../../lib/store';
import { photoCommons } from '../hologram/outils';
import type { DataAction, DataAnalysisView, ToolResult } from '../../lib/types';
import { fmtDateTime, fmtPct } from '../../lib/format';
import { Badge, RingGauge } from '../ui';
import { arr, domainOf, fmtBytes, fmtNum, isObj, num, obj, plainText, safeUrl, str, unwrapToolData } from './text';
import { useSend } from './useSend';

type Obj = Record<string, unknown>;

function Card({ icon, title, children, foot }: { icon: ReactNode; title: string; children: ReactNode; foot?: ReactNode }) {
  return (
    <div className="chat-widget">
      <div className="chat-widget-head">{icon}<span>{title}</span></div>
      <div className="chat-widget-body">{children}</div>
      {foot && <div className="chat-widget-foot">{foot}</div>}
    </div>
  );
}

function ExtLink({ url, children }: { url: unknown; children: ReactNode }) {
  const u = safeUrl(url);
  return u ? <a href={u} target="_blank" rel="noopener noreferrer nofollow">{children}</a> : <>{children}</>;
}

const DIAG_STATUS: Record<string, string> = { ok: 'aucun problème majeur', attention: 'points d’attention', critique: 'situation critique' };

function SystemWidget({ d }: { d: Obj }) {
  const s = obj(d, 'snapshot', 'system', 'current') ?? d;
  const gpu = obj(s, 'gpu');
  const causes = arr(d, 'causes').filter(isObj);
  const status = str(d, 'status');
  return (
    <Card icon={<Cpu size={15} />} title={status ? `Diagnostic · ${DIAG_STATUS[status] ?? status}` : 'Mesure système'}
      foot={causes.length ? (
        <ul className="chat-widget-causes">
          {causes.slice(0, 4).map((c, i) => <li key={i}>{plainText(str(c, 'title'))}{str(c, 'confidence') ? <small> · confiance {str(c, 'confidence')}</small> : null}</li>)}
        </ul>
      ) : undefined}>
      <div className="chat-widget-gauges">
        <RingGauge value={num(s, 'cpu_percent', 'cpu')} label="CPU" size={70} stroke={6} />
        <RingGauge value={num(s, 'ram_percent', 'ram')} label="RAM" color="violet" size={70} stroke={6} />
        <RingGauge value={num(s, 'disk_percent', 'disk')} label="Disque" color="green" size={70} stroke={6} />
      </div>
      <dl className="chat-kv chat-kv-inline">
        {num(s, 'ram_used_gb') !== null && <div><dt>RAM</dt><dd>{fmtNum(num(s, 'ram_used_gb'))} / {fmtNum(num(s, 'ram_total_gb'))} Go</dd></div>}
        {num(s, 'disk_used_gb') !== null && <div><dt>Disque</dt><dd>{fmtNum(num(s, 'disk_used_gb'), 0)} / {fmtNum(num(s, 'disk_total_gb'), 0)} Go</dd></div>}
        {num(s, 'net_down_kbps') !== null && <div><dt>Réseau</dt><dd>↓ {fmtNum(num(s, 'net_down_kbps'), 0)} · ↑ {fmtNum(num(s, 'net_up_kbps'), 0)} kbit/s</dd></div>}
        {num(s, 'battery_percent') !== null && <div><dt>Batterie</dt><dd>{fmtPct(num(s, 'battery_percent'))}</dd></div>}
        {gpu && <div><dt>GPU</dt><dd>{str(gpu, 'name') || '—'}{num(gpu, 'util_percent') !== null ? ` · ${fmtPct(num(gpu, 'util_percent'))}` : ''}</dd></div>}
      </dl>
    </Card>
  );
}

/** Times without a zone (Open-Meteo local time) are shown as received, not shifted to the browser zone. */
function localStamp(v: string): string {
  if (/(Z|[+-]\d{2}:?\d{2})$/i.test(v.trim())) return fmtDateTime(v);
  return `${v.replace('T', ' à ')} (heure locale)`;
}

function CitiesWeather({ cities }: { cities: Obj[] }) {
  return (
    <Card icon={<CloudSun size={15} />} title={`Météo · ${cities.length} ville(s)`}>
      <ul className="chat-widget-list">
        {cities.slice(0, 10).map((c, i) => (
          <li key={i}>
            <span>{str(c, 'name', 'city') || '—'}{str(c, 'country') ? <small> · {str(c, 'country')}</small> : null}</span>
            <small>{num(c, 'temperature_c', 'temperature') === null ? '—' : `${fmtNum(num(c, 'temperature_c', 'temperature'))} °C`}{str(c, 'condition') ? ` · ${plainText(str(c, 'condition'))}` : ''}</small>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function WeatherWidget({ d }: { d: Obj }) {
  const cities = arr(d, 'cities').filter(isObj);
  if (cities.length) return <CitiesWeather cities={cities} />;
  const cur = obj(d, 'current', 'weather') ?? d;
  const loc = obj(d, 'location', 'place') ?? d;
  const city = str(loc, 'city', 'name', 'location') || str(d, 'city', 'name');
  const country = str(loc, 'country', 'country_code');
  const temp = num(cur, 'temperature_c', 'temperature', 'temperature_2m', 'temp_c', 'temp');
  return (
    <Card icon={<CloudSun size={15} />} title={`Météo${city ? ` · ${city}` : ''}${country ? ` (${country})` : ''}`}
      foot={str(d, 'observed_at', 'time') ? `Mesure : ${localStamp(str(d, 'observed_at', 'time'))} · Open-Meteo` : undefined}>
      <div className="chat-weather">
        <strong className="chat-weather-temp">{temp === null ? '—' : `${fmtNum(temp)} °C`}</strong>
        <span>{plainText(str(cur, 'description', 'condition', 'label', 'summary', 'weather_label')) || '—'}</span>
      </div>
      <dl className="chat-kv chat-kv-inline">
        {num(cur, 'apparent_temperature_c', 'apparent_temperature', 'feels_like_c', 'feels_like') !== null && <div><dt>Ressenti</dt><dd>{fmtNum(num(cur, 'apparent_temperature_c', 'apparent_temperature', 'feels_like_c', 'feels_like'))} °C</dd></div>}
        {num(cur, 'wind_kmh', 'wind_speed_kmh', 'wind_speed_10m', 'windspeed', 'wind_speed') !== null && <div><dt>Vent</dt><dd>{fmtNum(num(cur, 'wind_kmh', 'wind_speed_kmh', 'wind_speed_10m', 'windspeed', 'wind_speed'), 0)} km/h</dd></div>}
        {num(cur, 'humidity_pct', 'humidity', 'relative_humidity', 'relative_humidity_2m', 'humidity_percent') !== null && <div><dt>Humidité</dt><dd>{fmtPct(num(cur, 'humidity_pct', 'humidity', 'relative_humidity', 'relative_humidity_2m', 'humidity_percent'))}</dd></div>}
        {num(cur, 'precipitation', 'precipitation_mm') !== null && <div><dt>Précipitations</dt><dd>{fmtNum(num(cur, 'precipitation', 'precipitation_mm'))} mm</dd></div>}
        {(num(d, 'today_min_c') !== null || num(d, 'today_max_c') !== null) && <div><dt>Aujourd’hui</dt><dd>{fmtNum(num(d, 'today_min_c'))} / {fmtNum(num(d, 'today_max_c'))} °C</dd></div>}
      </dl>
    </Card>
  );
}

type Pt = [number, number];
function toPoints(list: unknown[]): Pt[] {
  const pts: Pt[] = [];
  for (const p of list) {
    if (Array.isArray(p) && typeof p[0] === 'number' && typeof p[1] === 'number') pts.push([p[0], p[1]]);
    else if (isObj(p)) {
      const x = num(p, 'x', 'x_km', 'x_au');
      const y = num(p, 'y', 'y_km', 'y_au');
      if (x !== null && y !== null) pts.push([x, y]);
    }
  }
  return pts;
}

function OrbitWidget({ d }: { d: Obj }) {
  // sim.orbit nests its values (tools/orbit.py): body{name, radius_km}, initial{altitude_km,
  // eccentricity}, metrics{period_analytic_s, speed_periapsis_kms, periapsis_alt_km, apoapsis_alt_km}.
  const pts = toPoints(arr(d, 'points', 'trajectory', 'track', 'positions', 'path'));
  const radius = num(d, 'body.radius_km', 'body_radius_km', 'radius_km');
  const bodyName = str(d, 'body.name', 'body_name', 'body');
  const altitude = num(d, 'initial.altitude_km', 'altitude_km');
  const ecc = num(d, 'initial.eccentricity', 'eccentricity');
  const speedPeri = num(d, 'metrics.speed_periapsis_kms');
  const speed = speedPeri ?? num(d, 'velocity_km_s', 'speed_km_s', 'v_km_s', 'orbital_speed_km_s');
  const periapsis = num(d, 'metrics.periapsis_alt_km', 'periapsis_km', 'perigee_km');
  const apoapsis = num(d, 'metrics.apoapsis_alt_km', 'apoapsis_km', 'apogee_km');
  const periodMin = num(d, 'period_min') ?? ((num(d, 'metrics.period_analytic_s', 'period_s') ?? NaN) / 60);
  const period = Number.isFinite(periodMin) ? periodMin : (num(d, 'period_h') ?? NaN) * 60;
  let svg: ReactNode = null;
  if (pts.length > 2) {
    const ext = Math.max(...pts.map(([x, y]) => Math.max(Math.abs(x), Math.abs(y))), radius ?? 0) || 1;
    const sc = 46 / ext;
    const dPath = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${(x * sc).toFixed(2)},${(-y * sc).toFixed(2)}`).join(' ');
    svg = (
      <svg className="chat-orbit-svg" viewBox="-50 -50 100 100" role="img" aria-label="Trajectoire simulée">
        {radius !== null && radius > 0 && <circle r={Math.max(2, radius * sc)} className="chat-orbit-body" />}
        <path d={dPath} className="chat-orbit-path" />
        <circle cx={pts[pts.length - 1][0] * sc} cy={-pts[pts.length - 1][1] * sc} r={2.2} className="chat-orbit-probe" />
      </svg>
    );
  }
  return (
    <Card icon={<Orbit size={15} />} title={`Simulation orbitale${bodyName ? ` · ${bodyName}` : ''}`}>
      <div className="chat-widget-split">
        {svg}
        <dl className="chat-kv">
          {altitude !== null && <div><dt>Altitude</dt><dd>{fmtNum(altitude, 0)} km</dd></div>}
          {Number.isFinite(period) && <div><dt>Période</dt><dd>{fmtNum(period)} min</dd></div>}
          {speed !== null && <div><dt>{speedPeri !== null ? 'Vitesse au périapside' : 'Vitesse'}</dt><dd>{fmtNum(speed, 2)} km/s</dd></div>}
          {ecc !== null && <div><dt>Excentricité</dt><dd>{fmtNum(ecc, 3)}</dd></div>}
          {periapsis !== null && <div><dt>Périapside</dt><dd>{fmtNum(periapsis, 0)} km</dd></div>}
          {apoapsis !== null && <div><dt>Apoapside</dt><dd>{fmtNum(apoapsis, 0)} km</dd></div>}
        </dl>
      </div>
    </Card>
  );
}

function SolarWidget({ d }: { d: Obj }) {
  const planets = arr(d, 'planets', 'bodies').filter(isObj);
  const placed = planets.map(p => {
    const x = num(p, 'x_au', 'x');
    const y = num(p, 'y_au', 'y');
    const r = num(p, 'distance_au', 'r_au', 'distance') ?? (x !== null && y !== null ? Math.hypot(x, y) : null);
    return { name: str(p, 'name_fr', 'name', 'id'), x, y, r };
  });
  const drawable = placed.filter(p => p.x !== null && p.y !== null && p.r !== null && p.r > 0);
  const maxR = Math.max(...drawable.map(p => Math.sqrt(p.r!)), 1);
  return (
    <Card icon={<Sun size={15} />} title={`Système solaire${str(d, 'date') ? ` · ${str(d, 'date')}` : ''}`}>
      <div className="chat-widget-split">
        {drawable.length > 0 && (
          <svg className="chat-orbit-svg" viewBox="-52 -52 104 104" role="img" aria-label="Positions des planètes (échelle racine carrée)">
            <circle r={3.2} className="chat-solar-sun" />
            {drawable.map(p => {
              const k = (Math.sqrt(p.r!) / maxR) * 46;
              const a = Math.atan2(p.y!, p.x!);
              return (
                <g key={p.name}>
                  <circle r={k} className="chat-solar-ring" />
                  <circle cx={Math.cos(a) * k} cy={-Math.sin(a) * k} r={1.8} className="chat-solar-planet" />
                </g>
              );
            })}
          </svg>
        )}
        <ul className="chat-widget-list">
          {placed.slice(0, 9).map(p => <li key={p.name}><span>{p.name || '—'}</span><small>{p.r === null ? '—' : `${fmtNum(p.r, 2)} ua`}</small></li>)}
        </ul>
      </div>
    </Card>
  );
}

function quakeTime(q: Obj): string {
  const t = q.time_iso ?? q.time_utc ?? q.time;
  if (typeof t === 'number' && Number.isFinite(t)) return new Date(t).toISOString();
  if (typeof t === 'string' && t) return /^\d{10,}$/.test(t) ? new Date(Number(t)).toISOString() : t;
  return '';
}

function QuakesWidget({ d }: { d: Obj }) {
  const raw = arr(d, 'quakes', 'events', 'features', 'items');
  const list = raw.filter(isObj).map(q => (isObj(q.properties) ? { ...q.properties, ...q } : q) as Obj);
  return (
    <Card icon={<Activity size={15} />} title={`Séismes récents${num(d, 'count') !== null ? ` · ${num(d, 'count')}` : ''}`}>
      {list.length === 0 ? <div className="muted">Aucun séisme dans les données reçues.</div> : (
        <ul className="chat-widget-list">
          {list.slice(0, 6).map((q, i) => (
            <li key={i}>
              <span><strong className="chat-quake-mag">M {fmtNum(num(q, 'mag', 'magnitude'))}</strong> <ExtLink url={q.url}>{plainText(str(q, 'place', 'location', 'title')) || '—'}</ExtLink></span>
              <small>{quakeTime(q) ? fmtDateTime(quakeTime(q)) : ''}{num(q, 'depth_km', 'depth') !== null ? ` · ${fmtNum(num(q, 'depth_km', 'depth'), 0)} km` : ''}</small>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ProcessesWidget({ d }: { d: Obj }) {
  const list = arr(d, 'processes', 'items', 'top').filter(isObj);
  return (
    <Card icon={<Cpu size={15} />} title="Processus">
      {list.length === 0 ? <div className="muted">Aucun processus dans les données reçues.</div> : (
        <table className="chat-widget-table">
          <thead><tr><th>Nom</th><th>PID</th><th>CPU</th><th>Mémoire</th></tr></thead>
          <tbody>
            {list.slice(0, 8).map((p, i) => (
              <tr key={i}><td>{str(p, 'name') || '—'}</td><td className="mono">{num(p, 'pid') ?? '—'}</td><td>{fmtPct(num(p, 'cpu_percent'), 1)}</td><td>{num(p, 'memory_mb') === null ? '—' : `${fmtNum(num(p, 'memory_mb'), 0)} Mo`}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function SearchWidget({ d }: { d: Obj }) {
  const isNews = Array.isArray(d.feeds) || 'topic' in d;
  const instant = obj(d, 'instant');
  const list = [...arr(d, 'results', 'items', 'hits', 'pages').filter(isObj), ...(instant ? [instant] : [])];
  const subject = str(d, 'query', 'topic');
  return (
    <Card icon={isNews ? <Newspaper size={15} /> : <Search size={15} />} title={`${isNews ? 'Actualités scientifiques' : 'Recherche web'}${subject ? ` · « ${plainText(subject)} »` : ''}`}>
      {list.length === 0 ? <div className="muted">Aucun résultat dans les données reçues.</div> : (
        <ul className="chat-widget-results">
          {list.slice(0, 5).map((r, i) => (
            <li key={i}>
              <ExtLink url={r.url ?? r.link}>{plainText(str(r, 'title')) || '—'}</ExtLink>
              <small>{[domainOf(r.url ?? r.link), plainText(str(r, 'feed_title', 'origin'))].filter(Boolean).join(' · ')}</small>
              {str(r, 'extract', 'snippet', 'summary', 'description') && <p>{plainText(str(r, 'extract', 'snippet', 'summary', 'description')).slice(0, 220)}</p>}
              {arr(r, 'injection_signals').length > 0 && <p className="chat-warn">⚠ Ce contenu contient des formulations ressemblant à des instructions : il est traité comme simple donnée.</p>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** Size in bytes or, as files.quarantine reports it, in MiB rounded to 0.01 (size_mb = size / 1024²). */
const sizeText = (o: unknown, byteKeys: string[], mbKeys: string[]): string | null => {
  const b = num(o, ...byteKeys);
  if (b !== null) return fmtBytes(b);
  const mb = num(o, ...mbKeys);
  return mb === null ? null : mb === 0 ? '< 0,01 Mo' : fmtBytes(mb * 1024 * 1024);
};

function FilesWidget({ d, runId }: { d: Obj; runId: number }) {
  // files.scan lists {path, size_bytes…}; files.quarantine lists moved[{original, stored, size_mb}].
  const list = arr(d, 'files', 'items', 'candidates', 'moved').filter(x => isObj(x) || typeof x === 'string');
  const total = sizeText(d, ['total_bytes', 'total_size_bytes', 'size_bytes'], ['moved_total_mb', 'total_mb']);
  const count = num(d, 'count', 'file_count') ?? list.length;
  const quarantined = !!(str(d, 'quarantine_dir', 'manifest', 'manifest_path') || num(d, 'moved_count') || Array.isArray(d.moved));
  const [restore, setRestore] = useState<{ busy: boolean; result: ToolResult | null; error: string }>({ busy: false, result: null, error: '' });
  const doRestore = async () => {
    setRestore({ busy: true, result: null, error: '' });
    try { setRestore({ busy: false, result: await api.restoreFiles(runId), error: '' }); }
    catch (err) { setRestore({ busy: false, result: null, error: err instanceof Error ? err.message : 'Restauration impossible' }); }
  };
  return (
    <Card icon={<FileText size={15} />} title={`Fichiers · ${fmtNum(count, 0)}${total !== null ? ` · ${total}` : ''}`}
      foot={quarantined ? (
        <span className="chat-widget-restore">
          <button type="button" className="btn btn-sm" onClick={() => void doRestore()} disabled={restore.busy || !!restore.result?.ok}><RotateCcw size={13} /> {restore.busy ? 'Restauration…' : 'Restaurer les fichiers'}</button>
          {restore.result && <span className={restore.result.ok ? 'chat-ok' : 'chat-warn'}>{plainText(restore.result.summary || restore.result.error || '')}</span>}
          {restore.error && <span className="chat-warn">{restore.error}</span>}
        </span>
      ) : undefined}>
      {list.length === 0 ? <div className="muted">Aucun fichier dans les données reçues.</div> : (
        <ul className="chat-widget-list">
          {list.slice(0, 8).map((f, i) => (
            <li key={i}>
              <span className="mono chat-ellipsis">{typeof f === 'string' ? f : str(f, 'path', 'name', 'original') || '—'}</span>
              <small>{typeof f === 'string' ? '' : [sizeText(f, ['size_bytes', 'size'], ['size_mb']) ?? '', num(f, 'age_days') !== null ? `${fmtNum(num(f, 'age_days'), 0)} j` : ''].filter(x => x && x !== '—').join(' · ')}</small>
            </li>
          ))}
          {list.length > 8 && <li className="muted">… et {list.length - 8} autre(s)</li>}
        </ul>
      )}
    </Card>
  );
}

function MemoryWidget({ d }: { d: Obj }) {
  const list = arr(d, 'hits', 'memories', 'results', 'items').filter(isObj);
  return (
    <Card icon={<Database size={15} />} title="Mémoire">
      {list.length === 0 ? <div className="muted">Aucun souvenir correspondant.</div> : (
        <ul className="chat-widget-list">
          {list.slice(0, 6).map((h, i) => {
            const m = obj(h, 'memory') ?? h;
            const score = num(h, 'score');
            return (
              <li key={i}>
                <span><strong>{plainText(str(m, 'title')) || '—'}</strong>{str(m, 'category') && <small> · {str(m, 'category')}</small>}</span>
                <small>{score !== null ? `pertinence ${fmtNum(score, 2)}` : ''}{str(m, 'created_at') ? ` · ${fmtDateTime(str(m, 'created_at'))}` : ''}</small>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

// ---- P3 « Analyse de données » (intent data.analyze, ui.widget 'data': DataAnalysisView) ----
const CONFIDENCE_TONE: Record<string, 'green' | 'amber' | 'default'> = { 'élevée': 'green', moyenne: 'amber', faible: 'default' };
const count = (n: number | null, one: string, many: string) => `${fmtNum(n, 0)} ${n === 1 ? one : many}`;
const texts = (list: unknown[]) => list.filter((s): s is string => typeof s === 'string' && !!s.trim());

/** Key figures as received: a missing value stays null (« — »); digits kept in 0–6 for toLocaleString. */
function readHighlights(d: Obj): DataAnalysisView['highlights'] {
  return arr(d, 'highlights').filter(isObj).slice(0, 6).map(h => ({
    label: str(h, 'label'), value: num(h, 'value'), unit: str(h, 'unit'), note: str(h, 'note'),
    digits: Math.min(6, Math.max(0, Math.round(num(h, 'digits') ?? 1))),
  }));
}

/** An unknown kind, or a memory / routine action without its sentence or steps, gets no button (kind 'info'). */
function readActions(d: Obj): DataAction[] {
  return arr(d, 'actions').filter(isObj).map((a, i) => {
    const r = obj(a, 'routine');
    const steps = r ? texts(arr(r, 'steps')) : [];
    const routine = r && str(r, 'name') && steps.length ? { name: str(r, 'name'), steps } : undefined;
    const memoryText = str(a, 'memory_text');
    const kind = str(a, 'kind') === 'memory' && memoryText ? 'memory' : str(a, 'kind') === 'routine' && routine ? 'routine' : 'info';
    return {
      id: str(a, 'id') || String(i), title: str(a, 'title'), proof: str(a, 'proof'), effect: str(a, 'effect'),
      confidence: str(a, 'confidence') as DataAction['confidence'], kind, memory_text: memoryText || undefined, routine,
    };
  });
}

const ACTION_LABELS = {
  memory: { idle: 'Retenir', busy: 'Envoi…', done: 'Envoyé' },
  routine: { idle: 'Créer la routine', busy: 'Création…', done: 'Routine créée' },
};

/** One proposed action. Nothing leaves before the inline « Oui »: memory = a typed message, routine = POST /api/routines. */
function DataActionCard({ a }: { a: DataAction }) {
  const send = useSend();
  const [step, setStep] = useState<'idle' | 'confirm' | 'busy' | 'done'>('idle');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const confirm = async () => {
    setStep('busy'); setMsg(null);
    if (a.kind === 'memory') {
      // Sent exactly as if typed (same path as the chat suggestions), without the composer's pending attachments.
      const run = await send(`Retiens que ${a.memory_text}`, { source: 'text', withAttachments: false });
      setStep(run ? 'done' : 'idle');
      if (!run) setMsg({ ok: false, text: 'Envoi impossible : la phrase n’est pas partie.' });
      return;
    }
    if (!a.routine) return;
    try {
      const saved = await api.createRoutine({ name: a.routine.name, triggers: [], steps: a.routine.steps });
      setStep('done'); setMsg({ ok: true, text: `Routine « ${saved.name} » créée : Paramètres › Routines` });
    } catch (err) {
      setStep('idle'); setMsg({ ok: false, text: err instanceof Error ? err.message : 'Création de la routine impossible' });
    }
  };
  const labels = a.kind === 'info' ? null : ACTION_LABELS[a.kind];
  return (
    <li className="chat-data-action">
      <div className="chat-data-action-head">
        <strong>{a.title || '—'}</strong>
        <Badge tone={CONFIDENCE_TONE[a.confidence] ?? 'default'}>confiance {a.confidence || '—'}</Badge>
      </div>
      <p><span>Preuve : </span>{a.proof || '—'}</p>
      <p><span>Ce qu’elle ferait : </span>{a.effect || '—'}</p>
      {labels && (step === 'confirm' ? (
        <div className="chat-data-confirm" role="alertdialog" aria-label={a.kind === 'memory' ? 'Confirmer le souvenir' : 'Confirmer la routine'}>
          {a.kind === 'memory' ? <span>Retenir : « {a.memory_text} » ?</span> : (
            <>
              <span>Créer la routine « {a.routine?.name} » ? Elle est enregistrée sans être lancée. Étapes :</span>
              <ol>{a.routine?.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
            </>
          )}
          <button type="button" className="btn btn-sm btn-primary" onClick={() => void confirm()}>Oui</button>
          <button type="button" className="btn btn-sm" onClick={() => setStep('idle')} autoFocus>Non</button>
        </div>
      ) : (
        <div className="chat-data-do">
          <button type="button" className="btn btn-sm" disabled={step !== 'idle'} onClick={() => { setMsg(null); setStep('confirm'); }}>
            {step === 'done' ? <Check size={13} /> : a.kind === 'memory' ? <BookmarkPlus size={13} /> : <ListChecks size={13} />} {labels[step]}
          </button>
          {msg && <span className={msg.ok ? 'chat-ok' : 'chat-warn'} role={msg.ok ? 'status' : 'alert'}>{msg.text}</span>}
        </div>
      ))}
    </li>
  );
}

function DataWidget({ d }: { d: Obj }) {
  const ds = obj(d, 'dataset') ?? {};
  const rows = num(ds, 'rows');
  const columns = num(ds, 'columns');
  const skipped = num(ds, 'skipped');
  const source = str(ds, 'source');
  const highlights = readHighlights(d);
  const actions = readActions(d);
  return (
    <Card icon={<Sheet size={15} />} title={`Analyse · ${str(ds, 'name') || '—'}`}
      foot="Chiffres et preuves calculés par AURORA sur ce PC, pas par l’IA. Aucune action ne part sans votre confirmation.">
      <div className="chat-data-meta">
        <span>{count(rows, 'ligne', 'lignes')} · {count(columns, 'colonne', 'colonnes')}{skipped !== null && skipped > 0 ? ` · ${count(skipped, 'ligne ignorée', 'lignes ignorées')}` : ''}</span>
        {source === 'fichier' && <span>Fichier : <span className="mono">{str(ds, 'path') || '—'}</span></span>}
        {source === 'jointe' && <span>Fichier joint au message</span>}
      </div>
      <div className="chat-run-label">Chiffres clés</div>
      {highlights.length === 0 ? <div className="muted">Aucun chiffre clé dans les données reçues.</div> : (
        <dl className="chat-data-figures">
          {highlights.map((h, i) => (
            <div key={i}>
              <dt>{h.label || '—'}</dt>
              <dd>{h.value === null ? '—' : `${fmtNum(h.value, h.digits)}${h.unit ? ` ${h.unit}` : ''}`}</dd>
              {h.note && <dd className="chat-data-note">{h.note}</dd>}
            </div>
          ))}
        </dl>
      )}
      <div className="chat-run-label">Actions proposées</div>
      {actions.length === 0 ? <div className="muted">Aucune action proposée pour ces données.</div> : (
        <ul className="chat-data-actions">{actions.map((a, i) => <DataActionCard key={`${a.id}-${i}`} a={a} />)}</ul>
      )}
    </Card>
  );
}

/** 08/11/2026 (the owner: « histoire du Maroc », des images du sujet): run.ui.lieux, the photos of the World Heritage
 *  sites of the place the question names. Loaded from Wikimedia Commons by the browser, so never in Private mode (nor
 *  while the mode is unknown). A click shows the place on the globe of Monde 3D. */
function LieuxWidget({ d }: { d: Obj }) {
  const { settings, health, showOnGlobe } = useAurora();
  const mode = settings?.mode ?? health?.capabilities?.mode ?? null;
  const photos = arr(d.photos).flatMap(p => {
    const img = str(p, 'img'), lat = num(p, 'la'), lon = num(p, 'lo');
    return img && lat !== null && lon !== null ? [{ nom: plainText(str(p, 'nom')) || 'Lieu', img, lat, lon }] : [];
  }).slice(0, 6);
  if (!photos.length || mode === null || mode === 'prive') return null;
  return (
    <Card icon={<Camera size={15} />} title={`En images : ${plainText(d.titre) || 'le lieu'}`}
      foot="Lieux du patrimoine mondial (Wikidata) · photos Wikimedia Commons : auteur et licence sur la page de chaque photo. Un clic montre le lieu sur le globe.">
      <ul className="chat-lieux">
        {photos.map(({ nom, img, lat, lon }, i) => {
          return (
            <li key={`${img}-${i}`}>
              <button type="button" className="chat-lieu" title={`Voir ${nom} sur le globe`}
                onClick={() => showOnGlobe({ focus: { lat, lon, nom }, couche: 'guide' })}>
                <img src={photoCommons(img, 320)} alt={nom} loading="lazy" referrerPolicy="no-referrer"
                  onError={e => { const li = e.currentTarget.closest('li'); if (li) li.style.display = 'none'; }} />
                <span>{nom}</span>
              </button>
              <span className="chat-lieu-source">
                <ExtLink url={`https://commons.wikimedia.org/wiki/File:${encodeURIComponent(img)}`}>Commons</ExtLink>
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function GenericWidget({ d, name }: { d: Obj; name: string }) {
  const entries = Object.entries(d).filter(([k, v]) => !k.startsWith('_') && (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean')).slice(0, 10);
  if (!entries.length) return null;
  return (
    <Card icon={<Globe size={15} />} title={name}>
      <dl className="chat-kv">{entries.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{typeof v === 'boolean' ? (v ? 'oui' : 'non') : plainText(v)}</dd></div>)}</dl>
    </Card>
  );
}

export default function RunWidget({ widget, data, runId }: { widget: string; data: unknown; runId: number }) {
  const d = unwrapToolData(data);
  if (d._ok === false) {
    return <div className="unavailable"><strong>Données indisponibles</strong><span>{plainText(d._error ?? d._summary ?? '')}</span></div>;
  }
  switch (widget) {
    case 'system': return <SystemWidget d={d} />;
    case 'weather': return <WeatherWidget d={d} />;
    case 'orbit': return <OrbitWidget d={d} />;
    case 'solar': return <SolarWidget d={d} />;
    case 'quakes': return <QuakesWidget d={d} />;
    case 'processes': return <ProcessesWidget d={d} />;
    case 'search': return <SearchWidget d={d} />;
    case 'files': return <FilesWidget d={d} runId={runId} />;
    case 'memory': return <MemoryWidget d={d} />;
    case 'data': return <DataWidget d={d} />;
    case 'lieux': return <LieuxWidget d={d} />;
    default: return <GenericWidget d={d} name={widget} />;
  }
}
