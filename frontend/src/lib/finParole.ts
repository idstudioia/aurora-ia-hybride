// Fin de la parole d'après le niveau réel du micro (voix D3, 9/11/2026). Le moteur du navigateur dit lui-même quand
// une phrase est finie ; Whisper, lui, ne reçoit qu'un enregistrement : c'est le niveau du micro (RMS mesuré par
// l'AnalyserNode, 0 à 1) qui dit quand la personne a parlé puis s'est tue. Module pur, testé sans navigateur
// (tests/finParole.test.mjs). Les durées sont celles de l'écoute du navigateur (voice.ts, réglages d'Athéna allongés
// le 29/10/2026) : la phrase finit après 2 s de silence, 8 s pour commencer à parler, 2 minutes au plus.

export interface ReglagesFinParole {
  silenceApresParoleMs: number;
  attentePremierMotMs: number;
  maxMs: number;
}

/** 'continue' : on écoute encore ; 'fin' : on a entendu parler puis un silence ; 'rien' : personne n'a parlé. */
export type VerdictParole = 'continue' | 'fin' | 'rien';

export const CALIBRAGE_MS = 300;        // le bruit de fond est mesuré au début de l'écoute
export const PAROLE_MIN_MS = 150;       // un son plus court (un clic, un souffle) n'est pas de la parole
export const SEUIL_MIN = 0.012;         // en dessous : bruit de fond, même dans une pièce très calme
export const SEUIL_MAX = 0.05;          // au-dessus : une voix normale passe toujours, même si on parlait dès le début

export interface FinDeParole {
  /** Un niveau mesuré à `t` ms depuis le début de l'écoute (appelé à intervalles réguliers). */
  niveau(rms: number, t: number): VerdictParole;
  /** Vrai dès qu'une parole a été entendue. */
  aParle(): boolean;
  seuil(): number;
}

export function creerFinDeParole(r: ReglagesFinParole): FinDeParole {
  let somme = 0;
  let mesures = 0;
  let seuil = SEUIL_MIN;
  let fort = 0;               // durée du son fort en cours
  let parle = false;
  let dernierFort = 0;
  let precedent = 0;
  return {
    niveau(rms: number, t: number): VerdictParole {
      const dt = Math.max(0, t - precedent);
      precedent = t;
      if (t < CALIBRAGE_MS) {
        somme += rms;
        mesures += 1;
        seuil = Math.min(SEUIL_MAX, Math.max(SEUIL_MIN, (somme / mesures) * 3));
        return 'continue';
      }
      if (rms > seuil) {
        fort += dt;
        if (fort >= PAROLE_MIN_MS) parle = true;
        if (parle) dernierFort = t;
      } else fort = 0;
      if (t >= r.maxMs) return parle ? 'fin' : 'rien';
      if (parle) return t - dernierFort >= r.silenceApresParoleMs ? 'fin' : 'continue';
      return t >= r.attentePremierMotMs ? 'rien' : 'continue';
    },
    aParle: () => parle,
    seuil: () => seuil,
  };
}
