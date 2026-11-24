// Carte des outils de la page Tâches (11/11/2026) : ce qui relie la réponse de GET /api/tools/map au dessin
// (BrainGraph layout="tools") et aux textes de la fiche. Fonctions pures, sans React, testées hors navigateur
// (tests/carteOutils.test.mjs). Rien n'est inventé ici : une valeur que le service n'a pas est dite absente.
import type { GraphTool, GraphToolGroup, MemoryGraphData, ToolMapData, ToolMapFamily, ToolMapTool, ToolState } from '../../lib/types';
import { fmtNum } from '../chat/text';

/** Le mot de chaque état, toujours écrit à côté du signe et de la couleur. 'absent' se lit « Indisponible ». */
export const TOOL_STATE_LABELS: Record<ToolState, string> = {
  available: 'Disponible', disabled: 'Désactivé', absent: 'Indisponible',
};
export const TOOL_STATES: ToolState[] = ['available', 'disabled', 'absent'];
/** Un état que cette interface ne connaît pas (service plus récent) n'est jamais montré comme « Disponible ». */
export const toolStateLabel = (state: string | null | undefined): string =>
  TOOL_STATE_LABELS[state as ToolState] ?? 'État inconnu';

/** Le service n'a pas de phrase vérifiée pour cet outil : on le dit, on n'en invente pas. */
export const NO_EXAMPLE = "Pas de phrase d'exemple vérifiée pour cet outil";
/** Ce que le nombre compte : une demande qui a exécuté l'outil (voir la note du service, ToolMapData.usage.note). */
export const USES_LABEL = 'Utilisé dans une demande';
export const NO_COUNTER = 'compteur indisponible';

/** « 12 fois », « jamais », ou « compteur indisponible » quand le service n'a pas de compteur : jamais 0 à sa place. */
export function usesText(uses: number | null | undefined): string {
  if (uses === null || uses === undefined || !Number.isFinite(uses)) return NO_COUNTER;
  return uses <= 0 ? 'jamais' : `${fmtNum(uses, 0)} fois`;
}

/** Le compte d'un outil du dessin : `counted: false` veut dire « pas de compteur », quel que soit `uses`. */
export const graphUsesText = (tool: Pick<GraphTool, 'uses' | 'counted'>): string =>
  usesText(tool.counted === false ? null : tool.uses);

/** L'accord demandé à l'utilisateur. null : le mode interdit l'action, aucun accord ne la débloque. */
export function permissionText(permission: boolean | null | undefined): string {
  if (permission === true) return 'demandé avant d’agir';
  if (permission === false) return 'pas demandé';
  if (permission === null) return 'sans effet : ce mode interdit l’action';
  return 'inconnu';
}

/** Internet au moment de la mesure. null : non vérifié (mode privé). */
export function internetText(internet: boolean | null | undefined): string {
  return internet === true ? 'répond' : internet === false ? 'ne répond pas' : 'non vérifié';
}

/** Ce que BrainGraph dessine : les 4 champs de GraphTool, plus la famille, l'état et « compteur connu ou non ». */
export function toolMapGraph(map: ToolMapData): MemoryGraphData {
  const builtins = new Map<string, number>();
  for (const f of map.families) if (f.builtin) builtins.set(f.group, (builtins.get(f.group) ?? 0) + 1);
  const tools: GraphTool[] = map.tools.map(t => ({
    name: t.name, title: t.title || t.name, uses: typeof t.uses === 'number' && Number.isFinite(t.uses) ? Math.max(0, t.uses) : 0,
    last_used_at: t.last_used_at ?? null, group: t.group, family: t.family, state: t.state, reason: t.reason ?? null,
    counted: typeof t.uses === 'number' && Number.isFinite(t.uses),
  }));
  const tool_groups: GraphToolGroup[] = map.groups.map(g => ({ id: g.id, title: g.title, builtins: builtins.get(g.id) ?? 0 }));
  return { nodes: [], links: [], groups: [], routines: [], tools, tool_groups };
}

/** Une grande famille de la liste : ses outils, puis ses capacités intégrées (sans outil, sans état). */
export interface ToolListGroup {
  id: string;
  title: string;
  description: string;
  tools: ToolMapTool[];
  builtins: ToolMapFamily[];
}

/** Titre de secours de la grande famille des outils que le service n'a rangés nulle part. */
export const OTHER_TOOLS_TITLE = 'Autres outils';

/**
 * La liste à côté de la carte : chaque outil sous sa grande famille (familles dans l'ordre du service, puis par nom),
 * puis les capacités intégrées de cette grande famille. Un outil dont la grande famille n'est pas annoncée est rangé
 * sous « Autres outils » : montré, jamais perdu.
 */
export function toolList(map: ToolMapData): ToolListGroup[] {
  const familyRank = new Map(map.families.map((f, i) => [f.id, i]));
  const rank = (t: ToolMapTool) => familyRank.get(t.family) ?? map.families.length;
  const out: ToolListGroup[] = map.groups.map(g => ({ id: g.id, title: g.title, description: g.description, tools: [], builtins: [] }));
  const byId = new Map(out.map(g => [g.id, g]));
  let other: ToolListGroup | null = null;
  const lost = (): ToolListGroup => {
    if (!other) {
      other = { id: '(autres)', title: OTHER_TOOLS_TITLE, description: 'des outils que le service n’a rangés dans aucune grande famille', tools: [], builtins: [] };
      out.push(other);
    }
    return other;
  };
  for (const t of map.tools) (byId.get(t.group) ?? lost()).tools.push(t);
  for (const f of map.families) if (f.builtin) (byId.get(f.group) ?? lost()).builtins.push(f);
  for (const g of out) g.tools.sort((a, b) => rank(a) - rank(b) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  return out;
}

/** La famille d'un outil (son titre et sa phrase), ou null si le service ne l'a pas listée. */
export const familyOf = (map: ToolMapData, id: string): ToolMapFamily | null => map.families.find(f => f.id === id) ?? null;

/** Combien d'outils dans chaque état (légende de la carte). */
export function stateCounts(map: ToolMapData): Record<ToolState, number> {
  const counts: Record<ToolState, number> = { available: 0, disabled: 0, absent: 0 };
  for (const t of map.tools) if (t.state in counts) counts[t.state]++;
  return counts;
}

/** La réponse a-t-elle la forme attendue ? Sinon l'interface dit « indisponible » au lieu de dessiner une carte vide. */
export function isToolMap(value: unknown): value is ToolMapData {
  const v = value as Partial<ToolMapData> | null;
  return !!v && typeof v === 'object' && Array.isArray(v.tools) && Array.isArray(v.groups) && Array.isArray(v.families);
}
