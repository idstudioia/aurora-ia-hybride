// Shared API types — mirror of docs/CONTRACT.md §8. Extend, never rename.

export type Mode = 'prive' | 'equilibre' | 'avance';
export type Stage = 'input' | 'decision' | 'tool' | 'local_ai' | 'cloud_ai' | 'permission' | 'verify' | 'response' | 'memory' | 'system' | '';
export type PageId = 'home' | 'studio' | 'chat' | 'search' | 'computer' | 'world' | 'simulations' | 'projets' | 'memory' | 'tasks' | 'settings';

/** 13/11/2026 : les projets (tools/projets.py). L'état d'une exigence ou d'une vérification est celui que le projet
 *  déclare ; seule la présence des fichiers est mesurée par le service. */
export interface ProjetFichier { nom: string; chemin: string; role: string; genere: boolean; present: boolean; taille: number | null }
export interface ProjetRubrique<F = ProjetFichier[]> { id: string; titre: string; note: string; fichiers: F }
export interface ProjetResume {
  id: string; titre: string; resume: string; stade: string; maj: string; origine: string; dossier: string;
  fichiers: { presents: number; a_refaire: number; manquants: string[] };
  verifications: { faites: number; a_faire: number; bloquantes: number; total: number };
  exigences: Record<string, number>;
  pret_a_fabriquer: boolean; pourquoi_pas: string[];
  rubriques: ProjetRubrique<number>[];
  /** Le programme de calcul qu'AURORA peut appeler pour ce projet (« les calculs en premier ») ; absent ou null sinon. */
  calcul?: string | null;
  /** Le programme qui construit tout le projet, quand AURORA peut le lancer (tools/atelier.py) ; absent ou null sinon. */
  fabrication?: string | null;
  /** L'image de sa fiche technique (son fichier déclaré « fiche-technique »), quand elle est là ; absent ou null sinon. */
  fiche?: string | null;
  /** Les données de sa fiche technique interactive (son fichier déclaré « fiche-technique.json ») ; absent ou null sinon. */
  fiche_donnees?: string | null;
}
/** 15/11/2026 : un cadre de la fiche technique interactive d'un projet (tools/projets.py, CadreFiche). Une « vue » dit
 *  d'où regarder le modèle 3D du projet ; « etat » est l'état du projet, que la page tire de son dossier. */
export interface FicheCadre {
  type: 'vue' | 'tableau' | 'liste' | 'etat'; titre: string; accent: 'cyan' | 'orange';
  /** [colonne, ligne, largeur, hauteur] sur une grille de 24 colonnes ; vide : la page range le cadre elle-même. */
  place: number[];
  azimut: number; hauteur: number; eclate: boolean; cote_largeur: string; cote_hauteur: string;
  reperes: { n: number; piece: string }[]; lignes: string[][]; numerotee: boolean; note: string;
}
/** La fiche technique interactive d'un projet, validée par le service, et le modèle 3D de ses vues s'il y en a un. */
export interface FicheDonnees {
  fiche: { format: string; titre: string; sous_titre: string; modele: string; cadres: FicheCadre[]; cartouche: string[][]; note: string };
  modele: { chemin: string; taille: number } | null;
}
/** Où en est la construction d'un projet (tools/atelier.py). `n` sur `sur` et `quoi` : l'étape que le programme a
 *  dite en dernier. « fini » : le programme est sorti sans erreur et a nommé son modèle. */
export interface ProjetFabrication {
  projet: string; etat: 'jamais' | 'en_cours' | 'fini' | 'echec' | 'indisponible';
  titre?: string; programme?: string; raison?: string;
  n?: number; sur?: number; quoi?: string; etapes?: string[]; journal?: string[]; debut?: string; secondes?: number;
  resultat?: { modele: string; taille: number; resume: string; rapport?: string; pieces?: number; coques?: number; masse_g?: number; moteurs_qui_depassent?: number } | null;
  erreur?: string | null;
}
/** Un cahier des charges confronté au programme de calcul du projet (tools/projets.dimensionner). Tous les chiffres
 *  viennent de ce programme. Seuls `lignes`, `verdict` et `resume` sont garantis ; le reste dépend du programme. */
export interface ProjetCalcul {
  projet: string; titre: string; programme: string;
  lignes: { quoi: string; etat: 'tenue' | 'non_tenue'; detail: string }[];
  verdict: 'faisable' | 'marche_seulement' | 'pas_faisable'; resume?: string; hypotheses?: string[];
  pieces?: { servomoteur: string; batterie: string };
  masse_calcul_kg?: number;
  couples?: { situation: string; nom: string; force_n: number; genou_nm: number; hanche_nm: number; epaule_nm: number; part_nominal_pct: number }[];
  masse?: { postes: { nom: string; grammes: number; source: string }[]; total_kg: number; reste_kg: number };
}
export interface ProjetDetail extends Omit<ProjetResume, 'rubriques'> {
  rubriques: ProjetRubrique[];
  cahier: { texte: string; etat: 'tenue' | 'a_verifier' | 'a_etudier' | 'non_tenue'; note: string }[];
  /** `controle` / `calcule` (15/11/2026) : une vérification qui nomme des fichiers à faire contrôler par FreeCAD ou
   *  KiCad n'a pas l'état que le projet déclare : `etat` est alors calculé par le service, et `calcule` dit d'où il vient. */
  liste_verifications: { quoi: string; etat: 'faite' | 'a_faire' | 'bloquante'; detail: string; controle?: string[]; calcule?: VerificationCalculee }[];
}
export interface VerificationCalculee {
  conformes: number; total: number; texte: string;
  fichiers: { chemin: string; etat: 'conforme' | 'a_revoir' | 'change' | 'jamais' | 'absent' | 'non_verifiable'; resume: string; moteur: string; date: string }[];
}
/** Tous les fichiers nommés par les vérifications d'un projet, donnés à leur moteur (tools/moteurs_cao.py verifier_tout). */
export interface ProjetControles {
  projet: string; date: string;
  verifies: { fichier: string; outil: string; verdict: 'conforme' | 'a_revoir'; resume: string; moteur: string }[];
  refuses: { fichier: string; raison: string }[];
}
export interface ProjetsListe { projets: ProjetResume[]; refuses: { dossier: string; raison: string }[] }
/** FreeCAD ou KiCad (tools/apps.py design_tools) : `autorise` = son nom dans Applications autorisées (elle seule
 *  reçoit un fichier de projet), sinon `sur_ce_pc` = les raccourcis de ce PC qui le lancent, à ajouter dans Paramètres. */
export interface OutilConception {
  id: string; nom: string; ouvre: string[]; autorise: string | null; sur_ce_pc: string[];
  /** 15/11/2026 : les fichiers que son moteur sans fenêtre sait vérifier, et si ce moteur est là, à côté du logiciel
   *  autorisé. Absents d'un service qui ne connaît pas encore la vérification. */
  verifie?: string[]; moteur?: boolean;
}
/** Ce que le moteur de FreeCAD ou de KiCad a dit d'un fichier d'un projet (tools/moteurs_cao.py, 15/11/2026). Les
 *  chiffres et les violations sont ceux du moteur ; `limite` dit ce que ce contrôle ne prouve pas. */
export interface ProjetVerification {
  projet: string; fichier: string; outil: 'freecad' | 'kicad'; genre: 'piece' | 'schema' | 'carte'; limite: string; date: string;
  moteur: string; verdict: 'conforme' | 'a_revoir'; resume: string;
  objets?: { nom: string; solides: number; valide: boolean; ferme: boolean; volume_mm3: number; surface_mm2: number; boite_mm: number[] }[];
  erreurs?: number; avertissements?: number; non_connectes?: number; autres?: number;
  violations?: { gravite: 'erreur' | 'avertissement'; texte: string; ou: string }[];
}

/** Globe hologramme (Monde 3D, 8/11/2026) : couches que le globe sait ouvrir. Voir reviews/globe-contrat.md. */
export type GlobeLayer = 'meteo' | 'evenements' | 'actualites' | 'avions' | 'satellites' | 'guide' | 'routes';

/** What the service asks the globe to show after a request (run.ui.globe): a place, a layer, a filter. */
export interface GlobeOrder {
  /** Fly there (smooth rotation and zoom). `nom` is shown and may be said. */
  focus?: { lat: number; lon: number; nom?: string };
  couche?: GlobeLayer;
  /** Actualités : theme ('tout', 'actualite', 'economie', 'sport', 'culture', 'science', 'technologie', 'voyage'). */
  theme?: string;
  /** Actualités : country name exactly as in /terre/guide-pays.json (field `n`). */
  pays?: string;
  /** Satellites : NORAD number to select (25544 = ISS). */
  satellite?: number;
  /** A place named by the user (« montre-moi Paris sur le globe »), lower case without accents: the globe looks it
   *  up in its own lists (capitals, cities, countries), with no network call, and says so when it is not found. */
  lieu?: string;
}

/** A GlobeOrder as given to the globe: `id` grows at each order, and the globe acts once per id. */
export interface GlobeCommand extends GlobeOrder { id: number }

/** 08/11/2026 (run.ui.lieux): the photographed World Heritage sites of the place a question names, read by the service in
 *  the globe's own list (guide-sites.json, Wikidata). `img` is a Wikimedia Commons file; never sent in Private mode. */
export interface LieuxPhotos {
  titre: string;
  pays: string | null;
  photos: { nom: string; img: string; la: number; lo: number; pays: string | null }[];
}

/** Laboratoire 3D de l'accueil (8/11/2026) : format `aurora.scene/1`, voir reviews/accueil-holographique.md § 2.
 *  Une description, jamais du code : l'interface la revalide (components/labo/scene.ts) avant de construire. */
export type SceneNature = 'illustration' | 'modele_calcule' | 'simulation';
export type SceneCouleur = 'cyan' | 'violet' | 'ambre' | 'vert';
export type SceneAxe = 'x' | 'y' | 'z';
export type SceneVec = [number, number, number];
export type SceneForme =
  | { type: 'boite'; l: number; h: number; p: number; decalage?: SceneVec }
  | { type: 'cylindre'; rayon: number; rayon_haut?: number; hauteur: number; axe?: SceneAxe; decalage?: SceneVec }
  | { type: 'sphere'; rayon: number; decalage?: SceneVec }
  | { type: 'tore'; rayon: number; epaisseur: number; axe?: SceneAxe; decalage?: SceneVec }
  // 10/11/2026 : un modèle 3D donné par ID Studio IA (STL, GLB, OBJ), montré en un seul bloc. `fichier` : un nom du dossier
  // d'AURORA (jamais un chemin) ; `taille` : son plus grand côté une fois affiché ; `debout` : l'axe du haut dans le fichier
  // (les six, pour « redresse-le ») ; `quart` : quarts de tour sur lui-même (« tourne-le » ; absent : son grand côté se montre seul).
  | { type: 'maillage'; fichier: string; taille: number; debout?: SceneDebout; quart?: number; decalage?: SceneVec };
export type SceneDebout = 'x' | 'y' | 'z' | '-x' | '-y' | '-z';
export interface ScenePiece {
  id: string; nom: string; groupe: string | null; parent: string | null;
  position: SceneVec; rotation?: SceneVec; forme: SceneForme; couleur: SceneCouleur;
  // continu (phase 4) : un tour par période (rotor, roue), sans les bornes min et max.
  articulation?: { axe: SceneAxe; min: number; max: number; repos: number; animation?: { amplitude: number; periode: number; phase?: number; continu?: boolean } };
  eclate?: SceneVec; fonction: string; proprietes?: { nom: string; valeur: string }[]; mots?: string[];
}
/** Phase 4 : un modèle prédéfini (data/scenes) ou un objet généré automatiquement à la demande (id « gen_… »).
 *  10/11/2026 : ou un modèle 3D importé depuis un fichier d’ID Studio IA (id « imp_… »), montré tel quel ; ou une forme
 *  reconstruite sur ce PC depuis une photo d’ID Studio IA (« reconstruit », même id « imp_… »). */
export type SceneOrigine = 'predefini' | 'genere' | 'importe' | 'reconstruit';
export interface SceneDescription {
  format: 'aurora.scene/1'; id: string; titre: string; domaine: string; nature: SceneNature; origine?: SceneOrigine;
  avertissement: string; resume: string; fonctionnement: string; mots: string[];
  camera: { position: SceneVec; cible: SceneVec };
  groupes: { id: string; nom: string; mots?: string[] }[];
  pieces: ScenePiece[];
}
export interface SceneCatalogueItem { id: string; titre: string; domaine: string; nature: SceneNature; resume: string; mots: string[]; origine?: SceneOrigine }
export type SceneAction = 'ouvrir' | 'eclater' | 'assembler' | 'animer' | 'arreter' | 'recentrer' | 'noyau'
  | 'montrer' | 'masquer' | 'isoler' | 'tout_montrer' | 'expliquer' | 'agrandir' | 'interieur'
  // 10/11/2026, sur un modèle gardé seulement (importé, ou reconstruit depuis une photo) : le poser autrement, le
  // tourner d'un quart de tour, lui donner un autre nom (`titre`). Le service garde le réglage.
  | 'redresser' | 'tourner' | 'renommer';
/** One step of a scene order: an action, on a piece or a group of the same scene (`titre`: the new name, « renommer »). */
export interface SceneStep { action: SceneAction; piece?: string; groupe?: string; titre?: string }
/** What the service asks the scene of the home page to do (run.ui.scene). ``suite``: the next steps of a sentence of
 *  several commands (« retire le boîtier et montre les bobines »), applied in this order after the first. */
export interface SceneOrder extends SceneStep { scene: string; suite?: SceneStep[] }
/** A SceneOrder as given to the laboratory: acted on once per id, like GlobeCommand. */
export interface SceneCommand extends SceneOrder { id: number }
/** The scene shown, sent with each request from the home page (« démonte-le », « cette pièce »). */
export interface SceneContext { id: string; piece?: string | null }

export interface ActivityEvent {
  id: number;
  subject_type: 'memory' | 'task' | 'step' | 'conversation' | 'system' | 'permission' | 'provider';
  subject_id: number;
  kind: string;
  label: string;
  run_id: number | null;
  stage: Stage;
  payload: Record<string, any>;
  duration_ms: number | null;
  level: 'info' | 'warn' | 'error';
  created_at: string;
  /** B4: sent live and never stored (llm.delta): id 0, no replay. */
  transient?: boolean;
}

export interface MemoryItem {
  id: number;
  title: string;
  detail: string;
  category: string;
  kind: 'fact' | 'project' | 'preference' | 'decision' | 'document' | 'note' | 'summary';
  source: string;
  source_ref: string;
  confidentiality: 'public' | 'private' | 'sensitive';
  verification: 'verified' | 'unverified' | 'user_confirmed' | 'contradicted';
  pinned: number;
  recall_count: number;
  last_recalled_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryHit { memory: MemoryItem; score: number; matched_by: string[]; snippet: string }

export interface GraphNode {
  id: string;
  type: 'memory' | 'conversation' | 'run' | 'core' | 'level';
  label: string;
  category?: string;
  kind?: string;
  level?: 'immediate' | 'persistent' | 'semantic' | 'actions';
  weight?: number;
  created_at?: string;
  status?: string;
  /** Memory nodes only (S6); absent from older services: fall back to /api/memories. */
  pinned?: boolean;
  verification?: string;
}
export interface GraphLink { source: string; target: string; relation: string; weight: number }
export type MemoryLevel = NonNullable<GraphNode['level']>;
/** Rings view (29/10): true counts in the database, whatever the number of nodes sent. */
export interface GraphGroup { level: MemoryLevel; category: string; count: number; shown: number }
/** A tool of the registry, with its real uses (tool.result events). */
export interface GraphTool {
  name: string; title: string; uses: number; last_used_at: string | null;
  // Carte des outils de la page Tâches seulement (11/11/2026, brain/toolMap.ts) : jamais envoyés par /api/memories/graph.
  /** Grande famille (ToolMapGroup.id) : le secteur de l'outil sur la carte. */
  group?: string;
  family?: string;
  state?: ToolState;
  reason?: string | null;
  /** false : le service n'a pas de compteur. `uses` vaut alors 0 pour le dessin seulement, et n'est jamais affiché. */
  counted?: boolean;
}
export interface GraphRoutine { id: number; name: string; steps: number; runs: number; last_run_at: string | null }
/** Carte des outils : une grande famille, dessinée comme un secteur nommé. `builtins` : ses capacités intégrées, sans outil. */
export interface GraphToolGroup { id: string; title: string; builtins?: number }
export interface MemoryGraphData {
  nodes: GraphNode[];
  links: GraphLink[];
  /** Absent from services older than the rings view (29/10/2026). */
  totals?: Record<MemoryLevel, number>;
  groups?: GraphGroup[];
  tools?: GraphTool[];
  routines?: GraphRoutine[];
  limit?: number;
  /** Carte des outils seulement (BrainGraph layout="tools") : les grandes familles, dans l'ordre du service. */
  tool_groups?: GraphToolGroup[];
}

// ---------------------------------------------------------------- carte des outils (GET /api/tools/map)
// Forme écrite par backend/aurora/core/capabilities.tool_map (reviews/carte-des-outils-serveur.md § 4 et § 9).
/** 'absent' se lit « Indisponible » : il manque quelque chose à l'outil (un réglage, Internet…). */
export type ToolState = 'available' | 'disabled' | 'absent';
export interface ToolNeeds {
  internet: boolean;
  /** null : le mode interdit l'action, aucun accord ne la débloque. */
  permission: boolean | null;
  permission_reason: string;
  risk: string;   // 'low' | 'medium' | 'high'
}
export interface ToolMapTool {
  name: string;
  /** Le nom lisible ; quand le code n'en a pas, c'est l'identifiant (title === name). */
  title: string;
  family: string;
  group: string;
  description: string;
  state: ToolState;
  reason: string | null;
  needs: ToolNeeds;
  /** null : pas de phrase d'exemple vérifiée (jamais une phrase inventée). */
  example: string | null;
  /** null : pas de compteur (jamais affiché comme 0). */
  uses: number | null;
  last_used_at: string | null;
}
/** `builtin` : une capacité d'AURORA elle-même (mémoire, pages…), sans outil du registre ni état. */
export interface ToolMapFamily { id: string; title: string; group: string; description: string; builtin: boolean; tools: number }
export interface ToolMapGroup { id: string; title: string; description: string; families: string[]; tools: number }
export interface ToolMapData {
  checked_at: string | null;
  mode: string;
  /** null : non vérifié (mode privé). */
  internet: boolean | null;
  groups: ToolMapGroup[];
  families: ToolMapFamily[];
  tools: ToolMapTool[];
  /** Modules d'outils qui n'ont pas pu se charger : leurs outils ne sont pas dans `tools`. */
  absent_modules: { module: string; reason: string }[];
  /** null : pas de compteur d'utilisations. `note` : ce que le nombre compte, à montrer telle quelle. */
  usage: { source: string; since: string | null; note: string } | null;
}

export type TaskStatus = 'pending' | 'running' | 'waiting_permission' | 'paused' | 'done' | 'error' | 'cancelled' | 'interrupted';
export interface TaskItem {
  id: number;
  title: string;
  status: TaskStatus;
  result_text: string;
  kind: 'manual' | 'request' | 'plan' | 'diagnostic';
  conversation_id: number | null;
  input_text: string;
  source: string;
  intent: string;
  engine: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  cost_usd: number;
  created_at: string;
  updated_at: string;
}

export type StepStatus = 'pending' | 'running' | 'waiting_permission' | 'done' | 'error' | 'skipped' | 'cancelled';
export interface Step {
  id: number;
  task_id: number;
  idx: number;
  title: string;
  stage: Stage;
  tool: string;
  params_json?: string;
  status: StepStatus;
  requires_permission: number;
  permission_id: number | null;
  result_json?: string;
  error: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface Decision {
  intent: string;
  confidence: number;
  method: 'explicit' | 'rule' | 'semantic' | 'llm' | 'default' | 'clarification';
  slots: Record<string, any>;
  alternatives: [string, number][];
  secondary_intents: string[];
  /** Secondary requests detected but not performed by this plan (named in the answer). */
  unhandled_intents?: string[];
  normalized: string;
  duration_ms: number;
  explanation: string;
}

export interface EngineChoice {
  engine: 'tool' | 'tool+local' | 'tool+cloud' | 'local' | 'cloud' | 'none';
  cloud_tier: 'rapide' | 'standard' | 'expert' | null;
  model: string | null;
  needs_consent: boolean;
  reason: string;
}

export interface Source { title: string; url: string; kind: string; retrieved_at: string }

export interface Permission {
  id: number;
  task_id: number | null;
  step_id: number | null;
  action: string;
  risk: 'low' | 'medium' | 'high';
  title: string;
  preview: Record<string, any>;
  status: 'pending' | 'granted' | 'denied' | 'expired';
  reason: string;
  created_at: string;
  decided_at: string | null;
  expires_at: string | null;
}

/** A4: level of an answer check (backend core/verify.py LEVELS): a note, or the AI's text withheld. */
export type VerifyLevel = 'alerter' | 'bloquer';
export interface Verification {
  ok: boolean;
  /** label: the check's name as a « mod »; level: its level at the time of the run (absent on older runs). */
  checks: { name: string; ok: boolean; detail: string; label?: string; level?: VerifyLevel }[];
  /** Failed checks set to « bloquer »: the answer shows the tools' data instead of the AI's text. */
  blocked?: string[];
}

export interface RunResult {
  task_id: number;
  conversation_id: number | null;
  status: 'done' | 'error' | 'waiting_permission' | 'cancelled' | 'running';
  answer: string;
  decision: Decision;
  engine: EngineChoice;
  sources: Source[];
  steps: Step[];
  permission: Permission | null;
  verification: Verification | null;
  memories_used: { id: number; title: string; score: number }[];
  /** routine (D1): steps the interface sends next, one after the other, as if typed. */
  /** `auto`: a page opened for the subject of the answer, not asked in words (followed while auto_open_tab is on). */
  ui: { navigate?: PageId; auto?: boolean; widget?: string; data?: any; routine?: { id: number; name: string; steps: string[] }; globe?: GlobeOrder; scene?: SceneOrder; lieux?: LieuxPhotos } | null;
  duration_ms: number;
  cost_usd: number;
  error?: string;
  /** What left for Claude in this run, exactly as previewed (CLAUDE.md: always shown). */
  cloud_sends?: CloudSend[];
  /** Standing cloud consent of the conversation (mode Avancé), null when none. */
  conversation_consent?: { conversation_id: number; permission_id: number; granted_at: string } | null;
  /** The answer displays sensitive data (a secret…): never read aloud automatically. */
  shows_sensitive?: boolean;
  /** Where the answer really comes from (verify.origin on the steps that ran); null when nothing answered. */
  origin?: AnswerOrigin | null;
  /** A4: the AI's text held back by a « bloquer » check (verification.blocked), viewable on demand. */
  withheld_answer?: string | null;
  /** P1: the multi-step agent (intent agent.run); absent or null for every other intent. */
  agent?: AgentRun | null;
  /** The model's working memory used by the last AI call of the run (29/10); null when no AI answered. */
  context?: ContextUsage | null;
}

/** One part of the prompt: its length, and its share of the measured total (pro rata of the characters). */
export interface ContextPart {
  key: 'system' | 'tools' | 'memory' | 'conversation' | 'results' | 'question';
  label: string;
  chars: number;
  tokens: number;
}
/** Context of the last AI call: input_tokens is measured by the provider (Ollama prompt_eval_count, Claude usage);
 *  window is the model's real context size (Ollama /api/ps, Anthropic models API), null when unknown. */
export interface ContextUsage {
  provider: 'ollama' | 'anthropic';
  model: string;
  input_tokens: number;
  window: number | null;
  window_source: 'ollama_ps' | 'anthropic_models' | null;
  parts: ContextPart[];
  measured_at: string;
}

/** P3: run.ui.data of intent data.analyze (ui.widget 'data'). Every figure comes from the service's computations;
 *  the file itself never reaches an AI. Live answer only: ui is not kept in the history. */
export interface DataAnalysisView {
  dataset: { name: string; source: 'jointe' | 'fichier'; path: string | null; rows: number; columns: number; skipped: number };
  /** At most 6 key figures. */
  highlights: { label: string; value: number | null; unit: string; digits: number; note: string }[];
  actions: DataAction[];
}
/** A proposed action, computed by code with its proof. Nothing runs without the user's click and confirmation. */
export interface DataAction {
  id: string;
  title: string;
  /** The figure and the computation that justify it. */
  proof: string;
  confidence: 'élevée' | 'moyenne' | 'faible';
  /** What it would do (« Rien n'est modifié : … »). */
  effect: string;
  kind: 'info' | 'memory' | 'routine';
  /** kind 'memory': the sentence to remember. */
  memory_text?: string;
  /** kind 'routine': only when the source is a file of an allowed folder (readable again later). */
  routine?: { name: string; steps: string[] };
}

/** P1: one line of the agent's to-do list — shown ticked only when `done` is true. */
export interface AgentTodo { text: string; done: boolean }
/** Payload of the `agent.todo` event, one per turn (stage local_ai / cloud_ai). */
export interface AgentTurnEvent {
  step_id: number; turn: number; max_turns: number; todo: AgentTodo[];
  /** 'answer' (final answer) · a tool name ('web.search'…) · 'refused' (action refused by AURORA). */
  action: string;
  title: string;
  /** Why the model chose this action (one sentence). */
  reason: string;
  /** A correction turn after a failed answer check. */
  correction?: boolean;
}
export type AgentTurnStatus = 'answer' | 'done' | 'error' | 'skipped' | 'refused' | 'waiting';
export interface AgentRun {
  turns: number; max_turns: number; todo: AgentTodo[];
  /** Why the agent ended without concluding; null when it answered. */
  stopped: null | 'max_turns' | 'no_model' | 'invalid';
  /** The answer was redone after a failed check (auto-correction). */
  corrected: boolean;
  history: { turn: number; action: string; title: string; reason: string; status: AgentTurnStatus; summary: string }[];
}

export type OriginKind = 'mesure' | 'source' | 'memoire' | 'modele';
/** mesure: read on this PC by a tool · source: documents with links · memoire: AURORA's memory ·
 *  modele: the AI wrote with none of these. written_by: an AI wrote the text (over the data, or alone). */
export interface AnswerOrigin {
  kinds: OriginKind[]; written_by: 'local' | 'cloud' | null; tools: string[]; sources: number;
  /** « modele » only: dates and figures written from memory, shown « non vérifié » (A3). */
  unverified?: string[];
}

export interface CloudSend { permission_id: number; model: string | null; text: string; scope: 'once' | 'conversation' }
export interface ConversationConsent { active: boolean; granted_at: string | null; applies: boolean }
/** Where the Claude key comes from — the key itself is never sent back (CLAUDE.md). */
export interface AnthropicKeyStatus { configured: boolean; source: 'vault' | 'env' | null; vault_available: boolean }
/** Local speech recognition (voix D3): Athéna's Whisper service on this PC. `reason` says what is missing. */
export interface LocalRecognitionStatus { available: boolean; running: boolean; started_by_aurora: boolean; engine: string; local: boolean; reason: string }
export interface LocalTranscript { text: string; seconds: number; audio_seconds: number; engine: string }
/** E2, local vision (backend tools/vision.py): a vision model of Ollama, on this PC. */
export interface VisionStatus { available: boolean; model: string; reason: string }
export interface VisionReading { text: string; model: string; seconds: number; processor: string; truncated: boolean }
/** POST /api/vision/screen: the capture itself comes back, so the person sees exactly what was read. */
export interface ScreenReading extends VisionReading { width: number; height: number; image: string }

export interface RunDetail { task: TaskItem; steps: Step[]; events: ActivityEvent[]; permissions: Permission[] }

/** intent: an explicit intent, e.g. 'agent.run' (P1, sent by the composer's « Agent » switch). */
export interface AskOptions { engine?: 'auto' | 'local' | 'cloud'; cloud_tier?: 'rapide' | 'standard' | 'expert'; intent?: string; scene?: SceneContext }
export interface AskInput {
  text: string;
  conversation_id?: number | null;
  source?: 'text' | 'voice' | 'panel' | 'api' | 'routine';
  attachments?: { name: string; text: string }[];
  options?: AskOptions;
}

export interface ToolResult<T = Record<string, any>> {
  ok: boolean;
  data: T;
  summary: string;
  provenance: string;
  sources: Source[];
  duration_ms: number;
  error: string | null;
}

export interface SystemInfo {
  cpu_percent: number;
  ram_percent: number;
  disk_percent: number;
  ram_total_gb: number;
  ram_used_gb?: number;
  disk_total_gb?: number;
  disk_used_gb?: number;
  net_down_kbps?: number;
  net_up_kbps?: number;
  cpu_count?: number;
  cpu_freq_mhz?: number | null;
  uptime_s?: number;
  battery_percent?: number | null;
  gpu?: { name: string; util_percent: number | null; memory_used_mb: number | null; memory_total_mb: number | null } | null;
  temperatures?: Record<string, number> | null;
}
export interface SystemPoint { t: string; cpu: number; ram: number; net_down_kbps: number; net_up_kbps: number }
export interface ProcessInfo { pid: number; name: string; cpu_percent: number; memory_mb: number; username: string | null }

/** Anthropic base price in USD per million tokens (providers/pricing.py, below the long-context threshold). */
export interface TierPrices { input: number; output: number; cache_read: number; cache_write: number }
export interface Capabilities {
  /** null = not probed (e.g. private mode makes no connection test). */
  internet: boolean | null;
  /** chat_models (S5): installed models usable for conversation (embedding models excluded). */
  ollama: { up: boolean; models: string[]; chat_models?: string[]; chat_model: string; embed_model: string; embed_ready: boolean };
  cloud: { configured: boolean; provider: 'anthropic'; tiers: Record<string, { model: string; effort: string; prices?: TierPrices }> };
  mode: Mode;
  /** 08/11/2026: the terminal with permission is on (setting shell_enabled). Absent from an older service. */
  shell?: boolean;
  /** 9/11/2026: the mouse and keyboard are on (setting pc_control_enabled). Absent from an older service. */
  pc_control?: boolean;
  voice_hint: string;
  checked_at: string;
}
export interface Health { status: string; version: string; capabilities: Capabilities }

/** 9/11/2026: one app of the allowed list (Paramètres › Applications autorisées); url is a local address or ''. */
export interface AllowedApp {
  name: string;
  path: string;
  url?: string;
}
/** 9/11/2026: an application found on this PC (Desktop, Start menu: GET /api/apps/pc), to add in one click. */
export interface PcApp extends AllowedApp { source: string; detail: string }
export interface PcApps { apps: PcApp[]; skipped: Record<string, number>; folders: string[] }
/** Paramètres › Démarrage (service : tools/demarrage.py) : ce qui est réellement dans le dossier « Démarrage » de
 *  Windows. `autre` : un fichier de ce nom existe, mais AURORA ne l'a pas écrit. */
export interface StartupState { disponible: boolean; pourquoi: string; actif: boolean; fenetre: boolean; cette_copie: boolean; autre: boolean; fichier: string; lance: string }

export interface Settings {
  mode: Mode;
  /** Spending ceilings per mode (CLAUDE.md): Équilibré 1 $ / day, 0.25 $ / request; Avancé 3 $ and 1 $. */
  cloud_daily_budget_usd: number;
  cloud_request_budget_usd: number;
  cloud_daily_budget_avance_usd: number;
  cloud_request_budget_avance_usd: number;
  default_city: string;
  allowed_roots: string[];
  /** 9/11/2026: the apps AURORA may open by their name (tools/apps.py), empty by default. Absent from an older service. */
  allowed_apps?: AllowedApp[];
  local_model: string;
  local_fast_model: string;
  /** 13/11/2026: the model that writes the 3D maquettes, '' = the main one. Absent from an older service. */
  local_scene_model?: string;
  semantic_router: boolean;
  llm_router: boolean;
  /** B2 (04/11/2026): terminal avec autorisation, désactivé par défaut (une commande, un accord). */
  shell_enabled: boolean;
  /** 9/11/2026: souris et clavier, désactivés par défaut (un geste, un accord ; tools/pc_control.py). */
  pc_control_enabled?: boolean;
  /** 9/11/2026: « Publier sans redemander » — a YouTube comment asked in the chat is posted without a new click
   *  (its card is still made and kept). Off by default (tools/youtube_comments.py). */
  youtube_comment_auto?: boolean;
  /** 24/11/2026: OpenSky Network (world traffic of the globe, fallback for the planes), off until the person turns it
   *  on after reading its terms (tools/sky.py). Absent from an older service. */
  opensky_enabled?: boolean;
  /** 08/11/2026: AURORA opens by itself the tab of the subject (météo, actualités → Monde 3D). On by default. */
  auto_open_tab: boolean;
  theme: 'dark';
  /** E-mail or http(s) URL added to the web user agent (Wikimedia refuses requests without a contact). */
  web_contact: string;
  /** Folder of the daily copy of the base ('' = backups/ next to it); an existing absolute folder. */
  backup_dir?: string;
  /** A4: level of each answer check (« alerter » by default); a partial PATCH keeps the other checks. */
  verify_mods?: Record<string, VerifyLevel>;
  /** Real database path (S1), read-only: never sent back in PATCH (api.updateSettings drops it). */
  db_path?: string;
}

export interface Usage { today_usd: number; budget_usd: number; remaining_usd: number; by_model: { model: string; cost_usd: number; calls: number }[] }
export interface Conversation { id: number; title: string; created_at: string; updated_at: string; message_count?: number }
/** POST /api/attachments/extract (E4): the text of a joined PDF or Word file, and what was read. */
export interface DocumentText {
  name: string; kind: 'pdf' | 'docx'; text: string; pages: number | null; pages_read: number | null;
  chars: number; total_chars: number; truncated: boolean; notes: string[];
  /** 23/11/2026: set when the PDF holds no text at all (scanned, or printed as drawings) and this PC can read its
   *  pages as images: `text` is then empty, and the pages are asked one by one (POST /api/attachments/page).
   *  Absent from a service started before it, which refuses such a PDF as it always did. */
  images?: { model: string; max_pages: number; max_chars: number };
}
/** POST /api/attachments/page: one page of a PDF made of images, drawn by Windows then copied on this PC by the local
 *  vision model. `text` is that model's reading of the page, not the page's own text. */
export interface PageReading {
  page: number; pages: number; text: string; model: string; seconds: number; processor: string; truncated: boolean;
}
/** DELETE /api/conversations/{id}: what went (no copy is kept, at the user's request). */
export interface ConversationDeleted { id: number; messages: number }
export interface ChatMessage { id: number; conversation_id: number; role: 'user' | 'assistant' | 'note'; content: string; run_id: number | null; meta_json: string; created_at: string }

/** D1 « Routines » (backend tasks/routines.py): a named list of ordinary requests, launched by a phrase. */
export interface Routine {
  id: number; name: string; triggers: string[]; steps: string[];
  created_at: string; updated_at: string; runs: number; last_run_at: string | null;
  /** D2 (16/11/2026): absent from a service started before it — the panel then says it comes at the next restart. */
  schedule?: RoutineSchedule | null;
  auto?: RoutineAuto | null;
  /** The next launch by the schedule, « 2026-11-17 08:00 » in the PC's local time; computed by the service.
   *  Always null for a routine launched on an event: nobody knows when it will happen. */
  next?: string | null;
  /** D2, second part: the events this service can launch a routine on, with their words — { start: « au démarrage
   *  d’AURORA » }. Absent from a service started before it: the panel then offers the hour only. */
  events?: Record<string, string>;
  /** D2, last part (« réussi 5 fois de suite, alors proposé en automatique »): set by the service once the routine,
   *  without a schedule, went to its end `count` times in a row by hand (5 at least) — `time`: the usual hour of
   *  these launches, « 08:10 ». Null otherwise, and after « Ne plus proposer ». */
  proposal?: { count: number; time: string | null } | null;
}
/** D2: the days (0 = lundi … 6 = dimanche) the service launches the routine by itself, and either the hour
 *  (« 08:30 ») or, in its place, an event of `Routine.events` (« start »). */
export interface RoutineSchedule { time?: string; event?: string; days: number[]; since?: string }
/** D2: the last launch by the schedule, as the service counted it (tasks/routines.run_scheduled). */
export interface RoutineAuto {
  slot: string; at: string; late_min: number; run_id: number | null; conversation_id: number | null;
  steps: number; done: number; waiting: number; failed: number; over: boolean; interrupted?: boolean; error?: string;
}
/** `schedule`: sent to set it, null to remove it; left out, the service keeps the one the routine has.
 *  `propose`: false = « Ne plus proposer » de la lancer toute seule; left out, nothing changes. */
export interface RoutineInput { name: string; triggers: string[]; steps: string[]; schedule?: { time?: string; event?: string; days: number[] } | null; propose?: boolean }

/** C3 (16/11/2026, backend tasks/reminders.py): a reminder, a timer or an alarm kept by the service. « waiting »: still
 *  to come; « due »: its time came and it waits for « Fait ». `due`, `fired_at` and `ReminderList.now` are the PC's
 *  local time, written « 2026-11-16 18:00:00 ». */
export interface Reminder { id: number; text: string; kind: 'reminder' | 'timer' | 'alarm'; due: string; created_at: string; state: 'waiting' | 'due'; fired_at: string | null }
export interface ReminderList { now: string; items: Reminder[] }

/** « Signaler » (H3, backend tasks/reports.py): the user's verdict on an answer, one per run. */
export type ReportReason = 'misunderstood' | 'wrong_answer' | 'off_topic' | 'other';
/** Why a report is (or is not) a line of the bench export ('ok' = exported). */
export type ReportExportState = 'ok' | 'no_expected' | 'explicit' | 'attachment' | 'sensitive' | 'duplicate' | 'in_bench' | 'in_training';
export interface RunReport {
  id: number;
  run_id: number;
  conversation_id: number | null;
  /** The user's sentence, masked (security/redaction.py) when it was sensitive or held a secret. */
  text: string;
  sensitive: boolean;
  masked: boolean;
  source: string;
  attachments: number;
  got_intent: string;
  method: string;
  expected_intent: string | null;
  reason: ReportReason;
  reason_label: string;
  comment: string;
  /** Bench tag of the export line ('voix' for a dictated sentence, else 'normal'). */
  tag: string;
  created_at: string;
  updated_at: string;
  /** List route only. */
  export?: ReportExportState;
  export_label?: string;
}
export interface ReportInput { reason: ReportReason; expected_intent?: string | null; comment?: string }

/** P2 « Pages » (backend artefacts): a report built by the service from a run's real data, stored on this PC with its versions. */
export type ArtefactKind = 'diagnostic' | 'recherche' | 'routine' | 'banc' | 'reponse' | 'analyse';
export interface ArtefactSource { run_id: number | null; conversation_id: number | null; bench_generated_at: string | null }
export interface ArtefactVersionInfo { version: number; created_at: string; title: string; source: ArtefactSource }
export interface ArtefactSummary {
  id: string; kind: ArtefactKind; title: string; subtitle: string;
  created_at: string; updated_at: string;
  versions: number; latest_version: number; sensitive: boolean;
}
/** history: every version, the most recent first. */
export interface ArtefactDetail extends ArtefactSummary { history: ArtefactVersionInfo[] }
/** created=false: this run (or bench result) was already a version of the page. */
export interface ArtefactMade { artefact: ArtefactSummary; version: number; created: boolean }

/** Studio (backend GET /api/studio): the creator's public activity, every figure read by a real tool.
 *  A source not 'ok' gives its reason in French (summary / error). */
export type StudioState = 'ok' | 'error' | 'private' | 'not_connected' | 'offline';
export interface StudioSource<T> {
  state: StudioState; ok: boolean; data: T | null; summary: string; error: string | null;
  provenance: string; fetched_at: string | null;
  sources: { title: string; url: string; kind: string; retrieved_at: string }[];
}
export interface StudioData {
  generated_at: string;
  mode: 'prive' | 'equilibre' | 'avance';
  links: { site: string; youtube: string; youtube_studio: string; discord: string; github: string[]; kickstarter: string | null };
  github: StudioSource<{
    accounts: { login: string; type: string; public_repos: number; followers: number; url: string }[];
    repos: { name: string; full_name: string; url: string; stars: number; forks: number; open_issues: number; pushed_at: string | null; description: string }[];
    totals: { repos: number; stars: number; forks: number; followers: number };
    /** Recent public events. */
    activity: { type: string; label: string; repo: string; created_at: string; url: string }[];
  }>;
  downloads: StudioSource<{
    total: number;
    repos: { full_name: string; url: string; total: number; releases: { tag: string; name: string; published_at: string | null; downloads: number; url: string }[] }[];
    /** From the names of the published files. */
    by_os: { os: 'Windows' | 'macOS' | 'Linux' | 'Autres'; downloads: number; share: number }[];
  }>;
  discord: StudioSource<{ name: string; members: number | null; online: number | null; invite_url: string; icon_url: string | null }>;
  youtube: StudioSource<{
    /** The public feed only gives the 15 latest videos. */
    channel: string; channel_url: string; video_count: number; total_views: number;
    videos: { id: string; title: string; url: string; published: string | null; views: number | null; likes: number | null;
      comments?: number | null; thumbnail: string | null }[];
    /** With a YouTube API key (Paramètres › Studio): « api »; else the public feed only (« feed »). */
    stats?: 'api' | 'feed'; stats_error?: string | null;
    subscribers?: number | null; subscribers_hidden?: boolean; channel_views?: number | null; channel_videos?: number | null;
  }>;
  /** Latest comment threads of the channel (YouTube Data API, key needed): YouTube Manager's reading, in AURORA. */
  comments?: StudioSource<{
    threads: { id: string; video_id: string; video_title: string; author: string; text: string; published: string | null;
      replies: number; unanswered: boolean; uncertain: boolean; url: string }[];
    /** own = recent threads started by the channel itself: left out of the list, but counted. */
    total: number; unanswered: number; uncertain: number; own?: number;
  }>;
  /** Netlify Analytics; 'not_connected' without a token or while the option is off. */
  site: StudioSource<{ site: string; days: number; visitors: number | null; pageviews: number | null;
                       series: { date: string; visitors: number | null; pageviews: number | null }[] }>;
  /** 'not_connected': no campaign linked. */
  kickstarter: StudioSource<null>;
  /** AURORA's daily readings, oldest first. */
  history: { date: string; downloads: number | null; discord_members: number | null; youtube_views: number | null; github_stars: number | null;
    youtube_subscribers?: number | null }[];
}
export interface StudioSettings {
  github_users: string[]; release_repos: string[]; discord_invite: string; youtube_channel: string;
  site_url: string; netlify_site: string; kickstarter_url: string; netlify_token: boolean; youtube_key?: boolean;
}
/** PUT /api/studio/settings: everything but the secret flags (secrets go to /api/secrets/{netlify|youtube}). */
export type StudioSettingsInput = Omit<StudioSettings, 'netlify_token' | 'youtube_key'>;
/** Netlify token: kept in the Windows vault by the service, never sent back. */
export interface NetlifyTokenStatus { configured: boolean; vault_available?: boolean }
/** Studio secrets in the Windows vault (Netlify token, YouTube API key): same status, never sent back. */
/** 13/11/2026: also the keys of the two online 3D services (« meshy », « huggingface »), kept the same way. */
export type StudioSecretName = 'netlify' | 'youtube' | 'youtube_client_id' | 'youtube_client_secret' | 'meshy' | 'huggingface';
export type StudioSecretStatus = NetlifyTokenStatus;
/** GET /api/youtube/etat: AURORA's own Google connection, to post comments (tools/youtube_comments.py). Never a secret. */
export interface YoutubeConnection {
  client: boolean; connected: boolean; channel_id: string | null; channel_title: string | null;
  connected_at: string | null; same_channel: boolean | null; redirect_uri: string | null; vault_available: boolean;
  /** DELETE only: whether Google confirmed the withdrawal, and what to do when it did not (or in private mode). */
  revoked?: boolean | null; note?: string | null;
}

// ---------- Globe hologramme (Monde 3D, 8/11/2026) : le `data` des routes /api/globe/* (reviews/globe-contrat.md).
// Même forme que les réponses d'Athéna, sans « disponible » : l'enveloppe ToolResult dit ok / error.
// Les dates sont en millisecondes depuis 1970 (mesureA, mesure, date, orbiteDu).

/** Mode Privé : ok=false et data.private_mode=true, sans aucun appel réseau du service. */
export interface GlobePrivateFlag { private_mode?: boolean }
export type GlobeAircraftCategory = 'leger' | 'ligne' | 'rapide';
export interface GlobeAircraft {
  id: string; indicatif: string; pays: string; type: string; categorie?: GlobeAircraftCategory;
  lat: number; lon: number; altitude: number | null; vitesse: number | null; cap: number | null; mesure: number;
}
/** GET /api/globe/avions?lat=&lon= : adsb.lol (ODbL), OpenSky en secours ; carré de ±demiCote degrés. */
export interface GlobeAircraftData { lieu: { nom: string; lat: number; lon: number }; source: string; mesureA: number; demiCote: number; total: number; liste: GlobeAircraft[] }
/** GET /api/globe/monde : échantillon du trafic mondial (OpenSky), [lat, lon, cap, vitesse km/h, altitude m]. */
export interface GlobeTrafficData { source: string; mesureA: number; total: number; avions: [number, number, number, number, number][] }
export interface GlobeSatellite { norad: number; nom: string; role: string; lat: number; lon: number; altitude: number; orbiteDu: number; source?: string; trace: [number, number][] }
/** GET /api/globe/satellites : positions calculées (sgp4) depuis les orbites publiées (CelesTrak). */
export interface GlobeSatellitesData { liste: GlobeSatellite[] }
/** GET /api/globe/meteo?lat=&lon= : Open-Meteo ; `ciel` en français, vent en km/h. */
export interface GlobeWeatherData { temperature: number; code: number; ciel: string; vent: number }
export type GlobeEventType = 'seisme' | 'tsunami' | 'volcan' | 'tornade' | 'cyclone';
export interface GlobeEvent { type: GlobeEventType; titre: string; detail: string; lat: number; lon: number; date: number; source: string; infos?: { cle: string; valeur: string }[] }
/** GET /api/globe/evenements : USGS, NOAA (tsunamis), GDACS (volcans, cyclones), NWS (tornades). */
export interface GlobeEventsData { evenements: GlobeEvent[]; sources: { nom: string; ok: boolean; nombre: number }[] }
/** Grille régulière : nLat × nLon points, du sud-ouest (lat0, lon0), au pas de `pas` degrés. */
export interface GlobeGrid { mesureA: number; pas: number; lat0: number; lon0: number; nLat: number; nLon: number }
/** GET /api/globe/vent : Open-Meteo, vent u / v (m/s), pluie (mm), température (°C), humidité (%). */
export interface GlobeWindData extends GlobeGrid { u: number[]; v: number[]; pluie: number[]; temperature?: number[]; humidite?: number[] }
/** GET /api/globe/air : indice européen de la qualité de l'air (Copernicus CAMS, via Open-Meteo) et PM2,5. */
export interface GlobeAirData extends GlobeGrid { indice: number[]; pm25: number[] }
export type GlobeNewsTheme = 'actualite' | 'economie' | 'sport' | 'culture' | 'science' | 'technologie' | 'voyage';
export interface GlobeArticle {
  titre: string; lien: string; resume: string; image?: string; credit?: string; date: number; source: string;
  video: boolean; theme: GlobeNewsTheme; pays?: string; lat?: number; lon?: number;
}
/** GET /api/globe/actualites : flux RSS des médias ; le pays est trouvé dans le titre puis le résumé. */
export interface GlobeNewsData { mesureA: number; articles: GlobeArticle[]; points: { pays: string; lat: number; lon: number; n: number }[]; sources: { source: string; ok: boolean }[] }
/** POST /api/globe/article (11/11/2026, « Lis-le-moi » / « Explique-le », d'après Athéna payante) : le texte d'un
 *  article du globe lu sur la page du journal, ou son explication en trois phrases par le modèle local (`modele`).
 *  `partiel` : article réservé aux abonnés, seul le début. Refusé : `raison`, et la phrase du service dans `summary`. */
export type GlobeArticleMode = 'lire' | 'expliquer';
export interface GlobeArticleText {
  titre?: string; source?: string; lien?: string; mode?: GlobeArticleMode; texte?: string; partiel?: boolean; modele?: string;
  raison?: 'inconnu' | 'illisible' | 'hors-ligne' | 'modele';
}
/** GET /api/globe/guide?projet=wikipedia|wikivoyage&titre= : résumé de la page (CC BY-SA). */
export interface GlobeGuideData { titre: string; texte: string; lien: string; image?: string }
/** GET /api/globe/detail et /api/globe/nasa : une image JPEG, lue avec le jeton (une balise <img> ne peut pas l'envoyer). */
export type GlobeImage =
  | { ok: true; blob: Blob; bbox: number[] | null; date: string | null }
  | { ok: false; status: number; error: string; private_mode: boolean };
