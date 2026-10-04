"""Representation interne du graphe de routage d'une requete.

Le graphe envoye au routeur C++ peut compter plusieurs dizaines de milliers de
noeuds. Le materialiser en objets Strawberry (NodeDTO/EdgeDTO) a chaque
requete coutait a lui seul plusieurs centaines de millisecondes, alors que ces
objets ne servent qu'au champ GraphQL `graphSnapshot`, rarement demande.

Un `RoutingGraph` sait donc :
  - produire directement la requete gRPC `SolveRequest` ;
  - traduire le chemin renvoye par le C++ en aretes detaillees (`PathEdge`) ;
  - fabriquer, seulement si on le lui demande, le snapshot GraphQL complet.

Deux implementations :
  - `SnapshotGraph` enveloppe un `RoutingGraphSnapshot` deja construit (repli
    OSRM, tests) ;
  - `OsmRoutingGraph` (app/services/osmnx_graph.py) travaille sur des tableaux
    numpy extraits d'une region OSM en cache, plus d'eventuelles couches
    additionnelles (transport en commun).
"""

import math
from dataclasses import dataclass, field
from functools import cached_property
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.itinerary import EdgeDTO, NodeDTO, RoutingGraphSnapshot
from app.proto import astar_pb2

BBox = Tuple[float, float, float, float]  # (ouest, sud, est, nord)

LAYER_ROAD = 1
LAYER_TRANSIT = 2
LAYER_CONNECTION = 3


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius_m = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


@dataclass(frozen=True)
class GraphNode:
    """Noeud d'une couche additionnelle (arret GTFS, noeud de trajet...)."""

    id: str
    lat: float
    lon: float
    is_transit_stop: bool = False
    name: Optional[str] = None


@dataclass(frozen=True)
class GraphEdge:
    """Arete d'une couche additionnelle (trajet GTFS, montee, liaison a pied...)."""

    edge_id: str
    source_id: str
    target_id: str
    weight: float  # SECONDES
    length: float  # METRES
    layer: int
    name: Optional[str] = None
    transit_line_id: Optional[str] = None
    route_short_name: Optional[str] = None
    route_type: Optional[int] = None
    route_color: Optional[str] = None
    headsign: Optional[str] = None


@dataclass(frozen=True)
class PathEdge:
    """Une arete du chemin retenu, avec tout ce qu'il faut pour les instructions."""

    edge_id: str
    source_id: str
    target_id: str
    weight: float  # SECONDES
    length: float  # METRES
    layer: int
    coordinates: Tuple[Tuple[float, float], ...]  # (lon, lat), orientees source -> cible
    name: Optional[str] = None
    ref: Optional[str] = None
    highway: Optional[str] = None
    roundabout: bool = False
    junction_degree: int = 0  # voisins distincts du noeud source (0 = inconnu)
    source_exits: Optional[int] = None  # sorties hors rond-point au noeud source
    transit_line_id: Optional[str] = None
    route_short_name: Optional[str] = None
    route_type: Optional[int] = None
    route_color: Optional[str] = None
    headsign: Optional[str] = None
    source_name: Optional[str] = None  # nom d'arret si le noeud source en est un
    target_name: Optional[str] = None


class RoutingGraph:
    """Interface commune aux graphes de routage."""

    def is_empty(self) -> bool:
        raise NotImplementedError

    def has_node(self, node_id: str) -> bool:
        raise NotImplementedError

    def node_count(self) -> int:
        raise NotImplementedError

    def edge_count(self) -> int:
        raise NotImplementedError

    def build_solve_request(self, start_id: str, goal_id: str) -> "astar_pb2.SolveRequest":
        raise NotImplementedError

    def node_coordinate(self, node_id: str) -> Optional[Tuple[float, float]]:
        """(lon, lat) d'un noeud, ou None s'il est inconnu."""
        raise NotImplementedError

    def resolve_path(self, node_path: Sequence[str]) -> Optional[List[PathEdge]]:
        """Traduit une suite d'ids de noeuds en aretes ; None si incoherent."""
        raise NotImplementedError

    def to_snapshot(self) -> RoutingGraphSnapshot:
        """Snapshot GraphQL complet. Couteux : a n'appeler qu'a la demande."""
        raise NotImplementedError


class LazyGraphSnapshot:
    """Se comporte comme un `RoutingGraphSnapshot`, mais ne le construit qu'au
    premier acces a `nodes` ou `edges` — c'est-a-dire seulement si le client a
    demande le champ `graphSnapshot`."""

    def __init__(self, graph: RoutingGraph):
        self._graph = graph

    @cached_property
    def _snapshot(self) -> RoutingGraphSnapshot:
        return self._graph.to_snapshot()

    @property
    def nodes(self) -> List[NodeDTO]:
        return self._snapshot.nodes

    @property
    def edges(self) -> List[EdgeDTO]:
        return self._snapshot.edges


def _node_degrees(edges: Sequence[EdgeDTO]) -> Dict[str, int]:
    neighbours: Dict[str, set] = {}
    for edge in edges:
        neighbours.setdefault(edge.source_id, set()).add(edge.target_id)
        neighbours.setdefault(edge.target_id, set()).add(edge.source_id)
    return {node_id: len(ids) for node_id, ids in neighbours.items()}


@dataclass
class SnapshotGraph(RoutingGraph):
    """Graphe deja materialise en DTO (repli OSRM et tests)."""

    snapshot: RoutingGraphSnapshot
    node_names: Dict[str, str] = field(default_factory=dict)

    @cached_property
    def _node_by_id(self) -> Dict[str, NodeDTO]:
        return {node.id: node for node in self.snapshot.nodes}

    @cached_property
    def _edge_by_pair(self) -> Dict[Tuple[str, str], EdgeDTO]:
        edge_by_pair: Dict[Tuple[str, str], EdgeDTO] = {}
        for edge in self.snapshot.edges:
            pair = (edge.source_id, edge.target_id)
            current = edge_by_pair.get(pair)
            # aretes paralleles : on garde la moins couteuse
            if current is None or edge.weight < current.weight:
                edge_by_pair[pair] = edge
        return edge_by_pair

    @cached_property
    def _degrees(self) -> Dict[str, int]:
        return _node_degrees(self.snapshot.edges)

    def is_empty(self) -> bool:
        return not self.snapshot.nodes or not self.snapshot.edges

    def has_node(self, node_id: str) -> bool:
        return node_id in self._node_by_id

    def node_count(self) -> int:
        return len(self.snapshot.nodes)

    def edge_count(self) -> int:
        return len(self.snapshot.edges)

    def build_solve_request(self, start_id: str, goal_id: str) -> "astar_pb2.SolveRequest":
        request = astar_pb2.SolveRequest(start_id=start_id, goal_id=goal_id)
        add_node = request.nodes.add
        for node in self.snapshot.nodes:
            add_node(id=node.id, lat=node.lat, lon=node.lon)
        add_edge = request.edges.add
        for edge in self.snapshot.edges:
            add_edge(from_id=edge.source_id, to_id=edge.target_id, weight=edge.weight)
        return request

    def node_coordinate(self, node_id: str) -> Optional[Tuple[float, float]]:
        node = self._node_by_id.get(node_id)
        return None if node is None else (node.lon, node.lat)

    def resolve_path(self, node_path: Sequence[str]) -> Optional[List[PathEdge]]:
        if len(node_path) < 2:
            return None
        edges: List[PathEdge] = []
        for source_id, target_id in zip(node_path, node_path[1:]):
            edge = self._edge_by_pair.get((source_id, target_id))
            source = self._node_by_id.get(source_id)
            target = self._node_by_id.get(target_id)
            if edge is None or source is None or target is None:
                return None
            edges.append(
                PathEdge(
                    edge_id=edge.edge_id,
                    source_id=source_id,
                    target_id=target_id,
                    weight=edge.weight,
                    length=edge.length,
                    layer=edge.layer,
                    coordinates=((source.lon, source.lat), (target.lon, target.lat)),
                    name=edge.name,
                    junction_degree=self._degrees.get(source_id, 0),
                    transit_line_id=edge.transit_line_id,
                    source_name=self.node_names.get(source_id),
                    target_name=self.node_names.get(target_id),
                )
            )
        return edges

    def to_snapshot(self) -> RoutingGraphSnapshot:
        return self.snapshot


def as_routing_graph(graph) -> RoutingGraph:
    """Accepte un RoutingGraph ou un RoutingGraphSnapshot (compatibilite)."""
    if isinstance(graph, RoutingGraph):
        return graph
    if isinstance(graph, RoutingGraphSnapshot):
        return SnapshotGraph(graph)
    raise TypeError(f"graphe de routage non supporte : {type(graph)!r}")


def extra_edge_to_path_edge(
    edge: GraphEdge,
    source: GraphNode | Tuple[float, float, Optional[str]],
    target: GraphNode | Tuple[float, float, Optional[str]],
) -> PathEdge:
    """Construit un PathEdge pour une arete de couche additionnelle.

    `source`/`target` sont soit des GraphNode, soit des tuples (lon, lat, nom).
    """

    def _unpack(node) -> Tuple[float, float, Optional[str]]:
        if isinstance(node, GraphNode):
            return node.lon, node.lat, node.name
        return node

    s_lon, s_lat, s_name = _unpack(source)
    t_lon, t_lat, t_name = _unpack(target)
    return PathEdge(
        edge_id=edge.edge_id,
        source_id=edge.source_id,
        target_id=edge.target_id,
        weight=edge.weight,
        length=edge.length,
        layer=edge.layer,
        coordinates=((s_lon, s_lat), (t_lon, t_lat)),
        name=edge.name,
        transit_line_id=edge.transit_line_id,
        route_short_name=edge.route_short_name,
        route_type=edge.route_type,
        route_color=edge.route_color,
        headsign=edge.headsign,
        source_name=s_name,
        target_name=t_name,
    )
