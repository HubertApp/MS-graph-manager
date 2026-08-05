"""Generated from astar.proto - message classes (manually created for simplicity)."""
from google.protobuf import descriptor as _descriptor


class Node:
    """Node message."""
    def __init__(self, id: str = "", lat: float = 0.0, lon: float = 0.0):
        self.id = id
        self.lat = lat
        self.lon = lon

    def SerializeToString(self) -> bytes:
        # Simplified serialization (real proto would handle this)
        import json
        return json.dumps({"id": self.id, "lat": self.lat, "lon": self.lon}).encode()

    @staticmethod
    def FromString(data: bytes) -> 'Node':
        import json
        obj = json.loads(data.decode())
        return Node(id=obj["id"], lat=obj["lat"], lon=obj["lon"])


class Edge:
    """Edge message."""
    def __init__(self, from_id: str = "", to_id: str = "", weight: float = 0.0):
        self.from_id = from_id
        self.to_id = to_id
        self.weight = weight

    def SerializeToString(self) -> bytes:
        import json
        return json.dumps({"from_id": self.from_id, "to_id": self.to_id, "weight": self.weight}).encode()

    @staticmethod
    def FromString(data: bytes) -> 'Edge':
        import json
        obj = json.loads(data.decode())
        return Edge(from_id=obj["from_id"], to_id=obj["to_id"], weight=obj["weight"])


class GraphRequest:
    """GraphRequest message."""
    def __init__(self, graph_id: str = "", nodes: list = None, edges: list = None):
        self.graph_id = graph_id
        self.nodes = nodes or []
        self.edges = edges or []

    def SerializeToString(self) -> bytes:
        import json
        return json.dumps({
            "graph_id": self.graph_id,
            "nodes": [{"id": n.id, "lat": n.lat, "lon": n.lon} for n in self.nodes],
            "edges": [{"from_id": e.from_id, "to_id": e.to_id, "weight": e.weight} for e in self.edges]
        }).encode()

    @staticmethod
    def FromString(data: bytes) -> 'GraphRequest':
        import json
        obj = json.loads(data.decode())
        nodes = [Node(id=n["id"], lat=n["lat"], lon=n["lon"]) for n in obj["nodes"]]
        edges = [Edge(from_id=e["from_id"], to_id=e["to_id"], weight=e["weight"]) for e in obj["edges"]]
        return GraphRequest(graph_id=obj["graph_id"], nodes=nodes, edges=edges)


class StoreResponse:
    """StoreResponse message."""
    def __init__(self, success: bool = False, message: str = ""):
        self.success = success
        self.message = message

    def SerializeToString(self) -> bytes:
        import json
        return json.dumps({"success": self.success, "message": self.message}).encode()

    @staticmethod
    def FromString(data: bytes) -> 'StoreResponse':
        import json
        obj = json.loads(data.decode())
        return StoreResponse(success=obj["success"], message=obj["message"])


class SolveRequest:
    """SolveRequest message."""
    def __init__(self, graph_id: str = "", start_id: str = "", goal_id: str = "", use_asm: bool = False):
        self.graph_id = graph_id
        self.start_id = start_id
        self.goal_id = goal_id
        self.use_asm = use_asm

    def SerializeToString(self) -> bytes:
        import json
        return json.dumps({
            "graph_id": self.graph_id,
            "start_id": self.start_id,
            "goal_id": self.goal_id,
            "use_asm": self.use_asm
        }).encode()

    @staticmethod
    def FromString(data: bytes) -> 'SolveRequest':
        import json
        obj = json.loads(data.decode())
        return SolveRequest(graph_id=obj["graph_id"], start_id=obj["start_id"], 
                           goal_id=obj["goal_id"], use_asm=obj["use_asm"])


class SolveResponse:
    """SolveResponse message."""
    def __init__(self, found: bool = False, path: list = None, total_cost: float = 0.0, nodes_explored: int = 0):
        self.found = found
        self.path = path or []
        self.total_cost = total_cost
        self.nodes_explored = nodes_explored

    def SerializeToString(self) -> bytes:
        import json
        return json.dumps({
            "found": self.found,
            "path": self.path,
            "total_cost": self.total_cost,
            "nodes_explored": self.nodes_explored
        }).encode()

    @staticmethod
    def FromString(data: bytes) -> 'SolveResponse':
        import json
        obj = json.loads(data.decode())
        return SolveResponse(found=obj["found"], path=obj["path"], 
                            total_cost=obj["total_cost"], nodes_explored=obj["nodes_explored"])
