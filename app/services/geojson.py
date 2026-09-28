from typing import Any, Dict, List

from app.services.graph_engine import RenderedPath

GeoJSON = Dict[str, Any]


def _bbox(coordinates: List[List[float]]) -> List[float]:
    lons = [lon for lon, _ in coordinates]
    lats = [lat for _, lat in coordinates]
    return [min(lons), min(lats), max(lons), max(lats)]


def _point(coordinates: List[float], properties: Dict[str, Any]) -> GeoJSON:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coordinates},
        "properties": properties,
    }


def rendered_path_to_geojson(rendered: RenderedPath, routing_profile: str) -> GeoJSON:
    coordinates = rendered.geometry_coordinates 

    if len(coordinates) < 2:
        return {"type": "FeatureCollection", "features": []}

    route = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "properties": {
            "kind": "route",
            "profile": routing_profile,
            "distance_m": rendered.distance_m,
            "duration_s": rendered.duration_s,
        },
    }

    steps = [
        _point(coordinates[index], {
            "kind": "step",
            "step_index": index,
            "instruction": step.instruction,
            "distance_m": step.distance_m,
            "duration_s": step.duration_s,
        })
        for index, step in enumerate(rendered.front_steps)
    ]

    return {
        "type": "FeatureCollection",
        "bbox": _bbox(coordinates),
        "features": [
            route,
            *steps,
            _point(coordinates[0], {"kind": "start"}),
            _point(coordinates[-1], {"kind": "end"}),
        ],
    }