# AURORA — assistant IA hybride (local + Claude) : le guide complet

> Ce guide était la page d'accueil du projet jusqu'au 24/11/2026. La présentation courte est maintenant dans
> [`README.md`](../README.md) ; ici restent le détail des modules, l'architecture, l'installation à la main, les
> variables d'environnement, les essais et les crédits. Les chemins cités partent de la racine du projet.

AURORA est un assistant personnel qui tourne sur votre PC. Il répond d'abord avec des outils fiables (mesures, calculs, météo…), puis avec une IA locale (Ollama). Il ne passe à Claude (Anthropic) que si c'est utile et que vous l'acceptez.

Il est construit à partir du prototype **ASTRA starter hybride v0.1**, qui est conservé et relié au nouveau système : base SQLite étendue, routes de compatibilité, vue simple de la mémoire.

> Règle de base : **ce qui se mesure ne se génère pas.** La RAM, l'heure, un calcul ou la météo viennent d'un outil, jamais d'un modèle de langage.

---

## Ce qu'AURORA sait faire aujourd'hui

| Module | État | Détail |
|---|---|---|
| IA centrale (moteur de décision) | ✅ | Un seul moteur pour le texte, la voix et les boutons. Il passe d'abord par des règles, puis par Ollama, puis par Claude. Mesuré sur un banc de 239 phrases (28/10/2026) : 224/239 avec les règles seules, en moins d'une milliseconde. Vos signalements (« Signaler ») y sont rejoués. |
| Supervision du PC | ✅ | Processeur, RAM, disques, processus et diagnostic « mon PC est lent ». L'arrêt d'un processus demande votre accord. |
| Mémoire intelligente | ✅ | Quatre niveaux : immédiate, persistante, sémantique (FTS5 + vecteurs bge-m3) et actions. Le « cerveau » est animé à partir des vrais événements. |
| Recherche web | ✅ | SearXNG local d'abord (Docker, `127.0.0.1:8888`), puis Wikipédia et DuckDuckGo en secours. Les premières pages publiques trouvées sont lues par un lecteur sûr. Le **contact** demandé par Wikipédia (Paramètres) n'aide que la voie de secours. |
| Agent et tâches | ✅ | Plans, étapes, autorisations, annulation et reprise. Une animation « flux des tâches » rejoue les vrais traitements, sans jamais rien réexécuter. |
| Monde 3D | ✅ | Globe en hologramme : météo, vent et qualité de l'air (Open-Meteo), séismes et catastrophes (USGS, NOAA, GDACS), avions autour d'un point (adsb.lol), satellites, actualités par pays. Les données en direct sont éteintes tant que vous ne les allumez pas. |
| Laboratoire 3D | ✅ | Sur l'accueil : trois maquettes livrées (bras robotique à 4 axes, fusée à deux étages, X-SWING), une maquette construite sur demande par l'IA avec des formes simples, un modèle STL, GLB ou OBJ que vous donnez, ou une forme refaite à partir d'une photo. Des illustrations, jamais des simulations. |
| Laboratoire de simulations | ✅ | Orbites (intégration numérique SciPy) et système solaire (éléments képlériens approchés du JPL). |
| Routines et rappels | ✅ | Une suite de demandes lancée par son nom, à une heure ou au démarrage ; rappels, minuteurs et réveils, affichés et lus à voix haute. Pas encore de rappel qui se répète. |
| Documents | ✅ | Lit et résume un texte, un PDF ou un fichier Word joint, par l'IA locale ; un PDF fait d'images (scanné) est lu page par page par le modèle de vision local. |
| Assistant vocal | ✅ | Reconnaissance vocale du navigateur (Chrome ou Edge), envoyée au même moteur que le texte. Lecture des réponses par une **voix neuronale locale** (Piper, voix française « siwis ») générée sur le PC ; les voix du navigateur servent de secours. Micro au centre de la barre, entre deux ondes qui montrent le niveau réel (micro pendant l’écoute, voix d’AURORA pendant la lecture) ; sur l’accueil, des cercles bleus s’allument autour du globe pendant la conversation et suivent ce niveau. Les réponses sont lues automatiquement (réglable) ; un appui sur le micro coupe la voix ; le chat ne s’ouvre plus tout seul quand la réponse est lue : bouton « chat » dans la barre, sauf pour une demande d’autorisation, affichée d’office. |
| Vision locale (image, capture d'écran) | ✅ | Une image jointe, ou une capture de l'écran faite **sur votre clic** (bouton « Capture d'écran »), est décrite sur le PC par un modèle de vision d'Ollama (ornith-1.5 d'abord, réglages d'Athéna) ; la description devient un document joint, une donnée et jamais une consigne. AURORA ne regarde jamais l'écran d'elle-même. |
| Agir sur l'écran (souris, clavier) | ✅ | Un geste à la fois, montré puis fait après votre accord ; désactivé par défaut (Paramètres › Souris et clavier). Essayé sur un faux Windows dans les tests, pas encore en vrai. |
| Projets | ✅ | La page Projets tient le dossier d'un projet de conception : cahier des charges, fichiers par rubrique, vérifications avant fabrication, fiche technique. AURORA mesure la présence de chaque fichier et dit pourquoi un projet n'est pas prêt à fabriquer. Une installation neuve montre un seul projet, d'exemple (« Exemple — Robot X » : une fiche technique interactive et un modèle 3D) ; le bouton « Créer un projet » en commence un, vide, dans vos données. Ranger ses fichiers et écrire sa fiche se fait encore à la main, dans son dossier. |
| Démarrage avec Windows | ✅ | Paramètres › Démarrage : le service et l'interface démarrent sans fenêtre à l'ouverture de session, ou avec la fenêtre d'AURORA. AURORA pose pour cela un seul fichier dans le dossier « Démarrage » de Windows, et le retire quand vous désactivez l'option. Pas encore essayé sur une vraie ouverture de session. |
| Application de bureau (Tauri) | ❌ | Prévue plus tard : Rust n'est pas installé sur cette machine. Pour l'instant, AURORA s'ouvre dans le navigateur. |

## Les trois modes

| Mode | Comportement |
|---|---|
| **Privé** | Tout reste sur le PC. Pas de Claude, et chaque accès à Internet demande votre accord. |
| **Équilibré** (par défaut) | Outils et IA locale d'abord. Claude (Haiku, Sonnet) seulement si vous le demandez ou si c'est nécessaire, après un aperçu masqué et votre accord **à chaque envoi**. Plafonds : 1 $ par jour, 0,25 $ par requête. |
| **Avancé** | Claude, dont Opus 5.5 sur demande. L'aperçu reste toujours affiché, mais vous pouvez donner l'accord **pour toute la conversation** ; un bandeau visible permet de le révoquer à tout moment. Plafonds : 3 $ par jour, 1 $ par requête. |

Dans tous les modes, rien ne part vers le cloud sans aperçu du texte exact ni sans votre accord ; les données sensibles demandent toujours un accord séparé, à chaque envoi. Avant chaque envoi, AURORA masque les e-mails, les clés, les chemins et les numéros.

## Architecture

```
aurora/
├─ backend/                Service local Python (FastAPI, port 8765)
│  ├─ aurora/
│  │  ├─ main.py           Routes HTTP + flux temps réel (SSE)
│  │  ├─ core/             Moteur de décision : normalisation, règles, routeur, choix du moteur,
│  │  │                    orchestrateur, planificateur, vérification, réponses
│  │  ├─ memory/           Mémoire 4 niveaux : stockage, recherche, vecteurs, contexte
│  │  ├─ providers/        Ollama (local) et Claude (SDK anthropic), tarifs
│  │  ├─ security/         Politique, masquage, contenu non fiable, consentement, budget, jeton
│  │  ├─ tools/            Outils fiables : système, processus, fichiers, calcul, météo, séismes,
│  │  │                    actualités, web, orbites, heure, diagnostic
│  │  ├─ tasks/            Journal d'événements central
│  │  └─ db.py             SQLite (base ASTRA étendue, import unique et en lecture seule d'ASTRA)
│  └─ tests/               Tests unitaires et d'intégration, banc de routage
├─ frontend/               Interface React 19 + TypeScript (Vite, port 5173)
├─ docs/CONTRACT.md        Contrat entre modules (schéma, événements, routes)
├─ third_party/            Request Flow Replay (licence MIT)
└─ start-aurora.ps1        Démarrage en un clic
```

## Installation (Windows)

**Le plus simple :** double-cliquer sur `INSTALLER.bat`.
Une fenêtre s'ouvre (`fenetre-installation.ps1`) : elle vérifie le PC sans rien changer, montre ce qui sera
téléchargé, et n'installe qu'après le clic sur « INSTALLER ». Elle lance pour cela le script `installer-aurora.ps1`,
qui fait les étapes 1 à 4 : il crée l'environnement Python, installe les bibliothèques, prépare l'interface, propose
les modèles d'après la carte graphique, pose l'icône du Bureau et vérifie que le service démarre. Depuis le
23/11/2026, s'il manque Python (de 3.11 à 3.14, en 64 bits), Node.js ou Ollama, il prend lui-même le fichier
officiel de leur éditeur, à la version essayée, refuse un fichier dont l'empreinte n'est pas celle publiée, puis
l'installe : Python est déplié dans le dossier d'AURORA sans rien inscrire dans Windows ; Node.js et Ollama passent
par leur installateur. Sans la fenêtre, `INSTALLER.bat console` garde ce script dans une console, où
rien n'est téléchargé sans un « o » tapé au clavier. Pour seulement voir ce qui manque, sans rien changer :

```powershell
powershell -ExecutionPolicy Bypass -File .\installer-aurora.ps1 -Verifier
```

Répété le 22/11/2026 sur le PC d'ID Studio IA, dans un dossier neuf et avec de vrais téléchargements (sans les modèles) :
installation complète en 2 minutes, le service démarre, l'interface se construit, et les 1 277 essais passent avec
les bibliothèques installées ce jour-là.

Premier essai sur un autre PC le 23/11/2026 (GTX 1080 de 8 Go, 7,9 Go de mémoire vive) : l'installation s'est
d'abord arrêtée, parce que le seul Python de ce PC était le 3.15, trop récent pour la bibliothèque de la voix. Une
fois un Python 3.11 posé à la main, elle a fini en 2 min 7 s et AURORA a démarré. L'installateur a été corrigé le
jour même pour choisir un Python qui convient ou le prendre lui-même ; cette correction a été essayée sur le PC de
ID Studio IA avec un vrai téléchargement (99 secondes, les 1 299 essais passent), **pas encore sur un autre PC**.
L'installation de Node.js et d'Ollama par l'installateur n'a jamais tourné.

Quatre défauts relevés pendant cet essai ont été corrigés le même jour, et **pas encore revus sur l'autre PC** :
sur une carte de moins de 10 Go, AURORA démarrait sur un modèle absent (elle prend maintenant celui qui est
installé, tant que la personne n'en a choisi aucun) ; le modèle local n'avait qu'une fenêtre de 4 096 jetons (AURORA
en demande 8 192, mesuré sans perte de vitesse sur la GTX 1080 et sur la RX 6750 XT) ; la bibliothèque de la voix
arrêtait toute l'installation quand elle échouait (elle est à part, dans `backend/requirements-voix.txt`, et son
échec n'est plus qu'une remarque) ; la fenêtre d'AURORA restait ouverte après l'arrêt. Pour porter AURORA sur une clé :
`scripts/kit-installation/construire-kit.ps1`. Les étapes à la main restent décrites ci-dessous.

### 1. Prérequis

- Python, de 3.11 à 3.14, en 64 bits. Pas 3.15 : la bibliothèque de la voix (onnxruntime, demandée par piper-tts)
  n'existe pas encore pour lui, et AURORA n'a jamais été essayée avec ce Python
- Node.js 20 ou plus récent
- [Ollama](https://ollama.com), avec les modèles :

```bash
ollama pull qwen3.5:9b
```

```bash
ollama pull qwen3.5:4b
```

```bash
ollama pull bge-m3
```

### 2. Environnement Python

Windows limite les chemins à 260 caractères. L'environnement est donc créé dans `~/.aurora/venv`, et non dans le projet :

```bash
py -3.11 -m venv "$HOME/.aurora/venv"
```

(`-3.11`, ou toute version de 3.11 à 3.14 installée : sans numéro, `py` prend le Python le plus récent du PC.)

```bash
"$HOME/.aurora/venv/Scripts/python.exe" -m pip install -r backend/requirements.txt
```

La voix neuronale locale (Piper) est à part et facultative : sans elle, AURORA parle avec les voix du navigateur.

```bash
"$HOME/.aurora/venv/Scripts/python.exe" -m pip install -r backend/requirements-voix.txt
```

### 3. Claude (facultatif)

Créez une clé sur [console.anthropic.com](https://console.anthropic.com), puis enregistrez-la dans **Paramètres › Clé Claude**. AURORA la range dans le **coffre Windows** (Gestionnaire d'identification, cible `AURORA/anthropic_api_key`) et ne la réaffiche jamais. Elle n'est écrite ni dans un fichier, ni dans un journal, ni dans Git.

En secours, l'ancienne méthode reste acceptée, mais le coffre est prioritaire : la variable d'environnement `ANTHROPIC_API_KEY`.

Sans clé, AURORA fonctionne entièrement en local et l'indique clairement (« Cloud off »).

Paliers utilisés :

| Palier | Modèle | Effort | Quand |
|---|---|---|---|
| rapide | `claude-haiku-5-5` | low | routage, demandes simples |
| standard | `claude-sonnet-5-5` | medium | par défaut |
| expert | `claude-opus-5-5` | high | mode Avancé seulement, sur demande (« mode expert », « Claude Opus », choix dans le chat) |

### 4. Lancer

**Au quotidien : double-cliquez sur l'icône AURORA du Bureau**, ou sur `AURORA.bat`.

- Le service et l'interface démarrent sans fenêtre ; leurs journaux vont dans `~/.aurora/logs`.
- AURORA s'ouvre dans sa propre fenêtre, sans barre de navigateur : Edge, sinon Chrome, en mode application.
- Si AURORA tourne déjà, les serveurs sont réutilisés.
- Pour arrêter : `AURORA-arreter.bat`.
- Pour qu'elle démarre avec Windows : Paramètres › Démarrage. Les serveurs démarrent alors à l'ouverture de session, et l'icône du Bureau ouvre la fenêtre aussitôt.

Pour le développement, avec les fenêtres visibles :

```powershell
powershell -ExecutionPolicy Bypass -File .\start-aurora.ps1
```

Le script démarre le service (http://127.0.0.1:8765) et l'interface (http://127.0.0.1:5173), puis ouvre le navigateur. Avec `-Hidden`, il fait ce que fait `AURORA.bat` ; avec `-Stop`, il arrête AURORA.

Vous pouvez aussi lancer les deux à la main, dans deux terminaux. Le service, depuis `backend` :

```bash
"$HOME/.aurora/venv/Scripts/python.exe" -m uvicorn aurora.main:app --host 127.0.0.1 --port 8765
```

L'interface, depuis `frontend` :

```bash
npm install && npm run dev
```

## Vos données

| Quoi | Où |
|---|---|
| Base (mémoires, conversations, tâches, événements, réglages) | `~/.aurora/aurora.db` |
| Fichiers mis en quarantaine (restaurables depuis l'interface) | `~/.aurora/quarantine/<traitement>/` |
| Ancienne base ASTRA | `~/.astra/astra.db`, importée une seule fois et en lecture seule |
| Sauvegardes automatiques avant migration de la base | `~/.aurora/backups/` |
| Sauvegardes complètes (projet et historique Git, base, voix), chacune avec son `LISEZ-MOI.md` | `D:\Documents\sauvegardes-aurora\<date>_<étiquette>\` |

Le service n'écoute que sur `127.0.0.1`. Chaque appel exige un jeton de session, et les origines et l'hôte sont vérifiés. AURORA n'exécute jamais de commande shell.

## Variables d'environnement (facultatives)

| Variable | Rôle | Défaut |
|---|---|---|
| `ANTHROPIC_API_KEY` | Clé Claude, en secours (le coffre Windows est prioritaire) | — |
| `AURORA_HOME` | Dossier des données | `~/.aurora` |
| `AURORA_DB_PATH` | Base SQLite | `~/.aurora/aurora.db` |
| `AURORA_OLLAMA_URL` | Adresse d'Ollama | `http://127.0.0.1:11434` |
| `AURORA_LOCAL_MODEL` / `AURORA_LOCAL_FAST_MODEL` | Modèles locaux | `qwen3.5:9b` / `qwen3.5:4b` |
| `AURORA_EMBED_MODEL` | Modèle de vecteurs | `bge-m3` |
| `AURORA_OLLAMA_KEEP_ALIVE` | Durée de maintien des modèles en mémoire | celle d'Ollama |
| `AURORA_CLOUD_MODEL_RAPIDE` / `_STANDARD` / `_EXPERT` | Modèles Claude par palier | voir plus haut |
| `AURORA_SEARXNG_URL` | SearXNG local, première source de la recherche web (`off` le coupe ; Wikipédia et DuckDuckGo restent en secours) | `http://127.0.0.1:8888` |
| `AURORA_READ_PAGES` | Lecture des premières pages trouvées, pages HTTPS publiques uniquement (`off` la coupe) | activée |
| `AURORA_CONTACT` | Contact ajouté à l'identifiant envoyé à Wikipédia, en secours (sinon, réglage dans Paramètres) | — |
| `AURORA_ALLOWED_ORIGINS` | Origines autorisées pour l'interface (boucle locale uniquement : une origine publique est ignorée) | `http://127.0.0.1:5173,http://localhost:5173` |
| `AURORA_SESSION_TOKEN` | Jeton fourni au démarrage par un lanceur (future application de bureau), 32 caractères ou plus | jeton aléatoire |
| `AURORA_BOOTSTRAP` | `0` = route `/api/bootstrap` fermée (jeton fourni par le lanceur) | `1` |
| `AURORA_OFFLINE` | `1` = aucun appel réseau (tests) | `0` |
| `AURORA_STARTUP_DIR` | Dossier qui remplace le dossier « Démarrage » de Windows (essais : un essai n'écrit jamais dans le vrai) | le dossier « Démarrage » de la session |

## Tests et mesures

Depuis `backend` :

```bash
"$HOME/.aurora/venv/Scripts/python.exe" -m unittest discover -s tests -t .
```

Le banc de routage mesure la précision et la latence du moteur de décision :

```bash
"$HOME/.aurora/venv/Scripts/python.exe" tests/bench_routing.py
```

Il se lance aussi depuis Paramètres, dans l'interface. Le routeur sémantique et le routeur par LLM restent désactivés tant que le banc ne prouve pas qu'ils apportent un gain.

Relevé le 24/11/2026 : 1 309 essais du service, tous réussis (4 sautés, 1 échec attendu), et 84 essais de l'interface ; banc de routage, 317 phrases comprises sur 332 (95,5 %), 31 sortes de demandes, 0,31 ms en médiane.

Depuis `frontend` :

```bash
npx tsc -b --noEmit && npx vite build
```

## Limites connues

- AURORA tourne sous Windows seulement, et son interface est en français seulement.
- Sur une carte graphique de 12 Go, une réponse rédigée par `qwen3.5:9b` prend 4,5 s en moyenne, et son premier mot arrive après 2,3 s (banc de conversation du 22/11/2026) ; une réponse par outil prend 0,3 s. `qwen3.5:4b` est plus rapide (3,1 s) : vous pouvez le choisir dans Paramètres.
- Elle n'est installée que sur deux PC de son auteur : aucun retour d'autres utilisateurs.
- Jamais essayés en vrai : les gestes de souris et de clavier (essayés sur un faux Windows), et le démarrage avec Windows sur une vraie ouverture de session.
- Le trafic aérien mondial du globe est éteint au départ (voir « Licence »).
- Le nettoyage des Téléchargements ne propose un fichier que si sa date de modification **et** sa date de création dépassent le seuil choisi. C'est voulu, par prudence.

## Licence

AURORA est distribuée sous la licence **GNU Affero General Public License, version 3** (AGPL-3.0), choisie le
24/11/2026. Le texte officiel est dans le fichier [`LICENSE`](../LICENSE).

```
AURORA — assistant IA hybride
Copyright (C) 2026 ID Studio IA

Ce programme est un logiciel libre : vous pouvez le redistribuer et le modifier selon les termes de la
licence GNU Affero General Public License, version 3, publiée par la Free Software Foundation.

Ce programme est distribué dans l'espoir qu'il sera utile, mais SANS AUCUNE GARANTIE, sans même la garantie
implicite de qualité marchande ou d'adéquation à un usage particulier. Voir la licence pour plus de détails.

Une copie de la licence accompagne ce programme (fichier LICENSE). Sinon : <https://www.gnu.org/licenses/>.

Permission additionnelle selon l'article 7 de la licence, pour la bibliothèque GSAP : fichier EXCEPTION-GSAP.md.
```

En mots simples, et sans remplacer le texte de la licence :

- chacun peut utiliser AURORA, l'étudier, la modifier et la redistribuer, gratuitement ;
- celui qui la redistribue, modifiée ou non, ou qui en fait un service ouvert à d'autres par un réseau, doit donner
  le code source de sa version sous cette même licence ;
- AURORA est fournie sans aucune garantie.

Les bibliothèques et les services qu'AURORA utilise gardent chacun leur propre licence : voir
[`LICENCES-TIERCES.md`](../LICENCES-TIERCES.md). La bibliothèque d'animation GSAP n'est pas libre : une permission
additionnelle, prévue par l'article 7 de la licence, autorise à redistribuer AURORA avec elle
([`EXCEPTION-GSAP.md`](../EXCEPTION-GSAP.md)). Le service OpenSky Network (trafic aérien mondial du globe) est éteint
au départ : il réserve son accès à la recherche et à l'enseignement sans but lucratif, et chaque personne l'allume
elle-même dans Paramètres, après avoir lu ses conditions.

## Crédits

- **Request Flow Replay**, Copyright (c) 2026 Cliff Pyles, licence MIT (`third_party/request-flow-replay/LICENSE.txt`). Son principe de stations, rubans et points animés est adapté pour l'animation du flux des tâches, à partir des vrais événements d'AURORA.
- **ASTRA starter hybride v0.1** : le prototype d'origine d'AURORA, sous le premier nom du projet (même auteur) ; base du service et de l'interface.
- Données : Open-Meteo (météo), USGS (séismes), Wikipedia (recherche), flux RSS (actualités), éléments orbitaux approchés du JPL, Natural Earth via world-atlas (cartes).
- **Voix** : moteur [Piper](https://github.com/OHF-voice/piper1-gpl) (`piper-tts`, licence **GPL-3.0**, facultatif et installé à part) ; voix `fr_FR-siwis-medium` (jeu de données SIWIS, **CC-BY 4.0**), depuis [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices). Pour l'installer sur un autre PC : `pip install -r backend/requirements-voix.txt` (l'installateur le fait), puis placez `fr_FR-siwis-medium.onnx` et `fr_FR-siwis-medium.onnx.json` dans `~/.aurora/voices`.
