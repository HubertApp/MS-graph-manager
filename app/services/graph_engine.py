import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.itinerary import EdgeDTO, NodeDTO, PathSegmentDTO, RoutingGraphSnapshot

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
) -> RoutingGraphSnapshot:
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
            )
        )

    return RoutingGraphSnapshot(nodes=nodes, edges=edges)


def build_path_segments(
    edges: Sequence[EdgeDTO],
    departure_time: datetime,
    routing_profile: str,
) -> List[PathSegmentDTO]:
    if not edges:
        return []

    is_transit = routing_profile.lower() in {"transit", "multimodal"}
    segment_type = "transit" if is_transit else "road"

    segments: List[PathSegmentDTO] = []
    cumulative_seconds = 0.0

    for index, edge in enumerate(edges):
        cumulative_seconds += edge.weight
        expected_arrival = departure_time + timedelta(seconds=cumulative_seconds)

        segments.append(
            PathSegmentDTO(
                type=segment_type,
                osm_ids=[index],
                transit_line_id=(f"LINE-{edge.layer}" if is_transit else None),
                expected_arrival=expected_arrival,
            )
        )

    return segments
