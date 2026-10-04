"""Fusion de la couche transport en commun (GTFS) sur le graphe de voirie.

La couche transit d'un reseau couvre toute l'agglomeration ; on n'en garde que
ce qui tombe dans l'emprise du sous-graphe routier de la demande, sinon le
message gRPC enfle inutilement. Chaque arret retenu est relie au noeud de
voirie le plus proche (dans la composante principale) par deux aretes a pied :
`access` (voirie -> arret) et `egress` (arret -> voirie).
"""

import logging
from typing import List

from app.services.osmnx_graph import OsmRoutingGraph, expand_bbox
from app.services.routing_graph import LAYER_CONNECTION, GraphEdge
from app.services.transit_graph_client import TransitLayer

logger = logging.getLogger(__name__)

WALK_SPEED_MPS = 1.4
STOP_PREFIX = "gtfs:stop:"


def merge_transit_layer(
    graph: OsmRoutingGraph,
    transit: TransitLayer,
    access_radius_m: float,
) -> OsmRoutingGraph:
    if graph.is_empty() or not transit.nodes:
        return graph

    # Emprise de la voirie elargie du rayon d'acces : un arret juste au-dela
    # du dernier carrefour reste raccordable.
    west, south, east, north = expand_bbox(graph.bounds(), access_radius_m)
    kept_nodes = [
        node for node in transit.nodes
        if south <= node.lat <= north and west <= node.lon <= east
    ]
    if not kept_nodes:
        logger.info("aucun arret de %s dans l'emprise du graphe routier", transit.network_id)
        return graph

    stops = [node for node in kept_nodes if node.id.startswith(STOP_PREFIX)]
    if not stops:
        return graph

    region = graph.road.region
    nearest, distances = graph.road.nearest_many(
        [stop.lat for stop in stops], [stop.lon for stop in stops]
    )
    if nearest.size == 0:
        return graph

    connections: List[GraphEdge] = []
    for stop, road_index, distance in zip(stops, nearest.tolist(), distances.tolist()):
        if distance > access_radius_m:
            continue
        road_id = region.node_ids[road_index]
        # distance "a vol d'oiseau" : on ne descend jamais sous 1 m pour que
        # le temps de marche reste strictement positif.
        length = float(max(distance, 1.0))
        cost = length / WALK_SPEED_MPS
        label = stop.name or "l'arret"
        connections.append(GraphEdge(
            edge_id=f"access:{road_id}->{stop.id}",
            source_id=road_id, target_id=stop.id,
            weight=cost, length=length,
            layer=LAYER_CONNECTION, name=f"Marche vers {label}",
        ))
        connections.append(GraphEdge(
            edge_id=f"egress:{stop.id}->{road_id}",
            source_id=stop.id, target_id=road_id,
            weight=cost, length=length,
            layer=LAYER_CONNECTION, name=f"Marche depuis {label}",
        ))

    if not connections:
        logger.info(
            "aucun arret de %s raccordable a moins de %s m", transit.network_id, access_radius_m
        )
        return graph

    kept_ids = {node.id for node in kept_nodes}
    transit_edges = [
        edge for edge in transit.edges
        if edge.source_id in kept_ids and edge.target_id in kept_ids
    ]

    logger.info(
        "fusion %s : %d arrets raccordes, +%d noeuds, +%d aretes transit, "
        "+%d aretes de liaison",
        transit.network_id, len(connections) // 2, len(kept_nodes),
        len(transit_edges), len(connections),
    )
    return graph.with_extras(kept_nodes, transit_edges + connections)
