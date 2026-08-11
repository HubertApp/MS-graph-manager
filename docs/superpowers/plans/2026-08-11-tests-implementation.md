# Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add unit test coverage (mocked network/gRPC) for every currently-untested service module and GraphQL resolver in ms-graph-manager, plus one integration test that hits the real Overpass API, per `docs/superpowers/specs/2026-08-11-tests-design.md`.

**Architecture:** Two layers separated by a pytest marker — unit tests (default, fully mocked, no external dependency) and a single `@pytest.mark.integration` test (excluded by default, hits real Overpass). Network/HTTP calls are mocked with `respx`; the gRPC stub is mocked by monkeypatching `astar_pb2_grpc.AStarServiceStub`; osmnx/networkx are used for real (they're pure computation / already a hard runtime dependency) with only their two network-touching entry points (`osmnx.graph_from_bbox`, `osmnx.distance.nearest_nodes`) mocked.

**Tech Stack:** pytest, pytest-asyncio (`asyncio_mode = auto`), pytest-cov, respx, unittest.mock (stdlib).

## Global Constraints

- `requirements.txt` stays runtime-only (used by the Dockerfile) — test-only packages go in the new `requirements-dev.txt`.
- Every module under `app/services/` and `app/resolvers/` that makes a network or gRPC call gets its calls mocked in unit tests — no unit test may require network access, a running OSRM, or a running C++ router.
- `pytest` with no arguments must pass with zero network access.
- `pytest -m integration` is the only way to run the live Overpass test.
- All test code targets already-existing, already-correct application code (this session's earlier bug fixes) — no application code changes are expected in this plan. If a test fails against current code, stop and report rather than "fixing" the test to match a latent bug.

**Note on TDD framing:** every module under test already exists and was verified working manually earlier in this session (including live end-to-end testing for osmnx and Docker). This plan is a test *backfill*, not new-feature TDD — there is no meaningful "red" step (the implementation isn't being driven by the test, it already exists and is correct). Each task's steps are therefore: write the test file → run it → confirm it passes for the right reason → commit. Do not weaken or rewrite application code to make a test pass; if a written test fails, the test itself has a bug (fix the test).

---

## File Structure

| File | Responsibility |
|---|---|
| `requirements-dev.txt` | Runtime deps + pytest, pytest-cov, pytest-asyncio, respx |
| `pytest.ini` | `asyncio_mode`, `integration` marker registration, default marker exclusion |
| `Makefile` | `install` prefers `requirements-dev.txt`; `test` drops the now-redundant ad-hoc pip install |
| `README.md` | New "## Tests" section |
| `tests/test_osrm_client.py` | `app/services/osrm_client.py` |
| `tests/test_traffic_client.py` | `app/services/traffic_client.py` |
| `tests/test_itinerary_tracking.py` | `app/services/itinerary_tracking.py` |
| `tests/test_cpp_router_client.py` | `app/services/cpp_router_client.py` |
| `tests/test_osmnx_graph.py` | `app/services/osmnx_graph.py` |
| `tests/test_resolvers_route.py` | `app/resolvers/route.py` |
| `tests/test_integration_live_graph.py` | End-to-end, marked `integration` |

Tasks 2–8 are independent of each other and can be done in any order once Task 1 is complete.

---

### Task 1: Test tooling and configuration

**Files:**
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Modify: `Makefile:6-24`
- Modify: `README.md` (insert before the `## Structure du projet` heading)

**Interfaces:**
- Produces: `asyncio_mode = auto` (all subsequent `async def test_*` functions run without needing `@pytest.mark.asyncio`); the `integration` pytest marker; `pytest -m integration` as the command to run the live test; `requirements-dev.txt` as the file CI/devs install from.

- [ ] **Step 1: Create `requirements-dev.txt`**

```
-r requirements.txt
pytest
pytest-cov
pytest-asyncio
respx
```

- [ ] **Step 2: Create `pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
markers =
    integration: hits real external services (osmnx/Overpass API); excluded by default, run explicitly with `pytest -m integration`
addopts = -m "not integration"
```

- [ ] **Step 3: Update `Makefile`**

Replace the `install` target:

```makefile
install:
	$(PIP) install --upgrade pip
	@if [ -f requirements-dev.txt ]; then \
		$(PIP) install -r requirements-dev.txt; \
	elif [ -f requirements.txt ]; then \
		$(PIP) install -r requirements.txt; \
	else \
		echo "No requirements file found, skipping."; \
	fi
```

Replace the `test` target (drop the ad-hoc pip install, now covered by `requirements-dev.txt`):

```makefile
test:
	@echo "Running tests..."
	pytest --cov=app || \
		( echo "No tests collected or pytest failed; continuing CI (adjust Makefile to change this behavior)" && exit 0 )
```

- [ ] **Step 4: Add a "Tests" section to `README.md`**

Insert this section immediately before the `## Structure du projet` heading:

```markdown
## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Par défaut, `pytest` ne lance que les tests unitaires (tous les appels réseau/gRPC
sont mockés — aucune dépendance externe requise). Le test d'intégration
bout-en-bout (`tests/test_integration_live_graph.py`) appelle la vraie API
Overpass et est exclu du run par défaut ; pour le lancer :

```bash
pytest -m integration
```
```

- [ ] **Step 5: Verify the config is picked up**

Run: `pytest --collect-only`
Expected: exits 0, collects the 2 existing test files (`test_graph_engine.py`, `test_imports.py`), no `PytestUnknownMarkWarning`, output includes `asyncio: mode=Mode.AUTO`.

- [ ] **Step 6: Install dev requirements**

Run: `pip install -r requirements-dev.txt`
Expected: installs `pytest-asyncio` and `respx` (pytest/pytest-cov likely already present) without errors.

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt pytest.ini Makefile README.md
git commit -m "test: add pytest-asyncio/respx tooling and integration marker"
```

---

### Task 2: `tests/test_osrm_client.py`

**Files:**
- Create: `tests/test_osrm_client.py`
- Test target: `app/services/osrm_client.py` (`fetch_route`, `OSRM_BASE_URL`)

**Interfaces:**
- Consumes: `app.services.osrm_client.fetch_route(from_lat, from_lon, to_lat, to_lon) -> Optional[dict]`, module attribute `app.services.osrm_client.OSRM_BASE_URL` (monkeypatchable string).

- [ ] **Step 1: Write the test file**

```python
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
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_osrm_client.py -v`
Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_osrm_client.py
git commit -m "test: cover osrm_client.fetch_route"
```

---

### Task 3: `tests/test_traffic_client.py`

**Files:**
- Create: `tests/test_traffic_client.py`
- Test target: `app/services/traffic_client.py` (`get_realtime_factor`, `get_predictive_factor`, `_get_factor_from_service`)

**Interfaces:**
- Consumes: `app.services.traffic_client.get_realtime_factor(from_lat, from_lon, to_lat, to_lon) -> float`, `get_predictive_factor(...) -> float`, module attributes `TRAFFIC_INFO_URL` / `PREDICTIVE_INFO_URL` (monkeypatchable strings).

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for app/services/traffic_client.py."""
import httpx
import respx

from app.services import traffic_client


async def test_get_realtime_factor_returns_1_0_when_url_empty(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.TRAFFIC_INFO_URL", "")

    factor = await traffic_client.get_realtime_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 1.0


@respx.mock
async def test_get_realtime_factor_success(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.TRAFFIC_INFO_URL", "http://fake-traffic.test")
    respx.post("http://fake-traffic.test").mock(return_value=httpx.Response(200, json={"factor": 1.8}))

    factor = await traffic_client.get_realtime_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 1.8


@respx.mock
async def test_get_predictive_factor_clamps_high_values(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.PREDICTIVE_INFO_URL", "http://fake-predictive.test")
    respx.post("http://fake-predictive.test").mock(return_value=httpx.Response(200, json={"factor": 99.0}))

    factor = await traffic_client.get_predictive_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 3.0


@respx.mock
async def test_get_predictive_factor_clamps_low_values(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.PREDICTIVE_INFO_URL", "http://fake-predictive.test")
    respx.post("http://fake-predictive.test").mock(return_value=httpx.Response(200, json={"factor": 0.001}))

    factor = await traffic_client.get_predictive_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 0.1


@respx.mock
async def test_get_realtime_factor_non_200_returns_1_0(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.TRAFFIC_INFO_URL", "http://fake-traffic.test")
    respx.post("http://fake-traffic.test").mock(return_value=httpx.Response(500))

    factor = await traffic_client.get_realtime_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 1.0


@respx.mock
async def test_get_realtime_factor_malformed_payload_returns_1_0(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.TRAFFIC_INFO_URL", "http://fake-traffic.test")
    respx.post("http://fake-traffic.test").mock(
        return_value=httpx.Response(200, json={"factor": "not-a-number"})
    )

    factor = await traffic_client.get_realtime_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 1.0
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_traffic_client.py -v`
Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_traffic_client.py
git commit -m "test: cover traffic_client factor fetching and clamping"
```

---

### Task 4: `tests/test_itinerary_tracking.py`

**Files:**
- Create: `tests/test_itinerary_tracking.py`
- Test target: `app/services/itinerary_tracking.py` (`notify_tracking_service`)

**Interfaces:**
- Consumes: `app.services.itinerary_tracking.notify_tracking_service(request: RouteRequestDTO, itinerary: ItineraryResultDTO) -> None`, module attribute `TRACKING_SERVICE_URL`; `app.models.itinerary.{CoordinateInput, Geometry, ItineraryResultDTO, PathSegmentDTO, RouteRequestDTO, RoutingGraphSnapshot, TrafficInfoDTO}`.

- [ ] **Step 1: Write the test file**

```python
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
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_itinerary_tracking.py -v`
Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_itinerary_tracking.py
git commit -m "test: cover itinerary_tracking.notify_tracking_service"
```

---

### Task 5: `tests/test_cpp_router_client.py`

**Files:**
- Create: `tests/test_cpp_router_client.py`
- Test target: `app/services/cpp_router_client.py` (`compute_itinerary_with_cpp`)

**Interfaces:**
- Consumes: `app.services.cpp_router_client.compute_itinerary_with_cpp(snapshot, start_node_id: str, end_node_id: str, graph_id: str) -> Optional[List[str]]`; `app.proto.astar_pb2_grpc.AStarServiceStub` (monkeypatchable class, constructed as `AStarServiceStub(channel)` and expected to expose `.StoreGraph(request)` / `.Solve(request)`); `app.proto.astar_pb2.{StoreResponse, SolveResponse}`; `app.models.itinerary.{RoutingGraphSnapshot, NodeDTO, EdgeDTO}`.

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for app/services/cpp_router_client.py."""
import grpc

from app.models.itinerary import EdgeDTO, NodeDTO, RoutingGraphSnapshot
from app.proto import astar_pb2
from app.services import cpp_router_client


def _make_snapshot() -> RoutingGraphSnapshot:
    return RoutingGraphSnapshot(
        nodes=[
            NodeDTO(id="osm:1", lat=49.0, lon=6.0, is_transit_stop=False),
            NodeDTO(id="osm:2", lat=49.01, lon=6.01, is_transit_stop=False),
        ],
        edges=[
            EdgeDTO(
                edge_id="osm_1_2_0", source_id="osm:1", target_id="osm:2",
                weight=10.0, length=100.0, layer=1,
            )
        ],
    )


class _FakeStub:
    """Stand-in for astar_pb2_grpc.AStarServiceStub."""

    def __init__(self, channel, store_response=None, solve_response=None, store_error=None, solve_error=None):
        self._store_response = store_response
        self._solve_response = solve_response
        self._store_error = store_error
        self._solve_error = solve_error

    def StoreGraph(self, request):
        if self._store_error:
            raise self._store_error
        return self._store_response

    def Solve(self, request):
        if self._solve_error:
            raise self._solve_error
        return self._solve_response


class _FakeRpcError(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return "connection refused"


def _patch_stub(monkeypatch, **kwargs):
    monkeypatch.setattr(
        "app.services.cpp_router_client.astar_pb2_grpc.AStarServiceStub",
        lambda channel: _FakeStub(channel, **kwargs),
    )


async def test_empty_snapshot_returns_none():
    result = await cpp_router_client.compute_itinerary_with_cpp(
        RoutingGraphSnapshot(nodes=[], edges=[]), "osm:1", "osm:2", graph_id="test"
    )
    assert result is None


async def test_unknown_start_id_returns_none():
    result = await cpp_router_client.compute_itinerary_with_cpp(
        _make_snapshot(), "osm:999", "osm:2", graph_id="test"
    )
    assert result is None


async def test_success_returns_edge_ids(monkeypatch):
    _patch_stub(
        monkeypatch,
        store_response=astar_pb2.StoreResponse(success=True, message=""),
        solve_response=astar_pb2.SolveResponse(
            found=True, path=["osm:1", "osm:2"], total_cost=10.0, nodes_explored=2
        ),
    )

    result = await cpp_router_client.compute_itinerary_with_cpp(
        _make_snapshot(), "osm:1", "osm:2", graph_id="test"
    )

    assert result == ["osm_1_2_0"]


async def test_store_graph_failure_returns_none(monkeypatch):
    _patch_stub(
        monkeypatch,
        store_response=astar_pb2.StoreResponse(success=False, message="boom"),
    )

    result = await cpp_router_client.compute_itinerary_with_cpp(
        _make_snapshot(), "osm:1", "osm:2", graph_id="test"
    )

    assert result is None


async def test_solve_not_found_returns_none(monkeypatch):
    _patch_stub(
        monkeypatch,
        store_response=astar_pb2.StoreResponse(success=True, message=""),
        solve_response=astar_pb2.SolveResponse(found=False, path=[], total_cost=0.0, nodes_explored=0),
    )

    result = await cpp_router_client.compute_itinerary_with_cpp(
        _make_snapshot(), "osm:1", "osm:2", graph_id="test"
    )

    assert result is None


async def test_rpc_error_returns_none(monkeypatch):
    _patch_stub(monkeypatch, store_error=_FakeRpcError())

    result = await cpp_router_client.compute_itinerary_with_cpp(
        _make_snapshot(), "osm:1", "osm:2", graph_id="test"
    )

    assert result is None


async def test_path_ids_are_not_cast_to_int(monkeypatch):
    """Regression: solve_resp.path entries used to be int()-cast, which
    crashes as soon as node ids stopped being plain integer strings."""
    _patch_stub(
        monkeypatch,
        store_response=astar_pb2.StoreResponse(success=True, message=""),
        solve_response=astar_pb2.SolveResponse(
            found=True, path=["osm:1", "osm:2"], total_cost=10.0, nodes_explored=2
        ),
    )

    # Must not raise ValueError trying to int("osm:1").
    result = await cpp_router_client.compute_itinerary_with_cpp(
        _make_snapshot(), "osm:1", "osm:2", graph_id="test"
    )

    assert result == ["osm_1_2_0"]
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_cpp_router_client.py -v`
Expected: 7 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cpp_router_client.py
git commit -m "test: cover cpp_router_client with a mocked gRPC stub"
```

---

### Task 6: `tests/test_osmnx_graph.py`

**Files:**
- Create: `tests/test_osmnx_graph.py`
- Test target: `app/services/osmnx_graph.py`

**Interfaces:**
- Consumes: `app.services.osmnx_graph.{_tile_id, _compute_bbox, _infer_speed_mps, build_osmnx_snapshot_and_path, DEFAULT_PROFILE_SPEEDS_MPS}`; mocks `osmnx.graph_from_bbox` and `osmnx.distance.nearest_nodes` (real `networkx.MultiDiGraph` and real `networkx.shortest_path` are used, not mocked).

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for app/services/osmnx_graph.py."""
from unittest.mock import MagicMock

import networkx as nx
import pytest

from app.services import osmnx_graph


def test_tile_id():
    assert osmnx_graph._tile_id((6.123456, 49.123456)) == "49.12:6.12"


def test_compute_bbox_grows_the_area():
    north, south, east, west = osmnx_graph._compute_bbox(
        start_lat=49.0, start_lon=6.0, end_lat=49.01, end_lon=6.01, margin_m=100.0
    )
    assert north > 49.01
    assert south < 49.0
    assert east > 6.01
    assert west < 6.0


def test_infer_speed_mps_from_maxspeed_tag():
    speed = osmnx_graph._infer_speed_mps({"maxspeed": "50"}, "driving")
    assert speed == pytest.approx(50 / 3.6)


def test_infer_speed_mps_falls_back_to_profile_default():
    speed = osmnx_graph._infer_speed_mps({}, "walking")
    assert speed == osmnx_graph.DEFAULT_PROFILE_SPEEDS_MPS["walking"]


def _make_test_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=6.0, y=49.0)
    graph.add_node(2, x=6.001, y=49.001)
    graph.add_node(3, x=6.002, y=49.002)
    graph.add_edge(1, 2, key=0, length=100.0, maxspeed="50", name="Rue A")
    graph.add_edge(2, 3, key=0, length=150.0, maxspeed="50", name="Rue B")
    return graph


def test_graph_from_bbox_called_with_positional_bbox_tuple(monkeypatch):
    """Regression: ox.graph_from_bbox takes a single positional
    bbox=(west, south, east, north) tuple in osmnx>=2.0, not
    north=/south=/east=/west= kwargs. Calling it the old way raised
    TypeError on every request and silently broke the whole osmnx path."""
    empty_graph = MagicMock()
    empty_graph.number_of_nodes.return_value = 0
    empty_graph.number_of_edges.return_value = 0
    fake_graph_from_bbox = MagicMock(return_value=empty_graph)
    monkeypatch.setattr("osmnx.graph_from_bbox", fake_graph_from_bbox)

    result = osmnx_graph.build_osmnx_snapshot_and_path(
        start_lat=49.0, start_lon=6.0, end_lat=49.01, end_lon=6.01,
        routing_profile="driving",
    )

    assert result is None
    args, kwargs = fake_graph_from_bbox.call_args
    assert len(args) == 1
    assert len(args[0]) == 4
    assert "north" not in kwargs
    assert "south" not in kwargs
    assert "east" not in kwargs
    assert "west" not in kwargs
    assert kwargs["network_type"] == "drive"


def test_nearest_nodes_failure_falls_back(monkeypatch):
    monkeypatch.setattr("osmnx.graph_from_bbox", MagicMock(return_value=_make_test_graph()))
    monkeypatch.setattr(
        "osmnx.distance.nearest_nodes",
        MagicMock(side_effect=ImportError("scikit-learn must be installed")),
    )

    result = osmnx_graph.build_osmnx_snapshot_and_path(
        start_lat=49.0, start_lon=6.0, end_lat=49.002, end_lon=6.002,
        routing_profile="driving",
    )

    assert result is None


def test_no_path_between_nodes_falls_back(monkeypatch):
    graph = _make_test_graph()
    monkeypatch.setattr("osmnx.graph_from_bbox", MagicMock(return_value=graph))
    monkeypatch.setattr("osmnx.distance.nearest_nodes", MagicMock(side_effect=[1, 3]))
    monkeypatch.setattr(
        "networkx.shortest_path",
        MagicMock(side_effect=nx.NetworkXNoPath("no path")),
    )

    result = osmnx_graph.build_osmnx_snapshot_and_path(
        start_lat=49.0, start_lon=6.0, end_lat=49.002, end_lon=6.002,
        routing_profile="driving",
    )

    assert result is None


def test_build_osmnx_snapshot_and_path_happy_path(monkeypatch):
    graph = _make_test_graph()
    monkeypatch.setattr("osmnx.graph_from_bbox", MagicMock(return_value=graph))
    monkeypatch.setattr("osmnx.distance.nearest_nodes", MagicMock(side_effect=[1, 3]))

    result = osmnx_graph.build_osmnx_snapshot_and_path(
        start_lat=49.0, start_lon=6.0, end_lat=49.002, end_lon=6.002,
        routing_profile="driving",
    )

    assert result is not None
    snapshot = result["snapshot"]
    assert [n.id for n in snapshot.nodes] == ["osm:1", "osm:2", "osm:3"]
    assert snapshot.edges[0].source_id == "osm:1"
    assert snapshot.edges[0].target_id == "osm:2"
    assert result["start_node_id"] == "osm:1"
    assert result["end_node_id"] == "osm:3"
    assert result["selected_edge_ids"] == [0, 1]
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_osmnx_graph.py -v`
Expected: 7 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_osmnx_graph.py
git commit -m "test: cover osmnx_graph, including a regression test for the bbox API signature"
```

---

### Task 7: `tests/test_resolvers_route.py`

**Files:**
- Create: `tests/test_resolvers_route.py`
- Test target: `app/resolvers/route.py` (`RouteQuery.route`, `RouteQuery.info_trafic`, `RouteQuery.get_itineraire_from_to`, `_select_edge_ids`)

**Interfaces:**
- Consumes: `RouteQuery().route(from_lat, from_lon, to_lat, to_lon)`, `RouteQuery().info_trafic(from_lat, from_lon, to_lat, to_lon)`, `RouteQuery().get_itineraire_from_to(request, friction_updates=None)`, `_select_edge_ids(graph_snapshot, start_node_id, end_node_id, default_edge_ids)` — all called directly as plain async functions (bypassing the GraphQL layer, verified to work). Monkeypatches `app.resolvers.route.{fetch_route, build_osmnx_snapshot_and_path, compute_itinerary_with_cpp}`. Relies on `TRAFFIC_INFO_URL`/`PREDICTIVE_INFO_URL` being empty by default (no `.env` override) so `get_realtime_factor`/`get_predictive_factor` short-circuit to `1.0` without mocking.

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for the GraphQL resolvers in app/resolvers/route.py."""
from datetime import datetime, timezone

from app.models.itinerary import (
    CoordinateInput,
    EdgeDTO,
    FrontStepDTO,
    NodeDTO,
    RouteRequestDTO,
    RoutingGraphSnapshot,
)
from app.resolvers.route import RouteQuery, _select_edge_ids


def _make_request(routing_profile: str = "driving") -> RouteRequestDTO:
    return RouteRequestDTO(
        start_point=CoordinateInput(lat=49.0, lon=6.0),
        end_point=CoordinateInput(lat=49.01, lon=6.01),
        departure_time=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        routing_profile=routing_profile,
    )


def _make_osmnx_result() -> dict:
    node_a = NodeDTO(id="osm:1", lat=49.0, lon=6.0, is_transit_stop=False)
    node_b = NodeDTO(id="osm:2", lat=49.01, lon=6.01, is_transit_stop=False)
    edge_ab = EdgeDTO(
        edge_id="osm_1_2_0", source_id="osm:1", target_id="osm:2",
        weight=10.0, length=100.0, layer=1,
    )
    return {
        "snapshot": RoutingGraphSnapshot(nodes=[node_a, node_b], edges=[edge_ab]),
        "selected_edge_ids": [0],
        "geometry_coordinates": [[6.0, 49.0], [6.01, 49.01]],
        "front_steps": [FrontStepDTO(instruction="Continue", distance_m=100.0, duration_s=10.0)],
        "start_node_id": "osm:1",
        "end_node_id": "osm:2",
    }


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


async def test_get_itineraire_uses_osmnx_result_when_available(monkeypatch):
    monkeypatch.setattr(
        "app.resolvers.route.build_osmnx_snapshot_and_path",
        lambda **kwargs: _make_osmnx_result(),
    )

    async def fake_compute_itinerary_with_cpp(*args, **kwargs):
        return None  # router unreachable -> falls back to osmnx's own selection

    monkeypatch.setattr(
        "app.resolvers.route.compute_itinerary_with_cpp", fake_compute_itinerary_with_cpp
    )

    result = await RouteQuery().get_itineraire_from_to(request=_make_request())

    assert result is not None
    assert result.graph_snapshot.nodes[0].id == "osm:1"
    assert result.distance_m == 100.0
    assert result.duration_s == 10.0


async def test_get_itineraire_falls_back_to_osrm_when_osmnx_unavailable(monkeypatch):
    monkeypatch.setattr(
        "app.resolvers.route.build_osmnx_snapshot_and_path", lambda **kwargs: None
    )

    async def fake_fetch_route(*args, **kwargs):
        return {
            "distance": 300.0,
            "duration": 60.0,
            "geometry": {
                "type": "LineString",
                "coordinates": [[6.0, 49.0], [6.005, 49.005], [6.01, 49.01]],
            },
            "legs": [{"steps": [{"name": "Rue Fallback", "distance": 300.0, "duration": 60.0}]}],
        }

    monkeypatch.setattr("app.resolvers.route.fetch_route", fake_fetch_route)

    async def fake_compute_itinerary_with_cpp(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.resolvers.route.compute_itinerary_with_cpp", fake_compute_itinerary_with_cpp
    )

    result = await RouteQuery().get_itineraire_from_to(request=_make_request())

    assert result is not None
    assert result.graph_snapshot.nodes[0].id.startswith("synthetic:")


async def test_get_itineraire_returns_none_when_osmnx_and_osrm_both_fail(monkeypatch):
    monkeypatch.setattr(
        "app.resolvers.route.build_osmnx_snapshot_and_path", lambda **kwargs: None
    )

    async def fake_fetch_route(*args, **kwargs):
        return None

    monkeypatch.setattr("app.resolvers.route.fetch_route", fake_fetch_route)

    result = await RouteQuery().get_itineraire_from_to(request=_make_request())

    assert result is None


async def test_select_edge_ids_uses_unique_graph_id_per_call(monkeypatch):
    """Regression: graph_id used to be a shared "default" string, so
    concurrent requests could clobber each other's stored graph on the C++
    router."""
    snapshot = _make_osmnx_result()["snapshot"]
    seen_graph_ids = []

    async def fake_compute_itinerary_with_cpp(snapshot_arg, start_id, end_id, graph_id):
        seen_graph_ids.append(graph_id)
        return None

    monkeypatch.setattr(
        "app.resolvers.route.compute_itinerary_with_cpp", fake_compute_itinerary_with_cpp
    )

    await _select_edge_ids(snapshot, "osm:1", "osm:2", [0])
    await _select_edge_ids(snapshot, "osm:1", "osm:2", [0])

    assert len(seen_graph_ids) == 2
    assert seen_graph_ids[0] != seen_graph_ids[1]


async def test_select_edge_ids_remaps_cpp_edge_ids_to_indices(monkeypatch):
    node_a = NodeDTO(id="osm:1", lat=49.0, lon=6.0, is_transit_stop=False)
    node_b = NodeDTO(id="osm:2", lat=49.01, lon=6.01, is_transit_stop=False)
    node_c = NodeDTO(id="osm:3", lat=49.02, lon=6.02, is_transit_stop=False)
    edge_ab = EdgeDTO(edge_id="edge_ab", source_id="osm:1", target_id="osm:2", weight=1.0, length=1.0, layer=1)
    edge_bc = EdgeDTO(edge_id="edge_bc", source_id="osm:2", target_id="osm:3", weight=1.0, length=1.0, layer=1)
    snapshot = RoutingGraphSnapshot(nodes=[node_a, node_b, node_c], edges=[edge_ab, edge_bc])

    async def fake_compute_itinerary_with_cpp(*args, **kwargs):
        return ["edge_bc", "edge_ab"]

    monkeypatch.setattr(
        "app.resolvers.route.compute_itinerary_with_cpp", fake_compute_itinerary_with_cpp
    )

    result = await _select_edge_ids(snapshot, "osm:1", "osm:3", default_edge_ids=[0, 1])

    assert result == [1, 0]


async def test_select_edge_ids_falls_back_when_cpp_returns_unknown_edge_ids(monkeypatch):
    snapshot = _make_osmnx_result()["snapshot"]

    async def fake_compute_itinerary_with_cpp(*args, **kwargs):
        return ["does-not-exist"]

    monkeypatch.setattr(
        "app.resolvers.route.compute_itinerary_with_cpp", fake_compute_itinerary_with_cpp
    )

    result = await _select_edge_ids(snapshot, "osm:1", "osm:2", default_edge_ids=[0])

    assert result == [0]
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_resolvers_route.py -v`
Expected: 9 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_resolvers_route.py
git commit -m "test: cover route resolvers, including graph_id uniqueness and edge_id remapping regressions"
```

---

### Task 8: `tests/test_integration_live_graph.py`

**Files:**
- Create: `tests/test_integration_live_graph.py`
- Test target: full stack, via `app.main.app`

**Interfaces:**
- Consumes: `app.main.app` (FastAPI instance), `httpx.ASGITransport` + `httpx.AsyncClient` (verified working against this app).

- [ ] **Step 1: Write the test file**

```python
"""End-to-end test against the real Overpass API. Excluded from the default
pytest run (see pytest.ini) — run explicitly with `pytest -m integration`.
Requires network access."""
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


async def test_get_itineraire_returns_a_real_osm_graph():
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
    snapshot = body["data"]["getItineraireFromTo"]["graphSnapshot"]
    assert len(snapshot["nodes"]) > 0
    assert len(snapshot["edges"]) > 0
    assert snapshot["nodes"][0]["id"].startswith("osm:")
```

- [ ] **Step 2: Run the test**

Run: `pytest -m integration -v`
Expected: 1 passed (requires network access to Overpass; may take several seconds).

- [ ] **Step 3: Run the full default suite to confirm the integration test is excluded**

Run: `pytest -v`
Expected: all unit tests pass, `test_integration_live_graph.py` does not appear in the collected/run list.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration_live_graph.py
git commit -m "test: add live end-to-end integration test (marked, opt-in)"
```

---

## Final Verification

- [ ] Run: `pytest -v` — expect all unit tests across every task above to pass (`test_graph_engine.py`, `test_imports.py`, `test_osrm_client.py`, `test_traffic_client.py`, `test_itinerary_tracking.py`, `test_cpp_router_client.py`, `test_osmnx_graph.py`, `test_resolvers_route.py`), zero network access used, `test_integration_live_graph.py` excluded.
- [ ] Run: `pytest -m integration -v` — expect the live test to pass with network access.
- [ ] Run: `make validate` — expect it to complete (install, lint, test) using `requirements-dev.txt`.
