import strawberry
from typing import Optional
from app.services.osrm_client import fetch_route
from app.models.route import Route, Geometry, Step


@strawberry.type
class RouteQuery:

    @strawberry.field
    async def route(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float
    ) -> Optional[Route]:

        raw_route = await fetch_route(from_lat, from_lon, to_lat, to_lon)
        if not raw_route:
            return None

        leg = raw_route["legs"][0]

        steps = [
            Step(
                name=s.get("name"),
                distance=s["distance"],
                duration=s["duration"]
            )
            for s in leg["steps"]
        ]

        return Route(
            distance_m=raw_route["distance"],
            duration_s=raw_route["duration"],
            geometry=Geometry(
                type=raw_route["geometry"]["type"],
                coordinates=raw_route["geometry"]["coordinates"]
            ),
            steps=steps
        )
