"""End-to-end test against the real Overpass API. Excluded from the default
pytest run (see pytest.ini) — run explicitly with `pytest -m integration`.
Requires network access.

Since the osmnx/gRPC refactor, path-solving is delegated entirely to the C++
router (no more local Python fallback) — see app/services/cpp_router_client.py
and tests/test_route_resolver.py::test_renvoie_null_si_routeur_indisponible.
Without a reachable router (CPP_ROUTER_GRPC_TARGET, "envoy:50051" by default),
getItineraireFromTo now returns a clean null rather than a populated graph, so
that's what this test verifies here. If you have MS-itinerary-creator running
and reachable, you can extend this test to assert on the actual graph/path.
"""
import httpx
import pytest

from app.main import app

pytestmark = pytest.mark.integration

QUERY = """
query RouteMulticouche($request: RouteRequestDTO!) {
  getItineraireFromTo(request: $request) {
    graphSnapshot {
      nodes { id lat lon }
      edges { edgeId sourceId targetId weight }
    }
  }
}
"""


async def test_get_itineraire_handles_real_osmnx_data_without_crashing():
    variables = {
        "request": {
            "startPoint": {"lat": 49.1193, "lon": 6.1757},
            "endPoint": {"lat": 49.1180, "lon": 6.1770},
            "departureTime": "2026-01-01T12:00:00Z",
            "routingProfile": "driving",
        }
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
        response = await client.post("/graphql", json={"query": QUERY, "variables": variables})

    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    # No C++ router reachable in this environment -> graceful null, not a crash.
    assert body["data"]["getItineraireFromTo"] is None
