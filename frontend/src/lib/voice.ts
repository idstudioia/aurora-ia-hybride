// Voice input/output for AURORA: Web Speech API recognition (fr-FR), the real microphone level
// (Web Audio AnalyserNode, only while listening), optional speech synthesis of answers and the
// level of AURORA's own voice (drives the waveforms and the rings around the home globe).
// Everything is feature-detected; nothing here fakes a level or a transcript.
import { useEffect, useState, useSyncExternalStore } from 'react';
import { api } from './api';
import { creerFinDeParole } from './finParole';

// ---------- speech recognition ----------
// lib.dom only ships the result types, not the recognizer itself.
interface RecognitionErrorEventLike { error: string; message?: string }
interface RecognitionEventLike { resultIndex: number; results: SpeechRecognitionResultList }
interface RecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  processLocally?: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onstart: (() => void) | null;
  onresult: ((ev: RecognitionEventLike) => void) | null;
  onerror: ((ev: RecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
}
interface RecognitionCtor {
  new (): RecognitionLike;
  available?: (opts: { langs: string[]; processLocally: boolean }) => Promise<string>;
}

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export const VOICE_LANG = 'fr-FR';

/** 'local' = on-device recognition, 'browser' = the browser's speech service (may be online),
 *  'whisper' = Athéna's Whisper service run by AURORA on this PC (voix D3: the audio never leaves it). */
export type RecognitionMode = 'local' | 'browser' | 'whisper' | 'unsupported';

export interface VoiceSupport { recognition: boolean; synthesis: boolean; microphone: boolean; secure: boolean }

export function voiceSupport(): VoiceSupport {
  const secure = typeof window !== 'undefined' && window.isSecureContext;
  return {
    recognition: recognitionCtor() !== null || (whisper.available && typeof MediaRecorder !== 'undefined'),
    synthesis: typeof window !== 'undefined' && 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined',
    microphone: typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia,
    secure,
  };
}

export const RECOGNITION_MODE_LABELS: Record<RecognitionMode, string> = {
  local: "Reconnaissance sur l'appareil (l'audio reste local)",
  browser: 'Reconnaissance du navigateur (peut transmettre l’audio au service en ligne du navigateur)',
  whisper: 'Whisper sur ce PC (l’audio reste local)',
  unsupported: 'Reconnaissance vocale non prise en charge par ce navigateur',
};

// Local Whisper (voix D3): available when the service found Athéna's Whisper (GET /api/stt, read by App), used
// when the viewer chose it, and by itself when the browser cannot listen here (Private mode without on-device
// recognition, or no recognizer at all). The browser's recognition stays the way back when Whisper is missing.
let whisper = { available: false, preferred: false };
let recognitionVersion = 0;
const recognitionSubs = new Set<() => void>();
const recognitionChanged = () => { recognitionVersion += 1; recognitionSubs.forEach(fn => fn()); };
export function setLocalWhisper(available: boolean): void {
  if (whisper.available !== available) { whisper = { ...whisper, available }; recognitionChanged(); }
}
export function setWhisperPreferred(preferred: boolean): void {
  if (whisper.preferred !== preferred) { whisper = { ...whisper, preferred }; recognitionChanged(); }
}
export function localWhisper(): { available: boolean; preferred: boolean } { return whisper; }
/** Changes each time the recognition choice may have changed: components re-read recognitionMode() then. */
export function useRecognitionVersion(): number {
  return useSyncExternalStore(fn => { recognitionSubs.add(fn); return () => { recognitionSubs.delete(fn); }; },
    () => recognitionVersion, () => 0);
}

function usesWhisper(browser: RecognitionMode): boolean {
  if (!whisper.available || typeof MediaRecorder === 'undefined') return false;
  return whisper.preferred || browser === 'unsupported' || recognitionRefusal(browser) !== null;
}

/** The recognition used now: Whisper on this PC, the browser's on-device recognition, or its service. */
export function recognitionMode(): Promise<RecognitionMode> {
  return browserRecognitionMode().then(m => (usesWhisper(m) ? 'whisper' : m));
}

let modePromise: Promise<RecognitionMode> | null = null;

/** Detects whether on-device recognition is available for French (newer Chromium only). */
function browserRecognitionMode(): Promise<RecognitionMode> {
  const C = recognitionCtor();
  if (!C) return Promise.resolve('unsupported');
  if (typeof C.available !== 'function') return Promise.resolve('browser');
  if (!modePromise) {
    modePromise = C.available({ langs: [VOICE_LANG], processLocally: true })
      .then(state => (state === 'available' ? 'local' : 'browser') as RecognitionMode)
      .catch(() => 'browser' as RecognitionMode);
  }
  return modePromise;
}

// Private mode keeps everything on the PC (README, CLAUDE.md « local by default »): recognition that may send the
// audio to the browser's online service is refused there; on-device recognition stays allowed. Kept in sync by
// App from the service mode, like setOnlineVoicesAllowed for the voices. Unknown mode: refused by precaution.
let recognitionServiceMode: string | undefined;
export function setRecognitionServiceMode(mode: string | undefined): void {
  if (recognitionServiceMode !== mode) { recognitionServiceMode = mode; recognitionChanged(); }
}

/** Why the microphone must not listen with this recognition mode, or null. */
export function recognitionRefusal(mode: RecognitionMode, serviceMode: string | undefined = recognitionServiceMode): string | null {
  if (mode !== 'browser') return null;
  if (serviceMode === 'prive') {
    return 'Mode Privé : ce navigateur ne reconnaît pas la voix sur l’appareil et enverrait l’audio à son service en ligne. '
      + 'Le micro reste coupé en mode Privé : écrivez votre demande, ou passez en mode Équilibré dans Paramètres.';
  }
  if (!serviceMode) return 'Mode d’AURORA inconnu (service injoignable) : la reconnaissance en ligne reste coupée par précaution.';
  return null;
}

const RECOGNITION_ERRORS: Record<string, string> = {
  'no-speech': 'Aucune parole détectée. Réessayez en parlant près du micro.',
  'audio-capture': 'Aucun microphone utilisable n’a été trouvé.',
  'not-allowed': 'Accès au microphone refusé. Autorisez le micro dans le navigateur.',
  'service-not-allowed': 'Le service de reconnaissance vocale n’est pas autorisé dans ce navigateur.',
  network: 'Le service de reconnaissance vocale du navigateur est injoignable (réseau).',
  aborted: 'Écoute interrompue.',
  'language-not-supported': 'Le français n’est pas pris en charge par ce moteur de reconnaissance.',
  'bad-grammar': 'Erreur de reconnaissance vocale.',
};

export function recognitionErrorMessage(code: string): string {
  return RECOGNITION_ERRORS[code] ?? `Erreur de reconnaissance vocale (${code}).`;
}

/** What the recognizer itself says about what it heard: its lowest confidence over the final pieces (0–1), or null
 *  when it gave none (Chrome reports 0 for « unknown »). Read by the dictation check (dictee.ts, voix D1). */
export interface HeardInfo { confiance: number | null }

export interface ListenHandlers {
  onStart?: (mode: RecognitionMode) => void;
  onInterim?: (text: string) => void;
  onFinal: (text: string, info: HeardInfo) => void;
  onError?: (code: string, message: string) => void;
  onEnd?: () => void;
}

export interface ListenSession {
  /** Stop listening and keep what was recognised so far. */
  stop(): void;
  /** Stop listening and drop the result. */
  abort(): void;
}

// The end of a sentence is a silence, not the browser's first final result (29/10/2026: « il coupe vite »): timings
// from Athéna's dictation (dialogueVocal.ts:12, 30), a little longer here so that a pause to think does not cut.
export const SILENCE_AFTER_FINAL_MS = 2000;   // everything heard is final: the sentence is over after 2 s of silence
export const SILENCE_INTERIM_MS = 4000;       // a word is still being recognised: wait longer
export const WAIT_FIRST_WORD_MS = 8000;       // time to start speaking
export const MAX_LISTEN_MS = 120_000;
const RESTART_DELAY_MS = 350;                 // the browser ends a continuous run by itself: listening goes on

/** Listen to one request, however many pauses it has: the recognizer runs continuously (and is restarted when the
 *  browser ends it), the final pieces are kept in order, and the request ends after a silence, a click (stop) or
 *  two minutes. onFinal is called once with the whole text; onEnd once, at the very end. */
export async function startListening(handlers: ListenHandlers): Promise<ListenSession | null> {
  const mode = await recognitionMode();
  if (mode === 'whisper') return startWhisperListening(handlers);
  const C = recognitionCtor();
  if (!C) return null;
  const refusal = recognitionRefusal(mode);
  if (refusal) {                       // every caller is covered, not only the command bar
    handlers.onError?.('private-mode', refusal);
    return null;
  }
  let kept = '';                                 // text of the runs before a restart
  const finals = new Map<number, string>();      // final results of the current run, by their index
  let interim = '';
  let confiance: number | null = null;           // lowest confidence the recognizer gave for a final piece
  let done = false;
  let running = false;
  let ended = false;
  let silence = 0;
  let restartTimer = 0;
  const heard = () => [kept, ...[...finals.keys()].sort((a, b) => a - b).map(k => finals.get(k) ?? '')]
    .join(' ').replace(/\s+/g, ' ').trim();
  const end = () => {                            // the one and only end, whatever stopped the listening
    if (ended) return;
    ended = true;
    window.clearTimeout(silence);
    window.clearTimeout(restartTimer);
    window.clearTimeout(cap);
    setListening(false);
    handlers.onEnd?.();
  };
  const finish = () => {                         // silence, click or time limit: keep what was said
    if (done) return;
    done = true;
    window.clearTimeout(silence);
    window.clearTimeout(restartTimer);
    const text = [heard(), interim].join(' ').replace(/\s+/g, ' ').trim();
    try { rec.stop(); } catch { /* déjà arrêté */ }
    if (text) handlers.onFinal(text, { confiance });
    else handlers.onError?.('no-speech', recognitionErrorMessage('no-speech'));
    if (!running) end();
  };
  const arm = (ms: number) => { window.clearTimeout(silence); silence = window.setTimeout(finish, ms); };
  const cap = window.setTimeout(finish, MAX_LISTEN_MS);

  const make = (): RecognitionLike => {
    const r = new C();
    r.lang = VOICE_LANG;
    r.continuous = true;
    r.interimResults = true;
    r.maxAlternatives = 1;
    if (mode === 'local' && 'processLocally' in r) {
      try { r.processLocally = true; } catch { /* propriété non modifiable */ }
    }
    r.onstart = () => handlers.onStart?.(mode);
    r.onresult = ev => {
      if (done) return;
      let pending = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        const t = (res[0]?.transcript ?? '').trim();
        if (res.isFinal) {
          finals.set(i, t);
          const c = res[0]?.confidence;
          if (t && typeof c === 'number' && c > 0) confiance = confiance === null ? c : Math.min(confiance, c);
        } else pending += ` ${t}`;
      }
      interim = pending.trim();
      handlers.onInterim?.([heard(), interim].join(' ').replace(/\s+/g, ' ').trim());
      arm(interim ? SILENCE_INTERIM_MS : SILENCE_AFTER_FINAL_MS);
    };
    r.onerror = ev => {
      if (ev.error === 'no-speech' || ev.error === 'aborted') return;   // silence or our own stop: not a failure
      if (done) return;
      done = true;
      handlers.onError?.(ev.error, recognitionErrorMessage(ev.error));
    };
    r.onend = () => {
      running = false;
      if (done) { end(); return; }
      kept = heard();                            // a new run numbers its results from 0 again
      finals.clear();
      restartTimer = window.setTimeout(() => {
        if (done) { end(); return; }
        rec = make();
        try { rec.start(); running = true; } catch { done = true; end(); }
      }, RESTART_DELAY_MS);
    };
    return r;
  };

  let rec = make();
  try {
    rec.start();
    running = true;
    setListening(true);
  } catch (err) {
    window.clearTimeout(cap);
    handlers.onError?.('start', err instanceof Error ? `Démarrage de l’écoute impossible : ${err.message}` : 'Démarrage de l’écoute impossible.');
    handlers.onEnd?.();
    return null;
  }
  arm(WAIT_FIRST_WORD_MS);
  return {
    stop: finish,
    abort: () => {
      if (ended) return;
      done = true;                               // nothing is sent: abort drops the text
      try { rec.abort(); } catch { /* déjà arrêté */ }
      if (!running) end();
    },
  };
}

const WHISPER_TICK_MS = 50;
const RECORDING_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];

/** Voix D3: records one request from the real microphone, ends it on the silence that follows speech (finParole.ts,
 *  same timings as the browser's listening), then has it recognised on this PC by Whisper (POST /api/stt). No text
 *  while speaking (Whisper reads the whole recording) and no confidence (null): the dictation check (dictee.ts)
 *  relies on its own rules. onFinal then onEnd, as with the browser's recognition. */
async function startWhisperListening(handlers: ListenHandlers): Promise<ListenSession | null> {
  const failed = (message: string) => { handlers.onError?.('start', message); handlers.onEnd?.(); return null; };
  let mic: MicMeter;
  try {
    mic = await openMicMeter(false);
  } catch (err) {
    return failed(err instanceof Error && err.name === 'NotAllowedError' ? 'Accès au micro refusé.'
      : `Démarrage de l’écoute impossible : ${err instanceof Error ? err.message : 'micro indisponible'}.`);
  }
  void api.prepareRecognition().catch(() => undefined);       // Whisper starts while the viewer speaks
  const type = RECORDING_TYPES.find(t => MediaRecorder.isTypeSupported?.(t));
  let recorder: MediaRecorder;
  try {
    recorder = new MediaRecorder(mic.stream, type ? { mimeType: type } : undefined);
  } catch (err) {
    mic.close();
    return failed(`Enregistrement du micro impossible : ${err instanceof Error ? err.message : 'format non pris en charge'}.`);
  }
  const pieces: Blob[] = [];
  recorder.ondataavailable = ev => { if (ev.data.size) pieces.push(ev.data); };
  const detector = creerFinDeParole({
    silenceApresParoleMs: SILENCE_AFTER_FINAL_MS, attentePremierMotMs: WAIT_FIRST_WORD_MS, maxMs: MAX_LISTEN_MS,
  });
  const bands = new Float32Array(8);
  const controller = new AbortController();
  let recording = true;
  let aborted = false;
  let nobody = false;          // the detector heard no speech at all: nothing is sent
  let ended = false;
  let tick = 0;
  const end = () => { if (!ended) { ended = true; handlers.onEnd?.(); } };

  const transcribe = async () => {
    if (aborted) { end(); return; }
    const audio = new Blob(pieces, { type: recorder.mimeType || type || 'audio/webm' });
    if (nobody || !audio.size) {
      handlers.onError?.('no-speech', recognitionErrorMessage('no-speech'));
      end();
      return;
    }
    handlers.onInterim?.('Transcription sur ce PC…');
    try {
      const heard = (await api.transcribe(audio, controller.signal)).text.replace(/\s+/g, ' ').trim();
      if (aborted) { /* dropped while Whisper was reading */ }
      else if (heard) handlers.onFinal(heard, { confiance: null });
      else handlers.onError?.('no-speech', recognitionErrorMessage('no-speech'));
    } catch (err) {
      if (!aborted) handlers.onError?.('whisper', `Reconnaissance locale impossible : ${err instanceof Error ? err.message : 'erreur inconnue'}`);
    }
    end();
  };
  const stopRecording = () => {
    if (!recording) return;
    recording = false;
    window.clearInterval(tick);
    setListening(false);
    recorder.onstop = () => { mic.close(); void transcribe(); };
    try { recorder.stop(); } catch { mic.close(); void transcribe(); }
  };

  try {
    recorder.start(1000);
  } catch (err) {
    mic.close();
    return failed(`Enregistrement du micro impossible : ${err instanceof Error ? err.message : 'erreur inconnue'}.`);
  }
  setListening(true);
  handlers.onStart?.('whisper');
  const t0 = performance.now();
  tick = window.setInterval(() => {
    const verdict = detector.niveau(mic.read(bands), performance.now() - t0);
    if (verdict === 'rien') nobody = true;
    if (verdict !== 'continue') stopRecording();
  }, WHISPER_TICK_MS);
  return {
    stop: stopRecording,                                   // a click: what was said is read, even softly said
    abort: () => { aborted = true; controller.abort(); stopRecording(); },   // nothing is read nor sent
  };
}

// ---------- microphone level (Web Audio) ----------
export interface LevelMeter {
  /** Fills `out` with band levels in [0, 1] (voice range) and returns the overall RMS level. */
  read(out: Float32Array): number;
}
export interface MicMeter extends LevelMeter {
  /** The microphone stream itself (recorded for Whisper, voix D3). */
  readonly stream: MediaStream;
  close(): void;
}

const audioCtor = () => window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;

/** Band levels and RMS read from a real AnalyserNode (shared by the microphone and AURORA's own voice). */
function analyserReader(analyser: AnalyserNode, sampleRate: number): LevelMeter['read'] {
  const freq = new Uint8Array(analyser.frequencyBinCount);
  const time = new Uint8Array(analyser.fftSize);
  const binHz = sampleRate / analyser.fftSize;
  return (out: Float32Array): number => {
    analyser.getByteFrequencyData(freq);
    analyser.getByteTimeDomainData(time);
    // log-spaced bands between 90 Hz and 5 kHz (speech range)
    const lo = Math.log(90), hi = Math.log(5000);
    for (let b = 0; b < out.length; b++) {
      const f0 = Math.exp(lo + ((hi - lo) * b) / out.length);
      const f1 = Math.exp(lo + ((hi - lo) * (b + 1)) / out.length);
      const i0 = Math.max(1, Math.floor(f0 / binHz));
      const i1 = Math.min(freq.length - 1, Math.max(i0, Math.ceil(f1 / binHz)));
      let sum = 0;
      for (let i = i0; i <= i1; i++) sum += freq[i];
      out[b] = sum / ((i1 - i0 + 1) * 255);
    }
    let sq = 0;
    for (let i = 0; i < time.length; i++) { const v = (time[i] - 128) / 128; sq += v * v; }
    return Math.sqrt(sq / time.length);
  };
}

/** The microphone meter currently open (read by the globe animation while listening). */
let activeMic: MicMeter | null = null;

/** `shown` false: a meter for the listening itself (Whisper's end of speech), not the one the globe animation reads. */
export async function openMicMeter(shown = true): Promise<MicMeter> {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('Accès au micro indisponible dans ce contexte.');
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  const Ctx = audioCtor();
  if (!Ctx) { stream.getTracks().forEach(t => t.stop()); throw new Error('Web Audio indisponible.'); }
  const ctx = new Ctx();
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.55;
  source.connect(analyser); // never connected to the speakers
  const readLevels = analyserReader(analyser, ctx.sampleRate);
  let closed = false;
  const meter: MicMeter = {
    stream,
    read(out: Float32Array): number {
      if (closed) { out.fill(0); return 0; }
      return readLevels(out);
    },
    close() {
      if (closed) return;
      closed = true;
      if (activeMic === meter) activeMic = null;
      stream.getTracks().forEach(t => t.stop());
      try { source.disconnect(); } catch { /* déjà déconnecté */ }
      void ctx.close().catch(() => undefined);
    },
  };
  if (shown) activeMic = meter;
  return meter;
}

// ---------- speech synthesis ----------
let speaking = false;
let listening = false; // recognition running (set by startListening)
let generation = 0;
const speechSubs = new Set<() => void>();
const setSpeaking = (v: boolean) => { if (speaking !== v) { speaking = v; speechSubs.forEach(fn => fn()); } };
const setListening = (v: boolean) => { if (listening !== v) { listening = v; speechSubs.forEach(fn => fn()); } };
const subscribeSpeech = (fn: () => void) => { speechSubs.add(fn); return () => { speechSubs.delete(fn); }; };

export function isSpeaking(): boolean { return speaking; }
/** Changes each time a reading starts or is stopped: whoever started the current one can tell it is still theirs
 *  (8/11/2026: the hologram globe never talks over AURORA's answer, even when that answer cut the globe's sentence). */
export function speechGeneration(): number { return generation; }

export function useSpeaking(): boolean {
  return useSyncExternalStore(subscribeSpeech, () => speaking, () => false);
}

export type VoiceActivity = 'idle' | 'listening' | 'speaking';
/** What the conversation is doing by voice right now (AURORA speaking wins over listening). */
export function useVoiceActivity(): VoiceActivity {
  return useSyncExternalStore(subscribeSpeech, () => (speaking ? 'speaking' : listening ? 'listening' : 'idle'), () => 'idle');
}

function chunks(text: string, max = 220): string[] {
  const parts = text.replace(/\s+/g, ' ').trim().split(/(?<=[.!?…;:])\s+/);
  const out: string[] = [];
  let cur = '';
  for (const p of parts) {
    if ((cur + ' ' + p).trim().length <= max) { cur = (cur + ' ' + p).trim(); continue; }
    if (cur) out.push(cur);
    if (p.length <= max) { cur = p; continue; }
    // very long sentence: cut on commas / spaces
    let rest = p;
    while (rest.length > max) {
      let cut = rest.lastIndexOf(',', max);
      if (cut < max / 2) cut = rest.lastIndexOf(' ', max);
      if (cut < 1) cut = max;
      out.push(rest.slice(0, cut + 1).trim());
      rest = rest.slice(cut + 1);
    }
    cur = rest.trim();
  }
  if (cur) out.push(cur);
  return out;
}

export function frenchVoices(): SpeechSynthesisVoice[] {
  if (!voiceSupport().synthesis) return [];
  return window.speechSynthesis.getVoices().filter(v => v.lang.toLowerCase().startsWith('fr'));
}

/** French voices, refreshed when the browser finishes loading them. */
export function useFrenchVoices(): SpeechSynthesisVoice[] {
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>(() => frenchVoices());
  useEffect(() => {
    if (!voiceSupport().synthesis) return;
    const update = () => setVoices(frenchVoices());
    window.speechSynthesis.addEventListener('voiceschanged', update);
    update();
    return () => window.speechSynthesis.removeEventListener('voiceschanged', update);
  }, []);
  return voices;
}

// ---- the most human-sounding voice allowed ----
// Online voices (Edge « Microsoft … Online (Natural) », Chrome « Google français ») send the text read aloud
// to the browser vendor: used only when the viewer allowed them and never in Private mode (set by App).
let onlineAllowed = false;
export function setOnlineVoicesAllowed(allowed: boolean): void { onlineAllowed = allowed; }
export function onlineVoicesAllowed(): boolean { return onlineAllowed; }

/** Higher sounds more human: neural « Natural » voices first, then Google, then the local voices. */
export function voiceScore(v: SpeechSynthesisVoice): number {
  const name = v.name.toLowerCase();
  let score = 0;
  if (/natural|neural/.test(name)) score += 60;
  if (/google/.test(name)) score += 40;
  if (/online/.test(name)) score += 10;
  if (v.lang === VOICE_LANG) score += 8;          // fr-FR before fr-CA, fr-BE, fr-CH
  return score;
}

export const isNaturalVoice = (v: SpeechSynthesisVoice) => voiceScore(v) >= 40;

/** The voice that will read: the viewer's choice if it is allowed, otherwise the best allowed one. */
export function chosenVoice(voiceName = ''): SpeechSynthesisVoice | null {
  const allowed = frenchVoices().filter(v => v.localService || onlineAllowed);
  return allowed.find(v => v.name === voiceName)
    ?? allowed.sort((a, b) => voiceScore(b) - voiceScore(a))[0] ?? null;
}

const SPOKEN: [RegExp, string][] = [
  // Markdown is shown, not read (29/10/2026, Athéna's stripMarkdown, voixDecision.ts:54-79): code blocks, link
  // labels only, bold / italics / code marks, titles, quotes, list marks, table bars.
  [/```[\s\S]*?```/g, '. '], [/!?\[([^\]]+)\]\([^)]*\)/g, '$1'], [/`([^`]+)`/g, '$1'], [/\*\*|__|~~/g, ''],
  [/(^|\s)\*(?=\S)([^*\n]+?)\*(?=\s|[.,;:!?]|$)/g, '$1$2'], [/^\s{0,3}#{1,6}\s+/gm, ''], [/^\s*>\s?/gm, ''],
  [/^\s*(?:[-*+•]|\d+[.)])\s+/gm, ''], [/\s*\|\s*/g, ', '],
  // Model names are said, not spelled: « gemma4:e4b » → « Gemma 4 », « qwen3.5:9b » → « Qwen 3 virgule 5 ».
  [/\b(gemma|qwen|llama|mistral|phi|granite|deepseek|nomic|bge)[-\s]?(\d+)(?:\.(\d+))?(?::[\w.-]+)?/gi, '$1 $2 virgule $3'],
  [/ virgule (?=[\s,.;:!?)]|$)/g, ' '], [/\bIA\b/g, 'I A'], [/\bAURORA\b/g, 'Aurora'], [/\bAPI\b/g, 'A P I'],
  [/(\d)\.(\d)/g, '$1 virgule $2'], [/\b(\d{1,2}):(\d{2})\b/g, '$1 heures $2'],
  [/https?:\/\/\S+/g, ''], [/\[\d+(?:\s*[,–-]\s*\d+)*\]/g, ''],                  // links, source marks [1]
  [/(\d)\s?To\b/g, '$1 téraoctets'], [/(\d)\s?Go\b/g, '$1 gigaoctets'], [/(\d)\s?Mo\b/g, '$1 mégaoctets'],
  [/(\d)\s?Ko\b/g, '$1 kilooctets'], [/\bkm\/h\b/g, 'kilomètres heure'], [/°\s?C\b/g, ' degrés'],
  [/°/g, ' degrés'], [/\s?%/g, ' pour cent'], [/×/g, ' fois '], [/→|->/g, ' puis '], [/\s?·\s?/g, ', '],
  [/(\d)\s?\$/g, '$1 dollars'], [/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}️‍]/gu, ''],
  [/\s*(?:—|-)?\s*\bvoir\s*$/i, ''],                                              // « voir » left by a removed link
];

/** Text as a person would say it: units in words, no links nor source marks, no emoji. */
export function speechText(text: string): string {
  let out = text;
  for (const [re, by] of SPOKEN) out = out.replace(re, by);
  return out.replace(/[ \t]{2,}/g, ' ').trim();
}

export interface SpeakOptions { voiceName?: string; rate?: number; onEnd?: () => void }

// ---- local neural voice (Piper, generated by the AURORA service on this PC: nothing leaves it) ----
// It is the default when installed; the browser voices below remain the fallback (CLAUDE.md).
export const NEURAL_VOICE_ID = 'aurora-piper';
let neural = { available: false, name: '' };
export function setNeuralVoice(available: boolean, name = ''): void { neural = { available, name }; }
export function neuralVoice(): { available: boolean; name: string } { return neural; }
export const usesNeuralVoice = (voiceName = '') => neural.available && (voiceName === '' || voiceName === NEURAL_VOICE_ID);

let currentAudio: HTMLAudioElement | null = null;
let currentAbort: AbortController | null = null;

// Level of the local voice, measured on the audio really played (AnalyserNode). The audio goes through
// Web Audio only when the context is running: routed into a suspended context, the voice would be silent.
let outCtx: AudioContext | null = null;
let outAnalyser: AnalyserNode | null = null;
let outRead: LevelMeter['read'] | null = null;
let routedAudio: HTMLAudioElement | null = null;

async function routeToMeter(audio: HTMLAudioElement): Promise<MediaElementAudioSourceNode | null> {
  try {
    const Ctx = audioCtor();
    if (!Ctx) return null;
    if (!outCtx) {
      outCtx = new Ctx();
      outAnalyser = outCtx.createAnalyser();
      outAnalyser.fftSize = 1024;
      outAnalyser.smoothingTimeConstant = 0.6;
      outAnalyser.connect(outCtx.destination);
      outRead = analyserReader(outAnalyser, outCtx.sampleRate);
    }
    if (outCtx.state !== 'running') {
      await Promise.race([outCtx.resume().catch(() => undefined), new Promise(r => window.setTimeout(r, 150))]);
    }
    if (outCtx.state !== 'running' || !outAnalyser) return null;
    const source = outCtx.createMediaElementSource(audio);
    source.connect(outAnalyser);
    routedAudio = audio;
    return source;
  } catch {
    return null; // the voice plays anyway, only its level is missing
  }
}

function playBlob(blob: Blob): Promise<void> {
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  currentAudio = audio;
  let source: MediaElementAudioSourceNode | null = null;
  return routeToMeter(audio)
    .then(s => new Promise<void>((resolve, reject) => {
      source = s;
      if (currentAudio !== audio) { resolve(); return; } // stopped while the meter was being prepared
      audio.onended = () => resolve();
      audio.onerror = () => reject(new Error('lecture audio impossible'));
      audio.play().catch(reject);
    }))
    .finally(() => {
      URL.revokeObjectURL(url);
      try { source?.disconnect(); } catch { /* déjà déconnecté */ }
    });
}

let lastBoundary = 0;

/** Level of what AURORA is saying. Local voice: measured on the audio played. Browser voice: the browser
 *  gives no access to its audio, so this follows the word boundaries it reports (a rhythm, not a loudness). */
export const speechMeter: LevelMeter = {
  read(out: Float32Array): number {
    if (!speaking) { out.fill(0); return 0; }
    if (outRead && routedAudio && routedAudio === currentAudio) return outRead(out);
    const beat = Math.exp(-(performance.now() - lastBoundary) / 200);
    for (let b = 0; b < out.length; b++) out[b] = beat * (0.55 - 0.35 * (b / out.length));
    return beat * 0.2;
  },
};

const levelBands = new Float32Array(8);
/** 0–1: AURORA's voice while she speaks, the microphone while listening, 0 otherwise. */
export function voiceLevel(): number {
  // Piper's audio is peak-normalised (speech RMS ≈ 0.1–0.25): a lower gain than the microphone keeps headroom.
  if (speaking) return Math.min(1, speechMeter.read(levelBands) * 3);
  return listening && activeMic ? Math.min(1, activeMic.read(levelBands) * 4) : 0;
}

/** One sentence after the other, the next one prepared while the current one plays. Any failure hands
 *  the rest of the text to the browser voice instead of going silent. */
async function speakNeural(parts: string[], gen: number, opts: SpeakOptions): Promise<void> {
  const abort = new AbortController();
  currentAbort = abort;
  const fetchPart = (i: number) => { const p = api.speechAudio(parts[i], abort.signal); p.catch(() => undefined); return p; };
  let next = fetchPart(0);
  for (let i = 0; i < parts.length; i++) {
    try {
      const blob = await next;
      if (gen !== generation) return;
      if (i + 1 < parts.length) next = fetchPart(i + 1);
      await playBlob(blob);
    } catch {
      if (gen === generation) speakBrowser(parts.slice(i), gen, opts);
      return;
    }
    if (gen !== generation) return;
  }
  if (gen === generation) { setSpeaking(false); opts.onEnd?.(); }
}

/** True when an answer can be read aloud (local neural voice or a browser voice). */
export const canSpeak = (): boolean => neural.available || voiceSupport().synthesis;

/** Read a text aloud in French. Any previous reading is interrupted. Returns false if unsupported. */
export function speak(text: string, opts: SpeakOptions = {}): boolean {
  if (!canSpeak()) return false;
  stopSpeaking();
  const parts = chunks(speechText(text));
  const gen = generation;
  if (!parts.length) return true;
  setSpeaking(true);
  if (usesNeuralVoice(opts.voiceName)) void speakNeural(parts, gen, opts);
  else speakBrowser(parts, gen, opts);
  return true;
}

/** `done`: what happens after the last of these parts (by default the reading is over; a reading that receives its
 *  text while it is written, `speakLive`, decides by itself). */
function speakBrowser(parts: string[], gen: number, opts: SpeakOptions,
                      done: () => void = () => { setSpeaking(false); opts.onEnd?.(); }): void {
  if (!voiceSupport().synthesis) { setSpeaking(false); return; }
  const synth = window.speechSynthesis;
  const voice = chosenVoice(opts.voiceName === NEURAL_VOICE_ID ? '' : opts.voiceName);
  routedAudio = null; // the level now follows this voice's word boundaries
  parts.forEach((part, i) => {
    const u = new SpeechSynthesisUtterance(part);
    u.lang = voice?.lang ?? VOICE_LANG;
    if (voice) u.voice = voice;
    u.rate = opts.rate ?? 0.97;   // a touch slower than the default sounds less mechanical in French
    u.pitch = 1;
    const last = i === parts.length - 1;
    u.onstart = () => { lastBoundary = performance.now(); };
    u.onboundary = () => { lastBoundary = performance.now(); };
    u.onend = () => { if (last && gen === generation) done(); };
    u.onerror = () => { if (gen === generation) { setSpeaking(false); } };
    synth.speak(u);
  });
}

export function stopSpeaking(): void {
  generation++;
  if (voiceSupport().synthesis) window.speechSynthesis.cancel();
  currentAbort?.abort();
  currentAbort = null;
  currentAudio?.pause();
  currentAudio = null;
  routedAudio = null;
  setSpeaking(false);
}

/** A reading that receives its text while it is being written (11/11/2026, ID Studio IA : « la rapidité des réponses en
 *  vocal… sur Athéna on a un système » ; d'après Athéna, useNaturalVoice.annoncerSuiteVocale). The voice starts with
 *  what is there, takes the next sentences as they come, and waits for them when it has said everything before the
 *  end. Same voices, same sentence in advance and same fallback as `speak`. */
export interface LiveSpeech {
  /** More text to say, after what was already given. */
  more(text: string): void;
  /** Nothing else will come: the reading ends after what is left. */
  end(): void;
  /** False once this reading was cut (microphone, another reading, « stop »): nothing more will be said. */
  alive(): boolean;
}

export function speakLive(opts: SpeakOptions = {}): LiveSpeech | null {
  if (!canSpeak()) return null;
  stopSpeaking();
  const gen = generation;
  const parts: string[] = [];
  let closed = false, started = false, browser = !usesNeuralVoice(opts.voiceName), queued = 0;
  let wake: (() => void) | null = null;
  const rouse = () => { const w = wake; wake = null; w?.(); };
  const over = () => { if (gen === generation) { setSpeaking(false); opts.onEnd?.(); } };
  // The browser voice queues each batch itself; the last batch said ends the reading once nothing else will come.
  const toBrowser = (batch: string[]) => {
    queued++;
    speakBrowser(batch, gen, opts, () => { queued--; if (closed && queued === 0) over(); });
  };
  const neuralLoop = async () => {
    const abort = new AbortController();
    currentAbort = abort;
    const fetchPart = (i: number) => { const p = api.speechAudio(parts[i], abort.signal); p.catch(() => undefined); return p; };
    let next: Promise<Blob> | null = null;
    for (let i = 0; ;) {
      if (i >= parts.length) {
        if (closed) break;
        await new Promise<void>(resolve => { wake = resolve; });   // everything so far is said: wait for the rest
        if (gen !== generation) return;
        continue;
      }
      try {
        const blob = await (next ?? fetchPart(i));
        next = null;
        if (gen !== generation) return;
        if (i + 1 < parts.length) next = fetchPart(i + 1);
        await playBlob(blob);
      } catch {
        if (gen !== generation) return;
        browser = true;   // any failure hands the rest, and what comes later, to the browser voice
        toBrowser(parts.slice(i));
        return;
      }
      if (gen !== generation) return;
      i++;
    }
    over();
  };
  return {
    more(text: string) {
      if (gen !== generation || closed) return;
      const plain = speechText(text);
      // The first sentence leaves alone, so that the first sound comes sooner (Athéna, « premierCourt »).
      const first = started ? null : /^(.{20,}?[.!?…]+)\s+(\S.*)$/s.exec(plain);
      const batch = first ? [...chunks(first[1]), ...chunks(first[2])] : chunks(plain);
      if (!batch.length) return;
      if (!started) { started = true; setSpeaking(true); }
      if (browser) { toBrowser(batch); return; }
      const idle = parts.length === 0;
      parts.push(...batch);
      if (idle) void neuralLoop(); else rouse();
    },
    end() {
      if (closed) return;
      closed = true;
      if (gen !== generation || !started) return;
      if (browser) { if (queued === 0) over(); } else rouse();
    },
    alive: () => gen === generation,
  };
}
