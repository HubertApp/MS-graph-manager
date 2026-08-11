"""Unit tests for app/services/itinerary_tracking.py."""
import json
from datetime import datetime, timezone

import httpx
import respx

from app.models.itinerary import (
    CoordinateInput,
    Geometry,
    ItineraryResultDTO,
    PathSegmentDTO,
    RouteRequestDTO,
    RoutingGraphSnapshot,
    TrafficInfoDTO,
)
from app.services import itinerary_tracking


def _make_request() -> RouteRequestDTO:
    return RouteRequestDTO(
        start_point=CoordinateInput(lat=49.0, lon=6.0),
        end_point=CoordinateInput(lat=49.01, lon=6.01),
        departure_time=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        routing_profile="driving",
    )


def _make_itinerary() -> ItineraryResultDTO:
    return ItineraryResultDTO(
        distance_m=500.0,
        duration_s=120.0,
        geometry=Geometry(type="LineString", coordinates=[[6.0, 49.0], [6.01, 49.01]]),
        steps=[],
        segments=[
            PathSegmentDTO(
                type="road",
                osm_ids=[0],
                transit_line_id=None,
                expected_arrival=datetime(2026, 1, 1, 12, 2, 0, tzinfo=timezone.utc),
            )
        ],
        graph_snapshot=RoutingGraphSnapshot(nodes=[], edges=[]),
        traffic=TrafficInfoDTO(realtime_factor=1.0, predictive_factor=1.0, source="traffic+predictive"),
    )


async def test_notify_tracking_service_noop_when_url_empty(monkeypatch):
    monkeypatch.setattr("app.services.itinerary_tracking.TRACKING_SERVICE_URL", "")

    # No respx route registered: if a real call were attempted it would raise.
    await itinerary_tracking.notify_tracking_service(_make_request(), _make_itinerary())


@respx.mock
async def test_notify_tracking_service_posts_expected_payload(monkeypatch):
    monkeypatch.setattr(
        "app.services.itinerary_tracking.TRACKING_SERVICE_URL", "http://fake-tracking.test"
    )
    route = respx.post("http://fake-tracking.test").mock(return_value=httpx.Response(200))

    await itinerary_tracking.notify_tracking_service(_make_request(), _make_itinerary())

    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["distance_m"] == 500.0
    assert body["routing_profile"] == "driving"
    assert body["segments"][0]["type"] == "road"


@respx.mock
async def test_notify_tracking_service_swallows_http_errors(monkeypatch):
    monkeypatch.setattr(
        "app.services.itinerary_tracking.TRACKING_SERVICE_URL", "http://fake-tracking.test"
    )
    respx.post("http://fake-tracking.test").mock(side_effect=httpx.ConnectError("boom"))

    # Must not raise.
    await itinerary_tracking.notify_tracking_service(_make_request(), _make_itinerary())
