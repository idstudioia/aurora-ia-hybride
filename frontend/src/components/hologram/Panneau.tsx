// Globe hologramme : le panneau, porté d'Athéna (HologrammeTerre.tsx, <aside className="globe-panneau">).
// Il dit ce qui est montré, d'où cela vient et quand cela a été mesuré. Une source absente, en échec ou
// coupée par le Mode Privé est dite telle quelle : rien ne la remplace.
import type { ReactNode, Ref } from 'react';
import { Crosshair, Plane, Satellite, X } from 'lucide-react';
import type {
  GlobeAirData, GlobeAircraftData, GlobeArticle, GlobeArticleMode, GlobeEvent, GlobeEventsData, GlobeGuideData, GlobeNewsData,
  GlobeSatellite, GlobeSatellitesData, GlobeTrafficData, GlobeWeatherData, GlobeWindData, Source,
} from '../../lib/types';
import { PAGER_LEVELS, safeHttpUrl } from '../globe/data';
import { formatLatLon } from '../globe/geo';
import { messageEtat, type Etat } from './donnees';
import {
  ECHELLES, MAX_AVIONS_AFFICHES, MODES_CARTE, THEMES_ACTU, capitaleProche, photoCommons, sansParentheses,
  type Capitale, type Choix, type ModeCarte, type PaysGuide, type PointActu, type SiteGuide, type ThemeChoisi,
} from './outils';
import { T, TYPES_EVENEMENTS, dateHeure, heure, ilYa, jour, nombre } from './textes';

/** Ce que les routes /api/globe/* ont répondu ; null : couche éteinte ou réponse pas encore arrivée. */
export interface DonneesPanneau {
  monde: Etat<GlobeTrafficData> | null;
  sats: Etat<GlobeSatellitesData> | null;
  vent: Etat<GlobeWindData> | null;
  air: Etat<GlobeAirData> | null;
  evenements: Etat<GlobeEventsData> | null;
  actu: Etat<GlobeNewsData> | null;
  avions: Etat<GlobeAircraftData> | null;
  meteo: Etat<GlobeWeatherData> | null;
  resumeSite: Etat<GlobeGuideData> | null;
  resumePays: Etat<GlobeGuideData> | null;
}

/** 11/11/2026 : l'article qu’ID Studio IA a demandé de lire ou d'expliquer (« Lis-le-moi », « Explique-le ») : en attente de
 *  la page, prêt (le texte, dit à voix haute tant que `parle`), ou refusé avec la phrase du service. */
export interface LectureArticle {
  lien: string; mode: GlobeArticleMode; etat: 'attente' | 'pret' | 'refus';
  texte?: string; partiel?: boolean; modele?: string; message?: string; parle?: boolean;
}

export interface ActionsPanneau {
  setChoix: (c: Choix | null) => void;
  choisirCarte: (m: ModeCarte) => void;
  choisirTheme: (t: ThemeChoisi) => void;
  setPaysActu: (p: string | null) => void;
  setSiteChoisi: (i: number | null) => void;
  voirMonde: () => void;
  basculerDirect: (on: boolean) => void;
  lireArticle?: (a: GlobeArticle, mode: GlobeArticleMode) => void;
  arreterArticle?: () => void;
}

interface Props {
  panneauRef: Ref<HTMLElement>;
  d: DonneesPanneau;
  /** prive : Mode Privé ; photos : images externes permises (mode connu et pas Privé). */
  etat: { prive: boolean; direct: boolean; photos: boolean; nasaDate: string | null; nasaSatellite?: string | null; message: string | null };
  couches: { meteo: boolean; volants: boolean; routes: boolean; guide: boolean; actu: boolean };
  choix: Choix | null;
  capitales: Capitale[];
  zone: { lat: number; lon: number } | null;
  focus: { lat: number; lon: number; nom?: string } | null;
  modeCarte: ModeCarte;
  actu: { theme: ThemeChoisi; articles: GlobeArticle[]; points: PointActu[]; pays: string | null; articlesPays: GlobeArticle[]; lecture?: LectureArticle | null };
  guide: { sites: SiteGuide[]; pays: PaysGuide[] } | null;
  lieuGuide: SiteGuide | null;
  paysGuide: PaysGuide | null;
  actions: ActionsPanneau;
}

const lienExterne = { target: '_blank', rel: 'noopener noreferrer' } as const;

/** Une couche : « Lecture des sources… », la raison de l'échec (ou le Mode Privé), ou ses données. */
function EtatVu<D>({ e, children }: { e: Etat<D> | null; children: (d: D) => ReactNode }) {
  if (!e) return <p className="holo-attenue">{T.chargement}</p>;
  if (!e.ok) return <p className="holo-attenue">{messageEtat(e)}</p>;
  return <>{children(e.data)}</>;
}

function Fermer({ onClick }: { onClick: () => void }) {
  return <button type="button" className="holo-fermer" onClick={onClick} title={T.fermer} aria-label={T.fermer}><X size={14} /></button>;
}

/** Valeur d'une ligne `infos` d'un événement, dite en français (dates, niveau d'alerte, statut, témoignages). */
function valeurInfo(cle: string, v: string): string {
  if (['debut', 'fin', 'expire'].includes(cle)) { const t = Date.parse(v); return Number.isFinite(t) ? dateHeure(t) : v; }
  if (cle === 'alerte') return PAGER_LEVELS[v.toLowerCase()] ?? v;
  if (cle === 'statut') return T.statuts[v] ?? v;
  if (cle === 'ressenti') { const n = Number(v); return Number.isFinite(n) ? T.temoignages(n) : v; }
  return v;
}

function DetailEvenement({ e, capitales, fermer }: { e: GlobeEvent; capitales: Capitale[]; fermer: () => void }) {
  const pc = capitaleProche(capitales, e);
  return (
    <>
      <div className="holo-choix-tete">
        <p className="holo-fort"><span className="holo-pastille holo-pastille-evenement" />{T.typeUn[e.type] ?? e.type}</p>
        <Fermer onClick={fermer} />
      </div>
      <p className="holo-moyen">{e.titre}</p>
      {Number.isFinite(e.date) && <p className="holo-source">{dateHeure(e.date)} · {ilYa(e.date)}</p>}
      {e.detail && <p>{e.detail}</p>}
      {!!e.infos?.length && (
        <dl className="holo-infos">
          {e.infos.map(x => <div key={x.cle}><dt>{T.infos[x.cle] ?? x.cle}</dt><dd>{valeurInfo(x.cle, x.valeur)}</dd></div>)}
        </dl>
      )}
      <p>{formatLatLon(e.lat, e.lon)}</p>
      {pc && <p className="holo-source">{T.capitaleProche(pc.nom, pc.pays, pc.km)}</p>}
      <p className="holo-source">{T.sourceDe(e.source)}</p>
    </>
  );
}

function DetailSatellite({ s, fermer }: { s: GlobeSatellite; fermer: () => void }) {
  return (
    <>
      <div className="holo-choix-tete">
        <p className="holo-fort"><Satellite size={14} aria-hidden="true" /> {s.nom}</p>
        <Fermer onClick={fermer} />
      </div>
      {s.role && <p className="holo-attenue">{s.role}</p>}
      <p>{T.altitude} : {nombre(s.altitude)} km · {formatLatLon(s.lat, s.lon, 1)}</p>
      <p className="holo-source">
        {T.calcule}{Number.isFinite(s.orbiteDu) ? ` · TLE ${jour(s.orbiteDu)}` : ''}{s.source && s.source !== 'CelesTrak' ? ` · ${s.source}` : ''}
      </p>
    </>
  );
}

/** « Lis-le-moi » et « Explique-le » sous un article (11/11/2026, d'après Athéna payante). Le texte est lu sur la page
 *  du journal et dit à voix haute ; l'explication, trois phrases du modèle local, est écrite ici et dite aussi, avec
 *  d'où elle vient. Un journal qui refuse, un article réservé aux abonnés : c'est écrit tel quel. Jamais sous une vidéo. */
function BoutonsArticle({ a, lecture, actions }: { a: GlobeArticle; lecture?: LectureArticle | null; actions: ActionsPanneau }) {
  if (!actions.lireArticle || a.video || !safeHttpUrl(a.lien)) return null;
  const l = lecture?.lien === a.lien ? lecture : null;
  const attend = l?.etat === 'attente';
  return (
    <div className="holo-article-voix">
      <div className="holo-article-boutons">
        <button type="button" className="holo-bouton holo-bouton-discret" disabled={attend} title={T.actu.aideLire}
          onClick={() => actions.lireArticle?.(a, 'lire')}>{T.actu.lisLeMoi}</button>
        <button type="button" className="holo-bouton holo-bouton-discret" disabled={attend} title={T.actu.aideExpliquer}
          onClick={() => actions.lireArticle?.(a, 'expliquer')}>{T.actu.expliqueLe}</button>
        {l?.parle && <button type="button" className="holo-bouton" onClick={() => actions.arreterArticle?.()}>{T.actu.arreter}</button>}
      </div>
      {attend && <p className="holo-source" role="status">{l.mode === 'lire' ? T.actu.lecturePage : T.actu.explicationEnCours}</p>}
      {l?.etat === 'refus' && <p className="holo-source" role="alert">{l.message}</p>}
      {l?.etat === 'pret' && l.partiel && <p className="holo-source">{T.actu.partiel}</p>}
      {l?.etat === 'pret' && l.mode === 'expliquer' && (
        <>
          <p className="holo-source">{T.actu.explication(l.modele ?? '')}</p>
          <p>{l.texte}</p>
        </>
      )}
    </div>
  );
}

function Actualites({ p }: { p: Props }) {
  const { actu, etat, actions } = p;
  return (
    <section className="holo-section" data-section="actu">
      <p className="holo-titre-section"><span className="holo-pastille holo-pastille-actu" />{T.actu.titre}</p>
      <EtatVu e={p.d.actu}>{data => {
        const avecImages = [...actu.articlesPays, ...actu.articles].some(a => a.image);
        const image = (a: GlobeArticle) => etat.photos ? safeHttpUrl(a.image) : null;
        return (
          <>
            <div className="holo-puces" role="radiogroup" aria-label={T.actu.theme}>
              {THEMES_ACTU.map(th => (
                <button key={th} type="button" role="radio" aria-checked={actu.theme === th} className={actu.theme === th ? 'actif' : ''} onClick={() => actions.choisirTheme(th)}>{T.actu.themes[th]}</button>
              ))}
            </div>
            {!etat.photos && avecImages && <p className="holo-source">{T.actu.imagesPrive}</p>}
            {actu.pays ? (
              <div className="holo-choix">
                <div className="holo-choix-tete">
                  <p className="holo-fort">{actu.pays} · {T.actu.articles(actu.articlesPays.length)}</p>
                  <Fermer onClick={() => actions.setPaysActu(null)} />
                </div>
                <ul className="holo-articles">
                  {actu.articlesPays.slice(0, 8).map((a, i) => {
                    const lien = safeHttpUrl(a.lien), img = image(a);
                    return (
                      <li key={`${a.lien}-${i}`} className="holo-article">
                        {img && <img src={img} alt="" loading="lazy" referrerPolicy="no-referrer" onError={e => { e.currentTarget.style.display = 'none'; }} />}
                        <p className="holo-moyen">{a.titre}</p>
                        <p className="holo-source">{a.source}{Number.isFinite(a.date) ? ` · ${ilYa(a.date)}` : ''}{a.credit ? ` · ${a.credit}` : ''}</p>
                        {i < 3 && a.resume && <p>{a.resume}</p>}
                        {lien && <a className="holo-bouton holo-bouton-discret" href={lien} {...lienExterne}>{a.video ? T.actu.video : T.actu.lire}</a>}
                        <BoutonsArticle a={a} lecture={actu.lecture} actions={actions} />
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : (
              <>
                <p className="holo-attenue">{T.actu.aide}</p>
                {!actu.points.length && <p className="holo-attenue">{T.actu.aucun}</p>}
                <ul className="holo-actu-pays">
                  {actu.points.slice(0, 12).map(pt => (
                    <li key={pt.pays}><button type="button" onClick={() => actions.setPaysActu(pt.pays)}>{pt.pays} <span className="holo-attenue">({pt.n})</span></button></li>
                  ))}
                </ul>
                {actu.theme !== 'tout' && !!actu.articles.length && (
                  <>
                    <p className="holo-moyen">{T.actu.derniers}</p>
                    <ul className="holo-articles">
                      {[...actu.articles].sort((a, b) => b.date - a.date).slice(0, 6).map((a, i) => {
                        const lien = safeHttpUrl(a.lien), img = image(a);
                        return (
                          <li key={`${a.lien}-${i}`} className="holo-actu-ligne">
                            {img && <img src={img} alt="" loading="lazy" referrerPolicy="no-referrer" onError={e => { e.currentTarget.style.display = 'none'; }} />}
                            <div>
                              {lien ? <a href={lien} {...lienExterne}>{a.titre}</a> : <span>{a.titre}</span>}
                              <p className="holo-source">{a.source}{Number.isFinite(a.date) ? ` · ${ilYa(a.date)}` : ''}{a.pays ? ` · ${a.pays}` : ''}</p>
                              <BoutonsArticle a={a} lecture={actu.lecture} actions={actions} />
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  </>
                )}
              </>
            )}
            <p className="holo-source">
              {T.actu.bilan(actu.articles.length, actu.points.length)}
              {data.sources.length ? ` · ${data.sources.map(x => x.ok ? x.source : `${x.source} (${T.sourceIndisponible})`).join(', ')}` : ''}
              {Number.isFinite(data.mesureA) ? ` · ${T.mesureA(heure(data.mesureA))}` : ''}
            </p>
          </>
        );
      }}</EtatVu>
    </section>
  );
}

function Guide({ p }: { p: Props }) {
  const { lieuGuide: l, paysGuide: pays, etat, actions, d } = p;
  const wikipedia = l?.wp ? `https://fr.wikipedia.org/wiki/${encodeURIComponent(l.wp)}` : null;
  const wikivoyage = l?.wv ? `https://fr.wikivoyage.org/wiki/${encodeURIComponent(l.wv)}` : null;
  return (
    <section className="holo-section" data-section="guide">
      <p className="holo-titre-section"><span className="holo-pastille holo-pastille-guide" />{T.guide.titre}</p>
      {l && (
        <div className="holo-choix">
          <div className="holo-choix-tete">
            <p className="holo-fort">{l.n}</p>
            <Fermer onClick={() => actions.setSiteChoisi(null)} />
          </div>
          {l.img && etat.photos && (
            <a href={`https://commons.wikimedia.org/wiki/File:${encodeURIComponent(l.img)}`} {...lienExterne} title={T.guide.photo}>
              <img className="holo-guide-photo" src={photoCommons(l.img, 640)} alt={l.n} loading="lazy" referrerPolicy="no-referrer" onError={e => { e.currentTarget.style.display = 'none'; }} />
            </a>
          )}
          {l.p && <p className="holo-attenue">{l.p}</p>}
          {l.d && <p>{l.d}</p>}
          {l.wp && <EtatVu e={d.resumeSite}>{r => <p>{r.texte}</p>}</EtatVu>}
          <p className="holo-source">
            {wikipedia && <a href={wikipedia} {...lienExterne}>{T.guide.lireWikipedia}</a>}
            {wikivoyage && <>{wikipedia ? ' · ' : ''}<a href={wikivoyage} {...lienExterne}>{T.guide.lireWikivoyage}</a></>}
            {l.img && etat.photos && <> · {T.guide.photo}</>}
          </p>
        </div>
      )}
      {pays && (
        <div className="holo-bloc">
          <p className="holo-moyen">{T.guide.pays} : {pays.n}</p>
          <dl className="holo-infos">
            {pays.cap && <div><dt>{T.guide.capitale}</dt><dd>{pays.cap}</dd></div>}
            {pays.pop > 0 && <div><dt>{T.guide.population}</dt><dd>{nombre(pays.pop)}</dd></div>}
            {!!pays.mon?.length && <div><dt>{T.guide.monnaie}</dt><dd>{pays.mon.join(' · ')}</dd></div>}
            {!!pays.lang?.length && <div><dt>{T.guide.langues}</dt><dd>{pays.lang.join(', ')}</dd></div>}
          </dl>
          {pays.wv && <EtatVu e={d.resumePays}>{r => {
            const lien = safeHttpUrl(r.lien);
            return <p>{r.texte} {lien && <a className="holo-source" href={lien} {...lienExterne}>{T.guide.lireWikivoyage}</a>}</p>;
          }}</EtatVu>}
        </div>
      )}
      {!l && <p className="holo-attenue">{T.guide.aide}</p>}
      {!etat.photos && <p className="holo-source">{T.guide.sansPhotos}</p>}
      {p.guide && <p className="holo-source">{T.guide.lieux(p.guide.sites.length)}</p>}
      <p className="holo-source">{T.guide.source}</p>
    </section>
  );
}

function Meteo({ p }: { p: Props }) {
  const { d, modeCarte, actions } = p;
  const ech = modeCarte === 'aucune' ? null : ECHELLES[modeCarte];
  const grille = modeCarte === 'air' ? d.air : d.vent;
  const manque = grille?.ok && modeCarte !== 'air' && modeCarte !== 'aucune' && grille.data && !(grille.data as GlobeWindData)[modeCarte];
  return (
    <section className="holo-section" data-section="meteo">
      <p className="holo-titre-section"><span className="holo-pastille holo-pastille-meteo" />{T.ventPluie}</p>
      <EtatVu e={d.vent}>{v => <p className="holo-source">Open-Meteo · {T.mesureA(heure(v.mesureA))} · {T.ventAnime}</p>}</EtatVu>
      <div className="holo-puces" role="radiogroup" aria-label={T.carte}>
        {MODES_CARTE.map(m => (
          <button key={m} type="button" role="radio" aria-checked={modeCarte === m} className={modeCarte === m ? 'actif' : ''} onClick={() => actions.choisirCarte(m)}>{T.cartes[m]}</button>
        ))}
      </div>
      {ech && (
        <div className="holo-bloc">
          <div className="holo-legende" style={{ background: `linear-gradient(90deg, ${ech.arrets.map(([, c]) => c).join(', ')})` }} />
          <div className="holo-legende-bornes holo-source">
            <span>{ech.arrets[0][0]}{ech.unite && ` ${ech.unite}`}</span>
            <span>{ech.arrets[ech.arrets.length - 1][0]}{ech.unite && ` ${ech.unite}`}{modeCarte === 'air' ? '+' : ''}</span>
          </div>
          <p className="holo-source">
            {modeCarte === 'air' ? T.sourceAir : 'Open-Meteo'}{grille?.ok ? ` · ${T.mesureA(heure(grille.data.mesureA))}` : ''} · {T.survoler}
          </p>
          {modeCarte === 'air' && d.air && !d.air.ok && <p className="holo-attenue">{messageEtat(d.air)}</p>}
          {manque && <p className="holo-attenue">{T.cartes[modeCarte]} : {T.sourceIndisponible}</p>}
        </div>
      )}
      <p className="holo-titre-section"><span className="holo-pastille holo-pastille-evenement" />{T.catastrophes}</p>
      <EtatVu e={d.evenements}>{ev => (
        <ul className="holo-liste">
          {TYPES_EVENEMENTS.map(ty => {
            const n = ev.evenements.filter(e => e.type === ty).length;
            const src = ev.sources.find(x => typeof x?.nom === 'string' && x.nom.includes(T.motsSources[ty]));
            return (
              <li key={ty}>
                {T.types[ty]} : {src && !src.ok ? <span className="holo-attenue">{T.sourceIndisponible}</span> : n}
                {src && <span className="holo-source"> ({src.nom})</span>}
              </li>
            );
          })}
        </ul>
      )}</EtatVu>
    </section>
  );
}

function Zone({ p }: { p: Props }) {
  const { zone, d, couches, actions } = p;
  if (!zone) return <section className="holo-section" data-section="zone"><p className="holo-attenue">{T.cliquerZone}</p></section>;
  const proche = capitaleProche(p.capitales, zone);
  return (
    <section className="holo-section" data-section="zone">
      <p className="holo-moyen">{formatLatLon(zone.lat, zone.lon, 1)}</p>
      {proche && <p className="holo-source">{T.capitaleProche(proche.nom, proche.pays, proche.km)}</p>}
      {couches.volants && <EtatVu e={d.avions}>{a => {
        const affiches = Math.min(a.liste.length, MAX_AVIONS_AFFICHES);
        return (
          <>
            <p>{a.total ? T.zoneAvions(a.total) : T.aucunAvion}</p>
            {a.total > affiches && <p className="holo-source">{T.affiches(affiches)}</p>}
            <p className="holo-source">{a.source}{Number.isFinite(a.mesureA) ? ` · ${T.mesureA(heure(a.mesureA))}` : ''}</p>
            <p className="holo-source">{T.anime}</p>
          </>
        );
      }}</EtatVu>}
      {d.meteo?.ok && (
        <p>{T.meteoPoint} : {nombre(d.meteo.data.temperature, 1)} °C{d.meteo.data.ciel ? ` · ${d.meteo.data.ciel}` : ''}{Number.isFinite(d.meteo.data.vent) ? ` · ${T.vent} ${nombre(d.meteo.data.vent)} km/h` : ''} <span className="holo-source">(Open-Meteo)</span></p>
      )}
      {d.meteo && !d.meteo.ok && <p className="holo-attenue">{T.meteoPoint} : {messageEtat(d.meteo)}</p>}
      <button type="button" onClick={actions.voirMonde} className="holo-bouton holo-bouton-discret">{T.voirMonde}</button>
    </section>
  );
}

/** Les sources réellement consultées par les réponses affichées, sans doublon. */
function sourcesDe(d: DonneesPanneau): Source[] {
  const vues = new Map<string, Source>();
  for (const e of Object.values(d) as (Etat<unknown> | null)[]) {
    if (!e?.ok) continue;
    for (const s of e.sources) if (s && typeof s.url === 'string' && !vues.has(s.url)) vues.set(s.url, s);
  }
  return [...vues.values()];
}

export default function Panneau(p: Props) {
  const { d, etat, couches, choix, actions } = p;
  const fermerChoix = () => actions.setChoix(null);
  const sources = sourcesDe(d);
  return (
    <aside ref={p.panneauRef} className="holo-panneau" aria-label={T.titre}>
      <h2 className="holo-panneau-titre">{T.titre}</h2>
      {choix && (
        <div className="holo-choix">
          {choix.genre === 'evenement' ? <DetailEvenement e={choix.e} capitales={p.capitales} fermer={fermerChoix} />
            : choix.genre === 'sat' ? <DetailSatellite s={choix.s} fermer={fermerChoix} />
            : (
              <>
                <div className="holo-choix-tete">
                  <p className="holo-fort"><Plane size={14} aria-hidden="true" /> {choix.a.indicatif || choix.a.id}{choix.a.type && ` · ${choix.a.type}`}</p>
                  <Fermer onClick={fermerChoix} />
                </div>
                {choix.a.pays && <p className="holo-attenue">{choix.a.pays}</p>}
                <p>
                  {T.altitude} : {choix.a.altitude != null ? `${nombre(choix.a.altitude)} m` : '—'} · {T.vitesse} : {choix.a.vitesse != null ? `${nombre(choix.a.vitesse)} km/h` : '—'} · {T.cap} : {choix.a.cap != null ? `${nombre(choix.a.cap)}°` : '—'}
                </p>
                <p className="holo-source">{d.avions?.ok ? `${d.avions.data.source} · ` : ''}{T.mesureA(heure(choix.a.mesure))}</p>
              </>
            )}
        </div>
      )}
      {p.focus && (
        <p className="holo-focus">
          <Crosshair size={14} aria-hidden="true" /> {T.capSur(p.focus.nom || formatLatLon(p.focus.lat, p.focus.lon, 1))} <span className="holo-source">· {T.demandeAurora}</span>
        </p>
      )}
      {etat.message && <p className="holo-message" role="status">{etat.message}</p>}
      {couches.actu && etat.direct && !etat.prive && <Actualites p={p} />}
      {couches.guide && <Guide p={p} />}
      {couches.routes && (
        <section className="holo-section" data-section="routes">
          <p className="holo-titre-section">{T.routesTitre}</p>
          <ul className="holo-legende-routes">
            {(['autoroute', 'nationale', 'route', 'ferry'] as const).map(k => <li key={k}><span className={`holo-trait holo-trait-${k}`} />{T.routesLegende[k]}</li>)}
          </ul>
          <p className="holo-source">{T.routesSource}</p>
        </section>
      )}
      {etat.prive ? (
        <section className="holo-section" data-section="prive">
          <p className="holo-moyen">{T.prive}</p>
          <p className="holo-source">{T.priveTerre}</p>
          <p className="holo-source">{T.imageLocale}</p>
        </section>
      ) : !etat.direct ? (
        <section className="holo-section" data-section="direct">
          <p className="holo-attenue">{T.directOff}</p>
          <p className="holo-source">{T.detailOff}</p>
          <button type="button" onClick={() => actions.basculerDirect(true)} className="holo-bouton">{T.activer}</button>
          <p className="holo-source">{T.imageLocale}</p>
        </section>
      ) : (
        <>
          {couches.meteo && <Meteo p={p} />}
          {couches.volants && (
            <section className="holo-section" data-section="volants">
              <EtatVu e={d.monde}>{m => (
                <>
                  <p><span className="holo-pastille holo-pastille-avion" />{T.monde(m.avions.length, m.total)}</p>
                  <p className="holo-source">{m.source} · {T.mesureA(heure(m.mesureA))} · {T.mondeAnime}</p>
                </>
              )}</EtatVu>
            </section>
          )}
          <Zone p={p} />
          {couches.volants && (
            <section className="holo-section" data-section="satellites">
              <p className="holo-titre-section"><span className="holo-pastille holo-pastille-sat" />{T.satellites}</p>
              <EtatVu e={d.sats}>{s => (
                <ul className="holo-liste">
                  {s.liste.map(x => (
                    <li key={x.norad}>
                      <button type="button" className="holo-lien-bouton" onClick={() => actions.setChoix({ genre: 'sat', s: x })}>
                        {sansParentheses(x.nom)} <span className="holo-attenue">· {nombre(x.altitude)} km</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}</EtatVu>
              {/* Athéna, 06/10 : quand CelesTrak ne répond pas, la source de secours est nommée, pas cachée. */}
              <p className="holo-source">{T.calcule}{d.sats?.ok && d.sats.data.liste.some(x => x.source && x.source !== 'CelesTrak') ? ` · ${T.secoursTle}` : ''}</p>
            </section>
          )}
          <p className="holo-source">{etat.nasaDate ? T.nasa(etat.nasaDate, etat.nasaSatellite) : T.imageLocale}</p>
          <p className="holo-source">{T.detail}</p>
          <button type="button" onClick={() => actions.basculerDirect(false)} className="holo-bouton holo-bouton-discret">{T.desactiver}</button>
        </>
      )}
      {!!sources.length && (
        <details className="holo-details">
          <summary>{T.sourcesConsultees} ({sources.length})</summary>
          <ul className="holo-liste">
            {sources.map(s => {
              const lien = safeHttpUrl(s.url), lu = Date.parse(s.retrieved_at);
              return (
                <li key={s.url}>
                  {lien ? <a href={lien} {...lienExterne}>{s.title || lien}</a> : <span>{s.title || s.url}</span>}
                  {Number.isFinite(lu) && <span className="holo-source"> · {heure(lu)}</span>}
                </li>
              );
            })}
          </ul>
        </details>
      )}
      <details className="holo-details" open>
        <summary>{T.sources}</summary>
        <dl className="holo-attributions">
          {T.attributions.map(([titre, texte]) => <div key={titre}><dt>{titre}</dt><dd>{texte}</dd></div>)}
        </dl>
      </details>
      <p className="holo-source">{T.aide}</p>
    </aside>
  );
}
