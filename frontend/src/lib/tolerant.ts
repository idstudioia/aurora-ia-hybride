// Tolerant readers for tool payloads whose exact field names are not fixed by the contract
// (ToolResult.data, run.ui.data, bench results). They only READ what the API returned:
// a missing value stays null and is shown as unavailable, never replaced by a guess.

export type Json = Record<string, unknown>;

export const isObj = (v: unknown): v is Json => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Finite number from a number or numeric string, else null. */
export function num(v: unknown): number | null {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

/** Value at a dot path ("metrics.period_s"). */
export function getPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj;
  for (const part of path.split('.')) {
    if (!isObj(cur)) return undefined;
    cur = cur[part];
  }
  return cur;
}

/** First finite number found under one of the paths. */
export function pickNum(obj: unknown, paths: string[]): number | null {
  for (const p of paths) {
    const v = num(getPath(obj, p));
    if (v !== null) return v;
  }
  return null;
}

/** First non-empty string found under one of the paths. */
export function pickStr(obj: unknown, paths: string[]): string | null {
  for (const p of paths) {
    const v = getPath(obj, p);
    if (typeof v === 'string' && v.trim()) return v;
  }
  return null;
}

/** First array found under one of the paths. */
export function pickArr(obj: unknown, paths: string[]): unknown[] | null {
  for (const p of paths) {
    const v = getPath(obj, p);
    if (Array.isArray(v)) return v;
  }
  return null;
}

/** First object found under one of the paths. */
export function pickObj(obj: unknown, paths: string[]): Json | null {
  for (const p of paths) {
    const v = getPath(obj, p);
    if (isObj(v)) return v;
  }
  return null;
}

/** Every numeric scalar reachable from obj (depth-limited), keyed by dot path. Arrays are skipped. */
export function numericLeaves(obj: unknown, depth = 2, prefix = ''): { path: string; key: string; value: number }[] {
  const out: { path: string; key: string; value: number }[] = [];
  if (!isObj(obj)) return out;
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'number' && Number.isFinite(v)) out.push({ path, key: k, value: v });
    else if (isObj(v) && depth > 0) out.push(...numericLeaves(v, depth - 1, path));
  }
  return out;
}

/** First numeric leaf whose key matches the pattern (searched breadth-first by depth). */
export function findNum(obj: unknown, pattern: RegExp, exclude?: RegExp, depth = 2): { path: string; key: string; value: number } | null {
  const leaves = numericLeaves(obj, depth).sort((a, b) => a.path.split('.').length - b.path.split('.').length);
  return leaves.find(l => pattern.test(l.key) && !(exclude && exclude.test(l.key))) ?? null;
}

/** [x, y, z] from an array or an {x, y, z} object (also x_km / x_au variants). */
export function vec3(v: unknown): [number, number, number] | null {
  if (Array.isArray(v) && v.length >= 3) {
    const [x, y, z] = [num(v[0]), num(v[1]), num(v[2])];
    return x !== null && y !== null && z !== null ? [x, y, z] : null;
  }
  if (isObj(v)) {
    for (const suffix of ['', '_km', '_au', '_m']) {
      const x = num(v[`x${suffix}`]); const y = num(v[`y${suffix}`]); const z = num(v[`z${suffix}`]);
      if (x !== null && y !== null && z !== null) return [x, y, z];
    }
  }
  return null;
}

/** Human label from a snake_case key, used only when no French label is known. */
export const humanizeKey = (key: string) => key.replace(/[_.]+/g, ' ').replace(/\s+/g, ' ').trim();
