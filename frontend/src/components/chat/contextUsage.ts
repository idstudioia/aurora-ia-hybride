// « Contexte » frame of the Chat page (ContextCard.tsx): which context to show and the figures it shows.
// Pure functions, no React and no clock (the caller passes « now »). The total is measured by the provider;
// the split by part is the service's estimate, pro rata of each part's length, and the frame says so.
import type { ChatEntry } from '../../lib/store';
import type { ContextPart, ContextUsage } from '../../lib/types';
import { fixedFr, fmtDate, fmtPct, fmtTime } from '../../lib/format';
import { isObj, num } from '../../lib/tolerant';

export type PartKey = ContextPart['key'];
/** Order of the bar and of the list; « other » only appears if the parts do not add up to the measured total. */
export const ROW_ORDER = ['system', 'tools', 'memory', 'conversation', 'results', 'question', 'other'] as const;
export type RowKey = (typeof ROW_ORDER)[number] | 'free';

const PART_KEYS: readonly string[] = ROW_ORDER.slice(0, 6);
/** Used only when the service sent no label (it sends the French labels itself). */
const FALLBACK_LABELS: Record<PartKey, string> = {
  system: 'Consigne et règles', tools: 'Liste des outils', memory: 'Souvenirs',
  conversation: 'Conversation récente', results: 'Résultats des outils', question: 'Votre demande',
};
export const PROVIDER_LABELS: Record<ContextUsage['provider'], string> = { ollama: 'IA locale', anthropic: 'Claude' };
export const WINDOW_SOURCE_LABELS: Record<NonNullable<ContextUsage['window_source']>, string> = {
  ollama_ps: 'Taille communiquée par Ollama (/api/ps)',
  anthropic_models: "Taille communiquée par l'API des modèles d'Anthropic",
};

/** lib/tolerant num(), kept only when ≥ 0 (a count of characters or tokens). */
const nonNeg = (v: unknown): number | null => { const n = num(v); return n !== null && n >= 0 ? n : null; };

/** A ContextUsage read from a RunResult or a stored message meta; null when absent or without a measured total. */
export function asContextUsage(v: unknown): ContextUsage | null {
  if (!isObj(v) || (v.provider !== 'ollama' && v.provider !== 'anthropic')) return null;
  const input = nonNeg(v.input_tokens);
  if (!input) return null;
  const parts: ContextPart[] = [];
  for (const p of Array.isArray(v.parts) ? v.parts : []) {
    if (!isObj(p) || typeof p.key !== 'string' || !PART_KEYS.includes(p.key)) continue;
    const key = p.key as PartKey;
    const label = typeof p.label === 'string' && p.label.trim() ? p.label.trim() : FALLBACK_LABELS[key];
    parts.push({ key, label, chars: nonNeg(p.chars) ?? 0, tokens: nonNeg(p.tokens) ?? 0 });
  }
  const window = nonNeg(v.window);
  return {
    provider: v.provider, model: typeof v.model === 'string' ? v.model.trim() : '', input_tokens: input,
    window: window ? window : null,
    window_source: v.window_source === 'ollama_ps' || v.window_source === 'anthropic_models' ? v.window_source : null,
    parts, measured_at: typeof v.measured_at === 'string' ? v.measured_at : '',
  };
}

/** Context carried by one answer: its RunResult (answer of this session) or its stored meta (reopened conversation).
 *  null = the answer says no AI answered (RunResult.context === null); undefined = it says nothing. */
function entryContext(e: ChatEntry): ContextUsage | null | undefined {
  if (e.run) return e.run.context === null ? null : asContextUsage(e.run.context) ?? undefined;
  if (!e.meta_json) return undefined;
  try {
    const meta: unknown = JSON.parse(e.meta_json);
    return (isObj(meta) && asContextUsage(meta.context)) || undefined;
  } catch { return undefined; }
}

export interface KnownContext {
  usage: ContextUsage;
  /** Read from a stored message (conversation reopened), not from an answer received in this session. */
  fromHistory: boolean;
  /** A more recent answer of the conversation had no AI (measure, calculation…): the frame keeps this older one. */
  newerWithoutAi: boolean;
}

/** Last known context of the active conversation (newest answer first). */
export function latestContext(chat: readonly ChatEntry[]): KnownContext | null {
  let newerWithoutAi = false;
  for (let i = chat.length - 1; i >= 0; i--) {
    const e = chat[i];
    if (e.role !== 'assistant' || e.pending) continue;
    const usage = entryContext(e);
    if (usage) return { usage, fromHistory: !e.run, newerWithoutAi };
    if (usage === null) newerWithoutAi = true;
  }
  return null;
}

export interface ContextRow { key: RowKey; label: string; tokens: number; share: number; chars: number | null }
export interface ContextView {
  total: number;
  window: number | null;
  /** Measured total / window; null when the window is unknown. */
  used: number | null;
  /** One row per part (share of the window, or of the measured total when the window is unknown), then « Place libre ». */
  rows: ContextRow[];
  /** Width of each bar segment, as a fraction of the bar (they add up to at most 1). */
  segments: Partial<Record<RowKey, number>>;
}

export function contextView(u: ContextUsage): ContextView {
  const ref = u.window ?? u.input_tokens;
  const rows: ContextRow[] = [...u.parts]
    .filter(p => p.tokens > 0 || p.chars > 0)
    .sort((a, b) => ROW_ORDER.indexOf(a.key) - ROW_ORDER.indexOf(b.key))
    .map(p => ({ key: p.key, label: p.label, tokens: p.tokens, share: p.tokens / ref, chars: p.chars }));
  const given = rows.reduce((s, r) => s + r.tokens, 0);
  if (u.input_tokens - given > 0) {
    rows.push({ key: 'other', label: 'Non réparti', tokens: u.input_tokens - given, share: (u.input_tokens - given) / ref, chars: null });
  }
  const sum = Math.max(given, u.input_tokens);
  const scale = sum > ref ? ref / sum : 1;   // never wider than the bar, even if the provider counted past the window
  const segments: ContextView['segments'] = {};
  rows.forEach(r => { segments[r.key] = (r.tokens / ref) * scale; });
  if (u.window !== null) {
    const free = Math.max(0, u.window - u.input_tokens);
    rows.push({ key: 'free', label: 'Place libre', tokens: free, share: free / u.window, chars: null });
  }
  return { total: u.input_tokens, window: u.window, used: u.window !== null ? u.input_tokens / u.window : null, rows, segments };
}

/** Percentage of a fraction with `digits` decimals (lib/format fmtPct); a non-zero share too small to show reads « < 0,1 % ». */
export function fmtShare(f: number | null, digits = 1): string {
  if (f === null || !Number.isFinite(f)) return '—';
  const pct = Number((f * 100).toPrecision(12));   // 0,6615 × 100 = 66,149999… : read as 66,15, shown « 66,2 % »
  const min = 10 ** -digits;
  return pct > 0 && pct < min / 2 ? `< ${fixedFr(min, digits)} %` : fmtPct(pct, digits);
}

/** Headline share: « 34 % », with one decimal under 10 % (« 3,4 % »). */
export const fmtUsed = (f: number | null) => fmtShare(f, f !== null && f * 100 < 9.95 ? 1 : 0);

/** « à 14:05 » for a measure of today, « le 28 oct. 2026 à 14:05 » otherwise; '' when the time is unknown. */
export function fmtMeasuredAt(iso: string, now: Date): string {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return '';
  const hm = fmtTime(iso, false);
  return d.toDateString() === now.toDateString() ? `à ${hm}` : `le ${fmtDate(iso)} à ${hm}`;
}
