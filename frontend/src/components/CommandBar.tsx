// Bottom command bar: glowing pill with the microphone in its centre, a waveform on each side
// (real level of the microphone while listening, of AURORA's voice while she speaks), keyboard
// toggle, attachments, send, voice output and interruption. Text, voice and buttons all end in store.ask.
import { useEffect, useRef, useState, type KeyboardEvent, type MutableRefObject } from 'react';
import { AudioLines, Check, CircleStop, Keyboard, MessageSquare, Mic, MicOff, Pencil, SendHorizontal, Volume2, VolumeX, X } from 'lucide-react';
import { api } from '../lib/api';
import { corrigerDictee, douteDictee, reponseConfirmation } from '../lib/dictee';
import { useAurora } from '../lib/store';
import {
  RECOGNITION_MODE_LABELS, canSpeak, openMicMeter, recognitionMode, recognitionRefusal, speak, speakLive, speechMeter, startListening, stopSpeaking,
  useRecognitionVersion, useSpeaking, voiceSupport,
  type HeardInfo, type LevelMeter, type ListenSession, type LiveSpeech, type MicMeter, type RecognitionMode,
} from '../lib/voice';
import { debutPrononcable, finDesPhrasesEcrites, resteADire } from '../lib/voixFil';
import { AttachButton, AttachmentChips, EngineSelect, Notice, Photo3dButton, ScreenButton, useNotice } from './chat/ComposerParts';
import { activeRunId, claimRun, partialSpeakable, partialText, useLiveFeed } from './chat/live';
import { setPrefs, useAttachments, useChatPrefs } from './chat/prefs';
import { markdownToPlain } from './chat/text';
import { useSend } from './chat/useSend';
import '../pages/chat.css';

const BARS = 28;

/** The sentence heard, shown before it is sent (voix D1, 9/11/2026): corrected by Athéna's list, and with the
 *  reason for a doubt when it seems misheard (dictee.ts). */
interface Entendu { texte: string; corrections: string[]; doute: string | null }
/** A sentence without doubt is shown this long, with « Envoyer », « Corriger » and « Ne pas envoyer », then sent. */
const DELAI_RELECTURE_MS = 1500;

/** Waveform canvas: animated only while `active` and fed by a real analyser; flat otherwise.
 *  One on each side of the microphone, the strongest (lowest) bands next to it. */
function Waveform({ meter, active, side }: { meter: MutableRefObject<LevelMeter | null>; active: boolean; side: 'left' | 'right' }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const ctx = el.getContext('2d');
    if (!ctx) return;
    const bands = new Float32Array(BARS);
    let raf = 0;
    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      el.width = Math.max(1, Math.round(el.clientWidth * dpr));
      el.height = Math.max(1, Math.round(el.clientHeight * dpr));
    };
    const draw = () => {
      const w = el.width, h = el.height;
      ctx.clearRect(0, 0, w, h);
      const m = active ? meter.current : null;
      if (m) m.read(bands); else bands.fill(0);
      const grad = ctx.createLinearGradient(0, 0, w, 0);
      const stops = ['#8a5cff', '#2f7bff', '#22c8ff'];
      if (side === 'right') stops.reverse();
      stops.forEach((c, i) => grad.addColorStop(i / 2, c));
      ctx.fillStyle = grad;
      // about one bar every 6 CSS pixels, so a narrow waveform stays bars and not a solid block
      const n = Math.max(5, Math.min(BARS, Math.floor(el.clientWidth / 6)));
      const gap = w / n;
      const bw = Math.max(1.5, gap * 0.48);
      for (let i = 0; i < n; i++) {
        // lowest bands (most speech energy) next to the microphone
        const idx = side === 'left' ? (n - 1 - i) / (n - 1) : i / (n - 1);
        const v = bands[Math.min(BARS - 1, Math.floor(idx * (BARS - 1)))];
        const bh = Math.max(h * 0.06, Math.min(1, v * 1.7) * h * 0.92);
        const x = i * gap + (gap - bw) / 2;
        ctx.globalAlpha = m ? 0.95 : 0.35;
        ctx.fillRect(x, (h - bh) / 2, bw, bh);
      }
      ctx.globalAlpha = 1;
      if (active) raf = requestAnimationFrame(draw);
    };
    resize();
    draw();
    const ro = new ResizeObserver(() => { resize(); if (!active) draw(); });
    ro.observe(el);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, [active, meter, side]);
  return <canvas ref={canvas} className={`cmd-wave ${active ? 'cmd-wave-live' : ''}`} aria-hidden="true" />;
}

/** La réponse en attente que la voix lit déjà : ce qui lui a été donné (`dit`, le début du texte écrit, tel quel). */
interface FilVocal { id: string; voix: LiveSpeech; dit: string }

/** La voix au fil de l'écriture (11/11/2026, ID Studio IA : « la rapidité des réponses en vocal… sur Athéna on a un système qui
 *  améliore le temps des réponses »). AURORA ne parlait qu'une fois la réponse entière écrite et vérifiée. Ici, dès
 *  que deux phrases sont écrites, elles sont dites ; les suivantes suivent, phrase par phrase (lib/voixFil.ts, les
 *  règles d'Athéna). Seulement quand la lecture vocale est allumée et que le service le permet (`speak` : jamais pour
 *  une réponse qui peut montrer une donnée sensible, ni quand un contrôle retient la réponse). La réponse finale fait
 *  foi : CommandBar lui donne la suite, ou la lit à la place si ce n'est pas celle qui s'écrivait.
 *  Ne rend rien : seul ce composant est redessiné à chaque morceau de texte reçu. */
function VoixAuFil({ fil }: { fil: MutableRefObject<FilVocal | null> }) {
  const { chat } = useAurora();
  const prefs = useChatPrefs();
  const recu = useLiveFeed(prefs.tts);
  useEffect(() => {
    if (!prefs.tts) return;
    let i = chat.length - 1;
    while (i >= 0 && !(chat[i].role === 'assistant' && chat[i].pending)) i--;
    if (i < 1 || chat[i - 1].role !== 'user') return;
    const e = chat[i];
    const runId = claimRun(e.id, chat[i - 1].text, Date.parse(e.at));
    if (!partialSpeakable(runId)) return;
    const ecrit = partialText(runId);
    if (!ecrit || ecrit.length > 18_000) return;   // au-delà, le service n'envoie plus le début du texte
    const s = fil.current?.id === e.id ? fil.current : null;
    if (!s) {
      const debut = debutPrononcable(ecrit);
      const voix = debut ? speakLive({ voiceName: prefs.voiceName }) : null;
      if (!debut || !voix) return;
      voix.more(markdownToPlain(debut));
      fil.current = { id: e.id, voix, dit: debut };
      return;
    }
    if (!s.voix.alive()) return;                   // coupée (micro, arrêt) : plus rien n'est dit d'avance
    if (!ecrit.startsWith(s.dit)) {                // un autre texte s'écrit (un autre moteur a pris le relais)
      stopSpeaking();
      fil.current = null;
      return;
    }
    const fin = finDesPhrasesEcrites(ecrit);
    if (fin > s.dit.length) {
      s.voix.more(markdownToPlain(ecrit.slice(s.dit.length, fin)));
      s.dit = ecrit.slice(0, fin);
    }
  }, [recu, chat, prefs.tts, prefs.voiceName, fil]);
  return null;
}

export default function CommandBar() {
  const { chat, busy, applyRunUpdate, chatOpen, setChatOpen, page, health } = useAurora();
  const send = useSend();
  const prefs = useChatPrefs();
  const atts = useAttachments();
  const speakingNow = useSpeaking();
  const support = voiceSupport();
  const [notice, setNotice] = useNotice(7000);
  const [text, setText] = useState('');
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState('');
  const [mode, setMode] = useState<RecognitionMode>(support.recognition ? 'browser' : 'unsupported');
  const [voiceWaiting, setVoiceWaiting] = useState(false);
  const [envoye, setEnvoye] = useState('');               // the sentence being processed, shown in the pill
  const [entendu, setEntenduState] = useState<Entendu | null>(null);
  const entenduRef = useRef<Entendu | null>(null);
  const setEntendu = (e: Entendu | null) => { entenduRef.current = e; setEntenduState(e); };
  const relecture = useRef(0);
  const apresEcoute = useRef<(() => void) | null>(null);  // run once the current listening has really ended
  const [meterOff, setMeterOff] = useState('');
  const session = useRef<ListenSession | null>(null);
  const meter = useRef<MicMeter | null>(null);
  const speechRef = useRef<LevelMeter | null>(speechMeter);
  const listeningRef = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const inputMode = prefs.inputMode ?? (support.recognition ? 'voice' : 'keyboard');

  const recognitionVersion = useRecognitionVersion();   // Whisper found or chosen, mode changed: re-read
  useEffect(() => { void recognitionMode().then(setMode); }, [recognitionVersion]);
  useEffect(() => () => { window.clearTimeout(relecture.current); session.current?.abort(); meter.current?.close(); }, []);

  // Read finished answers aloud when voice output is on (only answers that arrive from now on).
  // The chat stays closed (opened by hand), except for an authorisation request: it must be seen to be answered.
  const spoken = useRef<Set<string> | null>(null);
  const fil = useRef<FilVocal | null>(null);   // the answer the voice already reads while it is written (VoixAuFil)
  useEffect(() => {
    const keyOf = (id: string, status: string) => `${id}:${status}`;
    if (spoken.current === null) {
      spoken.current = new Set(chat.map(e => keyOf(e.id, e.run?.status ?? '')));
      return;
    }
    for (const e of chat) {
      if (e.role !== 'assistant' || e.pending) continue;
      const k = keyOf(e.id, e.run?.status ?? '');
      if (spoken.current.has(k)) continue;
      spoken.current.add(k);
      const permission = e.run?.status === 'waiting_permission';
      const enCours = fil.current?.id === e.id ? fil.current : null;
      if (enCours) fil.current = null;
      // Shown instead of spoken: an authorisation request, or an answer holding sensitive data.
      if ((permission || e.run?.shows_sensitive) && page !== 'chat') setChatOpen(true);
      if (!prefs.tts) continue;
      // 11/11/2026: the voice already said the beginning while it was being written. The final answer is the one
      // that counts: the rest of it follows, without repeating; cut by the user, nothing is read again; and when
      // it is not the text that was being written (another engine took over), it is read whole, instead.
      if (enCours && !permission && !e.run?.shows_sensitive) {
        if (!enCours.voix.alive()) continue;
        const reste = resteADire(e.text || e.run?.answer || e.run?.error || '', enCours.dit);
        if (reste !== null) {
          enCours.voix.more(markdownToPlain(reste));
          enCours.voix.end();
          continue;
        }
      }
      const say = permission
        ? `Autorisation nécessaire : ${e.run?.permission?.title ?? 'voir la demande à l’écran'}. La demande est affichée dans le chat.`
        : e.run?.shows_sensitive   // a secret is never said aloud by itself (someone may be listening)
          ? 'La réponse contient une donnée sensible : je ne la lis pas à voix haute. Elle est affichée dans le chat.'
          : (e.text || e.run?.answer || e.run?.error || '');
      if (say) speak(markdownToPlain(say), { voiceName: prefs.voiceName });
    }
  }, [chat, prefs.tts, prefs.voiceName, page, setChatOpen]);

  const endListening = () => {
    listeningRef.current = false;
    meter.current?.close();
    meter.current = null;
    session.current = null;
    setListening(false);
    setInterim('');
    const suite = apresEcoute.current;
    apresEcoute.current = null;
    suite?.();
  };

  // ---- voix D1 : the sentence heard is shown before it is sent; a doubtful one is first confirmed ----
  const oublierEntendu = () => {
    window.clearTimeout(relecture.current);
    apresEcoute.current = null;
    if (entenduRef.current?.doute) stopSpeaking();   // the question « C'est bien ça ? » may still be read
    setEntendu(null);
  };

  const envoyerEntendu = (e: Entendu) => {
    oublierEntendu();
    if (listeningRef.current) session.current?.abort();
    setEnvoye(e.texte);
    setVoiceWaiting(true);
    void send(e.texte, { source: 'voice', openDrawer: 'unless-spoken' }).finally(() => setVoiceWaiting(false));
  };

  const annulerEntendu = () => {
    oublierEntendu();
    if (listeningRef.current) session.current?.abort();
  };

  const corrigerEntendu = (e: Entendu) => {
    annulerEntendu();
    setText(e.texte);
    wantFocus.current = true;
    setPrefs({ inputMode: 'keyboard' });
    window.setTimeout(() => { if (wantFocus.current) { wantFocus.current = false; input.current?.focus(); } }, 0);
  };

  /** « J'ai compris : « … ». C'est bien ça ? », read aloud when the answers are, then the microphone listens for the
   *  answer once both the listening and the question are over. */
  const demanderConfirmation = (e: Entendu) => {
    let ecouteFinie = !listeningRef.current;
    let questionFinie = !prefs.tts;
    const suite = () => { if (ecouteFinie && questionFinie && entenduRef.current === e) void startVoice(e); };
    if (!ecouteFinie) apresEcoute.current = () => { ecouteFinie = true; suite(); };
    if (!questionFinie) {
      const lue = speak(`J’ai compris : ${e.texte}. C’est bien ça ?`, { voiceName: prefs.voiceName, onEnd: () => { questionFinie = true; suite(); } });
      if (!lue) questionFinie = true;
    }
    suite();
  };

  const recu = (brut: string, info: HeardInfo) => {
    const c = corrigerDictee(brut);
    const e: Entendu = { texte: c.texte, corrections: c.corrections, doute: douteDictee(c.texte, info.confiance) };
    window.clearTimeout(relecture.current);
    setEntendu(e);
    if (e.doute) demanderConfirmation(e);
    else relecture.current = window.setTimeout(() => { if (entenduRef.current === e) envoyerEntendu(e); }, DELAI_RELECTURE_MS);
  };

  const surReponse = (brut: string, info: HeardInfo, e: Entendu) => {
    if (entenduRef.current !== e) return;              // already sent, corrected or dropped by a click
    const r = reponseConfirmation(brut);
    if (r.reponse === 'oui') { envoyerEntendu(e); return; }
    if (r.reponse === 'non' && !r.reste) {            // nothing is sent: AURORA listens to the request again
      oublierEntendu();
      apresEcoute.current = () => void startVoice();
      return;
    }
    recu(r.reste, info);                               // the request said again, checked in its turn
  };

  /** Listens to a request, or, with `question`, to the answer to « C'est bien ça ? ». */
  const startVoice = async (question?: Entendu) => {
    if (!support.recognition || listeningRef.current) return;
    const refusal = recognitionRefusal(await recognitionMode(), health?.capabilities.mode);
    if (refusal) { setNotice([refusal]); return; }   // Private mode: the microphone is not even opened
    stopSpeaking();
    setNotice([]);
    setMeterOff('');
    listeningRef.current = true;
    setListening(true);
    openMicMeter()
      .then(m => { if (listeningRef.current) meter.current = m; else m.close(); })
      .catch(err => setMeterOff(err instanceof Error && err.name === 'NotAllowedError' ? 'micro refusé : niveau non affiché' : 'niveau du micro indisponible'));
    const s = await startListening({
      onStart: m => setMode(m),
      onInterim: t => setInterim(t),
      onFinal: (t, info) => { if (question) surReponse(t, info, question); else recu(t, info); },
      onError: (code, message) => {
        if (code === 'aborted') return;
        // no answer to « C'est bien ça ? »: the sentence waits for a click, it is never sent by itself
        setNotice([question && code === 'no-speech' ? 'Pas de réponse entendue : la phrase attend. Envoie-la, corrige-la ou laisse tomber.' : message]);
      },
      onEnd: endListening,
    });
    if (!s) endListening();
    else if (!listeningRef.current) s.abort();
    else session.current = s;
  };

  const stopVoice = () => { session.current?.stop(); };

  const submitText = () => {
    if (!text.trim() && !atts.length) return;
    const t = text;
    setText('');
    void send(t, { source: 'text', openDrawer: 'unless-spoken' });
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape' && text) { e.preventDefault(); setText(''); }
  };

  const interrupt = async () => {
    oublierEntendu();
    stopSpeaking();
    if (listeningRef.current) { session.current?.abort(); endListening(); }
    const runId = activeRunId(chat.filter(e => e.role === 'user').map(e => e.text));
    if (runId === null) {
      if (busy) setNotice(['Traitement en cours non identifié (flux temps réel déconnecté ?) : impossible de l’annuler.']);
      return;
    }
    try {
      const run = await api.cancelRun(runId);
      if (chat.some(e => e.run?.task_id === runId)) applyRunUpdate(run);
      setNotice(['Traitement interrompu.']);
    } catch (err) {
      setNotice([err instanceof Error ? `Annulation impossible : ${err.message}` : 'Annulation impossible.']);
    }
  };

  const wantFocus = useRef(false);
  useEffect(() => {
    if (inputMode === 'keyboard' && wantFocus.current) { wantFocus.current = false; input.current?.focus(); }
  }, [inputMode]);

  const toggleInput = () => {
    const next = inputMode === 'voice' ? 'keyboard' : 'voice';
    wantFocus.current = next === 'keyboard';
    setPrefs({ inputMode: next });
  };

  const showStop = speakingNow || busy || listening;
  // While AURORA speaks, the microphone cuts her voice (a second press then listens).
  const cutVoice = speakingNow && !listening;
  const micTitle = cutVoice ? 'Couper la voix d’AURORA'
    : !support.recognition ? 'Reconnaissance vocale non prise en charge par ce navigateur (essayez Chrome ou Edge).'
    : listening ? 'Arrêter l’écoute'
    : recognitionRefusal(mode, health?.capabilities.mode) ? 'Micro coupé (mode Privé ou mode inconnu) : cliquez pour savoir pourquoi'
    : `Parler à AURORA — ${RECOGNITION_MODE_LABELS[mode]}`;
  const onMic = () => {
    if (listening) stopVoice();
    else if (cutVoice) stopSpeaking();
    else { if (entenduRef.current) oublierEntendu(); void startVoice(); }   // the mic again: the sentence is said anew
  };

  // « Envoyer », « Corriger au clavier », « Ne pas envoyer » for the sentence heard (voix D1)
  const actionsEntendu = entendu && (
    <div className="cmd-heard-actions">
      <button type="button" className="cmd-heard-btn cmd-heard-ok" onClick={() => envoyerEntendu(entendu)} title="Envoyer cette phrase" aria-label="Envoyer cette phrase"><Check size={15} /></button>
      <button type="button" className="cmd-heard-btn" onClick={() => corrigerEntendu(entendu)} title="Corriger au clavier" aria-label="Corriger au clavier"><Pencil size={14} /></button>
      <button type="button" className="cmd-heard-btn" onClick={annulerEntendu} title="Ne pas envoyer" aria-label="Ne pas envoyer"><X size={15} /></button>
    </div>
  );
  // shorter on screen than aloud (« J'ai compris : … »), so that the sentence itself stays visible in a narrow bar
  const question = entendu?.doute ? `« ${entendu.texte} » : c’est bien ça ?` : '';
  const corrige = entendu?.corrections.length ? ` · corrigé : ${entendu.corrections.join(', ')}` : '';

  // Both waveforms show what is heard (microphone) or said (AURORA's voice), measured, never invented.
  const waveMeter = listening ? meter : speechRef;
  const waveLive = listening || speakingNow;

  let pill;
  if (listening) {
    pill = (
      <div className={`cmd-pill-voice cmd-pill-main ${question ? 'cmd-heard cmd-heard-doute' : ''}`}>
        <span className="cmd-rec-dot" aria-hidden="true" />
        <div className="cmd-pill-text" aria-live={question ? 'polite' : undefined}>
          <strong title={question || undefined}>{question || 'Écoute en cours…'}</strong>
          <span>{interim || (question ? 'Réponds « oui » ou « non », ou redis ta demande' : mode === 'local' ? 'Reconnaissance sur l’appareil' : mode === 'whisper' ? 'Whisper sur ce PC : le texte arrive quand tu te tais' : 'Reconnaissance du navigateur (service en ligne possible)')}{meterOff ? ` · ${meterOff}` : ''}</span>
        </div>
        {question ? actionsEntendu : <Waveform meter={waveMeter} active side="left" />}
      </div>
    );
  } else if (entendu) {
    pill = (
      <div className={`cmd-pill-voice cmd-pill-main cmd-heard ${entendu.doute ? 'cmd-heard-doute' : ''}`}>
        <span className={`cmd-rec-dot ${entendu.doute ? 'cmd-rec-doute' : 'cmd-rec-busy'}`} aria-hidden="true" />
        <div className="cmd-pill-text" aria-live="polite">
          <strong title={question || entendu.texte}>{question || `« ${entendu.texte} »`}</strong>
          <span title={(entendu.doute ?? '') + corrige}>{entendu.doute ? `Doute : ${entendu.doute}` : 'Envoi dans un instant…'}{corrige}</span>
        </div>
        {!entendu.doute && <i key={entendu.texte} className="cmd-heard-timer" style={{ animationDuration: `${DELAI_RELECTURE_MS}ms` }} aria-hidden="true" />}
        {actionsEntendu}
      </div>
    );
  } else if (inputMode === 'keyboard') {
    pill = (
      <form className="cmd-pill-form cmd-pill-main" onSubmit={e => { e.preventDefault(); submitText(); }}>
        <input ref={input} className="cmd-input" value={text} onChange={e => setText(e.target.value)} onKeyDown={onKey} maxLength={12000}
          placeholder="Dites quelque chose ou tapez votre demande…" aria-label="Demande pour AURORA" />
      </form>
    );
  } else {
    pill = (
      <button type="button" className="cmd-pill-voice cmd-pill-idle cmd-pill-main" onClick={() => (support.recognition ? void startVoice() : toggleInput())}>
        <span className={`cmd-rec-dot ${voiceWaiting ? 'cmd-rec-busy' : 'cmd-rec-off'}`} aria-hidden="true" />
        <div className="cmd-pill-text">
          <strong>{voiceWaiting ? 'Traitement de votre demande…' : speakingNow ? 'Lecture de la réponse…' : !support.recognition ? 'Voix non disponible' : recognitionRefusal(mode, health?.capabilities.mode) ? 'Écrivez votre demande' : 'Appuyez sur le micro et parlez'}</strong>
          <span>{voiceWaiting && envoye ? `« ${envoye} »` : cutVoice ? 'Appuyez sur le micro pour la couper' : !support.recognition ? 'Ce navigateur ne prend pas en charge la reconnaissance vocale : utilisez le clavier.' : recognitionRefusal(mode, health?.capabilities.mode) ? (health?.capabilities.mode === 'prive' ? 'Mode Privé : micro coupé (reconnaissance en ligne)' : 'Micro coupé : mode d’AURORA inconnu') : RECOGNITION_MODE_LABELS[mode]}</span>
        </div>
        <Waveform meter={waveMeter} active={waveLive} side="left" />
      </button>
    );
  }

  return (
    <div className={`cmd-bar-wrap ${chatOpen ? 'cmd-beside-drawer' : ''}`}>
      <VoixAuFil fil={fil} />
      {(atts.length > 0 || notice.length > 0) && (
        <div className="cmd-above"><AttachmentChips /><Notice messages={notice} /></div>
      )}
      <div className={`cmd-bar ${listening ? 'cmd-listening' : ''} ${speakingNow && !listening ? 'cmd-speaking' : ''}`}>
        <div className="cmd-pill">
          <div className="cmd-half cmd-half-start"><EngineSelect compact />{pill}</div>
          <button type="button" className={`cmd-mic ${listening ? 'cmd-mic-on' : ''}`} onClick={onMic}
            disabled={!support.recognition && !cutVoice} title={micTitle} aria-label={micTitle} aria-pressed={listening}>
            {support.recognition || cutVoice ? <Mic size={30} /> : <MicOff size={30} />}
          </button>
          <div className="cmd-half cmd-half-end">
            <Waveform meter={waveMeter} active={waveLive} side="right" />
            <div className="cmd-pill-actions">
              {page !== 'chat' && (
                <button type="button" className={`cmd-icon-btn ${chatOpen ? 'cmd-on' : ''}`} onClick={() => setChatOpen(!chatOpen)}
                  title={chatOpen ? 'Fermer le chat' : 'Ouvrir le chat (les réponses y sont écrites)'} aria-label={chatOpen ? 'Fermer le chat' : 'Ouvrir le chat'} aria-pressed={chatOpen}>
                  <MessageSquare size={18} />
                </button>
              )}
              <AttachButton onMessages={setNotice} />
              <ScreenButton onMessages={setNotice} />
              <Photo3dButton onMessages={setNotice} />
              {(inputMode === 'keyboard' || atts.length > 0) && (
                <button type="button" className="cmd-icon-btn cmd-send-btn" onClick={submitText} disabled={!text.trim() && !atts.length}
                  title="Envoyer" aria-label="Envoyer"><SendHorizontal size={18} /></button>
              )}
              <button type="button" className="cmd-icon-btn" onClick={toggleInput} title={inputMode === 'voice' ? 'Passer au clavier' : 'Passer à la voix'} aria-label={inputMode === 'voice' ? 'Passer au clavier' : 'Passer à la voix'}>
                {inputMode === 'voice' ? <Keyboard size={18} /> : <AudioLines size={18} />}
              </button>
              <button type="button" className={`cmd-icon-btn ${prefs.tts ? 'cmd-on' : ''}`} disabled={!canSpeak()} onClick={() => { if (prefs.tts) stopSpeaking(); setPrefs({ tts: !prefs.tts }); }}
                title={!canSpeak() ? 'Synthèse vocale non prise en charge' : prefs.tts ? 'Lecture des réponses activée' : 'Lire les réponses à voix haute'} aria-pressed={prefs.tts} aria-label="Lecture vocale des réponses">
                {prefs.tts ? <Volume2 size={18} /> : <VolumeX size={18} />}
              </button>
              {showStop && (
                <button type="button" className="cmd-icon-btn cmd-stop" onClick={() => void interrupt()} title="Interrompre (voix et traitement en cours)" aria-label="Interrompre">
                  <CircleStop size={18} />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
