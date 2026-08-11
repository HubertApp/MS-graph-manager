import httpx

from app.config import PREDICTIVE_INFO_URL, TRAFFIC_INFO_URL


async def _get_factor_from_service(url: str, payload: dict) -> float:
    if not url:
        return 1.0

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                return 1.0

            data = response.json()
            factor = float(data.get("factor", 1.0))
            return max(0.1, min(3.0, factor))
    except (httpx.HTTPError, ValueError, TypeError):
        return 1.0


async def get_realtime_factor(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> float:
    payload = {
        "from": {"lat": from_lat, "lon": from_lon},
        "to": {"lat": to_lat, "lon": to_lon},
    }
    return await _get_factor_from_service(TRAFFIC_INFO_URL, payload)


async def get_predictive_factor(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> float:
    payload = {
        "from": {"lat": from_lat, "lon": from_lon},
        "to": {"lat": to_lat, "lon": to_lon},
    }
    return await _get_factor_from_service(PREDICTIVE_INFO_URL, payload)
