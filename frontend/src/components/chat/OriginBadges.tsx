// Where an answer really comes from (RunResult.origin, computed by the service on the steps that ran):
// Mesuré, Source, Mémoire or Modèle seul, plus « rédigé par … » when an AI reworded the data.
// Shared by the live answer (RunCard) and the history (ChatPage, message meta.origin).
import type { AnswerOrigin } from '../../lib/types';
import { Badge } from '../ui';
import { isObj } from './text';
import { ORIGIN_LABELS, WRITER_LABELS } from './labels';

/** A stored origin (history JSON) is checked before use: anything else shows no badge. */
export function asOrigin(value: unknown): AnswerOrigin | null {
  if (!isObj(value) || !Array.isArray(value.kinds)) return null;
  const kinds = value.kinds.filter((k): k is AnswerOrigin['kinds'][number] => typeof k === 'string' && k in ORIGIN_LABELS);
  if (!kinds.length) return null;
  const writer = value.written_by === 'local' || value.written_by === 'cloud' ? value.written_by : null;
  return { kinds, written_by: writer, tools: Array.isArray(value.tools) ? value.tools.map(String) : [],
    sources: typeof value.sources === 'number' ? value.sources : 0,
    unverified: Array.isArray(value.unverified) ? value.unverified.map(String).slice(0, 8) : [] };
}

export function OriginBadges({ origin }: { origin: unknown }) {
  const o = asOrigin(origin);
  if (!o) return null;
  const writer = o.written_by ? WRITER_LABELS[o.written_by] : '';
  const alone = o.kinds.length === 1 && o.kinds[0] === 'modele';
  // Grouped under « Origine »: the same tones also mean the intent (cyan) and Claude (violet) on this line.
  return (
    <span className="chat-origin" role="group" aria-label="Origine de la réponse">
      <span className="chat-origin-label">Origine</span>
      {o.kinds.map(k => {
        const l = ORIGIN_LABELS[k];
        const title = k === 'source' && o.sources ? `${l.title} (${o.sources} lien${o.sources > 1 ? 's' : ''})` : l.title;
        return <Badge key={k} tone={l.tone} title={title}>{alone && writer ? `${l.label} · ${writer}` : l.label}</Badge>;
      })}
      {!alone && writer && (
        <Badge tone="amber" title="Le texte a été reformulé par l’IA à partir de ces données : une phrase peut être inexacte même si les données sont justes.">
          rédigé par {o.written_by === 'cloud' ? 'Claude' : 'l’IA locale'}
        </Badge>
      )}
      {alone && o.unverified && o.unverified.length > 0 && (
        <Badge tone="amber" title={`Écrits de mémoire par l’IA, sans outil ni source : à vérifier avant de vous y fier. ${o.unverified.join(' · ')}`}>
          non vérifié : {o.unverified.slice(0, 3).join(', ')}{o.unverified.length > 3 ? ` (+${o.unverified.length - 3})` : ''}
        </Badge>
      )}
    </span>
  );
}
