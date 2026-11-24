// P1 « Agent en plusieurs étapes » (intent agent.run): the agent's to-do list, its current turn
// (LiveProgress, from the last `agent.todo` event) and its final account (RunCard, RunResult.agent).
// Honesty (CLAUDE.md): a line is ticked only when the service says `done: true`.
import { Bot, ListChecks, Square, SquareCheck, TriangleAlert } from 'lucide-react';
import type { AgentRun, AgentTodo, AgentTurnEvent } from '../../lib/types';
import { Badge } from '../ui';
import { AGENT_STATUS_LABELS, agentStopLabel } from './labels';
import { arr, isObj, num, plainText, str } from './text';

const todoOf = (raw: unknown): AgentTodo[] =>
  arr(raw).filter(isObj).map(t => ({ text: str(t, 'text'), done: t.done === true })).filter(t => t.text);

/** The `agent.todo` payload, read tolerantly (the event payload is untyped). */
export function asAgentTurn(p: unknown): AgentTurnEvent | null {
  if (!isObj(p)) return null;
  const turn = num(p, 'turn');
  if (turn === null) return null;
  return {
    step_id: num(p, 'step_id') ?? 0, turn, max_turns: num(p, 'max_turns') ?? 0, todo: todoOf(p.todo),
    action: str(p, 'action'), title: str(p, 'title'), reason: str(p, 'reason'), correction: p.correction === true,
  };
}

/** Readable name of the chosen action. */
const actionText = (action: string, title: string) =>
  plainText(title) || (action === 'answer' ? 'Rédaction de la réponse' : action === 'refused' ? 'Action refusée par AURORA' : action || 'Action');

export function AgentTodoList({ todo }: { todo: AgentTodo[] }) {
  if (!todo.length) return null;
  const done = todo.filter(t => t.done === true).length;
  return (
    <ul className="chat-agent-todo" aria-label={`Liste de l’agent : ${done} sur ${todo.length} fait(s)`}>
      {todo.map((t, i) => {
        const ok = t.done === true;
        return (
          <li key={i} className={ok ? 'chat-ok' : undefined}>
            {ok ? <SquareCheck size={13} role="img" aria-label="fait" /> : <Square size={13} role="img" aria-label="à faire" />}
            <span>{plainText(t.text)}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** Current turn while the agent works: « Tour n/6 », the chosen action, why, and the list. */
export function AgentLive({ turn }: { turn: AgentTurnEvent }) {
  return (
    <div className="chat-agent">
      <div className="chat-agent-head">
        <Bot size={14} aria-hidden="true" />
        <strong className="mono">Tour {turn.turn}{turn.max_turns > 0 ? `/${turn.max_turns}` : ''}</strong>
        <span className={`chat-agent-action ${turn.action === 'refused' ? 'chat-warn' : ''}`}>{actionText(turn.action, turn.title)}</span>
        {turn.correction && <Badge tone="amber" title="Un contrôle de la réponse a échoué : l’agent refait une tentative avant de répondre.">correction</Badge>}
      </div>
      {turn.reason && <p className="chat-agent-reason">{plainText(turn.reason)}</p>}
      <AgentTodoList todo={turn.todo} />
    </div>
  );
}

/** Final account in the answer card: why it stopped (if it did), the list, the collapsible history. */
export function AgentSummary({ agent }: { agent: AgentRun }) {
  const todo = todoOf(agent.todo);
  const history = arr(agent.history).filter(isObj);
  const maxTurns = num(agent, 'max_turns') ?? 0;
  const turns = num(agent, 'turns') ?? history.length;
  return (
    <>
      {agent.stopped && (
        <div className="cmd-notice chat-agent-stopped" role="status"><TriangleAlert size={14} aria-hidden="true" /> {agentStopLabel(agent.stopped, maxTurns)}</div>
      )}
      {todo.length > 0 && (
        <div className="chat-run-section">
          <div className="chat-run-label"><ListChecks size={13} /> Liste de l’agent · {turns} tour(s){maxTurns > 0 ? ` sur ${maxTurns}` : ''}</div>
          <AgentTodoList todo={todo} />
        </div>
      )}
      {history.length > 0 && (
        <details className="chat-run-why chat-agent-history">
          <summary>Étapes de l’agent ({history.length})</summary>
          <ol>
            {history.map((h, i) => {
              const status = str(h, 'status');
              const st = AGENT_STATUS_LABELS[status] ?? { label: status || 'inconnu', tone: 'default' as const };
              const reason = str(h, 'reason');
              return (
                <li key={i}>
                  <span className="mono">Tour {num(h, 'turn') ?? i + 1}</span>
                  <strong title={reason ? `Pourquoi : ${plainText(reason)}` : undefined}>{actionText(str(h, 'action'), str(h, 'title'))}</strong>
                  <Badge tone={st.tone}>{st.label}</Badge>
                  {str(h, 'summary') && <p>{plainText(str(h, 'summary'))}</p>}
                </li>
              );
            })}
          </ol>
        </details>
      )}
    </>
  );
}
