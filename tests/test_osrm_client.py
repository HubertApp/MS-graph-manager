"""Unit tests for app/services/osrm_client.py."""
import httpx
import respx

from app.services import osrm_client


@respx.mock
async def test_fetch_route_success(monkeypatch):
    monkeypatch.setattr("app.services.osrm_client.OSRM_BASE_URL", "http://fake-osrm.test")
    route = respx.get(url__startswith="http://fake-osrm.test/route/v1/driving/").mock(
        return_value=httpx.Response(
            200,
            json={
                "routes": [
                    {
                        "distance": 500.0,
                        "duration": 120.0,
                        "geometry": {"type": "LineString", "coordinates": [[6.0, 49.0], [6.01, 49.01]]},
                        "legs": [{"steps": [{"name": "Rue Test", "distance": 500.0, "duration": 120.0}]}],
                    }
                ]
            },
        )
    )

    result = await osrm_client.fetch_route(49.0, 6.0, 49.01, 6.01)

    assert route.called
    assert result["distance"] == 500.0


@respx.mock
async def test_fetch_route_non_200_returns_none(monkeypatch):
    monkeypatch.setattr("app.services.osrm_client.OSRM_BASE_URL", "http://fake-osrm.test")
    respx.get(url__startswith="http://fake-osrm.test/route/v1/driving/").mock(
        return_value=httpx.Response(500)
    )

    result = await osrm_client.fetch_route(49.0, 6.0, 49.01, 6.01)

    assert result is None


@respx.mock
async def test_fetch_route_empty_routes_returns_none(monkeypatch):
    monkeypatch.setattr("app.services.osrm_client.OSRM_BASE_URL", "http://fake-osrm.test")
    respx.get(url__startswith="http://fake-osrm.test/route/v1/driving/").mock(
        return_value=httpx.Response(200, json={"routes": []})
    )

    result = await osrm_client.fetch_route(49.0, 6.0, 49.01, 6.01)

    assert result is None


@respx.mock
async def test_fetch_route_connection_error_returns_none(monkeypatch):
    """Regression: a ConnectError used to propagate unhandled and crash the
    whole GraphQL query instead of failing gracefully."""
    monkeypatch.setattr("app.services.osrm_client.OSRM_BASE_URL", "http://fake-osrm.test")
    respx.get(url__startswith="http://fake-osrm.test/route/v1/driving/").mock(
        side_effect=httpx.ConnectError("boom")
    )

    result = await osrm_client.fetch_route(49.0, 6.0, 49.01, 6.01)

    assert result is None
