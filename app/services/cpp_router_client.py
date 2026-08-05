"""gRPC client for C++ A* router service on localhost:50051."""
import asyncio
import logging
from typing import List, Optional

import grpc

from app.models.itinerary import RoutingGraphSnapshot
from app.proto import astar_pb2, astar_pb2_grpc

logger = logging.getLogger(__name__)

GRPC_ROUTER_HOST = "localhost"
GRPC_ROUTER_PORT = 50051


async def compute_itinerary_with_cpp(
    snapshot: RoutingGraphSnapshot,
    start_node_id: int,
    end_node_id: int,
    graph_id: str = "default",
) -> Optional[List[str]]:
    """Async wrapper: runs the blocking gRPC call off the event loop."""
    return await asyncio.to_thread(
        _compute_itinerary_with_cpp_sync, snapshot, start_node_id, end_node_id, graph_id
    )


def _compute_itinerary_with_cpp_sync(
    snapshot: RoutingGraphSnapshot,
    start_node_id: int,
    end_node_id: int,
    graph_id: str = "default",
) -> Optional[List[str]]:
    """
    Send graph to C++ A* service via gRPC and solve.
    
    Args:
        snapshot: RoutingGraphSnapshot with nodes and edges
        start_node_id: index of start node in snapshot.nodes
        end_node_id: index of end node in snapshot.nodes
        graph_id: unique graph identifier
    
    Returns:
        List of edge IDs on the path, or None if routing fails
    """
    if not snapshot.nodes or not snapshot.edges:
        logger.warning("Empty graph snapshot; cannot route")
        return None

    if start_node_id >= len(snapshot.nodes) or end_node_id >= len(snapshot.nodes):
        logger.warning("Invalid start/end node indices")
        return None

    try:
        # Create gRPC channel (insecure for localhost testing)
        channel = grpc.insecure_channel(f"{GRPC_ROUTER_HOST}:{GRPC_ROUTER_PORT}")
        stub = astar_pb2_grpc.AStarServiceStub(channel)

        # Convert snapshot nodes/edges to proto messages
        proto_nodes = [
            astar_pb2.Node(id=str(n.id), lat=n.lat, lon=n.lon)
            for n in snapshot.nodes
        ]
        proto_edges = [
            astar_pb2.Edge(from_id=str(e.source_id), to_id=str(e.target_id), weight=e.weight)
            for e in snapshot.edges
        ]

        # Store graph on C++ service
        store_req = astar_pb2.GraphRequest(graph_id=graph_id, nodes=proto_nodes, edges=proto_edges)
        store_resp = stub.StoreGraph(store_req)

        if not store_resp.success:
            logger.error(f"Failed to store graph: {store_resp.message}")
            channel.close()
            return None

        # Solve for shortest path
        solve_req = astar_pb2.SolveRequest(
            graph_id=graph_id,
            start_id=str(start_node_id),
            goal_id=str(end_node_id),
            use_asm=True  # Use ASM optimization if available
        )
        solve_resp = stub.Solve(solve_req)
        channel.close()

        if not solve_resp.found:
            logger.warning("No path found by C++ solver")
            return None

        # Map path (node IDs) back to edge IDs
        edge_ids = []
        for i in range(len(solve_resp.path) - 1):
            from_id = int(solve_resp.path[i])
            to_id = int(solve_resp.path[i + 1])

            # Find edge with matching source/target
            for edge in snapshot.edges:
                if edge.source_id == from_id and edge.target_id == to_id:
                    edge_ids.append(edge.edge_id)
                    break

        logger.info(f"Path found: {len(edge_ids)} edges, cost={solve_resp.total_cost}")
        return edge_ids

    except grpc.RpcError as e:
        logger.error(f"gRPC error: {e.code()}: {e.details()}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in C++ routing: {e}")
        return None
