"""Instructions pas-a-pas en francais, a partir des aretes du chemin retenu.

Avant, le front recevait une "instruction" par arete OSM : le nom de la rue,
repete autant de fois qu'il y a de carrefours, et aucune direction. On regroupe
maintenant les aretes en etapes (une par voie empruntee, un rond-point, une
montee en vehicule...) et on calcule a chaque changement la manoeuvre a
effectuer, a partir de l'angle entre la direction d'arrivee et la direction de
depart au carrefour.

Le vocabulaire `maneuver`/`modifier` reprend celui de l'API Directions de
Mapbox, auquel s'ajoutent trois manoeuvres propres aux transports en commun :
`walk to stop`, `board` et `alight`.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from app.models.itinerary import FrontStepDTO
from app.services.routing_graph import LAYER_TRANSIT, PathEdge, haversine_m

MIN_STEP_M = 15.0  # troncon sans nom plus court : rattache a l'etape suivante
BEARING_SAMPLE_M = 20.0  # distance d'echantillonnage autour d'un carrefour
STRAIGHT_DEG = 20.0
SLIGHT_DEG = 60.0
TURN_DEG = 135.0
SHARP_DEG = 170.0
SAME_ROAD_TURN_DEG = 55.0  # virage marque sur une meme voie => nouvelle etape

PROFILE_MODE = {
    "walking": "walking",
    "cycling": "cycling",
    "driving": "driving",
    "transit": "walking",
    "multimodal": "walking",
}

_CARDINALS = (
    "le nord", "le nord-est", "l'est", "le sud-est",
    "le sud", "le sud-ouest", "l'ouest", "le nord-ouest",
)

_TURN_TEXT = {
    "slight right": "légèrement à droite",
    "right": "à droite",
    "sharp right": "franchement à droite",
    "slight left": "légèrement à gauche",
    "left": "à gauche",
    "sharp left": "franchement à gauche",
}

# Voies sans nom : on decrit leur nature plutot que de ne rien dire.
_UNNAMED_LABEL = {
    "footway": "le chemin piéton",
    "pedestrian": "la zone piétonne",
    "path": "le sentier",
    "steps": "l'escalier",
    "cycleway": "la piste cyclable",
    "track": "le chemin",
    "bridleway": "le chemin",
    "service": "la voie de desserte",
    "living_street": "la zone de rencontre",
    "corridor": "le couloir",
    "motorway_link": "la bretelle",
    "trunk_link": "la bretelle",
    "primary_link": "la bretelle",
    "secondary_link": "la bretelle",
    "tertiary_link": "la bretelle",
}

_VEHICLE = {
    "tram": "le tram",
    "metro": "le métro",
    "train": "le train",
    "bus": "le bus",
    "trolleybus": "le trolleybus",
    "ferry": "le bateau",
    "funicular": "le funiculaire",
    "aerial": "le téléphérique",
    "cable": "le tramway à câble",
    "monorail": "le monorail",
}


# ── Geometrie ───────────────────────────────────────────────────────────────

def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Cap initial de (lon1, lat1) vers (lon2, lat2), en degres [0, 360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    x = math.sin(d_lambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def turn_angle(bearing_before: float, bearing_after: float) -> float:
    """Angle signe dans ]-180, 180] : positif = a droite."""
    angle = (bearing_after - bearing_before + 540.0) % 360.0 - 180.0
    return 180.0 if angle == -180.0 else angle


def modifier_for(angle: float) -> str:
    magnitude = abs(angle)
    if magnitude <= STRAIGHT_DEG:
        return "straight"
    if magnitude >= SHARP_DEG:
        return "uturn"
    side = "right" if angle > 0 else "left"
    if magnitude <= SLIGHT_DEG:
        return f"slight {side}"
    if magnitude <= TURN_DEG:
        return side
    return f"sharp {side}"


def cardinal(bearing: float) -> str:
    return _CARDINALS[int(((bearing % 360.0) + 22.5) // 45.0) % 8]


def _point_along(
    coords: Sequence[Sequence[float]], index: int, direction: int, distance_m: float
) -> Optional[Sequence[float]]:
    """Point situe a ~distance_m de coords[index] en suivant le trace."""
    origin = coords[index]
    current = origin
    travelled = 0.0
    position = index
    while 0 <= position + direction < len(coords):
        following = coords[position + direction]
        travelled += haversine_m(current[0], current[1], following[0], following[1])
        current = following
        position += direction
        if travelled >= distance_m:
            break
    if travelled <= 0.0:
        return None
    return current


def _bearing_after(coords, index: int) -> Optional[float]:
    ahead = _point_along(coords, index, +1, BEARING_SAMPLE_M)
    if ahead is None:
        return None
    return bearing_deg(coords[index][0], coords[index][1], ahead[0], ahead[1])


def _bearing_before(coords, index: int) -> Optional[float]:
    behind = _point_along(coords, index, -1, BEARING_SAMPLE_M)
    if behind is None:
        return None
    return bearing_deg(behind[0], behind[1], coords[index][0], coords[index][1])


def concat_geometry(edges: Sequence[PathEdge]) -> Tuple[List[List[float]], List[Tuple[int, int]]]:
    """Trace complet [[lon, lat], ...] et, pour chaque arete, (debut, fin) dans ce trace."""
    coords: List[List[float]] = []
    ranges: List[Tuple[int, int]] = []
    for edge in edges:
        points = [[float(lon), float(lat)] for lon, lat in edge.coordinates]
        if not points:
            ranges.append((max(0, len(coords) - 1), max(0, len(coords) - 1)))
            continue
        if coords and points[0] == coords[-1]:
            start = len(coords) - 1
            coords.extend(points[1:])
        else:
            start = len(coords)
            coords.extend(points)
        ranges.append((start, len(coords) - 1))
    return coords, ranges


# ── Nature des aretes ───────────────────────────────────────────────────────

def edge_kind(edge: PathEdge) -> str:
    source, target = edge.source_id, edge.target_id
    source_ride, target_ride = source.startswith("gtfs:ride:"), target.startswith("gtfs:ride:")
    source_stop, target_stop = source.startswith("gtfs:stop:"), target.startswith("gtfs:stop:")
    if source_ride and target_ride:
        return "ride"
    if source_stop and target_ride:
        return "board"
    if source_ride and target_stop:
        return "alight"
    if target_stop:
        return "access"
    if source_stop:
        return "egress"
    if edge.layer == LAYER_TRANSIT:
        return "ride"
    return "road"


def road_label(edge: PathEdge) -> Optional[str]:
    """Nom a afficher : nom de voie, sinon reference (D953...), sinon nature."""
    if edge.name:
        return edge.name
    if edge.ref:
        return edge.ref
    return _UNNAMED_LABEL.get(edge.highway or "")


def _road_key(edge: PathEdge) -> str:
    if edge.name:
        return "name:" + edge.name.casefold()
    if edge.ref:
        return "ref:" + edge.ref.casefold()
    highway = edge.highway or ""
    return "kind:" + (highway if highway in _UNNAMED_LABEL else "road")


def line_type(route_type: Optional[int]) -> Optional[str]:
    """Type de vehicule d'apres route_type GTFS (types de base et etendus)."""
    if route_type is None:
        return None
    base = {
        0: "tram", 1: "metro", 2: "train", 3: "bus", 4: "ferry", 5: "cable",
        6: "aerial", 7: "funicular", 11: "trolleybus", 12: "monorail",
    }
    if route_type in base:
        return base[route_type]
    for low, high, kind in (
        (100, 200, "train"), (200, 300, "bus"), (400, 500, "metro"),
        (700, 800, "bus"), (800, 900, "trolleybus"), (900, 1000, "tram"),
        (1000, 1100, "ferry"), (1200, 1300, "ferry"), (1300, 1400, "aerial"),
        (1400, 1500, "funicular"),
    ):
        if low <= route_type < high:
            return kind
    return None


def _ordinal(number: int) -> str:
    return "1re" if number == 1 else f"{number}e"


def _on(label: Optional[str]) -> str:
    return f" sur {label}" if label else ""


def _minutes(seconds: float) -> int:
    return max(1, int(round(seconds / 60.0)))


# ── Regroupement des aretes en etapes ───────────────────────────────────────

@dataclass
class _Group:
    kind: str  # road | roundabout | walk_to_stop | ride | alight
    edges: List[PathEdge] = field(default_factory=list)
    ranges: List[Tuple[int, int]] = field(default_factory=list)
    key: Optional[str] = None
    after_transit: bool = False
    split_turn: bool = False

    def add(self, edge: PathEdge, span: Tuple[int, int]) -> None:
        self.edges.append(edge)
        self.ranges.append(span)

    def absorb(self, other: "_Group", before: bool = False) -> None:
        if before:
            self.edges = other.edges + self.edges
            self.ranges = other.ranges + self.ranges
        else:
            self.edges.extend(other.edges)
            self.ranges.extend(other.ranges)

    @property
    def distance(self) -> float:
        return sum(edge.length for edge in self.edges)

    @property
    def duration(self) -> float:
        return sum(edge.weight for edge in self.edges)

    @property
    def start_index(self) -> int:
        return self.ranges[0][0]

    @property
    def end_index(self) -> int:
        return self.ranges[-1][1]

    @property
    def label(self) -> Optional[str]:
        """Voie nommee du groupe ; a defaut, la nature de la premiere voie."""
        roads = [edge for edge in self.edges if edge_kind(edge) == "road"]
        named = next((edge for edge in roads if edge.name or edge.ref), None)
        if named is not None:
            return road_label(named)
        return road_label(roads[0]) if roads else None

    @property
    def first_road_index(self) -> int:
        """Debut de la premiere arete de voirie (apres une liaison depuis un arret)."""
        for edge, span in zip(self.edges, self.ranges):
            if edge_kind(edge) == "road":
                return span[0]
        return self.start_index

    @property
    def is_named(self) -> bool:
        return any(edge.name or edge.ref for edge in self.edges if edge_kind(edge) == "road")


def _junction_angle(coords, index: int) -> Optional[float]:
    before, after = _bearing_before(coords, index), _bearing_after(coords, index)
    if before is None or after is None:
        return None
    return turn_angle(before, after)


def _group_edges(edges, coords, ranges, roundabouts: bool = True) -> List[_Group]:
    """Regroupe les aretes en etapes. `roundabouts=False` (a pied) : un
    rond-point se decrit comme une rue ordinaire, un pieton ne "prend pas la
    2e sortie", il suit le trottoir."""
    groups: List[_Group] = []
    for edge, span in zip(edges, ranges):
        kind = edge_kind(edge)
        last = groups[-1] if groups else None

        if kind == "egress":
            # La marche reprend apres une descente : la voie suivante fixera la cle.
            groups.append(_Group("road", [edge], [span], key=None, after_transit=True))
            continue

        if kind == "road":
            if edge.roundabout and roundabouts:
                if last is not None and last.kind == "roundabout":
                    last.add(edge, span)
                else:
                    groups.append(_Group("roundabout", [edge], [span]))
                continue
            key = _road_key(edge)
            if last is not None and last.kind == "road":
                if last.key is None:
                    last.key = key
                    last.add(edge, span)
                    continue
                if last.key == key:
                    angle = _junction_angle(coords, span[0])
                    # Un rond-point decrit comme une rue tourne en permanence :
                    # ses carrefours ne justifient pas une nouvelle etape.
                    marked = (
                        not edge.roundabout
                        and angle is not None
                        and abs(angle) >= SAME_ROAD_TURN_DEG
                        and edge.junction_degree >= 3
                    )
                    if not marked:
                        last.add(edge, span)
                        continue
                    groups.append(_Group("road", [edge], [span], key=key, split_turn=True))
                    continue
            groups.append(_Group("road", [edge], [span], key=key))
            continue

        if kind == "access":
            groups.append(_Group("walk_to_stop", [edge], [span]))
        elif kind == "board":
            groups.append(_Group("ride", [edge], [span], key=edge.transit_line_id))
        elif kind == "ride":
            if last is not None and last.kind == "ride" and last.key == edge.transit_line_id:
                last.add(edge, span)
            else:
                groups.append(_Group("ride", [edge], [span], key=edge.transit_line_id))
        elif kind == "alight":
            groups.append(_Group("alight", [edge], [span]))
    return groups


def _tidy(groups: List[_Group], coords) -> List[_Group]:
    """Rattache les micro-troncons sans nom a l'etape suivante, puis refusionne
    les etapes consecutives d'une meme voie sans virage marque."""
    absorbed: List[_Group] = []
    index = 0
    while index < len(groups):
        group = groups[index]
        following = groups[index + 1] if index + 1 < len(groups) else None
        if (
            group.kind == "road"
            and absorbed
            and not group.after_transit
            and not group.is_named
            and group.distance < MIN_STEP_M
            and following is not None
            and following.kind == "road"
        ):
            following.absorb(group, before=True)
            following.after_transit = following.after_transit or group.after_transit
            index += 1
            continue
        absorbed.append(group)
        index += 1

    merged: List[_Group] = []
    for group in absorbed:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.kind == "road"
            and group.kind == "road"
            and not group.after_transit
            and previous.key is not None
            and previous.key == group.key
        ):
            angle = _junction_angle(coords, group.start_index)
            if angle is None or abs(angle) < SAME_ROAD_TURN_DEG:
                previous.absorb(group)
                continue
        merged.append(group)
    return merged


# ── Production des etapes ───────────────────────────────────────────────────

def _step(group: _Group, **values) -> FrontStepDTO:
    values.setdefault("distance_m", group.distance)
    values.setdefault("duration_s", group.duration)
    values.setdefault("geometry_start", group.start_index)
    values.setdefault("geometry_end", group.end_index)
    return FrontStepDTO(**values)


def _road_step(group: _Group, coords, mode: str, is_first: bool) -> FrontStepDTO:
    index = group.start_index
    location = list(coords[index])
    label = group.label
    after = _bearing_after(coords, index)

    if is_first or group.after_transit:
        if group.after_transit:
            # En descendant du vehicule, on rejoint d'abord la rue : c'est sa
            # direction a elle qu'on annonce.
            after = _bearing_after(coords, group.first_road_index)
        verb = "Partez" if is_first else "Marchez"
        direction = f" vers {cardinal(after)}" if after is not None else ""
        return _step(
            group,
            instruction=f"{verb}{direction}{_on(label)}",
            maneuver="depart",
            street_name=label,
            mode=mode,
            location=location,
            bearing_after=after,
        )

    before = _bearing_before(coords, index)
    angle = turn_angle(before, after) if before is not None and after is not None else 0.0
    modifier = modifier_for(angle)
    if modifier == "straight":
        instruction = f"Continuez tout droit{_on(label)}"
        maneuver = "new name"
    elif modifier == "uturn":
        instruction = f"Faites demi-tour{_on(label)}"
        maneuver = "turn"
    elif group.split_turn and label:
        instruction = f"Tournez {_TURN_TEXT[modifier]} pour rester sur {label}"
        maneuver = "turn"
    else:
        instruction = f"Tournez {_TURN_TEXT[modifier]}{_on(label)}"
        maneuver = "turn"
    return _step(
        group,
        instruction=instruction,
        maneuver=maneuver,
        modifier=modifier,
        street_name=label,
        mode=mode,
        location=location,
        bearing_before=before,
        bearing_after=after,
    )


def _roundabout_step(
    group: _Group, exit_group: Optional[_Group], coords, mode: str
) -> FrontStepDTO:
    index = group.start_index
    exits_known = all(edge.source_exits is not None for edge in group.edges[1:])
    exit_number = 1 + sum(1 for edge in group.edges[1:] if (edge.source_exits or 0) > 0)
    label = exit_group.label if exit_group is not None else None

    before = _bearing_before(coords, index)
    exit_index = exit_group.start_index if exit_group is not None else group.end_index
    after = _bearing_after(coords, exit_index)
    modifier = None
    if before is not None and after is not None:
        modifier = modifier_for(turn_angle(before, after))

    if exit_group is None:
        instruction = "Entrez dans le rond-point"
    elif exits_known:
        instruction = f"Au rond-point, prenez la {_ordinal(exit_number)} sortie{_on(label)}"
    else:
        instruction = f"Au rond-point, prenez la sortie{_on(label)}"

    distance = group.distance + (exit_group.distance if exit_group else 0.0)
    duration = group.duration + (exit_group.duration if exit_group else 0.0)
    return FrontStepDTO(
        instruction=instruction,
        distance_m=distance,
        duration_s=duration,
        maneuver="roundabout",
        modifier=modifier,
        street_name=label,
        mode=mode,
        location=list(coords[index]),
        bearing_before=before,
        bearing_after=_bearing_after(coords, index),
        geometry_start=index,
        geometry_end=exit_group.end_index if exit_group is not None else group.end_index,
        detail=f"{_ordinal(exit_number)} sortie" if exit_group is not None and exits_known else None,
    )


def css_color(value: Optional[str]) -> Optional[str]:
    """route_color GTFS ("E2001A") -> couleur CSS ("#E2001A") ; None si invalide."""
    if not value:
        return None
    text = value.strip().lstrip("#")
    if len(text) not in (3, 6) or any(char not in "0123456789abcdefABCDEF" for char in text):
        return None
    return f"#{text.upper()}"


def _line_fields(edges: Sequence[PathEdge]) -> dict:
    reference = next((e for e in edges if edge_kind(e) == "ride"), edges[0])
    return {
        "transit_line_id": reference.transit_line_id,
        "line_name": reference.route_short_name,
        "line_color": css_color(reference.route_color),
        "line_type": line_type(reference.route_type),
        "headsign": reference.headsign,
    }


def _ride_step(group: _Group, coords) -> FrontStepDTO:
    fields_ = _line_fields(group.edges)
    board = group.edges[0] if edge_kind(group.edges[0]) == "board" else None
    stop_count = sum(1 for edge in group.edges if edge_kind(edge) == "ride")
    vehicle = _VEHICLE.get(fields_["line_type"] or "", "la ligne")

    if fields_["line_name"]:
        instruction = f"Prenez {vehicle} {fields_['line_name']}"
        if fields_["headsign"]:
            instruction += f" direction {fields_['headsign']}"
    else:
        # Nom fabrique par l'agregateur : "Ligne 5 vers Gare" -> "la ligne 5 vers Gare".
        name = next((edge.name for edge in group.edges if edge.name), None)
        if name and name.lower().startswith("ligne "):
            instruction = f"Prenez la l{name[1:]}"
        else:
            instruction = f"Prenez {name}" if name else "Prenez la ligne"

    details = [f"{stop_count} arrêt{'s' if stop_count > 1 else ''}"]
    if board is not None and board.weight > 0:
        details.append(f"attente estimée ~{_minutes(board.weight)} min")
    stop_name = board.source_name if board is not None else group.edges[0].source_name
    if stop_name:
        details.insert(0, f"depuis {stop_name}")

    return _step(
        group,
        instruction=instruction,
        maneuver="board",
        mode="transit",
        location=list(coords[group.start_index]),
        detail=" · ".join(details),
        stop_count=stop_count,
        **fields_,
    )


def build_instructions(
    edges: Sequence[PathEdge], routing_profile: str = "driving"
) -> Tuple[List[List[float]], List[FrontStepDTO]]:
    """Renvoie (trace [[lon, lat], ...], etapes). La derniere etape est l'arrivee.

    Invariant : la somme des durees (et des distances) des etapes egale celle
    des aretes.
    """
    coords, ranges = concat_geometry(edges)
    if not edges or not coords:
        return coords, []

    road_mode = PROFILE_MODE.get(routing_profile.lower(), "driving")
    groups = _tidy(
        _group_edges(edges, coords, ranges, roundabouts=road_mode != "walking"), coords
    )

    steps: List[FrontStepDTO] = []
    index = 0
    while index < len(groups):
        group = groups[index]
        if group.kind == "road":
            steps.append(_road_step(group, coords, road_mode, is_first=not steps))
        elif group.kind == "roundabout":
            following = groups[index + 1] if index + 1 < len(groups) else None
            exit_group = following if following is not None and following.kind == "road" else None
            steps.append(_roundabout_step(group, exit_group, coords, road_mode))
            if exit_group is not None:
                index += 1
        elif group.kind == "walk_to_stop":
            stop_name = group.edges[-1].target_name
            steps.append(_step(
                group,
                instruction=f"Rejoignez l'arrêt {stop_name}" if stop_name else "Rejoignez l'arrêt",
                maneuver="walk to stop",
                street_name=stop_name,
                mode="walking",
                location=list(coords[group.start_index]),
                bearing_after=_bearing_after(coords, group.start_index),
            ))
        elif group.kind == "ride":
            steps.append(_ride_step(group, coords))
        elif group.kind == "alight":
            stop_name = group.edges[-1].target_name
            steps.append(_step(
                group,
                instruction=f"Descendez à l'arrêt {stop_name}" if stop_name else "Descendez à l'arrêt",
                maneuver="alight",
                street_name=stop_name,
                mode="transit",
                location=list(coords[group.end_index]),
                transit_line_id=group.edges[-1].transit_line_id,
            ))
        index += 1

    last_index = len(coords) - 1
    steps.append(FrontStepDTO(
        instruction="Vous êtes arrivé à destination",
        distance_m=0.0,
        duration_s=0.0,
        maneuver="arrive",
        mode=steps[-1].mode if steps else road_mode,
        location=list(coords[last_index]),
        bearing_before=_bearing_before(coords, last_index),
        geometry_start=last_index,
        geometry_end=last_index,
    ))
    return coords, steps
