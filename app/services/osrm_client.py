import httpx
from app.config import OSRM_BASE_URL


async def fetch_route(from_lat, from_lon, to_lat, to_lon):
    # "driving" here is hardcoded on purpose: the OSRM backend documented in the
    # README is extracted with car.lua only, and osrm-routed ignores the URL
    # profile segment at request time (it's determined by the dataset the
    # server was started with). Requesting another profile would either 404
    # or silently still route as a car, so we don't pretend it's selectable.
    url = (
        f"{OSRM_BASE_URL}/route/v1/driving/"
        f"{from_lon},{from_lat};{to_lon},{to_lat}"
        f"?overview=full&geometries=geojson&steps=true"
    )

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)

    if response.status_code != 200:
        return None

    data = response.json()
    if not data.get("routes"):
        return None

    return data["routes"][0]
