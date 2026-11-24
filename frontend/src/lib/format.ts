// Small French formatting helpers shared by every page.
import type { PageId, RoutineAuto } from './types';

export const fmtTime = (iso: string | null | undefined, seconds = true) =>
  iso ? new Date(iso).toLocaleTimeString('fr-FR', seconds ? { hour: '2-digit', minute: '2-digit', second: '2-digit' } : { hour: '2-digit', minute: '2-digit' }) : '—';

export const fmtDate = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';

export const fmtDateTime = (iso: string | null | undefined) =>
  iso ? `${fmtDate(iso)} · ${fmtTime(iso)}` : '—';

/** Fixed decimals with the French decimal comma (« 4,20 »). */
export const fixedFr = (v: number, digits: number) => v.toFixed(digits).replace('.', ',');

export const fmtMs = (ms: number | null | undefined) => {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—';
  if (ms < 1) return '< 1 ms';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${fixedFr(ms / 1000, ms < 10_000 ? 2 : 1)} s`;
  return `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1000)} s`;
};

export const fmtUsd = (usd: number | null | undefined) =>
  usd === null || usd === undefined || Number.isNaN(usd) ? '—' : usd === 0 ? '0 $' : usd < 0.01 ? `${fixedFr(usd * 100, 3)} ¢` : `${fixedFr(usd, usd < 1 ? 3 : 2)} $`;

export const fmtPct = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${fixedFr(v, digits)} %`;

/** Short token count, at most one decimal and no trailing « ,0 »: « 850 », « 1,2k », « 338,5k », « 1M ». */
export const fmtTokens = (n: number | null | undefined) => {
  if (n === null || n === undefined || !Number.isFinite(n) || n < 0) return '—';
  const one = (v: number) => String(Math.round(v * 10) / 10).replace('.', ',');
  if (Math.round(n) < 1000) return String(Math.round(n));
  const k = Math.round(n / 100) / 10;   // 999 960 rounds to 1000k: shown as « 1M »
  return k < 1000 ? `${one(k)}k` : `${one(n / 1_000_000)}M`;
};

export const relativeTime = (iso: string | null | undefined) => {
  if (!iso) return '—';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 45) return "à l'instant";
  if (diff < 3600) return `il y a ${Math.round(diff / 60)} min`;
  if (diff < 86400) return `il y a ${Math.round(diff / 3600)} h`;
  return `il y a ${Math.round(diff / 86400)} j`;
};

/** D2: the days of a routine's schedule as the service numbers them (0 = lundi … 6 = dimanche). */
export const WEEK_DAYS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'];

/** D2: the schedule of a routine in words — « tous les jours à 08:00 », « du lundi au vendredi à 07:30 » — or, for
 *  a routine launched on an event, « au démarrage d’AURORA, tous les jours » (`events`: the service's words for
 *  each event, `Routine.events`). */
export const scheduleText = (schedule: { time?: string; event?: string; days: number[] }, events?: Record<string, string>) => {
  const days = schedule.days.length === 7 ? 'tous les jours'
    : schedule.days.join() === '0,1,2,3,4' ? 'du lundi au vendredi' : `le ${schedule.days.map(d => WEEK_DAYS[d]).join(', ')}`;
  return schedule.event ? `${events?.[schedule.event] ?? 'sur un événement'}, ${days}` : `${days} à ${schedule.time}`;
};

/** D2: a slot written by the service, « 2026-11-17 08:00 » in the PC's local time → « mardi 17 novembre à 08:00 ». */
export const slotText = (slot: string) => {
  const [day, hour] = slot.split(' ');
  const date = new Date(`${day}T${hour}:00`);
  return Number.isNaN(date.getTime()) ? slot
    : `${date.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })} à ${hour}`;
};

/** D2: the last launch of a routine by its schedule, in words — only what the service counted
 *  (tasks/routines.run_scheduled): « lundi 16 novembre à 08:00 : 3 étape(s), 2 faite(s), 1 en attente de votre accord. » */
export function autoText(auto: RoutineAuto): string {
  const when = `${slotText(auto.slot)}${auto.late_min > 0 ? ` (partie avec ${auto.late_min} min de retard)` : ''}`;
  if (auto.error) return `${when} : ${auto.error}`;
  const left = auto.steps - auto.done - auto.waiting - auto.failed;
  const counts = [`${auto.done} faite(s)`, auto.waiting ? `${auto.waiting} en attente de votre accord` : '',
    auto.failed ? `${auto.failed} en échec` : '', auto.over && left > 0 ? `${left} non lancée(s)` : ''].filter(Boolean).join(', ');
  const state = !auto.over ? 'en cours — ' : auto.interrupted ? 'interrompue par l’arrêt d’AURORA — ' : '';
  return `${when} : ${state}${auto.steps} étape(s), ${counts}.`;
}

export const INTENT_LABELS: Record<string, string> = {
  'system.status': 'Mesure système', 'system.processes': 'Processus', 'system.diagnose': 'Diagnostic PC',
  'system.kill_process': 'Arrêt de processus', 'memory.search': 'Recherche mémoire', 'memory.save': 'Mémorisation',
  'web.search': 'Recherche web', 'news.latest': 'Actualités', 'weather.current': 'Météo', 'quakes.recent': 'Séismes',
  'calc.evaluate': 'Calcul', 'calc.convert': 'Conversion', 'time.now': 'Heure', 'sim.orbit': 'Simulation orbitale',
  'sim.solar_system': 'Système solaire', 'files.scan': 'Analyse de fichiers', 'files.cleanup': 'Nettoyage de fichiers',
  'files.restore': 'Restauration de fichiers', 'routine.run': 'Routine', 'reminder.set': 'Rappel ou minuteur',
  'doc.analyze': 'Analyse de document', 'task.plan': 'Planification', 'ui.navigate': 'Navigation',
  'meta.capabilities': 'Capacités', 'chat.general': 'Conversation', 'agent.run': 'Agent en plusieurs étapes',
  'data.analyze': 'Analyse de données',
};

/** Which page shows a tool's subject: the module lit on the Home page (pageForEvent) and, since 08/11/2026, the
 *  tab AURORA opens by itself (backend core/compose.py AUTO_PAGE_TOOLS says the same for the answers). */
export function pageForTool(tool: string): PageId {
  if (/^(system|process|files)\./.test(tool)) return 'computer';
  if (/^(weather|quakes|events|sky)\./.test(tool) || tool === 'news.world') return 'world';
  if (/^(web|news)\./.test(tool)) return 'search';
  if (tool.startsWith('sim.')) return 'simulations';
  if (tool.startsWith('studio.')) return 'studio';
  return 'chat';
}

/** Tools that look something up on the network: the globe shows its search animation while one of them runs. */
export const isSearchTool = (tool: string): boolean => /^(web|news|weather|quakes|events|sky)\./.test(tool);

export const ENGINE_LABELS: Record<string, string> = {
  tool: 'Outil local', 'tool+local': 'Outil + IA locale', 'tool+cloud': 'Outil + Claude',
  local: 'IA locale (Ollama)', cloud: 'Claude (cloud)', none: 'Aucun moteur',
};

export const STAGE_LABELS: Record<string, string> = {
  input: 'Entrée', decision: 'Décision', tool: 'Outils', local_ai: 'IA locale', cloud_ai: 'Claude',
  permission: 'Autorisation', verify: 'Vérification', response: 'Réponse', memory: 'Mémoire', system: 'Système',
};

export const MODE_LABELS: Record<string, string> = { prive: 'Privé', equilibre: 'Équilibré', avance: 'Avancé' };

/** P2 « Pages »: the kind of report a page is (ArtefactKind). */
export const ARTEFACT_KIND_LABELS: Record<string, string> = {
  diagnostic: 'Diagnostic du PC', recherche: 'Recherche web', routine: 'Bilan de routine', banc: 'Banc de routage', reponse: 'Réponse',
  analyse: 'Analyse de données',
};
