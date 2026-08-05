"""Generated from astar.proto - gRPC service stubs (manually created)."""
import grpc
from . import astar_pb2


class AStarServiceStub:
    """Stub for AStarService gRPC."""

    def __init__(self, channel: grpc.Channel):
        self.StoreGraph = channel.unary_unary(
            '/astar.proto.AStarService/StoreGraph',
            request_serializer=astar_pb2.GraphRequest.SerializeToString,
            response_deserializer=astar_pb2.StoreResponse.FromString,
        )
        self.Solve = channel.unary_unary(
            '/astar.proto.AStarService/Solve',
            request_serializer=astar_pb2.SolveRequest.SerializeToString,
            response_deserializer=astar_pb2.SolveResponse.FromString,
        )


class AStarServiceServicer:
    """Base class for AStarService servicer."""

    def StoreGraph(self, request: astar_pb2.GraphRequest, context: grpc.ServicerContext):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def Solve(self, request: astar_pb2.SolveRequest, context: grpc.ServicerContext):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_AStarServiceServicer_to_server(servicer, server: grpc.Server):
    rpc_method_handlers = {
        'StoreGraph': grpc.unary_unary_rpc_method_handler(
            servicer.StoreGraph,
            request_deserializer=astar_pb2.GraphRequest.FromString,
            response_serializer=astar_pb2.StoreResponse.SerializeToString,
        ),
        'Solve': grpc.unary_unary_rpc_method_handler(
            servicer.Solve,
            request_deserializer=astar_pb2.SolveRequest.FromString,
            response_serializer=astar_pb2.SolveResponse.SerializeToString,
        ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
        'astar.proto.AStarService', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))
