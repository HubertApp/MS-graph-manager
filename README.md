# MS Graph Manager

Microservice de calcul d'itinéraire du système **Hubert**. Il construit un graphe
routier pondéré autour d'une demande (OpenStreetMap via OSMnx, mis en cache par
région, repli OSRM), y fusionne au besoin la couche transport en commun (GTFS
servi par `MS-aom-agregator`), le fait résoudre par le routeur A* C++
(`MS-itinerary-creator`) en gRPC, puis expose le résultat — tracé, instructions
pas-à-pas en français, GeoJSON pour Mapbox — via une API GraphQL fédérée.

| | |
|---|---|
| Stack | Python 3.12 · FastAPI · Strawberry GraphQL (Federation 2.3) · FastStream (RabbitMQ) |
| Endpoint | `POST /graphql` — GraphiQL sur la même URL en `GET` · `GET /health` |
| Port | `3011` en conteneur · `8000` avec `uvicorn` en local |
| Dépendance dure | Routeur A* C++ en gRPC (`CPP_ROUTER_GRPC_TARGET`) |

Le service ne calcule aucun plus court chemin lui-même. Il ne persiste qu'un
cache : les régions OSM préparées (`OSMNX_CACHE_DIR`).

## Architecture

```
  Apollo Router ──▶ MS-graph-manager ──┬──▶ cache régional OSM   mémoire + disque (Overpass une fois par zone)
                    FastAPI/Strawberry │──▶ MS-aom-agregator     couche GTFS (GraphQL direct, pas via la gateway)
                                       │──▶ MS-itinerary-creator résolution A* (gRPC)
                                       │──▶ OSRM                 graphe de repli
                                       │──▶ trafic / prédictif   pondération (HTTP)
                                       └──▶ tracking             notification (HTTP, en tâche de fond)
  RabbitMQ  gtfs.events ──(gtfs.network.ingested)──▶ recharge la couche GTFS du réseau réingéré
```

| Dépendance | Obligatoire | Si indisponible |
|---|---|---|
| Routeur A* C++ (gRPC) | **oui** pour `getItineraireFromTo` | la query renvoie `null` |
| Overpass (via OSMnx) | seulement pour une zone pas encore en cache | bascule sur le repli OSRM ; pas de nouvel essai pendant `OSMNX_FAILURE_COOLDOWN_S` |
| MS-aom-agregator | non (profils `transit`/`multimodal`) | itinéraire à pied |
| RabbitMQ | non | reconnexion en tâche de fond ; la couche GTFS expire seule au changement d'heure |
| OSRM | **oui** pour `computeRoute` et le repli | `null` si OSMnx a aussi échoué |
| Trafic / prédictif | non | facteur neutre `1.0` |
| Tracking | non | notification abandonnée |
| Collecteur OTLP | non | export en échec silencieux |

**Flux de `getItineraireFromTo`** : facteurs trafic (en parallèle) → sous-graphe
de la demande découpé dans une région OSM en cache (thread dédié) → fusion de la
couche GTFS si profil `transit`/`multimodal` → `Solve` gRPC → reconstitution du
tracé (géométrie OSM réelle des rues), des instructions et des segments →
notification de suivi en tâche de fond. Il n'y a **pas de repli sur l'échec du
routeur C++** : la query renvoie `null`.

**Fédération** : deux renommages évitent une collision de composition avec
`service-aom-agregator`, qui expose déjà un `Route` désignant une ligne GTFS —
le type `Route` est publié sous le nom `ComputedRoute`, et `Query.route` sous le
nom `Query.computeRoute`.

## Performance : cache régional du graphe OSM

Avant, chaque demande appelait `ox.graph_from_bbox` : téléchargement Overpass,
construction et simplification du graphe (plusieurs secondes, davantage quand
Overpass est saturé), **de façon synchrone dans le resolver async** — toutes les
requêtes en cours étaient gelées pendant ce temps. S'y ajoutait la conversion de
chaque nœud et arête en objet Strawberry (≈ 0,4 s pour 10 000 nœuds, 1,1 s pour
22 000).

Désormais ([osmnx_graph.py](app/services/osmnx_graph.py)) :

1. une **région** (bbox arrondie au centième de degré + type de réseau) est
   téléchargée **une seule fois**, préparée en tableaux numpy, gardée en mémoire
   (LRU, `OSMNX_MEMORY_REGIONS`) et sur disque (`OSMNX_CACHE_DIR`, volume en
   compose) : un redémarrage ne retélécharge rien ;
2. `OSMNX_PRELOAD_BBOXES` précharge des régions au démarrage, en tâche de fond
   (Metz par défaut en compose) ;
3. par requête, le sous-graphe est découpé dans la région, pondéré de façon
   vectorisée, accroché à la plus grande composante fortement connexe (KD-tree),
   et la requête gRPC est construite directement, sans objets intermédiaires.

Mesuré hors Overpass et hors routeur, sur un même graphe : **415 ms → 33 ms**
(10 000 nœuds) et **1 112 ms → 77 ms** (22 500 nœuds). Le téléchargement
Overpass ne survient plus qu'une fois par zone.

La simplification OSMnx garde les nœuds où **la rue change de nom**
(`edge_attrs_differ=["name"]`) : sans eux, « Avenue Foch » puis « Rue du Sud »
fusionneraient en une seule arête et l'instruction serait perdue.

> Le routeur C++ acceptait au plus 4 Mo par requête (limite gRPC par défaut) :
> au-delà de ~20 000 nœuds, la recherche échouait en `RESOURCE_EXHAUSTED`. La
> limite est relevée à 64 Mo côté serveur (`ASTAR_MAX_MESSAGE_MB`), comme
> l'était déjà `CPP_ROUTER_MAX_MESSAGE_MB` côté client.

## Pondération

Chaque arête porte `length` (mètres, distance physique) et `weight` (secondes,
coût optimisé par le routeur). La distinction est contractuelle avec le C++.

```
weight = (length / vitesse) × pente × friction_tuile × trafic × prédictif
```

Vitesse par profil : `walking` 1.4 m/s, `cycling` 4.2, `driving` 13.9,
`transit`/`multimodal` 1.4 (la voirie se parcourt à pied, ce sont les arêtes GTFS
qui vont vite) ; profil inconnu ⇒ `driving`. En voiture, le tag `maxspeed`
remplace la vitesse du profil ; à pied et à vélo il ne sert que de plafond (un
piéton ne marche pas à 50 km/h parce que la rue est limitée à 50). `maxspeed` est
lu de façon tolérante (`"30;50"`, `"20 mph"`, `"FR:urban"` ⇒ défaut).

Les trois derniers facteurs valent `1.0` par défaut et sont bornés à `0.1`
minimum. Le `tileId` d'une friction est `f"{round(lat,2)}:{round(lon,2)}"`,
soit des tuiles d'environ 1,1 km, appliquées d'après le nœud source de l'arête.

## Couche transport en commun (GTFS)

Pour les profils `transit` et `multimodal`, la couche transit de chaque réseau
qui recouvre la zone est chargée depuis `MS-aom-agregator` et fusionnée sur la
voirie piétonne ([layer_merge.py](app/services/layer_merge.py)) : chaque arrêt est
relié au nœud de voirie le plus proche (≤ `TRANSIT_ACCESS_RADIUS_M`) par deux
arêtes à pied, `access` et `egress`.

- **Découverte** : la query `transitCoverage` de l'agrégateur liste les réseaux
  ingérés et l'emprise de leurs arrêts ; seuls ceux qui recouvrent la demande
  sont chargés. `TRANSIT_NETWORK_IDS` sert de liste blanche facultative.
- **Cache** : une couche par (réseau, date, heure UTC), `TRANSIT_CACHE_MAX_ENTRIES`
  au plus. L'heure courante est préchargée au démarrage.
- **Invalidation** : à chaque ingestion réussie, l'agrégateur publie
  `gtfs.network.ingested` ; le service vide la couche du réseau et la recharge.

## API GraphQL

### `getItineraireFromTo` — itinéraire complet

```graphql
query($request: RouteRequestDTO!, $frictions: [FrictionUpdateDTO!]) {
  getItineraireFromTo(request: $request, frictionUpdates: $frictions) {
    distanceM
    durationS
    geometry { type coordinates }
    traffic { realtimeFactor predictiveFactor source }
    steps {
      instruction distanceM durationS
      maneuver modifier streetName mode location bearingBefore bearingAfter
      geometryStart geometryEnd detail
      transitLineId lineName lineColor lineType headsign stopCount
    }
    segments { type osmIds transitLineId expectedArrival }
    geojson
    graphSnapshot { nodes { id lat lon } edges { edgeId sourceId targetId weight length } }
  }
}
```

```json
{
  "request": {
    "startPoint": { "lat": 49.1193, "lon": 6.1757 },
    "endPoint":   { "lat": 49.1096, "lon": 6.1825 },
    "departureTime": "2026-03-18T08:30:00Z",
    "routingProfile": "walking"
  },
  "frictions": [
    { "tileId": "49.12:6.18", "frictionCoefficient": 1.15, "sourceEvent": "road_work" }
  ]
}
```

`frictionUpdates` est facultatif, `routingProfile` vaut `driving` par défaut
(`walking`, `cycling`, `driving`, `transit`, `multimodal`).

**Étapes (`steps`)** — une par manœuvre, pas une par arête OSM
([instructions.py](app/services/instructions.py)) :

| `maneuver` | Exemple d'`instruction` |
|---|---|
| `depart` | Partez vers le nord-est sur Rue Serpenoise |
| `turn` (+ `modifier`) | Tournez à gauche sur Rue Fabert · Tournez légèrement à droite… · Faites demi-tour |
| `new name` | Continuez tout droit sur Avenue Foch |
| `roundabout` | Au rond-point, prenez la 2e sortie sur Rue du Nord (à pied : décrit comme une rue) |
| `walk to stop` | Rejoignez l'arrêt République |
| `board` | Prenez le tram A direction Hôpital (`detail` : arrêts, attente estimée) |
| `alight` | Descendez à l'arrêt Gare |
| `arrive` | Vous êtes arrivé à destination |

`modifier` : `straight`, `slight left|right`, `left|right`, `sharp left|right`,
`uturn` (vocabulaire de l'API Directions de Mapbox). `location` est le point de
la manœuvre `[lon, lat]` ; `geometryStart`/`geometryEnd` bornent le tronçon de
l'étape dans `geometry.coordinates`. La somme des durées et distances des étapes
égale celle de l'itinéraire. Les voies sans nom sont décrites par leur nature
(« le chemin piéton », « la piste cyclable »…).

**GeoJSON (`geojson`)** — `FeatureCollection` prête pour une source Mapbox,
coordonnées `[lon, lat]`, avec une `bbox`. `properties.kind` vaut `route` (tracé
complet), `step-line` (tronçon d'une étape, avec `mode` et `line_color`), `step`
(point de manœuvre, mêmes propriétés que l'étape), `start` ou `end` ; `step_index`
aligne `step`/`step-line` sur `steps`.

> `graphSnapshot` contient **tout** le graphe construit, pas seulement le chemin
> retenu — plusieurs milliers de nœuds. Il n'est construit que si le client le
> demande.

### `computeRoute` — appel OSRM direct

Sans pondération ni routeur C++. `null` si OSRM est injoignable.

```graphql
query {
  computeRoute(fromLat: 49.1193, fromLon: 6.1757, toLat: 49.1096, toLon: 6.1825) {
    distanceM durationS geometry { coordinates } steps { name distance duration }
  }
}
```

### `infoTrafic` — facteurs seuls

```graphql
query {
  infoTrafic(fromLat: 49.1193, fromLon: 6.1757, toLat: 49.1096, toLon: 6.1825) {
    realtimeFactor predictiveFactor source
  }
}
```

### Erreurs

Le service ne lève pas d'erreur GraphQL sur une panne de dépendance : il
journalise et renvoie `null`. Les trois causes se distinguent dans les logs —
`impossible de construire un graphe pour cette demande` (OSMnx et OSRM ont
échoué), `le routeur C++ n'a pas renvoye de chemin`, `le chemin renvoye par le
C++ n'est pas reconstituable`. Chaque itinéraire réussi est journalisé avec le
temps passé dans le graphe, le routeur et le rendu.

### `GET /health`

État du service : régions OSM en mémoire, préchargement en cours, connexion
RabbitMQ, couches GTFS en cache.

## Contrats

**gRPC (sortant)** — `AStarService.Solve(nodes, edges, start_id, goal_id) → {found, path,
total_cost, nodes_explored}`, contrat dans [proto/astar.proto](proto/astar.proto).
Canal non chiffré, rouvert à chaque appel, limite de message relevée à 64 Mo des
deux côtés.

**GraphQL vers MS-aom-agregator (sortant)** — `transitCoverage { networkId
stopCount north south east west }` et `transitGraph(networkId, departureTime)`,
adressés directement au subgraph (`AOM_GRAPHQL_URL`).

**RabbitMQ (entrant)** — exchange topic `gtfs.events` (durable), clé
`gtfs.network.ingested`, message `{"network_id", "ingestion_id", "ingested_at"}`.
Chaque processus déclare sa **propre** file (`graph-manager.gtfs.network.ingested.<hôte>.<pid>`,
supprimée à la déconnexion) : chaque réplique a son cache et doit recevoir sa
copie. La déclaration de l'exchange doit rester identique à celle de
l'agrégateur (`app/core/broker.py`), sinon `PRECONDITION_FAILED`.

**Trafic et prédictif (sortant)** — même contrat pour les deux :

```
POST <url>   { "from": {"lat":…, "lon":…}, "to": {"lat":…, "lon":…} }
→ 200        { "factor": 1.25 }          borné à [0.1, 3.0], défaut 1.0
```

**Tracking (sortant)** — `POST <url>` en *fire-and-forget* (tâche de fond) avec
les points, le profil, la distance, la durée et les segments. Réponse non lue,
erreurs ignorées.

## Configuration

Lues au démarrage via `python-dotenv` ; un `.env` à la racine est chargé.

| Variable | Défaut | Rôle |
|---|---|---|
| `OSRM_BASE_URL` | `http://localhost:5000` | base OSRM |
| `CPP_ROUTER_GRPC_TARGET` | `envoy:50051` | routeur A* C++, `host:port` |
| `CPP_ROUTER_TIMEOUT_S` | `10.0` | timeout de `Solve` |
| `CPP_ROUTER_MAX_MESSAGE_MB` | `64` | taille max des messages gRPC |
| `OSMNX_GRAPH_MARGIN_M` | `1500` | marge autour de la bbox du trajet |
| `OSMNX_CACHE_DIR` | `./cache/graphs` | régions OSM préparées (vide ⇒ mémoire seule) |
| `OSMNX_REGION_PADDING_M` | `1000` | marge ajoutée à une région téléchargée à la demande |
| `OSMNX_MEMORY_REGIONS` | `6` | régions gardées en mémoire (LRU) |
| `OSMNX_PRELOAD_BBOXES` | *(vide)* | `ouest,sud,est,nord;…` à précharger au démarrage |
| `OSMNX_PRELOAD_NETWORK_TYPES` | `walk,drive` | réseaux préchargés (`walk`, `drive`, `bike`) |
| `OSMNX_REQUESTS_TIMEOUT_S` | `90` | timeout HTTP d'une requête Overpass |
| `OSMNX_FAILURE_COOLDOWN_S` | `60` | pas de nouvel essai Overpass sur une zone en échec |
| `AOM_GRAPHQL_URL` | *(vide)* | subgraph de l'agrégateur ; vide ⇒ pas de couche GTFS |
| `TRANSIT_NETWORK_IDS` | *(vide)* | liste blanche de réseaux ; vide ⇒ découverte automatique |
| `TRANSIT_ACCESS_RADIUS_M` | `400` | distance max arrêt ↔ voirie |
| `TRANSIT_FETCH_TIMEOUT_S` | `30.0` | timeout des requêtes vers l'agrégateur |
| `TRANSIT_DISCOVERY_TTL_S` | `600` | durée de vie de la liste des réseaux |
| `TRANSIT_CACHE_MAX_ENTRIES` | `48` | couches (réseau × heure) gardées en mémoire |
| `RABBITMQ_URL` | *(vide)* | vide ⇒ pas d'invalidation sur `gtfs.network.ingested` |
| `TRAFFIC_INFO_URL` | *(vide)* | vide ⇒ facteur `1.0` |
| `PREDICTIVE_INFO_URL` | *(vide)* | vide ⇒ facteur `1.0` |
| `TRACKING_SERVICE_URL` | *(vide)* | vide ⇒ pas de notification |
| `PORT` | `3011` | port uvicorn dans le conteneur |
| `LOG_LEVEL` | `INFO` | niveau du logger racine |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` | collecteur OTLP |
| `OSRM_PBF_NAME` | `map` | *(compose)* nom du jeu `.osrm` monté |

Le compose renseigne déjà l'agrégateur, RabbitMQ, le volume de cache et le
préchargement de Metz. Le défaut `envoy:50051` vise le réseau Docker : **en
local, surchargez `CPP_ROUTER_GRPC_TARGET`**, sinon `getItineraireFromTo`
renverra toujours `null`.

## Démarrage

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # requirements.txt seul = exécution
uvicorn app.main:app --reload           # http://localhost:8000/graphql
```

Le premier appel dans une zone inconnue télécharge sa région (une fois) ; les
suivants sont servis depuis le cache. Pour un démarrage à chaud, précharger la
zone utile :

```bash
OSMNX_PRELOAD_BBOXES="6.10,49.06,6.27,49.16" uvicorn app.main:app
curl -s localhost:8000/health    # road_regions se remplit à la fin du préchargement
```

### OSRM

Les données doivent être extraites une fois, par exemple depuis un extrait
[Geofabrik](https://download.geofabrik.de/europe/france/lorraine.html) :

```bash
mkdir -p osrm-data && cd osrm-data   # y déposer le .osm.pbf
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-extract   -p /opt/car.lua /data/lorraine-latest.osm.pbf
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-partition /data/lorraine-latest.osrm
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-customize /data/lorraine-latest.osrm
docker run -t -p 5000:5000 -v "${PWD}:/data" osrm/osrm-backend osrm-routed --algorithm mld /data/lorraine-latest.osrm
```

Le jeu n'est extrait qu'avec `car.lua` et `osrm-routed` ignore le profil dans
l'URL — d'où le `driving` codé en dur dans
[osrm_client.py](app/services/osrm_client.py#L16).

### Docker

```bash
docker build -t ms-graph-manager . && docker run -p 3011:3011 --env-file .env ms-graph-manager
```

`docker compose` démarre `graph-manager` (3011) et `osrm` (5000), mais exige deux
réseaux externes préexistants et un jeu `.osrm` déjà extrait dans `./osrm-data` :

```bash
docker network create hubert-network
docker network create astar-net
OSRM_PBF_NAME=lorraine-latest docker compose up --build
```

Les régions OSM préparées vivent dans le volume `graph-manager-cache` ; pour
forcer un nouveau téléchargement : `docker volume rm ms-graph-manager_graph-manager-cache`.

## Développement

```bash
make validate   # install + lint (ruff) + tests (couverture ≥ 90 %)
pytest          # tests unitaires et bout en bout, aucune dépendance externe
pytest -m integration   # 1 test bout en bout, réseau requis
```

Les tests n'appellent jamais Overpass : un petit quartier OSM fictif
([tests/fixtures/mini_city.osm](tests/fixtures/mini_city.osm) — rond-point, sens
unique, rue courbe, chemin piéton) sert de graphe, et un vrai serveur gRPC en
mémoire joue le routeur ([tests/conftest.py](tests/conftest.py)).

Les `requirements*.txt` sont **générés** : on modifie les `.in`, puis `make lock`
recompile dans l'image du service. Les trois portes de `make validate` sont
bloquantes, et la CI les rejoue — sur branche de travail, puis sur `main` /
`develop` avec publication de l'image Docker (`:latest` et `:<sha>`). La qualité
de code est suivie par l'Automatic Analysis de SonarQube Cloud, branchée sur le
dépôt via l'App GitHub — rien à lancer depuis la CI.

Pour régénérer les stubs gRPC :

```bash
python -m grpc_tools.protoc -I proto --python_out=app/proto --grpc_python_out=app/proto proto/astar.proto
```

> `protoc` écrit `import astar_pb2`, ce qui casse l'import depuis le package.
> Rétablir la forme relative dans `astar_pb2_grpc.py` : `from . import astar_pb2 as astar__pb2`.

L'observabilité est initialisée dans [otel_setup.py](app/otel_setup.py), importé
**avant** FastAPI pour que l'auto-instrumentation s'accroche : traces, métriques
et logs partent en OTLP/gRPC.

## Structure

```
app/
├── main.py        point d'entrée FastAPI (+ /health, tâches de fond)
├── config.py      variables d'environnement
├── otel_setup.py  OpenTelemetry — importé avant tout le reste
├── schema.py      schéma fédéré Strawberry
├── models/        DTOs d'entrée et de sortie
├── resolvers/     les 3 queries et l'orchestration
└── services/
    ├── osmnx_graph.py           cache régional OSM, sous-graphe, requête gRPC
    ├── routing_graph.py         représentation interne du graphe et du chemin
    ├── instructions.py          instructions pas-à-pas en français
    ├── graph_engine.py          rendu du chemin, segments, repli OSRM
    ├── geojson.py               FeatureCollection pour Mapbox
    ├── transit_graph_client.py  couche GTFS (agrégateur) + caches
    ├── transit_events.py        consommateur RabbitMQ gtfs.network.ingested
    ├── layer_merge.py           fusion GTFS sur la voirie
    ├── lifecycle.py             préchargements et consommateur au démarrage
    └── cpp_router_client.py, osrm_client.py, traffic_client.py, itinerary_tracking.py
proto/astar.proto  contrat gRPC avec le routeur C++
tests/             unitaires + bout en bout (quartier OSM fictif, gRPC en mémoire)
```

## Auteur

Valentin Mignon
