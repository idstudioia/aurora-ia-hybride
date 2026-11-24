// Accent-insensitive highlighting of query terms in plain text, rendered with React elements
// (external or stored text is never injected as HTML).
import type { ReactNode } from 'react';

/** Sans accents ni casse (« Zélande » → « zelande ») ; aussi utilisé par le globe hologramme (trouverLieu). */
export const fold = (c: string) => c.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();

export function highlightRanges(text: string, query: string): [number, number][] {
  const terms = query.split(/\s+/).map(t => [...t].map(fold).join('')).filter(t => t.length >= 2);
  if (!terms.length || !text) return [];
  // Folded copy with a map back to original indices.
  let folded = '';
  const back: number[] = [];
  [...text].forEach((ch, i) => {
    const f = fold(ch);
    for (let j = 0; j < f.length; j++) { folded += f[j]; back.push(i); }
  });
  const chars = [...text];
  const offsets: number[] = [];
  let acc = 0;
  for (const ch of chars) { offsets.push(acc); acc += ch.length; }
  offsets.push(acc);
  const ranges: [number, number][] = [];
  for (const term of terms) {
    let from = 0;
    for (;;) {
      const at = folded.indexOf(term, from);
      if (at < 0) break;
      const startChar = back[at];
      const endChar = back[at + term.length - 1] + 1;
      ranges.push([offsets[startChar], offsets[endChar]]);
      from = at + term.length;
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const r of ranges) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([r[0], r[1]]);
  }
  return merged;
}

export default function Highlight({ text, query }: { text: string; query: string }) {
  const ranges = highlightRanges(text, query);
  if (!ranges.length) return <>{text}</>;
  const parts: ReactNode[] = [];
  let pos = 0;
  ranges.forEach(([a, b], i) => {
    if (a > pos) parts.push(text.slice(pos, a));
    parts.push(<mark key={i} className="mem-mark">{text.slice(a, b)}</mark>);
    pos = b;
  });
  if (pos < text.length) parts.push(text.slice(pos));
  return <>{parts}</>;
}
