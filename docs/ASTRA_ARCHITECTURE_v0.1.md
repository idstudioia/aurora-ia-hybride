# ASTRA : architecture détaillée (cible v1)

```
[UI React/TypeScript + animations React Flow + globe Cesium (futur)]
                         |
          [Tauri 2 / commandes natives Rust]
                         |
             [Service local Python]
                  |        |         \
               Tasks    Memory       AI Router
                  |        |         /      \
               SQLite  SQLite     Ollama    Cloud opt-in
                  |
               Event log ----> Projection visualisée et rejouable
```

## Découpage recommandé

1. `ui-shell` : navigation, thèmes, accessibilité, fonctionnalités chargées à la demande.
2. `system-monitor` : métriques PC en lecture seule ; collecte raisonnable (5 s) ; aucun espionnage.
3. `task-engine` : tâches avec étapes d'état `pending -> running -> done/error`, journaux et résultats vérifiables ; à terme moteur de jobs avec annulation, reprise et limites de temps.
4. `memory-core` : mémoire explicite, SQLite, recherche FTS5, provenance, expiration et suppression ; embeddings seulement si la recherche classique ne suffit plus.
5. `event-bus` : événement append-only horodaté, producteur/consommateur, replay fidèle, idempotence ; la visualisation n'est jamais la source de vérité.
6. `ai-router` : choix local/cloud explicite par tâche, quotas, budget, redaction avant cloud, mesure du temps/consommation, fallback contrôlé.
7. `tools` : liste autorisée de fonctions (jamais un shell arbitraire) ; confirmation avant actions externes ou destructrices.
8. `voice` : transcription et synthèse activables, streaming, interruption par l'utilisateur.
9. `web-research` : navigation contrôlée, provenance, citations et validation des sources.
10. `planet-3d/simulations` : modules chargés à la demande : CesiumJS, calcul Python SciPy, limites mémoire/GPU.

## Structure actuelle

- `backend/app/main.py` : REST API locale, supervision, tâches, mémoire, chat.
- `backend/app/db.py` : tables SQLite `tasks`, `memories`, `events`.
- `frontend/src/App.tsx` : tableau de bord + chat + formulaire + replay.
- `frontend/src/components/MemoryGraph.tsx` : graphe React Flow.
- `frontend/src/components/TaskTimeline.tsx` : journal visuel.

## Contrat des événements

```
{
  "id": 42,
  "subject_type": "task",
  "subject_id": 8,
  "kind": "done",
  "label": "Analyser le PC",
  "created_at": "2026-10-28T09:30:00.000Z"
}
```

Le graphe est une **projection** recalculée depuis la base. Le replay parcourt les événements classés par date/identifiant ; il n'exécute jamais de nouveau la tâche. Dans ce prototype, une arête vers ASTRA signifie seulement « appartient à mon espace ASTRA », pas une relation de cause à effet entre souvenirs.

## Sécurité — avant publication

- Le serveur HTTP de développement s'écoute uniquement sur `127.0.0.1`. **Ce n'est pas une protection suffisante pour une publication** : les autres processus locaux peuvent appeler l'API.
- Pour la version distribuée, préférer des commandes Tauri/Rust ou utiliser un jeton aléatoire par démarrage, inaccessible aux sites tiers, une vérification des origines et du CSRF et un contrôle strict des permissions.
- Tauri peut lancer le service Python sous forme de `sidecar` empaqueté. Prévoir démarrage/arrêt gérés, chemins de données applicatifs, signatures et mises à jour.
- L'utilisateur valide la capture d'écran, le micro, les fichiers, le réseau, les installations et toute action irréversible ; journaliser les validations.
- Pour les appels Cloud : secret seulement côté backend ; ne pas transmettre automatiquement la mémoire, la supervision PC ou l'écran ; consentement granulé et explicite.
- N'ajouter des outils d'action à l'IA qu'après avoir implémenté l'autorisation, les paramètres typés, le sandboxing et la traçabilité.
- Sauvegardes locales chiffrées, suppression complète du journal, export et politique de rétention avant utilisation personnelle intensive.

## Plan de développement (jalons indicatifs)

- **M0 — Starter** : React, backend, mesures réelles, SQLite, journal, graphe/replay, chat local et cloud contrôlé (ce dossier).
- **M1 — Tauri** : package bureau, IPC sécurisé, lancement/fermeture Python sidecar, sauvegardes, tests système.
- **M2 — Mémoire utile** : FTS5, provenance des sources, correction et consolidation contrôlées, recherche des conversations.
- **M3 — Véritables tâches** : queue async, annulation, WebSocket/SSE pour le progrès, planning durable, outils avec permissions.
- **M4 — Multimodal/web** : voix, navigation autorisée, traçabilité des sources, capture d'écran sur demande uniquement.
- **M5 — Planète/sciences** : CesiumJS lazy-loaded, SciPy, exécutions dans travailleurs isolés.

## Critères de qualité

- UI réactive même quand l'IA ne répond pas.
- Une tâche terminée doit produire un résultat vérifiable.
- Toute animation de « progression » provient d'un événement effectif.
- Aucune donnée personnelle envoyée au cloud par défaut.
- Chargement des moteurs lourds uniquement quand leur module est ouvert.
- Mesures des temps de réponse et des consommations plutôt que promesses arbitraires.
