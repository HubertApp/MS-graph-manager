import pytest

from app.models.itinerary import EdgeDTO, NodeDTO, RoutingGraphSnapshot
from app.services.graph_engine import render_path


def _snapshot() -> RoutingGraphSnapshot:
    """A -> B -> C, plus un contournement direct A -> C plus couteux."""
    return RoutingGraphSnapshot(
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
            EdgeDTO(edge_id="e_AC", source_id="A", target_id="C",
                    weight=300.0, length=2000.0, layer=1, name="Contournement"),
        ],
    )


def test_reconstruit_les_aretes_du_chemin():
    rendered = render_path(_snapshot(), ["A", "B", "C"])
    assert rendered is not None
    assert [e.edge_id for e in rendered.selected_edges] == ["e_AB", "e_BC"]


def test_calcule_distance_et_duree():
    rendered = render_path(_snapshot(), ["A", "B", "C"])
    assert rendered.distance_m == 2500.0   # 1000 + 1500
    assert rendered.duration_s == 150.0    # 60 + 90


def test_produit_une_geometrie_geojson():
    """GeoJSON attend [lon, lat], pas [lat, lon]."""
    rendered = render_path(_snapshot(), ["A", "B", "C"])
    assert rendered.geometry_coordinates == [
        [6.0, 49.0], [6.1, 49.1], [6.2, 49.2]
    ]


def test_produit_les_instructions_depuis_les_noms_de_voie():
    rendered = render_path(_snapshot(), ["A", "B", "C"])
    assert [s.instruction for s in rendered.front_steps] == ["Rue A", "Rue B"]


def test_geometrie_a_un_point_de_plus_que_les_etapes():
    """N noeuds -> N-1 aretes -> N-1 instructions."""
    rendered = render_path(_snapshot(), ["A", "B", "C"])
    assert len(rendered.geometry_coordinates) == len(rendered.front_steps) + 1


def test_somme_des_etapes_egale_les_totaux():
    rendered = render_path(_snapshot(), ["A", "B", "C"])
    assert sum(s.duration_s for s in rendered.front_steps) == rendered.duration_s
    assert sum(s.distance_m for s in rendered.front_steps) == rendered.distance_m


def test_instruction_par_defaut_si_voie_sans_nom():
    snap = _snapshot()
    snap.edges[0].name = None
    rendered = render_path(snap, ["A", "B"])
    assert rendered.front_steps[0].instruction == "Continue"


def test_refuse_un_chemin_a_un_seul_noeud():
    assert render_path(_snapshot(), ["A"]) is None


def test_refuse_un_chemin_vide():
    assert render_path(_snapshot(), []) is None


def test_refuse_une_arete_inexistante():
    """C -> A n'existe pas : le graphe est oriente."""
    assert render_path(_snapshot(), ["C", "A"]) is None


def test_refuse_un_noeud_inconnu():
    assert render_path(_snapshot(), ["A", "INEXISTANT"]) is None


def test_retient_l_arete_la_moins_couteuse():
    """OSMnx produit des aretes paralleles (MultiDiGraph)."""
    snap = _snapshot()
    snap.edges.append(
        EdgeDTO(edge_id="e_AB_lent", source_id="A", target_id="B",
                weight=500.0, length=1000.0, layer=1, name="Detour")
    )
    rendered = render_path(snap, ["A", "B"])
    assert rendered.selected_edges[0].edge_id == "e_AB"


def test_ordre_d_insertion_sans_effet_sur_le_choix():
    snap = _snapshot()
    snap.edges.insert(
        0,
        EdgeDTO(edge_id="e_AB_lent", source_id="A", target_id="B",
                weight=500.0, length=1000.0, layer=1, name="Detour"),
    )
    rendered = render_path(snap, ["A", "B"])
    assert rendered.selected_edges[0].edge_id == "e_AB"