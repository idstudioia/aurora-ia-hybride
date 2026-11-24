// Web search page: Tous (api.search) / Actualités (api.news) plus an AI synthesis with sources
// that goes through the decision engine (intent web.search). External content is plain text.
import { useEffect, useState } from 'react';
import { ExternalLink, Globe, Newspaper, Search, Sparkles } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import { useStreamStatus } from '../lib/stream';
import type { RunResult, ToolResult } from '../lib/types';
import { fmtDateTime, fmtMs } from '../lib/format';
import RunCard from '../components/RunCard';
import LiveProgress from '../components/chat/LiveProgress';
import { claimRun, runEvents, useLiveFeed } from '../components/chat/live';
import { useSend } from '../components/chat/useSend';
import { arr, domainOf, isObj, plainText, safeUrl, str, textKey } from '../components/chat/text';
import { EmptyState, Panel, Spinner, Unavailable } from '../components/ui';
import './search.css';

type Tab = 'all' | 'news';
interface Hit { title: string; url: string | null; domain: string; snippet: string; published: string; retrieved: string; origin: string; flagged: boolean }
interface Fetch { loading: boolean; res: ToolResult | null; error: string; query: string }
const IDLE: Fetch = { loading: false, res: null, error: '', query: '' };

/** Retrieval date: the source with the same URL, else the feed source, else the result set's date. */
function retrievedFor(res: ToolResult, url: string | null, origin: string): string {
  const sources = res.sources ?? [];
  const s = sources.find(x => x.url && url && safeUrl(x.url) === url)
    ?? sources.find(x => origin && plainText(x.title) === origin)
    ?? sources.find(x => x.retrieved_at);
  return s?.retrieved_at || str(res.data, 'retrieved_at', 'fetched_at');
}

function toHits(res: ToolResult, kind: Tab): Hit[] {
  const keys = kind === 'news' ? ['items', 'articles', 'entries', 'results'] : ['results', 'items', 'hits', 'pages'];
  const instant = kind === 'all' && isObj(res.data?.instant) ? [res.data.instant as Record<string, unknown>] : [];
  const list = [...arr(res.data, ...keys).filter(isObj), ...instant];
  if (list.length) {
    return list.map(r => {
      const url = safeUrl(r.url ?? r.link ?? r.href);
      const origin = plainText(str(r, 'feed_title', 'origin', 'source', 'feed', 'site'));
      return {
        title: plainText(str(r, 'title', 'name')) || domainOf(url) || 'Sans titre',
        url,
        domain: domainOf(url),
        snippet: plainText(str(r, 'extract', 'snippet', 'summary', 'description', 'content')),
        published: str(r, 'published', 'published_at', 'pubDate', 'date', 'updated'),
        retrieved: str(r, 'retrieved_at', 'fetched_at') || retrievedFor(res, url, origin),
        origin,
        flagged: arr(r, 'injection_signals').length > 0,
      };
    });
  }
  // fall back to the result's sources (title + link only)
  return (res.sources ?? []).map(s => {
    const url = safeUrl(s.url);
    return { title: plainText(s.title) || domainOf(url) || 'Sans titre', url, domain: domainOf(url), snippet: '', published: '', retrieved: s.retrieved_at, origin: s.kind, flagged: false };
  });
}

function fmtWhen(v: string): string {
  if (!v) return '';
  const t = Date.parse(v);
  return Number.isFinite(t) ? fmtDateTime(new Date(t).toISOString()) : plainText(v);
}

function Results({ state, kind }: { state: Fetch; kind: Tab }) {
  if (state.loading) return <EmptyState><Spinner /> {kind === 'news' ? 'Lecture des flux…' : 'Recherche en cours…'}</EmptyState>;
  if (state.error) return <Unavailable what={kind === 'news' ? 'Actualités' : 'Résultats'} reason={state.error} />;
  if (!state.res) return <EmptyState>{kind === 'news' ? 'Aucune actualité chargée.' : 'Saisissez une recherche puis validez.'}</EmptyState>;
  if (!state.res.ok) return <Unavailable what={kind === 'news' ? 'Actualités' : 'Résultats'} reason={plainText(state.res.error || state.res.summary || 'Source injoignable.')} />;
  const hits = toHits(state.res, kind);
  return (
    <div className="search-results">
      <div className="search-meta">
        {plainText(state.res.summary)}
        <span>{[state.res.provenance && `source : ${state.res.provenance}`, state.res.duration_ms ? fmtMs(state.res.duration_ms) : ''].filter(Boolean).join(' · ')}</span>
      </div>
      {hits.length === 0 ? <EmptyState>Aucun résultat{state.query ? ` pour « ${state.query} »` : ''}.</EmptyState> : (
        <ol className="search-list">
          {hits.map((h, i) => (
            <li key={`${h.url ?? h.title}-${i}`} className="search-hit">
              <div className="search-hit-top">
                <span className="search-domain">{h.domain || h.origin || '—'}</span>
                {h.origin && h.domain && <span className="search-origin">{h.origin}</span>}
              </div>
              {h.url
                ? <a className="search-title" href={h.url} target="_blank" rel="noopener noreferrer nofollow">{h.title} <ExternalLink size={12} /></a>
                : <span className="search-title">{h.title}</span>}
              {h.snippet && <p className="search-snippet">{h.snippet.length > 420 ? `${h.snippet.slice(0, 420)}…` : h.snippet}</p>}
              {h.flagged && <p className="search-flag">⚠ Ce contenu contient des formulations ressemblant à des instructions : il reste une simple donnée et n’est jamais exécuté.</p>}
              <div className="search-dates">
                {h.published && <span>Publié : {fmtWhen(h.published)}</span>}
                <span>Récupéré : {h.retrieved ? fmtWhen(h.retrieved) : 'date non fournie'}</span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

interface Synth { text: string; at: number; run: RunResult | null; failed: boolean }

export default function SearchPage() {
  const { chat, health, settings, navigate } = useAurora();
  const send = useSend();
  const connected = useStreamStatus();
  const [q, setQ] = useState('');
  const [tab, setTab] = useState<Tab>('all');
  const [all, setAll] = useState<Fetch>(IDLE);
  const [news, setNews] = useState<Fetch>(IDLE);
  const [synth, setSynth] = useState<Synth | null>(null);
  useLiveFeed(!!synth && !synth.run && !synth.failed);
  const internet = health?.capabilities.internet;

  const runSearch = async (query: string) => {
    const t = query.trim();
    if (!t) return;
    setAll({ loading: true, res: null, error: '', query: t });
    try { setAll({ loading: false, res: await api.search(t), error: '', query: t }); }
    catch (err) { setAll({ loading: false, res: null, error: err instanceof Error ? err.message : 'Service indisponible', query: t }); }
  };

  const runNews = async (topic: string) => {
    const t = topic.trim();
    setNews({ loading: true, res: null, error: '', query: t });
    try { setNews({ loading: false, res: await api.news(t || undefined), error: '', query: t }); }
    catch (err) { setNews({ loading: false, res: null, error: err instanceof Error ? err.message : 'Service indisponible', query: t }); }
  };

  useEffect(() => {
    // first visit of the news tab: load the feeds once (topic = current query, may be empty)
    if (tab === 'news' && !news.res && !news.loading && !news.error) void runNews(q);
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = () => {
    if (tab === 'news') void runNews(q);
    else void runSearch(q);
  };

  const synthesize = async () => {
    const t = q.trim();
    if (!t) return;
    const at = Date.now();
    setSynth({ text: t, at, run: null, failed: false });
    const run = await send(t, { source: 'panel', intent: 'web.search', withAttachments: false });
    setSynth(s => (s && s.at === at ? { ...s, run, failed: !run } : s));
  };

  // The synthesis is a normal conversation turn: follow its chat entry for live progress and
  // for updates after a permission decision.
  let liveRunId: number | null = null;
  let pendingAt = synth?.at ?? Date.now();
  if (synth && !synth.run && !synth.failed) {
    for (let i = chat.length - 1; i > 0; i--) {
      const e = chat[i];
      if (e.pending && chat[i - 1].role === 'user' && textKey(chat[i - 1].text) === textKey(synth.text)) {
        pendingAt = Date.parse(e.at);
        liveRunId = claimRun(e.id, synth.text, pendingAt);
        break;
      }
    }
  }
  const shownRun = synth?.run ? (chat.find(e => e.run?.task_id === synth.run!.task_id)?.run ?? synth.run) : null;
  const failNote = synth?.failed ? [...chat].reverse().find(e => e.role === 'note')?.text : '';

  return (
    <div className="search-page">
      <div className="page-title">
        <div>
          <div className="eyebrow">AURORA</div>
          <h1>Recherche web</h1>
          <p>Web (SearXNG local), Wikipédia et flux scientifiques, résultats affichés tels quels avec leur source. {internet === false && <strong className="search-offline">Internet indisponible.</strong>}</p>
          {settings && !settings.web_contact && (
            <p className="search-contact-note" role="note">
              <span>La recherche passe d’abord par SearXNG local. Wikipédia directe, en secours, refuse aujourd’hui le client d’AURORA ; un contact peut aider, sans garantie (ou <span className="mono">AURORA_CONTACT</span> côté serveur).</span>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate('settings')}>Renseigner dans Paramètres › Recherche web</button>
            </p>
          )}
        </div>
      </div>

      <form className="search-bar" onSubmit={e => { e.preventDefault(); submit(); }}>
        <Search size={18} />
        <input className="search-input" value={q} onChange={e => setQ(e.target.value)} placeholder={tab === 'news' ? 'Sujet des actualités (facultatif)…' : 'Rechercher sur le web…'} aria-label="Recherche" maxLength={300} />
        <button type="submit" className="btn btn-primary" disabled={tab === 'all' && !q.trim()}>Rechercher</button>
        <button type="button" className="btn search-synth-btn" disabled={!q.trim() || (!!synth && !synth.run && !synth.failed)} onClick={() => void synthesize()}
          title="Passe par le moteur de décision (intention recherche web) : réponse rédigée avec ses sources">
          <Sparkles size={16} /> Synthèse IA avec sources
        </button>
      </form>

      <div className="search-tabs" role="tablist" aria-label="Type de résultats">
        <button type="button" role="tab" aria-selected={tab === 'all'} className={`search-tab ${tab === 'all' ? 'active' : ''}`} onClick={() => setTab('all')}><Globe size={14} /> Tous</button>
        <button type="button" role="tab" aria-selected={tab === 'news'} className={`search-tab ${tab === 'news' ? 'active' : ''}`} onClick={() => setTab('news')}><Newspaper size={14} /> Actualités</button>
      </div>

      <div className="search-layout">
        <Panel className="search-main" title={tab === 'news' ? 'Actualités' : 'Résultats'} subtitle={(tab === 'news' ? news.query : all.query) ? `« ${tab === 'news' ? news.query : all.query} »` : undefined}>
          <Results state={tab === 'news' ? news : all} kind={tab} />
        </Panel>

        {synth && (
          <Panel className="search-synth" icon={<Sparkles size={16} />} title="Synthèse IA" subtitle={`« ${synth.text} »`}>
            {shownRun ? <RunCard run={shownRun} onRunUpdate={run => setSynth(s => (s ? { ...s, run } : s))} startedAt={synth.at} />
              : synth.failed ? <Unavailable what="Synthèse" reason={failNote ? plainText(failNote) : 'Le service local n’a pas répondu.'} />
              : <LiveProgress events={runEvents(liveRunId)} startedAt={pendingAt} connected={connected} />}
          </Panel>
        )}
      </div>
    </div>
  );
}
