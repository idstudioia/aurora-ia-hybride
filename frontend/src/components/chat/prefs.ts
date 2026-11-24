// Per-viewer chat preferences (engine choice, voice output, input mode) and the attachments
// waiting to be sent. Shared by the command bar, the drawer and the chat page through a tiny
// external store; preferences are remembered in localStorage when it is available.
import { useSyncExternalStore } from 'react';
import type { AskOptions } from '../../lib/types';

export type EnginePref = 'auto' | 'local' | 'cloud';
export type TierPref = '' | 'rapide' | 'standard' | 'expert';
export type InputMode = 'voice' | 'keyboard';
/** « Photo vers 3D » (11/11/2026): which local engine is tried first — Pixal3D (detailed, about 3 minutes) or
 *  TripoSR (fast, about 35 s). The other one stays the way out. The words are those of the service (`choix`). */
export type Photo3dChoice = 'detaille' | 'rapide';

export interface ChatPrefs {
  engine: EnginePref;
  tier: TierPref;
  tts: boolean;
  voiceName: string;
  /** Online voices (Edge « Natural », Google) sound more human but send the text read aloud to the
   *  browser vendor: off by default, never used in Private mode (CLAUDE.md, local by default). */
  voiceOnline: boolean;
  /** Voix D3: recognise the voice on this PC with Whisper even when the browser could listen (its French is
   *  weaker, but the audio never leaves the PC). Off: Whisper only where the browser cannot listen (Private mode). */
  whisper: boolean;
  inputMode: InputMode | null;   // null = not chosen yet (depends on voice support)
  photo3d: Photo3dChoice;
  /** P1: the composer sends to the multi-step agent (intent agent.run). Off by default and never
   *  restored at reload: each session starts without the agent. */
  agent: boolean;
}

/** preview: the image an attachment's text describes (E2), shown to the person only, never sent. */
export interface PendingAttachment { id: string; name: string; text: string; size: number; preview?: string }

const KEY = 'aurora.chat.prefs.v1';
const DEFAULTS: ChatPrefs = { engine: 'auto', tier: '', tts: true, voiceName: '', voiceOnline: false, whisper: false, inputMode: null, photo3d: 'detaille', agent: false };

function load(): ChatPrefs {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS };
    const p = JSON.parse(raw) as Partial<ChatPrefs>;
    return {
      engine: p.engine === 'local' || p.engine === 'cloud' ? p.engine : 'auto',
      tier: p.tier === 'rapide' || p.tier === 'standard' || p.tier === 'expert' ? p.tier : '',
      tts: p.tts !== false, // answers read aloud unless the viewer turned it off
      voiceName: typeof p.voiceName === 'string' ? p.voiceName : '',
      voiceOnline: p.voiceOnline === true,
      whisper: p.whisper === true,
      inputMode: p.inputMode === 'voice' || p.inputMode === 'keyboard' ? p.inputMode : null,
      photo3d: p.photo3d === 'rapide' ? 'rapide' : 'detaille',   // the detailed engine unless the viewer chose the fast one
      agent: false,
    };
  } catch {
    return { ...DEFAULTS };
  }
}

let prefs: ChatPrefs = typeof window === 'undefined' ? { ...DEFAULTS } : load();
let attachments: PendingAttachment[] = [];
const subs = new Set<() => void>();
const notify = () => subs.forEach(fn => fn());
const subscribe = (fn: () => void) => { subs.add(fn); return () => { subs.delete(fn); }; };

export function setPrefs(fields: Partial<ChatPrefs>): void {
  prefs = { ...prefs, ...fields };
  try { window.localStorage.setItem(KEY, JSON.stringify({ ...prefs, agent: undefined })); } catch { /* stockage indisponible */ }
  notify();
}

export function getPrefs(): ChatPrefs { return prefs; }

export function useChatPrefs(): ChatPrefs {
  return useSyncExternalStore(subscribe, () => prefs, () => prefs);
}

/** Options for /api/ask built from the viewer's engine preference, merged with explicit ones. */
export function askOptions(extra: AskOptions = {}, p: ChatPrefs = prefs): AskOptions {
  const out: AskOptions = { engine: p.engine };
  if (p.engine !== 'local' && p.tier) out.cloud_tier = p.tier;
  return { ...out, ...extra };
}

// ---- attachments waiting for the next message ----
export const MAX_ATTACHMENTS = 5;

export function useAttachments(): PendingAttachment[] {
  return useSyncExternalStore(subscribe, () => attachments, () => attachments);
}

export function addAttachment(a: PendingAttachment): void {
  attachments = [...attachments.filter(x => x.name !== a.name), a].slice(-MAX_ATTACHMENTS);
  notify();
}

export function removeAttachment(id: string): void {
  attachments = attachments.filter(a => a.id !== id);
  notify();
}

/** Take (and clear) the pending attachments; `restoreAttachments` puts them back after a failed send. */
export function takeAttachments(): PendingAttachment[] {
  const out = attachments;
  if (out.length) { attachments = []; notify(); }
  return out;
}

export function restoreAttachments(list: PendingAttachment[]): void {
  if (!list.length) return;
  attachments = [...list, ...attachments].slice(-MAX_ATTACHMENTS);
  notify();
}
