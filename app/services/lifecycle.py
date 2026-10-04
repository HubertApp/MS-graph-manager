"""Taches de fond du service : prechargement OSM, consommateur RabbitMQ,
prechargement de la couche transit. Aucune n'est bloquante pour le demarrage :
le service repond aux requetes des la premiere seconde."""

import asyncio
import logging
import threading
from typing import List, Optional

from app.config import (
    AOM_GRAPHQL_URL,
    OSMNX_PRELOAD_BBOXES,
    OSMNX_PRELOAD_NETWORK_TYPES,
)
from app.services import transit_graph_client
from app.services.osmnx_graph import get_road_cache, preload_regions
from app.services.transit_events import TransitEventsConsumer

logger = logging.getLogger(__name__)


class BackgroundServices:
    def __init__(self):
        self.consumer = TransitEventsConsumer()
        self._tasks: List[asyncio.Task] = []
        self._preload_thread: Optional[threading.Thread] = None

    async def start(self) -> None:
        if OSMNX_PRELOAD_BBOXES:
            # Thread demon et non executeur asyncio : un telechargement long ne
            # doit pas retarder l'arret du service.
            self._preload_thread = threading.Thread(
                target=preload_regions,
                args=(OSMNX_PRELOAD_BBOXES, OSMNX_PRELOAD_NETWORK_TYPES),
                name="osm-preload",
                daemon=True,
            )
            self._preload_thread.start()
            logger.info(
                "prechargement OSM lance : %s x %s",
                OSMNX_PRELOAD_BBOXES, OSMNX_PRELOAD_NETWORK_TYPES,
            )

        await self.consumer.start()

        if AOM_GRAPHQL_URL:
            self._tasks.append(asyncio.get_running_loop().create_task(transit_graph_client.prewarm()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        await self.consumer.stop()

    def status(self) -> dict:
        return {
            "osm_preload_running": bool(self._preload_thread and self._preload_thread.is_alive()),
            "road_regions": [
                {"network_type": network_type, "bbox": list(bbox)}
                for network_type, bbox in get_road_cache().cached_regions()
            ],
            "rabbitmq_connected": self.consumer.connected.is_set(),
            "transit_layers_cached": [
                {"network_id": network_id, "hour": hour}
                for network_id, hour in transit_graph_client.cached_layers()
            ],
        }
