"""Unit tests for app/resolvers/route.py::_attach_transit."""
from datetime import datetime, timezone

from app.models.itinerary import CoordinateInput, RouteRequestDTO, RoutingGraphSnapshot
from app.resolvers.route import _attach_transit


def _request(routing_profile: str) -> RouteRequestDTO:
    return RouteRequestDTO(
        start_point=CoordinateInput(lat=49.0, lon=6.0),
        end_point=CoordinateInput(lat=49.01, lon=6.01),
        departure_time=datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc),
        routing_profile=routing_profile,
    )


async def test_ignores_non_transit_profiles(monkeypatch):
    monkeypatch.setattr("app.resolvers.route.TRANSIT_NETWORK_IDS", ["metz"])
    snapshot = RoutingGraphSnapshot(nodes=[], edges=[])

    result = await _attach_transit(snapshot, road_graph=None, request=_request("driving"))

    assert result is snapshot


async def test_noop_when_no_network_ids_configured(monkeypatch):
    monkeypatch.setattr("app.resolvers.route.TRANSIT_NETWORK_IDS", [])
    snapshot = RoutingGraphSnapshot(nodes=[], edges=[])

    result = await _attach_transit(snapshot, road_graph=None, request=_request("transit"))

    assert result is snapshot


async def test_skips_a_network_when_transit_layer_unavailable(monkeypatch):
    monkeypatch.setattr("app.resolvers.route.TRANSIT_NETWORK_IDS", ["metz"])

    async def fake_get_transit_layer(network_id, departure_time):
        return None

    monkeypatch.setattr("app.resolvers.route.get_transit_layer", fake_get_transit_layer)

    snapshot = RoutingGraphSnapshot(nodes=[], edges=[])
    result = await _attach_transit(snapshot, road_graph=None, request=_request("multimodal"))

    assert result is snapshot


async def test_merges_the_transit_layer_when_available(monkeypatch):
    monkeypatch.setattr("app.resolvers.route.TRANSIT_NETWORK_IDS", ["metz"])

    sentinel_layer = object()

    async def fake_get_transit_layer(network_id, departure_time):
        assert network_id == "metz"
        return sentinel_layer

    merged_snapshot = RoutingGraphSnapshot(nodes=[], edges=[])
    merge_calls = []

    def fake_merge_transit_layer(road_snapshot, road_graph, transit, access_radius_m, ox):
        merge_calls.append((road_snapshot, road_graph, transit, access_radius_m))
        return merged_snapshot

    monkeypatch.setattr("app.resolvers.route.get_transit_layer", fake_get_transit_layer)
    monkeypatch.setattr("app.resolvers.route.merge_transit_layer", fake_merge_transit_layer)

    original_snapshot = RoutingGraphSnapshot(nodes=[], edges=[])
    fake_graph = object()
    result = await _attach_transit(
        original_snapshot, road_graph=fake_graph, request=_request("transit")
    )

    assert result is merged_snapshot
    assert len(merge_calls) == 1
    assert merge_calls[0][0] is original_snapshot
    assert merge_calls[0][1] is fake_graph
    assert merge_calls[0][2] is sentinel_layer
