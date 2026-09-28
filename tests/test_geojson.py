import json
from dataclasses import replace

import pytest

from app.models.itinerary import EdgeDTO, NodeDTO, RoutingGraphSnapshot
from app.services.geojson import rendered_path_to_geojson
from app.services.graph_engine import render_path


@pytest.fixture
def rendered():
    snapshot = RoutingGraphSnapshot(
        nodes=[
            NodeDTO(id="A", lat=49.0, lon=6.0, is_transit_stop=False),
            NodeDTO(id="B", lat=49.1, lon=6.1, is_transit_stop=False),
            NodeDTO(id="C", lat=49.2, lon=6.2, is_transit_stop=False),
        ],
        edges=[
            EdgeDTO(edge_id="e_AB", source_id="A", target_id="B",
                    weight=60.0, length=1000.0, layer=1, name="Rue A"),
            EdgeDTO(edge_id="e_BC", source_id="B", target_id="C",
                    weight=90.0, length=1500.0, layer=1, name="Rue B"),
        ],
    )
    return render_path(snapshot, ["A", "B", "C"])


def _par_kind(geojson, kind):
    return [f for f in geojson["features"] if f["properties"]["kind"] == kind]


def test_produit_une_feature_collection(rendered):
    geojson = rendered_path_to_geojson(rendered, "walking")
    assert geojson["type"] == "FeatureCollection"
    assert all(f["type"] == "Feature" for f in geojson["features"])


def test_le_trace_est_une_linestring_en_lon_lat(rendered):
    [route] = _par_kind(rendered_path_to_geojson(rendered, "walking"), "route")
    assert route["geometry"]["type"] == "LineString"
    assert route["geometry"]["coordinates"] == [[6.0, 49.0], [6.1, 49.1], [6.2, 49.2]]
    assert route["properties"]["distance_m"] == 2500.0
    assert route["properties"]["duration_s"] == 150.0


def test_une_feature_par_instruction_alignee_sur_steps(rendered):
    steps = _par_kind(rendered_path_to_geojson(rendered, "walking"), "step")
    assert [s["properties"]["instruction"] for s in steps] == \
        [step.instruction for step in rendered.front_steps]
    assert [s["properties"]["step_index"] for s in steps] == [0, 1]
    assert steps[0]["geometry"]["coordinates"] == [6.0, 49.0]
    assert steps[1]["geometry"]["coordinates"] == [6.1, 49.1]


def test_depart_et_arrivee(rendered):
    geojson = rendered_path_to_geojson(rendered, "walking")
    [start] = _par_kind(geojson, "start")
    [end] = _par_kind(geojson, "end")
    assert start["geometry"]["coordinates"] == [6.0, 49.0]
    assert end["geometry"]["coordinates"] == [6.2, 49.2]


def test_bbox_englobe_le_trajet(rendered):
    geojson = rendered_path_to_geojson(rendered, "walking")
    assert geojson["bbox"] == [6.0, 49.0, 6.2, 49.2]


def test_est_serialisable_en_json(rendered):
    json.dumps(rendered_path_to_geojson(rendered, "walking"))


def test_chemin_trop_court_donne_une_collection_vide(rendered):
    court = replace(rendered, geometry_coordinates=[[6.0, 49.0]], front_steps=[])
    assert rendered_path_to_geojson(court, "walking") == \
        {"type": "FeatureCollection", "features": []}


def test_ne_modifie_pas_son_entree(rendered):
    avant = [list(c) for c in rendered.geometry_coordinates]
    rendered_path_to_geojson(rendered, "walking")
    assert rendered.geometry_coordinates == avant