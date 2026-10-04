"""Graphe routier OpenStreetMap : cache regional + extraction par requete.

Pourquoi ce module a change
---------------------------
Avant, chaque demande d'itineraire appelait `ox.graph_from_bbox`, soit un
telechargement Overpass, la construction du graphe et sa simplification :
plusieurs secondes, parfois des dizaines quand Overpass est sature. L'appel
etait en plus synchrone dans un resolver async, si bien qu'il gelait la boucle
d'evenements et donc TOUTES les requetes en cours. Venait ensuite la
conversion de chaque noeud et de chaque arete en objet Strawberry, qui coutait
encore quelques centaines de millisecondes.

Fonctionnement actuel
---------------------
1. `RoadNetworkCache` : une *region* (une bbox arrondie et un type de reseau
   osmnx) est telechargee une seule fois, puis preparee en tableaux numpy
   compacts (`RoadRegion`). Elle est gardee en memoire (LRU) et persistee sur
   disque (pickle), si bien qu'un redemarrage ne retelecharge rien. Des
   regions peuvent etre prechargees au demarrage (OSMNX_PRELOAD_BBOXES).
2. Par requete (`build_osmnx_graph`, appele dans un thread) : on decoupe dans
   la region le sous-graphe de la bbox demandee, on calcule les poids de facon
   vectorisee, puis on accroche depart et arrivee a la plus grande composante
   fortement connexe grace a un KD-tree. Le resultat est un `OsmRoutingGraph`
   qui produit directement la requete gRPC, sans objets intermediaires.

Contrat inchange avec le C++ : `length` en METRES, `weight` en SECONDES,
identifiants de noeuds "osm:<id>".
"""

import logging
import math
import os
import pickle
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import cached_property
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.config import (
    OSMNX_CACHE_DIR,
    OSMNX_FAILURE_COOLDOWN_S,
    OSMNX_MEMORY_REGIONS,
    OSMNX_REGION_PADDING_M,
    OSMNX_REQUESTS_TIMEOUT_S,
)
from app.models.itinerary import EdgeDTO, NodeDTO, RoutingGraphSnapshot
from app.proto import astar_pb2
from app.services.routing_graph import (
    LAYER_ROAD,
    BBox,
    GraphEdge,
    GraphNode,
    PathEdge,
    RoutingGraph,
    extra_edge_to_path_edge,
    haversine_m,
)

logger = logging.getLogger(__name__)

Coordinate = Tuple[float, float]

PROFILE_TO_NETWORK_TYPE = {
    "walking": "walk",
    "cycling": "bike",
    "driving": "drive",
    # En transport en commun, la VOIRIE se parcourt a pied : ce sont les
    # aretes GTFS fusionnees (app/services/layer_merge.py) qui vont vite.
    "transit": "walk",
    "multimodal": "walk",
}

DEFAULT_PROFILE_SPEEDS_MPS = {
    "walking": 1.4,
    "cycling": 4.2,
    "driving": 13.9,
    "transit": 1.4,
    "multimodal": 1.4,
}

# Profils dont la vitesse suit la limitation de la voie. Les autres la
# prennent seulement comme plafond : on ne marche pas a 50 km/h parce que la
# rue est limitee a 50.
_SPEED_LIMITED_PROFILES = {"driving"}

_CACHE_FORMAT_VERSION = 1
_GRID_DEG = 0.01  # arrondi des regions : ~1,1 km en latitude
_M_PER_DEG = 111_320.0
_MPH_TO_KMH = 1.609344
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


# ── Utilitaires historiques (conserves : testes et reutilises) ──────────────

def _tile_id(coord: Coordinate) -> str:
    lon, lat = coord
    return f"{round(lat, 2)}:{round(lon, 2)}"


def _compute_bbox(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    margin_m: float,
) -> Tuple[float, float, float, float]:
    """Renvoie (nord, sud, est, ouest) : la bbox du trajet elargie de margin_m."""
    center_lat = (start_lat + end_lat) / 2.0
    margin_deg_lat = margin_m / _M_PER_DEG
    margin_deg_lon = margin_m / max(1.0, _M_PER_DEG * math.cos(math.radians(center_lat)))

    north = max(start_lat, end_lat) + margin_deg_lat
    south = min(start_lat, end_lat) - margin_deg_lat
    east = max(start_lon, end_lon) + margin_deg_lon
    west = min(start_lon, end_lon) - margin_deg_lon
    return north, south, east, west


def _parse_maxspeed_kmh(value) -> Optional[float]:
    """Lit un tag maxspeed OSM ("50", "30;50", "20 mph", ["50", "70"]...).

    Renvoie None pour les valeurs symboliques ("FR:urban", "none", "walk") :
    la vitesse par defaut du profil s'applique alors.
    """
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None if math.isnan(value) or value <= 0 else float(value)
    text = str(value).strip().lower()
    match = _NUMBER.search(text)
    if not match:
        return None
    speed = float(match.group(0).replace(",", "."))
    if "mph" in text:
        speed *= _MPH_TO_KMH
    return speed if speed > 0 else None


def _infer_speed_mps(edge_data: Dict, routing_profile: str) -> float:
    profile = routing_profile.lower()
    default = DEFAULT_PROFILE_SPEEDS_MPS.get(profile, DEFAULT_PROFILE_SPEEDS_MPS["driving"])
    speed_kmh = _parse_maxspeed_kmh(edge_data.get("maxspeed"))
    if speed_kmh is None:
        return default
    limit_mps = max(0.5, speed_kmh / 3.6)
    if profile in _SPEED_LIMITED_PROFILES or profile not in DEFAULT_PROFILE_SPEEDS_MPS:
        return limit_mps
    return min(default, limit_mps)


# ── Geometrie des bbox ──────────────────────────────────────────────────────

def expand_bbox(bbox: BBox, margin_m: float) -> BBox:
    west, south, east, north = bbox
    center_lat = (south + north) / 2.0
    d_lat = margin_m / _M_PER_DEG
    d_lon = margin_m / max(1.0, _M_PER_DEG * math.cos(math.radians(center_lat)))
    return west - d_lon, south - d_lat, east + d_lon, north + d_lat


def _snap_bbox_out(bbox: BBox, grid: float = _GRID_DEG) -> BBox:
    """Arrondit vers l'exterieur sur une grille : deux demandes voisines
    produisent la meme region, donc le meme fichier de cache."""
    west, south, east, north = bbox
    return (
        round(math.floor(west / grid) * grid, 6),
        round(math.floor(south / grid) * grid, 6),
        round(math.ceil(east / grid) * grid, 6),
        round(math.ceil(north / grid) * grid, 6),
    )


def _contains(outer: BBox, inner: BBox) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _first_text(value) -> Optional[str]:
    """Attribut OSM -> texte : premier element d'une liste, None si vide/NaN."""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _first_text(item)
            if text:
                return text
        return None
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


# ── Region preparee ─────────────────────────────────────────────────────────

@dataclass
class RoadRegion:
    """Un reseau routier OSM prepare en tableaux, pret a etre decoupe.

    Seuls ces champs sont persistes ; les index derives (cached_property) sont
    recalcules a la volee apres chargement.
    """

    network_type: str
    bbox: BBox
    osm_ids: np.ndarray  # int64 (N,)
    lat: np.ndarray  # float64 (N,)
    lon: np.ndarray  # float64 (N,)
    edge_u: np.ndarray  # int64 (E,) index de noeud source
    edge_v: np.ndarray  # int64 (E,) index de noeud cible
    edge_key: np.ndarray  # int64 (E,) cle MultiDiGraph
    length: np.ndarray  # float64 (E,) METRES
    maxspeed_mps: np.ndarray  # float64 (E,) NaN si inconnue
    grade_abs: np.ndarray  # float64 (E,)
    name_code: np.ndarray  # int32 (E,) index dans `strings`, -1 si absent
    ref_code: np.ndarray  # int32 (E,)
    highway_code: np.ndarray  # int32 (E,)
    strings: List[str]
    roundabout: np.ndarray  # bool (E,)
    geom_offsets: np.ndarray  # int64 (E+1,) decoupage de geom_coords par arete
    geom_coords: np.ndarray  # float64 (M, 2) lon/lat, orientees u -> v
    format_version: int = _CACHE_FORMAT_VERSION

    _PERSISTED = (
        "network_type", "bbox", "osm_ids", "lat", "lon", "edge_u", "edge_v",
        "edge_key", "length", "maxspeed_mps", "grade_abs", "name_code",
        "ref_code", "highway_code", "strings", "roundabout", "geom_offsets",
        "geom_coords", "format_version",
    )

    def __getstate__(self):
        return {name: self.__dict__[name] for name in self._PERSISTED}

    def __setstate__(self, state):
        self.__dict__.update(state)

    # -- construction --------------------------------------------------------

    @classmethod
    def from_graph(cls, graph, network_type: str, bbox: BBox) -> "RoadRegion":
        """Prepare un MultiDiGraph osmnx (noeuds x/y, aretes length/name/...)."""
        node_list = list(graph.nodes)
        node_count = len(node_list)
        index = {node: position for position, node in enumerate(node_list)}
        osm_ids = np.fromiter((int(node) for node in node_list), dtype=np.int64, count=node_count)
        lon = np.fromiter((float(graph.nodes[n]["x"]) for n in node_list), dtype=np.float64, count=node_count)
        lat = np.fromiter((float(graph.nodes[n]["y"]) for n in node_list), dtype=np.float64, count=node_count)

        edge_count = graph.number_of_edges()
        edge_u = np.empty(edge_count, dtype=np.int64)
        edge_v = np.empty(edge_count, dtype=np.int64)
        edge_key = np.empty(edge_count, dtype=np.int64)
        length = np.empty(edge_count, dtype=np.float64)
        maxspeed = np.full(edge_count, np.nan, dtype=np.float64)
        grade = np.zeros(edge_count, dtype=np.float64)
        name_code = np.full(edge_count, -1, dtype=np.int32)
        ref_code = np.full(edge_count, -1, dtype=np.int32)
        highway_code = np.full(edge_count, -1, dtype=np.int32)
        roundabout = np.zeros(edge_count, dtype=bool)
        geom_offsets = np.zeros(edge_count + 1, dtype=np.int64)
        geom_parts: List[np.ndarray] = []

        strings: List[str] = []
        codes: Dict[str, int] = {}

        def _code(value) -> int:
            text = _first_text(value)
            if text is None:
                return -1
            code = codes.get(text)
            if code is None:
                code = codes[text] = len(strings)
                strings.append(text)
            return code

        total_points = 0
        for position, (u, v, key, data) in enumerate(graph.edges(keys=True, data=True)):
            ui, vi = index[u], index[v]
            edge_u[position] = ui
            edge_v[position] = vi
            edge_key[position] = int(key) if isinstance(key, (int, np.integer)) else 0

            raw_length = data.get("length")
            try:
                edge_length = float(raw_length) if raw_length is not None else 0.0
            except (TypeError, ValueError):
                edge_length = 0.0
            if not edge_length > 0:
                edge_length = haversine_m(lon[ui], lat[ui], lon[vi], lat[vi])
            length[position] = edge_length

            speed_kmh = _parse_maxspeed_kmh(data.get("maxspeed"))
            if speed_kmh is not None:
                maxspeed[position] = max(0.5, speed_kmh / 3.6)
            try:
                grade[position] = abs(float(data.get("grade_abs", 0.0) or 0.0))
            except (TypeError, ValueError):
                grade[position] = 0.0

            name_code[position] = _code(data.get("name"))
            ref_code[position] = _code(data.get("ref"))
            highway_code[position] = _code(data.get("highway"))
            junction = _first_text(data.get("junction"))
            roundabout[position] = junction in {"roundabout", "circular"}

            geometry = data.get("geometry")
            if geometry is not None:
                coords = _oriented_coords(geometry, lon[ui], lat[ui], lon[vi], lat[vi])
                if coords is not None:
                    geom_parts.append(coords)
                    total_points += len(coords)
            geom_offsets[position + 1] = total_points

        geom_coords = (
            np.concatenate(geom_parts) if geom_parts else np.empty((0, 2), dtype=np.float64)
        )
        return cls(
            network_type=network_type,
            bbox=tuple(float(value) for value in bbox),
            osm_ids=osm_ids,
            lat=lat,
            lon=lon,
            edge_u=edge_u,
            edge_v=edge_v,
            edge_key=edge_key,
            length=length,
            maxspeed_mps=maxspeed,
            grade_abs=grade,
            name_code=name_code,
            ref_code=ref_code,
            highway_code=highway_code,
            strings=strings,
            roundabout=roundabout,
            geom_offsets=geom_offsets,
            geom_coords=geom_coords,
        )

    # -- index derives (non persistes) ----------------------------------------

    @property
    def node_count(self) -> int:
        return int(self.osm_ids.size)

    @property
    def edge_count(self) -> int:
        return int(self.edge_u.size)

    @cached_property
    def node_ids(self) -> List[str]:
        return [f"osm:{osm_id}" for osm_id in self.osm_ids.tolist()]

    @cached_property
    def _id_index(self) -> Tuple[np.ndarray, np.ndarray]:
        order = np.argsort(self.osm_ids, kind="stable")
        return self.osm_ids[order], order

    @cached_property
    def _pair_index(self) -> Tuple[np.ndarray, np.ndarray]:
        keys = self.edge_u * self.node_count + self.edge_v
        order = np.argsort(keys, kind="stable")
        return keys[order], order

    @cached_property
    def node_degree(self) -> np.ndarray:
        """Nombre de voisins distincts (entrants ou sortants) de chaque noeud."""
        if self.edge_count == 0:
            return np.zeros(self.node_count, dtype=np.int64)
        low = np.minimum(self.edge_u, self.edge_v)
        high = np.maximum(self.edge_u, self.edge_v)
        pairs = np.unique(low * self.node_count + high)
        a, b = pairs // self.node_count, pairs % self.node_count
        loops = a == b
        return (
            np.bincount(a[~loops], minlength=self.node_count)
            + np.bincount(b[~loops], minlength=self.node_count)
        )

    @cached_property
    def node_exits(self) -> np.ndarray:
        """Aretes sortantes hors rond-point : sert a compter les sorties."""
        return np.bincount(self.edge_u[~self.roundabout], minlength=self.node_count)

    @cached_property
    def node_tiles(self) -> List[str]:
        return [
            f"{round(lat, 2)}:{round(lon, 2)}"
            for lat, lon in zip(self.lat.tolist(), self.lon.tolist())
        ]

    # -- acces ---------------------------------------------------------------

    def text(self, code: int) -> Optional[str]:
        return self.strings[code] if code >= 0 else None

    def index_of(self, osm_id: int) -> Optional[int]:
        sorted_ids, order = self._id_index
        position = int(np.searchsorted(sorted_ids, osm_id))
        if position < sorted_ids.size and sorted_ids[position] == osm_id:
            return int(order[position])
        return None

    def edges_between(self, u_index: int, v_index: int) -> np.ndarray:
        keys, order = self._pair_index
        key = u_index * self.node_count + v_index
        low = np.searchsorted(keys, key, side="left")
        high = np.searchsorted(keys, key, side="right")
        return order[low:high]

    def edge_coordinates(self, edge_index: int) -> Tuple[Tuple[float, float], ...]:
        start, end = self.geom_offsets[edge_index], self.geom_offsets[edge_index + 1]
        u, v = self.edge_u[edge_index], self.edge_v[edge_index]
        if end - start >= 2:
            points = self.geom_coords[start:end].tolist()
            return tuple((lon, lat) for lon, lat in points)
        return (
            (float(self.lon[u]), float(self.lat[u])),
            (float(self.lon[v]), float(self.lat[v])),
        )

    def edge_id(self, edge_index: int) -> str:
        u, v = self.edge_u[edge_index], self.edge_v[edge_index]
        return f"osm_{self.osm_ids[u]}_{self.osm_ids[v]}_{self.edge_key[edge_index]}"

    def path_edge(self, edge_index: int, weight: float) -> PathEdge:
        u, v = int(self.edge_u[edge_index]), int(self.edge_v[edge_index])
        return PathEdge(
            edge_id=self.edge_id(edge_index),
            source_id=self.node_ids[u],
            target_id=self.node_ids[v],
            weight=float(weight),
            length=float(self.length[edge_index]),
            layer=LAYER_ROAD,
            coordinates=self.edge_coordinates(edge_index),
            name=self.text(int(self.name_code[edge_index])),
            ref=self.text(int(self.ref_code[edge_index])),
            highway=self.text(int(self.highway_code[edge_index])),
            roundabout=bool(self.roundabout[edge_index]),
            junction_degree=int(self.node_degree[u]),
            source_exits=int(self.node_exits[u]),
        )


def _oriented_coords(geometry, u_lon, u_lat, v_lon, v_lat) -> Optional[np.ndarray]:
    """Coordonnees d'une geometrie d'arete, orientees de u vers v, extremites
    recalees exactement sur les noeuds (pour un raccord sans couture)."""
    try:
        coords = np.asarray(geometry.coords, dtype=np.float64)
    except (AttributeError, TypeError, ValueError):
        return None
    if coords.ndim != 2 or coords.shape[0] < 2:
        return None
    coords = coords[:, :2].copy()
    forward = (coords[0, 0] - u_lon) ** 2 + (coords[0, 1] - u_lat) ** 2
    backward = (coords[-1, 0] - u_lon) ** 2 + (coords[-1, 1] - u_lat) ** 2
    if backward < forward:
        coords = coords[::-1].copy()
    coords[0] = (u_lon, u_lat)
    coords[-1] = (v_lon, v_lat)
    return coords


# ── Cache des regions ───────────────────────────────────────────────────────

_REGION_FILE = re.compile(
    r"^(?P<network>.+)_(?P<w>-?\d+\.\d+)_(?P<s>-?\d+\.\d+)_(?P<e>-?\d+\.\d+)_(?P<n>-?\d+\.\d+)\.pkl$"
)


def _region_filename(network_type: str, bbox: BBox) -> str:
    west, south, east, north = bbox
    return f"{network_type}_{west:.5f}_{south:.5f}_{east:.5f}_{north:.5f}.pkl"


def _configure_osmnx(ox) -> None:
    ox.settings.requests_timeout = OSMNX_REQUESTS_TIMEOUT_S


class RoadNetworkCache:
    """Regions OSM en memoire (LRU) et sur disque. Sur de plusieurs threads.

    Un verrou par type de reseau evite que dix requetes simultanees sur une
    zone froide declenchent dix telechargements identiques : la premiere
    telecharge, les suivantes attendent puis trouvent la region en memoire.
    """

    def __init__(
        self,
        cache_dir: Optional[str] = OSMNX_CACHE_DIR,
        max_regions: int = OSMNX_MEMORY_REGIONS,
        padding_m: float = OSMNX_REGION_PADDING_M,
        failure_cooldown_s: float = OSMNX_FAILURE_COOLDOWN_S,
    ):
        self._cache_dir = cache_dir or None
        self._max_regions = max(1, int(max_regions))
        self._padding_m = max(0.0, float(padding_m))
        self._failure_cooldown_s = max(0.0, float(failure_cooldown_s))
        self._regions: "OrderedDict[Tuple[str, BBox], RoadRegion]" = OrderedDict()
        self._lock = threading.Lock()
        self._download_locks: Dict[str, threading.Lock] = {}
        self._failures: Dict[Tuple[str, BBox], float] = {}

    # -- API -------------------------------------------------------------------

    def get_region(self, network_type: str, bbox: BBox) -> Optional[RoadRegion]:
        """Region couvrant `bbox` : memoire, puis disque, puis Overpass."""
        return self._get(network_type, bbox, padding_m=self._padding_m)

    def preload(self, network_type: str, bbox: BBox) -> Optional[RoadRegion]:
        """Charge ou telecharge exactement `bbox` (arrondie), sans marge."""
        return self._get(network_type, bbox, padding_m=0.0)

    def cached_regions(self) -> List[Tuple[str, BBox]]:
        with self._lock:
            return list(self._regions.keys())

    def clear(self) -> None:
        with self._lock:
            self._regions.clear()
            self._failures.clear()

    # -- interne -----------------------------------------------------------------

    def _get(self, network_type: str, bbox: BBox, padding_m: float) -> Optional[RoadRegion]:
        region = self._from_memory(network_type, bbox)
        if region is not None:
            return region

        with self._download_lock(network_type):
            # Une autre requete a pu charger la region pendant l'attente.
            region = self._from_memory(network_type, bbox)
            if region is None:
                region = self._from_disk(network_type, bbox)
            if region is None:
                region = self._download(network_type, bbox, padding_m)
            if region is not None:
                self._remember(region)
            return region

    def _download_lock(self, network_type: str) -> threading.Lock:
        with self._lock:
            lock = self._download_locks.get(network_type)
            if lock is None:
                lock = self._download_locks[network_type] = threading.Lock()
            return lock

    def _from_memory(self, network_type: str, bbox: BBox) -> Optional[RoadRegion]:
        with self._lock:
            best_key = None
            for key, region in self._regions.items():
                if key[0] != network_type or not _contains(region.bbox, bbox):
                    continue
                if best_key is None or _area(region.bbox) < _area(best_key[1]):
                    best_key = key
            if best_key is None:
                return None
            self._regions.move_to_end(best_key)
            return self._regions[best_key]

    def _remember(self, region: RoadRegion) -> None:
        with self._lock:
            key = (region.network_type, region.bbox)
            self._regions[key] = region
            self._regions.move_to_end(key)
            while len(self._regions) > self._max_regions:
                evicted, _ = self._regions.popitem(last=False)
                logger.info("region %s %s retiree du cache memoire", *evicted)

    def _from_disk(self, network_type: str, bbox: BBox) -> Optional[RoadRegion]:
        if not self._cache_dir:
            return None
        try:
            names = os.listdir(self._cache_dir)
        except OSError:
            return None

        candidates = []
        for name in names:
            match = _REGION_FILE.match(name)
            if not match or match.group("network") != network_type:
                continue
            region_bbox = tuple(float(match.group(k)) for k in ("w", "s", "e", "n"))
            if _contains(region_bbox, bbox):
                candidates.append((_area(region_bbox), name))

        for _, name in sorted(candidates):
            path = os.path.join(self._cache_dir, name)
            started = time.perf_counter()
            region = self._load(path)
            if region is not None:
                logger.info(
                    "region %s %s chargee depuis le disque en %.2fs (%d noeuds, %d aretes)",
                    network_type, region.bbox, time.perf_counter() - started,
                    region.node_count, region.edge_count,
                )
                return region
        return None

    @staticmethod
    def _load(path: str) -> Optional[RoadRegion]:
        # Fichiers produits par ce service uniquement (repertoire de cache).
        try:
            with open(path, "rb") as handle:
                region = pickle.load(handle)
        except Exception:
            logger.warning("cache de region illisible, ignore : %s", path, exc_info=True)
            return None
        if not isinstance(region, RoadRegion) or region.format_version != _CACHE_FORMAT_VERSION:
            logger.info("cache de region d'un ancien format, ignore : %s", path)
            return None
        return region

    def _save(self, region: RoadRegion) -> None:
        if not self._cache_dir:
            return
        path = os.path.join(self._cache_dir, _region_filename(region.network_type, region.bbox))
        tmp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            with open(tmp_path, "wb") as handle:
                pickle.dump(region, handle, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_path, path)
        except OSError:
            logger.warning("impossible d'ecrire le cache de region %s", path, exc_info=True)
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def _download(self, network_type: str, bbox: BBox, padding_m: float) -> Optional[RoadRegion]:
        target = _snap_bbox_out(expand_bbox(bbox, padding_m) if padding_m else bbox)
        key = (network_type, target)
        failed_at = self._failures.get(key)
        if failed_at is not None and time.monotonic() - failed_at < self._failure_cooldown_s:
            logger.info(
                "region %s %s en echec il y a moins de %.0fs : pas de nouvel essai",
                network_type, target, self._failure_cooldown_s,
            )
            return None

        try:
            import osmnx as ox
        except ImportError:
            logger.debug("osmnx non installe ; bascule sur le flux OSRM")
            return None
        _configure_osmnx(ox)

        started = time.perf_counter()
        try:
            # Simplification faite ensuite, a la main, pour garder les noeuds ou
            # la rue change de nom : sans eux, "Avenue Foch" puis "Rue du Sud"
            # fusionneraient en une seule arete et l'instruction "Continuez sur
            # Rue du Sud" serait perdue.
            graph = ox.graph_from_bbox(
                target,
                network_type=network_type,
                simplify=False,
                retain_all=False,
                truncate_by_edge=True,
            )
            if graph.number_of_nodes() and not graph.graph.get("simplified"):
                graph = ox.simplify_graph(graph, edge_attrs_differ=["name"])
        except Exception:
            logger.warning(
                "osmnx graph_from_bbox a echoue pour bbox=%s network_type=%s ; repli OSRM",
                target, network_type, exc_info=True,
            )
            self._failures[key] = time.monotonic()
            return None

        if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
            logger.warning(
                "osmnx a renvoye un graphe vide pour bbox=%s network_type=%s ; repli OSRM",
                target, network_type,
            )
            self._failures[key] = time.monotonic()
            return None

        region = RoadRegion.from_graph(graph, network_type, target)
        self._failures.pop(key, None)
        logger.info(
            "region %s %s telechargee et preparee en %.1fs : %d noeuds, %d aretes",
            network_type, target, time.perf_counter() - started,
            region.node_count, region.edge_count,
        )
        self._save(region)
        return region


_CACHE = RoadNetworkCache()


def get_road_cache() -> RoadNetworkCache:
    return _CACHE


def set_road_cache(cache: RoadNetworkCache) -> None:
    """Remplace le cache (tests, reconfiguration)."""
    global _CACHE
    _CACHE = cache


def preload_regions(bboxes: Iterable[BBox], network_types: Iterable[str]) -> None:
    """Precharge des regions (appele au demarrage, dans un thread dedie)."""
    network_types = list(network_types)
    for bbox in bboxes:
        for network_type in network_types:
            started = time.perf_counter()
            region = get_road_cache().preload(network_type, bbox)
            if region is None:
                logger.warning("prechargement %s %s impossible", network_type, bbox)
            else:
                logger.info(
                    "prechargement %s %s pret en %.1fs", network_type, bbox,
                    time.perf_counter() - started,
                )


# ── Sous-graphe d'une requete ───────────────────────────────────────────────

def compute_edge_weights(
    region: RoadRegion,
    edge_idx: np.ndarray,
    routing_profile: str,
    friction_by_tile: Optional[Dict[str, float]] = None,
    realtime_factor: float = 1.0,
    predictive_factor: float = 1.0,
) -> np.ndarray:
    """weight = (length / vitesse) x pente x friction_tuile x trafic x predictif."""
    profile = routing_profile.lower()
    default = DEFAULT_PROFILE_SPEEDS_MPS.get(profile, DEFAULT_PROFILE_SPEEDS_MPS["driving"])
    limits = region.maxspeed_mps[edge_idx]
    known = ~np.isnan(limits)
    speed = np.full(edge_idx.shape, default, dtype=np.float64)
    if profile in _SPEED_LIMITED_PROFILES or profile not in DEFAULT_PROFILE_SPEEDS_MPS:
        speed[known] = limits[known]
    else:
        speed[known] = np.minimum(default, limits[known])
    speed = np.maximum(speed, 0.5)

    travel_time_s = region.length[edge_idx] / speed
    slope_penalty = 1.0 + np.minimum(0.3, region.grade_abs[edge_idx] * 2.0)
    weights = (
        travel_time_s
        * slope_penalty
        * max(realtime_factor, 0.1)
        * max(predictive_factor, 0.1)
    )

    if friction_by_tile:
        tiles = region.node_tiles
        sources, inverse = np.unique(region.edge_u[edge_idx], return_inverse=True)
        friction = np.fromiter(
            (friction_by_tile.get(tiles[i], 1.0) for i in sources.tolist()),
            dtype=np.float64,
            count=sources.size,
        )
        weights = weights * np.maximum(friction[inverse], 0.1)

    return weights


class RoadSubgraph:
    """Une region restreinte a la bbox d'une demande, avec ses poids."""

    def __init__(self, region: RoadRegion, edge_idx: np.ndarray, weights: np.ndarray):
        self.region = region
        self.edge_idx = edge_idx
        self.weights = weights
        self.node_idx = np.unique(
            np.concatenate([region.edge_u[edge_idx], region.edge_v[edge_idx]])
        )

    @cached_property
    def weight_by_edge(self) -> np.ndarray:
        """Poids indexe par arete de la region ; +inf hors du sous-graphe."""
        full = np.full(self.region.edge_count, np.inf, dtype=np.float64)
        full[self.edge_idx] = self.weights
        return full

    def contains_node(self, node_index: int) -> bool:
        position = int(np.searchsorted(self.node_idx, node_index))
        return position < self.node_idx.size and int(self.node_idx[position]) == node_index

    @cached_property
    def main_component(self) -> np.ndarray:
        """Noeuds (index region) de la plus grande composante fortement connexe.

        Accrocher un depart a un bout de voie isole (parking prive, impasse a
        sens unique...) rendrait le trajet introuvable pour le routeur.
        """
        try:
            from scipy.sparse import csr_matrix
            from scipy.sparse.csgraph import connected_components
        except ImportError:  # pragma: no cover - scipy vient avec scikit-learn
            return self.node_idx
        size = self.node_idx.size
        sources = np.searchsorted(self.node_idx, self.region.edge_u[self.edge_idx])
        targets = np.searchsorted(self.node_idx, self.region.edge_v[self.edge_idx])
        matrix = csr_matrix(
            (np.ones(sources.size, dtype=np.int8), (sources, targets)), shape=(size, size)
        )
        _, labels = connected_components(matrix, directed=True, connection="strong")
        largest = np.bincount(labels).argmax()
        return self.node_idx[labels == largest]

    @cached_property
    def _projection(self) -> Tuple[float, float]:
        lat0 = float(np.mean(self.region.lat[self.node_idx])) if self.node_idx.size else 0.0
        return _M_PER_DEG * math.cos(math.radians(lat0)), _M_PER_DEG

    def _project(self, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
        kx, ky = self._projection
        return np.column_stack((np.asarray(lons, dtype=np.float64) * kx, np.asarray(lats, dtype=np.float64) * ky))

    @cached_property
    def _kdtree(self):
        from scipy.spatial import cKDTree

        candidates = self.main_component
        return cKDTree(self._project(self.region.lat[candidates], self.region.lon[candidates]))

    def nearest_many(self, lats: Sequence[float], lons: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
        """Noeud (index region) le plus proche de chaque point, et distance en m."""
        if self.main_component.size == 0:
            return np.empty(0, dtype=np.int64), np.empty(0)
        distances, positions = self._kdtree.query(self._project(np.asarray(lats), np.asarray(lons)))
        return self.main_component[np.atleast_1d(positions)], np.atleast_1d(distances)

    def nearest(self, lat: float, lon: float) -> Optional[Tuple[int, float]]:
        indices, distances = self.nearest_many([lat], [lon])
        if indices.size == 0:
            return None
        return int(indices[0]), float(distances[0])

    def bounds(self) -> BBox:
        lons = self.region.lon[self.node_idx]
        lats = self.region.lat[self.node_idx]
        return float(lons.min()), float(lats.min()), float(lons.max()), float(lats.max())


def extract_subgraph(
    region: RoadRegion,
    bbox: BBox,
    routing_profile: str,
    friction_by_tile: Optional[Dict[str, float]] = None,
    realtime_factor: float = 1.0,
    predictive_factor: float = 1.0,
) -> Optional[RoadSubgraph]:
    west, south, east, north = bbox
    inside = (
        (region.lon >= west) & (region.lon <= east)
        & (region.lat >= south) & (region.lat <= north)
    )
    edge_idx = np.flatnonzero(inside[region.edge_u] & inside[region.edge_v])
    if edge_idx.size == 0:
        return None
    weights = compute_edge_weights(
        region, edge_idx, routing_profile, friction_by_tile, realtime_factor, predictive_factor
    )
    return RoadSubgraph(region, edge_idx, weights)


# ── Graphe de routage : voirie OSM + couches additionnelles ─────────────────

class OsmRoutingGraph(RoutingGraph):
    """Sous-graphe routier, plus des noeuds/aretes additionnels (GTFS...)."""

    def __init__(
        self,
        road: RoadSubgraph,
        extra_nodes: Sequence[GraphNode] = (),
        extra_edges: Sequence[GraphEdge] = (),
    ):
        self.road = road
        self.extra_nodes: List[GraphNode] = list(extra_nodes)
        self.extra_edges: List[GraphEdge] = list(extra_edges)
        self._extra_node_by_id = {node.id: node for node in self.extra_nodes}

    def with_extras(
        self, nodes: Iterable[GraphNode], edges: Iterable[GraphEdge]
    ) -> "OsmRoutingGraph":
        known = set(self._extra_node_by_id)
        new_nodes = [node for node in nodes if node.id not in known]
        return OsmRoutingGraph(
            self.road, self.extra_nodes + new_nodes, self.extra_edges + list(edges)
        )

    @cached_property
    def _extra_edges_by_pair(self) -> Dict[Tuple[str, str], List[GraphEdge]]:
        by_pair: Dict[Tuple[str, str], List[GraphEdge]] = {}
        for edge in self.extra_edges:
            by_pair.setdefault((edge.source_id, edge.target_id), []).append(edge)
        return by_pair

    # -- RoutingGraph ----------------------------------------------------------

    def is_empty(self) -> bool:
        return self.road.node_idx.size == 0 or self.road.edge_idx.size == 0

    def node_count(self) -> int:
        return int(self.road.node_idx.size) + len(self.extra_nodes)

    def edge_count(self) -> int:
        return int(self.road.edge_idx.size) + len(self.extra_edges)

    def bounds(self) -> BBox:
        return self.road.bounds()

    def _osm_index(self, node_id: str) -> Optional[int]:
        if not node_id.startswith("osm:"):
            return None
        try:
            osm_id = int(node_id[4:])
        except ValueError:
            return None
        return self.road.region.index_of(osm_id)

    def has_node(self, node_id: str) -> bool:
        if node_id in self._extra_node_by_id:
            return True
        index = self._osm_index(node_id)
        return index is not None and self.road.contains_node(index)

    def build_solve_request(self, start_id: str, goal_id: str) -> "astar_pb2.SolveRequest":
        region = self.road.region
        ids = region.node_ids
        request = astar_pb2.SolveRequest(start_id=start_id, goal_id=goal_id)

        # repeated.add(...) : environ 3x plus rapide que de construire la liste
        # de messages puis de la passer au constructeur (mesure sur 100k messages).
        add_node = request.nodes.add
        node_idx = self.road.node_idx
        for index, lat, lon in zip(
            node_idx.tolist(), region.lat[node_idx].tolist(), region.lon[node_idx].tolist()
        ):
            add_node(id=ids[index], lat=lat, lon=lon)
        for node in self.extra_nodes:
            add_node(id=node.id, lat=node.lat, lon=node.lon)

        add_edge = request.edges.add
        edge_idx = self.road.edge_idx
        for source, target, weight in zip(
            region.edge_u[edge_idx].tolist(),
            region.edge_v[edge_idx].tolist(),
            self.road.weights.tolist(),
        ):
            add_edge(from_id=ids[source], to_id=ids[target], weight=weight)
        for edge in self.extra_edges:
            add_edge(from_id=edge.source_id, to_id=edge.target_id, weight=edge.weight)
        return request

    def node_coordinate(self, node_id: str) -> Optional[Tuple[float, float]]:
        node = self._extra_node_by_id.get(node_id)
        if node is not None:
            return node.lon, node.lat
        index = self._osm_index(node_id)
        if index is None:
            return None
        region = self.road.region
        return float(region.lon[index]), float(region.lat[index])

    def _describe(self, node_id: str) -> Optional[Tuple[float, float, Optional[str]]]:
        node = self._extra_node_by_id.get(node_id)
        if node is not None:
            return node.lon, node.lat, node.name
        coordinate = self.node_coordinate(node_id)
        return None if coordinate is None else (coordinate[0], coordinate[1], None)

    def _road_path_edge(self, source_id: str, target_id: str) -> Optional[PathEdge]:
        source, target = self._osm_index(source_id), self._osm_index(target_id)
        if source is None or target is None:
            return None
        candidates = self.road.region.edges_between(source, target)
        if candidates.size == 0:
            return None
        weights = self.road.weight_by_edge[candidates]
        best = int(np.argmin(weights))
        if not np.isfinite(weights[best]):
            return None  # l'arete existe dans la region mais pas dans le sous-graphe
        return self.road.region.path_edge(int(candidates[best]), float(weights[best]))

    def _extra_path_edge(self, source_id: str, target_id: str) -> Optional[PathEdge]:
        candidates = self._extra_edges_by_pair.get((source_id, target_id))
        if not candidates:
            return None
        edge = min(candidates, key=lambda candidate: candidate.weight)
        source, target = self._describe(source_id), self._describe(target_id)
        if source is None or target is None:
            return None
        return extra_edge_to_path_edge(edge, source, target)

    def resolve_path(self, node_path: Sequence[str]) -> Optional[List[PathEdge]]:
        if len(node_path) < 2:
            return None
        edges: List[PathEdge] = []
        for source_id, target_id in zip(node_path, node_path[1:]):
            if source_id.startswith("osm:") and target_id.startswith("osm:"):
                edge = self._road_path_edge(source_id, target_id)
            else:
                edge = self._extra_path_edge(source_id, target_id)
            if edge is None:
                return None
            edges.append(edge)
        return edges

    def to_snapshot(self) -> RoutingGraphSnapshot:
        region = self.road.region
        ids = region.node_ids
        node_idx = self.road.node_idx
        nodes = [
            NodeDTO(id=ids[index], lat=lat, lon=lon, is_transit_stop=False)
            for index, lat, lon in zip(
                node_idx.tolist(), region.lat[node_idx].tolist(), region.lon[node_idx].tolist()
            )
        ]
        nodes.extend(
            NodeDTO(id=node.id, lat=node.lat, lon=node.lon, is_transit_stop=node.is_transit_stop)
            for node in self.extra_nodes
        )
        edges = [
            EdgeDTO(
                edge_id=region.edge_id(index),
                source_id=ids[int(region.edge_u[index])],
                target_id=ids[int(region.edge_v[index])],
                weight=weight,
                length=float(region.length[index]),
                layer=LAYER_ROAD,
                name=region.text(int(region.name_code[index])),
            )
            for index, weight in zip(self.road.edge_idx.tolist(), self.road.weights.tolist())
        ]
        edges.extend(
            EdgeDTO(
                edge_id=edge.edge_id,
                source_id=edge.source_id,
                target_id=edge.target_id,
                weight=edge.weight,
                length=edge.length,
                layer=edge.layer,
                name=edge.name,
                transit_line_id=edge.transit_line_id,
            )
            for edge in self.extra_edges
        )
        return RoutingGraphSnapshot(nodes=nodes, edges=edges)


# ── Point d'entree ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RoadGraphResult:
    graph: OsmRoutingGraph
    start_node_id: str
    end_node_id: str
    start_snap_m: float = 0.0
    end_snap_m: float = 0.0


def build_osmnx_graph(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    routing_profile: str,
    friction_by_tile: Optional[Dict[str, float]] = None,
    realtime_factor: float = 1.0,
    predictive_factor: float = 1.0,
    margin_m: float = 1500.0,
) -> Optional[RoadGraphResult]:
    """Construit le graphe pondere d'une demande. Bloquant : a appeler dans un
    thread (`asyncio.to_thread`). Aucun chemin n'est calcule ici."""
    started = time.perf_counter()
    network_type = PROFILE_TO_NETWORK_TYPE.get(routing_profile.lower(), "drive")
    north, south, east, west = _compute_bbox(
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        margin_m=margin_m,
    )
    bbox = (west, south, east, north)

    region = get_road_cache().get_region(network_type, bbox)
    if region is None:
        return None

    subgraph = extract_subgraph(
        region, bbox, routing_profile, friction_by_tile, realtime_factor, predictive_factor
    )
    if subgraph is None:
        logger.warning("aucune voie %s dans la bbox %s ; repli OSRM", network_type, bbox)
        return None

    start = subgraph.nearest(start_lat, start_lon)
    end = subgraph.nearest(end_lat, end_lon)
    if start is None or end is None:
        logger.warning("impossible d'accrocher depart/arrivee au graphe ; repli OSRM")
        return None

    graph = OsmRoutingGraph(subgraph)
    logger.info(
        "graphe construit : %d noeuds, %d aretes (profil=%s) en %.0f ms",
        graph.node_count(), graph.edge_count(), routing_profile,
        (time.perf_counter() - started) * 1000,
    )
    return RoadGraphResult(
        graph=graph,
        start_node_id=region.node_ids[start[0]],
        end_node_id=region.node_ids[end[0]],
        start_snap_m=start[1],
        end_snap_m=end[1],
    )
