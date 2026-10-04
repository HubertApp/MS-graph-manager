"""Consommateur RabbitMQ de l'evenement `gtfs.network.ingested`.

MS-aom-agregator publie cet evenement sur l'exchange topic `gtfs.events` a la
fin de chaque ingestion GTFS reussie. En le recevant, on vide le cache de la
couche transit du reseau concerne, on retient son identifiant (decouverte) et
on recharge sa couche pour l'heure courante : la prochaine demande en
transport en commun beneficie immediatement des nouvelles lignes.

Deux choix a connaitre :
  - la file est PROPRE a chaque processus (nom unique, supprimee a la
    deconnexion) : chaque replique du graph-manager a son propre cache et doit
    donc recevoir sa copie de l'evenement. Une file partagee les ferait se voler
    les messages. Un evenement emis pendant qu'une instance est arretee est
    perdu pour elle, ce qui est sans consequence : son cache repart vide.
  - la connexion est etablie en tache de fond, avec reprises : le service doit
    repondre aux itineraires meme si RabbitMQ n'est pas (encore) joignable.
"""

import asyncio
import logging
import os
import socket
from typing import Optional

from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue
from pydantic import BaseModel

from app.config import RABBITMQ_URL
from app.services.transit_graph_client import invalidate, prewarm, register_network

logger = logging.getLogger(__name__)

# Parametres IDENTIQUES a la declaration de MS-aom-agregator
# (app/core/broker.py) : une divergence provoquerait un PRECONDITION_FAILED.
GTFS_EVENTS_EXCHANGE = RabbitExchange(
    "gtfs.events",
    type=ExchangeType.TOPIC,
    durable=True,
)
NETWORK_INGESTED_ROUTING_KEY = "gtfs.network.ingested"

_RETRY_MIN_S = 2.0
_RETRY_MAX_S = 60.0


def queue_name() -> str:
    return f"graph-manager.{NETWORK_INGESTED_ROUTING_KEY}.{socket.gethostname()}.{os.getpid()}"


def network_ingested_queue() -> RabbitQueue:
    return RabbitQueue(
        queue_name(),
        durable=False,
        auto_delete=True,
        routing_key=NETWORK_INGESTED_ROUTING_KEY,
    )


class NetworkIngestedEvent(BaseModel):
    network_id: str
    ingestion_id: Optional[str] = None
    ingested_at: Optional[str] = None


_background_tasks: set = set()


def _in_background(coroutine) -> None:
    task = asyncio.get_running_loop().create_task(coroutine)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _on_network_ingested(event: NetworkIngestedEvent) -> None:
    logger.info(
        "evenement %s : reseau %s reingere (version %s)",
        NETWORK_INGESTED_ROUTING_KEY, event.network_id, event.ingestion_id,
    )
    invalidate(event.network_id)
    register_network(event.network_id)
    # Rechargement en tache de fond : l'acquittement du message n'attend pas
    # l'agregation de la couche (plusieurs secondes sur un gros reseau).
    _in_background(prewarm(event.network_id))


def build_broker() -> Optional[RabbitBroker]:
    if not RABBITMQ_URL:
        logger.info("RABBITMQ_URL absente : pas d'invalidation automatique du cache transit")
        return None

    broker = RabbitBroker(RABBITMQ_URL)
    broker.subscriber(network_ingested_queue(), GTFS_EVENTS_EXCHANGE)(_on_network_ingested)
    return broker


class TransitEventsConsumer:
    """Demarre et arrete le consommateur, avec reprises exponentielles."""

    def __init__(self):
        self._broker: Optional[RabbitBroker] = None
        self._task: Optional[asyncio.Task] = None
        self.connected = asyncio.Event()

    async def start(self) -> None:
        if not RABBITMQ_URL or self._task is not None:
            return
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        delay = _RETRY_MIN_S
        while True:
            broker = build_broker()
            if broker is None:
                return
            try:
                await broker.start()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # connexion refusee, DNS, identifiants...
                logger.warning(
                    "RabbitMQ injoignable (%s) ; nouvel essai dans %.0fs", error, delay
                )
                await _stop_quietly(broker)
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RETRY_MAX_S)
                continue
            self._broker = broker
            self.connected.set()
            logger.info(
                "abonne a %s (exchange %s)", NETWORK_INGESTED_ROUTING_KEY, GTFS_EVENTS_EXCHANGE.name
            )
            return

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._broker is not None:
            await _stop_quietly(self._broker)
            self._broker = None
        self.connected.clear()


async def _stop_quietly(broker: RabbitBroker) -> None:
    try:
        await broker.stop()
    except Exception:
        logger.debug("arret du broker en erreur (ignore)", exc_info=True)
