"""Unit tests for app/services/transit_graph_client.py."""
from datetime import datetime, timezone

import httpx
import respx

from app.services import transit_graph_client


def _make_response_body(hour_bucket: int = 8):
    return {
        "data": {
            "transitGraph": {
                "networkId": "metz",
                "hourBucket": hour_bucket,
                "nodes": [
                    {"id": "gtfs:stop:metz:gare", "lat": 49.1193, "lon": 6.1757, "isTransitStop": True},
                    {"id": "gtfs:ride:metz:C1|0|metz:gare", "lat": 49.1193, "lon": 6.1757, "isTransitStop": True},
                ],
                "edges": [
                    {
                        "edgeId": "ride:metz:C1|0|metz:gare->metz:mairie",
                        "sourceId": "gtfs:ride:metz:C1|0|metz:gare",
                        "targetId": "gtfs:ride:metz:C1|0|metz:mairie",
                        "weight": 330.0,
                        "length": 800.0,
                        "layer": 2,
                        "transitLineId": "metz:C1",
                        "name": "Ligne C1",
                    }
                ],
            }
        }
    }


async def test_get_transit_layer_returns_none_when_url_empty(monkeypatch):
    monkeypatch.setattr("app.services.transit_graph_client.AOM_GRAPHQL_URL", "")

    layer = await transit_graph_client.get_transit_layer(
        "metz", datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc)
    )

    assert layer is None


@respx.mock
async def test_get_transit_layer_success(monkeypatch):
    monkeypatch.setattr(
        "app.services.transit_graph_client.AOM_GRAPHQL_URL", "http://fake-aom.test/graphql"
    )
    transit_graph_client._cache.clear()
    respx.post("http://fake-aom.test/graphql").mock(
        return_value=httpx.Response(200, json=_make_response_body())
    )

    layer = await transit_graph_client.get_transit_layer(
        "metz", datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc)
    )

    assert layer is not None
    assert len(layer.nodes) == 2
    assert layer.edges[0].transit_line_id == "metz:C1"
    assert layer.edges[0].weight == 330.0


@respx.mock
async def test_get_transit_layer_caches_by_network_and_hour(monkeypatch):
    monkeypatch.setattr(
        "app.services.transit_graph_client.AOM_GRAPHQL_URL", "http://fake-aom.test/graphql"
    )
    transit_graph_client._cache.clear()
    route = respx.post("http://fake-aom.test/graphql").mock(
        return_value=httpx.Response(200, json=_make_response_body())
    )

    moment = datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc)
    await transit_graph_client.get_transit_layer("metz", moment)
    await transit_graph_client.get_transit_layer("metz", moment)

    assert route.call_count == 1


@respx.mock
async def test_invalidate_clears_only_the_targeted_network(monkeypatch):
    monkeypatch.setattr(
        "app.services.transit_graph_client.AOM_GRAPHQL_URL", "http://fake-aom.test/graphql"
    )
    transit_graph_client._cache.clear()
    respx.post("http://fake-aom.test/graphql").mock(
        return_value=httpx.Response(200, json=_make_response_body())
    )

    moment = datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc)
    await transit_graph_client.get_transit_layer("metz", moment)
    await transit_graph_client.get_transit_layer("nancy", moment)
    assert ("metz", 8) in transit_graph_client._cache
    assert ("nancy", 8) in transit_graph_client._cache

    transit_graph_client.invalidate("metz")

    assert ("metz", 8) not in transit_graph_client._cache
    assert ("nancy", 8) in transit_graph_client._cache


@respx.mock
async def test_get_transit_layer_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.transit_graph_client.AOM_GRAPHQL_URL", "http://fake-aom.test/graphql"
    )
    transit_graph_client._cache.clear()
    respx.post("http://fake-aom.test/graphql").mock(side_effect=httpx.ConnectError("boom"))

    layer = await transit_graph_client.get_transit_layer(
        "metz", datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc)
    )

    assert layer is None


@respx.mock
async def test_get_transit_layer_returns_none_on_graphql_errors(monkeypatch):
    monkeypatch.setattr(
        "app.services.transit_graph_client.AOM_GRAPHQL_URL", "http://fake-aom.test/graphql"
    )
    transit_graph_client._cache.clear()
    respx.post("http://fake-aom.test/graphql").mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "boom"}]})
    )

    layer = await transit_graph_client.get_transit_layer(
        "metz", datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc)
    )

    assert layer is None
