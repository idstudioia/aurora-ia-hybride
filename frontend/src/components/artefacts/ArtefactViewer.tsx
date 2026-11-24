// P2 « Pages »: the viewer, a modal over the whole app (mounted once in App.tsx, opened by store.ts).
// The page is the service's standalone HTML (escaped text, SVG charts, no script), shown in a sandboxed
// frame: never allow-scripts nor allow-same-origin; links may only open outside (popups).
import { useEffect, useId, useRef, useState } from 'react';
import { Download, FileText, Trash2, X } from 'lucide-react';
import { api } from '../../lib/api';
import { ARTEFACT_KIND_LABELS, fmtDateTime } from '../../lib/format';
import type { ArtefactDetail } from '../../lib/types';
import { Badge, Spinner, Unavailable } from '../ui';
import { download } from '../QualityReports';
import { artefactsChanged, closeArtefact, type OpenArtefact } from './store';
import './artefacts.css';

const FOCUSABLE = 'a[href], button:not([disabled]), select:not([disabled]), iframe, [tabindex]:not([tabindex="-1"])';

export default function ArtefactViewer({ open }: { open: OpenArtefact }) {
  const { id } = open;
  const [detail, setDetail] = useState<ArtefactDetail | null>(null);
  const [detailError, setDetailError] = useState('');
  // null = the latest version (no need to wait for the detail to show it)
  const [version, setVersion] = useState<number | null>(open.version);
  const [html, setHtml] = useState<string | null>(null);
  const [htmlError, setHtmlError] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const dialog = useRef<HTMLDivElement>(null);
  const deleteBtn = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef(false);
  confirmRef.current = confirm;
  const titleId = useId();

  useEffect(() => {
    let alive = true;
    api.artefact(id)
      .then(d => { if (alive) { setDetail(d); setDetailError(''); } })
      .catch(err => { if (alive) setDetailError(err instanceof Error ? err.message : 'Service local indisponible'); });
    return () => { alive = false; };
  }, [id]);

  useEffect(() => {
    let alive = true;
    setHtml(null);
    setHtmlError('');
    api.artefactHtml(id, version ?? undefined)
      .then(text => { if (alive) setHtml(text); })
      .catch(err => { if (alive) setHtmlError(err instanceof Error ? err.message : 'Service local indisponible'); });
    return () => { alive = false; };
  }, [id, version]);

  // Focus into the dialog; Échap closes the confirmation first, then the viewer. Captured on window so
  // the chat drawer and the flow replay (their own Échap) never see this press.
  useEffect(() => {
    dialog.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopPropagation();
      if (confirmRef.current) { setConfirm(false); deleteBtn.current?.focus(); } else closeArtefact();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, []);

  // Focus guards around the dialog: Tab never leaves it (the frame included).
  const focusEdge = (edge: 'first' | 'last') => {
    const items = [...(dialog.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])];
    (edge === 'first' ? items[0] : items[items.length - 1])?.focus();
  };

  const shown = version ?? detail?.latest_version ?? null;
  const info = detail?.history.find(v => v.version === shown) ?? null;
  const title = info?.title || detail?.title || '';
  const kind = detail ? ARTEFACT_KIND_LABELS[detail.kind] ?? detail.kind : '—';

  const save = () => {
    if (html === null || shown === null) return;
    download(new Blob([html], { type: 'text/html;charset=utf-8' }), `aurora-page-${id.replace(/[^\w.-]+/g, '-')}-v${shown}.html`);
  };

  const remove = async () => {
    setBusy(true);
    setActionError('');
    try {
      await api.deleteArtefact(id);
      artefactsChanged();
      closeArtefact();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Suppression impossible');
      setBusy(false);
    }
  };

  return (
    <div className="art-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) closeArtefact(); }}>
      <span tabIndex={0} className="art-guard" onFocus={() => focusEdge('last')} />
      <div ref={dialog} className="art-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <header className="art-head">
          <div className="art-titles">
            <p className="art-kicker"><FileText size={13} /> {kind}</p>
            <h2 id={titleId}>{title || (detailError ? 'Page' : 'Chargement…')}</h2>
            {detail?.subtitle && <p className="art-subtitle">{detail.subtitle}</p>}
          </div>
          <div className="art-tools">
            {detail?.sensitive && <Badge tone="amber" title="Cette page contient des données marquées sensibles.">sensible</Badge>}
            {detail && detail.history.length > 1 ? (
              <label className="art-version">Version
                <select className="select" value={shown ?? ''} onChange={e => setVersion(Number(e.target.value))}>
                  {detail.history.map(v => <option key={v.version} value={v.version}>v{v.version} · {fmtDateTime(v.created_at)}</option>)}
                </select>
              </label>
            ) : (
              <span className="art-version">{shown !== null ? `v${shown} · ${fmtDateTime(info?.created_at)}` : '—'}</span>
            )}
            <button type="button" className="btn btn-sm" onClick={save} disabled={html === null || shown === null}
              title="Enregistrer cette version comme fichier .html"><Download size={14} /> Télécharger</button>
            <button ref={deleteBtn} type="button" className="btn btn-sm btn-danger" onClick={() => setConfirm(true)} disabled={busy || !detail}>
              <Trash2 size={14} /> Supprimer
            </button>
            <button type="button" className="icon-btn" onClick={closeArtefact} title="Fermer (Échap)" aria-label="Fermer la page"><X size={18} /></button>
          </div>
        </header>
        <p className="art-note">Chiffres et graphiques calculés par AURORA à partir des outils ; le texte marqué « rédigé par… » vient de l'IA.</p>
        {confirm && detail && (
          <div className="art-confirm" role="alertdialog" aria-label="Confirmer la suppression">
            <span>Supprimer définitivement cette page et {detail.versions > 1 ? `ses ${detail.versions} versions` : 'sa version'} ?</span>
            <button type="button" className="btn btn-sm btn-danger" onClick={() => void remove()} disabled={busy}>
              {busy ? <Spinner size={12} /> : <Trash2 size={13} />} Oui, supprimer
            </button>
            <button type="button" className="btn btn-sm" autoFocus onClick={() => { setConfirm(false); deleteBtn.current?.focus(); }} disabled={busy}>Non</button>
          </div>
        )}
        {actionError && <p className="art-error" role="alert">{actionError}</p>}
        {detailError && !htmlError && <Unavailable what="Informations de la page" reason={detailError} />}
        <div className="art-body">
          {htmlError ? <Unavailable what="Page" reason={htmlError} />
            : html === null ? <div className="art-loading"><Spinner size={22} /> Chargement de la page…</div>
              : <iframe className="art-frame" title={`Page : ${title || 'sans titre'}`} sandbox="allow-popups allow-popups-to-escape-sandbox"
                  referrerPolicy="no-referrer" srcDoc={html} />}
        </div>
      </div>
      <span tabIndex={0} className="art-guard" onFocus={() => focusEdge('first')} />
    </div>
  );
}
