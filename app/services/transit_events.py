import logging
from typing import Optional

from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue
from pydantic import BaseModel

from app.config import RABBITMQ_URL
from app.services.transit_graph_client import invalidate

logger = logging.getLogger(__name__)

gtfs_events_exchange = RabbitExchange(
    "gtfs.events",
    type=ExchangeType.TOPIC,
    durable=True,
)

# File PROPRE a ce service. Un autre consommateur du meme evenement declare la
# sienne : chacun recoit une copie. Une file partagee les ferait se voler les
# messages a tour de role.
network_ingested_queue = RabbitQueue(
    "graph-manager.gtfs.network.ingested",
    durable=True,
    routing_key="gtfs.network.ingested",
)


class NetworkIngestedEvent(BaseModel):
    network_id: str
    ingestion_id: str
    ingested_at: Optional[str] = None


async def _on_network_ingested(event: NetworkIngestedEvent) -> None:
    logger.info(
        "reseau %s reingere (version %s)", event.network_id, event.ingestion_id
    )
    invalidate(event.network_id)


def build_broker() -> Optional[RabbitBroker]:
    if not RABBITMQ_URL:
        logger.info("RABBITMQ_URL absente : pas d'invalidation automatique du cache")
        return None

    broker = RabbitBroker(RABBITMQ_URL)
    broker.subscriber(network_ingested_queue, gtfs_events_exchange)(_on_network_ingested)
    return broker
