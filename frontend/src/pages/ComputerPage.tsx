// "Mon ordinateur": live psutil measurements, history curves, processes and diagnostics.
// Every number comes from the local service; actions (stop a process, diagnose) go through ask().
import './computer.css';
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from 'react';
import { Activity, ArrowDownUp, BatteryMedium, Clock3, Cpu, HardDrive, MemoryStick, MessageSquare, OctagonX, RefreshCw, Stethoscope, Thermometer } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import { fmtDateTime, fmtTime, relativeTime, ENGINE_LABELS } from '../lib/format';
import { fmtMsFr } from '../lib/numfr';
import { isObj, pickArr, pickStr } from '../lib/tolerant';
import type { ProcessInfo, RunResult, SystemPoint, TaskItem } from '../lib/types';
import { Badge, EmptyState, Panel, RingGauge, Spinner, Unavailable } from '../components/ui';
import { useElementSize } from '../components/sim/hooks';
import { askOptions } from '../components/chat/prefs';

// ---------- formatting ----------
/** French number, or an em dash when the measurement is missing. */
const nf = (v: number | null | undefined, d = 1) => (typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('fr-FR', { maximumFractionDigits: d }) : '—');
const fmtGb = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${nf(v, 1)} Go`);
const fmtKbps = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v >= 1000 ? `${nf(v / 1000, 2)} Mbit/s` : `${nf(v, 0)} kbit/s`);
function fmtUptime(s: number | null | undefined) {
  if (s === null || s === undefined || !Number.isFinite(s)) return '—';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} j ${h} h ${m} min` : h ? `${h} h ${m} min` : `${m} min`;
}

// ---------- history chart (fixed or auto domain, gaps for missing samples, crosshair tooltip) ----------
type Sample = number | null | undefined;
interface Series { label: string; color: string; values: Sample[] }
const ok = (v: Sample): v is number => typeof v === 'number' && Number.isFinite(v);

function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

function HistoryChart({ series, times, max, format, height = 132, title }: {
  series: Series[]; times: string[]; max?: number; format: (v: number) => string; height?: number; title: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { w } = useElementSize(ref);
  const [hover, setHover] = useState<number | null>(null);
  const n = times.length;
  const top = max ?? niceMax(Math.max(1, ...series.flatMap(s => s.values.filter(ok))));
  const padL = 52, padR = 10, padT = 8, padB = 20;
  const iw = Math.max(10, w - padL - padR), ih = height - padT - padB;
  const x = (i: number) => padL + (n <= 1 ? iw : (i / (n - 1)) * iw);
  const y = (v: number) => padT + ih - (Math.max(0, Math.min(top, v)) / top) * ih;
  const ticks = [0, top / 2, top];
  /** Line and area paths, broken wherever a sample is missing. */
  const paths = (vals: Sample[]) => {
    let line = '', area = '', runStart = -1;
    const close = (end: number) => { if (runStart >= 0) area += ` L${x(end).toFixed(1)},${padT + ih} L${x(runStart).toFixed(1)},${padT + ih} Z`; };
    vals.forEach((v, i) => {
      if (!ok(v)) { close(i - 1); runStart = -1; return; }
      const pt = `${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      if (runStart < 0) { runStart = i; line += ` M${pt}`; area += ` M${pt}`; } else { line += ` L${pt}`; area += ` L${pt}`; }
    });
    close(vals.length - 1);
    return { line, area };
  };

  const onMove = (ev: PointerEvent<SVGSVGElement>) => {
    if (n < 2) return;
    const rect = ev.currentTarget.getBoundingClientRect();
    const i = Math.round(((ev.clientX - rect.left - padL) / iw) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };

  return (
    <div className="cmp-chart" ref={ref}>
      {series.length > 1 && (
        <div className="cmp-legend" aria-hidden="true">
          {series.map(s => <span key={s.label}><i style={{ background: s.color }} />{s.label}</span>)}
        </div>
      )}
      {w > 0 && n >= 2 ? (
        <svg width={w} height={height} role="img" aria-label={`${title} — ${n} mesures`} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
          {ticks.map(t => (
            <g key={t}>
              <line x1={padL} x2={padL + iw} y1={y(t)} y2={y(t)} className="cmp-gridline" />
              <text x={padL - 6} y={y(t)} className="cmp-axis" textAnchor="end" dominantBaseline="middle">{format(t)}</text>
            </g>
          ))}
          <text x={padL} y={height - 4} className="cmp-axis">{fmtTime(times[0])}</text>
          <text x={padL + iw} y={height - 4} className="cmp-axis" textAnchor="end">{fmtTime(times[n - 1])}</text>
          {series.map(s => {
            const { line, area } = paths(s.values);
            const last = s.values[n - 1];
            return (
              <g key={s.label}>
                <path d={area} fill={s.color} opacity={0.1} />
                <path d={line} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                {ok(last) && <circle cx={x(n - 1)} cy={y(last)} r={4} fill={s.color} stroke="#081830" strokeWidth={2} />}
              </g>
            );
          })}
          {hover !== null && (
            <g pointerEvents="none">
              <line x1={x(hover)} x2={x(hover)} y1={padT} y2={padT + ih} className="cmp-crosshair" />
              {series.map(s => { const v = s.values[hover]; return ok(v) ? <circle key={s.label} cx={x(hover)} cy={y(v)} r={4} fill={s.color} stroke="#081830" strokeWidth={2} /> : null; })}
            </g>
          )}
        </svg>
      ) : <div className="cmp-chart-empty" style={{ height }}>{n < 2 ? 'Historique en cours de constitution…' : ''}</div>}
      {hover !== null && n >= 2 && (
        <div className="cmp-tooltip" style={{ left: Math.min(Math.max(x(hover), 70), Math.max(70, w - 70)) }}>
          <strong>{fmtTime(times[hover])}</strong>
          {series.map(s => { const v = s.values[hover]; return <span key={s.label}><i style={{ background: s.color }} />{s.label} : {ok(v) ? format(v) : 'non mesuré'}</span>; })}
        </div>
      )}
    </div>
  );
}

// ---------- diagnosis (system.diagnose data: findings, causes, suggestions) ----------
interface DiagItem { title: string; detail: string; tag: string | null }
interface Suggestion { title: string; benefit: string; risk: string; kill: { pid: number | null; name: string } | null }
interface Diagnosis { status: string | null; summary: string | null; findings: DiagItem[]; causes: DiagItem[]; suggestions: Suggestion[] }

function readItems(list: unknown[] | null, tagKeys: string[]): DiagItem[] {
  const out: DiagItem[] = [];
  for (const item of list ?? []) {
    if (typeof item === 'string') out.push({ title: item, detail: '', tag: null });
    else if (isObj(item)) {
      const title = pickStr(item, ['title', 'label', 'cause', 'name', 'message', 'text']) ?? '';
      const detail = pickStr(item, ['detail', 'description', 'explanation']) ?? '';
      if (title || detail) out.push({ title: title || detail, detail: title ? detail : '', tag: pickStr(item, tagKeys) });
    }
  }
  return out;
}

/** The diagnose tool's data, from its step result when available, else from run.ui.data. */
function readDiagnosis(run: RunResult | null): Diagnosis | null {
  if (!run) return null;
  const candidates: unknown[] = [];
  const step = run.steps?.find(s => s.tool === 'system.diagnose');
  if (step?.result_json) {
    try { const r = JSON.parse(step.result_json); candidates.push(isObj(r) && isObj(r.data) ? r.data : r); } catch { /* résultat illisible */ }
  }
  candidates.push(run.ui?.data);
  const data = candidates.find(c => pickArr(c, ['findings', 'causes', 'issues', 'suggestions']) !== null);
  if (!data) return null;
  const suggestions: Suggestion[] = [];
  for (const s of pickArr(data, ['suggestions', 'recommendations', 'advice']) ?? []) {
    if (typeof s === 'string') { suggestions.push({ title: s, benefit: '', risk: '', kill: null }); continue; }
    if (!isObj(s)) continue;
    const action = isObj(s.action) ? s.action : null;
    const params = action && isObj(action.params) ? action.params : null;
    const kill = action && action.tool === 'process.terminate' && params
      ? { pid: typeof params.pid === 'number' ? params.pid : null, name: typeof params.name === 'string' ? params.name : '' } : null;
    suggestions.push({ title: pickStr(s, ['title', 'label', 'text']) ?? '', benefit: pickStr(s, ['benefit']) ?? '', risk: pickStr(s, ['risk']) ?? '', kill });
  }
  return {
    status: pickStr(data, ['status']),
    summary: pickStr(data, ['summary']),
    findings: readItems(pickArr(data, ['findings', 'issues', 'constats']), ['severity', 'level']),
    causes: readItems(pickArr(data, ['causes']), ['confidence', 'likelihood']),
    suggestions: suggestions.filter(s => s.title),
  };
}
const sevTone = (s: string | null): 'red' | 'amber' | 'green' | 'cyan' | 'default' => {
  const k = (s ?? '').toLowerCase();
  if (/(crit|severe|grave|error|élev|elev|high)/.test(k)) return 'red';
  if (/(warn|attention|moy|med)/.test(k)) return 'amber';
  if (/^(ok|low|faible)/.test(k)) return 'green';
  if (/info/.test(k)) return 'cyan';
  return 'default';
};
const SEV_LABELS: Record<string, string> = { critical: 'critique', warning: 'attention', info: 'info', high: 'élevée', medium: 'moyenne', low: 'faible', ok: 'ok' };
const sevLabel = (s: string) => SEV_LABELS[s.toLowerCase()] ?? s;

// ---------- page ----------
export default function ComputerPage() {
  const { system, online, ask, setChatOpen } = useAurora();
  const [history, setHistory] = useState<SystemPoint[] | null>(null);
  const [historyError, setHistoryError] = useState('');
  const [procs, setProcs] = useState<ProcessInfo[] | null>(null);
  const [procError, setProcError] = useState('');
  const [sort, setSort] = useState<'cpu' | 'memory'>('cpu');
  const [stopping, setStopping] = useState<number | null>(null);
  const [diag, setDiag] = useState<RunResult | null>(null);
  const [diagBusy, setDiagBusy] = useState(false);
  const [diagError, setDiagError] = useState('');
  const [pastDiags, setPastDiags] = useState<TaskItem[] | null>(null);

  // history every 3 s
  useEffect(() => {
    let alive = true;
    const tick = () => api.systemHistory()
      .then(r => { if (alive) { setHistory(Array.isArray(r?.points) ? r.points : []); setHistoryError(''); } })
      .catch(err => { if (alive) { setHistory(null); setHistoryError(err instanceof Error ? err.message : 'Service indisponible'); } });
    tick();
    const t = window.setInterval(tick, 3000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  // processes every 5 s
  const loadProcs = useCallback(async (s: 'cpu' | 'memory') => {
    try {
      const r = await api.processes(15, s);
      setProcs(Array.isArray(r?.processes) ? r.processes : []);
      setProcError('');
    } catch (err) {
      setProcs(null);
      setProcError(err instanceof Error ? err.message : 'Service indisponible');
    }
  }, []);
  useEffect(() => {
    void loadProcs(sort);
    const t = window.setInterval(() => { void loadProcs(sort); }, 5000);
    return () => window.clearInterval(t);
  }, [sort, loadProcs]);

  const loadPastDiags = useCallback(async () => {
    try {
      const runs = await api.runs(50);
      setPastDiags((Array.isArray(runs) ? runs : []).filter(r => r.intent === 'system.diagnose').slice(0, 6));
    } catch { setPastDiags(null); }
  }, []);
  useEffect(() => { void loadPastDiags(); }, [loadPastDiags]);

  const stopProcess = async (p: ProcessInfo) => {
    setStopping(p.pid);
    setChatOpen(true);
    try {
      // The PID makes the target unique (the router reads « (PID n) »); the name alone would aim at every homonym.
      await ask(p.name ? `Arrête le processus ${p.name} (PID ${p.pid})` : `Arrête le processus PID ${p.pid}`, { source: 'panel', options: { intent: 'system.kill_process' } });
    } finally {
      setStopping(null);
      void loadProcs(sort);
    }
  };

  const runDiagnostic = async () => {
    setDiagBusy(true); setDiagError('');
    // The diagnosis ends with an AI step: the viewer's engine preference applies to it.
    const run = await ask('Pourquoi mon PC ralentit ?', { source: 'panel', options: askOptions({ intent: 'system.diagnose' }) });
    setDiagBusy(false);
    if (run) setDiag(run); else setDiagError('Le diagnostic n’a pas pu être lancé (service local indisponible).');
    void loadPastDiags();
  };

  const pts = history ?? [];
  const times = pts.map(p => p.t);
  const diagnosis = useMemo(() => readDiagnosis(diag), [diag]);
  const gpu = system?.gpu ?? null;
  const temps = system?.temperatures && Object.keys(system.temperatures).length ? system.temperatures : null;

  return (
    <div className="cmp-page">
      <div className="page-title">
        <div>
          <div className="eyebrow">Système</div>
          <h1>Mon ordinateur</h1>
          <p>Mesures locales en temps réel (psutil) — rien n’est estimé ni inventé.</p>
        </div>
        <div className="cmp-title-actions">
          <button className="btn btn-primary" onClick={runDiagnostic} disabled={diagBusy || !online}>
            {diagBusy ? <Spinner /> : <Stethoscope size={16} />} Diagnostic
          </button>
        </div>
      </div>

      <div className="cmp-grid">
        <Panel className="cmp-gauges" title="Utilisation" subtitle={system ? 'Instantané rafraîchi toutes les 3 s' : undefined} icon={<Activity size={18} />}>
          {system ? (
            <div className="cmp-rings">
              <RingGauge value={system.cpu_percent} label="Processeur" color="cyan" size={112} stroke={8} />
              <RingGauge value={system.ram_percent} label="Mémoire" color="violet" size={112} stroke={8} />
              <RingGauge value={system.disk_percent} label="Stockage" color="green" size={112} stroke={8} />
              {gpu && <RingGauge value={gpu.util_percent} label={`GPU${gpu.util_percent === null ? ' (charge non exposée)' : ''}`} color="amber" size={112} stroke={8} />}
            </div>
          ) : <Unavailable what="Mesures système" reason={online ? 'La mesure psutil n’a pas répondu.' : 'Noyau local hors ligne.'} />}
        </Panel>

        <Panel className="cmp-info" title="Matériel et état" icon={<Cpu size={18} />}>
          {system ? (
            <dl className="cmp-facts">
              <div><dt><Cpu size={14} /> Cœurs logiques</dt><dd>{system.cpu_count ?? '—'}</dd></div>
              <div><dt><Activity size={14} /> Fréquence</dt><dd>{system.cpu_freq_mhz ? `${nf(system.cpu_freq_mhz / 1000, 2)} GHz` : 'non exposée'}</dd></div>
              <div><dt><MemoryStick size={14} /> Mémoire vive</dt><dd>{fmtGb(system.ram_used_gb)} / {fmtGb(system.ram_total_gb)}</dd></div>
              <div><dt><HardDrive size={14} /> Disque système</dt><dd>{fmtGb(system.disk_used_gb)} / {fmtGb(system.disk_total_gb)}</dd></div>
              <div><dt><ArrowDownUp size={14} /> Réseau ↓ / ↑</dt><dd>{fmtKbps(system.net_down_kbps)} / {fmtKbps(system.net_up_kbps)}</dd></div>
              <div><dt><Clock3 size={14} /> Temps de fonctionnement</dt><dd>{fmtUptime(system.uptime_s)}</dd></div>
              <div><dt><BatteryMedium size={14} /> Batterie</dt><dd>{system.battery_percent === null || system.battery_percent === undefined ? 'aucune batterie détectée' : `${nf(system.battery_percent, 0)} %`}</dd></div>
              <div className="cmp-wide"><dt><Thermometer size={14} /> Températures</dt>
                <dd>{temps ? Object.entries(temps).map(([k, v]) => <span key={k} className="cmp-temp">{k} : {nf(v, 0)} °C</span>) : <span className="muted">non exposées par Windows</span>}</dd>
              </div>
              {gpu && (
                <div className="cmp-wide"><dt>GPU</dt>
                  <dd>{gpu.name}{gpu.memory_total_mb ? ` · ${gpu.memory_used_mb == null ? '—' : nf(gpu.memory_used_mb / 1024, 1)} / ${nf(gpu.memory_total_mb / 1024, 1)} Go VRAM` : ''}</dd>
                </div>
              )}
            </dl>
          ) : <Unavailable what="Informations matérielles" />}
        </Panel>

        <Panel className="cmp-curves" title="Historique" subtitle="Courbes rafraîchies toutes les 3 s" icon={<Activity size={18} />}>
          {history === null ? <Unavailable what="Historique système" reason={historyError} /> : (
            <div className="cmp-curve-grid">
              <div><h3>Processeur <b>{pts.length ? `${nf(pts[pts.length - 1].cpu, 0)} %` : '—'}</b></h3>
                <HistoryChart title="Processeur" times={times} max={100} format={v => `${nf(v, 0)} %`} series={[{ label: 'CPU', color: '#22c8ff', values: pts.map(p => p.cpu) }]} /></div>
              <div><h3>Mémoire <b>{pts.length ? `${nf(pts[pts.length - 1].ram, 0)} %` : '—'}</b></h3>
                <HistoryChart title="Mémoire" times={times} max={100} format={v => `${nf(v, 0)} %`} series={[{ label: 'RAM', color: '#8a5cff', values: pts.map(p => p.ram) }]} /></div>
              <div className="cmp-wide"><h3>Réseau <b>↓ {pts.length ? fmtKbps(pts[pts.length - 1].net_down_kbps) : '—'} · ↑ {pts.length ? fmtKbps(pts[pts.length - 1].net_up_kbps) : '—'}</b></h3>
                <HistoryChart title="Réseau" times={times} format={fmtKbps} series={[
                  { label: 'Réception ↓', color: '#2ee6a8', values: pts.map(p => p.net_down_kbps) },
                  { label: 'Émission ↑', color: '#ffb547', values: pts.map(p => p.net_up_kbps) },
                ]} /></div>
            </div>
          )}
        </Panel>

        <Panel className="cmp-procs" title="Processus" subtitle="Les plus gourmands · actualisé toutes les 5 s" icon={<Cpu size={18} />}
          actions={
            <div className="cmp-seg" role="radiogroup" aria-label="Trier par">
              <button role="radio" aria-checked={sort === 'cpu'} className={sort === 'cpu' ? 'active' : ''} onClick={() => setSort('cpu')}>CPU</button>
              <button role="radio" aria-checked={sort === 'memory'} className={sort === 'memory' ? 'active' : ''} onClick={() => setSort('memory')}>Mémoire</button>
              <button className="icon-btn" aria-label="Actualiser" onClick={() => void loadProcs(sort)}><RefreshCw size={15} /></button>
            </div>
          }>
          {procs === null ? <Unavailable what="Processus" reason={procError} /> : procs.length === 0 ? <EmptyState>Aucun processus renvoyé.</EmptyState> : (
            <div className="cmp-table-wrap">
              <table className="cmp-table">
                <thead><tr><th>Nom</th><th className="num">PID</th><th className="num">CPU</th><th className="num">Mémoire</th><th>Utilisateur</th><th aria-label="Action" /></tr></thead>
                <tbody>
                  {procs.map(p => (
                    <tr key={p.pid}>
                      <td className="cmp-pname" title={p.name}>{p.name || '—'}</td>
                      <td className="num mono">{p.pid}</td>
                      <td className="num">{p.cpu_percent == null ? '—' : `${nf(p.cpu_percent, 1)} %`}</td>
                      <td className="num">{p.memory_mb == null ? '—' : p.memory_mb >= 1024 ? `${nf(p.memory_mb / 1024, 2)} Go` : `${nf(p.memory_mb, 0)} Mo`}</td>
                      <td className="cmp-user" title={p.username ?? ''}>{p.username ? p.username.split('\\').pop() : <span className="muted">—</span>}</td>
                      <td className="cmp-act">
                        <button className="btn btn-sm btn-danger" disabled={stopping !== null || !online} onClick={() => void stopProcess(p)}
                          title="Demande l’arrêt via AURORA : une autorisation vous sera demandée dans la conversation">
                          {stopping === p.pid ? <Spinner size={12} /> : <OctagonX size={13} />} Arrêter
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="cmp-footnote">« Arrêter » passe par le moteur de décision : AURORA vérifie le processus (système, critique, autre utilisateur…) et vous demande l’autorisation dans la conversation avant toute action.</p>
            </div>
          )}
        </Panel>

        <Panel className="cmp-diag" title="Diagnostic" subtitle="Constats calculés à partir des mesures, sans modification du PC" icon={<Stethoscope size={18} />}
          actions={<button className="btn btn-sm" onClick={runDiagnostic} disabled={diagBusy || !online}>{diagBusy ? <Spinner size={12} /> : <Stethoscope size={14} />} Lancer</button>}>
          {diagError && <Unavailable what="Diagnostic" reason={diagError} />}
          {diagBusy && <div className="cmp-diag-wait"><Spinner /> Analyse en cours…</div>}
          {diag && !diagBusy && (
            <div className="cmp-diag-result">
              <div className="cmp-diag-meta">
                <Badge tone={diag.status === 'done' ? 'green' : diag.status === 'error' ? 'red' : 'amber'}>{diag.status === 'done' ? 'Terminé' : diag.status === 'error' ? 'Erreur' : diag.status === 'waiting_permission' ? 'Autorisation requise' : diag.status}</Badge>
                <Badge tone="cyan">{ENGINE_LABELS[diag.engine?.engine ?? ''] ?? diag.engine?.engine ?? '—'}</Badge>
                <span className="muted">{fmtMsFr(diag.duration_ms)}</span>
                <button className="btn btn-sm btn-ghost" onClick={() => setChatOpen(true)}><MessageSquare size={13} /> Voir dans la conversation</button>
              </div>
              {diagnosis?.status && (
                <div className="cmp-diag-status">État : <Badge tone={sevTone(diagnosis.status === 'critique' ? 'critical' : diagnosis.status === 'attention' ? 'warning' : diagnosis.status)}>{diagnosis.status}</Badge></div>
              )}
              {diagnosis && diagnosis.findings.length > 0 && (
                <>
                  <h4 className="cmp-h4">Constats</h4>
                  <ol className="cmp-findings">
                    {diagnosis.findings.map((f, i) => (
                      <li key={i}>
                        <div><strong>{f.title}</strong>{f.tag && <Badge tone={sevTone(f.tag)}>{sevLabel(f.tag)}</Badge>}</div>
                        {f.detail && <p>{f.detail}</p>}
                      </li>
                    ))}
                  </ol>
                </>
              )}
              {diagnosis && diagnosis.causes.length > 0 && (
                <>
                  <h4 className="cmp-h4">Causes probables</h4>
                  <ul className="cmp-causes">
                    {diagnosis.causes.map((c, i) => <li key={i}><span>{c.title}</span>{c.tag && <Badge tone={sevTone(c.tag)}>confiance {c.tag}</Badge>}</li>)}
                  </ul>
                </>
              )}
              {diagnosis && diagnosis.suggestions.length > 0 && (
                <>
                  <h4 className="cmp-h4">Suggestions</h4>
                  <ul className="cmp-suggestions">
                    {diagnosis.suggestions.map((s, i) => (
                      <li key={i}>
                        <div>
                          <strong>{s.title}</strong>
                          {s.benefit && <p>Gain : {s.benefit}</p>}
                          {s.risk && <p>Risque : {s.risk}</p>}
                        </div>
                        {s.kill && s.kill.name && (
                          <button className="btn btn-sm btn-danger" disabled={stopping !== null || !online}
                            onClick={() => void stopProcess({ pid: s.kill?.pid ?? 0, name: s.kill?.name ?? '', cpu_percent: 0, memory_mb: 0, username: null })}>
                            <OctagonX size={13} /> Arrêter {s.kill.name}
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {(diag.answer || diag.error) && (diag.answer || diag.error) !== diagnosis?.summary && (
                <>
                  {diagnosis && <h4 className="cmp-h4">Réponse d’AURORA</h4>}
                  <p className="cmp-answer">{diag.answer || diag.error}</p>
                </>
              )}
              {diagnosis?.summary && !diag.answer && <p className="cmp-small">{diagnosis.summary}</p>}
            </div>
          )}
          {!diag && !diagBusy && !diagError && <EmptyState>Lancez un diagnostic pour obtenir les causes probables d’un ralentissement.</EmptyState>}

          <h3 className="cmp-sub">Derniers diagnostics</h3>
          {pastDiags === null ? <Unavailable what="Historique des diagnostics" /> : pastDiags.length === 0 ? <p className="muted cmp-small">Aucun diagnostic enregistré.</p> : (
            <ul className="cmp-past">
              {pastDiags.map(t => (
                <li key={t.id}>
                  <details>
                    <summary>
                      <span className={`cmp-st cmp-st-${t.status}`} aria-hidden="true" />
                      <span>{fmtDateTime(t.created_at)}</span>
                      <span className="muted">{relativeTime(t.created_at)} · {fmtMsFr(t.duration_ms)}</span>
                    </summary>
                    <p className="cmp-answer">{t.result_text || 'Aucun texte de résultat enregistré.'}</p>
                  </details>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
