// Visible state of a conversation-wide cloud consent (modes Équilibré and Avancé, CLAUDE.md) with its revoke button.
// Refreshed on every permission.* event (grant, revoke, mode change), so it never shows a stale consent.
import { useCallback, useEffect, useState } from 'react';
import { Cloud } from 'lucide-react';
import { api } from '../../lib/api';
import { useAurora } from '../../lib/store';
import { useEventStream } from '../../lib/stream';
import type { ConversationConsent } from '../../lib/types';
import { Spinner } from '../ui';

export default function ConversationConsentBar() {
  const { conversationId } = useAurora();
  const [state, setState] = useState<ConversationConsent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(() => {
    if (!conversationId) { setState(null); return; }
    api.conversationConsent(conversationId).then(setState).catch(() => setState(null));
  }, [conversationId]);
  useEffect(() => { refresh(); }, [refresh]);
  useEventStream(event => { if (event.kind.startsWith('permission.')) refresh(); });

  if (!conversationId || !state?.active) return null;
  const revoke = async () => {
    setBusy(true); setError('');
    try { setState(await api.revokeConversationConsent(conversationId)); }
    catch (err) { setError(err instanceof Error ? err.message : 'Révocation impossible'); }
    finally { setBusy(false); }
  };
  return (
    <div className="chat-consent-bar" role="status">
      <Cloud size={15} aria-hidden="true" />
      <span>
        Envois à Claude autorisés pour toute cette conversation. Chaque texte envoyé reste affiché ;
        les données sensibles demandent toujours votre accord.
        {error && <strong role="alert"> {error}</strong>}
      </span>
      <button type="button" className="btn btn-sm btn-danger" onClick={() => void revoke()} disabled={busy}>
        {busy ? <Spinner size={13} /> : null} Révoquer
      </button>
    </div>
  );
}
