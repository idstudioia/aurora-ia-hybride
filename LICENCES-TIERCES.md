# Bibliothèques et services utilisés par AURORA

AURORA — Copyright (C) 2026 ID Studio IA — est distribuée sous la licence GNU Affero General Public License,
version 3 (fichier `LICENSE`). Elle s'appuie sur les bibliothèques et les services ci-dessous, qui gardent chacun leur propre licence.

Les licences des bibliothèques sont celles que chaque paquet déclare lui-même. Elles ont été relevées le 24/11/2026
dans les versions installées sur le PC de développement ; une autre installation peut recevoir des versions plus
récentes. Aucune de ces bibliothèques n'est copiée dans ce dépôt : `pip` et `npm` les téléchargent à l'installation.

## Service (Python)

| Bibliothèque | Version relevée | Licence déclarée |
|---|---|---|
| fastapi | 0.162.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| psutil | 7.2.2 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| anthropic | 2.3.0 | Apache-2.0 |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| scipy | 1.17.1 | BSD à 3 clauses (le paquet donne le texte entier, sans sigle) |
| pypdf | 6.21.4 | BSD-3-Clause |
| sgp4 | 2.27 | MIT |
| piper-tts (facultative, `backend/requirements-voix.txt`) | 1.8.0 | GPL-3.0-or-later |

## Interface

| Bibliothèque | Version relevée | Licence déclarée |
|---|---|---|
| react, react-dom | 19.3.1 | MIT |
| three | 0.163.0 | MIT |
| @xyflow/react | 12.12.0 | MIT |
| d3-geo | 3.1.1 | ISC |
| topojson-client | 3.1.0 | ISC |
| world-atlas | 2.0.2 | ISC |
| lucide-react | 1.62.0 | ISC |
| gsap | 3.15.0 | licence « Standard no charge » de son éditeur — voir plus bas |

Outils de construction, qui ne font pas partie de l'interface construite : vite 7.3.7 (MIT),
@vitejs/plugin-react 5.2.0 (MIT), typescript 5.9.3 (Apache-2.0), et les paquets `@types/…` (MIT).

## Un point à connaître : GSAP

GSAP n'est pas une bibliothèque libre. Sa [licence](https://gsap.com/community/standard-license/), lue le
24/11/2026, permet de l'utiliser dans tout site ou toute application, mais elle reste la propriété de Webflow.
AURORA s'en sert pour une seule animation (`frontend/src/components/flow/FlowReplay.tsx`).

Pour qu'AURORA puisse être redistribuée avec elle (une interface déjà construite, un dossier `dist`, contient GSAP),
l'auteur d'AURORA a donné le 24/11/2026 une permission additionnelle, comme le prévoit l'article 7 de l'AGPL : voir
[`EXCEPTION-GSAP.md`](EXCEPTION-GSAP.md). Les conditions de GSAP elle-même restent celles de son éditeur. La remplacer
un jour par une bibliothèque libre rendrait cette permission inutile.

## Code repris

- **Request Flow Replay**, Copyright (c) 2026 Cliff Pyles, licence MIT : `third_party/request-flow-replay/LICENSE.txt`.

## Modèles et maquettes 3D livrés

- **Modèle 3D « AI Robot X »** (`docs/exemple-robot-x/modeles/ai-robot-x.glb`, 20,9 Mo, 4 animations) : créé par
  ID Studio IA avec Meshy, livré comme illustration du projet d'exemple. Ce n'est pas un plan.
- **Maquettes « Bras robotique à 4 axes », « Fusée à deux étages » et « X-SWING »** (`backend/aurora/data/scenes/`) :
  des descriptions en formes simples, écrites pour AURORA ; les deux dernières ont été écrites par Claude, dans
  Claude Code, le X-SWING d'après une image d'ID Studio IA.
- **Modèles du globe et formes du noyau** (`frontend/public/terre/`, `frontend/public/noyau/`) : faits par
  ID Studio IA avec Meshy ; voir les fichiers `SOURCE.txt` de ces dossiers.

## Voix

- **Piper** (`piper-tts`, GPL-3.0-or-later) : facultative, installée à part.
- **Voix `fr_FR-siwis-medium`** (jeu de données SIWIS, CC-BY 4.0) : elle n'est pas dans ce dépôt, la personne la
  télécharge elle-même (README, « Crédits »).

## Modèles d'IA

Aucun modèle n'est dans ce dépôt. Les modèles locaux sont téléchargés par Ollama, et chacun a sa propre licence :
elles n'ont pas été relues ici. Le cloud, facultatif, passe par la clé Claude de la personne.

## Services de données

AURORA interroge ces services depuis le PC de la personne. Seules les deux premières lignes ont été relues, les 23 et
24/11/2026.

| Service | Sert à | Conditions |
|---|---|---|
| [Open-Meteo](https://open-meteo.com/en/terms) | météo, qualité de l'air | service gratuit réservé à l'usage non commercial ; données sous CC-BY 4.0, avec mention de la source |
| [OpenSky Network](https://opensky-network.org/about/terms-of-use) | avions : trafic mondial, secours autour d'un point | accès réservé à la recherche et à l'enseignement sans but lucratif ; accord écrit demandé pour tout autre usage. **Éteint par défaut depuis le 24/11/2026** : AURORA ne l'interroge que si la personne l'allume elle-même dans Paramètres, après avoir lu ces conditions |
| adsb.lol | avions autour d'un point | données sous ODbL, d'après la mention du service ; conditions non relues |
| CelesTrak, TLE API, JPL, NASA GIBS | satellites, orbites, images de la Terre | conditions non relues |
| USGS, NOAA (weather.gov, tsunami.gov), GDACS | séismes, alertes | conditions non relues |
| Flux de presse (titres et liens) | actualités | conditions non relues |
| Wikipédia, DuckDuckGo | recherche de faits | conditions non relues |

Les services du Studio (GitHub, YouTube, Discord, Netlify) ne sont interrogés qu'avec les comptes que la personne
règle elle-même.
