// Building blocks shared by the command bar, the drawer composer and the chat page:
// engine selector, attachment button / chips and a compact text composer.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Bot, Box, FileText, Gem, Paperclip, ScreenShare, SendHorizontal, Square, X, Zap } from 'lucide-react';
import { useAurora } from '../../lib/store';
import { Spinner } from '../ui';
import { ACCEPT_ATTR, MODEL_EXTENSIONS, TEXT_EXTENSIONS, attachFiles, attachScreen, isDocument, isImage, isModel3d, photoTo3d } from './attachments';
import { AGENT_HELP, AGENT_INTENT } from './labels';
import { removeAttachment, setPrefs, useAttachments, useChatPrefs, type EnginePref, type Photo3dChoice, type TierPref } from './prefs';
import { fmtBytes } from './text';
import { useSend } from './useSend';

export function EngineSelect({ compact = false }: { compact?: boolean }) {
  const prefs = useChatPrefs();
  const { health } = useAurora();
  const caps = health?.capabilities;
  const cloudReady = !!caps?.cloud.configured;
  const localReady = caps ? caps.ollama.up : true;
  return (
    <div className={`cmd-engine ${compact ? 'cmd-engine-compact' : ''}`} role="group" aria-label="Choix du moteur">
      <select className="cmd-select" value={prefs.engine} onChange={e => setPrefs({ engine: e.target.value as EnginePref })}
        aria-label="Moteur" title="Auto : l’IA locale d’abord, Claude seulement si elle ne peut pas répondre (selon le mode). Local : Ollama uniquement. Claude : cloud Anthropic directement.">
        <option value="auto">Auto</option>
        <option value="local">{localReady ? 'Local' : 'Local (Ollama indisponible)'}</option>
        <option value="cloud" disabled={!cloudReady}>{cloudReady ? 'Claude' : 'Claude (non configuré)'}</option>
      </select>
      {prefs.engine !== 'local' && (
        <select className="cmd-select" value={prefs.tier} onChange={e => setPrefs({ tier: e.target.value as TierPref })}
          aria-label="Palier Claude" title={cloudReady ? 'Palier utilisé quand Claude répond (choisi, ou en secours de l’IA locale) ; il ne fait pas passer Claude en premier' : 'Claude non configuré : le palier ne sera utilisé que si une clé est ajoutée côté serveur'}>
          <option value="">Palier par défaut (Sonnet)</option>
          <option value="rapide">Rapide (Haiku)</option>
          <option value="standard">Standard (Sonnet)</option>
          {/* Opus is reserved to the Avancé mode (CLAUDE.md); the server lowers it to Sonnet otherwise. */}
          <option value="expert" disabled={caps?.mode !== 'avance'}>
            {caps?.mode === 'avance' ? 'Expert (Opus)' : 'Expert (Opus, mode Avancé uniquement)'}
          </option>
        </select>
      )}
    </div>
  );
}

/** P1: the composer's next requests go to the multi-step agent (intent agent.run). Off by default. */
export function AgentToggle() {
  const { agent } = useChatPrefs();
  return (
    <label className={`chat-toggle chat-agent-toggle ${agent ? 'on' : ''}`} title={AGENT_HELP}>
      <input type="checkbox" checked={agent} onChange={e => setPrefs({ agent: e.target.checked })} />
      <Bot size={14} aria-hidden="true" /> Agent
    </label>
  );
}

export function AttachButton({ onMessages, className = 'cmd-icon-btn' }: { onMessages: (m: string[]) => void; className?: string }) {
  const input = useRef<HTMLInputElement>(null);
  const { showScene } = useAurora();
  // 23/11/2026: a PDF made of images is read page by page by the local vision model (20 to 40 s a page). While it
  // is read, this button stops the reading; the pages already read are joined.
  const [stop, setStop] = useState<(() => void) | null>(null);
  return (
    <>
      <button type="button" className={className} onClick={() => (stop ? stop() : input.current?.click())} aria-busy={!!stop}
        title={stop ? 'Arrêter la lecture du PDF : les pages déjà lues seront jointes' : `Joindre un PDF, un Word (.docx, 15 Mo max), une image (décrite sur ce PC) ou un fichier texte (${TEXT_EXTENSIONS.join(' ')}, 200 Ko max). Un PDF fait d’images (scanné) est lu page par page sur ce PC, 20 à 40 s par page. Un modèle 3D (${MODEL_EXTENSIONS.join(' ')}, 64 Mo max) s’ouvre dans le laboratoire de l’accueil.`}
        aria-label={stop ? 'Arrêter la lecture du PDF' : 'Joindre un fichier'}>
        {stop ? <Square size={15} /> : <Paperclip size={18} />}
      </button>
      <input ref={input} type="file" hidden multiple accept={ACCEPT_ATTR}
        onChange={async e => {
          const files = e.target.files;
          const list = files ? Array.from(files) : [];
          const docs = list.filter(f => isDocument(f.name)).length;
          const images = list.filter(isImage).length;
          if (docs) onMessages([`Lecture ${docs > 1 ? `de ${docs} documents` : 'du document'} par AURORA…`]);   // a PDF takes a few seconds
          if (images) onMessages([`Lecture de ${images > 1 ? `${images} images` : 'l’image'} sur ce PC par le modèle de vision local… (jusqu’à une minute au premier chargement)`]);
          if (list.some(f => isModel3d(f.name))) onMessages(['Ouverture du modèle 3D dans le laboratoire de l’accueil…']);
          const halt = new AbortController();
          let msgs: string[];
          try {
            msgs = await attachFiles(files, id => showScene({ scene: id, action: 'ouvrir' }), {
              signal: halt.signal,
              progress: m => { setStop(() => () => halt.abort()); onMessages([m]); },   // only a page-by-page reading reports
            });
          } finally {
            setStop(null);
          }
          e.target.value = '';
          onMessages(msgs);
        }} />
    </>
  );
}

/** E2: the screen, captured only when this button is clicked, read on this PC; its description is joined. */
export function ScreenButton({ onMessages, className = 'cmd-icon-btn' }: { onMessages: (m: string[]) => void; className?: string }) {
  const [reading, setReading] = useState(false);
  const capture = async () => {
    setReading(true);
    onMessages(['Capture de l’écran, puis lecture sur ce PC par le modèle de vision local… (30 s à 1 min environ)']);
    try { onMessages(await attachScreen()); } finally { setReading(false); }
  };
  return (
    <button type="button" className={className} onClick={() => void capture()} disabled={reading} aria-busy={reading}
      title={reading ? 'Lecture de l’écran en cours sur ce PC…' : 'Capture d’écran : l’écran est capturé maintenant, lu sur ce PC par le modèle de vision local, et sa description est jointe au prochain message'}
      aria-label="Capture d’écran">
      {reading ? <Spinner size={16} /> : <ScreenShare size={18} />}
    </button>
  );
}

/** The two engines of « Photo vers 3D », named by what they give (11/11/2026, ID Studio IA: « une petite icône pour sélectionner
 *  le moteur »). The times are those measured on this PC on 10/11/2026; the other engine is always the way out. */
const PHOTO_3D: { id: Photo3dChoice; nom: string; detail: string; attente: string; Icone: typeof Zap }[] = [
  { id: 'detaille', nom: 'Détaillé', detail: 'Pixal3D, 3 minutes environ : pour un dessin ou une forme fine',
    attente: '3 minutes environ avec Pixal3D ; TripoSR en secours, 35 s environ', Icone: Gem },
  { id: 'rapide', nom: 'Rapide', detail: 'TripoSR, 35 secondes environ : pour la photo d’un objet simple',
    attente: '35 s environ avec TripoSR ; Pixal3D en secours, 3 minutes environ', Icone: Zap },
];

/** « Photo vers 3D » (10/11/2026): the photo of an object, chosen here, is rebuilt as a 3D shape on this PC by a local
 *  AI, then opened in the laboratory of the home page. One photo at a time; the time shown is measured. The small
 *  badge on the button opens the choice of the engine tried first (PHOTO_3D), remembered with the chat preferences;
 *  which engine really ran is known only when the service answers: the waiting line names the two. */
export function Photo3dButton({ onMessages, className = 'cmd-icon-btn' }: { onMessages: (m: string[]) => void; className?: string }) {
  const input = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLSpanElement>(null);
  const { showScene } = useAurora();
  const { photo3d } = useChatPrefs();
  const [working, setWorking] = useState(false);
  const [open, setOpen] = useState(false);
  const chosen = PHOTO_3D.find(c => c.id === photo3d) ?? PHOTO_3D[0];
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    // Échap closes this menu only: taken first and marked handled, so the chat drawer (its own Échap) stays open.
    const key = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setOpen(false); } };
    document.addEventListener('pointerdown', away);
    document.addEventListener('keydown', key, true);
    return () => { document.removeEventListener('pointerdown', away); document.removeEventListener('keydown', key, true); };
  }, [open]);
  const rebuild = async (file: File) => {
    setWorking(true);
    setOpen(false);
    const choice = chosen;   // the choice at the click: this run keeps its name whatever is chosen meanwhile
    const start = Date.now();
    const say = () => onMessages([`Reconstruction de la forme 3D de « ${file.name} » sur ce PC, choix « ${choice.nom} »… ${Math.round((Date.now() - start) / 1000)} s (${choice.attente} ; davantage si le PC est occupé)`]);
    say();
    const tick = window.setInterval(say, 4000);   // a notice fades after 6 s: this one stays, with the time elapsed
    let messages: string[];
    try {
      messages = await photoTo3d(file, id => showScene({ scene: id, action: 'ouvrir' }), choice.id);
    } finally {
      window.clearInterval(tick);
      setWorking(false);
    }
    onMessages(messages);
  };
  return (
    <span className="cmd-photo3d" ref={box}>
      <button type="button" className={className} onClick={() => input.current?.click()} disabled={working} aria-busy={working}
        title={working ? 'Reconstruction de la forme 3D en cours sur ce PC…' : `Photo vers 3D (${chosen.nom}) : choisissez la photo d’un objet. Sa forme est reconstruite sur ce PC par une IA locale (${chosen.attente}) et s’ouvre dans le laboratoire de l’accueil. Les côtés que la photo ne montre pas sont inventés. La photo ne part nulle part.`}
        aria-label="Photo vers 3D">
        {working ? <Spinner size={16} /> : <Box size={18} />}
      </button>
      <button type="button" className="cmd-photo3d-choice" onClick={() => setOpen(o => !o)} disabled={working}
        aria-haspopup="menu" aria-expanded={open} aria-label={`Moteur de Photo vers 3D : ${chosen.nom}. Changer`}
        title={`Moteur de « Photo vers 3D » : ${chosen.nom} (${chosen.detail}). Cliquez pour changer.`}>
        <chosen.Icone size={11} aria-hidden="true" />
      </button>
      {open && (
        <div className="cmd-photo3d-menu" role="menu" aria-label="Moteur de Photo vers 3D">
          {PHOTO_3D.map(c => (
            <button key={c.id} type="button" role="menuitemradio" aria-checked={c.id === chosen.id} className={c.id === chosen.id ? 'on' : undefined}
              onClick={() => { setPrefs({ photo3d: c.id }); setOpen(false); }}>
              <c.Icone size={15} aria-hidden="true" />
              <span><strong>{c.nom}</strong><small>{c.detail}</small></span>
            </button>
          ))}
          <p>Si le moteur choisi ne peut pas faire la forme, l’autre prend le relais, et la réponse le dit.</p>
        </div>
      )}
      <input ref={input} type="file" hidden accept="image/*"
        onChange={e => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) void rebuild(file);
        }} />
    </span>
  );
}

export function AttachmentChips() {
  const atts = useAttachments();
  if (!atts.length) return null;
  return (
    <ul className="cmd-chips" aria-label="Fichiers joints au prochain message">
      {atts.map(a => (
        <li key={a.id} className="cmd-chip">
          {a.preview ? <img src={a.preview} alt="" /> : <FileText size={13} />}<span title={a.preview ? a.text : a.name}>{a.name}</span><small>{fmtBytes(a.size)}</small>
          <button type="button" onClick={() => removeAttachment(a.id)} aria-label={`Retirer ${a.name}`}><X size={12} /></button>
        </li>
      ))}
    </ul>
  );
}

/** Transient messages (attachment refusals, voice errors). */
export function useNotice(timeoutMs = 6000): [string[], (m: string[]) => void] {
  const [msgs, setMsgs] = useState<string[]>([]);
  useEffect(() => {
    if (!msgs.length) return;
    // 23/11/2026: a long message stays long enough to be read, 70 ms a character and 30 s at most. The refusal of a
    // PDF (175 characters) vanished after 6 s, and ID Studio IA could not tell why his file was not joined.
    const stay = Math.min(30_000, Math.max(timeoutMs, msgs.join(' ').length * 70));
    const t = window.setTimeout(() => setMsgs([]), stay);
    return () => window.clearTimeout(t);
  }, [msgs, timeoutMs]);
  return [msgs, setMsgs];
}

export function Notice({ messages }: { messages: string[] }) {
  if (!messages.length) return null;
  return <div className="cmd-notice" role="status">{messages.map((m, i) => <div key={i}>{m}</div>)}</div>;
}

/** Compact text composer (drawer, pages). Enter sends, Shift+Entrée inserts a line break. */
export function Composer({ placeholder = 'Écrivez votre message…', autoFocus = false, showEngine = true }: { placeholder?: string; autoFocus?: boolean; showEngine?: boolean }) {
  const send = useSend();
  const { busy } = useAurora();
  const atts = useAttachments();
  const { agent } = useChatPrefs();
  const [text, setText] = useState('');
  const [notice, setNotice] = useNotice();
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (autoFocus) area.current?.focus(); }, [autoFocus]);
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(140, el.scrollHeight)}px`;
  }, [text]);
  const submit = () => {
    if (!text.trim() && !atts.length) return;
    const t = text;
    setText('');
    void send(t, { source: 'text', intent: agent ? AGENT_INTENT : undefined });
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  };
  return (
    <div className="chat-composer">
      <AttachmentChips />
      <Notice messages={notice} />
      <form className="chat-composer-row" onSubmit={e => { e.preventDefault(); submit(); }}>
        <AttachButton onMessages={setNotice} />
        <ScreenButton onMessages={setNotice} />
        <Photo3dButton onMessages={setNotice} />
        <textarea ref={area} className="chat-composer-input" rows={1} value={text} maxLength={12000} placeholder={placeholder}
          onChange={e => setText(e.target.value)} onKeyDown={onKey} aria-label="Message pour AURORA" />
        <button type="submit" className="cmd-send" disabled={!text.trim() && !atts.length} title={busy ? 'Envoyer (une demande est déjà en cours)' : 'Envoyer'} aria-label="Envoyer">
          <SendHorizontal size={18} />
        </button>
      </form>
      {/* The « Agent » switch stays visible where the page hides this engine choice (bottom bar on wide screens). */}
      <div className="chat-composer-opts">
        {showEngine && <EngineSelect compact />}
        <AgentToggle />
      </div>
      {agent && <p className="chat-help chat-agent-help">{AGENT_HELP}</p>}
    </div>
  );
}
