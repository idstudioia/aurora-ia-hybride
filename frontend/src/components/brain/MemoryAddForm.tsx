// Side panel: add a memory by hand. The brain animates when the service emits memory.saved.
import { useState } from 'react';
import { Plus } from 'lucide-react';
import { api } from '../../lib/api';
import type { MemoryItem } from '../../lib/types';
import { Spinner } from '../ui';
import { CONFIDENTIALITY_LABELS, KIND_LABELS } from './model';
import { errorText } from './hooks';

interface Props {
  categories: string[];
  onSaved: (memory: MemoryItem) => void;
}

const KINDS = Object.keys(KIND_LABELS) as MemoryItem['kind'][];
const CONFIDENTIALITIES = Object.keys(CONFIDENTIALITY_LABELS) as MemoryItem['confidentiality'][];

export default function MemoryAddForm({ categories, onSaved }: Props) {
  const [title, setTitle] = useState('');
  const [detail, setDetail] = useState('');
  const [category, setCategory] = useState('');
  const [kind, setKind] = useState<MemoryItem['kind']>('fact');
  const [confidentiality, setConfidentiality] = useState<MemoryItem['confidentiality']>('private');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  return (
    <form className="mem-form" onSubmit={async ev => {
      ev.preventDefault();
      const t = title.trim();
      if (!t) return;
      setBusy(true); setError(''); setNotice('');
      try {
        const saved = await api.addMemory(t, detail.trim(), category.trim() || 'general', { kind, confidentiality });
        setTitle(''); setDetail('');
        setNotice(`« ${saved.title} » enregistré.`);
        onSaved(saved);
      } catch (err) {
        setError(errorText(err, 'Enregistrement impossible'));
      } finally {
        setBusy(false);
      }
    }}>
      <label className="field">Titre
        <input className="input" value={title} onChange={ev => setTitle(ev.target.value)} required maxLength={200} placeholder="Ex. : je préfère le mode sombre" />
      </label>
      <label className="field">Détail
        <textarea className="textarea" rows={4} value={detail} onChange={ev => setDetail(ev.target.value)} maxLength={4000} placeholder="Contexte, précisions, source…" />
      </label>
      <label className="field">Catégorie
        <input className="input" value={category} onChange={ev => setCategory(ev.target.value)} list="mem-categories" maxLength={60} placeholder="general" />
        <datalist id="mem-categories">{categories.map(c => <option key={c} value={c} />)}</datalist>
      </label>
      <div className="mem-row">
        <label className="field">Type
          <select className="select" value={kind} onChange={ev => setKind(ev.target.value as MemoryItem['kind'])}>
            {KINDS.map(k => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}
          </select>
        </label>
        <label className="field">Confidentialité
          <select className="select" value={confidentiality} onChange={ev => setConfidentiality(ev.target.value as MemoryItem['confidentiality'])}>
            {CONFIDENTIALITIES.map(c => <option key={c} value={c}>{CONFIDENTIALITY_LABELS[c]}</option>)}
          </select>
        </label>
      </div>
      {confidentiality === 'sensitive' && <p className="muted mem-small">Un souvenir sensible n'est jamais inclus dans un contexte envoyé au cloud.</p>}
      <button type="submit" className="btn btn-primary" disabled={busy || !title.trim()}>{busy ? <Spinner size={14} /> : <Plus size={15} />} Enregistrer le souvenir</button>
      {error && <p className="mem-error" role="alert">{error}</p>}
      {notice && <p className="mem-notice" role="status">{notice}</p>}
    </form>
  );
}
