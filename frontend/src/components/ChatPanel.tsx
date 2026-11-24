// The active conversation (store.chat). Pending messages show live progress rebuilt from the
// SSE events of their run; answers are RunCards; loose pending permissions are listed first.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Mic, MousePointerClick } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora, type ChatEntry } from '../lib/store';
import { useStreamStatus } from '../lib/stream';
import { fmtTime } from '../lib/format';
import RunCard from './RunCard';
import { Markdown } from './chat/Markdown';
import LiveProgress from './chat/LiveProgress';
import PermissionCard from './chat/PermissionCard';
import ConversationConsentBar from './chat/ConversationConsentBar';
import { claimRun, runEvents, useLiveFeed } from './chat/live';
import { plainText } from './chat/text';
import '../pages/chat.css';

function userTextBefore(chat: ChatEntry[], index: number): string {
  for (let i = index - 1; i >= 0; i--) if (chat[i].role === 'user') return chat[i].text;
  return '';
}

function PendingEntry({ entry, userText, compact, connected }: { entry: ChatEntry; userText: string; compact: boolean; connected: boolean }) {
  const sentAt = Date.parse(entry.at);
  const runId = claimRun(entry.id, userText, sentAt);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState('');
  const cancel = async () => {
    if (runId === null) return;
    setCancelling(true);
    setCancelError('');
    try { await api.cancelRun(runId); } catch (err) { setCancelError(err instanceof Error ? err.message : 'Annulation impossible'); setCancelling(false); }
  };
  return (
    <div className="chat-entry chat-entry-assistant">
      <LiveProgress events={runEvents(runId)} startedAt={Number.isFinite(sentAt) ? sentAt : Date.now()} connected={connected} compact={compact}
        onCancel={runId !== null ? () => void cancel() : undefined} cancelling={cancelling} />
      {cancelError && <div className="chat-run-error" role="alert">{cancelError}</div>}
    </div>
  );
}

export default function ChatPanel({ compact = false, empty, className = '' }: { compact?: boolean; empty?: ReactNode; className?: string }) {
  const { chat, pendingPermissions, applyRunUpdate } = useAurora();
  // follow live events only while something is in progress (pending message or running run)
  const live = useLiveFeed(chat.some(e => e.pending || e.run?.status === 'running'));
  const connected = useStreamStatus();
  const scrollRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const linkedPermissions = new Set<number>();
  const waitingTasks = new Set<number>();
  for (const e of chat) {
    if (e.run?.permission) linkedPermissions.add(e.run.permission.id);
    if (e.run?.status === 'waiting_permission') waitingTasks.add(e.run.task_id);
  }
  const loose = pendingPermissions.filter(p => !linkedPermissions.has(p.id) && !(p.task_id !== null && waitingTasks.has(p.task_id)));

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [chat, live, loose.length]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => { stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90; };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <div className={`chat-panel ${compact ? 'chat-panel-compact' : ''} ${className}`} ref={scrollRef} role="log" aria-label="Conversation avec AURORA" aria-live="polite">
      <ConversationConsentBar />
      {loose.length > 0 && (
        <section className="chat-loose" aria-label="Autorisations en attente">
          <div className="chat-loose-title">Autorisations en attente</div>
          {loose.map(p => <PermissionCard key={p.id} permission={p} compact={compact} />)}
        </section>
      )}
      {chat.length === 0 && loose.length === 0 && (empty ?? <div className="chat-empty">Posez une question à AURORA : texte, voix ou boutons passent tous par le même moteur de décision.</div>)}
      {chat.map((entry, i) => {
        if (entry.role === 'user') {
          return (
            <div key={entry.id} className="chat-entry chat-entry-user">
              <div className="chat-bubble">{plainText(entry.text)}</div>
              <div className="chat-meta">
                {entry.source === 'voice' && <span title="Dicté à la voix"><Mic size={11} /> voix</span>}
                {entry.source === 'panel' && <span title="Envoyé par un bouton"><MousePointerClick size={11} /> bouton</span>}
                <time dateTime={entry.at}>{fmtTime(entry.at)}</time>
              </div>
            </div>
          );
        }
        if (entry.role === 'note') {
          return <div key={entry.id} className="chat-entry chat-entry-note" role="status">{plainText(entry.text)}</div>;
        }
        if (entry.pending) {
          return <PendingEntry key={entry.id} entry={entry} userText={userTextBefore(chat, i)} compact={compact} connected={connected} />;
        }
        return (
          <div key={entry.id} className="chat-entry chat-entry-assistant">
            {entry.run ? <RunCard run={entry.run} compact={compact} onRunUpdate={applyRunUpdate} startedAt={Date.parse(entry.at)} /> : <Markdown text={entry.text} className="chat-run-answer" />}
          </div>
        );
      })}
    </div>
  );
}
