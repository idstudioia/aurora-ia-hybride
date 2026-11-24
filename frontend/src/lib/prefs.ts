// Un choix d'affichage gardé dans le navigateur (vue d'une page, bascule). Sorti tel quel de pages/MemoryPage.tsx
// (11/11/2026) pour servir aussi à la page Tâches, au lieu d'être recopié. Stockage bloqué : le choix dure jusqu'au
// rechargement.

/** A stored choice (`fallback` when absent, unknown or the storage is unavailable). */
export function readPref<T extends string>(key: string, values: readonly T[], fallback: T): T {
  try {
    const v = window.localStorage.getItem(key);
    return values.includes(v as T) ? (v as T) : fallback;
  } catch { return fallback; }
}

export const writePref = (key: string, value: string) => {
  try { window.localStorage.setItem(key, value); } catch { /* stockage indisponible */ }
};
