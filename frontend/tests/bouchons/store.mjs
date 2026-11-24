// Bouchon de src/lib/store.tsx pour les tests hors navigateur (tests/crochets.mjs) : le tiroir du chat est fermé,
// rien n'est demandé au service local.
export const useAurora = () => ({ chatOpen: false });
