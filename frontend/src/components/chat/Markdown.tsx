// Safe mini-markdown: builds React elements (never raw HTML). Supports paragraphs, headings,
// lists, quotes, simple tables, fenced code, inline code, bold, italic and http(s) links.
import { memo, useMemo, type ReactNode } from 'react';
import { safeUrl } from './text';

const INLINE = new RegExp([
  '(`[^`\\n]+`)',                                         // 1 inline code
  '\\[([^\\]\\n]+)\\]\\((https?:\\/\\/[^\\s)]+)\\)',     // 2,3 link
  '\\*\\*([^*\\n]+?)\\*\\*',                              // 4 bold
  '__([^_\\n]+?)__',                                      // 5 bold
  '(?<![\\w*])\\*(?!\\s)([^*\\n]+?)\\*(?![\\w*])',        // 6 italic
  '(?<![\\w_])_(?!\\s)([^_\\n]+?)_(?![\\w_])',            // 7 italic
  '(https?:\\/\\/[^\\s<>()\\[\\]]+[^\\s<>()\\[\\].,;:!?\'"»])', // 8 bare URL
].join('|'), 'g');

function Anchor({ href, children }: { href: string; children: ReactNode }) {
  const url = safeUrl(href);
  if (!url) return <>{children}</>;
  return <a href={url} target="_blank" rel="noopener noreferrer nofollow">{children}</a>;
}

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  const re = new RegExp(INLINE.source, 'g'); // own instance: inline() recurses
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${keyBase}-${i++}`;
    if (m[1]) out.push(<code key={k}>{m[1].slice(1, -1)}</code>);
    else if (m[2] && m[3]) out.push(<Anchor key={k} href={m[3]}>{inline(m[2], k)}</Anchor>);
    else if (m[4] || m[5]) out.push(<strong key={k}>{inline(m[4] ?? m[5], k)}</strong>);
    else if (m[6] || m[7]) out.push(<em key={k}>{inline(m[6] ?? m[7], k)}</em>);
    else if (m[8]) out.push(<Anchor key={k} href={m[8]}>{m[8]}</Anchor>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function withBreaks(lines: string[], keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  lines.forEach((line, i) => {
    if (i) out.push(<br key={`${keyBase}-br${i}`} />);
    out.push(...inline(line, `${keyBase}-l${i}`));
  });
  return out;
}

const LIST_RE = /^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const splitRow = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());

export function renderMarkdown(src: string): ReactNode[] {
  const lines = src.replace(/\r\n?/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let b = 0;
  const flush = () => {
    if (para.length) { const k = `p${b++}`; blocks.push(<p key={k}>{withBreaks(para, k)}</p>); para = []; }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/);
    if (fence) {
      flush();
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { code.push(lines[i]); i++; }
      blocks.push(<pre key={`c${b++}`} className="chat-md-code" data-lang={fence[1] || undefined}><code>{code.join('\n')}</code></pre>);
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    const h = line.match(/^\s{0,3}(#{1,4})\s+(.+?)\s*#*\s*$/);
    if (h) {
      flush();
      const k = `h${b++}`;
      const level = h[1].length;
      const content = inline(h[2], k);
      blocks.push(level <= 2 ? <h3 key={k} className="chat-md-h">{content}</h3> : <h4 key={k} className="chat-md-h">{content}</h4>);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      flush();
      const head = splitRow(line);
      const body: string[][] = [];
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { body.push(splitRow(lines[i])); i++; }
      i--;
      const k = `t${b++}`;
      blocks.push(
        <div key={k} className="chat-md-table-wrap">
          <table className="chat-md-table">
            <thead><tr>{head.map((c, j) => <th key={j}>{inline(c, `${k}h${j}`)}</th>)}</tr></thead>
            <tbody>{body.map((r, ri) => <tr key={ri}>{head.map((_, j) => <td key={j}>{inline(r[j] ?? '', `${k}r${ri}c${j}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (/^\s*>/.test(line)) {
      flush();
      const quote: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { quote.push(lines[i].replace(/^\s*>\s?/, '')); i++; }
      i--;
      const k = `q${b++}`;
      blocks.push(<blockquote key={k}>{withBreaks(quote, k)}</blockquote>);
      continue;
    }
    const li = line.match(LIST_RE);
    if (li) {
      flush();
      const ordered = /\d/.test(li[2]);
      const items: { text: string; indent: number }[] = [];
      while (i < lines.length) {
        const m = lines[i].match(LIST_RE);
        if (m && /\d/.test(m[2]) === ordered) { items.push({ text: m[3], indent: m[1].length }); i++; continue; }
        // continuation line of the previous item (indented, not blank)
        if (items.length && lines[i].trim() && /^\s{2,}/.test(lines[i]) && !LIST_RE.test(lines[i])) {
          items[items.length - 1].text += `\n${lines[i].trim()}`; i++; continue;
        }
        break;
      }
      i--;
      const k = `l${b++}`;
      const children = items.map((it, j) => (
        <li key={j} className={it.indent >= 2 ? 'chat-md-sub' : undefined}>{withBreaks(it.text.split('\n'), `${k}i${j}`)}</li>
      ));
      blocks.push(ordered ? <ol key={k}>{children}</ol> : <ul key={k}>{children}</ul>);
      continue;
    }
    para.push(line.trim());
  }
  flush();
  return blocks;
}

export const Markdown = memo(function Markdown({ text, className = '' }: { text: string; className?: string }) {
  const nodes = useMemo(() => renderMarkdown(text), [text]);
  return <div className={`chat-md ${className}`}>{nodes}</div>;
});
