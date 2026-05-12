import strawberry
from typing import List, Optional

from app.models.itinerary import (
    FrictionUpdateDTO,
    FrontStepDTO,
    ItineraryResultDTO,
    RouteRequestDTO,
    TrafficInfoDTO,
)
from app.services.osrm_client import fetch_route
from app.services.cpp_router_client import compute_itinerary_with_cpp
from app.services.graph_engine import build_multilayer_snapshot, build_path_segments
from app.services.itinerary_tracking import notify_tracking_service
from app.services.osmnx_graph import build_osmnx_snapshot_and_path
from app.services.traffic_client import get_predictive_factor, get_realtime_factor
from app.models.route import Route, Geometry, Step
from app.config import OSMNX_GRAPH_MARGIN_M


@strawberry.type
class RouteQuery:

    @strawberry.field
    async def route(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float
    ) -> Optional[Route]:

        raw_route = await fetch_route(from_lat, from_lon, to_lat, to_lon)
        if not raw_route:
            return None

        leg = raw_route["legs"][0]

        steps = [
            Step(
                name=s.get("name"),
                distance=s["distance"],
                duration=s["duration"]
            )
            for s in leg["steps"]
        ]

        return Route(
            distance_m=raw_route["distance"],
            duration_s=raw_route["duration"],
            geometry=Geometry(
                type=raw_route["geometry"]["type"],
                coordinates=raw_route["geometry"]["coordinates"]
            ),
            steps=steps
        )

    @strawberry.field(name="infoTrafic")
    async def info_trafic(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
    ) -> TrafficInfoDTO:
        realtime_factor = await get_realtime_factor(from_lat, from_lon, to_lat, to_lon)
        predictive_factor = await get_predictive_factor(from_lat, from_lon, to_lat, to_lon)

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
            update.tile_id: update.friction_coefficient for update in (friction_updates or [])
        }

        osmnx_result = build_osmnx_snapshot_and_path(
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
            graph_snapshot = osmnx_result["snapshot"]
            default_edge_ids = osmnx_result["selected_edge_ids"]
            selected_edge_ids = await compute_itinerary_with_cpp(
                graph_snapshot,
                request,
                default_edge_ids=default_edge_ids,
            )

            selected_edges = [graph_snapshot.edges[edge_id] for edge_id in selected_edge_ids]
            segments = build_path_segments(selected_edges, request.departure_time, request.routing_profile)
            distance_m = sum(edge.length for edge in selected_edges)
            duration_s = sum(edge.weight for edge in selected_edges)
            geometry_type = "LineString"
            geometry_coordinates = osmnx_result["geometry_coordinates"]
            front_steps = osmnx_result["front_steps"]
        else:
            raw_route = await fetch_route(
                request.start_point.lat,
                request.start_point.lon,
                request.end_point.lat,
                request.end_point.lon,
            ) 
            if not raw_route:
                return None

            coordinates = raw_route["geometry"]["coordinates"]
            graph_snapshot = build_multilayer_snapshot(
                coordinates=coordinates,
                routing_profile=request.routing_profile,
                friction_by_tile=friction_map,
                realtime_factor=realtime_factor,
                predictive_factor=predictive_factor,
            )

            default_edge_ids = list(range(len(graph_snapshot.edges)))
            selected_edge_ids = await compute_itinerary_with_cpp(
                graph_snapshot,
                request,
                default_edge_ids=default_edge_ids,
            )

            selected_edges = [graph_snapshot.edges[edge_id] for edge_id in selected_edge_ids]
            segments = build_path_segments(selected_edges, request.departure_time, request.routing_profile)
            distance_m = sum(edge.length for edge in selected_edges)
            duration_s = sum(edge.weight for edge in selected_edges)
            geometry_type = raw_route["geometry"]["type"]
            geometry_coordinates = coordinates
            front_steps = [
                FrontStepDTO(
                    instruction=step.get("name") or "Continue",
                    distance_m=step.get("distance", 0.0),
                    duration_s=step.get("duration", 0.0),
                )
                for step in raw_route["legs"][0]["steps"]
            ]

        itinerary = ItineraryResultDTO(
            distance_m=distance_m,
            duration_s=duration_s,
            geometry=Geometry(
                type=geometry_type,
                coordinates=geometry_coordinates,
            ),
            steps=front_steps,
            segments=segments,
            graph_snapshot=graph_snapshot,
            traffic=TrafficInfoDTO(
                realtime_factor=realtime_factor,
                predictive_factor=predictive_factor,
                source="traffic+predictive",
            ),
        )

        await notify_tracking_service(request, itinerary)
        return itinerary
