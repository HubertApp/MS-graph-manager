import logging
from typing import List

from app.models.itinerary import EdgeDTO, RoutingGraphSnapshot
from app.services.transit_graph_client import TransitLayer

logger = logging.getLogger(__name__)

WALK_SPEED_MPS = 1.4
LAYER_CONNECTION = 3
STOP_PREFIX = "gtfs:stop:"


def merge_transit_layer(
    road_snapshot: RoutingGraphSnapshot,
    road_graph,
    transit: TransitLayer,
    access_radius_m: float,
    ox,
) -> RoutingGraphSnapshot:
    """Colle la couche transit sur le graphe de voirie.

    La couche transit couvre tout le reseau ; on ne garde que ce qui tombe dans
    l'emprise du graphe routier, sinon le message gRPC explose.
    """
    if not road_snapshot.nodes or not transit.nodes:
        return road_snapshot

    north = max(node.lat for node in road_snapshot.nodes)
    south = min(node.lat for node in road_snapshot.nodes)
    east = max(node.lon for node in road_snapshot.nodes)
    west = min(node.lon for node in road_snapshot.nodes)

    kept_ids = {
        node.id
        for node in transit.nodes
        if south <= node.lat <= north and west <= node.lon <= east
    }
    if not kept_ids:
        logger.info("aucun arret dans l'emprise du graphe routier")
        return road_snapshot

    stops = [
        node for node in transit.nodes
        if node.id in kept_ids and node.id.startswith(STOP_PREFIX)
    ]
    if not stops:
        return road_snapshot

    road_node_ids = {node.id for node in road_snapshot.nodes}

    nearest_ids, distances = ox.distance.nearest_nodes(
        road_graph,
        X=[stop.lon for stop in stops],
        Y=[stop.lat for stop in stops],
        return_dist=True,
    )

    connection_edges: List[EdgeDTO] = []
    connected = 0

    for stop, osm_node, distance in zip(stops, nearest_ids, distances):
        if distance > access_radius_m:
            continue
        road_id = f"osm:{osm_node}"
        if road_id not in road_node_ids:
            continue

        cost = distance / WALK_SPEED_MPS
        connected += 1
        connection_edges.append(EdgeDTO(
            edge_id=f"access:{road_id}->{stop.id}",
            source_id=road_id, target_id=stop.id,
            weight=cost, length=distance,
            layer=LAYER_CONNECTION, name="Marche vers l'arret",
        ))
        connection_edges.append(EdgeDTO(
            edge_id=f"egress:{stop.id}->{road_id}",
            source_id=stop.id, target_id=road_id,
            weight=cost, length=distance,
            layer=LAYER_CONNECTION, name="Marche depuis l'arret",
        ))

    if not connected:
        logger.info("aucun arret raccordable a moins de %s m", access_radius_m)
        return road_snapshot

    transit_nodes = [node for node in transit.nodes if node.id in kept_ids]
    transit_edges = [
        edge for edge in transit.edges
        if edge.source_id in kept_ids and edge.target_id in kept_ids
    ]

    logger.info(
        "fusion : %d arrets raccordes, +%d noeuds, +%d aretes transit, "
        "+%d aretes de liaison",
        connected, len(transit_nodes), len(transit_edges), len(connection_edges),
    )

    return RoutingGraphSnapshot(
        nodes=road_snapshot.nodes + transit_nodes,
        edges=road_snapshot.edges + transit_edges + connection_edges,
    )
