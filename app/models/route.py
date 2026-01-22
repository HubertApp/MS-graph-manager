import strawberry
from typing import List, Optional


@strawberry.type
class Step:
    distance: float
    duration: float
    name: Optional[str]


@strawberry.type
class Geometry:
    coordinates: List[List[float]] 
    type: str


@strawberry.type
class Route:
    distance_m: float
    duration_s: float
    geometry: Geometry
    steps: List[Step]
