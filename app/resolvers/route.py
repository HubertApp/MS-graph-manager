import asyncio
import logging
import time
from typing import List, Optional, Tuple

import strawberry

from app.config import OSMNX_GRAPH_MARGIN_M, TRANSIT_ACCESS_RADIUS_M
from app.models.itinerary import (
    FrictionUpdateDTO,
    FrontStepDTO,
    ItineraryResultDTO,
    RouteRequestDTO,
    TrafficInfoDTO,
)
from app.models.route import Geometry, Route, Step
from app.services.cpp_router_client import solve_with_cpp
from app.services.geojson import rendered_path_to_geojson
from app.services.graph_engine import (
    RenderedPath,
    build_multilayer_snapshot,
    build_path_segments,
    render_path,
)
from app.services.instructions import PROFILE_MODE
from app.services.itinerary_tracking import notify_tracking_service
from app.services.layer_merge import merge_transit_layer
from app.services.osmnx_graph import OsmRoutingGraph, build_osmnx_graph
from app.services.osrm_client import fetch_route, route_polyline
from app.services.routing_graph import LazyGraphSnapshot, RoutingGraph, SnapshotGraph
from app.services.traffic_client import get_predictive_factor, get_realtime_factor
from app.services.transit_graph_client import get_transit_layer, list_networks

logger = logging.getLogger(__name__)

TRANSIT_PROFILES = {"transit", "multimodal"}

# Les notifications de suivi partent en tache de fond : la reponse au front
# n'attend plus le service de tracking.
_background_tasks: set = set()


def _in_background(coroutine) -> None:
    task = asyncio.get_running_loop().create_task(coroutine)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _attach_transit(graph: RoutingGraph, request: RouteRequestDTO) -> RoutingGraph:
    """Fusionne les couches GTFS des reseaux qui recouvrent la zone."""
    if request.routing_profile.lower() not in TRANSIT_PROFILES:
        return graph
    if not isinstance(graph, OsmRoutingGraph):
        return graph

    bounds = graph.bounds()
    for network in await list_networks():
        if not network.intersects(bounds):
            continue
        layer = await get_transit_layer(network.network_id, request.departure_time)
        if layer is None:
            continue
        graph = await asyncio.to_thread(
            merge_transit_layer, graph, layer, TRANSIT_ACCESS_RADIUS_M
        )
    return graph


async def _build_graph(
    request: RouteRequestDTO,
    friction_map: dict,
    realtime_factor: float,
    predictive_factor: float,
) -> Optional[Tuple[RoutingGraph, str, str]]:
    """Construit le graphe pondere : OSMnx (cache regional) en priorite, OSRM en repli.

    Returns:
        (graphe, start_node_id, end_node_id) ou None si aucune source
        de donnees n'est disponible. Aucun chemin n'est calcule ici.
    """
    # Bloquant (eventuel telechargement d'une region, calculs numpy) : on le
    # sort de la boucle d'evenements pour ne pas figer les autres requetes.
    osmnx_result = await asyncio.to_thread(
        build_osmnx_graph,
        start_lat=request.start_point.lat,
        start_lon=request.start_point.lon,
        end_lat=request.end_point.lat,
        end_lon=request.end_point.lon,
        routing_profile=request.routing_profile,
        friction_by_tile=friction_map,
        realtime_factor=realtime_factor,
        predictive_factor=predictive_factor,
        margin_m=OSMNX_GRAPH_MARGIN_M,
    )
    if osmnx_result:
        graph = await _attach_transit(osmnx_result.graph, request)
        return graph, osmnx_result.start_node_id, osmnx_result.end_node_id

    # ── Repli : geometrie OSRM transformee en chaine lineaire ponderee ──────
    if request.routing_profile.lower() != "driving":
        logger.warning(
            "osmnx indisponible : repli sur la geometrie OSRM 'driving' pour une "
            "demande '%s' ; seules les durees sont ajustees au profil",
            request.routing_profile,
        )

    raw_route = await fetch_route(
        request.start_point.lat,
        request.start_point.lon,
        request.end_point.lat,
        request.end_point.lon,
    )
    if not raw_route:
        return None

    coordinates, names = route_polyline(raw_route)
    snapshot = build_multilayer_snapshot(
        coordinates=coordinates,
        names=names,
        routing_profile=request.routing_profile,
        friction_by_tile=friction_map,
        realtime_factor=realtime_factor,
        predictive_factor=predictive_factor,
    )
    if not snapshot.nodes:
        return None

    return SnapshotGraph(snapshot), snapshot.nodes[0].id, snapshot.nodes[-1].id


def _already_there(graph: RoutingGraph, node_id: str, profile: str) -> Optional[RenderedPath]:
    """Depart et arrivee accroches au meme noeud : trajet vide mais valide."""
    coordinate = graph.node_coordinate(node_id)
    if coordinate is None:
        return None
    point = [coordinate[0], coordinate[1]]
    return RenderedPath(
        selected_edges=[],
        geometry_coordinates=[point, list(point)],
        front_steps=[
            FrontStepDTO(
                instruction="Vous êtes arrivé à destination",
                distance_m=0.0,
                duration_s=0.0,
                maneuver="arrive",
                mode=PROFILE_MODE.get(profile.lower(), "driving"),
                location=point,
                geometry_start=0,
                geometry_end=1,
            )
        ],
        distance_m=0.0,
        duration_s=0.0,
    )


@strawberry.type
class RouteQuery:

    # Renommé depuis "route" : collision de composition fédérée avec
    # Query.route(id) de service-aom-agregator (une ligne de transport GTFS,
    # concept différent d'un calcul d'itinéraire point à point).
    @strawberry.field(name="computeRoute")
    async def route(
        self, from_lat: float, from_lon: float, to_lat: float, to_lon: float
    ) -> Optional[Route]:
        raw_route = await fetch_route(from_lat, from_lon, to_lat, to_lon)
        if not raw_route:
            return None

        leg = raw_route["legs"][0]
        return Route(
            distance_m=raw_route["distance"],
            duration_s=raw_route["duration"],
            geometry=Geometry(
                type=raw_route["geometry"]["type"],
                coordinates=raw_route["geometry"]["coordinates"],
            ),
            steps=[
                Step(name=s.get("name"), distance=s["distance"], duration=s["duration"])
                for s in leg["steps"]
            ],
        )

    @strawberry.field(name="infoTrafic")
    async def info_trafic(
        self, from_lat: float, from_lon: float, to_lat: float, to_lon: float
    ) -> TrafficInfoDTO:
        realtime_factor, predictive_factor = await asyncio.gather(
            get_realtime_factor(from_lat, from_lon, to_lat, to_lon),
            get_predictive_factor(from_lat, from_lon, to_lat, to_lon),
        )
        return TrafficInfoDTO(
            realtime_factor=realtime_factor,
            predictive_factor=predictive_factor,
            source="traffic+predictive",
        )

    @strawberry.field(name="getItineraireFromTo")
    async def get_itineraire_from_to(
        self,
        request: RouteRequestDTO,
        friction_updates: Optional[List[FrictionUpdateDTO]] = None,
    ) -> Optional[ItineraryResultDTO]:
        started = time.perf_counter()
        coordinates = (
            request.start_point.lat,
            request.start_point.lon,
            request.end_point.lat,
            request.end_point.lon,
        )
        # Les deux facteurs sont independants : on les demande en parallele.
        realtime_factor, predictive_factor = await asyncio.gather(
            get_realtime_factor(*coordinates),
            get_predictive_factor(*coordinates),
        )
        friction_map = {
            update.tile_id: update.friction_coefficient
            for update in (friction_updates or [])
        }

        graph_result = await _build_graph(
            request, friction_map, realtime_factor, predictive_factor
        )
        if graph_result is None:
            logger.error("impossible de construire un graphe pour cette demande")
            return None

        graph, start_node_id, end_node_id = graph_result
        built = time.perf_counter()

        routing = await solve_with_cpp(graph, start_node_id, end_node_id)
        if routing is None:
            logger.error(
                "le routeur C++ n'a pas renvoye de chemin (%s -> %s)",
                start_node_id,
                end_node_id,
            )
            return None
        solved = time.perf_counter()

        if len(routing.node_path) == 1:
            rendered = _already_there(graph, routing.node_path[0], request.routing_profile)
        else:
            rendered = render_path(graph, routing.node_path, request.routing_profile)
        if rendered is None:
            logger.error("le chemin renvoye par le C++ n'est pas reconstituable")
            return None

        itinerary = ItineraryResultDTO(
            distance_m=rendered.distance_m,
            duration_s=rendered.duration_s,
            geometry=Geometry(
                type="LineString",
                coordinates=rendered.geometry_coordinates,
            ),
            steps=rendered.front_steps,
            segments=build_path_segments(
                rendered.selected_edges,
                request.departure_time,
                request.routing_profile,
            ),
            # Materialise seulement si le client demande graphSnapshot.
            graph_snapshot=LazyGraphSnapshot(graph),
            traffic=TrafficInfoDTO(
                realtime_factor=realtime_factor,
                predictive_factor=predictive_factor,
                source="traffic+predictive",
            ),
            geojson=rendered_path_to_geojson(rendered, request.routing_profile),
        )

        logger.info(
            "itineraire %s : %.0f m, %.0f s, %d etapes | graphe %.0f ms, "
            "routeur %.0f ms, rendu %.0f ms",
            request.routing_profile, itinerary.distance_m, itinerary.duration_s,
            len(itinerary.steps), (built - started) * 1000, (solved - built) * 1000,
            (time.perf_counter() - solved) * 1000,
        )
        _in_background(notify_tracking_service(request, itinerary))
        return itinerary
