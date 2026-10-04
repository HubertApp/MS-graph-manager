"""DTOs for itinerary management and multilayer graph routing."""
from datetime import datetime
from typing import List, Optional


import strawberry

from strawberry.scalars import JSON

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
    weight: float  # SECONDES - cout de traversee ajuste (contrat gRPC)
    length: float  # METRES - longueur physique
    layer: int
    name: Optional[str] = None  # nom de voie, sert aux instructions
    transit_line_id: Optional[str] = None  # route_id GTFS si l'arete est un trajet en transit


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
    """Instruction pas-a-pas, prete a afficher.

    Les trois premiers champs sont historiques ; les suivants sont facultatifs
    et suivent le vocabulaire de l'API Directions de Mapbox (maneuver/modifier)
    pour que le front puisse choisir une icone et centrer la carte.
    """
    instruction: str
    distance_m: float
    duration_s: float
    maneuver: str = strawberry.field(
        default="continue",
        description=(
            "depart | turn | new name | roundabout | arrive | "
            "walk to stop | board | alight"
        ),
    )
    modifier: Optional[str] = strawberry.field(
        default=None,
        description=(
            "straight | slight left | left | sharp left | slight right | "
            "right | sharp right | uturn"
        ),
    )
    street_name: Optional[str] = None
    mode: str = strawberry.field(
        default="driving", description="walking | cycling | driving | transit"
    )
    location: Optional[List[float]] = strawberry.field(
        default=None, description="Point de la manoeuvre, [lon, lat]"
    )
    bearing_before: Optional[float] = None
    bearing_after: Optional[float] = None
    geometry_start: Optional[int] = strawberry.field(
        default=None,
        description="Index dans geometry.coordinates du debut du troncon de l'etape",
    )
    geometry_end: Optional[int] = strawberry.field(
        default=None,
        description="Index dans geometry.coordinates de la fin du troncon de l'etape",
    )
    detail: Optional[str] = strawberry.field(
        default=None, description="Precision secondaire (arrets, attente...)"
    )
    transit_line_id: Optional[str] = None
    line_name: Optional[str] = None
    line_color: Optional[str] = None
    line_type: Optional[str] = strawberry.field(
        default=None, description="bus | tram | metro | train | ferry | ..."
    )
    headsign: Optional[str] = None
    stop_count: Optional[int] = None


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
    geojson: JSON = strawberry.field(
        description=(
            "Obliger de convertir les instructions en GeoJSON pour MapBox"
            "FeatureCollection GeoJSON, coordonnées [lon, lat]. "
            "Chaque Feature porte properties.kind : 'route' (LineString du tracé), "
            "'step' (Point de départ d'une instruction, aligné sur steps par "
            "properties.step_index), 'start' et 'end'. Inclut une bbox."
        )
    )