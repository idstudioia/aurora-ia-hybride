// Full chat page: conversation list (api.conversations), history of past conversations
// (api.messages, read-only until « Continuer » makes one active via store.openConversation),
// the live conversation (ChatPanel) and suggestions.
import { useCallback, useEffect, useState } from 'react';
import { Archive, MessageSquare, MessageSquarePlus, RefreshCw, Sparkles, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import type { ChatMessage, Conversation } from '../lib/types';
import { ENGINE_LABELS, INTENT_LABELS, fmtDateTime, fmtUsd, relativeTime } from '../lib/format';
import ChatPanel from '../components/ChatPanel';
import { Composer } from '../components/chat/ComposerParts';
import ContextCard from '../components/chat/ContextCard';
import { Markdown } from '../components/chat/Markdown';
import { OriginBadges, asOrigin } from '../components/chat/OriginBadges';
import { useSend } from '../components/chat/useSend';
import MakePageButton from '../components/artefacts/MakePageButton';
import { isObj, num, plainText, str } from '../components/chat/text';
import { EmptyState, Spinner, Unavailable } from '../components/ui';
import './chat.css';

const SUGGESTIONS = [
  'Aurora, pourquoi mon ordinateur ralentit-il ?',
  'Aurora, retrouve notre ancienne recherche sur Mars',
  'Aurora, recherche les dernières découvertes de la NASA',
  "Aurora, simule la trajectoire d'une sonde autour de Mars",
  'Aurora, mon PC est lent. Analyse le problème, recherche des solutions et prépare une optimisation.',
  'Quelle quantité de RAM est utilisée ?',
  'As-tu accès à Internet ?',
];

function parseMeta(raw: string): Record<string, unknown> {
  try { const v = JSON.parse(raw || '{}'); return isObj(v) ? v : {}; } catch { return {}; }
}

function HistoryMessage({ m }: { m: ChatMessage }) {
  const meta = parseMeta(m.meta_json);
  const intent = str(meta, 'intent');
  const engine = str(meta, 'engine');
  const model = str(meta, 'model');
  const cost = num(meta, 'cost_usd');
  const origin = asOrigin(meta.origin);
  if (m.role === 'user') {
    return (
      <div className="chat-entry chat-entry-user">
        <div className="chat-bubble">{plainText(m.content)}</div>
        <div className="chat-meta"><time dateTime={m.created_at}>{fmtDateTime(m.created_at)}</time></div>
      </div>
    );
  }
  if (m.role === 'note') return <div className="chat-entry chat-entry-note">{plainText(m.content)}</div>;
  return (
    <div className="chat-entry chat-entry-assistant">
      <article className="chat-run">
        {(origin || intent || engine || model || m.run_id) && (
          <div className="chat-run-badges">
            <OriginBadges origin={origin} />
            {intent && <span className="badge badge-cyan">{INTENT_LABELS[intent] ?? intent}</span>}
            {engine && <span className="badge">{ENGINE_LABELS[engine] ?? engine}</span>}
            {model && <span className="badge">{model}</span>}
            {cost !== null && cost > 0 && <span className="badge badge-amber">{fmtUsd(cost)}</span>}
            {m.run_id !== null && <span className="badge" title="Identifiant du traitement (voir la page Tâches pour le rejouer)">traitement #{m.run_id}</span>}
          </div>
        )}
        <Markdown text={m.content} className="chat-run-answer" />
        <div className="chat-meta">
          <time dateTime={m.created_at}>{fmtDateTime(m.created_at)}</time>
          {/* P2: the service answers 409 / 422 (shown in line) when this run cannot become a page */}
          {m.run_id !== null && <MakePageButton source={{ runId: m.run_id }} />}
        </div>
      </article>
    </div>
  );
}

function HistoryView({ conversation, onContinue }: { conversation: Conversation; onContinue: () => void }) {
  const { openConversation } = useAurora();
  const [messages, setMessages] = useState<ChatMessage[] | null>(null);
  const [error, setError] = useState('');
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState('');
  useEffect(() => {
    let alive = true;
    setMessages(null);
    setError('');
    setOpenError('');
    api.messages(conversation.id)
      .then(list => { if (alive) setMessages(list); })
      .catch(err => { if (alive) setError(err instanceof Error ? err.message : 'Service indisponible'); });
    return () => { alive = false; };
  }, [conversation.id]);
  const resume = async () => {
    setOpening(true);
    setOpenError('');
    try { await openConversation(conversation.id); onContinue(); }
    catch (err) { setOpenError(err instanceof Error ? err.message : 'Conversation impossible à rouvrir'); }
    finally { setOpening(false); }
  };
  return (
    <div className="chat-panel chat-history" role="log" aria-label={`Historique : ${conversation.title || 'conversation'}`}>
      <div className="chat-history-banner">
        <Archive size={14} />
        <span>Conversation archivée du {fmtDateTime(conversation.created_at)} — lecture seule. Les nouvelles demandes partent dans la conversation active.</span>
        <button type="button" className="btn btn-sm chat-history-resume" onClick={() => void resume()} disabled={opening || !!error}>
          {opening ? <Spinner size={12} /> : <MessageSquare size={13} />} Continuer cette conversation
        </button>
      </div>
      {openError && <Unavailable what="Reprise de la conversation" reason={openError} />}
      {error ? <Unavailable what="Messages" reason={error} />
        : messages === null ? <EmptyState><Spinner /> Chargement des messages…</EmptyState>
        : messages.length === 0 ? <EmptyState>Aucun message enregistré dans cette conversation.</EmptyState>
        : messages.map(m => <HistoryMessage key={m.id} m={m} />)}
    </div>
  );
}

export default function ChatPage() {
  const { conversationId, chat, newConversation, online } = useAurora();
  const send = useSend();
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [listError, setListError] = useState('');
  const [selected, setSelected] = useState<number | null>(null); // null = live conversation
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try { setConversations(await api.conversations()); setListError(''); }
    catch (err) { setListError(err instanceof Error ? err.message : 'Service indisponible'); }
    finally { setLoading(false); }
  }, []);

  // refresh when the active conversation changes or a new answer arrives
  const answered = chat.filter(e => e.role === 'assistant' && !e.pending).length;
  useEffect(() => { void load(); }, [load, conversationId, answered, online]);

  // a new message (sent from anywhere: bar, voice, drawer) brings the live conversation back
  const sent = chat.filter(e => e.role === 'user').length;
  useEffect(() => { if (sent) setSelected(null); }, [sent]);

  const viewing = selected !== null && selected !== conversationId ? conversations?.find(c => c.id === selected) ?? null : null;

  // Bin icon of a row: an inline confirmation first (CLAUDE.md), then the service deletes (no copy, as asked).
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const askDelete = (id: number | null) => { setConfirmId(id); setDeleteError(''); };
  const remove = async (c: Conversation) => {
    setDeleting(true);
    setDeleteError('');
    try {
      await api.deleteConversation(c.id);
      if (c.id === conversationId) newConversation();   // its next answer would have nowhere to go
      if (selected === c.id) setSelected(null);
      setConfirmId(null);
      await load();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Suppression impossible');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="chat-page">
      <aside className="chat-page-list panel" aria-label="Conversations">
        <div className="chat-page-list-head">
          <strong>Conversations</strong>
          <button type="button" className="icon-btn" onClick={() => void load()} title="Actualiser" aria-label="Actualiser la liste">{loading ? <Spinner size={14} /> : <RefreshCw size={15} />}</button>
        </div>
        <button type="button" className="btn btn-primary chat-page-new" onClick={() => { newConversation(); setSelected(null); }}>
          <MessageSquarePlus size={16} /> Nouvelle conversation
        </button>
        <button type="button" className={`chat-conv ${viewing === null ? 'active' : ''}`} onClick={() => setSelected(null)}>
          <MessageSquare size={14} />
          <span className="chat-conv-title">Conversation active</span>
          <small>{chat.length ? `${chat.filter(e => e.role === 'user').length} message(s)` : 'vide'}</small>
        </button>
        <div className="chat-conv-list">
          {listError ? <Unavailable what="Conversations" reason={listError} />
            : conversations === null ? <EmptyState><Spinner /></EmptyState>
            : conversations.length === 0 ? <EmptyState>Aucune conversation enregistrée.</EmptyState>
            : conversations.map(c => {
              const title = plainText(c.title) || 'Sans titre';
              return (
                <div key={c.id} className="chat-conv-row">
                  <button type="button" className={`chat-conv ${viewing?.id === c.id || (viewing === null && c.id === conversationId) ? 'active' : ''}`}
                    onClick={() => setSelected(c.id === conversationId ? null : c.id)} title={c.title || 'Sans titre'}>
                    <MessageSquare size={14} />
                    <span className="chat-conv-title">{title}</span>
                    <small>{c.id === conversationId ? 'en cours' : relativeTime(c.updated_at)}</small>
                  </button>
                  {confirmId !== c.id && (
                    <button type="button" className="icon-btn chat-conv-del" onClick={() => askDelete(c.id)} disabled={!online || deleting}
                      title="Supprimer cette conversation" aria-label={`Supprimer la conversation « ${title} »`}>
                      <Trash2 size={14} />
                    </button>
                  )}
                  {confirmId === c.id && (
                    <div className="chat-conv-confirm" role="alertdialog" aria-label="Confirmer la suppression">
                      <span>
                        Supprimer « {title} »{c.message_count !== undefined ? ` et ses ${c.message_count} message(s)` : ''} ?
                        Ce sera définitif.
                      </span>
                      <div className="chat-conv-confirm-actions">
                        <button type="button" className="btn btn-sm btn-danger" onClick={() => void remove(c)} disabled={deleting}>
                          {deleting ? <Spinner size={12} /> : <Trash2 size={13} />} Oui, supprimer
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => askDelete(null)} disabled={deleting}>Non</button>
                      </div>
                      {deleteError && <p className="chat-conv-error" role="alert">{deleteError}</p>}
                    </div>
                  )}
                </div>
              );
            })}
        </div>
      </aside>

      <section className="chat-page-main panel" aria-label="Conversation">
        <div className="chat-page-head">
          <div>
            <div className="eyebrow">AURORA</div>
            <h1>Chat IA</h1>
          </div>
          <p className="muted">{viewing ? plainText(viewing.title) || 'Sans titre' : conversationId ? `Conversation #${conversationId}` : 'Nouvelle conversation'}</p>
        </div>
        {viewing ? <HistoryView conversation={viewing} onContinue={() => setSelected(null)} /> : (
          <ChatPanel className="chat-page-panel" empty={
            <div className="chat-page-empty">
              <Sparkles size={22} />
              <p>Posez une question par écrit, à la voix (micro en bas) ou choisissez une suggestion.</p>
            </div>
          } />
        )}
        {!viewing && <Composer placeholder="Écrivez votre message… (Entrée pour envoyer, Maj+Entrée pour aller à la ligne)" />}
      </section>

      <aside className="chat-page-side panel" aria-label="Contexte et suggestions">
        <ContextCard />
        <div className="chat-page-list-head"><strong>Suggestions</strong></div>
        <ul className="chat-suggestions">
          {SUGGESTIONS.map(s => (
            <li key={s}><button type="button" className="chat-suggestion" onClick={() => { setSelected(null); void send(s, { source: 'text', withAttachments: false }); }}>{s}</button></li>
          ))}
        </ul>
      </aside>
    </div>
  );
}
