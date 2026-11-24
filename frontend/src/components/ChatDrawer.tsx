// Right-hand sliding assistant ("Assistant IA · En ligne"): Chat / Fichiers / Voix tabs, compact
// conversation, composer and quick-action chips. Opened by store.chatOpen, closed with Échap.
import { useEffect, useRef, useState } from 'react';
import { Atom, Bot, Calculator, Cpu, FileText, Globe, MessageSquarePlus, Mic, Paperclip, Square, Volume2, X } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import { useStreamStatus } from '../lib/stream';
import {
  NEURAL_VOICE_ID, RECOGNITION_MODE_LABELS, chosenVoice, isNaturalVoice, neuralVoice, openMicMeter, recognitionMode,
  localWhisper, setOnlineVoicesAllowed, speak, stopSpeaking, useFrenchVoices, useRecognitionVersion, useSpeaking, usesNeuralVoice,
  voiceSupport, type MicMeter, type RecognitionMode,
} from '../lib/voice';
import ChatPanel from './ChatPanel';
import { AttachButton, Composer, Notice, ScreenButton, useNotice } from './chat/ComposerParts';
import { MAX_ATTACHMENT_BYTES, TEXT_EXTENSIONS } from './chat/attachments';
import { removeAttachment, setPrefs, useAttachments, useChatPrefs } from './chat/prefs';
import { LiveFeedBridge } from './chat/live';
import { fmtBytes } from './chat/text';
import { useSend } from './chat/useSend';
import { StatusDot } from './ui';
import '../pages/chat.css';

type Tab = 'chat' | 'files' | 'voice';

const QUICK_ACTIONS: { label: string; text: string; intent: string; icon: typeof Cpu }[] = [
  { label: 'Calculs & analyses', text: 'Convertis 1 unité astronomique en kilomètres', intent: 'calc.convert', icon: Calculator },
  { label: 'Simulations 3D', text: "Simule la trajectoire d'une sonde autour de Mars", intent: 'sim.orbit', icon: Atom },
  { label: 'Informations planétaires', text: 'Montre la position actuelle des planètes du système solaire', intent: 'sim.solar_system', icon: Globe },
  { label: 'Contrôle du PC', text: "Donne-moi l'état actuel de mon PC : processeur, mémoire, disque et réseau", intent: 'system.status', icon: Cpu },
];

function FilesTab() {
  const atts = useAttachments();
  const send = useSend();
  const [notice, setNotice] = useNotice();
  return (
    <div className="chat-drawer-tab">
      <p className="chat-help">
        Joignez des fichiers texte ({TEXT_EXTENSIONS.join(' ')}, {fmtBytes(MAX_ATTACHMENT_BYTES)} max chacun). Leur contenu est envoyé avec
        votre prochain message comme <strong>donnée</strong> : il ne peut pas donner d’ordre à AURORA. Une image (PNG, JPEG…) est décrite sur ce PC par le modèle de vision local, et c’est cette description qui est jointe.
      </p>
      <div className="chat-files-actions">
        <AttachButton onMessages={setNotice} className="btn" />
        <ScreenButton onMessages={setNotice} className="btn" />
        <button type="button" className="btn btn-primary" disabled={!atts.length} onClick={() => void send('', { source: 'text' })}>
          <FileText size={15} /> Analyser {atts.length > 1 ? 'les fichiers' : 'le fichier'}
        </button>
      </div>
      <Notice messages={notice} />
      {atts.length === 0 ? <div className="chat-empty">Aucun fichier en attente.</div> : (
        <ul className="chat-files-list">
          {atts.map(a => (
            <li key={a.id}>
              <div className="chat-files-head"><FileText size={14} /><strong>{a.name}</strong><small>{fmtBytes(a.size)} · {a.text.split('\n').length} ligne(s)</small>
                <button type="button" className="icon-btn" onClick={() => removeAttachment(a.id)} aria-label={`Retirer ${a.name}`}><X size={14} /></button>
              </div>
              {a.preview && <img className="chat-files-image" src={a.preview} alt={`Image décrite : ${a.name}`} />}
              {/* a description (E2) is shown whole: it is exactly what goes with the next message */}
              <pre className="chat-files-preview">{a.preview ? a.text : a.text.slice(0, 600)}{!a.preview && a.text.length > 600 ? '\n…' : ''}</pre>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MicTest() {
  const [level, setLevel] = useState<number | null>(null);
  const [error, setError] = useState('');
  const meter = useRef<MicMeter | null>(null);
  const raf = useRef(0);
  const timer = useRef(0);
  // False once unmounted: the microphone permission prompt can resolve after the tab was left.
  const alive = useRef(true);
  const stop = () => {
    cancelAnimationFrame(raf.current); window.clearTimeout(timer.current);
    meter.current?.close(); meter.current = null;
    if (alive.current) setLevel(null);
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; stop(); }; }, []);
  const start = async () => {
    setError('');
    try {
      const m = await openMicMeter();
      if (!alive.current) { m.close(); return; }
      meter.current = m;
      const bands = new Float32Array(8);
      const loop = () => { if (!meter.current) return; setLevel(meter.current.read(bands)); raf.current = requestAnimationFrame(loop); };
      loop();
      timer.current = window.setTimeout(() => { if (meter.current === m) stop(); }, 8000);
    } catch (err) {
      if (alive.current) setError(err instanceof Error && err.name === 'NotAllowedError' ? 'Accès au micro refusé.' : 'Micro indisponible.');
    }
  };
  return (
    <div className="chat-mic-test">
      <button type="button" className="btn btn-sm" onClick={() => (level === null ? void start() : stop())}><Mic size={14} /> {level === null ? 'Tester le micro (8 s)' : 'Arrêter le test'}</button>
      {level !== null && <div className="chat-meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(1, level * 4) * 100)} aria-label="Niveau du micro"><span style={{ width: `${Math.min(100, level * 400)}%` }} /></div>}
      {error && <span className="chat-warn">{error}</span>}
    </div>
  );
}

function VoiceTab() {
  const prefs = useChatPrefs();
  const support = voiceSupport();
  const voices = useFrenchVoices();
  const speakingNow = useSpeaking();
  const { health } = useAurora();
  const privateMode = health?.capabilities.mode === 'prive';
  const onlineOk = prefs.voiceOnline && !privateMode;
  setOnlineVoicesAllowed(onlineOk);                 // also kept in sync by App for the other readers
  const current = voices.length ? chosenVoice(prefs.voiceName) : null;
  const neural = neuralVoice();
  const [mode, setMode] = useState<RecognitionMode>(support.recognition ? 'browser' : 'unsupported');
  const recognitionVersion = useRecognitionVersion();
  useEffect(() => { void recognitionMode().then(setMode); }, [recognitionVersion]);
  // Voix D3: what the service says about Athéna's Whisper (what is missing, when it is)
  const [whisperMissing, setWhisperMissing] = useState<string | null>(null);
  useEffect(() => {
    api.recognitionStatus().then(s => setWhisperMissing(s.available ? '' : s.reason)).catch(() => setWhisperMissing('service injoignable'));
  }, [recognitionVersion]);
  return (
    <div className="chat-drawer-tab">
      <dl className="chat-kv chat-voice-status">
        <div><dt>Reconnaissance (fr-FR)</dt><dd><StatusDot ok={support.recognition} /> {RECOGNITION_MODE_LABELS[mode]}</dd></div>
        <div><dt>Synthèse vocale</dt><dd><StatusDot ok={support.synthesis} /> {support.synthesis ? `${voices.length} voix française(s)` : 'non prise en charge'}</dd></div>
        <div><dt>Micro</dt><dd><StatusDot ok={support.microphone} /> {support.microphone ? 'accessible sur demande' : 'inaccessible dans ce contexte'}</dd></div>
      </dl>
      {whisperMissing === '' ? (
        <>
          <label className="chat-toggle">
            <input type="checkbox" checked={prefs.whisper} onChange={e => setPrefs({ whisper: e.target.checked })} />
            Reconnaître la voix sur ce PC (Whisper)
          </label>
          <p className="chat-help">{localWhisper().preferred || mode === 'whisper'
            ? 'Le son reste sur ce PC : le service Whisper d’Athéna (modèle « base », processeur) le lit quand tu te tais. Son français est moins sûr que celui du navigateur : AURORA te montre toujours la phrase avant l’envoi.'
            : 'Coché, le son ne part plus au service en ligne du navigateur : Whisper (modèle « base », processeur) le lit sur ce PC. Son français est moins sûr. En mode Privé, il est utilisé de lui-même.'}</p>
        </>
      ) : whisperMissing !== null && (
        <p className="chat-help">Whisper sur ce PC : indisponible ({whisperMissing}).</p>
      )}
      {mode === 'browser' && (
        <p className="chat-help">La reconnaissance du navigateur peut transmettre l’audio au service en ligne de son éditeur (Google pour Chrome, Microsoft pour Edge). Le reste du traitement reste local par défaut.</p>
      )}
      {/* No wake word: recognition starts on a click on the microphone, for one utterance (lib/voice.ts). One text,
          the service's (capabilities.voice_hint, B7); the fallback only covers an older or unreachable service. */}
      {support.recognition && <p className="chat-help">{health?.capabilities.voice_hint || 'Cliquez sur le micro (barre du bas) puis dites votre demande ; le mot « Aurora » en tête est facultatif.'}</p>}
      <label className="chat-toggle">
        <input type="checkbox" checked={prefs.tts} disabled={!support.synthesis} onChange={e => { if (!e.target.checked) stopSpeaking(); setPrefs({ tts: e.target.checked }); }} />
        Lire les réponses à voix haute
      </label>
      {support.synthesis && (
        <>
          <label className="chat-toggle" title={privateMode ? 'Mode privé : aucune voix en ligne.' : undefined}>
            <input type="checkbox" checked={prefs.voiceOnline && !privateMode} disabled={privateMode}
              onChange={e => setPrefs({ voiceOnline: e.target.checked })} />
            Autoriser les voix en ligne, plus naturelles
          </label>
          <p className="chat-help">
            {privateMode
              ? 'Mode privé : seules les voix installées sur ce PC sont utilisées.'
              : 'Le texte lu est alors envoyé au service vocal de l’éditeur du navigateur (Microsoft pour Edge, Google pour Chrome). Sinon, seules les voix installées sur ce PC sont utilisées. Les voix les plus naturelles sont « Google français » dans Chrome et les voix « Natural » de Microsoft dans Edge.'}
          </p>
          <label className="field">Voix
            <select className="select" value={prefs.voiceName} onChange={e => setPrefs({ voiceName: e.target.value })}>
              <option value="">Automatique : la plus naturelle autorisée</option>
              {neural.available && <option value={NEURAL_VOICE_ID}>AURORA · voix neuronale locale (Piper, {neural.name})</option>}
              {voices.map(v => (
                <option key={v.name} value={v.name} disabled={!v.localService && !onlineOk}>
                  {v.name}{isNaturalVoice(v) ? ' · naturelle' : ''}{v.localService ? ' (locale)' : ' (en ligne)'}
                </option>
              ))}
            </select>
          </label>
          <p className="chat-help">Voix utilisée : {usesNeuralVoice(prefs.voiceName)
            ? `AURORA · voix neuronale locale (Piper, ${neural.name}), générée sur ce PC`
            : current ? `${current.name}${current.localService ? ' (locale)' : ' (en ligne)'}` : 'aucune voix française disponible'}.</p>
        </>
      )}
      <div className="chat-files-actions">
        {support.synthesis && (speakingNow
          ? <button type="button" className="btn btn-sm" onClick={stopSpeaking}><Square size={13} /> Arrêter la lecture</button>
          : <button type="button" className="btn btn-sm" onClick={() => speak('Bonjour, je suis AURORA. Voici ma voix de lecture.', { voiceName: prefs.voiceName })}><Volume2 size={14} /> Tester la voix</button>)}
      </div>
      {support.microphone && <MicTest />}
    </div>
  );
}

export default function ChatDrawer() {
  const { chatOpen, setChatOpen, online, newConversation, chat } = useAurora();
  const live = useStreamStatus();
  const send = useSend();
  const atts = useAttachments();
  const [tab, setTab] = useState<Tab>('chat');
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!chatOpen) return;
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape' && !e.defaultPrevented) setChatOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [chatOpen, setChatOpen]);

  const tabs: { id: Tab; label: string; icon: typeof Bot }[] = [
    { id: 'chat', label: 'Chat', icon: Bot },
    { id: 'files', label: `Fichiers${atts.length ? ` (${atts.length})` : ''}`, icon: Paperclip },
    { id: 'voice', label: 'Voix', icon: Mic },
  ];

  return (
    <div className="chat-drawer-clip">
    <LiveFeedBridge />
    <aside ref={panel} className={`chat-drawer ${chatOpen ? 'chat-drawer-open' : ''}`} aria-label="Assistant IA" aria-hidden={!chatOpen} inert={!chatOpen}>
      <header className="chat-drawer-head">
        <span className="chat-drawer-avatar"><Bot size={20} /></span>
        <div className="chat-drawer-titles">
          <strong>Assistant IA</strong>
          <span><StatusDot ok={online} warn={online && !live} /> {online ? (live ? 'En ligne' : 'En ligne · flux temps réel déconnecté') : 'Hors ligne'}</span>
        </div>
        <button type="button" className="icon-btn" onClick={newConversation} disabled={!chat.length} title="Nouvelle conversation" aria-label="Nouvelle conversation"><MessageSquarePlus size={18} /></button>
        <button type="button" className="icon-btn" onClick={() => setChatOpen(false)} title="Fermer (Échap)" aria-label="Fermer l’assistant"><X size={18} /></button>
      </header>
      <div className="chat-tabs" role="tablist" aria-label="Sections de l’assistant">
        {tabs.map(t => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={`chat-tab ${tab === t.id ? 'active' : ''}`} onClick={() => setTab(t.id)}>
            <t.icon size={14} /> {t.label}
          </button>
        ))}
      </div>
      <div className="chat-drawer-body">
        {tab === 'chat' && (
          <>
            <ChatPanel compact className="chat-drawer-panel" />
            <div className="chat-quick" aria-label="Actions rapides">
              {QUICK_ACTIONS.map(q => (
                <button key={q.label} type="button" className="chat-quick-chip" title={q.text} onClick={() => void send(q.text, { source: 'panel', intent: q.intent, withAttachments: false })}>
                  <q.icon size={14} /> {q.label}
                </button>
              ))}
            </div>
            <Composer />
          </>
        )}
        {tab === 'files' && <FilesTab />}
        {tab === 'voice' && <VoiceTab />}
      </div>
    </aside>
    </div>
  );
}
