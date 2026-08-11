"""gRPC client for C++ A* router service on localhost:50051."""

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

import grpc

from app.config import (
    CPP_ROUTER_GRPC_TARGET,
    CPP_ROUTER_MAX_MESSAGE_MB,
    CPP_ROUTER_TIMEOUT_S,
)
from app.models.itinerary import RoutingGraphSnapshot
from app.proto import astar_pb2, astar_pb2_grpc

logger = logging.getLogger(__name__)

_MB = 1024 * 1024


def _channel_options() -> list:
    """Releve la limite de taille des messages (4 Mo par defaut en gRPC)."""
    limit = CPP_ROUTER_MAX_MESSAGE_MB * _MB
    return [
        ("grpc.max_send_message_length", limit),
        ("grpc.max_receive_message_length", limit),
    ]


@dataclass(frozen=True)
class RoutingResult:
    """Resultat brut du solveur C++."""

    node_path: List[str]
    total_cost: float
    nodes_explored: int


async def solve_with_cpp(
    snapshot: RoutingGraphSnapshot,
    start_node_id: str,
    end_node_id: str,
) -> Optional[RoutingResult]:
    """Enveloppe async : l'appel gRPC bloquant part sur un thread dedie."""
    return await asyncio.to_thread(
        _solve_with_cpp_sync, snapshot, start_node_id, end_node_id
    )


def _solve_with_cpp_sync(
    snapshot: RoutingGraphSnapshot,
    start_node_id: str,
    end_node_id: str,
) -> Optional[RoutingResult]:
    if not snapshot.nodes or not snapshot.edges:
        logger.warning("snapshot vide : rien a resoudre")
        return None

    known_ids = {node.id for node in snapshot.nodes}
    if start_node_id not in known_ids or end_node_id not in known_ids:
        logger.warning(
            "noeuds de depart/arrivee absents du snapshot (start=%s end=%s)",
            start_node_id,
            end_node_id,
        )
        return None

    request = astar_pb2.SolveRequest(
        nodes=[
            astar_pb2.Node(id=node.id, lat=node.lat, lon=node.lon)
            for node in snapshot.nodes
        ],
        edges=[
            astar_pb2.Edge(
                from_id=edge.source_id, to_id=edge.target_id, weight=edge.weight
            )
            for edge in snapshot.edges
        ],
        start_id=start_node_id,
        goal_id=end_node_id,
    )

    try:
        with grpc.insecure_channel(
            CPP_ROUTER_GRPC_TARGET, options=_channel_options()
        ) as channel:
            stub = astar_pb2_grpc.AStarServiceStub(channel)
            response = stub.Solve(request, timeout=CPP_ROUTER_TIMEOUT_S)

    except grpc.RpcError as error:
        logger.error(
            "erreur gRPC %s vers %s : %s",
            error.code(),
            CPP_ROUTER_GRPC_TARGET,
            error.details(),
        )
        return None
    except Exception:
        logger.exception("erreur inattendue lors de l'appel au routeur C++")
        return None

    if not response.found:
        logger.warning(
            "aucun chemin entre %s et %s (%d noeuds explores)",
            start_node_id,
            end_node_id,
            response.nodes_explored,
        )
        return None

    logger.info(
        "chemin trouve : %d noeuds, cout=%.1fs, explores=%d",
        len(response.path),
        response.total_cost,
        response.nodes_explored,
    )
    return RoutingResult(
        node_path=list(response.path),
        total_cost=response.total_cost,
        nodes_explored=response.nodes_explored,
    )