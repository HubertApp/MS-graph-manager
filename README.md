# MS Graph Manager

## Objectif du MS 

Pouvoir rechercher un itinéraire grâce à des coordonnées géographiques et établir un trajet sur une carte avec OpenStreetMap.

## Prérequis

- Python 3.10+
- Docker (pour OSRM)

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
```

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