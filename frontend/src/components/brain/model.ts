// Brain view model: the four memory levels, colours, French labels and the mapping
// from real persisted events (docs/CONTRACT.md §3) to brain animations.
import type { ActivityEvent, GraphNode } from '../../lib/types';

export type LevelId = 'immediate' | 'persistent' | 'semantic' | 'actions';

export interface LevelMeta { id: LevelId; label: string; short: string; color: string; description: string }

// Descriptions are the user's own wording of the four memory levels.
export const LEVELS: LevelMeta[] = [
  { id: 'immediate', label: 'Mémoire immédiate', short: 'Immédiate', color: '#22c8ff',
    description: 'Contexte de la conversation active et étapes de la tâche en cours.' },
  { id: 'persistent', label: 'Mémoire persistante', short: 'Persistante', color: '#8a5cff',
    description: 'Projets confirmés, préférences, décisions importantes, documents référencés.' },
  { id: 'semantic', label: 'Mémoire sémantique', short: 'Sémantique', color: '#2ee6a8',
    description: 'Retrouver un souvenir même quand la question est reformulée (FTS5 + vecteurs locaux).' },
  { id: 'actions', label: 'Mémoire des actions', short: 'Actions', color: '#ffb547',
    description: 'Tâches réellement exécutées, résultats, erreurs, outils, autorisations.' },
];

export const LEVEL_BY_ID: Record<LevelId, LevelMeta> = Object.fromEntries(LEVELS.map(l => [l.id, l])) as Record<LevelId, LevelMeta>;
export const isLevelId = (v: unknown): v is LevelId => typeof v === 'string' && v in LEVEL_BY_ID;

export const CORE_COLOR = '#7fe8ff';
export const LINK_COLOR = '#7fdcff';
export const DANGER_COLOR = '#ff6b7a';
/** Rings layout: the ROUTINES ring (orange) and the TOOLS ring (blue hexagons). */
export const ROUTINE_COLOR = '#ff8a3d';
export const TOOL_COLOR = '#4d8dff';
/** Tool map (page Tâches): a tool switched off in Paramètres, and a tool that lacks something now. The colour is
 *  never the only sign: a bar or a cross is drawn in the hexagon, and the word is written in the list. */
export const TOOL_DISABLED_COLOR = '#ffb547';
export const TOOL_ABSENT_COLOR = '#8fa3bd';

export const RUN_STATUS_COLORS: Record<string, string> = {
  pending: '#8fb8ff', running: '#22c8ff', waiting_permission: '#ff9f5a', paused: '#ffb547',
  done: '#2ee6a8', error: '#ff6b7a', cancelled: '#7f9cc0', interrupted: '#ffb547',
};
export const RUN_STATUS_LABELS: Record<string, string> = {
  pending: 'En attente', running: 'En cours', waiting_permission: 'Autorisation requise', paused: 'En pause',
  done: 'Terminée', error: 'Erreur', cancelled: 'Annulée', interrupted: 'Interrompue',
};
export const runStatusColor = (status: string | undefined) => RUN_STATUS_COLORS[status ?? ''] ?? RUN_STATUS_COLORS.pending;

export const KIND_LABELS: Record<string, string> = {
  fact: 'Fait', project: 'Projet', preference: 'Préférence', decision: 'Décision',
  document: 'Document', note: 'Note', summary: 'Résumé',
};
export const CONFIDENTIALITY_LABELS: Record<string, string> = { public: 'Publique', private: 'Privée', sensitive: 'Sensible' };
export const VERIFICATION_LABELS: Record<string, string> = {
  verified: 'Vérifiée', unverified: 'Non vérifiée', user_confirmed: 'Confirmée par vous', contradicted: 'Contredite',
};
export const RELATION_LABELS: Record<string, string> = {
  related: 'Liée à', part_of: 'Fait partie de', contradicts: 'Contredit', updates: 'Met à jour', derived_from: 'Dérivée de',
};
export const NODE_TYPE_LABELS: Record<string, string> = {
  memory: 'Souvenir', conversation: 'Conversation', run: 'Exécution', core: 'Cœur AURORA', level: 'Niveau de mémoire',
  tool: 'Outil', routine: 'Routine',
};
export const MATCHED_BY_LABELS: Record<string, string> = {
  fts: 'Mots-clés (FTS5)', fts5: 'Mots-clés (FTS5)', keyword: 'Mots-clés', bm25: 'Mots-clés (BM25)',
  semantic: 'Sens (vecteurs)', vector: 'Sens (vecteurs)', embedding: 'Sens (vecteurs)', title: 'Titre', category: 'Catégorie',
};
export const sourceLabel = (source: string) => {
  if (!source) return '—';
  if (source === 'user') return 'Vous';
  if (source === 'conversation') return 'Conversation';
  if (source === 'web') return 'Web';
  if (source === 'astra-import') return 'Import ASTRA';
  if (source.startsWith('tool:')) return `Outil ${source.slice(5)}`;
  return source;
};

/** Trailing integer of a graph node id ("m:12" → 12). */
export function nodeNum(id: string): number | null {
  const m = /(\d+)$/.exec(id);
  return m ? Number(m[1]) : null;
}

/** Stable lookup key for a data node: type + numeric id (independent of the backend's id prefix). */
export const nodeKey = (type: string, num: number) => `${type}#${num}`;
export const memoryKey = (id: number) => nodeKey('memory', id);
export const runKey = (id: number) => nodeKey('run', id);

/** Memory level of a node; falls back on the node type when the backend omits it. */
export function nodeLevel(n: Pick<GraphNode, 'type' | 'level' | 'id'>): LevelId | null {
  if (isLevelId(n.level)) return n.level;
  if (n.type === 'level') {
    const tail = n.id.split(':').pop();
    return isLevelId(tail) ? tail : null;
  }
  if (n.type === 'conversation') return 'immediate';
  if (n.type === 'run') return 'actions';
  if (n.type === 'memory') return 'persistent';
  return null;
}

// ---------------------------------------------------------------- events → brain actions

export type BrainAction =
  | 'mem.saved' | 'mem.recalled' | 'mem.linked' | 'mem.updated' | 'mem.deleted'
  | 'run.created' | 'run.decided' | 'run.step' | 'run.stepdone' | 'run.steperror' | 'run.final'
  | 'tool.used';

export interface BrainEvent {
  action: BrainAction;
  memoryIds: number[];
  link: [number, number] | null;
  runId: number | null;
  status: string | null;   // run status implied by the event, if any
  tool: string | null;     // tool.result: payload.tool (registry name)
  ok: boolean | null;      // tool.result: payload.ok
  intent: string | null;   // route.decided: payload.intent ('routine.run'…)
  routineId: number | null; // route.decided: payload.slots.routine_id, when a routine phrase matched
}

const RUN_FINAL: Record<string, string> = {
  'run.done': 'done', 'run.error': 'error', 'run.cancelled': 'cancelled', 'run.interrupted': 'interrupted', 'run.resumed': 'running',
  'task.done': 'done', 'task.error': 'error',
};

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : typeof v === 'string' && /^\d+$/.test(v) ? Number(v) : null);

function memoryIdOf(e: ActivityEvent): number | null {
  return num(e.payload?.memory_id) ?? (e.subject_type === 'memory' && e.subject_id ? e.subject_id : null);
}

function runIdOf(e: ActivityEvent): number | null {
  return e.run_id ?? (e.subject_type === 'task' && e.subject_id ? e.subject_id : null);
}

/** Map a real event to a brain animation; null for events the brain does not show. */
export function classifyEvent(e: ActivityEvent): BrainEvent | null {
  const kind = e.kind;
  const base = {
    memoryIds: [] as number[], link: null as [number, number] | null, runId: null as number | null, status: null as string | null,
    tool: null as string | null, ok: null as boolean | null, intent: null as string | null, routineId: null as number | null,
  };
  const isMemorySubject = e.subject_type === 'memory';

  if (kind === 'memory.saved' || (isMemorySubject && kind === 'saved')) {
    const id = memoryIdOf(e);
    return { ...base, action: 'mem.saved', memoryIds: id !== null ? [id] : [] };
  }
  if (kind === 'memory.deleted' || (isMemorySubject && kind === 'deleted')) {
    const id = memoryIdOf(e);
    return { ...base, action: 'mem.deleted', memoryIds: id !== null ? [id] : [] };
  }
  if (kind === 'memory.updated') {
    const id = memoryIdOf(e);
    return { ...base, action: 'mem.updated', memoryIds: id !== null ? [id] : [] };
  }
  if (kind === 'memory.recalled') {
    const raw = Array.isArray(e.payload?.memory_ids) ? e.payload.memory_ids : [];
    const ids = raw.map(num).filter((v: number | null): v is number => v !== null);
    return { ...base, action: 'mem.recalled', memoryIds: ids, runId: e.run_id };
  }
  if (kind === 'memory.linked') {
    // Payload field for the other end is not fixed by the contract: accept the usual names.
    const p = e.payload ?? {};
    const a = num(p.source_id) ?? memoryIdOf(e);
    const candidates = [num(p.target_id), num(p.linked_id), num(p.other_id), num(p.to_id), num(p.memory_id)];
    const b = candidates.find(v => v !== null && v !== a) ?? null;
    return { ...base, action: 'mem.linked', memoryIds: [a, b].filter((v): v is number => v !== null), link: a !== null && b !== null ? [a, b] : null };
  }

  const runId = runIdOf(e);
  const isTaskSubject = e.subject_type === 'task';
  if (kind === 'run.created' || kind === 'task.pending' || (isTaskSubject && kind === 'pending')) {
    return { ...base, action: 'run.created', runId, status: 'pending' };
  }
  if (kind === 'route.decided' || kind === 'plan.created') {
    const p = e.payload ?? {};
    return {
      ...base, action: 'run.decided', runId,
      intent: kind === 'route.decided' && typeof p.intent === 'string' ? p.intent : null,
      routineId: kind === 'route.decided' ? num(p.slots?.routine_id) : null,
    };
  }
  // A real tool call of a task step (docs/CONTRACT.md: {step_id, tool, ok, …}, subject_type 'step').
  if (kind === 'tool.result') {
    const p = e.payload ?? {};
    return { ...base, action: 'tool.used', runId, tool: typeof p.tool === 'string' && p.tool ? p.tool : null, ok: p.ok !== false };
  }
  if (kind === 'step.started' || kind === 'task.running' || (isTaskSubject && kind === 'running')) {
    return { ...base, action: 'run.step', runId, status: 'running' };
  }
  if (kind === 'step.done' || kind === 'step.skipped') return { ...base, action: 'run.stepdone', runId };
  if (kind === 'step.error') return { ...base, action: 'run.steperror', runId };
  if (kind in RUN_FINAL || (isTaskSubject && (kind === 'done' || kind === 'error'))) {
    const fromPayload = typeof e.payload?.status === 'string' && e.payload.status in RUN_STATUS_COLORS ? e.payload.status as string : null;
    return { ...base, action: 'run.final', runId, status: fromPayload ?? RUN_FINAL[kind] ?? kind };
  }
  return null;
}

export const isBrainEvent = (e: ActivityEvent) => classifyEvent(e) !== null;

const EVENT_LABELS: Record<string, string> = {
  'memory.saved': 'Enregistrée', saved: 'Enregistrée', 'memory.recalled': 'Rappelée', 'memory.linked': 'Liée',
  'memory.updated': 'Modifiée', 'memory.deleted': 'Supprimée', deleted: 'Supprimée',
  'run.created': 'Demande reçue', 'task.pending': 'Créée', pending: 'Créée', 'route.decided': 'Route décidée',
  'plan.created': 'Plan établi', 'step.started': 'Étape démarrée', 'task.running': 'En cours', running: 'En cours',
  'step.done': 'Étape terminée', 'step.skipped': 'Étape ignorée', 'step.error': 'Étape en erreur', 'tool.result': "Résultat d'outil",
  'run.done': 'Terminée', 'task.done': 'Terminée', done: 'Terminée', 'run.error': 'Erreur', 'task.error': 'Erreur', error: 'Erreur',
  'run.cancelled': 'Annulée', 'run.interrupted': 'Interrompue', 'run.resumed': 'Reprise',
};

/** French label of an event kind (falls back on the raw kind). */
export const eventKindLabel = (kind: string) => EVENT_LABELS[kind] ?? kind;

/** Short French sentence describing an event, for the replay bar. */
export function describeEvent(e: ActivityEvent): string {
  const what = e.subject_type === 'memory' ? 'Mémoire' : 'Tâche';
  return `${what} · ${eventKindLabel(e.kind)} — ${e.label}`;
}

/** Deterministic pseudo-random in [0,1) from a string (stable initial layout). */
export function hash01(s: string, salt = 0): number {
  let h = 2166136261 ^ salt;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 100000) / 100000;
}
