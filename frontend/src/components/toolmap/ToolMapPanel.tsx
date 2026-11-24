// Carte des outils, panneau de droite de la page Tâches : la liste des outils par grande famille (utilisable au
// clavier, et secours si le canvas ne s'affiche pas), ou la fiche de l'outil choisi. Échap ferme la fiche.
// La phrase d'exemple est montrée et peut être copiée ; elle n'est jamais envoyée d'ici : certaines lancent une
// vraie action (une commande de terminal, l'ouverture d'une application).
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { Check, Copy, RefreshCw, X } from 'lucide-react';
import { fmtDateTime, relativeTime } from '../../lib/format';
import { useAurora } from '../../lib/store';
import type { ToolMapData, ToolMapTool } from '../../lib/types';
import { Badge, Spinner } from '../ui';
import { RISK_LABELS } from '../chat/labels';
import {
  NO_EXAMPLE, OTHER_TOOLS_TITLE, USES_LABEL, familyOf, permissionText, toolList, toolStateLabel, usesText, type ToolListGroup,
} from '../brain/toolMap';
import { ToolGlyph } from './ToolMapView';
import type { ToolMapState } from './useToolMap';
import './toolmap.css';

/** Les textes de famille du service commencent par une minuscule (ils suivent « Titre : ») : ici ils sont seuls. */
const sentence = (text: string) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : '');
const plural = (n: number, word: string) => `${n} ${word}${n > 1 ? 's' : ''}`;

export default function ToolMapPanel({ state, selected, onSelect }: {
  state: ToolMapState;
  selected: string | null;
  onSelect: (name: string | null) => void;
}) {
  const { map, error, loading, reload } = state;
  const { chatOpen } = useAurora();
  const root = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  /** La fiche a été ouverte depuis la liste (au clavier ou à la souris) : le focus la suit, puis revient à la liste. */
  const viaList = useRef(false);
  const last = useRef<string | null>(null);
  const groups = useMemo(() => (map ? toolList(map) : []), [map]);
  const tool = map && selected ? map.tools.find(t => t.name === selected) ?? null : null;
  const open = !!tool;
  const close = useCallback(() => onSelect(null), [onSelect]);

  // L'outil choisi n'est plus dans la carte relue : on revient à la liste plutôt que de montrer une fiche vide.
  useEffect(() => { if (map && selected && !tool) onSelect(null); }, [map, selected, tool, onSelect]);

  // Échap ferme la fiche. Le tiroir du chat, s'il est ouvert, se ferme d'abord (même règle que le flux).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || e.ctrlKey || e.altKey || e.metaKey) return;
      const target = e.target instanceof HTMLElement ? e.target : null;
      if (chatOpen || target?.closest('.chat-drawer, [role="dialog"]')) return;
      close();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, chatOpen, close]);

  // Le focus suit : dans la fiche quand elle s'ouvre depuis la liste, puis de retour sur l'outil dans la liste.
  const name = tool?.name ?? null;
  useEffect(() => {
    if (name) {
      last.current = name;
      // Le haut de la fiche d'abord (son nom, le bouton Fermer) : la liste pouvait être défilée loin.
      const box = root.current?.parentElement;
      if (box && box.scrollTop > 0) box.scrollTop = 0;
      // Fenêtre étroite : le panneau est sous la carte. Si la fiche est hors de l'écran, on l'y amène.
      const el = card.current;
      if (el && el.getBoundingClientRect().top >= window.innerHeight) el.scrollIntoView({ block: 'nearest' });
      if (viaList.current) el?.focus({ preventScroll: true });
      return;
    }
    const back = last.current;
    last.current = null;
    if (!back) return;
    const active = document.activeElement;
    if (viaList.current || !active || active === document.body) {
      root.current?.querySelector<HTMLElement>(`[data-tool="${CSS.escape(back)}"]`)?.focus();
    }
    viaList.current = false;
  }, [name]);

  const openFromList = (toolName: string) => { viaList.current = true; onSelect(toolName); };

  return (
    <div ref={root}>
      {map && tool ? <ToolCard map={map} tool={tool} onClose={close} cardRef={card} />
        : map ? <FamilyList map={map} groups={groups} onOpen={openFromList} />
          : (
            <div className="tasks-follow-empty">
              <h3>Outils d'AURORA</h3>
              {error ? (
                <>
                  <p role="alert">La liste des outils n'a pas pu être lue : {error}</p>
                  <p><button type="button" className="btn btn-sm" onClick={reload} disabled={loading}>{loading ? <Spinner size={12} /> : <RefreshCw size={14} />} Réessayer</button></p>
                </>
              ) : <p><Spinner size={12} /> Lecture des outils…</p>}
            </div>
          )}
    </div>
  );
}

// ---------------------------------------------------------------- liste par grande famille

/** Les groupes calculés par toolList (brain/toolMap.ts), en boutons : c'est par elle que la carte se parcourt au clavier. */
function FamilyList({ map, groups, onOpen }: { map: ToolMapData; groups: ToolListGroup[]; onOpen: (name: string) => void }) {
  return (
    <div className="toolmap-list">
      <div className="tasks-follow-empty">
        <h3>Outils par grande famille</h3>
        <p>Choisissez un outil, ici ou sur la carte, pour ouvrir sa fiche. Au clavier : Tab pour avancer, Entrée pour ouvrir, Échap pour fermer.</p>
      </div>
      {groups.map((g, i) => (
        <section key={g.id} className="toolmap-group" aria-labelledby={`toolmap-group-${i}`}>
          <h4 id={`toolmap-group-${i}`}>{g.title}<span>{g.tools.length ? plural(g.tools.length, 'outil') : 'aucun outil'}</span></h4>
          {g.description && <p className="toolmap-group-desc">{sentence(g.description)}.</p>}
          <ul>
            {g.tools.map(t => {
              const family = familyOf(map, t.family)?.title ?? t.family;
              const named = !!t.title && t.title !== t.name;
              return (
                <li key={t.name}>
                  <button type="button" className="toolmap-item" data-tool={t.name} data-state={t.state} onClick={() => onOpen(t.name)}
                    title={t.reason ?? undefined}>
                    <ToolGlyph state={t.state} />
                    <span className="toolmap-item-copy">
                      <strong>{t.title || t.name}</strong>
                      <small>{named ? `${family} · ${t.name}` : family}</small>
                    </span>
                    <span className="toolmap-item-state">{toolStateLabel(t.state)}</span>
                  </button>
                </li>
              );
            })}
            {/* Une capacité d'AURORA elle-même : pas un outil du registre, donc ni état, ni hexagone, ni fiche. */}
            {g.builtins.map(f => (
              <li key={f.id} className="toolmap-builtin">
                <span className="toolmap-item-copy">
                  <strong>{f.title}</strong>
                  {f.description && <small>{sentence(f.description)}.</small>}
                </span>
                <span className="toolmap-item-state">capacité intégrée</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- fiche d'un outil

function ToolCard({ map, tool, onClose, cardRef }: {
  map: ToolMapData; tool: ToolMapTool; onClose: () => void; cardRef: RefObject<HTMLDivElement | null>;
}) {
  const family = familyOf(map, tool.family);
  const group = map.groups.find(g => g.id === tool.group) ?? null;
  const named = !!tool.title && tool.title !== tool.name;
  const needs = tool.needs;
  const risk = needs ? RISK_LABELS[needs.risk] : undefined;
  const example = typeof tool.example === 'string' && tool.example.trim() ? tool.example : null;

  // « Copier » : la phrase va dans le presse-papiers, rien n'est envoyé à AURORA.
  const [copied, setCopied] = useState<'' | 'ok' | 'failed'>('');
  useEffect(() => { setCopied(''); }, [tool.name]);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(''), 4000);
    return () => window.clearTimeout(t);
  }, [copied]);
  const copy = () => {
    if (!example) return;
    const done = navigator.clipboard?.writeText(example);
    if (!done) { setCopied('failed'); return; }
    done.then(() => setCopied('ok'), () => setCopied('failed'));
  };

  return (
    <div className="flow-trace toolmap-card" ref={cardRef} tabIndex={-1} role="region" aria-labelledby="toolmap-card-title" data-state={tool.state}>
      <header className="flow-trace-head">
        <div>
          <p className="flow-trace-kicker">Fiche d'un outil</p>
          <h3 className="flow-trace-title" id="toolmap-card-title">{tool.title || tool.name}</h3>
          <p className="toolmap-id">Identifiant : <code>{tool.name}</code>{!named && ' (le code ne lui donne pas d’autre nom)'}</p>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fermer la fiche (Échap)" title="Fermer (Échap)"><X size={18} /></button>
      </header>

      <dl className="flow-trace-facts">
        <div><dt>Grande famille</dt><dd>{group?.title ?? OTHER_TOOLS_TITLE}</dd></div>
        <div><dt>Famille</dt><dd>{family?.title ?? tool.family ?? '—'}</dd></div>
      </dl>

      <section className="flow-trace-section">
        <h4>Ce qu'il fait</h4>
        <p className="toolmap-text">{tool.description || 'Le service ne donne pas de description pour cet outil.'}</p>
      </section>

      <section className="flow-trace-section">
        <h4>Son état</h4>
        <p className="toolmap-state"><ToolGlyph state={tool.state} size={18} /><strong>{toolStateLabel(tool.state)}</strong></p>
        {tool.reason && <p className="toolmap-text">{tool.reason}</p>}
      </section>

      <section className="flow-trace-section">
        <h4>Ce qu'il lui faut</h4>
        {needs ? (
          <dl className="toolmap-needs">
            <div><dt>Internet</dt><dd>{needs.internet ? 'oui' : 'non'}</dd></div>
            <div>
              <dt>Votre accord</dt>
              <dd>{permissionText(needs.permission)}{needs.permission_reason && <small>{needs.permission_reason}</small>}</dd>
            </div>
            <div><dt>Niveau de risque</dt><dd>{risk ? <Badge tone={risk.tone}>{risk.label}</Badge> : needs.risk || 'inconnu'}</dd></div>
          </dl>
        ) : <p className="toolmap-text toolmap-muted">Le service ne le dit pas pour cet outil.</p>}
      </section>

      <section className="flow-trace-section">
        <h4>Phrase d'exemple</h4>
        {example ? (
          <>
            <blockquote className="toolmap-example">{example}</blockquote>
            <div className="toolmap-example-actions">
              <button type="button" className="btn btn-sm" onClick={copy}>
                {copied === 'ok' ? <Check size={14} /> : <Copy size={14} />} {copied === 'ok' ? 'Phrase copiée' : 'Copier la phrase'}
              </button>
              <span className="toolmap-copy-note" role="status">
                {copied === 'failed' ? 'Copie impossible : sélectionnez la phrase à la main.' : ''}
              </span>
            </div>
            <p className="flow-hint">La phrase n'est pas envoyée d'ici : certaines lancent une vraie action. Pour l'essayer, collez-la vous-même dans le chat.</p>
          </>
        ) : <p className="toolmap-text toolmap-muted">{NO_EXAMPLE}.</p>}
      </section>

      <section className="flow-trace-section">
        <h4>Utilisation</h4>
        <dl className="toolmap-needs">
          <div><dt>{USES_LABEL}</dt><dd>{usesText(tool.uses)}</dd></div>
          <div>
            <dt>Dernière fois</dt>
            <dd>{tool.last_used_at ? `${relativeTime(tool.last_used_at)} · ${fmtDateTime(tool.last_used_at)}` : typeof tool.uses === 'number' ? 'jamais' : 'inconnue'}</dd>
          </div>
        </dl>
      </section>
    </div>
  );
}
