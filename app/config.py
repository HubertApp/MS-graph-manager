import os
from typing import List, Tuple

from dotenv import load_dotenv

load_dotenv()

BBox = Tuple[float, float, float, float]  # (ouest, sud, est, nord) — ordre osmnx >= 2


def _env_list(name: str, default: str = "") -> List[str]:
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


def _parse_bboxes(raw: str) -> List[BBox]:
    """Lit "ouest,sud,est,nord;ouest,sud,est,nord". Une entree invalide est ignoree."""
    bboxes: List[BBox] = []
    for chunk in raw.split(";"):
        parts = [part.strip() for part in chunk.split(",") if part.strip()]
        if len(parts) != 4:
            continue
        try:
            west, south, east, north = (float(part) for part in parts)
        except ValueError:
            continue
        if west < east and south < north:
            bboxes.append((west, south, east, north))
    return bboxes


OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://localhost:5000")
TRAFFIC_INFO_URL = os.getenv("TRAFFIC_INFO_URL", "")
PREDICTIVE_INFO_URL = os.getenv("PREDICTIVE_INFO_URL", "")
TRACKING_SERVICE_URL = os.getenv("TRACKING_SERVICE_URL", "")
OSMNX_GRAPH_MARGIN_M = float(os.getenv("OSMNX_GRAPH_MARGIN_M", "1500"))

# ── Cache regional du graphe routier (voir app/services/osmnx_graph.py) ─────
# Une region telechargee une fois sert toutes les demandes qu'elle contient :
# plus aucun appel Overpass par requete.
OSMNX_CACHE_DIR = os.getenv("OSMNX_CACHE_DIR", "./cache/graphs")
OSMNX_REGION_PADDING_M = float(os.getenv("OSMNX_REGION_PADDING_M", "1000"))
OSMNX_MEMORY_REGIONS = int(os.getenv("OSMNX_MEMORY_REGIONS", "6"))
OSMNX_REQUESTS_TIMEOUT_S = float(os.getenv("OSMNX_REQUESTS_TIMEOUT_S", "90"))
OSMNX_FAILURE_COOLDOWN_S = float(os.getenv("OSMNX_FAILURE_COOLDOWN_S", "60"))
OSMNX_PRELOAD_BBOXES = _parse_bboxes(os.getenv("OSMNX_PRELOAD_BBOXES", ""))
OSMNX_PRELOAD_NETWORK_TYPES = _env_list("OSMNX_PRELOAD_NETWORK_TYPES", "walk,drive")

CPP_ROUTER_GRPC_TARGET = os.getenv("CPP_ROUTER_GRPC_TARGET", "envoy:50051")
CPP_ROUTER_TIMEOUT_S = float(os.getenv("CPP_ROUTER_TIMEOUT_S", "10.0"))

CPP_ROUTER_MAX_MESSAGE_MB = int(os.getenv("CPP_ROUTER_MAX_MESSAGE_MB", "64"))

# ── Couche transport en commun (GTFS servi par MS-aom-agregator) ────────────
# URL vide => couche desactivee, les profils transit/multimodal marchent a pied.
AOM_GRAPHQL_URL = os.getenv("AOM_GRAPHQL_URL", "")
# Liste blanche facultative. Vide => decouverte automatique des reseaux ingeres.
TRANSIT_NETWORK_IDS = _env_list("TRANSIT_NETWORK_IDS")
TRANSIT_ACCESS_RADIUS_M = float(os.getenv("TRANSIT_ACCESS_RADIUS_M", "400"))
TRANSIT_FETCH_TIMEOUT_S = float(os.getenv("TRANSIT_FETCH_TIMEOUT_S", "30.0"))
TRANSIT_DISCOVERY_TTL_S = float(os.getenv("TRANSIT_DISCOVERY_TTL_S", "600"))
TRANSIT_CACHE_MAX_ENTRIES = int(os.getenv("TRANSIT_CACHE_MAX_ENTRIES", "48"))

# ── RabbitMQ : evenement gtfs.network.ingested publie par MS-aom-agregator ──
# Vide => pas d'invalidation automatique (le cache transit expire seul au
# changement d'heure).
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "")
