// Simulations: (a) solar system from the server's Keplerian elements, propagated client-side;
// (b) orbit simulator (SciPy solve_ivp on the server) rendered in 3D with its own numerical checks.
import './simulations.css';
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Atom, Bot, CircleCheck, Orbit, Rocket, Sparkles, Sun, TriangleAlert } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import { useSend } from '../components/chat/useSend';
import { fmtMsFr } from '../lib/numfr';
import type { ToolResult } from '../lib/types';
import { Badge, EmptyState, Panel, Spinner, Unavailable } from '../components/ui';
import SolarSystem from '../components/SolarSystem';
import OrbitView from '../components/OrbitView';
import { parseSolar } from '../components/sim/solarData';
import { pickStr } from '../lib/tolerant';
import { AU_KM } from '../components/sim/kepler';
import { ORBIT_BODIES, expFr, fmtMetric, orbitBodyFr, parseOrbit, type Metric, type OrbitBodyId } from '../components/sim/orbitData';

type Tab = 'solar' | 'orbit';

function SourceList({ result }: { result: ToolResult }) {
  if (!result.sources?.length) return null;
  return (
    <ul className="sim-sources">
      {result.sources.map((s, i) => (
        <li key={i}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.title || s.url}</a> : s.title}</li>
      ))}
    </ul>
  );
}

function MetricCard({ m, hint }: { m: Metric | null; hint?: string }) {
  if (!m) return null;
  return (
    <div className="sim-metric" title={`Champ ${m.path} = ${m.value.toLocaleString('fr-FR', { maximumSignificantDigits: 10 })}${m.unit === 's' ? ' s' : m.unit === 'km' ? ' km' : m.unit === 'kms' ? ' km/s' : ''}`}>
      <span>{m.label}</span>
      <strong>{fmtMetric(m)}</strong>
      {hint && <small>{hint}</small>}
    </div>
  );
}

// ---------- solar system ----------
function SolarPanel() {
  const [result, setResult] = useState<ToolResult | null>(null);
  const [requestedMs, setRequestedMs] = useState(() => Date.now());
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const hasView = useRef(false);
  hasView.current = !!result?.ok;

  const load = useCallback(async (iso?: string) => {
    setLoading(true); setError('');
    const ms = iso ? Date.parse(iso) : Date.now();
    try {
      const r = await api.solar(iso);
      // a refused date (outside the elements' validity) keeps the current view and shows why
      if (!r.ok && hasView.current) setError(r.error || r.summary || 'Date refusée par le calcul serveur.');
      else { setRequestedMs(ms); setResult(r); }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Service indisponible');
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const model = useMemo(() => (result?.ok ? parseSolar(result.data, requestedMs) : null), [result, requestedMs]);
  const methodText = result?.ok ? pickStr(result.data, ['method']) : null;
  const validRange = useMemo<[number, number] | null>(() => {
    const m = /(\d{4})\s*[–-]\s*(\d{4})/.exec(methodText ?? '');
    return m ? [Number(m[1]), Number(m[2])] : null;
  }, [methodText]);
  const check = model?.check ?? null;
  const errKm = check ? check.maxErrAu * AU_KM : null;

  return (
    <Panel className="sim-panel" title="Système solaire — simulation 3D" icon={<Sun size={18} />}
      subtitle="Éléments orbitaux calculés par le noyau local, positions propagées dans le navigateur (équation de Kepler)"
      actions={loading ? <Spinner /> : null}>
      {error && !result && <Unavailable what="Données du système solaire" reason={error} />}
      {result && !result.ok && <Unavailable what="Données du système solaire" reason={result.error ?? result.summary} />}
      {result?.ok && !model && <Unavailable what="Éléments orbitaux" reason="La réponse ne contient pas d’éléments képlériens exploitables." />}
      {error && result && <div className="sim-inline-error" role="alert"><TriangleAlert size={14} /> {error}</div>}
      {model && result && (
        <>
          <div className="sim-badges">
            <Badge tone="cyan">Provenance : {result.provenance || '—'}</Badge>
            <Badge>{model.bodies.length} planètes</Badge>
            {check ? (
              <Badge tone={check.maxErrAu < 1e-4 ? 'green' : check.maxErrAu < 1e-2 ? 'amber' : 'red'}
                title={`Lecture des éléments retenue : ${check.interpretation}. ${check.compared} position(s) serveur comparée(s) à la date de référence.`}>
                {check.maxErrAu < 1e-4 ? <CircleCheck size={12} /> : <TriangleAlert size={12} />}
                Écart client/serveur max : {errKm !== null && errKm < 1 ? 'moins de 1 km' : errKm !== null && errKm < 1e6 ? `${Math.round(errKm).toLocaleString('fr-FR')} km` : `${expFr(check.maxErrAu, 2)} UA`}
              </Badge>
            ) : <Badge title="Le serveur n’a pas renvoyé de positions à comparer">Positions serveur non fournies : pas de comparaison</Badge>}
            {!model.refDeclared && <Badge tone="amber" title="La réponse ne précise pas sa date : la date demandée est utilisée">Date de référence non précisée</Badge>}
            <span className="muted sim-dur">calcul serveur {fmtMsFr(result.duration_ms)}</span>
          </div>
          {model.incomplete.length > 0 && <p className="sim-note">Éléments incomplets, non affichés : {model.incomplete.join(', ')}.</p>}
          <SolarSystem model={model} loading={loading} validYears={validRange} onNow={() => void load()} onJumpDate={iso => void load(iso)} />
          {result.summary && <p className="sim-summary">{result.summary}</p>}
          <dl className="sim-method">
            {methodText && <div><dt>Méthode serveur</dt><dd>{methodText}</dd></div>}
            {pickStr(result.data, ['propagation']) && <div><dt>Propagation</dt><dd>{pickStr(result.data, ['propagation'])} — reproduite à l’identique dans le navigateur.</dd></div>}
            {pickStr(result.data, ['frame']) && <div><dt>Repère</dt><dd>{pickStr(result.data, ['frame'])}</dd></div>}
            {pickStr(result.data, ['accuracy_note']) && <div><dt>Précision</dt><dd>{pickStr(result.data, ['accuracy_note'])}</dd></div>}
          </dl>
          <SourceList result={result} />
        </>
      )}
      {!result && !error && <EmptyState><Spinner /> Calcul des positions…</EmptyState>}
    </Panel>
  );
}

// ---------- orbit simulator ----------
function OrbitPanel() {
  const { ask, setChatOpen, online } = useAurora();
  const send = useSend();
  const [body, setBody] = useState<OrbitBodyId>('mars');
  const [altitude, setAltitude] = useState('400');
  const [ecc, setEcc] = useState('0');
  const [hours, setHours] = useState('6');
  const [result, setResult] = useState<ToolResult | null>(null);
  const [resultBody, setResultBody] = useState<string>('mars');
  const [origin, setOrigin] = useState<'form' | 'aurora'>('form');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [askBusy, setAskBusy] = useState(false);

  const alt = Number(altitude), e = Number(ecc), hrs = Number(hours);
  const invalid = !Number.isFinite(alt) || alt <= 0 || alt > 5_000_000 ? 'Altitude : nombre positif en km.'
    : !Number.isFinite(e) || e < 0 || e >= 0.95 ? 'Excentricité : entre 0 (circulaire) et 0,94.'
      : !Number.isFinite(hrs) || hrs <= 0 || hrs > 2000 ? 'Durée : entre 0,1 et 2000 heures.' : '';

  const simulate = async (ev?: FormEvent) => {
    ev?.preventDefault();
    if (invalid) return;
    setLoading(true); setError('');
    try {
      const r = await api.orbit({ body, altitude_km: alt, eccentricity: e, hours: hrs });
      setResult(r); setResultBody(body); setOrigin('form');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Service indisponible');
    } finally { setLoading(false); }
  };

  // plain digits (no thousands separator) so the engine reads the altitude slot as typed
  const askText = `Simule la trajectoire d'une sonde autour de ${body === 'moon' ? 'la Lune' : body === 'earth' ? 'la Terre' : orbitBodyFr(body)} à ${Number.isFinite(alt) ? String(alt) : altitude} km`;
  const askAurora = async () => {
    setAskBusy(true); setError('');
    const run = await ask(askText,
      { source: 'panel', options: { intent: 'sim.orbit' } });
    setAskBusy(false);
    if (!run) { setError('AURORA n’a pas pu traiter la demande (service local indisponible).'); return; }
    if (run.ui?.widget === 'orbit' && run.ui.data) {
      // provenance as recorded by the tool step, when the run exposes it
      const step = run.steps?.find(s => s.tool === 'sim.orbit');
      let provenance = '';
      try { provenance = step?.result_json ? String(JSON.parse(step.result_json).provenance ?? '') : ''; } catch { provenance = ''; }
      setResult({ ok: true, data: run.ui.data, summary: run.answer, provenance, sources: run.sources ?? [], duration_ms: step?.duration_ms ?? run.duration_ms, error: null });
      setResultBody(body); setOrigin('aurora');
    } else {
      setChatOpen(true);
      setError(run.status === 'done' ? 'La réponse d’AURORA ne contient pas de trajectoire : voir la conversation.' : run.error || 'La simulation demandée à AURORA n’a pas abouti.');
    }
  };

  const model = useMemo(() => (result?.ok ? parseOrbit(result.data, resultBody) : null), [result, resultBody]);

  const periodCheck = useMemo(() => {
    const a = model?.periodAnalytic, n = model?.periodNumeric;
    if (!a || !n || a.unit !== 's' || n.unit !== 's' || a.value <= 0) return null;
    return Math.abs(n.value - a.value) / a.value;
  }, [model]);
  const drift = model?.energyDrift?.value ?? null;
  // e.g. "SciPy solve_ivp · DOP853": provenance + integration method, both as returned by the tool
  const integratorLabel = result?.provenance === 'scipy'
    ? `SciPy solve_ivp${model?.integrator ? ` · ${model.integrator}` : ''}`
    : [result?.provenance, model?.integrator].filter(Boolean).join(' · ') || '—';

  const interpret = () => {
    if (!model || !result) return;
    const parts = [
      model.periodAnalytic && `${model.periodAnalytic.label} ${fmtMetric(model.periodAnalytic)}`,
      model.periodNumeric && `${model.periodNumeric.label} ${fmtMetric(model.periodNumeric)}`,
      ...model.speeds.map(s => `${s.label} ${fmtMetric(s)}`),
      model.periapsis && `${model.periapsis.label} ${fmtMetric(model.periapsis)}`,
      model.apoapsis && `${model.apoapsis.label} ${fmtMetric(model.apoapsis)}`,
      model.energyDrift && `${model.energyDrift.label} ${fmtMetric(model.energyDrift)}`,
    ].filter(Boolean).join(' ; ');
    setChatOpen(true);
    // Through useSend: the viewer's engine preference applies (a « Local » choice never goes to Claude).
    void send(`Interprète ce résultat de simulation orbitale autour de ${model.bodyName} (intégrateur ${integratorLabel}) : ${result.summary || ''} ${parts ? `Valeurs : ${parts}.` : ''} Explique ce que signifient ces valeurs pour une sonde, sans les recalculer.`.replace(/\s+/g, ' ').trim(),
      { source: 'panel', intent: 'chat.general', withAttachments: false });
  };

  return (
    <Panel className="sim-panel" title="Simulateur d’orbite" icon={<Orbit size={18} />}
      subtitle="Intégration numérique côté serveur ; la vue 3D trace exactement la trajectoire renvoyée">
      <div className="sim-orbit">
        <form className="sim-form" onSubmit={simulate}>
          <label className="field">Corps central
            <select className="select" value={body} onChange={ev => setBody(ev.target.value as OrbitBodyId)}>
              {ORBIT_BODIES.map(b => <option key={b.id} value={b.id}>{b.fr}</option>)}
            </select>
          </label>
          <label className="field">Altitude initiale (km)
            <input className="input" type="number" inputMode="decimal" min={1} step="any" value={altitude} onChange={ev => setAltitude(ev.target.value)} />
          </label>
          <label className="field">Excentricité
            <input className="input" type="number" inputMode="decimal" min={0} max={0.94} step={0.01} value={ecc} onChange={ev => setEcc(ev.target.value)} />
          </label>
          <label className="field">Durée simulée (h)
            <input className="input" type="number" inputMode="decimal" min={0.1} step="any" value={hours} onChange={ev => setHours(ev.target.value)} />
          </label>
          {invalid && <p className="sim-invalid" role="alert">{invalid}</p>}
          <button className="btn btn-primary" type="submit" disabled={!!invalid || loading || !online}>
            {loading ? <Spinner size={14} /> : <Rocket size={15} />} Simuler
          </button>
          <button className="btn" type="button" onClick={askAurora} disabled={!!invalid || askBusy || !online}
            title="Envoie la demande en langage naturel au moteur de décision d’AURORA">
            {askBusy ? <Spinner size={14} /> : <Bot size={15} />} Demander à AURORA
          </button>
          <p className="sim-note">« Demander à AURORA » envoie au moteur unique : « {askText} ».</p>
        </form>

        <div className="sim-orbit-main">
          {error && <Unavailable what="Simulation orbitale" reason={error} />}
          {result && !result.ok && <Unavailable what="Simulation orbitale" reason={result.error ?? result.summary} />}
          {result?.ok && !model && <Unavailable what="Résultat de simulation" reason="La réponse ne contient ni trajectoire ni métrique exploitable." />}
          {!result && !error && !loading && <EmptyState><Atom size={22} /><br />Choisissez un corps et une altitude, puis lancez la simulation.</EmptyState>}
          {loading && !result && <EmptyState><Spinner /> Intégration en cours…</EmptyState>}
          {model && result && (
            <>
              {model.points.length >= 2
                ? <OrbitView points={model.points} radiusKm={model.radiusKm} bodyKey={model.bodyKey}
                  label={`Trajectoire autour de ${model.bodyName} : ${model.points.length} points intégrés`} />
                : <Unavailable what="Trajectoire" reason="Le serveur n’a pas renvoyé de points de trajectoire ; seules les métriques sont affichées." />}
              <div className="sim-prov">
                <Badge tone="cyan">Provenance : {integratorLabel}</Badge>
                <Badge>{origin === 'aurora' ? 'Via le moteur AURORA' : 'Appel direct de l’outil'}</Badge>
                {!model.radiusFromServer && <Badge title="Rayon non fourni par le serveur : rayon moyen UAI utilisé pour le dessin">Rayon moyen de référence</Badge>}
                <span className="muted">{fmtMsFr(result.duration_ms)}</span>
              </div>
            </>
          )}
        </div>

        {model && result && (
          <aside className="sim-metrics">
            <MetricCard m={model.periodAnalytic} />
            <MetricCard m={model.periodNumeric} hint={periodCheck !== null ? `écart relatif avec Kepler : ${periodCheck < 1e-4 ? expFr(periodCheck, 1) : `${(periodCheck * 100).toLocaleString('fr-FR', { maximumSignificantDigits: 3 })} %`}` : undefined} />
            {model.speeds.length > 0 && (
              <div className="sim-metric">
                <span>Vitesses</span>
                <dl className="sim-speed-list">
                  {model.speeds.map(sp => <div key={sp.path} title={`Champ ${sp.path}`}><dt>{sp.label.replace(/^Vitesse /, '')}</dt><dd>{fmtMetric(sp)}</dd></div>)}
                </dl>
              </div>
            )}
            <MetricCard m={model.periapsis} hint={model.periapsisNumeric ? `trajectoire intégrée : ${fmtMetric(model.periapsisNumeric)}` : undefined} />
            <MetricCard m={model.apoapsis} hint={model.apoapsisNumeric ? `trajectoire intégrée : ${fmtMetric(model.apoapsisNumeric)}` : undefined} />
            {model.impacted && <div className="sim-metric sim-warn"><span>Trajectoire</span><strong>Collision avec la surface</strong><small>L’intégration s’arrête à l’impact.</small></div>}
            {model.checks.length > 0 && (
              <ul className="sim-checks" aria-label="Vérifications numériques">
                {model.checks.map(c => <li key={c.name} className={c.ok ? 'ok' : 'ko'}>{c.ok ? <CircleCheck size={13} /> : <TriangleAlert size={13} />}<span><b>{c.name}</b>{c.detail && ` — ${c.detail}`}</span></li>)}
              </ul>
            )}
            {model.energyDrift && (
              <div className={`sim-metric ${drift !== null && Math.abs(drift) < 1e-6 ? 'sim-ok' : 'sim-warn'}`} title={`Champ : ${model.energyDrift.path}`}>
                <span>{model.energyDrift.label}</span>
                <strong>{fmtMetric(model.energyDrift)}</strong>
                <small>{drift !== null && Math.abs(drift) < 1e-6 ? 'Énergie conservée : intégration numériquement fiable' : 'Dérive notable : résultat à interpréter avec prudence'}</small>
              </div>
            )}
            {model.others.length > 0 && (
              <details className="sim-others">
                <summary>Autres valeurs renvoyées ({model.others.length})</summary>
                <dl>{model.others.map(o => <div key={o.path}><dt>{o.label}</dt><dd>{fmtMetric(o)}</dd></div>)}</dl>
              </details>
            )}
            <button className="btn" onClick={interpret} disabled={!online}><Sparkles size={15} /> Interpréter avec l’IA</button>
            {result.summary && <p className="sim-summary">{result.summary}</p>}
            <SourceList result={result} />
          </aside>
        )}
      </div>
    </Panel>
  );
}

export default function SimulationsPage() {
  const [tab, setTab] = useState<Tab>('solar');
  return (
    <div className="sim-page">
      <div className="page-title">
        <div>
          <div className="eyebrow">Simulations 3D</div>
          <h1>Simulations</h1>
          <p>Calculs physiques réels : mécanique képlérienne et intégration numérique. Les vues 3D ne dessinent que ce qui a été calculé.</p>
        </div>
        <div className="sim-tabs" role="tablist" aria-label="Simulation">
          <button role="tab" aria-selected={tab === 'solar'} className={tab === 'solar' ? 'active' : ''} onClick={() => setTab('solar')}><Sun size={15} /> Système solaire</button>
          <button role="tab" aria-selected={tab === 'orbit'} className={tab === 'orbit' ? 'active' : ''} onClick={() => setTab('orbit')}><Orbit size={15} /> Orbite d’une sonde</button>
        </div>
      </div>
      {/* both stay mounted so results survive a tab switch */}
      <div role="tabpanel" hidden={tab !== 'solar'}><SolarPanel /></div>
      <div role="tabpanel" hidden={tab !== 'orbit'}><OrbitPanel /></div>
    </div>
  );
}
