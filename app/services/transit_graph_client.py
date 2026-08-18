import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx

from app.config import AOM_GRAPHQL_URL, TRANSIT_FETCH_TIMEOUT_S
from app.models.itinerary import EdgeDTO, NodeDTO

logger = logging.getLogger(__name__)

_QUERY = """
query TransitGraph($networkId: ID!, $departureTime: DateTime!) {
  transitGraph(networkId: $networkId, departureTime: $departureTime) {
    networkId
    hourBucket
    nodes { id lat lon isTransitStop }
    edges { edgeId sourceId targetId weight length layer transitLineId name }
  }
}
"""


@dataclass(frozen=True)
class TransitLayer:
    nodes: List[NodeDTO]
    edges: List[EdgeDTO]


_cache: Dict[Tuple[str, int], TransitLayer] = {}
_locks: Dict[Tuple[str, int], asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


def invalidate(network_id: str) -> None:
    """Appelee par le consommateur RabbitMQ quand le reseau est reingere."""
    removed = [key for key in _cache if key[0] == network_id]
    for key in removed:
        _cache.pop(key, None)
    logger.info(
        "cache transit vide pour %s (%d tranche(s) horaire(s))", network_id, len(removed)
    )


async def get_transit_layer(
    network_id: str, departure_time: datetime
) -> Optional[TransitLayer]:
    if not AOM_GRAPHQL_URL:
        return None

    key = (network_id, departure_time.hour)

    cached = _cache.get(key)
    if cached is not None:
        return cached

    async with _locks_guard:
        lock = _locks.setdefault(key, asyncio.Lock())

    # Sans ce verrou, dix requetes simultanees declencheraient dix traductions
    # completes cote agregateur.
    async with lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached

        layer = await _fetch(network_id, departure_time)
        if layer is not None:
            _cache[key] = layer
        return layer


async def _fetch(network_id: str, departure_time: datetime) -> Optional[TransitLayer]:
    payload = {
        "query": _QUERY,
        "variables": {
            "networkId": network_id,
            "departureTime": departure_time.isoformat(),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=TRANSIT_FETCH_TIMEOUT_S) as client:
            response = await client.post(AOM_GRAPHQL_URL, json=payload)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError):
        logger.warning(
            "couche transit indisponible pour %s ; itineraire en voirie seule",
            network_id,
            exc_info=True,
        )
        return None

    if body.get("errors"):
        logger.warning("erreurs graphql cote agregateur : %s", body["errors"])
        return None

    graph = (body.get("data") or {}).get("transitGraph")
    if not graph or not graph.get("nodes"):
        return None

    nodes = [
        NodeDTO(
            id=node["id"],
            lat=node["lat"],
            lon=node["lon"],
            is_transit_stop=node["isTransitStop"],
        )
        for node in graph["nodes"]
    ]
    edges = [
        EdgeDTO(
            edge_id=edge["edgeId"],
            source_id=edge["sourceId"],
            target_id=edge["targetId"],
            weight=edge["weight"],
            length=edge["length"],
            layer=edge["layer"],
            transit_line_id=edge.get("transitLineId"),
            name=edge.get("name"),
        )
        for edge in graph["edges"]
    ]

    logger.info(
        "couche transit chargee : %d noeuds, %d aretes (%s, h=%s)",
        len(nodes), len(edges), network_id, graph.get("hourBucket"),
    )
    return TransitLayer(nodes=nodes, edges=edges)
