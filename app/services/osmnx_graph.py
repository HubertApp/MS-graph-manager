import logging
import math
from typing import Dict, List, Optional, Tuple

from app.models.itinerary import EdgeDTO, FrontStepDTO, NodeDTO, RoutingGraphSnapshot

logger = logging.getLogger(__name__)

Coordinate = Tuple[float, float]

PROFILE_TO_NETWORK_TYPE = {
    "walking": "walk",
    "cycling": "bike",
    "driving": "drive",
    "transit": "drive",
    "multimodal": "drive",
}

DEFAULT_PROFILE_SPEEDS_MPS = {
    "walking": 1.4,
    "cycling": 4.2,
    "driving": 13.9,
    "transit": 8.3,
    "multimodal": 7.0,
}


def _tile_id(coord: Coordinate) -> str:
    lon, lat = coord
    return f"{round(lat, 2)}:{round(lon, 2)}"


def _compute_bbox(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    margin_m: float,
) -> Tuple[float, float, float, float]:
    center_lat = (start_lat + end_lat) / 2.0
    margin_deg_lat = margin_m / 111_320.0
    margin_deg_lon = margin_m / max(1.0, 111_320.0 * math.cos(math.radians(center_lat)))

    north = max(start_lat, end_lat) + margin_deg_lat
    south = min(start_lat, end_lat) - margin_deg_lat
    east = max(start_lon, end_lon) + margin_deg_lon
    west = min(start_lon, end_lon) - margin_deg_lon
    return north, south, east, west


def _infer_speed_mps(edge_data: Dict, routing_profile: str) -> float:
    maxspeed = edge_data.get("maxspeed")
    if isinstance(maxspeed, list):
        maxspeed = maxspeed[0] if maxspeed else None

    if isinstance(maxspeed, str):
        numeric = "".join(char for char in maxspeed if (char.isdigit() or char == "."))
        if numeric:
            try:
                speed_kmh = float(numeric)
                return max(0.5, speed_kmh / 3.6)
            except ValueError:
                pass

    return DEFAULT_PROFILE_SPEEDS_MPS.get(
        routing_profile.lower(), DEFAULT_PROFILE_SPEEDS_MPS["driving"]
    )


def _build_nodes(graph, layer: int) -> Tuple[List[NodeDTO], Dict[int, int], List[int]]:
    node_ids = list(graph.nodes())
    node_index_map = {node_id: index for index, node_id in enumerate(node_ids)}

    nodes: List[NodeDTO] = []
    for node_id in node_ids:
        node_data = graph.nodes[node_id]
        nodes.append(
            NodeDTO(
                id=f"osm:{node_id}",
                lat=float(node_data["y"]),
                lon=float(node_data["x"]),
                is_transit_stop=(layer == 2 and node_index_map[node_id] % 11 == 0),
            )
        )

    return nodes, node_index_map, node_ids


def _resolve_edge_length_m(graph, u: int, v: int, data: Dict, ox) -> float:
    length_m = float(data.get("length", 0.0))
    if length_m > 0:
        return length_m

    source_coord = (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"]))
    target_coord = (float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"]))
    return ox.distance.great_circle(
        source_coord[1],
        source_coord[0],
        target_coord[1],
        target_coord[0],
    )


def _build_edges(
    graph,
    routing_profile: str,
    layer: int,
    friction_by_tile: Dict[str, float],
    realtime_factor: float,
    predictive_factor: float,
    ox,
) -> Tuple[List[EdgeDTO], Dict[Tuple[int, int, int], int], Dict[int, str]]:
    edges: List[EdgeDTO] = []
    edge_index_map: Dict[Tuple[int, int, int], int] = {}
    edge_names: Dict[int, str] = {}

    for u, v, key, data in graph.edges(keys=True, data=True):
        source_coord = (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"]))
        length_m = _resolve_edge_length_m(graph, u, v, data, ox)

        speed_mps = _infer_speed_mps(data, routing_profile)
        travel_time_s = length_m / max(speed_mps, 0.5)
        grade_abs = abs(float(data.get("grade_abs", 0.0)))
        slope_penalty = 1.0 + min(0.3, grade_abs * 2.0)
        tile_friction = friction_by_tile.get(_tile_id(source_coord), 1.0)

        effective_weight = (
            travel_time_s
            * slope_penalty
            * max(tile_friction, 0.1)
            * max(realtime_factor, 0.1)
            * max(predictive_factor, 0.1)
        )

        data["_effective_weight"] = effective_weight

        edge_index = len(edges)
        edges.append(
            EdgeDTO(
                edge_id=f"osm_{u}_{v}_{key}",
                source_id=f"osm:{u}",
                target_id=f"osm:{v}",
                weight=effective_weight,
                length=length_m,
                layer=layer,
            )
        )
        edge_index_map[(u, v, key)] = edge_index

        edge_name = data.get("name")
        if isinstance(edge_name, list):
            edge_name = edge_name[0] if edge_name else ""
        edge_names[edge_index] = str(edge_name or "Continue")

    return edges, edge_index_map, edge_names


def _extract_selected_path(
    graph,
    shortest_path_nodes: List[int],
    edge_index_map: Dict[Tuple[int, int, int], int],
    edge_names: Dict[int, str],
    edges: List[EdgeDTO],
) -> Tuple[List[int], List[FrontStepDTO]]:
    selected_edge_ids: List[int] = []
    front_steps: List[FrontStepDTO] = []

    for path_index in range(len(shortest_path_nodes) - 1):
        u = shortest_path_nodes[path_index]
        v = shortest_path_nodes[path_index + 1]
        candidates = graph.get_edge_data(u, v)
        if not candidates:
            continue

        best_key = min(
            candidates,
            key=lambda candidate_key, candidates=candidates: float(
                candidates[candidate_key].get("_effective_weight", float("inf"))
            ),
        )

        edge_index = edge_index_map.get((u, v, best_key))
        if edge_index is None:
            continue

        selected_edge_ids.append(edge_index)
        selected_edge = edges[edge_index]
        front_steps.append(
            FrontStepDTO(
                instruction=edge_names.get(edge_index, "Continue"),
                distance_m=selected_edge.length,
                duration_s=selected_edge.weight,
            )
        )

    return selected_edge_ids, front_steps


def build_osmnx_snapshot_and_path(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    routing_profile: str,
    friction_by_tile: Optional[Dict[str, float]] = None,
    realtime_factor: float = 1.0,
    predictive_factor: float = 1.0,
    margin_m: float = 1500.0,
) -> Optional[dict]:
    try:
        import networkx as nx
        import osmnx as ox
    except ImportError:
        logger.debug("networkx/osmnx not installed; skipping osmnx routing path")
        return None

    friction_by_tile = friction_by_tile or {}

    network_type = PROFILE_TO_NETWORK_TYPE.get(routing_profile.lower(), "drive")
    north, south, east, west = _compute_bbox(
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        margin_m=margin_m,
    )

    try:
        graph = ox.graph_from_bbox(
            north=north,
            south=south,
            east=east,
            west=west,
            network_type=network_type,
            simplify=True,
        )
    except Exception:
        logger.warning(
            "osmnx graph_from_bbox failed for bbox=(%s,%s,%s,%s) network_type=%s; "
            "falling back to OSRM",
            north, south, east, west, network_type, exc_info=True,
        )
        return None

    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        logger.warning(
            "osmnx returned an empty graph for bbox=(%s,%s,%s,%s) network_type=%s; "
            "falling back to OSRM",
            north, south, east, west, network_type,
        )
        return None

    try:
        start_node = ox.distance.nearest_nodes(graph, X=start_lon, Y=start_lat)
        end_node = ox.distance.nearest_nodes(graph, X=end_lon, Y=end_lat)
    except Exception:
        logger.warning("osmnx nearest_nodes failed; falling back to OSRM", exc_info=True)
        return None

    layer = 2 if routing_profile.lower() in {"transit", "multimodal"} else 1
    nodes, _, _ = _build_nodes(graph, layer)
    edges, edge_index_map, edge_names = _build_edges(
        graph=graph,
        routing_profile=routing_profile,
        layer=layer,
        friction_by_tile=friction_by_tile,
        realtime_factor=realtime_factor,
        predictive_factor=predictive_factor,
        ox=ox,
    )

    try:
        shortest_path_nodes = nx.shortest_path(
            graph,
            source=start_node,
            target=end_node,
            weight="_effective_weight",
        )
    except Exception:
        logger.warning(
            "no path between osm nodes %s and %s; falling back to OSRM",
            start_node, end_node, exc_info=True,
        )
        return None

    selected_edge_ids, front_steps = _extract_selected_path(
        graph=graph,
        shortest_path_nodes=shortest_path_nodes,
        edge_index_map=edge_index_map,
        edge_names=edge_names,
        edges=edges,
    )

    if not selected_edge_ids:
        logger.warning(
            "shortest path between %s and %s produced no usable edges; falling back to OSRM",
            start_node, end_node,
        )
        return None

    geometry_coordinates = [
        [float(graph.nodes[node_id]["x"]), float(graph.nodes[node_id]["y"])]
        for node_id in shortest_path_nodes
    ]

    return {
        "snapshot": RoutingGraphSnapshot(nodes=nodes, edges=edges),
        "selected_edge_ids": selected_edge_ids,
        "geometry_coordinates": geometry_coordinates,
        "front_steps": front_steps,
        "start_node_id": f"osm:{start_node}",
        "end_node_id": f"osm:{end_node}",
    }
