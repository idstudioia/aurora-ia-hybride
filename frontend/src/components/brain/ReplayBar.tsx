// Replay transport bar: replays recorded memory + run events in order (read-only).
import { Pause, Play, Radio, RotateCcw } from 'lucide-react';
import { fmtDateTime } from '../../lib/format';
import { Spinner } from '../ui';
import { describeEvent } from './model';
import { SPEEDS, type ReplayControls } from './replay';

interface Props {
  replay: ReplayControls;
  loading: boolean;
  error: string;
  available: number;          // events in the journal (live mode)
  missedLive: number;         // live events received while replaying
  onRestart: () => void;
  onExit: () => void;
}

export default function ReplayBar({ replay, loading, error, available, missedLive, onRestart, onExit }: Props) {
  const { active, events, index, playing, speed, current } = replay;
  const total = active ? events.length : available;
  const canPlay = total > 0 && !loading;
  return (
    <div className="brain-replay" role="group" aria-label="Relecture des événements enregistrés">
      <div className="brain-replay-buttons">
        <button type="button" className="btn btn-sm" onClick={onRestart} disabled={loading} title="Rejouer depuis le début">
          {loading ? <Spinner size={13} /> : <RotateCcw size={14} />} Rejouer
        </button>
        {playing ? (
          <button type="button" className="btn btn-sm" onClick={replay.pause} title="Pause"><Pause size={14} /> Pause</button>
        ) : (
          <button type="button" className="btn btn-sm" onClick={active ? replay.resume : onRestart} disabled={!canPlay} title="Lecture"><Play size={14} /> Lecture</button>
        )}
        <div className="brain-speeds" role="radiogroup" aria-label="Vitesse de relecture">
          {SPEEDS.map(s => (
            <button key={s} type="button" role="radio" aria-checked={speed === s} className={`brain-speed ${speed === s ? 'on' : ''}`} onClick={() => replay.setSpeed(s)}>×{s}</button>
          ))}
        </div>
      </div>
      <div className="brain-replay-track">
        <input
          type="range" className="brain-range" min={0} max={Math.max(0, events.length - 1)} step={1}
          value={active ? Math.max(0, index) : 0}
          disabled={!active || events.length === 0}
          onChange={ev => replay.seek(Number(ev.target.value))}
          aria-label="Curseur de temps de la relecture"
          aria-valuetext={current ? `${fmtDateTime(current.created_at)} — ${current.label}` : 'Début'}
        />
        <div className="brain-replay-label" aria-live="polite">
          {error ? <span className="brain-replay-error">Journal indisponible — {error}</span>
            : !active ? <span className="muted">{total ? `Direct · ${total} événement${total > 1 ? 's' : ''} mémoire et tâches enregistrés` : 'Aucun événement enregistré à rejouer.'}</span>
            : current ? <><time>{fmtDateTime(current.created_at)}</time><span>{describeEvent(current)}</span></>
            : <span className="muted">Début de la relecture · {events.length} événement{events.length > 1 ? 's' : ''}</span>}
        </div>
      </div>
      <div className="brain-replay-side">
        {active && <span className="brain-replay-count mono">{Math.max(0, index + 1)}/{events.length}</span>}
        {active && (
          <button type="button" className="btn btn-sm btn-ghost" onClick={onExit} title="Revenir aux événements en direct">
            <Radio size={14} /> Direct{missedLive > 0 ? ` (+${missedLive})` : ''}
          </button>
        )}
      </div>
    </div>
  );
}
