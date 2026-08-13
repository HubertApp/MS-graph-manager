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


# Nommé "ComputedRoute" (pas "Route") : collision de composition fédérée
# avec le type Route (ligne de transport GTFS) de service-aom-agregator —
# même nom, aucun champ commun, pas de @key, donc federation ne peut pas les
# fusionner.
@strawberry.type(name="ComputedRoute")
class Route:
    distance_m: float
    duration_s: float
    geometry: Geometry
    steps: List[Step]
