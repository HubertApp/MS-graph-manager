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

    result = osmnx_graph.build_osmnx_snapshot(
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

    result = osmnx_graph.build_osmnx_snapshot(
        start_lat=49.0, start_lon=6.0, end_lat=49.002, end_lon=6.002,
        routing_profile="driving",
    )

    assert result is None


def test_build_osmnx_snapshot_happy_path(monkeypatch):
    graph = _make_test_graph()
    monkeypatch.setattr("osmnx.graph_from_bbox", MagicMock(return_value=graph))
    monkeypatch.setattr("osmnx.distance.nearest_nodes", MagicMock(side_effect=[1, 3]))

    result = osmnx_graph.build_osmnx_snapshot(
        start_lat=49.0, start_lon=6.0, end_lat=49.002, end_lon=6.002,
        routing_profile="driving",
    )

    assert result is not None
    snapshot = result["snapshot"]
    assert [n.id for n in snapshot.nodes] == ["osm:1", "osm:2", "osm:3"]
    assert snapshot.edges[0].source_id == "osm:1"
    assert snapshot.edges[0].target_id == "osm:2"
    assert snapshot.edges[0].name == "Rue A"
    assert result["start_node_id"] == "osm:1"
    assert result["end_node_id"] == "osm:3"
