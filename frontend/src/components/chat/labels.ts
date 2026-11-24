// French labels used by the chat components (complements lib/format.ts, which stays untouched).
import type { ReportReason } from '../../lib/types';

export const METHOD_LABELS: Record<string, string> = {
  explicit: 'explicite', rule: 'règle', semantic: 'similarité', llm: 'classifieur IA', default: 'par défaut',
  clarification: 'précision demandée',
};

export const ACTION_LABELS: Record<string, string> = {
  'cloud.send': 'Envoi vers Claude (cloud)',
  'files.quarantine': 'Mise en quarantaine de fichiers',
  'process.terminate': "Arrêt d'un processus",
  'web.fetch': 'Accès à Internet',
  'shell.run': 'Commande de terminal',
  'pc.control': 'Geste de la souris ou du clavier',
  'app.launch': 'Ouverture d’une application autorisée',
  'youtube.comment': 'Publication d’un commentaire YouTube',
  'reminder.set': 'Rappel ou minuteur noté dans AURORA',
  'files.scan': 'Analyse de fichiers',
  'system.read': 'Lecture de l’état du système',
  'memory.write': 'Écriture en mémoire',
};

export const RISK_LABELS: Record<string, { label: string; tone: 'green' | 'amber' | 'red' }> = {
  low: { label: 'Risque faible', tone: 'green' },
  medium: { label: 'Risque moyen', tone: 'amber' },
  high: { label: 'Risque élevé', tone: 'red' },
};

export const REDACTION_LABELS: Record<string, string> = {
  email: 'Adresses e-mail', phone: 'Numéros de téléphone', iban: 'IBAN', card: 'Numéros de carte',
  credit_card: 'Numéros de carte', ip: 'Adresses IP', path: 'Chemins utilisateur', user_path: 'Chemins utilisateur',
  api_key: 'Clés API', key: 'Clés API', token: 'Jetons', secret: 'Secrets', name: 'Noms', address: 'Adresses',
  password: 'Mots de passe', nir: 'Numéros de sécurité sociale',
};

export const STATUS_LABELS: Record<string, string> = {
  done: 'Terminé', error: 'Erreur', waiting_permission: 'Autorisation requise', cancelled: 'Annulé', running: 'En cours',
  interrupted: 'Interrompu', paused: 'En pause', pending: 'En attente',
};

export const TIER_LABELS: Record<string, string> = { rapide: 'Rapide (routage)', standard: 'Standard', expert: 'Expert (sur demande)' };

export const SOURCE_LABELS: Record<string, string> = { text: 'Texte', voice: 'Voix', panel: 'Bouton', api: 'API' };

/** Origin badge of an answer (Athéna's registers, docs/JOURNAL_ORIGINE.md): one meaning per tone. */
export const ORIGIN_LABELS: Record<string, { label: string; tone: 'green' | 'cyan' | 'violet' | 'amber'; title: string }> = {
  mesure: { label: 'Mesuré', tone: 'green', title: 'Lu ou calculé sur ce PC par un outil, à l’instant : rien n’a été inventé.' },
  source: { label: 'Source', tone: 'cyan', title: 'Des documents ont été consultés avant de répondre : les liens sont affichés.' },
  memoire: { label: 'Mémoire', tone: 'violet', title: 'Tiré de la mémoire d’AURORA.' },
  modele: { label: 'Modèle seul', tone: 'amber', title: 'L’IA a répondu de mémoire, sans outil ni source : c’est ici qu’elle peut se tromper. Vérifiez les faits importants.' },
};
export const WRITER_LABELS: Record<string, string> = { local: 'IA locale', cloud: 'Claude' };

/** A4: the answer checks as « mods », same keys, names and order as backend core/verify.py MODS
 *  (tests/test_verify_mods.py checks it). */
export const VERIFY_MOD_LABELS: Record<string, { name: string; description: string }> = {
  provenance: { name: 'Chaque résultat a une origine', description: 'Tout résultat d’outil dit d’où il vient (psutil, SearXNG, horloge…).' },
  mesures: { name: 'Les chiffres viennent des mesures', description: 'Un chiffre cité par l’IA (CPU, RAM, disque…) existe dans les données mesurées.' },
  capacites: { name: 'Pas de fausse affirmation sur AURORA', description: 'L’IA ne prétend pas avoir cherché en ligne sans recherche, ne nie pas une recherche faite et ne dit pas être privée d’Internet quand elle y a accès.' },
  sources: { name: 'Chaque référence a sa source', description: 'Une référence [n] de la réponse renvoie à une source réellement fournie.' },
  consentement: { name: 'Rien vers Claude sans accord', description: 'Chaque envoi à Claude respecte le mode et l’accord donné.' },
  actions: { name: 'Pas d’action annoncée sans l’avoir faite', description: 'L’IA n’annonce pas avoir supprimé, arrêté, noté ou programmé ce qu’aucune étape n’a fait.' },
};

/** P1 « Agent en plusieurs étapes »: the intent sent by the composer's « Agent » switch. */
export const AGENT_INTENT = 'agent.run';
/** The service's turn ceiling is a constant (6) with no setting yet: only this help text repeats it. */
export const AGENT_HELP = 'L’agent enchaîne les outils autorisés, tour après tour (6 au plus). Les actions sensibles et chaque envoi à Claude gardent leur carte d’accord.';
export const AGENT_STATUS_LABELS: Record<string, { label: string; tone: 'default' | 'green' | 'amber' | 'red' | 'cyan' }> = {
  answer: { label: 'réponse', tone: 'cyan' },
  done: { label: 'fait', tone: 'green' },
  error: { label: 'erreur', tone: 'red' },
  skipped: { label: 'ignoré', tone: 'default' },
  refused: { label: 'refusé par AURORA', tone: 'red' },
  waiting: { label: 'en attente d’accord', tone: 'amber' },
};
/** Why the agent ended without concluding (RunResult.agent.stopped). */
export const agentStopLabel = (stopped: string, maxTurns: number): string =>
  stopped === 'max_turns' ? `Arrêté au plafond de ${maxTurns > 0 ? `${maxTurns} ` : ''}tours sans conclure`
    : stopped === 'no_model' ? 'Aucune IA disponible pour l’agent'
    : stopped === 'invalid' ? 'L’agent n’a pas produit de choix valide'
    : `Agent arrêté (${stopped})`;

/** « Signaler » (H3): reasons, in the order of the form (backend tasks/reports.py REASONS). */
export const REPORT_REASON_LABELS: Record<ReportReason, string> = {
  misunderstood: 'Mauvaise compréhension de la demande',
  wrong_answer: 'Réponse fausse',
  off_topic: 'Hors sujet',
  other: 'Autre',
};
