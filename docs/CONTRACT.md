# AURORA — Contrat d'architecture (v0.2)

Ce document est **la source de vérité** pour tous les modules. Chaque module respecte les
signatures, schémas et noms d'événements ci-dessous. En cas de doute : ce fichier gagne.

AURORA est le successeur du starter ASTRA v0.1 (code d'origine conservé et étendu, jamais jeté).
Le cloud utilise **Claude (Anthropic)** à la place d'OpenAI. L'IA locale reste **Ollama**.

## 0. Principes non négociables

1. **Ce qui se mesure ne se génère pas.** Une valeur mesurable (CPU, RAM, météo, calcul, orbite)
   vient d'un outil. Les réponses des intentions « mesure » sont composées **sans LLM** à partir
   des données de l'outil (gabarit déterministe). Le LLM n'intervient que pour interpréter.
2. **Un seul moteur de décision.** Texte, voix, boutons de panneau et API passent tous par
   `POST /api/ask` → `orchestrator.handle_request()` → `DecisionEngine.decide()`.
   Un bouton de panneau envoie `options.intent` (méthode `explicit`), il ne court-circuite pas.
3. **Toute animation provient d'un événement réel** persisté dans SQLite. Le replay relit les
   événements, il **ne réexécute jamais** rien (aucun appel d'endpoint d'exécution pendant un replay).
4. **Local par défaut.** Rien ne part vers Claude sans la politique de consentement du mode actif.
   Les données envoyées sont **rédigées** (masquage PII) et montrées à l'utilisateur avant envoi.
5. **Le contenu externe est une donnée, jamais une instruction.** Pages web, flux RSS, documents :
   encadrés par `security.untrusted.wrap_external()`, ne peuvent ni accorder de permission ni
   déclencher d'outil. Seul l'utilisateur (endpoint `/api/permissions/{id}`) accorde une permission.
6. **Pas de shell.** L'IA n'a accès qu'aux outils du registre, paramètres validés (pydantic),
   portée limitée, permission obligatoire pour toute action sensible.
7. **Capacités réelles avant génération.** Le prompt système de chaque appel LLM contient l'état
   réel (Internet, Ollama, Claude configuré, mode). On ne corrige pas un texte après coup.
8. **Erreurs honnêtes.** Un outil indisponible renvoie `ok=False` + message clair en français.
   Jamais de fausse donnée de remplacement.

## 1. Arborescence et propriétaires

```
aurora/
  backend/                       (Python 3.11, lancé depuis ce dossier)
    requirements.txt
    requirements-voix.txt         la voix neuronale (piper-tts), facultative : installée à part depuis le 23/11/2026
    aurora/
      __init__.py                 __version__ = "0.2.0"
      config.py                   [B5] chemins, variables d'environnement, constantes
      db.py                       [B1] SQLite : schéma, migration ASTRA, connect(), rows(), insert_event()
      main.py                     [B5] FastAPI : routes HTTP + SSE
      core/
        normalize.py              [B2] normalisation FR (accents, fautes, mot d'éveil)
        intents.py                [B2] taxonomie des intentions (INTENTS)
        rules.py                  [B2] routeur par règles
        semantic.py               [B2] routeur par similarité (n-grammes de caractères ; EmbeddingRouter seulement dans le banc d'essai)
        router.py                 [B2] DecisionEngine
        engine_select.py          [B2] choix du moteur (outil / local / Claude) selon mode
        capabilities.py           [B5] état réel des capacités (Internet, Ollama, Claude)
        prompts.py                [B5] prompts système FR
        planner.py                [B5] plan d'étapes (intentions composées, task.plan)
        verify.py                 [B5] vérification des résultats
        compose.py                [B5] gabarits déterministes de réponse par intention
        orchestrator.py           [B5] handle_request / resume / cancel
      providers/
        base.py                   [B3] ProviderResult, ProviderError, ChatProvider
        pricing.py                [B3] tarifs Claude
        ollama.py                 [B3] chat, embed, classify
        anthropic_provider.py     [B3] Claude (SDK officiel `anthropic`)
      security/
        auth.py                   [B3] jeton de session + contrôle d'origine
        policy.py                 [B3] actions, risques, besoin de permission selon mode
        redaction.py              [B3] masquage PII avant cloud
        untrusted.py              [B3] encadrement du contenu externe + détection d'injection
        budget.py                 [B3] budget cloud quotidien
        consent.py                [B3] demandes de permission (DB)
      memory/
        store.py                  [B1] CRUD mémoires, liens, graphe, conversations
        search.py                 [B1] recherche hybride FTS5 + sémantique
        embeddings.py             [B1] vecteurs bge-m3 via Ollama (optionnel)
        context.py                [B1] mémoire immédiate + souvenirs pertinents
      tools/
        base.py                   [B4] ToolSpec, ToolResult, Source, run_tool, REGISTRY
        system.py                 [B4] psutil : instantané, historique, processus, GPU si dispo
        diagnose.py               [B4] diagnostic règles + causes probables
        calc.py                   [B4] évaluateur AST sûr + conversions d'unités
        timeinfo.py               [B4] date/heure
        weather.py                [B4] Open-Meteo (géocodage + actuel + villes du globe)
        web.py                    [B4] recherche web : SearXNG local, puis Wikipedia (FR puis EN) + extraits, puis DuckDuckGo
        news.py                   [B4] flux RSS scientifiques
        quakes.py                 [B4] séismes USGS (GeoJSON)
        orbit.py                  [B4] simulation orbitale SciPy + système solaire (éléments képlériens)
        files.py                  [B4] scan lecture seule + quarantaine réversible (permission)
        processes.py              [B4] arrêt de processus utilisateur (permission, protections)
        data.py                   [P3] tableau CSV (joint ou dossier autorisé) + calculs fixes numpy : data.describe,
                                  data.group, data.trend, data.outliers, data.compare (action files.scan, lecture seule)
        lab.py                    [08/11/2026] laboratoire 3D de l'accueil : scènes aurora.scene/1 de data/scenes,
                                  validées strictement, cache par fichier ; lab.catalogue, lab.scene (sans réseau,
                                  system.read, déterministes). Une scène invalide reste hors du catalogue (journal).
                                  Phase 4 : build_generated répare, complète et valide la description JSON écrite
                                  par l'IA (étape « scene », schéma generation_schema, jamais de code), puis la garde
                                  dans AURORA_HOME/scenes_generees/gen_<objet>.json (id gen_…, origine « genere »,
                                  nature « illustration », jamais écrasée, 200 au plus)
      tasks/
        events.py                 [B5] emit() + EventBus (SSE)
        runs.py                   [B5] persistance runs/étapes
        artefacts.py              [P2] pages : blocs fixes, constructeurs, rangement versionné (~/.aurora/artefacts)
        artefact_html.py          [P2] HTML autonome d'une page (texte échappé, SVG, aucun script)
      data/
        router_examples.jsonl     [B2] exemples d'entraînement du routeur sémantique
    tests/
      data/routing_bench.jsonl    [B2] banc d'essai (phrases DIFFÉRENTES des exemples)
      bench_routing.py            [B2] mesure exactitude + latence p50/p95
      test_*.py                   [chacun] unittest (stdlib), pas de réseau par défaut
  frontend/                       (Vite + React 19 + TypeScript)
  docs/
```

Environnement Python : `%USERPROFILE%\.aurora\venv\Scripts\python.exe` (chemin court ;
le dossier projet dépasse la limite Windows de 260 caractères pour pip). Tests :
`cd backend && <python> -m unittest discover -s tests -t .`

Variables d'environnement (toutes optionnelles) — lues dans `config.py` :

| Variable | Défaut | Rôle |
|---|---|---|
| `AURORA_DB_PATH` | `~/.aurora/aurora.db` | base SQLite |
| `AURORA_HOME` | `~/.aurora` | données (quarantaine, logs) |
| `AURORA_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama |
| `AURORA_LOCAL_MODEL` | `qwen3.5:9b` | conversation locale |
| `AURORA_LOCAL_FAST_MODEL` | `qwen3.5:4b` | classification, résumés courts |
| `AURORA_EMBED_MODEL` | `bge-m3` | embeddings |
| `ANTHROPIC_API_KEY` | — | clé Claude en secours ; source principale : coffre Windows, cible `AURORA/anthropic_api_key` (security/vault.py), écrite par `PUT /api/secrets/anthropic` (`{api_key}`, jamais renvoyée ; GET → `{configured, source, vault_available}` ; DELETE la retire). `AURORA_VAULT=0` ignore le coffre (tests). |
| `AURORA_CLOUD_MODEL_RAPIDE` | `claude-haiku-5-5` | palier rapide |
| `AURORA_CLOUD_MODEL_STANDARD` | `claude-sonnet-5-5` | palier standard (défaut cloud) |
| `AURORA_CLOUD_MODEL_EXPERT` | `claude-opus-5-5` | palier expert : mode Avancé seulement, sur demande explicite (« mode expert », « Claude Opus », choix dans l'interface) ; en Équilibré, abaissé au palier standard avec une note |
| `AURORA_OFFLINE` | `0` | `1` = aucun appel réseau (tests) |
| `AURORA_ALLOWED_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | CORS + contrôle d'origine ; seules les origines de la boucle locale (127.0.0.1, localhost, *.localhost, [::1]) sont retenues, `*` et toute origine publique sont ignorés |
| `AURORA_SESSION_TOKEN` | aléatoire | jeton fourni au démarrage par un lanceur (Tauri prévu), 32 à 256 caractères URL-safe ; sinon ignoré |
| `AURORA_BOOTSTRAP` | `1` | `0` : `/api/bootstrap` répond 404 ; le jeton vient uniquement du lanceur, qui l'injecte dans l'interface (`window.__AURORA_TOKEN__`) |

## 2. Schéma SQLite (propriétaire B1 — `db.py`)

`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON`. Horodatage ISO UTC
`strftime('%Y-%m-%dT%H:%M:%fZ','now')` noté `NOW`.

```sql
conversations(id INTEGER PK, title TEXT NOT NULL DEFAULT '', created_at NOW, updated_at NOW)
messages(id PK, conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('user','assistant','note')), content TEXT NOT NULL,
  run_id INTEGER NULL, meta_json TEXT NOT NULL DEFAULT '{}', created_at NOW)

memories(id PK, title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'general',
  kind TEXT NOT NULL DEFAULT 'fact' CHECK(kind IN ('fact','project','preference','decision','document','note','summary')),
  source TEXT NOT NULL DEFAULT 'user',         -- 'user' | 'conversation' | 'tool:<nom>' | 'web' | 'astra-import'
  source_ref TEXT NOT NULL DEFAULT '',         -- URL, id de run, chemin
  confidentiality TEXT NOT NULL DEFAULT 'private' CHECK(confidentiality IN ('public','private','sensitive')),
  verification TEXT NOT NULL DEFAULT 'unverified' CHECK(verification IN ('verified','unverified','user_confirmed','contradicted')),
  pinned INTEGER NOT NULL DEFAULT 0, recall_count INTEGER NOT NULL DEFAULT 0, last_recalled_at TEXT NULL,
  created_at NOW, updated_at NOW)
memories_fts  -- FTS5(title, detail, category, content='memories', content_rowid='id',
              --      tokenize='unicode61 remove_diacritics 2') + triggers ai/ad/au
memory_embeddings(memory_id INTEGER PK REFERENCES memories(id) ON DELETE CASCADE,
  model TEXT NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL, updated_at NOW)   -- float32 little-endian
memory_links(id PK, source_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  target_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  relation TEXT NOT NULL CHECK(relation IN ('related','part_of','contradicts','updates','derived_from')),
  weight REAL NOT NULL DEFAULT 0.5, created_by TEXT NOT NULL DEFAULT 'auto' CHECK(created_by IN ('user','auto')),
  created_at NOW, UNIQUE(source_id, target_id, relation))

tasks(id PK, title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','waiting_permission','paused','done','error','cancelled','interrupted')),
  result_text TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'manual' CHECK(kind IN ('manual','request','plan','diagnostic')),
  conversation_id INTEGER NULL, input_text TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual',
  intent TEXT NOT NULL DEFAULT '', decision_json TEXT NOT NULL DEFAULT '{}', engine TEXT NOT NULL DEFAULT '',
  parent_id INTEGER NULL, started_at TEXT NULL, finished_at TEXT NULL, duration_ms REAL NULL,
  cost_usd REAL NOT NULL DEFAULT 0, created_at NOW, updated_at NOW)
task_steps(id PK, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, idx INTEGER NOT NULL,
  title TEXT NOT NULL, stage TEXT NOT NULL,         -- tool | local_ai | cloud_ai | verify | memory | response
  tool TEXT NOT NULL DEFAULT '', params_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','waiting_permission','done','error','skipped','cancelled')),
  requires_permission INTEGER NOT NULL DEFAULT 0, permission_id INTEGER NULL,
  result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
  started_at TEXT NULL, finished_at TEXT NULL, duration_ms REAL NULL)

events(id PK, subject_type TEXT NOT NULL CHECK(subject_type IN ('memory','task','step','conversation','system','permission','provider')),
  subject_id INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL, label TEXT NOT NULL,
  run_id INTEGER NULL, stage TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}',
  duration_ms REAL NULL, level TEXT NOT NULL DEFAULT 'info' CHECK(level IN ('info','warn','error')),
  created_at NOW)
  -- index (run_id), (created_at)

permission_requests(id PK, task_id INTEGER NULL, step_id INTEGER NULL, action TEXT NOT NULL,
  risk TEXT NOT NULL CHECK(risk IN ('low','medium','high')), title TEXT NOT NULL,
  preview_json TEXT NOT NULL DEFAULT '{}',   -- ce qui sera fait / envoyé, montré à l'utilisateur
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','granted','denied','expired')),
  reason TEXT NOT NULL DEFAULT '', created_at NOW, decided_at TEXT NULL, expires_at TEXT NULL)

settings(key TEXT PK, value_json TEXT NOT NULL)
cloud_usage(id PK, task_id INTEGER NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0, created_at NOW)
```

**Migration ASTRA** : au premier `initialize()`, si la base AURORA est neuve et que
`~/.astra/astra.db` (ou `ASTRA_DB_PATH`) existe, copier `memories` (source=`astra-import`),
`tasks` (kind=`manual`) et `events` (kinds d'origine conservés), puis émettre un événement
`system.imported`. Jamais d'écriture dans la base ASTRA.

API `db.py` (garder les fonctions du starter, compatibles) :
```python
def db_path() -> Path
@contextmanager
def connect() -> Iterator[sqlite3.Connection]          # row_factory=Row, commit/rollback
def initialize() -> None                               # idempotent, crée tout + migration
def rows(query: str, params=()) -> list[dict]
def row(query: str, params=()) -> dict | None
def insert_event(conn, *, subject_type, subject_id, kind, label, run_id=None, stage='',
                 payload: dict | None = None, duration_ms=None, level='info') -> dict
def get_setting(key: str, default=None) -> Any
def set_setting(key: str, value: Any) -> None
# compat starter (tâches manuelles) : add_task, set_task_status, complete_task, add_memory,
# delete_memory, clear_events — ils écrivent désormais leurs événements via insert_event
# avec les kinds 'task.pending' / 'task.running' / 'task.done' / 'task.error' / 'memory.saved' / 'memory.deleted'
```
Les fonctions DB qui écrivent un événement retournent aussi l'événement créé ou appellent
`aurora.tasks.events.publish(event_dict)` (pas d'écriture DB) pour le diffuser en SSE.
`tasks/events.py` n'importe que `db.py` (pas de cycle).

## 3. Événements (propriétaire B5 — `tasks/events.py`)

```python
def emit(kind: str, label: str, *, subject_type='task', subject_id=0, run_id=None, stage='',
         payload: dict | None = None, duration_ms: float | None = None, level='info') -> dict
    # insère via db.insert_event puis publish()
def publish(event: dict) -> None        # thread-safe, vers tous les abonnés SSE
class EventBus:
    def attach_loop(self, loop) -> None
    def subscribe(self) -> asyncio.Queue
    def unsubscribe(self, q) -> None
bus: EventBus
```
Forme JSON d'un événement (identique en REST et SSE) :
```json
{"id": 42, "subject_type": "task", "subject_id": 8, "kind": "tool.result", "label": "Mesure système",
 "run_id": 8, "stage": "tool", "payload": {...}, "duration_ms": 12.4, "level": "info",
 "created_at": "2026-10-28T09:30:00.000Z"}
```
`stage` ∈ `input | decision | tool | local_ai | cloud_ai | permission | verify | response | memory | system`.

| kind | stage | payload |
|---|---|---|
| `run.created` | input | `{text, source, conversation_id}` — `text` = les 2 000 premiers caractères de la demande (clé d'appariement côté interface) |
| `route.decided` | decision | `{intent, confidence, method, slots, alternatives, secondary_intents, engine, cloud_tier, model, reason, ms}` |
| `plan.created` | decision | `{steps:[{id, idx, title, stage, tool, requires_permission}]}` |
| `step.started` | stage de l'étape | `{step_id, idx, title, tool}` |
| `tool.result` | tool | `{step_id, tool, ok, summary, provenance, sources:[...], ms}` |
| `model.result` | local_ai / cloud_ai | `{step_id, provider, model, input_tokens, output_tokens, cost_usd, ms, stop_reason}` |
| `step.done` / `step.error` / `step.skipped` | stage de l'étape | `{step_id, idx, error?}` |
| `permission.requested` | permission | `{permission_id, action, risk, title, preview}` |
| `permission.granted` / `permission.denied` | permission | `{permission_id, action, reason?}` — `reason: 'expired'` quand la demande a expiré sans décision (30 min) : le run est alors terminé proprement (« Autorisation expirée sans décision… », `run.done`). Accord de conversation : `{…, scope:'conversation', conversation_id}` |
| `permission.revoked` | permission | `{action:'cloud.send', scope:'conversation', conversation_id, reason}` — accord de conversation révoqué (bouton, ou mode Avancé quitté) |
| `verify.passed` / `verify.failed` | verify | `{checks:[{name, ok, detail}]}` |
| `agent.todo` | local_ai / cloud_ai | P1, un par tour de l'agent (subject_type=`step`) : `{step_id, turn, max_turns, todo:[{text, done}], action, title, reason, correction?}` — `action` = un outil, `'answer'` (réponse finale) ou `'refused'` (action refusée par AURORA ; `reason` dit pourquoi) |
| `memory.recalled` | memory | `{memory_ids, method}` |
| `memory.saved` / `memory.updated` / `memory.deleted` | memory | `{memory_id, title, category, ...}` (subject_type=`memory`) |
| `memory.linked` | memory | `{memory_id, source_id, target_id, relation, weight, created_by, title, category}` |
| `response.ready` | response | `{chars, engine, deterministic}` |
| `run.done` / `run.error` / `run.cancelled` / `run.interrupted` / `run.resumed` | response | `{status, duration_ms, cost_usd, error?}` |
| `system.imported` / `system.started` | system | `{...}` |
| `system.backup` | system | `{path, schema_version}` — copie faite avant une migration de schéma (CLAUDE.md), dans `backups/` à côté de la base ; une base d'un schéma plus récent est refusée sans être modifiée |

Kinds hérités d'ASTRA (lignes importées) : `pending running done error saved deleted` — le
frontend les affiche aussi.

## 4. Mémoire (propriétaire B1)

```python
# memory/store.py
def add_memory(title, detail='', category='general', *, kind='fact', source='user', source_ref='',
               confidentiality='private', verification='unverified', pinned=False,
               run_id=None, auto_link=True) -> dict          # émet memory.saved (+ memory.linked)
def update_memory(memory_id, **fields) -> dict | None      # émet memory.updated
def delete_memory(memory_id) -> bool                       # émet memory.deleted (titre générique)
def get_memory(memory_id) -> dict | None
def list_memories(*, category=None, kind=None, limit=150) -> list[dict]
def link_memories(source_id, target_id, relation='related', weight=0.5, created_by='user') -> dict
def mark_recalled(memory_ids: list[int], run_id: int | None, method: str) -> None   # émet memory.recalled
def graph(limit=120) -> dict
    # {"nodes":[{id:"m:12", type:"memory"|"conversation"|"run"|"core"|"level", label, category, kind,
    #            level:"immediate"|"persistent"|"semantic"|"actions", weight, created_at, status?,
    #            pinned: bool, verification: str  (nœuds 'memory' seulement)}],
    #  "links":[{source, target, relation, weight}]}
    # nœuds 'level' fixes : level:immediate, level:persistent, level:semantic, level:actions ; nœud 'core'
def create_conversation(title='') -> dict
def add_message(conversation_id, role, content, *, run_id=None, meta=None) -> dict
def recent_messages(conversation_id, limit=12) -> list[dict]
def list_conversations(limit=50) -> list[dict]

# memory/search.py
@dataclass
class MemoryHit: memory: dict; score: float; matched_by: list[str]; snippet: str
def search(query: str, *, limit=8, mode='hybrid', min_score=0.0) -> list[MemoryHit]   # sync, FTS5 BM25
async def search_async(query, *, limit=8, mode='hybrid') -> list[MemoryHit]           # + sémantique si embeddings dispo

# memory/embeddings.py   (optionnel : tout fonctionne sans Ollama)
async def embed_texts(texts: list[str]) -> list[list[float]] | None
async def ensure_embedding(memory_id: int) -> bool
async def backfill(limit=200) -> int

# memory/context.py — mémoire immédiate + souvenirs pertinents, JAMAIS toute la mémoire
@dataclass
class ContextPack: turns: list[dict]; memories: list[MemoryHit]; text: str   # text = bloc prêt pour le prompt
async def build_context(conversation_id: int | None, query: str, *, max_memories=5, max_turns=6,
                        budget_chars=6000, include_sensitive=False, run_id=None,
                        mark_recalled=True) -> ContextPack
```
Chaque souvenir injecté garde : source, date, confidentialité, statut de vérification.
Les souvenirs `sensitive` ne sont jamais inclus dans un contexte destiné au cloud — ni directement,
ni à travers les derniers échanges : un message qui a affiché un souvenir sensible porte
`meta.sensitive=true` et il est remplacé par « [message affichant un souvenir sensible : masqué] »
quand `include_sensitive=False`. De même, un message de l'utilisateur jugé sensible (`sensitivity_of`,
par exemple un mot de passe tapé) porte `meta.sensitive=true` et il est remplacé par
« [message de l’utilisateur jugé sensible : masqué] ». Les résumés automatiques (`kind='summary'`) sont annoncés comme
mesures datées et périmées ; les souvenirs de source `tool:*`, `web`, `astra-import` et les documents
sont encadrés comme contenu externe. L'orchestrateur ne compte un rappel (`memory.recalled`) que
si une étape IA lit réellement le contexte.
Chaque réponse d'AURORA dans les derniers échanges dit qui l'a rédigée (08/11/2026), d'après le badge
stocké avec elle (`meta.origin`) : « AURORA (rédigé par Claude, claude-sonnet-5-5) », « AURORA (rédigé par
l’IA locale, gemma4:e4b) », « AURORA (composé à partir de ses outils, sans IA) », « AURORA (composé sans IA) » ;
le modèle n'est nommé que si `meta.context` vient du même fournisseur, et un ancien message sans badge reste
« AURORA ». La règle 8 du prompt (`core/prompts.RULES`) interdit au modèle de s'attribuer une réponse
rédigée par un autre.

## 5. Moteur de décision (propriétaire B2)

Intentions (`core/intents.py`, dict `INTENTS[name] = {label, kind: 'measure'|'action'|'lookup'|'generative', slots:[...] }`) :

| intent | kind | slots | exemple |
|---|---|---|---|
| `system.status` | measure | `metric` (cpu, ram, disk, network, gpu, battery, uptime, temperature, all) | « Quelle quantité de RAM est utilisée ? » · « Depuis combien de temps mon PC est allumé ? » |
| `system.processes` | measure | `sort` (cpu, memory) | « Quels programmes consomment le plus ? » |
| `system.diagnose` | measure | — | « Pourquoi mon PC ralentit ? » |
| `system.kill_process` | action | `name`, `pid` | « Ferme Chrome » |
| `memory.search` | lookup | `query` (`''` = « que sais-tu de moi ? » : liste des souvenirs, épinglés d'abord) | « Retrouve notre projet sur Mars » |
| `memory.save` | action | `content`, `category` | « Retiens que je préfère le mode sombre » |
| `web.search` | lookup | `query` | « Recherche les dernières découvertes spatiales » |
| `news.latest` | lookup | `topic`, `monde?` (08/11/2026, normalize.extract_world_news : `{pays?, theme?}`, vide pour « les infos du jour » ; absent pour la science ou un sujet comme l'espace, qui gardent les 5 flux scientifiques. Avec `monde`, le plan passe à `news.world`, 27 flux RSS filtrés par pays et thème, et le globe montre les actualités du pays) | « Quoi de neuf dans l'actualité scientifique ? » · « Quelles sont les actualités en Ukraine ? » |
| `weather.current` | measure | `city`, `days?` (décalages de jours : demain → [1], week-end → [samedi, dimanche]) | « Quel temps fera-t-il demain à Lyon ? » |
| `quakes.recent` | measure | — | « Y a-t-il eu des séismes aujourd'hui ? » |
| `calc.evaluate` | measure | `expression` | « Combien font 17 × 23 ? » |
| `calc.convert` | measure | `value`, `from_unit`, `to_unit` | « Convertis 30 miles en km » |
| `time.now` | measure | `place?` (ville ; l'outil la résout en fuseau ou répond honnêtement « indisponible ») | « Quelle heure est-il à Tokyo ? » |
| `sim.orbit` | measure | `body`, `altitude_km` | « Simule la trajectoire d'une sonde autour de Mars » |
| `sim.solar_system` | measure | `date` | « Montre la position des planètes » |
| `files.scan` | lookup | `folder` (chemin repris tel quel, `/` ou `\`), `older_than_days`, `min_size_mb?` (1 Mo = 2^20 octets), `size_default?` (« gros fichiers » sans nombre : seuil par défaut de 100 Mo, dit dans la réponse) | « Quels vieux fichiers dans Téléchargements ? » · « Trouve les fichiers de plus de 500 Mo dans Documents » |
| `files.cleanup` | action | `folder`, `older_than_days`, `min_size_mb?`, `size_default?` | « Supprime mes anciens fichiers » |
| `doc.analyze` | generative | — (texte joint) | « Analyse ce document » |
| `task.plan` | generative | `goal` | « Organise mes recherches de la semaine » |
| `agent.run` | generative | — | « En mode agent : regarde ma RAM puis cherche pourquoi elle est haute » (ou l'interrupteur « Agent » du Chat, qui envoie `options.intent = 'agent.run'`) |
| `data.analyze` | lookup | `source` (fixée par AURORA : `jointe:<run>:<n>` ou `fichier:<chemin>`), `source_error` | « Analyse Documents\depenses.csv » ; un tableau joint (`tools/data.is_table`) fait aussi passer `chat.general` / `doc.analyze` à `data.analyze`. Plan : lecture + socle choisi par le code (tour 0), puis l'agent restreint aux outils `data.*` (4 tours au plus, la source jamais choisie par le modèle) ; sans IA, `compose.data_summary` |
| `ui.navigate` | action | `page` (home, chat, search, computer, world, simulations, memory, tasks, settings), `globe` (page world : couche, lieu, satellite), `scene` (08/11/2026, page home : `{scene, action, piece?, groupe?}` ; avec `options.scene`, aussi `inconnu` ou `sans_selection` quand la pièce manque, et `suite` = les ordres suivants d'une phrase de plusieurs commandes), `sujet_3d` (objet demandé en 3D sans scène au catalogue : phase 4, outil `lab.catalogue` puis étape IA `scene` qui construit la maquette ; si elle échoue, réponse honnête et rien ne s'ouvre) | « Ouvre la mémoire », « Montre l'ISS sur le globe », « Montre-moi comment fonctionne un bras robotique », « Montre-moi un moteur électrique en 3D » ; scène affichée : « démonte-le », « agrandis cette pièce » |
| `meta.capabilities` | lookup | — | « As-tu accès à Internet ? » · « Rappelle-moi demain à 9 h… » (réponse : aucun rappel possible) · « C'est toi ou Claude qui a écrit ce code ? » (08/11/2026, `rules.asks_answer_author` : l'auteur de la réponse précédente de la conversation, ou de la dernière avec du code quand la question nomme du code, est lu sur sa trace — `meta.origin.written_by` du message, modèle lu sur les étapes IA de sa tâche, sinon `meta.context` — par `Orchestrator._answer_author` puis `compose.answer_author_line`, sans aucun modèle ; avec un fichier joint, la même question reste `doc.analyze`) |
| `chat.general` | generative | — | tout le reste |

```python
# core/normalize.py
def normalize(text: str) -> str      # minuscules, sans accents, ponctuation→espace, fautes courantes,
                                     # retire « aurora/astra » en tête, nombres conservés
# core/router.py
@dataclass
class Decision:
    intent: str; confidence: float; method: str          # 'explicit'|'rule'|'semantic'|'llm'|'default'
    slots: dict; alternatives: list[tuple[str, float]]; secondary_intents: list[str]
    normalized: str; duration_ms: float; explanation: str
    def to_dict(self) -> dict
class DecisionEngine:
    def __init__(self, *, llm_classifier=None, use_semantic=True, semantic_threshold=None): ...
    def decide_fast(self, text: str, *, intent_hint: str | None = None) -> Decision   # règles + sémantique, < 30 ms
    async def decide(self, text: str, *, intent_hint=None, context=None) -> Decision  # + LLM si ambigu et dispo
engine: DecisionEngine   # instance partagée paresseuse : get_engine()
```
Ordre : `intent_hint` (explicit) → règles → similarité sémantique (seuil + marge, testée sur le banc)
→ classifieur LLM local (optionnel, délai court, sortie JSON contrainte) → `chat.general`.
`secondary_intents` : intentions composées détectées (« analyse le problème **et** recherche des solutions »).
Le planificateur exécute les secondaires mono-outil, `web.search`, `memory.save` et `memory.search` ; les
autres (nettoyage, arrêt de programme…) ne sont jamais enchaînées : le `RunResult.decision` ne garde
alors dans `secondary_intents` que celles réellement traitées et liste les autres dans
`unhandled_intents`, et la réponse dit « Non traité dans cette demande : … ».
Les nombres « 2 000 » (espace, U+00A0 ou U+202F comme séparateur de milliers), « 1 500,5 » et « -300 »
sont lus tels quels (`normalize.join_thousands`).

```python
# core/engine_select.py
@dataclass
class EngineChoice:
    engine: str            # 'tool' | 'tool+local' | 'tool+cloud' | 'local' | 'cloud' | 'none'
                           # memory.search, files.scan, news.latest, meta.capabilities : 'tool' (gabarit, pas de LLM)
                           # après un repli local (refus, expiration, budget, erreur) : moteur réellement utilisé
    cloud_tier: str | None # 'rapide' | 'standard' | 'expert'
    model: str | None
    needs_consent: bool
    reason: str            # phrase FR affichée dans l'interface
def select_engine(decision: Decision, *, mode: str, text: str, caps: dict,
                  user_pref: str = 'auto', cloud_tier_pref: str | None = None,
                  sensitivity: str = 'normal', budget_remaining_usd: float = 0.0) -> EngineChoice
```
Règles : `measure` → `tool` (réponse déterministe) ; `system.diagnose` → `tool+local` si Ollama dispo
sinon `tool`; `generative`/`lookup` complexe → local d'abord ; cloud seulement si le mode l'autorise,
Claude configuré, budget restant, et (demande explicite « utilise Claude / mode expert », ou
complexité élevée, ou Ollama indisponible). `sensitivity='sensitive'` → jamais de cloud en mode
équilibré sans consentement, jamais en mode privé.

**Modes** (`settings.mode`) :
- `prive` : outils locaux + Ollama. Aucun appel Claude. Requêtes web : permission à chaque fois.
- `equilibre` (défaut) : local d'abord ; Claude possible avec **consentement par requête**
  (aperçu du texte rédigé + modèle + coût estimé).
- `avance` : Claude autorisé sans demande pour les données non sensibles ; consentement si sensible.

Banc d'essai : `tests/bench_routing.py` affiche l'exactitude (règles seules, règles+sémantique,
+LLM si `--llm`) et la latence p50/p95, et écrit `~/.aurora/bench_latest.json`. Le corpus du banc
contient fautes d'orthographe, transcriptions vocales imparfaites, demandes ambiguës et
contre-exemples (≥ 180 lignes). Le routeur sémantique n'est activé par défaut que s'il améliore
le score sans en faire perdre (le banc le vérifie).

Signalements rejoués (A1) : « Lancer le banc d'essai » (`POST /api/quality/bench`) rejoue aussi les
signalements exportables (`tasks/reports.bench_rows`, état `ok`) dans un bloc `reports`
`{n, ignored, rules, rules_semantic?, cases:[{run_id, text, expected, reported, now, now_semantic?}]}`,
**hors** du score du corpus ; `{error}` si les signalements sont illisibles. En ligne de commande :
`--reports FICHIER` rejoue un export, `--add-reports FICHIER` l'ajoute au corpus (doublons, exemples
d'entraînement, intentions et étiquettes inconnues écartés ; ajout en fin de fichier, rien n'est réécrit).

## 6. Fournisseurs IA et sécurité (propriétaire B3)

```python
# providers/base.py
@dataclass
class ProviderResult:
    text: str; provider: str; model: str
    input_tokens: int = 0; output_tokens: int = 0; cache_read_tokens: int = 0; cache_write_tokens: int = 0
    cost_usd: float = 0.0; duration_ms: float = 0.0; stop_reason: str = ''; meta: dict = field(default_factory=dict)
class ProviderError(Exception):
    kind: str   # 'unavailable'|'auth'|'rate_limit'|'overloaded'|'bad_request'|'timeout'|'refusal'|'budget'|'connection'
    message_fr: str   # message sûr pour l'interface (jamais de clé, jamais d'en-têtes)
# messages = [{"role": "user"|"assistant", "content": str}]
# providers/ollama.py
class OllamaProvider:
    async def available(self) -> bool                      # cache 10 s
    async def models(self) -> list[dict]
    async def chat(self, messages, *, system: str, model: str | None = None, max_tokens=1024,
                   on_token: Callable[[str], Awaitable[None]] | None = None, timeout=120) -> ProviderResult   # think=False
    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]
    async def classify(self, text: str, labels: list[str], *, instructions: str, timeout=8) -> tuple[str, float] | None
ollama: OllamaProvider   # instance partagée
# providers/anthropic_provider.py — SDK officiel `anthropic` (AsyncAnthropic), streaming
class AnthropicProvider:
    def configured(self) -> bool
    def tiers(self) -> dict      # {'rapide': {...model, effort}, 'standard': {...}, 'expert': {...}}
    def estimate_cost(self, text_chars: int, tier: str) -> float
    async def chat(self, messages, *, system: str, tier='standard', max_tokens=4096,
                   on_token=None, web_search=False, timeout=180, system_dynamic='') -> ProviderResult
    # system = partie stable (mise en cache) ; system_dynamic = date + état mesuré (après, sans cache)
    # max_tokens envoyé par l'orchestrateur : rapide 8 000, standard 16 000, expert 16 000 (réflexion incluse) ;
    # meta.truncated / warning_fr → note « Réponse tronquée » dans la réponse ; texte vide → repli local
claude: AnthropicProvider
# providers/pricing.py
PRICES = {model: {"input": $/MTok, "output": ..., "cache_read": ..., "cache_write": ...}}
def cost_usd(model, input_tokens, output_tokens, cache_read=0, cache_write=0) -> float
def prices_for(model) -> dict   # tarif de base ; modèle inconnu = carte la plus chère (known_model False)

# security/policy.py
ACTIONS = {action_id: {"risk": 'low'|'medium'|'high', "label": str}}
#   system.read, web.fetch, files.scan, files.quarantine, process.terminate, cloud.send, memory.write
def requires_permission(action: str, *, mode: str, sensitivity='normal') -> bool
# security/redaction.py
def redact(text: str) -> tuple[str, list[dict]]   # findings: [{type, count}] ; e-mail, téléphone (+ / 00), IBAN
                                                  # (toute casse), carte, IP, chemin utilisateur, clés API
                                                  # (sk-, sk_live_, hf_, glpat-, NOM_API_KEY=…), jetons, mots de
                                                  # passe / codes en langage courant (« mon mot de passe Wi-Fi est … »)
def sensitivity_of(text: str) -> str              # 'normal' | 'sensitive' (aussi dès qu'un secret est évoqué)
# security/untrusted.py
def wrap_external(source: str, text: str, *, max_chars=6000) -> str
def injection_signals(text: str) -> list[str]
# security/consent.py
def create_request(*, task_id, step_id, action, title, preview: dict, risk=None, ttl_minutes=30) -> dict  # émet permission.requested
def decide(permission_id: int, decision: str, reason='') -> dict | None   # 'grant'|'deny' ; émet permission.granted/denied
def get(permission_id) -> dict | None
def pending() -> list[dict]
def expire_stale() -> int   # pending échues → 'expired' + permission.denied {reason:'expired'}
# Un accord est lié à son étape (step_id) et ne sert qu'une fois : une étape interrompue après accord
# redemande l'accord. Les étapes réseau d'un plan rédigé par le modèle demandent toujours l'accord.
# L'arrêt de processus est limité aux cibles de l'aperçu (pid + nom + heure de démarrage) et la
# quarantaine aux fichiers de l'aperçu inchangés (taille + date).
# security/budget.py
def daily_budget_usd() -> float          # settings 'cloud.daily_budget_usd', défaut 1.0
def spent_today_usd() -> float
def remaining_usd() -> float
def record_usage(result: ProviderResult, task_id: int | None) -> None
# l'orchestrateur réserve l'estimation sous verrou avant chaque appel Claude (pas de dépassement concurrent)
# security/auth.py
SESSION_TOKEN: str                        # secrets.token_urlsafe(32) au démarrage
def origin_allowed(origin: str | None) -> bool
async def require_token(request) -> None  # FastAPI dependency : en-tête X-Aurora-Token ou ?token= ; 401 sinon
# AURORA_ALLOWED_ORIGINS : origines exactes ; '*' est ignoré (CORS et /api/bootstrap partagent la même liste).
# Limite connue (décision du propriétaire en attente) : le jeton protège contre les pages web, pas contre
# un programme local qui imite l'en-tête Origin (127.0.0.1 est partagé par les sessions Windows).
# Toutes les routes exigent le jeton (dependencies=LOCKED), sauf GET /api/bootstrap qui le délivre
# aux origines autorisées (54 routes sur 55, vérifié le 28/10/2026 ; les 6 routes des pages P2 aussi, testé
# le 29/10/2026 dans tests/test_artefacts.py).
```

## 7. Outils (propriétaire B4)

```python
# tools/base.py
@dataclass
class Source: title: str; url: str = ''; kind: str = ''; retrieved_at: str = ''
@dataclass
class ToolResult:
    ok: bool; data: dict; summary: str            # summary : phrase FR factuelle construite depuis data
    provenance: str                               # 'psutil' | 'open-meteo' | 'wikipedia' | 'usgs' | 'rss:<flux>' | 'scipy' | 'calc' | 'filesystem' | 'clock'
    sources: list[Source] = field(default_factory=list)
    duration_ms: float = 0.0; error: str | None = None
    def to_dict(self) -> dict
@dataclass
class ToolSpec:
    name: str; description: str; action: str      # id d'action de security.policy
    params: type[BaseModel]; run: Callable          # sync ou async, reçoit l'instance params
    network: bool = False; deterministic: bool = True
REGISTRY: dict[str, ToolSpec]
async def run_tool(name: str, params: dict) -> ToolResult   # valide, exécute (thread si sync), chronomètre, ne lève jamais
```
Outils : `system.snapshot`, `system.history`, `system.processes`, `system.diagnose`,
`process.terminate` (permission ; refuse processus système/critiques/AURORA/non-utilisateur ;
paramètre `approved` = cibles de l'aperçu, les autres sont refusées « absent de l'aperçu validé »),
`calc.evaluate`, `calc.convert`, `time.now`, `weather.current`, `weather.cities`, `web.search`,
`news.latest`, `quakes.recent`, `sim.orbit`, `sim.solar_system`, `files.scan` (lecture seule,
racines autorisées = dossiers utilisateur Documents/Téléchargements/Bureau + `settings.allowed_roots`),
`files.quarantine` (permission ; déplace vers `~/.aurora/quarantine/<run_id>/` + manifeste,
réversible ; paramètre `expected` = fichiers de l'aperçu), `files.restore`.
Studio (tools/studio.py, 29/10/2026, réseau, action `web.fetch`, sans paramètre, réglages `studio.settings`) :
`studio.github` (API publique, 60 requêtes par heure sans compte), `studio.downloads` (fichiers publiés des
versions GitHub ; le système d'exploitation est déduit du nom du fichier, sinon « Autres »), `studio.discord`
(comptes approchés d'une invitation), `studio.youtube` (flux Atom public, 15 dernières vidéos, XML sûr comme
news.py), `studio.site` (Netlify Analytics, jeton dans le coffre ; `data.not_connected` tant qu'il manque le jeton
ou l'option Analytics). Aucun de ces outils n'est proposé à l'IA (listes fermées de planner/agent).
`youtube.comment` (tools/youtube_comments.py, 09/11/2026, réseau, action `youtube.comment`, risque moyen ;
intention `youtube.comment`, emplacements `{text, video:'latest'|'other', remove}` lus sur le texte brut) :
un commentaire public sous la **dernière** vidéo de la chaîne reliée par la connexion Google d'AURORA.
Paramètres `{text?, request?, video_id?, video_title?, dry_run, origin, permission_id?, approved_fingerprint?}`.
`dry_run` : la carte `{channel_id, channel_title, video_id, video_title, video_url, video_published, text,
drafted_by:'user'|'local_model', model, origin, auto, same_channel, does, risk, risk_label, undo, fingerprint}` ;
la vidéo vient du flux public relu à l'instant (sans quota), le texte est celui donné (après « : », entre
guillemets ou après « qui dit ») ou rédigé par l'IA locale (Ollama, jamais le cloud ; titre et demande sont des
données). Étape réelle (`force_permission`, dérivée `comment_from_dry_run`) : publie seulement si l'empreinte
(chaîne, vidéo, texte) est celle de la carte accordée (`pc_control.card_matches`), puis relit le commentaire sur
YouTube (`commentThreads.insert` 50 unités, `commentThreads.list` 1 unité, relu après 0, 3, 5 puis 8 s tant que
YouTube ne le montre pas encore : `READ_BACK_WAITS_S`) ; `data` ajoute `{thread_id, url,
verified, verify_note, published_at}` et le dernier commentaire publié est gardé (réglage `youtube.last_comment`,
jamais un secret). Erreur 401 ou accès expiré : « reconnectez-vous » ; 4xx : « Rien n'a été publié » ; 5xx ou
réseau : « Je ne sais pas si le commentaire est parti ». Refusé en mode Privé (sans aucun appel), sans chaîne
reliée, pour une autre vidéo et pour le retrait (pas encore faits : la réponse le dit, aucune étape ne part).
Instance d'essai (`trial_instance`) : rien n'est publié. Absent des listes de l'agent.
`files.scan` : chaque fichier porte `size_bytes` (taille exacte) en plus de `size_mb` ; un chemin
`C:/…` ou `C:\…` et le nom d'un dossier ajouté sont reconnus, un dossier cité inconnu donne une erreur
honnête (jamais Téléchargements par défaut). `weather.current` : paramètre `days` (prévision
quotidienne sur 7 jours, `data.daily`). `time.now` : paramètre `place`. Les sources mises en cache
(météo, séismes, RSS) gardent l'heure réelle de récupération dans `retrieved_at`.
`system.snapshot` doit rester compatible avec le starter : clés `cpu_percent, ram_percent,
disk_percent, ram_total_gb` + `ram_used_gb, disk_total_gb, disk_used_gb, net_down_kbps,
net_up_kbps, cpu_count, cpu_freq_mhz, uptime_s, battery_percent|null, gpu|null, temperatures|null`.
Paramètre `metric` : `all, cpu, ram, disk, network, gpu, battery, uptime, temperature` (la température
indisponible est dite telle quelle, jamais remplacée par l'utilisation CPU).

## 8. API HTTP (propriétaire B5 — `main.py`)

Toutes les réponses JSON. Les routes marquées 🔒 exigent `X-Aurora-Token` (ou `?token=` pour SSE).
**Depuis le 28/10/2026 (CLAUDE.md : « un jeton secret exigé à chaque requête »), TOUTES les routes `/api/*`
l'exigent, lectures comprises ; seule `/api/bootstrap` (origine autorisée) le délivre. Pas de `/docs`,
`/redoc` ni `/openapi.json` publics : cette section est la description de l'API.** Le marqueur 🔒 ci-dessous
est conservé pour l'historique ; une route sans marqueur exige aussi le jeton.

| Méthode | Route | Corps / réponse |
|---|---|---|
| GET | `/api/bootstrap` | `{token, version, mode}` — uniquement si l'origine est exactement celle de l'interface locale ; 403 sinon (origine absente, `null`, étrangère, hôte non local) ; `Cache-Control: no-store` ; 404 si `AURORA_BOOTSTRAP=0`. Toutes les réponses portent `X-Frame-Options: DENY` et `frame-ancestors 'none'` |
| GET | `/api/health` | `{status, version, capabilities}` |
| GET | `/api/capabilities` | `Capabilities` |
| GET | `/api/system` | instantané (compat starter + champs étendus) |
| GET | `/api/system/history` | `{points:[{t, cpu, ram, net_down_kbps, net_up_kbps}]}` |
| GET | `/api/system/processes?limit=12&sort=cpu` | `{processes:[{pid, name, cpu_percent, memory_mb, username}]}` |
| POST 🔒 | `/api/ask` | `AskInput` → `RunResult` |
| POST 🔒 | `/api/chat` | compat starter `{prompt, mode:'local'|'cloud'}` → `{answer, mode, run}` |
| POST 🔒 | `/api/tools/diagnostic` | compat starter → passe par `/api/ask` intent `system.diagnose` |
| GET | `/api/runs?limit=50` | `RunSummary[]` |
| GET | `/api/runs/{id}` | `RunDetail` (tâche + étapes + événements) |
| POST 🔒 | `/api/runs/{id}/cancel` | `RunResult` |
| POST 🔒 | `/api/runs/{id}/resume` | `RunResult` |
| GET | `/api/permissions?status=pending` | `Permission[]` |
| POST 🔒 | `/api/permissions/{id}` | `{decision:'grant'|'deny', scope?:'once'|'conversation'}` → `RunResult` (reprise du run). `scope:'conversation'` : mode Avancé, `cloud.send`, données non sensibles seulement (sinon 422) ; les envois suivants de la conversation sont accordés sans attente, chacun avec sa demande et son aperçu exact enregistrés, jusqu'à révocation. Quitter le mode Avancé révoque tous ces accords. |
| GET 🔒 · DELETE 🔒 | `/api/conversations/{id}/cloud-consent` | `{active, granted_at, applies}` ; DELETE = bouton « Révoquer ». `RunResult` porte aussi `cloud_sends: {permission_id, model, text, scope}[]` (ce qui est parti vers Claude, tel quel) et `conversation_consent`. |
| GET | `/api/memories?category=&kind=` | `Memory[]` |
| POST 🔒 | `/api/memories` | `{title, detail, category, kind?, confidentiality?}` → `Memory` |
| PATCH 🔒 | `/api/memories/{id}` | champs partiels → `Memory` |
| DELETE 🔒 | `/api/memories/{id}` | 204 |
| GET | `/api/memories/search?q=&limit=8` | `MemoryHit[]` |
| GET | `/api/memories/graph?limit=` | `{nodes, links, totals, groups, tools, routines, limit}` — `limit` de 1 à 1000 (120 par défaut) souvenirs envoyés, avec au plus `max(10, limit // 12)` conversations et `max(30, limit // 4)` tâches. `totals` (`{immediate, persistent, semantic, actions}`) et `groups` (`[{level, category, count, shown}]` : souvenirs par catégorie, conversations sous `'conversations'`, tâches par intention ou `'autre'`) sont les **vrais comptes en base**, quel que soit le nombre de nœuds envoyés. `tools` : `[{name, title, uses, last_used_at}]` (registre, utilisations comptées sur les événements `tool.result`). `routines` : `[{id, name, steps, runs, last_run_at}]`. Vue cerveau en anneaux (29/10/2026) |
| POST 🔒 | `/api/memories/{id}/links` | `{target_id, relation}` |
| GET | `/api/tasks` · POST 🔒 `/api/tasks` · PATCH 🔒 `/api/tasks/{id}` | compat starter (tâches manuelles) |
| GET | `/api/events?limit=200&run_id=&since_id=` | `ActivityEvent[]` (ordre décroissant) |
| DELETE 🔒 | `/api/events` | 204 |
| GET 🔒 | `/api/stream?token=` | SSE `event: aurora` + `data: <ActivityEvent>` ; commentaire `: ping` toutes les 15 s |
| POST | `/api/attachments/extract?name=` | E4 (29/10/2026) : corps = octets bruts d'un PDF ou d'un Word (.docx), 15 Mo au plus (413 au-delà) → `{name, kind, text, pages, pages_read, chars, total_chars, truncated, notes}` (tools/documents.py). Rien n'est gardé ; l'interface joint ensuite le texte comme une pièce jointe ordinaire (donnée, jamais instruction). 422 en français : .doc, PDF protégé ou abîmé, Word invalide ou avec DOCTYPE. Un PDF sans aucun texte (scanné, ou fait d'images) : depuis le 23/11/2026, `text` vide et `images: {model, max_pages, max_chars}` quand ce PC peut lire ses pages comme des images (Windows et un modèle de vision installé), l'interface demandant alors les pages une à une (ligne suivante) ; sinon 422 qui dit pourquoi. Texte coupé à 60 000 caractères, et 300 pages au plus, avec une note. Pour l'IA : avec le modèle local, un long document est lu en entier, par parties de 12 000 caractères (12 parties au plus ; une étape d'IA locale de but `doc_part` par partie, puis la réponse écrite à partir des notes) ; avec Claude, il reste coupé à 12 000 caractères. Dans les deux cas la réponse le dit |
| POST | `/api/attachments/page?page=` | 23/11/2026 : corps = octets bruts d'un PDF (15 Mo au plus), `page` comptée à partir de 1 → `{page, pages, text, model, seconds, processor, truncated}`. La page est dessinée en image par le lecteur PDF de Windows (script fixe `tools/pdf_page.ps1`, côté le plus long 1 280 pixels, fichier temporaire retiré aussitôt), puis recopiée sur ce PC par le modèle de vision local (tools/documents.py `read_page`, tools/vision.py) : `text` est la lecture d'un modèle, pas le texte de la page, et l'interface le dit avec la pièce jointe. 20 à 40 s par page (mesuré le 23/11/2026) ; 30 pages au plus côté interface, qui peut s'arrêter entre deux pages. Rien n'est gardé ni journalisé. 422 en français : page absente, PDF que Windows n'ouvre pas, aucun modèle de vision, Ollama arrêté |
| GET | `/api/stt` | Voix D3 (9/11/2026) : `{available, running, started_by_aurora, engine, local, reason}` — la reconnaissance vocale sur ce PC par le service Whisper d'Athéna (providers/whisper_local.py, dossier `AURORA_WHISPER_DIR`) ; `reason` dit ce qui manque (service, environnement Python ou modèle : rien n'est téléchargé) |
| POST | `/api/stt/prepare` | démarre le service Whisper d'Athéna (caché, seulement s'il ne répond pas déjà) pendant que la personne parle → même forme que `GET /api/stt` ; 503 en français s'il manque ou ne démarre pas |
| POST | `/api/stt` | corps = octets bruts d'un enregistrement (webm/opus du navigateur, ou wav), 5 Mo au plus (413 au-delà, la limite d'Athéna) → `{text, seconds, audio_seconds, engine}`. Reconnu sur ce PC (Whisper « base », français, vocabulaire d'Athéna) ; ni le son ni le texte ne sont gardés ou journalisés. 503 en français : service absent, injoignable ou son illisible |
| GET | `/api/vision` | E2 (9/11/2026) : `{available, model, reason}` — le modèle de vision d'Ollama utilisé (ornith-1.5 d'abord, ordre d'Athéna ; `AURORA_VISION_MODEL` le force s'il est installé) ; `reason` dit ce qui manque (Ollama arrêté, aucun modèle de vision : rien n'est téléchargé) |
| POST | `/api/vision/describe` | corps = octets bruts d'une image PNG ou JPEG, 15 Mo au plus (413 au-delà) → `{text, model, seconds, processor, truncated}` : la description faite sur ce PC (`processor` : « carte graphique », ou « processeur » après un second essai). L'interface la joint comme un document (donnée, jamais consigne). 422 en français : format non lu, aucun modèle, Ollama arrêté. Rien n'est gardé ni journalisé |
| POST | `/api/vision/screen` | seulement par le bouton « Capture d'écran » : l'écran principal est capturé maintenant (Windows), ramené à 1 600 px de large au plus, puis décrit → même forme que `describe` + `{width, height, image}` (taille réelle de l'écran, et la capture en `data:image/png;base64,…` pour que la personne voie ce qui a été lu). 422 en français si la capture ou la lecture échoue. Pas de `GET` : un lien ou une image ne peut pas la déclencher |
| GET | `/api/conversations` · `/api/conversations/{id}/messages` | |
| DELETE | `/api/conversations/{id}` | icône corbeille de la liste, après confirmation dans l'interface (29/10/2026) : la conversation et ses messages, définitivement (pas de copie, à la demande de l'utilisateur) ; les traitements restent dans le journal ; son accord cloud est retiré. → `{id, messages}` ; 404 inconnue ; 409 si une de ses demandes est encore en cours |
| GET | `/api/weather?city=Paris` · `/api/weather/cities` | `ToolResult` |
| GET | `/api/news?topic=` · `/api/quakes` · `/api/search?q=` | `ToolResult` |
| GET | `/api/globe/avions?lat=&lon=` · `/api/globe/monde` · `/api/globe/satellites` | Globe hologramme, le ciel (08/11/2026, tools/sky.py) : `ToolResult` de `sky.planes` (40 avions au plus autour du point ; sans point, la ville des réglages), `sky.traffic` (trafic mondial, OpenSky, cache 5 min ; `ok: false` avec la marche à suivre tant que le réglage `opensky_enabled` est éteint, et dans ce cas `sky.planes` n'a pas de secours), `sky.satellites` (positions SGP4, CelesTrak puis API TLE en secours) |
| GET | `/api/globe/detail?bbox=la1,lo1,la2,lo2` · `/api/globe/nasa` | Images JPEG (NASA GIBS) : carte détaillée d'une région (40° de haut au plus, sinon 400 ; région servie dans `X-Bbox`) et image du monde de la veille (`X-Date-Image`). Erreur : JSON `{ok:false, detail, error, data}` ; mode Privé : 403 sans appel réseau ; source indisponible : 503 |
| GET | `/api/globe/meteo?lat=&lon=` · `/api/globe/vent` · `/api/globe/air` · `/api/globe/evenements` · `/api/globe/actualites` · `/api/globe/guide?titre=&projet=wikipedia\|wikivoyage` | Globe hologramme, la Terre : `ToolResult` de `weather.point`, `weather.grid`, `weather.air`, `events.active` (NASA EONET), `news.world` (27 flux, tous les articles) et `web.wiki_summary`. Toutes passent par `_tool` (mode Privé : aucun appel réseau). Détail des formes : reviews/globe-contrat.md |
| GET | `/api/lab/scenes` · `/api/lab/scenes/{id}` | Laboratoire 3D de l'accueil (08/11/2026, tools/lab.py, reviews/accueil-holographique.md § 2-3) : `ToolResult` de `lab.catalogue` (`data.scenes = [{id, titre, domaine, nature, resume, mots, origine}]`, origine `predefini` ou `genere`) et de `lab.scene` (`data` = la description `aurora.scene/1` validée ; scène inconnue ou invalide : `ok:false` et `error` en français). `{id}` suit `^[a-z][a-z0-9_]{0,39}$`, sinon 422. Sans réseau, passent par `_tool` |
| GET | `/api/studio?refresh=1` | Page Studio (29/10/2026) : `StudioData` = `{generated_at, mode, links, github, downloads, discord, youtube, site, kickstarter, history}`. Chaque source est `{state:'ok'|'error'|'private'|'offline'|'not_connected', ok, data, summary, error, provenance, fetched_at, sources}` ; `data` n'existe que si la mesure a réussi. Chaque source passe par `_tool` (mode Privé : aucun appel réseau, état `private`). Cache de 10 min ; `refresh=1` le vide au plus une fois par minute. `history` : un relevé par jour mesuré par AURORA (`{date, downloads, discord_members, youtube_views, github_stars}`, réglage `studio.history`, 400 jours) |
| GET · PUT | `/api/studio/settings` | `{github_users[], release_repos[], discord_invite, youtube_channel, site_url, netlify_site, kickstarter_url, netlify_token:bool}`. Le PUT accepte les liens (github.com/…, discord.gg/…, studio.youtube.com/channel/UC…) et les ramène à des identifiants ; 422 avec un `detail` en français |
| GET · PUT · DELETE | `/api/secrets/netlify` · `/api/secrets/youtube` | secrets du Studio (`{token}`), rangés dans le coffre Windows (cibles `AURORA/netlify_token` et `AURORA/youtube_api_key`, tools/studio.SECRETS), jamais renvoyés ; réponse `{configured, vault_available}`. La clé YouTube (« AIza… », 29/10/2026) part dans l'en-tête `X-Goog-Api-Key`, jamais dans l'adresse : elle ajoute à `youtube` les abonnés et les statistiques (`stats:'api'`), et donne la source `comments` (`studio.comments`, lecture des fils de commentaires reprise de `lireFils` de YouTube Manager ; rien n'est jamais posté) |
| GET · PUT · DELETE | `/api/secrets/youtube_client_id` · `/api/secrets/youtube_client_secret` | le client OAuth de la connexion Google d'AURORA (09/11/2026, tools/youtube_comments.py), même traitement que les secrets ci-dessus (cibles `AURORA/youtube_oauth_client_id` et `AURORA/youtube_oauth_client_secret`) ; tout autre nom : 422. L'accès durable donné par Google (`AURORA/youtube_oauth_refresh_token`) n'est écrit que par le retour de Google, jamais par une route |
| GET · POST · DELETE | `/api/youtube/etat` · `/api/youtube/connexion` | connexion Google d'AURORA, pour publier des commentaires (09/11/2026, tools/youtube_comments.py). `etat` : `{client, connected, channel_id, channel_title, connected_at, same_channel, redirect_uri, vault_available}`, jamais un secret ; `redirect_uri` = `<origine de l'interface>/youtube-retour` (en-tête `Origin`, origine autorisée seulement). POST `connexion` : `{url, redirect_uri, expires_in_s}`, l'adresse de la page Google (un seul droit, `youtube.force-ssl` : le seul qu'accepte la publication d'un commentaire, mais qui permet aussi de voir, modifier et supprimer définitivement vidéos, notes, commentaires et sous-titres, ce que Paramètres dit ; `prompt=select_account consent` ; état à usage unique de 10 min, lié à l'ID client ; PKCE) ; 409 sans client OAuth, hors de l'interface ou en mode Privé. DELETE : coffre et chaîne oubliés d'abord, puis retrait demandé à Google (`revoked`, et `note` quand Google ne l'a pas confirmé) ; en mode Privé, Google n'est pas appelé (`revoked:false`, `note` dit comment le retirer soi-même) |
| POST | `/api/youtube/retour` | `{state, code, error}` (rien d'autre : 422) : ce que Google a donné à la page `/youtube-retour` de l'interface. Google y renvoie le navigateur ; l'interface retire aussitôt le code de l'adresse (`history.replaceState`, lib/youtubeReturn.ts) et l'envoie ici **avec le jeton de session**, comme toute route. Échange le code contre l'accès durable (coffre Windows), lit la chaîne reliée (`channels.list mine=true`) et renvoie `etat` ; 400 et rien d'enregistré si l'état est inconnu, expiré ou déjà utilisé, si Google ou la personne refuse, sans la permission demandée ou sans accès durable (un accès que Google a donné mais qu'AURORA ne garde pas est retiré aussitôt ; si les réglages ne s'écrivent pas, le coffre reprend son contenu d'avant) ; 503 si le réseau ou Google ne répond pas ; 409 en mode Privé |
| GET | `/api/sim/solar?date=` | `ToolResult` |
| POST | `/api/sim/orbit` | `{body, altitude_km, eccentricity?, hours?}` → `ToolResult` |
| GET | `/api/settings` · PATCH 🔒 `/api/settings` | `Settings` (PATCH ignore `db_path` ; le cache des capacités est invalidé : le GET `/api/health` suivant reflète le nouveau mode) |
| GET | `/api/demarrage` · PUT 🔒 `/api/demarrage` | 16/11/2026 (E3, `tools/demarrage.py`) : AURORA démarre-t-elle avec Windows ? `{disponible, pourquoi, actif, fenetre, cette_copie, autre, fichier, lance}`, lu sur le disque à chaque appel, jamais un réglage mémorisé. PUT `{actif, fenetre?}` (rien d'autre : 422) pose ou retire le fichier de commande d'AURORA (`AURORA-demarrage.cmd`) dans le dossier « Démarrage » de la session Windows, et renvoie l'état relu ; il lance `start-aurora.ps1 -Hidden`, avec `-SansFenetre` quand `fenetre` est faux. 409 avec la raison, et rien de changé, hors de Windows, sans le dossier, ou si un fichier de ce nom existe qu'AURORA n'a pas écrit (`autre`). Seule la page Paramètres appelle ces routes : ni le chat, ni l'agent. `AURORA_STARTUP_DIR` remplace le dossier pour les essais |
| GET | `/api/usage` | `{today_usd, budget_usd, remaining_usd, by_model:[{model, cost_usd, calls, input_tokens, output_tokens, known_model}]}` |
| POST 🔒 | `/api/quality/bench` | lance le banc (sans LLM) → résultats ; GET `/api/quality/latest` |
| GET | `/api/artefacts` · GET `/api/artefacts/{id}` | P2 « Pages » (29/10/2026) : `ArtefactSummary[]` (la plus récemment changée d'abord) ; `ArtefactDetail` = résumé + `history` (versions, la plus récente d'abord) ; 404 si inconnue |
| GET | `/api/artefacts/{id}/html?version=` | HTML autonome de la version (la dernière par défaut), `text/html`, en-tête CSP `default-src 'none'` + `sandbox` ; 404 page ou version inconnue. L'interface l'affiche dans un `<iframe sandbox>` sans `allow-scripts` ni `allow-same-origin` |
| GET · DELETE · POST 🔒 | `/api/reminders` · `/api/reminders/{id}` · `/api/reminders/{id}/snooze` | Rappels, minuteurs et réveils (C3, 16/11/2026, tasks/reminders.py). GET : `{now, items}` — `now` est l'horloge locale du PC (« AAAA-MM-JJ HH:MM:SS »), `items` = `{id, text, kind: reminder·timer·alarm, due, created_at, state: waiting·due, fired_at}`, ceux qui ont sonné d'abord, puis par heure. Un rappel se **crée par une demande** (intention `reminder.set`, outil `reminder.set`) : le moment est lu dans la phrase par le code (`understand` : « à 18 h 30 », « dans 20 minutes », « demain à 9 h », « lundi », « le 25 novembre », « minuteur de 10 minutes », « réveille-moi à 7 h »), jamais par un modèle ; une heure sans jour vaut aujourd'hui, ou demain si elle est passée ; sans moment compris, rien n'est créé et la réponse le dit. La même intention liste (« quels sont mes rappels ? ») et annule (« annule le minuteur », « supprime tous mes rappels » ; un singulier ambigu n'annule rien). Aucune autorisation (action `reminder.set`, risque faible : des mots gardés dans AURORA) ; l'agent ne peut pas l'appeler. La boucle `_reminder_loop` (toutes les 2 s) marque `due` ce dont l'heure est venue et émet `reminder.due` (charge utile : le rappel) ; un rappel dont l'heure est passée service arrêté sonne au démarrage. DELETE : « Fait » ou « Supprimer » (204, 404). POST `…/snooze` `{minutes: 1..1440, défaut 10}` : le rappel repart en attente. Limites : 50 en attente, un an d'avance, pas de répétition |
| GET · POST · PUT · DELETE 🔒 | `/api/routines` · `/api/routines/{id}` | Routines (D1, tasks/routines.py) : `{id, name, triggers, steps, created_at, updated_at, runs, last_run_at, schedule, auto, next, events, proposal, no_proposal}`. Corps : `{name, triggers?, steps, schedule?, propose?}`. **Horaire (D2, 16/11/2026)** : `schedule` = `{time: 'HH:MM', days: [0..6]}` (0 = lundi) pour le régler, `null` pour le retirer ; absent, l'horaire en place est gardé. La réponse y ajoute `since` (minute locale du réglage : une heure déjà passée ce jour-là attend le lendemain). `next` : prochain lancement, « AAAA-MM-JJ HH:MM » en heure locale, calculé à chaque lecture. `auto` : compte rendu du dernier lancement par l'horaire, `{slot, at, late_min, run_id, conversation_id, steps, done, waiting, failed, over, interrupted?, error?}`. Le service lance une routine à son heure par la boucle `_routine_loop` (toutes les 30 s) : la demande de lancement (source `schedule`, refusée sur `/api/ask`), puis chaque étape (source `routine`), toutes avec l'option `unattended` — aucun accord donné d'avance ne vaut, chaque action sensible attend un clic. Événement `routine.scheduled`. **Sur un événement (D2, suite, 16/11/2026)** : à la place de l'heure, `schedule` = `{event: 'start', days: [0..6]}` ; la réponse donne `events`, la liste fermée des événements connus du service avec leurs mots (`{start: 'au démarrage d’AURORA'}`), et `next` vaut alors `null`. `start` : la routine part au premier contrôle après le démarrage du service (45 s), un jour coché, une fois par jour au plus (pas si elle est déjà partie seule ce jour-là), et seulement si le réglage date d'avant ce démarrage ; son `slot` est la minute du lancement. **Proposition « réussi 5 fois de suite » (D2, fin, 16/11/2026)** : la réponse donne `proposal` = `{count, time}` quand la routine, sans horaire, a été lancée à la main `count` fois de suite (5 au moins, 12 lus au plus) jusqu'au bout — lu dans le journal des demandes : chaque étape actuelle envoyée dans l'ordre et « done » ; un lancement par l'horaire n'est pas compté ; `time` est l'heure habituelle de ces lancements (médiane locale, à 5 min) — sinon `null`. Corps : `propose: false` = « Ne plus proposer » (gardé dans la routine, `no_proposal`), `true` le rétablit, absent ne change rien. Rien n'est jamais planifié sans un réglage de `schedule`. 422 en français (nom, étapes, heure, jours, événement inconnu) ; 404 routine inconnue |
| POST 🔒 | `/api/artefacts/from-run/{run_id}` · `/api/artefacts/bench` | `{artefact, version, created}` : page d'une demande terminée (analyse de données P3, diagnostic, recherche web, lancement de routine, sinon réponse) ou du dernier banc. Même demande ou même banc → même version (`created: false`) ; seul le bilan d'une routine gagne une version quand ses étapes avancent. 404 demande inconnue / aucun banc ; 409 demande pas terminée ; 422 rien à mettre en page |
| DELETE 🔒 | `/api/artefacts/{id}` | 204 ; supprime la page et ses versions (l'interface demande confirmation) |
| POST 🔒 | `/api/files/restore/{run_id}` | `ToolResult` |

```ts
AskInput = { text: string; conversation_id?: number | null; source?: 'text'|'voice'|'panel'|'api';
  attachments?: { name: string; text: string }[];
  options?: { engine?: 'auto'|'local'|'cloud'; cloud_tier?: 'rapide'|'standard'|'expert'; intent?: string;
              scene?: { id: string; piece?: string | null } } }
// options.scene (08/11/2026) : la scène 3D affichée sur l'accueil, envoyée seulement pendant qu'elle est affichée ;
// id et piece suivent ^[a-z][a-z0-9_]{0,39}$, aucun autre champ (sinon 422). La phrase est d'abord lue comme une
// commande sur cette scène (core/normalize.extract_scene_order) ; « cette pièce », « celle-ci », « ça » = piece. Sans
// commande reconnue, ou si une autre intention est sûre à 0,7 au moins sans qu'une pièce soit nommée, routage habituel.
RunResult = { task_id: number; conversation_id: number | null;
  status: 'done'|'error'|'waiting_permission'|'cancelled'|'running';
  answer: string; decision: Decision; engine: EngineChoice;
  sources: Source[]; steps: Step[]; permission: Permission | null;
  verification: { ok: boolean; checks: {name: string; ok: boolean; detail: string;
                  label?: string; level?: 'alerter'|'bloquer'}[]; blocked?: string[] } | null;
  // A4 : chaque contrôle est un « mod » (core/verify.py MODS) au niveau choisi (Settings.verify_mods).
  // blocked = contrôles « bloquer » en échec : answer montre alors les résumés des outils, et
  // withheld_answer?: string | null garde le texte de l'IA retenu (consultable, jamais présenté comme la réponse).
  memories_used: { id: number; title: string; score: number; method?: 'fts'|'hybrid' }[];   // [] si aucun modèle n'a lu le contexte
  ui: { navigate?: string; auto?: boolean; globe?: GlobeOrder; scene?: SceneOrder; lieux?: LieuxPhotos; widget?: 'system'|'weather'|'orbit'|'solar'|'quakes'|'processes'|'search'|'files'|'memory'|'data'; data?: any } | null;
  // lieux (08/11/2026, chat.general, web.search, agent.run ; jamais en mode Privé) : LieuxPhotos = { titre: string;
  //   pays: string | null; photos: {nom, img, la, lo, pays}[] (≤ 6) } — les lieux du patrimoine mondial photographiés
  //   du lieu que nomme la question, lus dans guide-sites.json (normalize.place_photos) : un site nommé en entier
  //   d'abord, puis les plus connus de son pays ; ou ceux d'un pays nommé par son nom (pas par sa capitale : Sucre,
  //   Roseau, Malé sont aussi des mots courants). img = fichier Wikimedia Commons, chargé par le navigateur ; l'interface
  //   n'affiche rien tant que le mode n'est pas connu ni en mode Privé. Un clic sur une photo ouvre Monde 3D sur le lieu.
  // scene (08/11/2026, laboratoire 3D, avec navigate: 'home') : SceneOrder = { scene: string; action: 'ouvrir'|'eclater'|
  //   'assembler'|'animer'|'arreter'|'recentrer'|'noyau'|'montrer'|'masquer'|'isoler'|'tout_montrer'|'expliquer'|
  //   'agrandir'|'interieur'; piece?: string; groupe?: string; suite?: {action, piece?, groupe?}[] }. La réponse est
  //   lue dans la description par une étape lab.scene (provenance réelle, gabarit compose.scene_view, sans modèle) ;
  //   une illustration est toujours dite « illustration pédagogique » (« … générée automatiquement » pour une scène
  //   d'origine « genere », phase 4). suite : « retire le boîtier et montre les bobines » donne une réponse par
  //   commande, dans l'ordre dit, et l'interface applique l'ordre puis chaque étape de suite (seules celles qui changent
  //   quelque chose y sont). ui est null quand rien ne change (pièce inconnue, aucune sélection, pièce fixe à animer,
  //   scène illisible) et pour un objet qui n'a pas pu être construit (sujet_3d). Objet construit à la demande :
  //   ui.scene = {scene: 'gen_…', action: 'ouvrir'}. Les outils lab.* ne donnent pas de badge « Mesuré ».
  // 08/11/2026, onglet du sujet : auto = page choisie par AURORA pour le sujet (compose.auto_page : météo, séismes,
  // événements, ciel, news.world -> world ; system.* -> computer ; studio.* -> studio), suivie par l'interface tant que
  // Settings.auto_open_tab est vrai. Sans auto : page demandée en mots (ui.navigate), toujours suivie.
  // globe = ce que montre le globe de Monde 3D (compose.globe_order, normalize.extract_globe_order) :
  // { couche?: 'meteo'|'evenements'|'actualites'|'avions'|'satellites'|'guide'|'routes'; focus?: {lat, lon, nom?}
  //   (coordonnées mesurées par un outil, jamais par un modèle) ; lieu?: string (cherché par le globe dans ses
  //   propres listes, sans réseau : pays et capitales, villes, puis lieux du patrimoine mondial, guide-sites.json,
  //   depuis le 08/11/2026) ; theme?; pays?; satellite?: number (NORAD) }.
  //   Le lieu se dit aussi « où est X sur le globe », « sur le globe où est X », « X sur le globe » (4 mots au
  //   plus) et, sans « globe », « où est X ? », « zoome sur X », « situe X », « va en X » pour un lieu que le globe
  //   connaît seulement : pays ou capitale (tools/news.country_index) ou lieu du patrimoine mondial (lu dans
  //   frontend/public/terre/guide-sites.json, le fichier du globe, sans copie) — normalize.globe_place_named.
  //   « … ou pas », « s'il te plaît » ne font pas partie du lieu. Pour un lieu du patrimoine mondial, la réponse
  //   nomme son pays, lu dans la même liste (normalize.globe_site).
  // P3 (widget 'data', compose.data_view) : data = { dataset: {name, source: 'jointe'|'fichier', path, rows, columns,
  // skipped}, highlights: {label, value, unit, digits, note}[] (≤ 6, venus des calculs), actions: {id, title, proof,
  // confidence: 'élevée'|'moyenne'|'faible', effect, kind: 'info'|'memory'|'routine', memory_text?, routine?: {name,
  // steps}}[] } — règles du code ; rien ne s'exécute sans le clic et la confirmation de l'utilisateur.
  duration_ms: number; cost_usd: number; error?: string;
  // P1 (intentions agent.run et data.analyze, sinon null) : les tours de l'agent (core/agent.py). Un tour = un appel du
  // modèle qui choisit UN outil autorisé (ceux de task.plan + memory.search) ou répond ; l'outil est une étape
  // ordinaire (accord, contrôles, badge d'origine). 6 tours au plus ; le plafond par requête vaut pour tous les
  // tours ; un refus de l'utilisateur bloque ce type d'action pour toute la demande ; une seule correction quand
  // un contrôle corrigible (mesures, capacites, sources, actions) échoue.
  agent?: { turns: number; max_turns: number; todo: {text: string; done: boolean}[];
            stopped: null|'max_turns'|'no_model'|'invalid'; corrected: boolean;
            history: {turn: number; action: string; title: string; reason: string;
                      status: 'answer'|'done'|'error'|'skipped'|'refused'|'waiting'; summary: string}[] } | null;
  // Cadre Contexte du Chat (29/10/2026) : la mémoire de travail du modèle utilisée par le dernier appel à l'IA
  // de la demande (null si aucune IA n'a répondu). Aussi dans la méta du message de réponse (meta.context).
  // input_tokens est mesuré (Ollama prompt_eval_count ; Claude input + cache) ; window vient du fournisseur
  // (Ollama /api/ps context_length du modèle chargé ; API des modèles d'Anthropic max_input_tokens), null si
  // inconnue ; parts : longueur de chaque partie, et sa part du total au prorata des caractères (pas une mesure).
  context?: { provider: 'ollama'|'anthropic'; model: string; input_tokens: number; window: number | null;
              window_source: 'ollama_ps'|'anthropic_models'|null; measured_at: string;
              parts: {key: 'system'|'tools'|'memory'|'conversation'|'results'|'question'; label: string;
                      chars: number; tokens: number}[] } | null }
Capabilities = { internet: boolean | null;   // null = non vérifié (mode privé : aucune sonde réseau)
  ollama: { up: boolean; models: string[]; chat_models: string[]; chat_model: string; embed_model: string; embed_ready: boolean };
  //        chat_models : modèles installés utilisables en conversation (sans les modèles d'embedding)
  cloud: { configured: boolean; provider: 'anthropic';
           tiers: Record<string, {model: string; effort: string;
                                  prices: {input: number; output: number; cache_read: number; cache_write: number}}> };
  //        prices : $ par million de jetons, tarif de base (providers/pricing.py)
  mode: 'prive'|'equilibre'|'avance';
  shell: boolean;   // 08/11/2026 : le terminal avec autorisation est activé (réglage shell_enabled), lu à chaque mesure ;
  //        « as-tu le terminal ? » est répondu par meta.capabilities avec cet état (capabilities.terminal_state)
  voice_hint: string; checked_at: string }
Settings = { mode; cloud_daily_budget_usd: number; default_city: string; allowed_roots: string[];
  // Plafonds par mode (CLAUDE.md) : cloud_daily_budget_usd et cloud_request_budget_usd = Équilibré (1 $, 0,25 $),
  // *_avance_usd = Avancé (3 $, 1 $). Requête : 0,01 à 100 $ ; jour : 0 à 1000 $.
  cloud_request_budget_usd: number; cloud_daily_budget_avance_usd: number; cloud_request_budget_avance_usd: number;
  // Aperçu cloud.send : + request_cap_usd, max_cost_usd, max_tokens. max_tokens est calculé pour que
  // prompt + sortie ne dépassent ni le plafond de la requête ni le reste du jour (le pire cas est réservé).
  local_model: string; local_fast_model: string; semantic_router: boolean; llm_router: boolean; theme: 'dark';
  web_contact: string;   // e-mail ou URL exigé par Wikimedia (vide = non renseigné)
  opensky_enabled: boolean;   // 24/11/2026 : OpenSky Network (trafic mondial, secours des avions), faux par défaut ;
                              // la personne l'allume elle-même dans Paramètres après avoir lu ses conditions
  verify_mods: Record<'provenance'|'mesures'|'capacites'|'sources'|'consentement'|'actions', 'alerter'|'bloquer'>;
  //        A4 : « alerter » par défaut ; un PATCH partiel garde les autres contrôles ; 422 si contrôle ou niveau inconnu
  auto_open_tab: boolean;   // 08/11/2026 : AURORA ouvre seule l'onglet du sujet (ui.auto) ; vrai par défaut
  youtube_comment_auto: boolean;   // 09/11/2026 : « Publier sans redemander » (Paramètres › Studio) ; faux par défaut.
  //        Vrai : la carte youtube.comment est créée, gardée au journal et accordée aussitôt (raison « réglage ») ;
  //        jamais en mode Privé, jamais pour l'agent (l'outil n'est pas dans ses listes)
  db_path: string }      // lecture seule : chemin absolu réel de la base (config.db_path())
// local_model / local_fast_model : un modèle d'embedding est refusé (422).
// Decision (RunResult.decision) peut porter unhandled_intents: string[] ; decision_json.cloud_declined
// (texte FR) dit pourquoi Claude, demandé explicitement, n'est pas utilisé.
```
Erreurs : `HTTPException(status, detail="message français")`. Jamais de trace, jamais de secret.

## 9. Frontend (Vite + React 19 + TS)

`src/lib/` (`api.ts`, `types.ts`, `stream.ts`, `store.tsx`, `format.ts`) et `src/components/ui/`
sont **partagés** : on les importe, on ne les réécrit pas (on peut y AJOUTER une fonction si besoin,
sans modifier les signatures existantes). Pages dans `src/pages/`, composants dans `src/components/`.

Thème « AURORA » (images de référence) : fond spatial bleu nuit `#030914`, panneaux verre
`rgba(8,24,48,.72)` bordure `#1b4677` lueur cyan, accent `#22c8ff`, bleu `#2f7bff`,
violet `#8a5cff`, vert `#2ee6a8`, ambre `#ffb547`, rouge `#ff6b7a`, texte `#dcebff`,
atténué `#7f9cc0`. Jetons CSS dans `src/styles/tokens.css`. Animations : respecter
`prefers-reduced-motion`. Tout le texte de l'interface est en français.

Règles d'interface :
- Toute donnée affichée vient de l'API ; si l'API échoue, afficher l'état « indisponible », jamais
  une valeur inventée. Pas de données factices (les maquettes montraient des chiffres d'exemple).
- Les animations de progression sont pilotées par les événements SSE (`useEventStream`).
- Les boutons d'action passent par `api.ask(..., {options:{intent}})`.
- Le cloud affiche toujours : modèle, coût, ce qui a été envoyé (aperçu du consentement).
