// One send path for the command bar, the drawer, quick actions and suggestions: it always ends
// in store.ask (POST /api/ask), with the viewer's engine preference and pending attachments.
import { useCallback } from 'react';
import { useAurora } from '../../lib/store';
import type { AskInput, AskOptions, RunResult } from '../../lib/types';
import { canSpeak } from '../../lib/voice';
import { askOptions, getPrefs, restoreAttachments, takeAttachments } from './prefs';

export interface SendArgs {
  source?: AskInput['source'];
  intent?: string;
  /** Open the chat drawer unless the chat page is already shown. 'unless-spoken': only when the answer
   *  will not be read aloud (voice reading off or unavailable); otherwise the viewer opens the chat. */
  openDrawer?: boolean | 'unless-spoken';
  /** Include the attachments waiting in the composer (default true). */
  withAttachments?: boolean;
}

export function useSend(): (text: string, args?: SendArgs) => Promise<RunResult | null> {
  const { ask, setChatOpen, page } = useAurora();
  return useCallback(async (text: string, args: SendArgs = {}) => {
    const atts = args.withAttachments === false ? [] : takeAttachments();
    let clean = text.trim();
    const extra: AskOptions = args.intent ? { intent: args.intent } : {};
    if (!clean && atts.length) {
      clean = `Analyse ${atts.length > 1 ? 'ces documents' : 'ce document'} : ${atts.map(a => a.name).join(', ')}`;
      extra.intent = extra.intent ?? 'doc.analyze';
    }
    if (!clean) { restoreAttachments(atts); return null; }
    const open = args.openDrawer === 'unless-spoken' ? !(getPrefs().tts && canSpeak()) : !!args.openDrawer;
    if (open && page !== 'chat') setChatOpen(true);
    const run = await ask(clean, {
      source: args.source ?? 'text',
      options: askOptions(extra),
      attachments: atts.length ? atts.map(a => ({ name: a.name, text: a.text })) : undefined,
    });
    if (!run) restoreAttachments(atts);
    return run;
  }, [ask, setChatOpen, page]);
}
