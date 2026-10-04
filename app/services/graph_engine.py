import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.itinerary import (
    EdgeDTO,
    FrontStepDTO,
    NodeDTO,
    PathSegmentDTO,
    RoutingGraphSnapshot,
)
from app.services.instructions import build_instructions, edge_kind
from app.services.routing_graph import PathEdge, as_routing_graph

Coordinate = Tuple[float, float]

DEFAULT_PROFILE_SPEEDS_MPS = {
    "walking": 1.4,
    "cycling": 4.2,
    "driving": 13.9,
    "transit": 8.3,
    "multimodal": 7.0,
}


def _haversine_m(from_coord: Coordinate, to_coord: Coordinate) -> float:
    lon1, lat1 = from_coord
    lon2, lat2 = to_coord

    radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _tile_id(coord: Coordinate) -> str:
    lon, lat = coord
    return f"{round(lat, 2)}:{round(lon, 2)}"


def _estimate_slope_penalty(
    start: Coordinate,
    end: Coordinate,
    edge_index: int,
) -> float:
    length = _haversine_m(start, end)
    if length <= 0:
        return 1.0

    pseudo_delta_elevation_m = ((edge_index % 7) - 3) * 0.6
    slope = abs(pseudo_delta_elevation_m) / max(length, 1.0)
    return 1.0 + min(0.25, slope * 10.0)


def _base_speed_mps(profile: str) -> float:
    return DEFAULT_PROFILE_SPEEDS_MPS.get(profile.lower(), DEFAULT_PROFILE_SPEEDS_MPS["driving"])


def _edge_layer(profile: str) -> int:
    if profile.lower() in {"transit", "multimodal"}:
        return 2
    return 1


def build_multilayer_snapshot(
    coordinates: Sequence[Coordinate],
    routing_profile: str,
    friction_by_tile: Optional[Dict[str, float]] = None,
    realtime_factor: float = 1.0,
    predictive_factor: float = 1.0,
    names: Optional[Sequence[Optional[str]]] = None,
) -> RoutingGraphSnapshot:
    """Chaine lineaire ponderee (repli OSRM).

    `names[i]` est le nom de la voie du segment coordinates[i] -> [i+1] ; il
    permet aux instructions de nommer les rues ("Tournez a droite sur ...").
    """
    if not coordinates:
        return RoutingGraphSnapshot(nodes=[], edges=[])

    friction_by_tile = friction_by_tile or {}
    speed_mps = _base_speed_mps(routing_profile)
    layer = _edge_layer(routing_profile)

    nodes: List[NodeDTO] = []
    for index, coord in enumerate(coordinates):
        lon, lat = coord
        nodes.append(
            NodeDTO(
                id=f"synthetic:{index}",
                lat=lat,
                lon=lon,
                is_transit_stop=(layer == 2 and index % 8 == 0),
            )
        )

    edges: List[EdgeDTO] = []
    for index in range(len(coordinates) - 1):
        start = coordinates[index]
        end = coordinates[index + 1]

        length_m = _haversine_m(start, end)
        travel_time_s = length_m / max(speed_mps, 0.1)
        slope_penalty = _estimate_slope_penalty(start, end, index)
        tile_friction = friction_by_tile.get(_tile_id(start), 1.0)

        weight = (
            travel_time_s
            * slope_penalty
            * max(tile_friction, 0.1)
            * max(realtime_factor, 0.1)
            * max(predictive_factor, 0.1)
        )

        edges.append(
            EdgeDTO(
                edge_id=f"synthetic_{index}",
                source_id=f"synthetic:{index}",
                target_id=f"synthetic:{index + 1}",
                weight=weight,
                length=length_m,
                layer=layer,
                # Nom de voie issu des etapes OSRM ; None si inconnu, les
                # instructions decrivent alors la direction sans nommer la rue.
                name=(names[index] or None) if names and index < len(names) else None,
            )
        )

    return RoutingGraphSnapshot(nodes=nodes, edges=edges)


def _osm_ids_of(edge) -> List[int]:
    """Identifiants OSM d'un edge_id de la forme osm_{u}_{v}_{key}."""
    if not edge.edge_id.startswith("osm_"):
        return []
    parts = edge.edge_id.split("_")
    return [int(part) for part in parts[1:3] if part.isdigit()]


def _segment_kind(edge) -> str:
    """road | transit | connection. Accepte un PathEdge ou un EdgeDTO."""
    if isinstance(edge, PathEdge):
        kind = edge_kind(edge)
        if kind == "ride":
            return "transit"
        return "road" if kind == "road" else "connection"
    if edge.layer == 2:
        return "transit"
    if edge.layer == 3:
        return "connection"
    return "road"


def build_path_segments(
    edges: Sequence,
    departure_time: datetime,
    routing_profile: str,
) -> List[PathSegmentDTO]:
    """Un segment par troncon homogene (meme type, meme ligne), pas par arete.

    `expected_arrival` est l'heure prevue en fin de segment.
    """
    segments: List[PathSegmentDTO] = []
    cumulative_seconds = 0.0
    current_key = None

    for edge in edges:
        cumulative_seconds += edge.weight
        kind = _segment_kind(edge)
        key = (kind, edge.transit_line_id)

        if key != current_key:
            segments.append(PathSegmentDTO(
                type=kind,
                osm_ids=[],
                transit_line_id=edge.transit_line_id,
                expected_arrival=departure_time + timedelta(seconds=cumulative_seconds),
            ))
            current_key = key

        for osm_id in _osm_ids_of(edge):
            if not segments[-1].osm_ids or segments[-1].osm_ids[-1] != osm_id:
                segments[-1].osm_ids.append(osm_id)
        segments[-1].expected_arrival = departure_time + timedelta(seconds=cumulative_seconds)

    return segments


@dataclass(frozen=True)
class RenderedPath:
    """Traduction d'un chemin (ids de noeuds) en donnees pretes pour le front."""

    selected_edges: List[PathEdge]
    geometry_coordinates: List[List[float]]   # [[lon, lat], ...] format GeoJSON
    front_steps: List[FrontStepDTO]
    distance_m: float
    duration_s: float


def render_path(
    graph,
    node_path: Sequence[str],
    routing_profile: str = "driving",
) -> Optional[RenderedPath]:
    """Traduit une suite d'ids de noeuds en aretes, geometrie et instructions.

    Args:
        graph: le graphe qui a servi a la resolution (RoutingGraph, ou
            RoutingGraphSnapshot pour compatibilite)
        node_path: ids de noeuds renvoyes par le C++, dans l'ordre
        routing_profile: profil de la demande, fixe le mode des etapes a pied/velo/voiture

    Returns:
        RenderedPath, ou None si le chemin n'est pas reconstituable.
    """
    if len(node_path) < 2:
        return None

    selected_edges = as_routing_graph(graph).resolve_path(node_path)
    if selected_edges is None:
        return None

    geometry_coordinates, front_steps = build_instructions(selected_edges, routing_profile)
    if len(geometry_coordinates) < 2:
        return None

    return RenderedPath(
        selected_edges=selected_edges,
        geometry_coordinates=geometry_coordinates,
        front_steps=front_steps,
        distance_m=sum(edge.length for edge in selected_edges),
        duration_s=sum(edge.weight for edge in selected_edges),
    )
