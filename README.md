# MS Graph Manager

Microservice de calcul d'itinéraire du système **Hubert**. Il construit un graphe
routier pondéré autour d'une demande (OpenStreetMap via OSMnx, repli OSRM), le
fait résoudre par le routeur A* C++ (`MS-itinerary-creator`) en gRPC, puis expose
le résultat via une API GraphQL fédérée.

| | |
|---|---|
| Stack | Python 3.12 · FastAPI · Strawberry GraphQL (Federation 2.3) |
| Endpoint | `POST /graphql` — GraphiQL sur la même URL en `GET` |
| Port | `3011` en conteneur · `8000` avec `uvicorn` en local |
| Dépendance dure | Routeur A* C++ en gRPC (`CPP_ROUTER_GRPC_TARGET`) |

Le service ne calcule aucun plus court chemin lui-même et ne persiste rien.

## Architecture

```
  Apollo Router ──▶ MS-graph-manager ──┬──▶ Overpass (via OSMnx)   graphe principal
                    FastAPI/Strawberry │──▶ OSRM                   graphe de repli
                                       │──▶ MS-itinerary-creator   résolution A* (gRPC)
                                       │──▶ trafic / prédictif     pondération (HTTP)
                                       └──▶ tracking               notification (HTTP)
```

| Dépendance | Obligatoire | Si indisponible |
|---|---|---|
| Routeur A* C++ (gRPC) | **oui** pour `getItineraireFromTo` | la query renvoie `null` |
| Overpass (via OSMnx) | non | bascule sur le repli OSRM |
| OSRM | **oui** pour `computeRoute` et le repli | `null` si OSMnx a aussi échoué |
| Trafic / prédictif | non | facteur neutre `1.0` |
| Tracking | non | notification abandonnée |
| Collecteur OTLP | non | export en échec silencieux |

**Flux de `getItineraireFromTo`** : facteurs trafic → construction du graphe
(OSMnx, sinon OSRM) → `Solve` gRPC → reconstitution géométrie/étapes/segments →
notification de suivi. Il n'y a **pas de repli sur l'échec du routeur C++** :
la query renvoie `null`.

**Fédération** : deux renommages évitent une collision de composition avec
`service-aom-agregator`, qui expose déjà un `Route` désignant une ligne GTFS —
le type `Route` est publié sous le nom `ComputedRoute`, et `Query.route` sous le
nom `Query.computeRoute`.

## Pondération

Chaque arête porte `length` (mètres, distance physique) et `weight` (secondes,
coût optimisé par le routeur). La distinction est contractuelle avec le C++.

```
weight = (length / vitesse) × pente × friction_tuile × trafic × prédictif
```

La vitesse vient du tag `maxspeed`, sinon du profil : `walking` 1.4 m/s,
`cycling` 4.2, `driving` 13.9, `transit` 8.3, `multimodal` 7.0 (profil inconnu ⇒
`driving`). Les trois derniers facteurs valent `1.0` par défaut et sont bornés à
`0.1` minimum. Le `tileId` d'une friction est `f"{round(lat,2)}:{round(lon,2)}"`,
soit des tuiles d'environ 1,1 km, appliquées d'après le nœud source de l'arête.

> Les profils `transit` / `multimodal` sont acceptés mais aucune donnée GTFS
> n'est chargée : la couche transport en commun est un stub.

## API GraphQL

### `getItineraireFromTo` — itinéraire complet

```graphql
query($request: RouteRequestDTO!, $frictions: [FrictionUpdateDTO!]) {
  getItineraireFromTo(request: $request, frictionUpdates: $frictions) {
    distanceM
    durationS
    geometry { type coordinates }
    traffic { realtimeFactor predictiveFactor source }
    steps { instruction distanceM durationS }
    segments { type osmIds transitLineId expectedArrival }
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
    "routingProfile": "driving"
  },
  "frictions": [
    { "tileId": "49.12:6.18", "frictionCoefficient": 1.15, "sourceEvent": "road_work" }
  ]
}
```

`frictionUpdates` est facultatif, `routingProfile` vaut `driving` par défaut.

> `graphSnapshot` contient **tout** le graphe construit, pas seulement le chemin
> retenu — plusieurs milliers de nœuds sur une bbox urbaine. Ne le demandez que
> si le client en a besoin.

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
C++ n'est pas reconstituable`.

## Contrats sortants

**gRPC** — `AStarService.Solve(nodes, edges, start_id, goal_id) → {found, path,
total_cost, nodes_explored}`, contrat dans [proto/astar.proto](proto/astar.proto).
Canal non chiffré, rouvert à chaque appel, limite de message relevée à 64 Mo.

**Trafic et prédictif** — même contrat pour les deux :

```
POST <url>   { "from": {"lat":…, "lon":…}, "to": {"lat":…, "lon":…} }
→ 200        { "factor": 1.25 }          borné à [0.1, 3.0], défaut 1.0
```

**Tracking** — `POST <url>` en *fire-and-forget* avec les points, le profil, la
distance, la durée et les segments. Réponse non lue, erreurs ignorées.

## Configuration

Lues au démarrage via `python-dotenv` ; un `.env` à la racine est chargé.

| Variable | Défaut | Rôle |
|---|---|---|
| `OSRM_BASE_URL` | `http://localhost:5000` | base OSRM |
| `CPP_ROUTER_GRPC_TARGET` | `envoy:50051` | routeur A* C++, `host:port` |
| `CPP_ROUTER_TIMEOUT_S` | `10.0` | timeout de `Solve` |
| `CPP_ROUTER_MAX_MESSAGE_MB` | `64` | taille max des messages gRPC |
| `OSMNX_GRAPH_MARGIN_M` | `1500` | marge autour de la bbox du trajet |
| `TRAFFIC_INFO_URL` | *(vide)* | vide ⇒ facteur `1.0` |
| `PREDICTIVE_INFO_URL` | *(vide)* | vide ⇒ facteur `1.0` |
| `TRACKING_SERVICE_URL` | *(vide)* | vide ⇒ pas de notification |
| `PORT` | `3011` | port uvicorn dans le conteneur |
| `LOG_LEVEL` | `INFO` | niveau du logger racine |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` | collecteur OTLP |
| `OSRM_PBF_NAME` | `map` | *(compose)* nom du jeu `.osrm` monté |

Le défaut `envoy:50051` vise le réseau Docker : **en local, surchargez
`CPP_ROUTER_GRPC_TARGET`**, sinon `getItineraireFromTo` renverra toujours `null`.

## Démarrage

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # requirements.txt seul = exécution
uvicorn app.main:app --reload           # http://localhost:8000/graphql
```

`scikit-learn` est une dépendance d'exécution : OSMnx s'en sert pour accrocher
les points de départ et d'arrivée au graphe. Sans elle, le service bascule
silencieusement sur le repli OSRM.

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

## Développement

```bash
make validate   # install + lint (ruff) + tests (couverture ≥ 90 %)
pytest          # 64 tests unitaires, aucune dépendance externe
pytest -m integration   # 1 test bout en bout, réseau requis
```

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
├── main.py        point d'entrée FastAPI
├── config.py      variables d'environnement
├── otel_setup.py  OpenTelemetry — importé avant tout le reste
├── schema.py      schéma fédéré Strawberry
├── models/        DTOs d'entrée et de sortie
├── resolvers/     les 3 queries et l'orchestration
└── services/      OSMnx, OSRM, client gRPC, trafic, tracking
proto/astar.proto  contrat gRPC avec le routeur C++
tests/             11 fichiers (unitaires + 1 intégration)
```

## Auteur

Valentin Mignon
