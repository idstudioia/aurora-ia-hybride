// Bouchon de src/lib/stream.ts pour les tests hors navigateur (tests/crochets.mjs) : aucun flux en direct n'est ouvert.
export const useEventStream = () => undefined;
export const useStreamStatus = () => false;
