# MS Graph Manager

## Objectif du MS 

Pouvoir rechercher un itinéraire grâce à des coordonnées géographiques et établir un trajet sur une carte avec OpenStreetMap.

## Prérequis

- Python 3.10+
- Docker (pour OSRM, et en option pour lancer le service lui-même en conteneur)

## Installation

### 1. Cloner le projet

```bash
git clone https://github.com/HubertApp/MS-graph-manager.git
cd MS-graph-manager
```

### 2. Installer les dépendances Python

```bash
pip install -r requirements.txt
```

### 3. Configuration

Créer un fichier `.env` à la racine du projet :

```env
OSRM_BASE_URL=http://localhost:5000
TRAFFIC_INFO_URL=
PREDICTIVE_INFO_URL=
CPP_ROUTER_GRPC_TARGET=localhost:50051
TRACKING_SERVICE_URL=
OSMNX_GRAPH_MARGIN_M=1500
```

Les URLs/adresses supplémentaires sont optionnelles :
- si absentes, le service reste fonctionnel en mode local simplifié (facteurs = 1.0, calcul fallback Python)
- si présentes, elles activent respectivement les ajustements trafic, prédictifs, la notification de suivi d'itinéraire
- `CPP_ROUTER_GRPC_TARGET` pointe vers le service de routage C++ (gRPC, `host:port`) ; si injoignable, le calcul retombe automatiquement sur le plus court chemin déjà calculé localement (osmnx/OSRM)

### Note sur OSMnx

- `getItineraireFromTo` utilise en priorité un prégraph local OSMnx (bbox autour du trajet), puis calcule le plus court chemin pondéré.
- si OSMnx est indisponible ou échoue sur la zone demandée, fallback automatique vers le flux OSRM existant.
- `OSMNX_GRAPH_MARGIN_M` permet d'ajuster la taille de la zone chargée autour de la demande (plus grand = plus robuste, mais plus lourd).

## Lancement

### 1. Démarrer le serveur OSRM (Docker)

```bash
mkdir -p ~/docker_osrm/osrm-data
cd ~/docker_osrm/osrm-data
```

Télécharger un fichier OSM PBF (ex: [lorraine](https://download.geofabrik.de/europe/france/lorraine.html)) et le placer dans ce dossier.

```bash
# Préparer les données (à faire une seule fois)
docker run -t -v ${PWD}:/data osrm/osrm-backend osrm-extract -p /opt/car.lua /data/lorraine-251229.osm.pbf
docker run -t -v ${PWD}:/data osrm/osrm-backend osrm-partition /data/lorraine-251229.osrm
docker run -t -v ${PWD}:/data osrm/osrm-backend osrm-customize /data/lorraine-251229.osrm

# Lancer le serveur OSRM
docker run -t -p 5000:5000 -v ${PWD}:/data osrm/osrm-backend osrm-routed --algorithm mld /data/lorraine-251229.osrm
```

Vérifier que OSRM fonctionne :
```
http://localhost:5000/route/v1/driving/6.1757,49.1193;6.1825,49.1096
```

### 2. Démarrer l'API FastAPI

```bash
uvicorn app.main:app --reload
```

L'API est accessible sur : **http://localhost:8000**

L'interface GraphQL (GraphiQL) est accessible sur : **http://localhost:8000/graphql**

### 3. Alternative : lancer le service en conteneur

```bash
docker build -t ms-graph-manager .
docker run -p 8003:80 --env-file .env ms-graph-manager
```

Ou via `docker-compose.yml` (service `graph-manager` + un service `osrm` optionnel, qui
suppose des données déjà extraites dans `./osrm-data`, voir étape 1 ci-dessus) :

```bash
docker compose up --build
```

L'API est alors accessible sur **http://localhost:8003/graphql**.

## Queries GraphQL principales

### 1) Query historique (compatibilité)

```graphql
query {
	route(fromLat: 49.1193, fromLon: 6.1757, toLat: 49.1096, toLon: 6.1825) {
		distanceM
		durationS
	}
}
```

### 2) Query cible: `getItineraireFromTo`

```graphql
query RouteMulticouche($request: RouteRequestDTO!, $frictions: [FrictionUpdateDTO!]) {
	getItineraireFromTo(request: $request, frictionUpdates: $frictions) {
		distanceM
		durationS
		traffic {
			realtimeFactor
			predictiveFactor
			source
		}
		steps {
			instruction
			distanceM
			durationS
		}
		segments {
			type
			osmIds
			transitLineId
			expectedArrival
		}
		graphSnapshot {
			nodes {
				id
				lat
				lon
				isTransitStop
			}
			edges {
				sourceId
				targetId
				weight
				length
				layer
			}
		}
	}
}
```

Variables:

```json
{
	"request": {
		"startPoint": {"lat": 49.1193, "lon": 6.1757},
		"endPoint": {"lat": 49.1096, "lon": 6.1825},
		"departureTime": "2026-03-18T08:30:00Z",
		"routingProfile": "driving"
	},
	"frictions": [
		{
			"tileId": "49.12:6.18",
			"frictionCoefficient": 1.15,
			"sourceEvent": "road_work"
		}
	]
}
```

### 3) Query optionnelle trafic

```graphql
query {
	infoTrafic(fromLat: 49.1193, fromLon: 6.1757, toLat: 49.1096, toLon: 6.1825) {
		realtimeFactor
		predictiveFactor
		source
	}
}
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Par défaut, `pytest` ne lance que les tests unitaires (tous les appels réseau/gRPC
sont mockés — aucune dépendance externe requise). Le test d'intégration
bout-en-bout (`tests/test_integration_live_graph.py`) appelle la vraie API
Overpass et est exclu du run par défaut ; pour le lancer :

```bash
pytest -m integration
```

## Structure du projet

```
app/
├── main.py          # Point d'entrée FastAPI
├── config.py        # Configuration (variables d'environnement)
├── schema.py        # Schéma GraphQL (Strawberry)
├── index.html       # Démo carte Leaflet
├── models/          # Modèles de données
├── resolvers/       # Resolvers GraphQL
└── services/        # Services (client OSRM)
```

## Auteur

### Valentin Mignon