import logging
from typing import List, Optional, Tuple

import strawberry

from app.config import OSMNX_GRAPH_MARGIN_M
from app.models.itinerary import (
    FrictionUpdateDTO,
    ItineraryResultDTO,
    RouteRequestDTO,
    RoutingGraphSnapshot,
    TrafficInfoDTO,
)
from app.models.route import Geometry, Route, Step
from app.services.cpp_router_client import solve_with_cpp
from app.services.graph_engine import (
    build_multilayer_snapshot,
    build_path_segments,
    render_path,
)
from app.services.itinerary_tracking import notify_tracking_service
from app.services.osmnx_graph import build_osmnx_snapshot
from app.services.osrm_client import fetch_route
from app.services.traffic_client import get_predictive_factor, get_realtime_factor

logger = logging.getLogger(__name__)


async def _build_graph(
    request: RouteRequestDTO,
    friction_map: dict,
    realtime_factor: float,
    predictive_factor: float,
) -> Optional[Tuple[RoutingGraphSnapshot, str, str]]:
    """Construit le graphe pondere : OSMnx en priorite, OSRM en repli.

    Returns:
        (snapshot, start_node_id, end_node_id) ou None si aucune source
        de donnees n'est disponible. Aucun chemin n'est calcule ici.
    """
    osmnx_result = build_osmnx_snapshot(
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
        return (
            osmnx_result["snapshot"],
            osmnx_result["start_node_id"],
            osmnx_result["end_node_id"],
        )

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

    snapshot = build_multilayer_snapshot(
        coordinates=raw_route["geometry"]["coordinates"],
        routing_profile=request.routing_profile,
        friction_by_tile=friction_map,
        realtime_factor=realtime_factor,
        predictive_factor=predictive_factor,
    )
    if not snapshot.nodes:
        return None

    return snapshot, snapshot.nodes[0].id, snapshot.nodes[-1].id


@strawberry.type
class RouteQuery:

    @strawberry.field
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
        return TrafficInfoDTO(
            realtime_factor=await get_realtime_factor(
                from_lat, from_lon, to_lat, to_lon
            ),
            predictive_factor=await get_predictive_factor(
                from_lat, from_lon, to_lat, to_lon
            ),
            source="traffic+predictive",
        )

    @strawberry.field(name="getItineraireFromTo")
    async def get_itineraire_from_to(
        self,
        request: RouteRequestDTO,
        friction_updates: Optional[List[FrictionUpdateDTO]] = None,
    ) -> Optional[ItineraryResultDTO]:

        realtime_factor = await get_realtime_factor(
            request.start_point.lat,
            request.start_point.lon,
            request.end_point.lat,
            request.end_point.lon,
        )
        predictive_factor = await get_predictive_factor(
            request.start_point.lat,
            request.start_point.lon,
            request.end_point.lat,
            request.end_point.lon,
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

        snapshot, start_node_id, end_node_id = graph_result

        routing = await solve_with_cpp(snapshot, start_node_id, end_node_id)
        if routing is None:
            logger.error(
                "le routeur C++ n'a pas renvoye de chemin (%s -> %s)",
                start_node_id,
                end_node_id,
            )
            return None

        rendered = render_path(snapshot, routing.node_path)
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
            graph_snapshot=snapshot,
            traffic=TrafficInfoDTO(
                realtime_factor=realtime_factor,
                predictive_factor=predictive_factor,
                source="traffic+predictive",
            ),
        )

        await notify_tracking_service(request, itinerary)
        return itinerary