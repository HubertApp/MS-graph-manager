"""Conversion d'un itineraire rendu en FeatureCollection GeoJSON pour Mapbox.

Chaque Feature porte `properties.kind` :
  - `route`     : LineString du trace complet ;
  - `step-line` : LineString du troncon d'une etape (surlignage, couleur de
                  ligne en transport en commun), alignee sur `steps` par
                  `properties.step_index` ;
  - `step`      : Point de la manoeuvre d'une etape, meme `step_index` ;
  - `start` / `end` : extremites.
Coordonnees [lon, lat]. La collection porte une `bbox`.
"""

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
        "geometry": {"type": "Point", "coordinates": list(coordinates)},
        "properties": properties,
    }


def _step_properties(index: int, step) -> Dict[str, Any]:
    properties = {
        "kind": "step",
        "step_index": index,
        "instruction": step.instruction,
        "distance_m": step.distance_m,
        "duration_s": step.duration_s,
        "maneuver": step.maneuver,
        "modifier": step.modifier,
        "mode": step.mode,
        "street_name": step.street_name,
        "bearing_after": step.bearing_after,
        "line_name": step.line_name,
        "line_color": step.line_color,
        "line_type": step.line_type,
    }
    # Mapbox refuse les proprietes null dans certaines expressions : on les omet.
    return {key: value for key, value in properties.items() if value is not None}


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

    step_lines = []
    step_points = []
    for index, step in enumerate(rendered.front_steps):
        start, end = step.geometry_start, step.geometry_end
        if start is not None and end is not None and 0 <= start < end < len(coordinates):
            line_properties = {
                "kind": "step-line",
                "step_index": index,
                "mode": step.mode,
                "maneuver": step.maneuver,
            }
            if step.line_color:
                line_properties["line_color"] = step.line_color
            if step.line_type:
                # Couleur par defaut cote front quand le GTFS n'en fournit pas
                line_properties["line_type"] = step.line_type
            step_lines.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coordinates[start:end + 1]},
                "properties": line_properties,
            })

        location = step.location
        if location is None and start is not None and 0 <= start < len(coordinates):
            location = coordinates[start]
        if location is not None:
            step_points.append(_point(location, _step_properties(index, step)))

    return {
        "type": "FeatureCollection",
        "bbox": _bbox(coordinates),
        "features": [
            route,
            *step_lines,
            *step_points,
            _point(coordinates[0], {"kind": "start"}),
            _point(coordinates[-1], {"kind": "end"}),
        ],
    }
