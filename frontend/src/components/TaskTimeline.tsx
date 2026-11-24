// ASTRA starter component, kept and extended for AURORA: same props, labels for every event
// kind of CONTRACT §3 (plus the legacy ASTRA kinds), and its own stylesheet so any page can use it.
import type { ActivityEvent } from '../lib/types';
import './flow/task-timeline.css';

const labels: Record<string, string> = {
  // ASTRA legacy kinds (imported rows)
  pending: 'Créée', running: 'En cours', done: 'Terminée', error: 'Erreur',
  saved: 'Enregistrée', deleted: 'Supprimée',
  // manual tasks (starter compatibility)
  'task.pending': 'Créée', 'task.running': 'En cours', 'task.done': 'Terminée', 'task.error': 'Erreur',
  // runs
  'run.created': 'Requête reçue', 'route.decided': 'Décision prise', 'plan.created': 'Plan établi',
  'step.started': 'Étape démarrée', 'tool.result': "Résultat d'outil", 'model.result': 'Réponse du modèle',
  'step.done': 'Étape terminée', 'step.error': "Erreur d'étape", 'step.skipped': 'Étape ignorée',
  'permission.requested': 'Autorisation demandée', 'permission.granted': 'Autorisation accordée',
  'permission.denied': 'Autorisation refusée', 'permission.revoked': 'Autorisation révoquée',
  'verify.passed': 'Vérification réussie', 'verify.failed': 'Vérification échouée',
  'memory.recalled': 'Souvenirs rappelés', 'memory.saved': 'Mémoire enregistrée', 'memory.updated': 'Mémoire mise à jour',
  'memory.deleted': 'Mémoire supprimée', 'memory.linked': 'Mémoires liées',
  'response.ready': 'Réponse prête', 'agent.todo': 'Tour de l’agent',
  'run.done': 'Terminée', 'run.error': 'Erreur', 'run.cancelled': 'Annulée', 'run.interrupted': 'Interrompue', 'run.resumed': 'Reprise',
  'system.imported': 'Import ASTRA', 'system.started': 'Démarrage',
};

const subjects: Record<ActivityEvent['subject_type'], string> = {
  memory: 'Mémoire', task: 'Tâche', step: 'Étape', conversation: 'Conversation', system: 'Système',
  permission: 'Autorisation', provider: 'Fournisseur',
};

/** Colour role of an event bullet (data-tone), from its level and kind. */
const toneOf = (event: ActivityEvent): string => {
  const k = event.kind;
  if (event.level === 'error' || k.endsWith('error') || k.endsWith('failed') || k.endsWith('denied')) return 'red';
  if (event.level === 'warn' || k === 'permission.requested' || k.endsWith('interrupted') || k.endsWith('cancelled')) return 'amber';
  if (k.endsWith('done') || k.endsWith('passed') || k.endsWith('granted') || k === 'response.ready' || k.endsWith('saved')) return 'green';
  if (k.startsWith('memory.') || k === 'saved' || k === 'deleted') return 'violet';
  return 'cyan';
};

interface Props {
  events: ActivityEvent[];
  activeId: number | null;
  onPick: (id: number) => void;
}

export default function TaskTimeline({ events, activeId, onPick }: Props) {
  return (
    <div className="timeline tasks-tl" aria-label="Historique réel des événements">
      {events.length === 0 ? <p className="muted tasks-tl-empty">Aucun événement. Créez une tâche ou posez une question pour commencer.</p> : events.map(event => (
        <button key={event.id} type="button" onClick={() => onPick(event.id)} className={`timeline-row tasks-tl-row ${activeId === event.id ? 'selected' : ''}`} aria-pressed={activeId === event.id}>
          <span className={`event-bullet tasks-tl-bullet`} data-tone={toneOf(event)} data-kind={event.kind} />
          <span className="timeline-copy tasks-tl-copy">
            <strong>{event.label}</strong>
            <small>{subjects[event.subject_type] ?? 'Tâche'} · {labels[event.kind] || event.kind}{event.run_id ? ` · n° ${event.run_id}` : ''}</small>
          </span>
          <time className="tasks-tl-time" dateTime={event.created_at}>{new Date(event.created_at).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}</time>
        </button>
      ))}
    </div>
  );
}
