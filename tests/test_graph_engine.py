from datetime import datetime

from app.services import graph_engine


def test_haversine_and_tile_id():
    a = (2.0, 48.0)
    b = (2.001, 48.001)
    d = graph_engine._haversine_m(a, b)
    assert d > 0
    tid = graph_engine._tile_id(a)
    assert ":" in tid


def test_slope_penalty_and_base_speed():
    a = (2.0, 48.0)
    b = (2.01, 48.01)
    p = graph_engine._estimate_slope_penalty(a, b, 5)
    assert p >= 1.0
    assert graph_engine._base_speed_mps("walking") == graph_engine.DEFAULT_PROFILE_SPEEDS_MPS["walking"]


def test_build_multilayer_snapshot_and_edges():
    coords = [(2.0, 48.0), (2.01, 48.01), (2.02, 48.02)]
    snap = graph_engine.build_multilayer_snapshot(coords, "driving")
    assert len(snap.nodes) == 3
    assert len(snap.edges) == 2
    for e in snap.edges:
        assert e.weight > 0
        assert e.length > 0


def test_build_path_segments_and_arrival():
    coords = [(2.0, 48.0), (2.01, 48.01)]
    snap = graph_engine.build_multilayer_snapshot(coords, "walking")
    now = datetime.utcnow()
    segments = graph_engine.build_path_segments(snap.edges, now, "walking")
    assert len(segments) == len(snap.edges)
    # expected arrival increases
    assert segments[-1].expected_arrival >= segments[0].expected_arrival
