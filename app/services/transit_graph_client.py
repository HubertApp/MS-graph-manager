"""Client de la couche transport en commun servie par MS-aom-agregator.

Deux requetes GraphQL, adressees directement au subgraph (pas via la gateway) :

  - `transitCoverage` : les reseaux GTFS ingeres et l'emprise de leurs arrets.
    Sert a ne charger que les reseaux qui recouvrent la zone d'une demande, sans
    avoir a configurer leurs identifiants a la main.
  - `transitGraph(networkId, departureTime)` : la couche transit d'un reseau
    pour une tranche horaire (arrets, trajets, montees, descentes).

Les deux sont mises en cache : la couverture pour TRANSIT_DISCOVERY_TTL_S, la
couche par (reseau, date, heure). L'evenement RabbitMQ `gtfs.network.ingested`
(app/services/transit_events.py) invalide les deux pour le reseau reingere.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import httpx

from app.config import (
    AOM_GRAPHQL_URL,
    TRANSIT_CACHE_MAX_ENTRIES,
    TRANSIT_DISCOVERY_TTL_S,
    TRANSIT_FETCH_TIMEOUT_S,
    TRANSIT_NETWORK_IDS,
)
from app.services.routing_graph import BBox, GraphEdge, GraphNode

logger = logging.getLogger(__name__)

_GRAPH_QUERY = """
query TransitGraph($networkId: ID!, $departureTime: DateTime!) {
  transitGraph(networkId: $networkId, departureTime: $departureTime) {
    networkId
    hourBucket
    nodes { id lat lon isTransitStop name }
    edges {
      edgeId sourceId targetId weight length layer transitLineId name
      routeShortName routeType routeColor headsign
    }
  }
}
"""

_COVERAGE_QUERY = """
query TransitCoverage {
  transitCoverage { networkId stopCount north south east west }
}
"""

# Sans reponse exploitable de l'agregateur, on reessaie la decouverte plus tot
# qu'apres le TTL nominal, sans pour autant le solliciter a chaque requete.
_DISCOVERY_RETRY_S = 60.0


@dataclass(frozen=True)
class TransitLayer:
    network_id: str
    hour_bucket: Optional[int]
    nodes: List[GraphNode]
    edges: List[GraphEdge]


@dataclass(frozen=True)
class NetworkCoverage:
    network_id: str
    stop_count: int = 0
    west: Optional[float] = None
    south: Optional[float] = None
    east: Optional[float] = None
    north: Optional[float] = None

    @property
    def known_extent(self) -> bool:
        return None not in (self.west, self.south, self.east, self.north)

    def intersects(self, bbox: BBox) -> bool:
        """Emprise inconnue => on tente quand meme (la fusion filtrera)."""
        if not self.known_extent:
            return True
        west, south, east, north = bbox
        return not (
            self.east < west or self.west > east or self.north < south or self.south > north
        )


_cache: "OrderedDict[Tuple[str, str], TransitLayer]" = OrderedDict()
_locks: Dict[Tuple[str, str], asyncio.Lock] = {}
_networks: Optional[List[NetworkCoverage]] = None
_networks_expire_at = 0.0
_seen_networks: Set[str] = set()


def reset_cache() -> None:
    """Vide tous les caches (tests)."""
    global _networks, _networks_expire_at
    _cache.clear()
    _locks.clear()
    _seen_networks.clear()
    _networks = None
    _networks_expire_at = 0.0


def _hour_key(departure_time: datetime) -> str:
    """Tranche horaire de la demande. Une date avec fuseau est ramenee en UTC ;
    une date naive est prise telle quelle, comme le fait l'agregateur."""
    moment = departure_time
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H")


def invalidate(network_id: str) -> None:
    """Appelee par le consommateur RabbitMQ quand un reseau est reingere."""
    global _networks, _networks_expire_at
    removed = [key for key in _cache if key[0] == network_id]
    for key in removed:
        _cache.pop(key, None)
    # La couverture a pu changer (nouveaux arrets) : on la redecouvrira.
    _networks = None
    _networks_expire_at = 0.0
    logger.info(
        "cache transit vide pour %s (%d tranche(s) horaire(s))", network_id, len(removed)
    )


def register_network(network_id: str) -> None:
    """Retient un reseau vu passer sur le bus, meme si la decouverte echoue."""
    _seen_networks.add(network_id)


def cached_layers() -> List[Tuple[str, str]]:
    """(reseau, tranche horaire) des couches en cache — pour /health."""
    return list(_cache.keys())


async def get_transit_layer(
    network_id: str, departure_time: datetime
) -> Optional[TransitLayer]:
    if not AOM_GRAPHQL_URL:
        return None

    key = (network_id, _hour_key(departure_time))
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached

    # Sans ce verrou, dix requetes simultanees declencheraient dix traductions
    # completes cote agregateur.
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached

        layer = await _fetch_layer(network_id, departure_time)
        if layer is not None:
            _cache[key] = layer
            while len(_cache) > max(1, TRANSIT_CACHE_MAX_ENTRIES):
                _cache.popitem(last=False)
        _locks.pop(key, None)
        return layer


async def _post(query: str, variables: Optional[dict] = None) -> Optional[dict]:
    payload = {"query": query, "variables": variables or {}}
    try:
        async with httpx.AsyncClient(timeout=TRANSIT_FETCH_TIMEOUT_S) as client:
            response = await client.post(AOM_GRAPHQL_URL, json=payload)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("agregateur AOM injoignable (%s)", AOM_GRAPHQL_URL, exc_info=True)
        return None

    if body.get("errors"):
        logger.warning("erreurs graphql cote agregateur : %s", body["errors"])
        return None
    return body.get("data") or None


def _as_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _fetch_layer(network_id: str, departure_time: datetime) -> Optional[TransitLayer]:
    started = time.perf_counter()
    data = await _post(
        _GRAPH_QUERY,
        {"networkId": network_id, "departureTime": departure_time.isoformat()},
    )
    graph = (data or {}).get("transitGraph")
    if not graph or not graph.get("nodes"):
        if data is not None:
            logger.info("aucune couche transit pour %s a %s", network_id, departure_time)
        return None

    nodes = [
        GraphNode(
            id=node["id"],
            lat=float(node["lat"]),
            lon=float(node["lon"]),
            is_transit_stop=bool(node.get("isTransitStop", True)),
            name=node.get("name"),
        )
        for node in graph["nodes"]
    ]
    edges = [
        GraphEdge(
            edge_id=edge["edgeId"],
            source_id=edge["sourceId"],
            target_id=edge["targetId"],
            weight=float(edge["weight"]),
            length=float(edge["length"]),
            layer=int(edge["layer"]),
            name=edge.get("name"),
            transit_line_id=edge.get("transitLineId"),
            route_short_name=edge.get("routeShortName"),
            route_type=_as_int(edge.get("routeType")),
            route_color=edge.get("routeColor"),
            headsign=edge.get("headsign"),
        )
        for edge in graph.get("edges") or []
    ]

    logger.info(
        "couche transit chargee en %.0f ms : %d noeuds, %d aretes (%s, h=%s)",
        (time.perf_counter() - started) * 1000,
        len(nodes), len(edges), network_id, graph.get("hourBucket"),
    )
    return TransitLayer(
        network_id=network_id,
        hour_bucket=_as_int(graph.get("hourBucket")),
        nodes=nodes,
        edges=edges,
    )


def _allowed(network_id: str) -> bool:
    return not TRANSIT_NETWORK_IDS or network_id in TRANSIT_NETWORK_IDS


async def list_networks() -> List[NetworkCoverage]:
    """Reseaux candidats a la fusion.

    Sources, par ordre de preference : la requete `transitCoverage` de
    l'agregateur (emprise connue), sinon la liste TRANSIT_NETWORK_IDS et les
    reseaux vus passer sur le bus (emprise inconnue).
    """
    global _networks, _networks_expire_at
    if not AOM_GRAPHQL_URL:
        return []

    now = time.monotonic()
    if _networks is not None and now < _networks_expire_at:
        return _networks

    data = await _post(_COVERAGE_QUERY)
    coverages = (data or {}).get("transitCoverage")
    if coverages is None:
        known = sorted(set(TRANSIT_NETWORK_IDS) | {nid for nid in _seen_networks if _allowed(nid)})
        _networks = [NetworkCoverage(network_id=network_id) for network_id in known]
        _networks_expire_at = now + _DISCOVERY_RETRY_S
        return _networks

    networks = [
        NetworkCoverage(
            network_id=item["networkId"],
            stop_count=int(item.get("stopCount") or 0),
            west=item.get("west"),
            south=item.get("south"),
            east=item.get("east"),
            north=item.get("north"),
        )
        for item in coverages
        if item.get("networkId") and _allowed(item["networkId"])
    ]
    listed = {network.network_id for network in networks}
    # Un reseau configure explicitement mais absent de la reponse reste tente.
    networks.extend(
        NetworkCoverage(network_id=network_id)
        for network_id in TRANSIT_NETWORK_IDS
        if network_id not in listed
    )
    logger.info("reseaux de transport decouverts : %s", [n.network_id for n in networks])
    _networks = networks
    _networks_expire_at = now + max(1.0, TRANSIT_DISCOVERY_TTL_S)
    return networks


async def prewarm(network_id: Optional[str] = None, departure_time: Optional[datetime] = None) -> None:
    """Charge a l'avance la couche de l'heure courante (au demarrage, apres une
    ingestion) : la premiere demande en transport n'attend plus l'agregation."""
    moment = departure_time or datetime.now(timezone.utc)
    try:
        networks = await list_networks()
        for network in networks:
            if network_id is None or network.network_id == network_id:
                await get_transit_layer(network.network_id, moment)
    except Exception:  # best effort : ne doit jamais faire tomber le service
        logger.warning("prechargement de la couche transit impossible", exc_info=True)
