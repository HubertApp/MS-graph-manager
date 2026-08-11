"""Unit tests for app/services/traffic_client.py."""
import httpx
import respx

from app.services import traffic_client


async def test_get_realtime_factor_returns_1_0_when_url_empty(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.TRAFFIC_INFO_URL", "")

    factor = await traffic_client.get_realtime_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 1.0


@respx.mock
async def test_get_realtime_factor_success(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.TRAFFIC_INFO_URL", "http://fake-traffic.test")
    respx.post("http://fake-traffic.test").mock(return_value=httpx.Response(200, json={"factor": 1.8}))

    factor = await traffic_client.get_realtime_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 1.8


@respx.mock
async def test_get_predictive_factor_clamps_high_values(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.PREDICTIVE_INFO_URL", "http://fake-predictive.test")
    respx.post("http://fake-predictive.test").mock(return_value=httpx.Response(200, json={"factor": 99.0}))

    factor = await traffic_client.get_predictive_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 3.0


@respx.mock
async def test_get_predictive_factor_clamps_low_values(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.PREDICTIVE_INFO_URL", "http://fake-predictive.test")
    respx.post("http://fake-predictive.test").mock(return_value=httpx.Response(200, json={"factor": 0.001}))

    factor = await traffic_client.get_predictive_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 0.1


@respx.mock
async def test_get_realtime_factor_non_200_returns_1_0(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.TRAFFIC_INFO_URL", "http://fake-traffic.test")
    respx.post("http://fake-traffic.test").mock(return_value=httpx.Response(500))

    factor = await traffic_client.get_realtime_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 1.0


@respx.mock
async def test_get_realtime_factor_malformed_payload_returns_1_0(monkeypatch):
    monkeypatch.setattr("app.services.traffic_client.TRAFFIC_INFO_URL", "http://fake-traffic.test")
    respx.post("http://fake-traffic.test").mock(
        return_value=httpx.Response(200, json={"factor": "not-a-number"})
    )

    factor = await traffic_client.get_realtime_factor(49.0, 6.0, 49.01, 6.01)

    assert factor == 1.0
