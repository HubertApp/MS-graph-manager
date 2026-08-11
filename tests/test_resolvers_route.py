"""Unit tests for the `route` and `infoTrafic` GraphQL queries in
app/resolvers/route.py.

`getItineraireFromTo` (and the graph-building/`_build_graph` helper) is
covered by tests/test_route_resolver.py instead — this file only covers
the two queries that weren't touched by the osmnx/gRPC refactor.
"""
from app.resolvers.route import RouteQuery


async def test_route_query_happy_path(monkeypatch):
    async def fake_fetch_route(from_lat, from_lon, to_lat, to_lon):
        return {
            "distance": 500.0,
            "duration": 120.0,
            "geometry": {"type": "LineString", "coordinates": [[6.0, 49.0], [6.01, 49.01]]},
            "legs": [{"steps": [{"name": "Rue Test", "distance": 500.0, "duration": 120.0}]}],
        }

    monkeypatch.setattr("app.resolvers.route.fetch_route", fake_fetch_route)

    result = await RouteQuery().route(from_lat=49.0, from_lon=6.0, to_lat=49.01, to_lon=6.01)

    assert result is not None
    assert result.distance_m == 500.0
    assert result.duration_s == 120.0
    assert result.steps[0].name == "Rue Test"


async def test_route_query_returns_none_when_osrm_unavailable(monkeypatch):
    async def fake_fetch_route(*args, **kwargs):
        return None

    monkeypatch.setattr("app.resolvers.route.fetch_route", fake_fetch_route)

    result = await RouteQuery().route(from_lat=49.0, from_lon=6.0, to_lat=49.01, to_lon=6.01)

    assert result is None


async def test_info_trafic_defaults_to_1_0_without_urls():
    result = await RouteQuery().info_trafic(from_lat=49.0, from_lon=6.0, to_lat=49.01, to_lon=6.01)

    assert result.realtime_factor == 1.0
    assert result.predictive_factor == 1.0
    assert result.source == "traffic+predictive"
