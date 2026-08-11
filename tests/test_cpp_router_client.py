
import grpc
import pytest

from app.models.itinerary import EdgeDTO, NodeDTO, RoutingGraphSnapshot
from app.services import cpp_router_client
from app.services.cpp_router_client import _solve_with_cpp_sync


def _snapshot() -> RoutingGraphSnapshot:
    """A -> B -> D coute 9. A -> C -> D coute 19. Optimal : A, B, D."""
    return RoutingGraphSnapshot(
        nodes=[
            NodeDTO(id="A", lat=49.1193, lon=6.1757, is_transit_stop=False),
            NodeDTO(id="B", lat=49.1150, lon=6.1800, is_transit_stop=False),
            NodeDTO(id="C", lat=49.1250, lon=6.1700, is_transit_stop=False),
            NodeDTO(id="D", lat=49.1096, lon=6.1825, is_transit_stop=False),
        ],
        edges=[
            EdgeDTO(edge_id="e_AB", source_id="A", target_id="B",
                    weight=8.0, length=800.0, layer=1, name="Rue A"),
            EdgeDTO(edge_id="e_AC", source_id="A", target_id="C",
                    weight=9.0, length=900.0, layer=1, name="Bd C"),
            EdgeDTO(edge_id="e_BD", source_id="B", target_id="D",
                    weight=1.0, length=100.0, layer=1, name="Rue B"),
            EdgeDTO(edge_id="e_CD", source_id="C", target_id="D",
                    weight=10.0, length=1000.0, layer=1, name="Av D"),
        ],
    )


class _FakeResponse:
    def __init__(self, found=True, path=("A", "B", "D"), cost=9.0, explored=4):
        self.found = found
        self.path = list(path)
        self.total_cost = cost
        self.nodes_explored = explored


class _FakeStub:
    """Double du stub gRPC : enregistre la requete recue."""

    def __init__(self, response=None, error=None):
        self._response = response or _FakeResponse()
        self._error = error
        self.last_request = None
        self.last_timeout = None

    def Solve(self, request, timeout=None):
        if self._error:
            raise self._error
        self.last_request = request
        self.last_timeout = timeout
        return self._response


class _FakeChannel:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def patch_grpc(monkeypatch):
    """Remplace channel et stub. Retourne le double pour inspection."""

    def _install(stub):
        monkeypatch.setattr(
            cpp_router_client.grpc, "insecure_channel", lambda *a, **k: _FakeChannel()
        )
        monkeypatch.setattr(
            cpp_router_client.astar_pb2_grpc, "AStarServiceStub", lambda channel: stub
        )
        return stub

    return _install


def test_retourne_le_chemin_optimal(patch_grpc):
    patch_grpc(_FakeStub())
    result = _solve_with_cpp_sync(_snapshot(), "A", "D")
    assert result is not None
    assert result.node_path == ["A", "B", "D"]
    assert result.total_cost == 9.0
    assert result.nodes_explored == 4


def test_envoie_tous_les_noeuds_et_aretes(patch_grpc):
    stub = patch_grpc(_FakeStub())
    _solve_with_cpp_sync(_snapshot(), "A", "D")
    assert len(stub.last_request.nodes) == 4
    assert len(stub.last_request.edges) == 4


def test_envoie_les_poids_en_secondes(patch_grpc):
    """Le contrat impose des secondes : EdgeDTO.weight passe tel quel."""
    stub = patch_grpc(_FakeStub())
    _solve_with_cpp_sync(_snapshot(), "A", "D")
    poids = {(e.from_id, e.to_id): e.weight for e in stub.last_request.edges}
    assert poids[("A", "B")] == 8.0
    assert poids[("B", "D")] == 1.0


def test_envoie_les_coordonnees_des_noeuds(patch_grpc):
    """Le C++ en a besoin pour son heuristique."""
    stub = patch_grpc(_FakeStub())
    _solve_with_cpp_sync(_snapshot(), "A", "D")
    node_a = next(n for n in stub.last_request.nodes if n.id == "A")
    assert node_a.lat == pytest.approx(49.1193)
    assert node_a.lon == pytest.approx(6.1757)


def test_transmet_start_et_goal(patch_grpc):
    stub = patch_grpc(_FakeStub())
    _solve_with_cpp_sync(_snapshot(), "A", "D")
    assert stub.last_request.start_id == "A"
    assert stub.last_request.goal_id == "D"


def test_applique_un_timeout(patch_grpc):
    """Sans timeout, un routeur bloque figerait le worker uvicorn."""
    stub = patch_grpc(_FakeStub())
    _solve_with_cpp_sync(_snapshot(), "A", "D")
    assert stub.last_timeout is not None
    assert stub.last_timeout > 0

def test_retourne_none_si_aucun_chemin(patch_grpc):
    patch_grpc(_FakeStub(response=_FakeResponse(found=False, path=())))
    assert _solve_with_cpp_sync(_snapshot(), "A", "D") is None


def test_retourne_none_si_routeur_injoignable(patch_grpc):
    class _Unavailable(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "connexion refusee"

    patch_grpc(_FakeStub(error=_Unavailable()))
    assert _solve_with_cpp_sync(_snapshot(), "A", "D") is None


def test_retourne_none_si_timeout_depasse(patch_grpc):
    class _Deadline(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.DEADLINE_EXCEEDED

        def details(self):
            return "deadline exceeded"

    patch_grpc(_FakeStub(error=_Deadline()))
    assert _solve_with_cpp_sync(_snapshot(), "A", "D") is None


def test_retourne_none_si_argument_invalide(patch_grpc):
    class _Invalid(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.INVALID_ARGUMENT

        def details(self):
            return "noeud absent du graphe"

    patch_grpc(_FakeStub(error=_Invalid()))
    assert _solve_with_cpp_sync(_snapshot(), "A", "D") is None


def test_refuse_un_snapshot_vide():
    vide = RoutingGraphSnapshot(nodes=[], edges=[])
    assert _solve_with_cpp_sync(vide, "A", "D") is None


def test_refuse_un_noeud_de_depart_inconnu():
    assert _solve_with_cpp_sync(_snapshot(), "INEXISTANT", "D") is None


def test_refuse_un_noeud_d_arrivee_inconnu():
    assert _solve_with_cpp_sync(_snapshot(), "A", "INEXISTANT") is None