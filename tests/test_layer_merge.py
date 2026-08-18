"""Unit tests for app/services/layer_merge.py."""
from unittest.mock import MagicMock

from app.models.itinerary import EdgeDTO, NodeDTO, RoutingGraphSnapshot
from app.services.layer_merge import merge_transit_layer
from app.services.transit_graph_client import TransitLayer


def _road_snapshot() -> RoutingGraphSnapshot:
    return RoutingGraphSnapshot(
        nodes=[
            NodeDTO(id="osm:1", lat=49.1190, lon=6.1750, is_transit_stop=False),
            NodeDTO(id="osm:2", lat=49.1200, lon=6.1760, is_transit_stop=False),
        ],
        edges=[
            EdgeDTO(edge_id="osm_1_2_0", source_id="osm:1", target_id="osm:2",
                    weight=10.0, length=100.0, layer=1, name="Rue Test"),
        ],
    )


def _transit_layer(stop_id="gtfs:stop:metz:gare", stop_lat=49.1193, stop_lon=6.1757) -> TransitLayer:
    ride_id = "gtfs:ride:metz:C1|0|metz:gare"
    stop = NodeDTO(id=stop_id, lat=stop_lat, lon=stop_lon, is_transit_stop=True)
    ride = NodeDTO(id=ride_id, lat=stop_lat, lon=stop_lon, is_transit_stop=True)
    board = EdgeDTO(
        edge_id=f"board:metz:C1|0|metz:gare", source_id=stop_id, target_id=ride_id,
        weight=270.0, length=0.0, layer=3, transit_line_id="metz:C1",
    )
    return TransitLayer(nodes=[stop, ride], edges=[board])


def test_merge_returns_road_snapshot_unchanged_when_transit_layer_is_empty():
    result = merge_transit_layer(
        road_snapshot=_road_snapshot(),
        road_graph=None,
        transit=TransitLayer(nodes=[], edges=[]),
        access_radius_m=400.0,
        ox=None,
    )
    assert len(result.nodes) == 2
    assert len(result.edges) == 1


def test_merge_ignores_stops_outside_the_road_bbox():
    far_transit = _transit_layer(stop_lat=10.0, stop_lon=10.0)
    result = merge_transit_layer(
        road_snapshot=_road_snapshot(),
        road_graph=None,
        transit=far_transit,
        access_radius_m=400.0,
        ox=None,
    )
    assert len(result.nodes) == 2
    assert len(result.edges) == 1


def test_merge_skips_stops_further_than_access_radius():
    ox = MagicMock()
    ox.distance.nearest_nodes.return_value = ([1], [1000.0])  # 1000m, au-dela du rayon

    result = merge_transit_layer(
        road_snapshot=_road_snapshot(),
        road_graph=MagicMock(),
        transit=_transit_layer(),
        access_radius_m=400.0,
        ox=ox,
    )

    assert len(result.nodes) == 2
    assert len(result.edges) == 1


def test_merge_connects_a_stop_within_radius():
    ox = MagicMock()
    ox.distance.nearest_nodes.return_value = ([1], [50.0])  # osm node 1, a 50m

    result = merge_transit_layer(
        road_snapshot=_road_snapshot(),
        road_graph=MagicMock(),
        transit=_transit_layer(),
        access_radius_m=400.0,
        ox=ox,
    )

    # 2 noeuds route + 2 noeuds transit (stop + ride)
    assert len(result.nodes) == 4
    # 1 arete route + 1 arete transit (board) + 2 aretes de liaison (access/egress)
    assert len(result.edges) == 4

    access_edges = [e for e in result.edges if e.layer == 3 and e.edge_id.startswith("access:")]
    egress_edges = [e for e in result.edges if e.layer == 3 and e.edge_id.startswith("egress:")]
    assert len(access_edges) == 1
    assert len(egress_edges) == 1
    assert access_edges[0].source_id == "osm:1"
    assert access_edges[0].target_id == "gtfs:stop:metz:gare"
    assert access_edges[0].weight == 50.0 / 1.4


def test_merge_skips_stop_when_nearest_osm_node_not_in_road_snapshot():
    ox = MagicMock()
    # noeud osm renvoye par nearest_nodes n'existe pas dans le snapshot routier
    ox.distance.nearest_nodes.return_value = ([999], [50.0])

    result = merge_transit_layer(
        road_snapshot=_road_snapshot(),
        road_graph=MagicMock(),
        transit=_transit_layer(),
        access_radius_m=400.0,
        ox=ox,
    )

    assert len(result.nodes) == 2
    assert len(result.edges) == 1
