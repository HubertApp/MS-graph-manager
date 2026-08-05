"""DTOs for itinerary management and multilayer graph routing."""
from datetime import datetime
from typing import List, Optional

import strawberry

from app.models.route import Geometry


@strawberry.input
class CoordinateInput:
    """Input for a single coordinate point."""
    lat: float
    lon: float


@strawberry.input
class RouteRequestDTO:
    """Main request DTO for route computation."""
    start_point: CoordinateInput
    end_point: CoordinateInput
    departure_time: datetime
    routing_profile: str = "driving"


@strawberry.input
class FrictionUpdateDTO:
    """Real-time friction/impedance update for a tile."""
    tile_id: str
    friction_coefficient: float
    source_event: str


@strawberry.type
class NodeDTO:
    """Graph node representation."""
    id: str  # Traceable id, e.g. "osm:123456" or "gtfs:stop_42"
    lat: float
    lon: float
    is_transit_stop: bool


@strawberry.type
class EdgeDTO:
    """Graph edge with multilayer support."""
    edge_id: str  # Unique identifier (e.g., "osm_12345" or "gtfs_line_5_seg_2")
    source_id: str  # Matches the source NodeDTO.id
    target_id: str  # Matches the target NodeDTO.id
    weight: float
    length: float
    layer: int


@strawberry.type
class RoutingGraphSnapshot:
    """Complete snapshot of the routing graph at request time."""
    nodes: List[NodeDTO]
    edges: List[EdgeDTO]


@strawberry.type
class PathSegmentDTO:
    """Detailed segment along the itinerary."""
    type: str
    osm_ids: List[int]
    transit_line_id: Optional[str] = None
    expected_arrival: Optional[datetime] = None


@strawberry.type
class FrontStepDTO:
    """Front-friendly step instruction."""
    instruction: str
    distance_m: float
    duration_s: float


@strawberry.type
class TrafficInfoDTO:
    """Real-time and predictive traffic factors."""
    realtime_factor: float  # 1.0 = no congestion, >1.0 = slower
    predictive_factor: float  # ML-based prediction factor
    source: str  # Source of the factors (e.g. "traffic+predictive")


@strawberry.type
class ItineraryResultDTO:
    """Complete itinerary result for the front."""
    distance_m: float
    duration_s: float
    geometry: Geometry
    steps: List[FrontStepDTO]
    segments: List[PathSegmentDTO]
    graph_snapshot: RoutingGraphSnapshot
    traffic: TrafficInfoDTO
