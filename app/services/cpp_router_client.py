import base64
import gzip
import json
from typing import List, Optional

import httpx

from app.config import CPP_ROUTER_URL, GRAPH_COMPRESSION_EDGE_THRESHOLD
from app.models.itinerary import RouteRequestDTO, RoutingGraphSnapshot


def _compress_payload(payload: dict) -> str:
    raw_bytes = json.dumps(payload).encode("utf-8")
    compressed = gzip.compress(raw_bytes)
    return base64.b64encode(compressed).decode("utf-8")


async def compute_itinerary_with_cpp(
    snapshot: RoutingGraphSnapshot,
    request: RouteRequestDTO,
    default_edge_ids: Optional[List[int]] = None,
) -> List[int]:
    if not snapshot.edges:
        return []

    if not CPP_ROUTER_URL:
        return default_edge_ids or []

    graph_payload = {
        "nodes": [
            {
                "id": node.id,
                "lat": node.lat,
                "lon": node.lon,
                "is_transit_stop": node.is_transit_stop,
            }
            for node in snapshot.nodes
        ],
        "edges": [
            {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "weight": edge.weight,
                "length": edge.length,
                "layer": edge.layer,
            }
            for edge in snapshot.edges
        ],
    }

    request_payload = {
        "start": {
            "lat": request.start_point.lat,
            "lon": request.start_point.lon,
        },
        "end": {
            "lat": request.end_point.lat,
            "lon": request.end_point.lon,
        },
        "profile": request.routing_profile,
        "departure_time": request.departure_time.isoformat(),
    }

    if len(snapshot.edges) > GRAPH_COMPRESSION_EDGE_THRESHOLD:
        body = {
            "encoding": "gzip+base64",
            "graph": _compress_payload(graph_payload),
            "request": request_payload,
        }
    else:
        body = {
            "encoding": "json",
            "graph": graph_payload,
            "request": request_payload,
        }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(CPP_ROUTER_URL, json=body)
            if response.status_code != 200:
                return default_edge_ids or []

            data = response.json()
            edge_ids = data.get("edge_ids", [])
            if not edge_ids:
                return default_edge_ids or []

            return [edge_id for edge_id in edge_ids if 0 <= edge_id < len(snapshot.edges)]
    except httpx.HTTPError:
        return default_edge_ids or []
