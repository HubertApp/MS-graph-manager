# Ajout de tests — ms-graph-manager

## Contexte

Le projet ne dispose actuellement que de `tests/test_graph_engine.py` (fonctions
pures de `graph_engine.py`) et `tests/test_imports.py` (smoke test d'import).
Aucun test ne couvre les modules qui font des appels externes (osmnx/Overpass,
gRPC vers le routeur C++, OSRM, services de trafic, service de tracking) ni les
resolvers GraphQL qui les orchestrent.

Plusieurs bugs réels ont été trouvés cette session uniquement en testant
manuellement en live (signature `ox.graph_from_bbox` obsolète, dépendance
`scikit-learn` manquante, erreur de connexion OSRM non gérée, contrat cassé
entre le resolver et `cpp_router_client`, `graph_id` non unique). Aucun n'aurait
été détecté par un test unitaire avec mocks — l'objectif de ce travail est de
combler ce trou.

## Objectif

Ajouter une couverture de tests unitaires (mocks) sur tous les modules de
service et les resolvers, plus un test d'intégration bout-en-bout qui
automatise la vérification manuelle faite cette session (vraie requête GraphQL
contre l'Overpass API réelle).

## Périmètre

### Deux couches, séparées par un marker pytest

- **Unitaire** (par défaut) : tous les appels réseau/gRPC mockés
  (`respx` pour httpx, `unittest.mock` pour le stub gRPC). Rapide,
  déterministe, ne dépend d'aucun service externe. C'est ce qui tourne en CI.
- **Intégration** (`@pytest.mark.integration`, exclu du run par défaut) : un
  seul test qui monte l'app FastAPI en mémoire (`httpx.AsyncClient` +
  `ASGITransport`, sans lancer uvicorn) et exécute une vraie query
  `getItineraireFromTo` contre l'Overpass API réelle. Marqué pour ne pas
  dépendre d'un service tiers à chaque run CI.

### Fichiers de test à ajouter

| Fichier | Couvre | Cas à tester |
|---|---|---|
| `tests/test_osrm_client.py` | `osrm_client.fetch_route` | Succès (200 + routes) ; statut non-200 → `None` ; `routes` vide → `None` ; erreur de connexion → `None` (régression du bug corrigé cette session) |
| `tests/test_traffic_client.py` | `traffic_client.py` | URL vide → `1.0` sans appel réseau ; succès → facteur clampé [0.1, 3.0] ; statut non-200 → `1.0` ; réponse malformée → `1.0` |
| `tests/test_itinerary_tracking.py` | `itinerary_tracking.notify_tracking_service` | URL vide → pas d'appel ; succès → payload envoyé avec la bonne forme ; erreur HTTP → avalée sans lever (best-effort) |
| `tests/test_cpp_router_client.py` | `cpp_router_client.py` | Snapshot vide → `None` ; id start/end inconnu → `None` ; succès (StoreGraph + Solve found=True) → edge_ids mappés ; StoreGraph échoue → `None` ; Solve found=False → `None` ; `grpc.RpcError` → `None` ; régression : le path retourné par `Solve` est traité comme des strings, jamais casté en `int` |
| `tests/test_osmnx_graph.py` | `osmnx_graph.py` (fonctions pures + orchestration avec osmnx/networkx mockés) | `_tile_id`, `_compute_bbox`, `_infer_speed_mps`, `_resolve_edge_length_m` en isolation ; **régression directe du bug bbox** : assert que `ox.graph_from_bbox` est appelé avec un tuple positionnel `(west, south, east, north)` et non les kwargs `north=/south=/east=/west=` ; graphe vide → `None` ; exception sur `nearest_nodes` → `None` ; pas de chemin trouvé → `None` |
| `tests/test_resolvers_route.py` | `route`, `infoTrafic`, `getItineraireFromTo`, `_select_edge_ids` | Happy path des 3 queries ; fallback OSRM quand osmnx renvoie falsy ; `None` global quand osmnx ET OSRM échouent ; `_select_edge_ids` retombe sur `default_edge_ids` quand le routeur C++ échoue ou renvoie des edge_ids inconnus ; régression : `graph_id` différent à chaque appel (uuid4) |
| `tests/test_integration_live_graph.py` | Bout-en-bout réel | Marqué `integration`, skip par défaut ; requête `getItineraireFromTo` réelle, vérifie que la réponse contient des nœuds avec un `id` préfixé `osm:` |

## Outillage

- **`requirements-dev.txt`** (nouveau) : `-r requirements.txt` puis `pytest`,
  `pytest-cov`, `pytest-asyncio`, `respx`. `requirements.txt` reste
  runtime-only — l'image Docker ne change pas.
- **`pytest.ini`** (nouveau) :
  - `asyncio_mode = auto` (pas de `@pytest.mark.asyncio` répété sur chaque test)
  - déclaration du marker `integration` (évite les `PytestUnknownMarkWarning`)
  - `addopts = -m "not integration"` (le run par défaut exclut l'intégration ;
    `pytest -m integration` la sélectionne explicitement)
- **`Makefile`** : la cible `install` installe `requirements-dev.txt` s'il
  existe (sinon `requirements.txt`, comportement actuel conservé en fallback).
  La cible `test` actuelle fait `pip install pytest pytest-cov` en dur — retiré
  puisque couvert par `requirements-dev.txt`.
- **`README.md`** : section courte expliquant `pytest` (unitaire, défaut) vs
  `pytest -m integration` (bout-en-bout, manuel).

## Hors périmètre

- Pas de refonte des modules testés — uniquement l'ajout de tests sur le code
  existant (déjà corrigé cette session).
- Pas de tests de charge, pas de tests d'acceptation, pas de CI supplémentaire
  au-delà de ce que `make validate` fait déjà (le workflow CI n'est pas modifié
  dans ce lot, seul `Makefile` l'est).
- Pas de mock du serveur gRPC réel de `MS-itinerary-creator` — uniquement le
  stub client généré, mocké en mémoire.

## Critères de succès

- `pytest` (sans argument) passe en local sans accès réseau, sans OSRM, sans
  routeur C++ lancé, et n'exécute aucun test marqué `integration`.
- `pytest -m integration` passe en local avec accès réseau et retrouve un
  graphe OSM réel.
- Chaque bug corrigé cette session a une régression correspondante qui aurait
  échoué avant le fix (vérifié en relisant le diff du fix associé).
