"""Unit tests for app/services/transit_events.py."""
from app.services import transit_events


def test_build_broker_returns_none_when_rabbitmq_url_empty(monkeypatch):
    monkeypatch.setattr("app.services.transit_events.RABBITMQ_URL", "")

    broker = transit_events.build_broker()

    assert broker is None


def test_build_broker_returns_a_broker_when_url_set(monkeypatch):
    monkeypatch.setattr(
        "app.services.transit_events.RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"
    )

    broker = transit_events.build_broker()

    assert broker is not None


async def test_on_network_ingested_invalidates_the_cache(monkeypatch):
    invalidated = []
    monkeypatch.setattr(
        "app.services.transit_events.invalidate", lambda network_id: invalidated.append(network_id)
    )

    await transit_events._on_network_ingested(
        transit_events.NetworkIngestedEvent(network_id="metz", ingestion_id="abc123")
    )

    assert invalidated == ["metz"]


def test_build_broker_registers_the_network_ingested_subscriber(monkeypatch):
    monkeypatch.setattr(
        "app.services.transit_events.RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"
    )

    broker = transit_events.build_broker()

    assert len(broker._subscribers) == 1
