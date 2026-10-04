"""Client gRPC du routeur A* C++ (MS-itinerary-creator)."""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

import grpc

from app.config import (
    CPP_ROUTER_GRPC_TARGET,
    CPP_ROUTER_MAX_MESSAGE_MB,
    CPP_ROUTER_TIMEOUT_S,
)
from app.proto import astar_pb2_grpc
from app.services.routing_graph import RoutingGraph, as_routing_graph

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
    graph: RoutingGraph,
    start_node_id: str,
    end_node_id: str,
) -> Optional[RoutingResult]:
    """Enveloppe async : la construction du message et l'appel gRPC bloquant
    partent sur un thread dedie, la boucle d'evenements reste libre."""
    return await asyncio.to_thread(
        _solve_with_cpp_sync, graph, start_node_id, end_node_id
    )


def _solve_with_cpp_sync(
    graph,
    start_node_id: str,
    end_node_id: str,
) -> Optional[RoutingResult]:
    graph = as_routing_graph(graph)
    if graph.is_empty():
        logger.warning("snapshot vide : rien a resoudre")
        return None

    if not graph.has_node(start_node_id) or not graph.has_node(end_node_id):
        logger.warning(
            "noeuds de depart/arrivee absents du snapshot (start=%s end=%s)",
            start_node_id,
            end_node_id,
        )
        return None

    started = time.perf_counter()
    request = graph.build_solve_request(start_node_id, end_node_id)
    built = time.perf_counter()

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
        "chemin trouve : %d noeuds, cout=%.1fs, explores=%d "
        "(message %d noeuds/%d aretes construit en %.0f ms, Solve %.0f ms)",
        len(response.path),
        response.total_cost,
        response.nodes_explored,
        len(request.nodes),
        len(request.edges),
        (built - started) * 1000,
        (time.perf_counter() - built) * 1000,
    )
    return RoutingResult(
        node_path=list(response.path),
        total_cost=response.total_cost,
        nodes_explored=response.nodes_explored,
    )