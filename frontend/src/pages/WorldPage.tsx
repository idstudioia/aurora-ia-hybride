// Monde 3D : globe interactif (glisser, molette, clavier) et calques réels — météo des villes
// (Open-Meteo), séismes (USGS), jour/nuit (position du Soleil calculée à l'heure courante).
// Les lumières des villes sont un décor. Une source en échec → « indisponible », jamais de valeur inventée.
// Depuis le 8/11/2026, ce globe est la vue « Classique » ; la vue par défaut est le globe hologramme
// (components/hologram), au choix en haut de la page.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Activity, CloudSun, Crosshair, Droplets, Earth, ExternalLink, Layers, LocateFixed, Moon, Orbit, Pause, Play, Radio, RefreshCw,
  Sparkles, Sun, Thermometer, Wind, X, ZoomIn, ZoomOut,
} from 'lucide-react';
import Globe, { type GlobeHandle, type GlobeMarker } from '../components/Globe';
import HologramGlobe from '../components/hologram/HologramGlobe';
import { ecrireChoix, ecrireOui, lireChoix, lireOui, trouverLieu, webglDisponible } from '../components/hologram/outils';
import { T as TH } from '../components/hologram/textes';
import { Panel, Spinner, StatusDot, Unavailable } from '../components/ui';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import { fmtDateTime, fmtTime, relativeTime } from '../lib/format';
import type { GlobeCommand } from '../lib/types';
import { useNow, useToolResult, type ToolState } from '../components/globe/hooks';
import { PAGER_LEVELS, firstSource, readCityWeather, readQuakes, retrievedAt, type QuakeFeed, type Quake, type WeatherNow } from '../components/globe/data';
import { CITY_LIGHTS } from '../components/globe/cities';
import { clamp, formatLatLon, isNight, subsolarPoint } from '../components/globe/geo';
import { magnitudeColor, temperatureColor } from '../components/globe/sky';
import SkyIcon from '../components/globe/SkyIcon';
import './world.css';

type ViewId = 'planet' | 'weather' | 'quakes';
type ListTab = 'quakes' | 'cities';
interface LayerState { weather: boolean; quakes: boolean; night: boolean; lights: boolean }

const VIEWS: { id: ViewId; label: string; layers: LayerState; list: ListTab }[] = [
  { id: 'planet', label: 'Planète', layers: { weather: true, quakes: true, night: true, lights: true }, list: 'quakes' },
  { id: 'weather', label: 'Météo mondiale', layers: { weather: true, quakes: false, night: true, lights: false }, list: 'cities' },
  { id: 'quakes', label: 'Séismes', layers: { weather: false, quakes: true, night: false, lights: false }, list: 'quakes' },
];

const fmtTemp = (t: number | null) => (t === null ? '—' : `${Math.round(t)}°C`);
const fmtMag = (m: number | null) => (m === null ? '?' : m.toFixed(1).replace('.', ','));
const fmtNum = (v: number | null, unit: string, digits = 0) => (v === null ? '—' : `${v.toFixed(digits).replace('.', ',')} ${unit}`);

/** Sticky flag: becomes true once `want` was true, so toggling a layer does not refetch. */
function useSticky(want: boolean): boolean {
  const [on, setOn] = useState(want);
  useEffect(() => { if (want) setOn(true); }, [want]);
  return on || want;
}

function sourceLine(name: string, state: ToolState, enabled: boolean, online: boolean, observedAt: string | null = null) {
  if (!enabled) return `${name} : non chargé (calque désactivé).`;
  if (state.loading && !state.result) return `${name} : récupération en cours…`;
  if (state.error) return `${name} : indisponible — ${state.error}`;
  if (!state.result) return `${name} : indisponible${online ? '' : ' (noyau local hors ligne)'}.`;
  const src = firstSource(state.result);
  const at = retrievedAt(state.result) ?? state.fetchedAt;
  return `${src?.title || name}${observedAt ? ` · relevés de ${fmtTime(observedAt).slice(0, 5)}` : ''}${at ? ` · récupéré à ${fmtTime(at)}` : ''}`;
}

/** « M ≥ 2,5 · 24 h » from the feed parameters, else the source's own title. */
function feedScope(feed: QuakeFeed): string {
  const parts = [
    feed.minMag !== null ? `M ≥ ${feed.minMag.toFixed(1).replace('.', ',')}` : '',
    feed.windowHours !== null ? `${feed.windowHours} h` : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : feed.title;
}

/** Most recent observation instant among the cities (null if none is known). */
function latestObservation(cities: WeatherNow[]): string | null {
  let best: string | null = null;
  for (const c of cities) if (c.observedAt && (!best || c.observedAt > best)) best = c.observedAt;
  return best;
}

function LayerToggle({ on, onToggle, icon, label, status }: { on: boolean; onToggle: () => void; icon: ReactNode; label: string; status?: ReactNode }) {
  return (
    <button type="button" className={`world-layer ${on ? 'world-layer-on' : ''}`} aria-pressed={on} onClick={onToggle}>
      <span className="world-layer-icon">{icon}</span>
      <span className="world-layer-label">{label}</span>
      <span className="world-layer-status">{status}</span>
      <span className="world-switch" aria-hidden="true"><span /></span>
    </button>
  );
}

function layerStatus(state: ToolState, count: number, enabled: boolean): ReactNode {
  if (!enabled) return null;
  if (state.loading && !state.result) return <Spinner size={12} />;
  if (state.error || !state.result) return <span className="world-err" title={state.error || 'Aucune donnée reçue'}>indisponible</span>;
  return <span className="muted">{count}</span>;
}

function QuakeDetail({ q }: { q: Quake }) {
  return (
    <div className="world-detail">
      <div className="world-detail-head">
        <span className="world-mag" style={{ color: magnitudeColor(q.mag), borderColor: magnitudeColor(q.mag) }}>M {fmtMag(q.mag)}</span>
        <div>
          <strong>{q.place || 'Lieu non précisé'}</strong>
          <small>{q.time ? `${fmtDateTime(q.time)} · ${relativeTime(q.time)}` : 'Heure inconnue'}</small>
        </div>
      </div>
      <dl className="world-facts">
        <dt>Profondeur</dt><dd>{fmtNum(q.depthKm, 'km', 1)}</dd>
        <dt>Coordonnées</dt><dd>{formatLatLon(q.lat, q.lon)}</dd>
        {q.magType && <><dt>Type de magnitude</dt><dd>{q.magType}</dd></>}
        {q.felt !== null && <><dt>Signalements « ressenti »</dt><dd>{q.felt}</dd></>}
        {q.alert && <><dt>Alerte PAGER (USGS)</dt><dd>{PAGER_LEVELS[q.alert] ?? q.alert}</dd></>}
        {q.tsunami && <><dt>Tsunami</dt><dd>indicateur USGS levé</dd></>}
      </dl>
      {q.url && <a className="world-link" href={q.url} target="_blank" rel="noopener noreferrer">Fiche USGS <ExternalLink size={12} aria-hidden="true" /></a>}
    </div>
  );
}

function CityDetail({ c, night }: { c: WeatherNow; night: boolean }) {
  return (
    <div className="world-detail">
      <div className="world-detail-head">
        <SkyIcon kind={c.kind} night={night} size={36} />
        <div>
          <strong>{[c.city, c.country].filter(Boolean).join(', ') || 'Ville'}</strong>
          <small>{c.label || '—'}</small>
        </div>
        <span className="world-temp" style={{ color: temperatureColor(c.temperature) }}>{fmtTemp(c.temperature)}</span>
      </div>
      <dl className="world-facts">
        <dt><Wind size={12} aria-hidden="true" /> Vent</dt><dd>{fmtNum(c.windKmh, 'km/h')}</dd>
        <dt><Droplets size={12} aria-hidden="true" /> Humidité</dt><dd>{fmtNum(c.humidity, '%')}</dd>
        {c.apparent !== null && <><dt><Thermometer size={12} aria-hidden="true" /> Ressenti</dt><dd>{fmtTemp(c.apparent)}</dd></>}
        {c.lat !== null && c.lon !== null && <><dt>Coordonnées</dt><dd>{formatLatLon(c.lat, c.lon)}</dd></>}
        {c.observedAt && <><dt>Relevé</dt><dd>{fmtDateTime(c.observedAt)}</dd></>}
      </dl>
    </div>
  );
}

/** L'id du dernier ordre d'AURORA appliqué par le globe classique (gardé hors du composant, comme l'hologramme). */
let derniereCommandeClassique: number | null = null;

/** Le globe classique (SVG), gardé intact ; `switcher` : le choix Hologramme | Classique, posé dans son en-tête.
 *  `commande` (8/11/2026) : les ordres d'AURORA, suivis comme un clic dans la liste (couche, puis focusOn). */
function ClassicWorld({ switcher, commande }: { switcher: ReactNode; commande: GlobeCommand | null }) {
  const { online } = useAurora();
  const globeRef = useRef<GlobeHandle>(null);
  const coordRef = useRef<HTMLSpanElement>(null);
  const [view, setView] = useState<ViewId>('planet');
  const [layers, setLayers] = useState<LayerState>(VIEWS[0].layers);
  const [listTab, setListTab] = useState<ListTab>('quakes');
  const [quakeSort, setQuakeSort] = useState<'time' | 'mag'>('time');
  const [citySort, setCitySort] = useState<'name' | 'temp'>('name');
  const [autoRotate, setAutoRotate] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const now = useNow(60_000);
  const sun = useMemo(() => subsolarPoint(now), [now]);

  const weatherOn = useSticky(layers.weather || listTab === 'cities');
  const quakesOn = useSticky(layers.quakes || listTab === 'quakes');
  const weather = useToolResult(() => api.weatherCities(), 'cities', { enabled: online && weatherOn, refreshMs: 15 * 60_000 });
  const quakes = useToolResult(() => api.quakes(), 'quakes', { enabled: online && quakesOn, refreshMs: 5 * 60_000 });

  const cities = useMemo(() => readCityWeather(weather.result), [weather.result]);
  const feed = useMemo(() => readQuakes(quakes.result), [quakes.result]);
  const cityNight = useCallback((c: WeatherNow) => (c.isDay !== null ? !c.isDay : c.lat !== null && c.lon !== null && isNight({ lat: c.lat, lon: c.lon }, sun)), [sun]);

  const markers = useMemo(() => {
    const list: GlobeMarker[] = [];
    if (layers.weather && !weather.error) {
      for (const c of cities) {
        if (c.lat === null || c.lon === null) continue;
        list.push({
          id: `w:${c.id}`, lat: c.lat, lon: c.lon, color: temperatureColor(c.temperature), size: 3,
          label: c.temperature === null ? '—' : `${Math.round(c.temperature)}°`, glyph: c.kind, night: cityNight(c),
          tooltip: `${c.city || 'Ville'} · ${fmtTemp(c.temperature)}${c.label ? ` · ${c.label}` : ''}`,
        });
      }
    }
    if (layers.quakes && !quakes.error) {
      for (const q of feed.quakes) {
        list.push({
          id: `q:${q.id}`, lat: q.lat, lon: q.lon, color: magnitudeColor(q.mag), pulse: true,
          size: q.mag === null ? 2.5 : clamp(1.6 + q.mag * 1.25, 2, 11),
          label: q.mag !== null && q.mag >= 5 ? `M${fmtMag(q.mag)}` : undefined,
          tooltip: `M ${fmtMag(q.mag)} · ${q.place || 'lieu non précisé'}`,
        });
      }
    }
    return list;
  }, [layers.weather, layers.quakes, cities, feed, weather.error, quakes.error, cityNight]);

  const selectedQuake = selected?.startsWith('q:') ? feed.quakes.find(q => `q:${q.id}` === selected) ?? null : null;
  const selectedCity = selected?.startsWith('w:') ? cities.find(c => `w:${c.id}` === selected) ?? null : null;

  const sortedQuakes = useMemo(() => {
    const list = feed.quakes.slice();
    if (quakeSort === 'mag') list.sort((a, b) => (b.mag ?? -9) - (a.mag ?? -9));
    else list.sort((a, b) => (b.time ? Date.parse(b.time) : 0) - (a.time ? Date.parse(a.time) : 0));
    return list;
  }, [feed, quakeSort]);
  const sortedCities = useMemo(() => {
    const list = cities.slice();
    if (citySort === 'temp') list.sort((a, b) => (b.temperature ?? -999) - (a.temperature ?? -999));
    else list.sort((a, b) => a.city.localeCompare(b.city, 'fr'));
    return list;
  }, [cities, citySort]);
  const maxMag = feed.quakes.reduce<number | null>((m, q) => (q.mag !== null && (m === null || q.mag > m) ? q.mag : m), null);

  const applyView = (v: (typeof VIEWS)[number]) => { setView(v.id); setLayers(v.layers); setListTab(v.list); };
  const toggle = (k: keyof LayerState) => setLayers(l => ({ ...l, [k]: !l[k] }));

  // A selection stops the auto-rotation so the selected marker stays in view.
  const focusQuake = (q: Quake) => {
    setLayers(l => (l.quakes ? l : { ...l, quakes: true }));
    setSelected(`q:${q.id}`);
    setAutoRotate(false);
    globeRef.current?.focusOn(q.lat, q.lon);
  };
  const focusCity = (c: WeatherNow) => {
    if (c.lat === null || c.lon === null) return;
    setLayers(l => (l.weather ? l : { ...l, weather: true }));
    setSelected(`w:${c.id}`);
    setAutoRotate(false);
    globeRef.current?.focusOn(c.lat, c.lon);
  };

  // AURORA's orders (store globeCommand), once each: the layer it has (weather, quakes), then the place. The classic
  // globe has no aircraft, satellite or news layer: for those it only turns to the place.
  useEffect(() => {
    if (!commande || commande.id === derniereCommandeClassique) return;
    derniereCommandeClassique = commande.id;
    const c = commande;
    if (c.couche === 'meteo') { setLayers(l => ({ ...l, weather: true })); setListTab('cities'); }
    if (c.couche === 'evenements') { setLayers(l => ({ ...l, quakes: true })); setListTab('quakes'); }
    const aller = (lat: number, lon: number) => { setAutoRotate(false); globeRef.current?.focusOn(lat, lon); };
    if (c.focus && Number.isFinite(c.focus.lat) && Number.isFinite(c.focus.lon)) aller(c.focus.lat, c.focus.lon);
    else if (c.pays || c.lieu) void trouverLieu((c.pays || c.lieu)!).then(f => { if (f) aller(f.lat, f.lon); });
  }, [commande]);

  // Cursor coordinates are written straight into the DOM: no re-render on every pointer move.
  const onHover = useCallback((lat: number | null, lon: number | null) => {
    if (coordRef.current) coordRef.current.textContent = lat === null || lon === null ? 'hors du globe' : formatLatLon(lat, lon);
  }, []);
  const onMarkerClick = useCallback((m: GlobeMarker) => { setSelected(m.id); setAutoRotate(false); }, []);

  const reload = () => { if (weatherOn) weather.reload(); if (quakesOn) quakes.reload(); };
  const failing = [layers.weather && weather.error ? 'Météo' : '', layers.quakes && quakes.error ? 'Séismes' : ''].filter(Boolean);

  return (
    <div className="world">
      <header className="page-title world-title">
        <div>
          <div className="eyebrow">AURORA · Monde 3D</div>
          <h1>Explorer la planète</h1>
          <p>Données réelles : Open-Meteo, USGS ; jour et nuit calculés à l'heure de votre ordinateur.</p>
        </div>
        {switcher}
        <div className="world-tabs" role="tablist" aria-label="Vues du globe">
          {VIEWS.map(v => (
            <button key={v.id} type="button" role="tab" aria-selected={view === v.id} className={`world-tab ${view === v.id ? 'world-tab-on' : ''}`} onClick={() => applyView(v)}>
              {v.id === 'planet' ? <Sparkles size={15} /> : v.id === 'weather' ? <CloudSun size={15} /> : <Activity size={15} />}{v.label}
            </button>
          ))}
        </div>
      </header>

      <aside className="world-left" aria-label="Calques et sélection">
        <Panel title="Calques" icon={<Layers size={18} />} className="world-panel">
          <div className="world-layers">
            <LayerToggle on={layers.weather} onToggle={() => toggle('weather')} icon={<CloudSun size={16} />} label="Météo mondiale" status={layerStatus(weather, cities.length, weatherOn)} />
            <LayerToggle on={layers.quakes} onToggle={() => toggle('quakes')} icon={<Activity size={16} />} label="Séismes" status={layerStatus(quakes, feed.quakes.length, quakesOn)} />
            <LayerToggle on={layers.night} onToggle={() => toggle('night')} icon={<Moon size={16} />} label="Jour / Nuit" />
            <LayerToggle on={layers.lights} onToggle={() => toggle('lights')} icon={<Sun size={16} />} label="Lumières des villes" status={<span className="muted">décor</span>} />
          </div>
        </Panel>

        <Panel title="Position" icon={<Crosshair size={18} />} className="world-panel">
          <dl className="world-facts">
            <dt>Sous le curseur</dt><dd><span ref={coordRef} className="mono">hors du globe</span></dd>
            <dt>Soleil au zénith</dt><dd className="mono">{formatLatLon(sun.lat, sun.lon, 1)}</dd>
            <dt>Calculé pour</dt><dd>{now.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })} (UTC {now.toISOString().slice(11, 16)})</dd>
          </dl>
        </Panel>

        <Panel title="Sélection" icon={<LocateFixed size={18} />} className="world-panel"
          actions={selected ? <button type="button" className="icon-btn" onClick={() => setSelected(null)} aria-label="Effacer la sélection"><X size={16} /></button> : undefined}>
          {selectedQuake ? <QuakeDetail q={selectedQuake} />
            : selectedCity ? <CityDetail c={selectedCity} night={cityNight(selectedCity)} />
              : <p className="world-hint">Cliquez sur un marqueur du globe ou sur une ligne des listes pour afficher son détail.</p>}
        </Panel>
      </aside>

      <section className="world-stage" aria-label="Globe interactif">
        <Globe ref={globeRef} detail="50m" interactive autoRotate={autoRotate} rotateSpeed={3} minZoom={0.8} maxZoom={8}
          initialCenter={{ lat: 25, lon: 10 }} showNight={layers.night} cityLights={layers.lights ? CITY_LIGHTS : null}
          markers={markers} selectedId={selected} onMarkerClick={onMarkerClick} onHover={onHover}
          ariaLabel={`Globe terrestre. ${markers.length} marqueur(s) affiché(s).`} />
        <div className="world-toolbar" role="toolbar" aria-label="Contrôles du globe">
          <button type="button" className="icon-btn" onClick={() => globeRef.current?.zoomBy(1.3)} aria-label="Zoomer" title="Zoomer"><ZoomIn size={17} /></button>
          <button type="button" className="icon-btn" onClick={() => globeRef.current?.zoomBy(1 / 1.3)} aria-label="Dézoomer" title="Dézoomer"><ZoomOut size={17} /></button>
          <button type="button" className="icon-btn" onClick={() => globeRef.current?.resetView()} aria-label="Recentrer" title="Recentrer"><LocateFixed size={17} /></button>
          <button type="button" className="icon-btn" onClick={() => setAutoRotate(a => !a)} aria-pressed={autoRotate}
            aria-label={autoRotate ? 'Arrêter la rotation automatique' : 'Activer la rotation automatique'} title={autoRotate ? 'Arrêter la rotation' : 'Rotation automatique'}>
            {autoRotate ? <Pause size={17} /> : <Play size={17} />}
          </button>
          <button type="button" className="icon-btn" onClick={reload} aria-label="Actualiser les données" title="Actualiser les données" disabled={!online}>
            {weather.loading || quakes.loading ? <Spinner size={14} /> : <RefreshCw size={17} />}
          </button>
        </div>
        {(failing.length > 0 || !online) && (
          <div className="world-banner" role="status">
            <StatusDot ok={false} /> {!online ? 'Noyau local hors ligne : données indisponibles' : `${failing.join(' et ')} indisponible${failing.length > 1 ? 's' : ''}`}
          </div>
        )}
        {layers.quakes && !quakes.error && feed.quakes.length > 0 && (
          <div className="world-legend" aria-label="Légende des magnitudes">
            {[['< 3', 2], ['3 – 4,5', 3.5], ['4,5 – 6', 5], ['≥ 6', 6.5]].map(([label, m]) => (
              <span key={label as string}><i style={{ background: magnitudeColor(m as number) }} />M {label}</span>
            ))}
          </div>
        )}
      </section>

      <aside className="world-right" aria-label="Listes et sources">
        <Panel className="world-panel world-list-panel" title={listTab === 'quakes' ? 'Séismes récents' : 'Villes météo'}
          subtitle={listTab === 'quakes'
            ? (!quakes.result?.ok ? undefined : `${feed.quakes.length} événement(s)${maxMag !== null ? ` · max M ${fmtMag(maxMag)}` : ''}${feedScope(feed) ? ` · ${feedScope(feed)}` : ''}`)
            : (!weather.result?.ok ? undefined : `${cities.length} ville(s)`)}
          icon={listTab === 'quakes' ? <Activity size={18} /> : <CloudSun size={18} />}>
          <div className="world-list-tabs" role="tablist" aria-label="Liste affichée">
            <button type="button" role="tab" aria-selected={listTab === 'quakes'} className={`world-chip ${listTab === 'quakes' ? 'world-chip-on' : ''}`} onClick={() => setListTab('quakes')}>Séismes</button>
            <button type="button" role="tab" aria-selected={listTab === 'cities'} className={`world-chip ${listTab === 'cities' ? 'world-chip-on' : ''}`} onClick={() => setListTab('cities')}>Villes</button>
            <span className="world-sort">
              {listTab === 'quakes' ? (
                <select className="select world-select" value={quakeSort} onChange={e => setQuakeSort(e.target.value as 'time' | 'mag')} aria-label="Trier les séismes">
                  <option value="time">Plus récents</option><option value="mag">Magnitude</option>
                </select>
              ) : (
                <select className="select world-select" value={citySort} onChange={e => setCitySort(e.target.value as 'name' | 'temp')} aria-label="Trier les villes">
                  <option value="name">Nom</option><option value="temp">Température</option>
                </select>
              )}
            </span>
          </div>

          {listTab === 'quakes' ? (
            !online && !quakes.result ? <Unavailable what="Séismes" reason="Noyau local hors ligne." />
              : quakes.loading && !quakes.result ? <div className="world-loading"><Spinner /> Récupération des séismes (USGS)…</div>
                : quakes.error ? <Unavailable what="Séismes" reason={quakes.error} />
                  : sortedQuakes.length === 0 ? <p className="world-hint">Aucun séisme dans le flux USGS pour la période.</p>
                    : (
                      <ul className="world-list">
                        {sortedQuakes.slice(0, 120).map(q => (
                          <li key={q.id}>
                            <button type="button" className={`world-row ${selected === `q:${q.id}` ? 'world-row-on' : ''}`} onClick={() => focusQuake(q)}>
                              <span className="world-mag world-mag-sm" style={{ color: magnitudeColor(q.mag), borderColor: magnitudeColor(q.mag) }}>{fmtMag(q.mag)}</span>
                              <span className="world-row-copy"><strong>{q.place || 'Lieu non précisé'}</strong>
                                <small>{q.time ? `${relativeTime(q.time)} · ${fmtTime(q.time).slice(0, 5)}` : 'heure inconnue'}{q.depthKm !== null ? ` · ${Math.round(q.depthKm)} km` : ''}</small></span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )
          ) : (
            !online && !weather.result ? <Unavailable what="Données météo" reason="Noyau local hors ligne." />
              : weather.loading && !weather.result ? <div className="world-loading"><Spinner /> Récupération de la météo (Open-Meteo)…</div>
                : weather.error ? <Unavailable what="Données météo" reason={weather.error} />
                  : sortedCities.length === 0 ? <p className="world-hint">La source n'a renvoyé aucune ville exploitable.</p>
                    : (
                      <ul className="world-list">
                        {sortedCities.map(c => (
                          <li key={c.id}>
                            <button type="button" className={`world-row ${selected === `w:${c.id}` ? 'world-row-on' : ''}`} onClick={() => focusCity(c)}>
                              <SkyIcon kind={c.kind} night={cityNight(c)} size={20} />
                              <span className="world-row-copy"><strong>{c.city || 'Ville'}</strong><small>{c.label || '—'}</small></span>
                              <span className="world-row-temp" style={{ color: temperatureColor(c.temperature) }}>{fmtTemp(c.temperature)}</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )
          )}
        </Panel>

        <Panel title="Sources" icon={<ExternalLink size={16} />} className="world-panel">
          <ul className="world-sources">
            <li><strong>Météo</strong> — {sourceLine('Open-Meteo', weather, weatherOn, online, latestObservation(cities))}</li>
            <li><strong>Séismes</strong> — {sourceLine('USGS Earthquake Hazards Program', quakes, quakesOn, online)}</li>
            <li><strong>Fond de carte</strong> — Natural Earth (domaine public), via world-atlas, embarqué localement.</li>
            <li><strong>Jour / Nuit</strong> — position du Soleil calculée localement (éphéméride simplifiée), mise à jour chaque minute.</li>
            <li><strong>Lumières des villes</strong> — décor indicatif (grandes agglomérations), pas une mesure.</li>
          </ul>
        </Panel>
      </aside>
    </div>
  );
}

type Vue = 'hologramme' | 'classique';
const VUES: Vue[] = ['hologramme', 'classique'];
const CLE_VUE = 'aurora.world.vue';

/**
 * Monde 3D (8/11/2026) : le globe hologramme (porté d'Athéna) par défaut, ou le globe classique, au choix en haut
 * de la page (retenu sous aurora.world.vue). Sans WebGL, la page passe seule au classique et le dit, sans changer le
 * choix enregistré. Le globe hologramme suit les commandes d'AURORA (globeCommand) et montre ses recherches (searching).
 */
export default function WorldPage() {
  const { globeCommand, searching, settings } = useAurora();
  const [vue, setVue] = useState<Vue>(() => lireChoix(CLE_VUE, VUES, 'hologramme'));
  // 24/11/2026 (ID Studio IA : « il faut ajouter une icône pour activer […] au niveau du texte ») : le direct se règle
  // aussi dans l'en-tête, à côté de la phrase qui en parle. Même préférence que l'icône du globe (aurora.globe.direct).
  const [direct, setDirect] = useState(() => lireOui('aurora.globe.direct'));
  const prive = settings?.mode === 'prive';
  const basculerDirect = () => { const on = !direct; setDirect(on); ecrireOui('aurora.globe.direct', on); };
  const [webgl] = useState(webglDisponible);
  const affichee: Vue = vue === 'hologramme' && webgl ? 'hologramme' : 'classique';
  const choisir = (v: Vue) => { setVue(v); ecrireChoix(CLE_VUE, v); };
  const switcher = (
    <div className="world-vue-bloc">
      <div className="world-vue" role="radiogroup" aria-label={TH.vues.titre}>
        <button type="button" role="radio" aria-checked={affichee === 'hologramme'} disabled={!webgl}
          className={`world-tab ${affichee === 'hologramme' ? 'world-tab-on' : ''}`} onClick={() => choisir('hologramme')}>
          <Orbit size={15} />{TH.vues.hologramme}
        </button>
        <button type="button" role="radio" aria-checked={affichee === 'classique'}
          className={`world-tab ${affichee === 'classique' ? 'world-tab-on' : ''}`} onClick={() => choisir('classique')}>
          <Earth size={15} />{TH.vues.classique}
        </button>
      </div>
      {vue === 'hologramme' && !webgl && <p className="world-vue-note" role="status">{TH.basculeClassique}</p>}
    </div>
  );
  if (affichee === 'classique') return <ClassicWorld switcher={switcher} commande={globeCommand} />;
  return (
    <div className="world-holo">
      <header className="page-title world-title">
        <div>
          <div className="eyebrow">AURORA · Monde 3D</div>
          <h1>Explorer la planète</h1>
          <p>Données mesurées par leurs sources (NASA, adsb.lol, OpenSky, Open-Meteo, USGS, médias) ; le direct est éteint tant que vous ne l'activez pas.</p>
          <button type="button" className={`world-tab world-direct ${direct && !prive ? 'world-tab-on' : ''}`} aria-pressed={direct && !prive}
            disabled={prive} title={prive ? TH.directPrive : direct ? TH.desactiver : TH.activer} onClick={basculerDirect}>
            <Radio size={15} />{prive ? TH.directPrive : direct ? TH.directAllume : TH.directAllumer}
          </button>
        </div>
        {switcher}
      </header>
      <HologramGlobe commande={globeCommand} recherche={searching} direct={direct} onDirect={setDirect} />
    </div>
  );
}
