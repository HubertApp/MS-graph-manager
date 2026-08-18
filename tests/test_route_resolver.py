
from datetime import datetime, timezone

import pytest

from app.models.itinerary import (
    CoordinateInput,
    EdgeDTO,
    NodeDTO,
    RouteRequestDTO,
    RoutingGraphSnapshot,
)
from app.resolvers import route as route_module
from app.resolvers.route import RouteQuery
from app.services.cpp_router_client import RoutingResult


def _request(profile: str = "driving") -> RouteRequestDTO:
    return RouteRequestDTO(
        start_point=CoordinateInput(lat=49.1193, lon=6.1757),
        end_point=CoordinateInput(lat=49.1096, lon=6.1825),
        departure_time=datetime.now(timezone.utc),
        routing_profile=profile,
    )


def _snapshot() -> RoutingGraphSnapshot:
    return RoutingGraphSnapshot(
        nodes=[
            NodeDTO(id="A", lat=49.1193, lon=6.1757, is_transit_stop=False),
            NodeDTO(id="B", lat=49.1150, lon=6.1800, is_transit_stop=False),
            NodeDTO(id="D", lat=49.1096, lon=6.1825, is_transit_stop=False),
        ],
        edges=[
            EdgeDTO(edge_id="e_AB", source_id="A", target_id="B",
                    weight=8.0, length=800.0, layer=1, name="Rue A"),
            EdgeDTO(edge_id="e_BD", source_id="B", target_id="D",
                    weight=1.0, length=100.0, layer=1, name="Rue B"),
        ],
    )


@pytest.fixture(autouse=True)
def stub_dependances(monkeypatch):
    """Neutralise trafic et tracking pour tous les tests du module."""

    async def _facteur(*args, **kwargs):
        return 1.0

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(route_module, "get_realtime_factor", _facteur)
    monkeypatch.setattr(route_module, "get_predictive_factor", _facteur)
    monkeypatch.setattr(route_module, "notify_tracking_service", _noop)


@pytest.fixture
def graphe_disponible(monkeypatch):
    monkeypatch.setattr(
        route_module,
        "build_osmnx_snapshot",
        lambda **kwargs: {
            "snapshot": _snapshot(),
            "start_node_id": "A",
            "end_node_id": "D",
            "graph": None,
        },
    )


@pytest.fixture
def routeur_repond(monkeypatch):
    async def _solve(snapshot, start, goal):
        return RoutingResult(
            node_path=["A", "B", "D"], total_cost=9.0, nodes_explored=3
        )

    monkeypatch.setattr(route_module, "solve_with_cpp", _solve)



@pytest.mark.asyncio
async def test_produit_un_itineraire_complet(graphe_disponible, routeur_repond):
    result = await RouteQuery().get_itineraire_from_to(_request())
    assert result is not None
    assert result.distance_m == 900.0
    assert result.duration_s == 9.0
    assert len(result.steps) == 2
    assert [s.instruction for s in result.steps] == ["Rue A", "Rue B"]


@pytest.mark.asyncio
async def test_geometrie_coherente(graphe_disponible, routeur_repond):
    result = await RouteQuery().get_itineraire_from_to(_request())
    assert result.geometry.type == "LineString"
    assert len(result.geometry.coordinates) == 3
    assert result.geometry.coordinates[0] == [6.1757, 49.1193]


@pytest.mark.asyncio
async def test_expose_le_snapshot_du_graphe(graphe_disponible, routeur_repond):
    result = await RouteQuery().get_itineraire_from_to(_request())
    assert len(result.graph_snapshot.nodes) == 3
    assert len(result.graph_snapshot.edges) == 2


@pytest.mark.asyncio
async def test_expose_les_facteurs_trafic(graphe_disponible, routeur_repond):
    result = await RouteQuery().get_itineraire_from_to(_request())
    assert result.traffic.realtime_factor == 1.0
    assert result.traffic.source == "traffic+predictive"


@pytest.mark.asyncio
async def test_regroupe_les_aretes_homogenes_en_un_segment(graphe_disponible, routeur_repond):
    """Depuis l'etape 7b : un segment par troncon homogene (meme type, meme
    ligne), pas un segment par arete. e_AB et e_BD sont toutes deux layer=1
    (route) sans transit_line_id : elles fusionnent en un seul segment."""
    result = await RouteQuery().get_itineraire_from_to(_request())
    assert len(result.segments) == 1
    assert result.segments[0].type == "road"


@pytest.mark.asyncio
async def test_duree_totale_egale_somme_des_etapes(graphe_disponible, routeur_repond):
    result = await RouteQuery().get_itineraire_from_to(_request())
    assert sum(s.duration_s for s in result.steps) == pytest.approx(result.duration_s)



@pytest.mark.asyncio
async def test_renvoie_null_si_routeur_indisponible(graphe_disponible, monkeypatch):
    """Consequence assumee de la separation : plus de repli Python."""

    async def _echec(*args, **kwargs):
        return None

    monkeypatch.setattr(route_module, "solve_with_cpp", _echec)
    assert await RouteQuery().get_itineraire_from_to(_request()) is None


@pytest.mark.asyncio
async def test_renvoie_null_si_graphe_inconstructible(monkeypatch):
    monkeypatch.setattr(route_module, "build_osmnx_snapshot", lambda **kw: None)

    async def _pas_de_route(*args, **kwargs):
        return None

    monkeypatch.setattr(route_module, "fetch_route", _pas_de_route)
    assert await RouteQuery().get_itineraire_from_to(_request()) is None


@pytest.mark.asyncio
async def test_renvoie_null_si_chemin_non_reconstituable(
    graphe_disponible, monkeypatch
):
    """Le C++ renvoie un chemin dont une arete n'existe pas dans le snapshot."""

    async def _chemin_incoherent(*args, **kwargs):
        return RoutingResult(
            node_path=["A", "FANTOME", "D"], total_cost=9.0, nodes_explored=3
        )

    monkeypatch.setattr(route_module, "solve_with_cpp", _chemin_incoherent)
    assert await RouteQuery().get_itineraire_from_to(_request()) is None



@pytest.mark.asyncio
async def test_bascule_sur_osrm_si_osmnx_absent(monkeypatch, routeur_repond):
    monkeypatch.setattr(route_module, "build_osmnx_snapshot", lambda **kw: None)

    async def _osrm(*args, **kwargs):
        return {
            "geometry": {
                "type": "LineString",
                "coordinates": [[6.1757, 49.1193], [6.1800, 49.1150],
                                [6.1825, 49.1096]],
            }
        }

    monkeypatch.setattr(route_module, "fetch_route", _osrm)

    async def _solve(snapshot, start, goal):
        return RoutingResult(
            node_path=[n.id for n in snapshot.nodes],
            total_cost=sum(e.weight for e in snapshot.edges),
            nodes_explored=len(snapshot.nodes),
        )

    monkeypatch.setattr(route_module, "solve_with_cpp", _solve)

    result = await RouteQuery().get_itineraire_from_to(_request())
    assert result is not None
    assert len(result.steps) == 2