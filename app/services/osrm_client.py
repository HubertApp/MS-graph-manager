import logging

from typing import List, Optional, Tuple

import httpx
from app.config import OSRM_BASE_URL

logger = logging.getLogger(__name__)


async def fetch_route(from_lat, from_lon, to_lat, to_lon):
    # "driving" here is hardcoded on purpose: the OSRM backend documented in the
    # README is extracted with car.lua only, and osrm-routed ignores the URL
    # profile segment at request time (it's determined by the dataset the
    # server was started with). Requesting another profile would either 404
    # or silently still route as a car, so we don't pretend it's selectable.
    url = (
        f"{OSRM_BASE_URL}/route/v1/driving/"
        f"{from_lon},{from_lat};{to_lon},{to_lat}"
        f"?overview=full&geometries=geojson&steps=true"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
    except httpx.HTTPError:
        logger.warning("OSRM unreachable at %s", OSRM_BASE_URL, exc_info=True)
        return None

    if response.status_code != 200:
        return None

    data = response.json()
    if not data.get("routes"):
        return None

    return data["routes"][0]


def route_polyline(route: dict) -> Tuple[List[List[float]], List[Optional[str]]]:
    """Trace d'une route OSRM et nom de voie de chaque segment.

    OSRM renvoie (steps=true) une etape par troncon homogene, avec sa
    geometrie et le nom de la voie. On les enchaine pour que chaque segment
    du trace porte le nom de sa rue ; sans etapes, on se rabat sur la
    geometrie globale, sans noms.

    Returns:
        (coordonnees [[lon, lat], ...], noms) avec len(noms) == len(coordonnees) - 1.
    """
    coordinates: List[List[float]] = []
    names: List[Optional[str]] = []
    for leg in route.get("legs") or []:
        for step in leg.get("steps") or []:
            points = ((step.get("geometry") or {}).get("coordinates")) or []
            name = (step.get("name") or "").strip() or None
            for point in points:
                point = [float(point[0]), float(point[1])]
                if coordinates and coordinates[-1] == point:
                    continue
                if coordinates:
                    names.append(name)
                coordinates.append(point)

    if len(coordinates) < 2:
        coordinates = [
            [float(lon), float(lat)]
            for lon, lat in ((route.get("geometry") or {}).get("coordinates") or [])
        ]
        names = [None] * max(0, len(coordinates) - 1)
    return coordinates, names
