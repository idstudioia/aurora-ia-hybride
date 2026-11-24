// Text helpers for the chat / search UI. External content is always rendered as plain text:
// these helpers strip markup instead of interpreting it.
import { isObj, pickArr, pickNum, pickObj, pickStr, type Json } from '../../lib/tolerant';

/** Remove HTML tags and decode the common entities. The result is meant for text nodes only. */
export function plainText(input: unknown): string {
  if (input === null || input === undefined) return '';
  const s = String(input)
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|li|h\d)>/gi, '\n')
    .replace(/<[^>]*>/g, '');
  return decodeEntities(s).replace(/[ \t ]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
}

const NAMED: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', laquo: '«', raquo: '»', eacute: 'é', egrave: 'è', agrave: 'à', ccedil: 'ç', hellip: '…', rsquo: '’', lsquo: '‘', ldquo: '“', rdquo: '”', ndash: '–', mdash: '—' };

export function decodeEntities(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, code: string) => {
    if (code[0] === '#') {
      const n = code[1] === 'x' || code[1] === 'X' ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
      return Number.isFinite(n) && n > 0 && n < 0x110000 ? String.fromCodePoint(n) : m;
    }
    return NAMED[code.toLowerCase()] ?? m;
  });
}

/** Only http(s) links are ever rendered as anchors. */
export function safeUrl(url: unknown): string | null {
  if (typeof url !== 'string') return null;
  const u = url.trim();
  if (!/^https?:\/\//i.test(u)) return null;
  try { return new URL(u).toString(); } catch { return null; }
}

export function domainOf(url: unknown): string {
  const u = safeUrl(url);
  if (!u) return '';
  try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return ''; }
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  if (n < 1024) return `${n} o`;
  const units = ['Ko', 'Mo', 'Go', 'To'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toLocaleString('fr-FR', { maximumFractionDigits: v < 10 ? 1 : 0 })} ${units[i]}`;
}

export function fmtNum(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return n.toLocaleString('fr-FR', { maximumFractionDigits: digits });
}

/** Normalised comparison key for user texts (whitespace collapsed). */
export function textKey(s: unknown): string {
  return typeof s === 'string' ? s.trim().replace(/\s+/g, ' ') : '';
}

// ---- tolerant accessors for tool data whose exact shape belongs to another module ----
// Thin variadic wrappers over the shared readers of lib/tolerant.ts (same semantics).
type Obj = Json;
export { isObj };

export function num(o: unknown, ...keys: string[]): number | null {
  return pickNum(o, keys);
}

/** First non-empty string (or finite number, as text) under one of the keys; '' if none. */
export function str(o: unknown, ...keys: string[]): string {
  if (!isObj(o)) return '';
  for (const k of keys) {
    const s = pickStr(o, [k]);
    if (s !== null) return s;
    const v = o[k];
    if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  }
  return '';
}

export function arr(o: unknown, ...keys: string[]): unknown[] {
  if (Array.isArray(o)) return o;
  return pickArr(o, keys) ?? [];
}

export function obj(o: unknown, ...keys: string[]): Obj | null {
  return pickObj(o, keys);
}

/** Unwrap a ToolResult-like object ({ok, data, summary}) to its data, keeping the rest reachable. */
export function unwrapToolData(d: unknown): Obj {
  if (isObj(d) && typeof d.ok === 'boolean' && isObj(d.data)) return { ...d.data, _summary: d.summary, _ok: d.ok, _error: d.error };
  return isObj(d) ? d : Array.isArray(d) ? { items: d } : {};
}

/** Strip markdown markers so text can be read aloud or shown in a one-line preview. */
export function markdownToPlain(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, ' (bloc de code) ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, 'lien')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+•]\s+/gm, '')
    .replace(/^\s*>\s?/gm, '')
    .replace(/\|/g, ' ')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/(\*|_)(.*?)\1/g, '$2')
    .replace(/[ \t]+/g, ' ')
    .trim();
}
