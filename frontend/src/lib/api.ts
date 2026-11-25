// Typed client for the local AURORA service (docs/CONTRACT.md §8).
// Extended from the ASTRA starter's api.ts: same request() helper, plus the session token.
import type {
  ActivityEvent, AnthropicKeyStatus, ArtefactDetail, ArtefactMade, ArtefactSummary, AskInput, Capabilities, ChatMessage, Conversation, ConversationConsent, ConversationDeleted, DocumentText, Health, LocalRecognitionStatus, LocalTranscript, MemoryGraphData, MemoryHit,
  MemoryItem, NetlifyTokenStatus, PageReading, PcApps, Permission, ProcessInfo, ReportInput, Routine, RoutineInput, RunDetail, RunReport, RunResult, Settings, StudioData,
  StudioSecretName, StudioSecretStatus, StudioSettings, StudioSettingsInput, SystemInfo, SystemPoint, TaskItem, TaskStatus, ToolMapData, ToolResult, Usage,
  ScreenReading, StartupState, VisionReading, VisionStatus, YoutubeConnection,
} from './types';
import type { FicheDonnees, OutilConception, ProjetCalcul, ProjetControles, ProjetDetail, ProjetFabrication, ProjetVerification, ProjetsListe } from './types';
import type { Reminder, ReminderList } from './types';
import type {
  GlobeAirData, GlobeAircraftData, GlobeArticleMode, GlobeArticleText, GlobeEventsData, GlobeGuideData, GlobeImage, GlobeNewsData, GlobeSatellitesData, GlobeTrafficData,
  GlobeWeatherData, GlobeWindData, SceneCatalogueItem, SceneDebout, SceneDescription,
} from './types';

export const API_BASE: string = import.meta.env.VITE_AURORA_API_URL || import.meta.env.VITE_ASTRA_API_URL || 'http://127.0.0.1:8765';

let token: string | null = null;
let tokenPromise: Promise<string> | null = null;

async function ensureToken(): Promise<string> {
  if (token) return token;
  // The desktop app (Tauri, planned) injects the token at start-up: no HTTP route is then needed
  // (the service runs with AURORA_BOOTSTRAP=0). In the browser, /api/bootstrap remains the fallback.
  const injected = (window as unknown as { __AURORA_TOKEN__?: unknown }).__AURORA_TOKEN__;
  if (typeof injected === 'string' && /^[A-Za-z0-9_-]{32,256}$/.test(injected)) {
    token = injected;
    return token;
  }
  if (!tokenPromise) {
    tokenPromise = fetch(`${API_BASE}/api/bootstrap`)
      .catch(() => { throw new Error('Service local injoignable'); })
      .then(async r => {
        if (!r.ok) throw new Error(`Service local indisponible (${r.status})`);
        const data = await r.json();
        token = data.token as string;
        return token;
      })
      .finally(() => { tokenPromise = null; });
  }
  return tokenPromise;
}

export function sessionToken(): string | null { return token; }
/** Forget the cached session token: the next call fetches a fresh one (the backend may have restarted). */
export function resetToken(): void { token = null; }
export { ensureToken };

/** fetch() rejects with a browser message in English (« Failed to fetch »): show a French one. */
const UNREACHABLE = 'Service local injoignable';

/** The checked response of a call: token, one retry after a backend restart, French error from `detail`. */
async function respond(endpoint: string, init?: RequestInit, retry = true): Promise<Response> {
  const t = await ensureToken().catch(() => null);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${endpoint}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(t ? { 'X-Aurora-Token': t } : {}), ...init?.headers },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new Error(UNREACHABLE);
  }
  if (response.status === 401 && retry) {
    token = null; // backend restarted: fetch a fresh session token once
    return respond(endpoint, init, false);
  }
  if (!response.ok) {
    let message = `Erreur ${response.status}`;
    try { message = (await response.json()).detail ?? message; } catch { /* pas de JSON */ }
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
  }
  return response;
}

async function request<T>(endpoint: string, init?: RequestInit): Promise<T> {
  const response = await respond(endpoint, init);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/** Same as request() for a text body (a page's HTML). */
async function requestText(endpoint: string, init?: RequestInit): Promise<string> {
  return (await respond(endpoint, init)).text();
}

const json = (body: unknown) => JSON.stringify(body);
const qs = (params: Record<string, string | number | null | undefined>) => {
  const s = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join('&');
  return s ? `?${s}` : '';
};

export const api = {
  health: () => request<Health>('/api/health'),
  capabilities: () => request<Capabilities>('/api/capabilities'),
  system: () => request<SystemInfo>('/api/system'),
  systemHistory: () => request<{ points: SystemPoint[] }>('/api/system/history'),
  processes: (limit = 12, sort: 'cpu' | 'memory' = 'cpu') => request<{ processes: ProcessInfo[] }>(`/api/system/processes${qs({ limit, sort })}`),

  ask: (input: AskInput) => request<RunResult>('/api/ask', { method: 'POST', body: json(input) }),
  runs: (limit = 50) => request<TaskItem[]>(`/api/runs${qs({ limit })}`),
  run: (id: number) => request<RunDetail>(`/api/runs/${id}`),
  cancelRun: (id: number) => request<RunResult>(`/api/runs/${id}/cancel`, { method: 'POST' }),
  resumeRun: (id: number) => request<RunResult>(`/api/runs/${id}/resume`, { method: 'POST' }),
  permissions: (status = 'pending') => request<Permission[]>(`/api/permissions${qs({ status })}`),
  /** scope 'conversation': mode Avancé, cloud.send, non-sensitive data only (checked by the server). */
  decidePermission: (id: number, decision: 'grant' | 'deny', scope: 'once' | 'conversation' = 'once') =>
    request<RunResult>(`/api/permissions/${id}`, { method: 'POST', body: json({ decision, scope }) }),
  speechStatus: () => request<{ available: boolean; engine: string; local: boolean; voice: string; loaded: boolean; license: string }>('/api/tts'),
  /** WAV of a short text from the local neural voice (Piper): generated on this PC, nothing leaves it. */
  speechAudio: async (text: string, signal?: AbortSignal): Promise<Blob> => {
    const t = await ensureToken();
    const response = await fetch(`${API_BASE}/api/tts`, {
      method: 'POST', signal, body: json({ text }), headers: { 'Content-Type': 'application/json', 'X-Aurora-Token': t },
    });
    if (!response.ok) throw new Error(`Voix locale indisponible (${response.status})`);
    return response.blob();
  },
  /** Local speech recognition (voix D3): Athéna's Whisper service, started by AURORA on this PC when needed. */
  recognitionStatus: () => request<LocalRecognitionStatus>('/api/stt'),
  prepareRecognition: () => request<LocalRecognitionStatus>('/api/stt/prepare', { method: 'POST' }),
  /** Text of a recording, recognised on this PC (the audio never leaves it). */
  transcribe: (audio: Blob, signal?: AbortSignal) => request<LocalTranscript>('/api/stt', {
    method: 'POST', body: audio, signal, headers: { 'Content-Type': 'application/octet-stream' },
  }),
  /** E2: local vision. The image never leaves the PC; only its description is joined (as data). */
  visionStatus: () => request<VisionStatus>('/api/vision'),
  describeImage: (image: Blob) => request<VisionReading>('/api/vision/describe', {
    method: 'POST', body: image, headers: { 'Content-Type': 'application/octet-stream' },
  }),
  /** Only from the « Capture d’écran » button: the screen is captured now, then read on this PC. */
  describeScreen: () => request<ScreenReading>('/api/vision/screen', { method: 'POST' }),
  anthropicKey: () => request<AnthropicKeyStatus>('/api/secrets/anthropic'),
  /** Stored in the Windows vault by the local service; never returned afterwards. */
  saveAnthropicKey: (api_key: string) => request<AnthropicKeyStatus>('/api/secrets/anthropic', { method: 'PUT', body: json({ api_key }) }),
  deleteAnthropicKey: () => request<AnthropicKeyStatus>('/api/secrets/anthropic', { method: 'DELETE' }),
  conversationConsent: (conversationId: number) => request<ConversationConsent>(`/api/conversations/${conversationId}/cloud-consent`),
  revokeConversationConsent: (conversationId: number) =>
    request<ConversationConsent>(`/api/conversations/${conversationId}/cloud-consent`, { method: 'DELETE' }),

  memories: (category?: string, kind?: string) => request<MemoryItem[]>(`/api/memories${qs({ category, kind })}`),
  addMemory: (title: string, detail: string, category: string, extra: Partial<Pick<MemoryItem, 'kind' | 'confidentiality'>> = {}) =>
    request<MemoryItem>('/api/memories', { method: 'POST', body: json({ title, detail, category, ...extra }) }),
  updateMemory: (id: number, fields: Partial<MemoryItem>) => request<MemoryItem>(`/api/memories/${id}`, { method: 'PATCH', body: json(fields) }),
  deleteMemory: (id: number) => request<void>(`/api/memories/${id}`, { method: 'DELETE' }),
  searchMemories: (q: string, limit = 8) => request<MemoryHit[]>(`/api/memories/search${qs({ q, limit })}`),
  memoryGraph: (limit?: number) => request<MemoryGraphData>(`/api/memories/graph${limit ? `?limit=${limit}` : ''}`),
  /** Carte des outils (page Tâches) : chaque outil du registre avec sa famille, son état réel et son usage. Lecture seule. */
  toolMap: () => request<ToolMapData>('/api/tools/map'),
  linkMemories: (id: number, target_id: number, relation = 'related') => request<unknown>(`/api/memories/${id}/links`, { method: 'POST', body: json({ target_id, relation }) }),

  tasks: () => request<TaskItem[]>('/api/tasks'),
  addTask: (title: string) => request<TaskItem>('/api/tasks', { method: 'POST', body: json({ title }) }),
  setTaskStatus: (id: number, status: TaskStatus) => request<TaskItem>(`/api/tasks/${id}`, { method: 'PATCH', body: json({ status }) }),

  events: (params: { limit?: number; run_id?: number; since_id?: number } = {}) => request<ActivityEvent[]>(`/api/events${qs({ limit: params.limit ?? 200, run_id: params.run_id, since_id: params.since_id })}`),
  clearEvents: () => request<void>('/api/events', { method: 'DELETE' }),

  conversations: () => request<Conversation[]>('/api/conversations'),
  messages: (conversationId: number) => request<ChatMessage[]>(`/api/conversations/${conversationId}/messages`),
  /** E4: the text of a PDF or Word file, read by the local service (nothing is kept there). */
  extractDocument: (file: File, name: string) => request<DocumentText>(`/api/attachments/extract${qs({ name })}`, {
    method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' },
  }),
  /** 23/11/2026: one page of a PDF made of images, read on this PC by the local vision model (20 to 40 s). */
  readDocumentPage: (file: File, page: number, signal?: AbortSignal) => request<PageReading>(`/api/attachments/page${qs({ page })}`, {
    method: 'POST', body: file, signal, headers: { 'Content-Type': 'application/octet-stream' },
  }),
  deleteConversation: (conversationId: number) =>
    request<ConversationDeleted>(`/api/conversations/${conversationId}`, { method: 'DELETE' }),

  weather: (city?: string) => request<ToolResult>(`/api/weather${qs({ city })}`),
  weatherCities: () => request<ToolResult>('/api/weather/cities'),
  news: (topic?: string) => request<ToolResult>(`/api/news${qs({ topic })}`),
  quakes: () => request<ToolResult>('/api/quakes'),
  search: (q: string) => request<ToolResult>(`/api/search${qs({ q })}`),
  solar: (date?: string) => request<ToolResult>(`/api/sim/solar${qs({ date })}`),
  orbit: (params: { body: string; altitude_km: number; eccentricity?: number; hours?: number }) => request<ToolResult>('/api/sim/orbit', { method: 'POST', body: json(params) }),

  settings: () => request<Settings>('/api/settings'),
  updateSettings: (fields: Partial<Settings>) => {
    const body: Partial<Settings> = { ...fields };
    delete body.db_path; // read-only (S1)
    return request<Settings>('/api/settings', { method: 'PATCH', body: json(body) });
  },
  pcApps: () => request<PcApps>('/api/apps/pc'),   // read only: the Desktop and Start menu apps (tools/apps.py)
  // Paramètres › Démarrage (16/11/2026, tools/demarrage.py) : l'état lu sur le disque, puis le réglage.
  demarrage: () => request<StartupState>('/api/demarrage'),
  reglerDemarrage: (actif: boolean, fenetre: boolean) => request<StartupState>('/api/demarrage', { method: 'PUT', body: json({ actif, fenetre }) }),
  usage: () => request<Usage>('/api/usage'),
  runBench: () => request<Record<string, any>>('/api/quality/bench', { method: 'POST' }),
  latestBench: () => request<Record<string, any> | null>('/api/quality/latest'),
  restoreFiles: (runId: number) => request<ToolResult>(`/api/files/restore/${runId}`, { method: 'POST' }),

  // « Signaler » (H3): one report per run; PUT again corrects it, DELETE cancels it.
  runReport: (runId: number) => request<RunReport | null>(`/api/runs/${runId}/report`),
  saveRunReport: (runId: number, input: ReportInput) => request<RunReport>(`/api/runs/${runId}/report`, { method: 'PUT', body: json(input) }),
  deleteRunReport: (runId: number) => request<void>(`/api/runs/${runId}/report`, { method: 'DELETE' }),
  reports: (limit = 500) => request<RunReport[]>(`/api/quality/reports${qs({ limit })}`),
  // P2 « Pages »: reports built by the service from real data, stored on this PC with their versions.
  artefacts: () => request<ArtefactSummary[]>('/api/artefacts'),
  artefact: (id: string) => request<ArtefactDetail>(`/api/artefacts/${encodeURIComponent(id)}`),
  /** Standalone HTML of a version (the latest without one): shown in a sandboxed frame, never in the app's DOM. */
  artefactHtml: (id: string, version?: number) => requestText(`/api/artefacts/${encodeURIComponent(id)}/html${qs({ version })}`),
  artefactFromRun: (runId: number) => request<ArtefactMade>(`/api/artefacts/from-run/${runId}`, { method: 'POST' }),
  artefactFromBench: () => request<ArtefactMade>('/api/artefacts/bench', { method: 'POST' }),
  deleteArtefact: (id: string) => request<void>(`/api/artefacts/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  routines: () => request<Routine[]>('/api/routines'),
  createRoutine: (input: RoutineInput) => request<Routine>('/api/routines', { method: 'POST', body: json(input) }),
  updateRoutine: (id: number, input: RoutineInput) => request<Routine>(`/api/routines/${id}`, { method: 'PUT', body: json(input) }),
  deleteRoutine: (id: number) => request<void>(`/api/routines/${id}`, { method: 'DELETE' }),
  // C3 (16/11/2026): reminders, timers and alarms kept by the service (tasks/reminders.py).
  reminders: () => request<ReminderList>('/api/reminders'),
  removeReminder: (id: number) => request<void>(`/api/reminders/${id}`, { method: 'DELETE' }),
  snoozeReminder: (id: number, minutes: number) => request<Reminder>(`/api/reminders/${id}/snooze`, { method: 'POST', body: json({ minutes }) }),
  // Studio: the creator's public activity. refresh=1 empties the service caches (« Actualiser »).
  studio: (refresh = false) => request<StudioData>(`/api/studio${qs({ refresh: refresh ? 1 : undefined })}`),
  studioSettings: () => request<StudioSettings>('/api/studio/settings'),
  saveStudioSettings: (input: StudioSettingsInput) => request<StudioSettings>('/api/studio/settings', { method: 'PUT', body: json(input) }),
  /** Studio secrets (Netlify token, YouTube API key): stored in the Windows vault by the local service, never returned. */
  studioSecret: (name: StudioSecretName) => request<StudioSecretStatus>(`/api/secrets/${name}`),
  saveStudioSecret: (name: StudioSecretName, token: string) =>
    request<StudioSecretStatus>(`/api/secrets/${name}`, { method: 'PUT', body: json({ token }) }),
  deleteStudioSecret: (name: StudioSecretName) => request<StudioSecretStatus>(`/api/secrets/${name}`, { method: 'DELETE' }),
  netlifyToken: () => api.studioSecret('netlify'),
  saveNetlifyToken: (token: string) => api.saveStudioSecret('netlify', token),
  deleteNetlifyToken: () => api.deleteStudioSecret('netlify'),
  /** AURORA's own Google connection (posting comments): status, address of Google's page, disconnection. */
  youtubeConnection: () => request<YoutubeConnection>('/api/youtube/etat'),
  youtubeConnect: () => request<{ url: string; redirect_uri: string; expires_in_s: number }>('/api/youtube/connexion', { method: 'POST' }),
  youtubeDisconnect: () => request<YoutubeConnection>('/api/youtube/connexion', { method: 'DELETE' }),
  /** What Google gave the page /youtube-retour (lib/youtubeReturn.ts), sent with the session token. */
  youtubeReturn: (back: { state: string; code: string; error: string }) =>
    request<YoutubeConnection>('/api/youtube/retour', { method: 'POST', body: json(back) }),
  /** JSONL in the format of backend/tests/data/routing_bench.jsonl, for a download: nothing is written in the repository. */
  exportReports: async (retry = true): Promise<Blob> => {
    const t = await ensureToken();
    let response: Response;
    try {
      response = await fetch(`${API_BASE}/api/quality/reports/export`, { headers: { 'X-Aurora-Token': t } });
    } catch {
      throw new Error(UNREACHABLE);
    }
    if (response.status === 401 && retry) { token = null; return api.exportReports(false); }   // backend restarted
    if (!response.ok) throw new Error(`Export impossible (${response.status})`);
    return response.blob();
  },

  // Globe hologramme (Monde 3D, 8/11/2026) : routes /api/globe/* (reviews/globe-contrat.md), avec le jeton comme les autres.
  // Mode Privé : ok=false et data.private_mode, sans appel réseau du service.
  globeAircraft: (lat?: number, lon?: number) =>
    request<ToolResult<GlobeAircraftData>>(`/api/globe/avions${qs({ lat: lat?.toFixed(3), lon: lon?.toFixed(3) })}`),
  globeTraffic: () => request<ToolResult<GlobeTrafficData>>('/api/globe/monde'),
  globeSatellites: () => request<ToolResult<GlobeSatellitesData>>('/api/globe/satellites'),
  globeWeather: (lat: number, lon: number) => request<ToolResult<GlobeWeatherData>>(`/api/globe/meteo${qs({ lat: lat.toFixed(3), lon: lon.toFixed(3) })}`),
  globeEvents: () => request<ToolResult<GlobeEventsData>>('/api/globe/evenements'),
  globeWind: () => request<ToolResult<GlobeWindData>>('/api/globe/vent'),
  globeAir: () => request<ToolResult<GlobeAirData>>('/api/globe/air'),
  globeNews: () => request<ToolResult<GlobeNewsData>>('/api/globe/actualites'),
  // 11/11/2026 : « Lis-le-moi » / « Explique-le ». Seulement un lien que le service a lui-même donné dans l'actualité.
  globeArticle: (lien: string, mode: GlobeArticleMode) =>
    request<ToolResult<GlobeArticleText>>('/api/globe/article', { method: 'POST', body: json({ lien, mode }) }),
  globeGuide: (projet: 'wikipedia' | 'wikivoyage', titre: string) => request<ToolResult<GlobeGuideData>>(`/api/globe/guide${qs({ projet, titre })}`),
  // Laboratoire 3D de l'accueil (8/11/2026) : outils lab.catalogue et lab.scene (reviews/accueil-holographique.md § 3).
  // Sans réseau côté service ; une scène inconnue ou invalide revient avec ok=false et un message en français.
  // 13/11/2026 : les projets (lecture seule) : la liste avec l'état de chacun, puis un projet en entier.
  projets: () => request<ProjetsListe>('/api/projets'),
  projet: (id: string) => request<ProjetDetail>(`/api/projets/${encodeURIComponent(id)}`),
  /** 23/11/2026 : le bouton « Créer un projet ». Un projet vide, écrit dans les projets de la personne. */
  creerProjet: (titre: string, resume: string) => request<ProjetDetail>('/api/projets', { method: 'POST', body: json({ titre, resume }) }),
  outilsConception: () => request<{ outils: OutilConception[] }>('/api/apps/conception'),   // FreeCAD, KiCad : lecture seule
  // 15/11/2026 : un fichier déclaré d'un projet, vérifié par le moteur sans fenêtre du logiciel autorisé, sur une copie.
  projetVerifier: (id: string, chemin: string) => request<ProjetVerification>(
    `/api/projets/${encodeURIComponent(id)}/verification${qs({ chemin })}`, { method: 'POST' }),
  // Tous les fichiers que les vérifications du projet font contrôler, d'un coup ; leur état est ensuite recalculé.
  projetVerifierTout: (id: string) => request<ProjetControles>(`/api/projets/${encodeURIComponent(id)}/verifications/moteurs`, { method: 'POST' }),
  // L'atelier (15/11/2026) : où en est la construction d'un projet, la lancer, garder son modèle parmi les modèles 3D.
  // 15/11/2026 : l'image de la fiche technique d'un projet. Un <img> ne peut pas envoyer le jeton : l'appelant en fait
  // une adresse d'objet et la libère.
  projetFiche: async (id: string): Promise<Blob> => (await respond(`/api/projets/${encodeURIComponent(id)}/fiche`)).blob(),
  // 15/11/2026 : la fiche technique interactive. Ses cadres, validés par le service ; puis les octets du modèle 3D
  // (GLB) de ses vues, lus ensuite par le même lecteur fermé que le laboratoire (labo/origine.ts).
  projetFicheDonnees: (id: string) => request<FicheDonnees>(`/api/projets/${encodeURIComponent(id)}/fiche/donnees`),
  projetFicheModele: async (id: string): Promise<ArrayBuffer> => (await respond(`/api/projets/${encodeURIComponent(id)}/fiche/modele`)).arrayBuffer(),
  projetFabrication: (id: string) => request<ProjetFabrication>(`/api/projets/${encodeURIComponent(id)}/fabrication`),
  lancerFabrication: (id: string) => request<ProjetFabrication>(`/api/projets/${encodeURIComponent(id)}/fabrication`, { method: 'POST' }),
  garderModeleFabrique: (id: string) => request<{ id: string; titre: string; deja: boolean }>(
    `/api/projets/${encodeURIComponent(id)}/fabrication/modele`, { method: 'POST' }),
  // « Les calculs en premier » : un cahier des charges confronté au programme de calcul du projet (jamais une IA).
  projetCalcul: (id: string, masseKg: number, autonomieMin: number, articulations: number) => request<ProjetCalcul>(
    `/api/projets/${encodeURIComponent(id)}/calcul?masse_kg=${masseKg}&autonomie_min=${autonomieMin}&articulations=${articulations}`),
  labScenes: () => request<ToolResult<{ scenes: SceneCatalogueItem[] }>>('/api/lab/scenes'),
  labScene: (id: string) => request<ToolResult<SceneDescription>>(`/api/lab/scenes/${encodeURIComponent(id)}`),
  // 11/11/2026 (« retirer un modèle gardé ») : après la confirmation de la liste « Modèles 3D ». Un modèle gardé ou une
  // maquette générée quitte le catalogue ; ses fichiers sont mis de côté (`mis_de_cote`), jamais effacés.
  labRemoveScene: (id: string) => request<{ id: string; titre: string; origine: string; fichiers: string[]; mis_de_cote: string }>(
    `/api/lab/scenes/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  // 10/11/2026 : un modèle 3D choisi par « Joindre un fichier » (STL, GLB, OBJ). Le service le vérifie, le copie dans
  // son dossier et en fait une scène d'un seul bloc ; `deja` : ce même fichier était déjà là, sa scène est rouverte.
  labImportModel: (file: File, name: string) => request<{ id: string; titre: string; deja: boolean }>(`/api/lab/modeles${qs({ name })}`, {
    method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' },
  }),
  // 10/11/2026 (« Photo vers 3D ») : la photo d'un objet est donnée à une IA locale sur ce PC (Pixal3D, 3 minutes
  // environ ; TripoSR en secours, 35 s environ ; rien ne part ailleurs) ; la forme reconstruite est gardée comme une scène d'un seul bloc, marquée « reconstruit ». Les mesures
  // viennent de l'exécution : `couverture`, la part de la photo gardée comme « l'objet » après détourage (au-delà des
  // deux tiers, l'interface le signale comme une mise en garde : chat/attachments.ts).
  // `choix` (11/11/2026) : le moteur essayé en premier, « detaille » (Pixal3D) ou « rapide » (TripoSR) ; l'autre sert de secours.
  // `moteur` : l'IA locale qui a fait la forme (« Pixal3D » ou « TripoSR ») ; `repli` : ce qu'a dit le premier moteur
  // quand l'autre a pris son relais, sinon null.
  labPhotoModel: (photo: Blob, name: string, choix: 'detaille' | 'rapide' = 'detaille') => request<{ id: string; titre: string; deja: boolean; secondes: number; triangles: number | null; couverture: number | null; moteur?: string | null; repli?: string | null }>(`/api/lab/photo${qs({ name, choix })}`, {
    method: 'POST', body: photo, headers: { 'Content-Type': 'application/octet-stream' },
  }),
  // 10/11/2026 (« redresser et renommer ») : un modèle gardé est posé autrement (`debout`, `quart`) ou renommé (`titre`).
  // Le service ne réécrit que la description de la scène ; le fichier du modèle n'est jamais touché.
  labAdjustModel: (id: string, reglage: { titre?: string; debout?: SceneDebout; quart?: number }) =>
    request<{ id: string; titre: string }>(`/api/lab/modeles/${encodeURIComponent(id)}`, { method: 'PUT', body: json(reglage) }),
  /** Les octets du modèle d'une scène importée (le jeton est exigé : un chargeur de three.js ne saurait pas l'envoyer). */
  labModel: async (fichier: string) => (await respond(`/api/lab/modeles/${encodeURIComponent(fichier)}`)).arrayBuffer(),
  /**
   * An image of the globe (NASA GIBS through the service): a TextureLoader or an <img> cannot send the token,
   * so fetch → blob; the caller makes the object URL and revokes it. X-Bbox: the region really returned;
   * X-Date-Image: the day of the NASA image. 403 = Mode Privé (no network call). Never throws, except on abort.
   */
  globeImage: async (kind: 'detail' | 'nasa', bbox?: string, signal?: AbortSignal, retry = true): Promise<GlobeImage> => {
    const t = await ensureToken().catch(() => null);
    if (!t) return { ok: false, status: 0, error: UNREACHABLE, private_mode: false };
    let response: Response;
    try {
      response = await fetch(`${API_BASE}/api/globe/${kind}${qs({ bbox })}`, { signal, headers: { 'X-Aurora-Token': t } });
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') throw err;
      const timeout = err instanceof DOMException && err.name === 'TimeoutError';
      return { ok: false, status: 0, error: timeout ? 'Délai dépassé' : UNREACHABLE, private_mode: false };
    }
    if (response.status === 401 && retry) { token = null; return api.globeImage(kind, bbox, signal, false); }   // backend restarted
    if (!response.ok) {
      let error = `Erreur ${response.status}`;
      try {
        const body = await response.json();
        const detail = body?.detail ?? body?.error;
        if (typeof detail === 'string') error = detail;
      } catch { /* pas de JSON */ }
      return { ok: false, status: response.status, error, private_mode: response.status === 403 };
    }
    const parts = response.headers.get('X-Bbox')?.split(',').map(Number) ?? null;
    let blob: Blob;
    try { blob = await response.blob(); } catch { return { ok: false, status: 0, error: 'Image incomplète', private_mode: false }; }
    return { ok: true, blob, bbox: parts && parts.length === 4 && parts.every(Number.isFinite) ? parts : null, date: response.headers.get('X-Date-Image'),
      satellite: response.headers.get('X-Satellite-Image') };
  },
};
