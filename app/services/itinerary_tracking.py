import httpx

from app.config import TRACKING_SERVICE_URL
from app.models.itinerary import ItineraryResultDTO, RouteRequestDTO


async def notify_tracking_service(request: RouteRequestDTO, itinerary: ItineraryResultDTO) -> None:
    if not TRACKING_SERVICE_URL:
        return

    payload = {
        "start_point": {
            "lat": request.start_point.lat,
            "lon": request.start_point.lon,
        },
        "end_point": {
            "lat": request.end_point.lat,
            "lon": request.end_point.lon,
        },
        "routing_profile": request.routing_profile,
        "distance_m": itinerary.distance_m,
        "duration_s": itinerary.duration_s,
        "segments": [
            {
                "type": segment.type,
                "osm_ids": segment.osm_ids,
                "transit_line_id": segment.transit_line_id,
                "expected_arrival": segment.expected_arrival.isoformat(),
            }
            for segment in itinerary.segments
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(TRACKING_SERVICE_URL, json=payload)
    except httpx.HTTPError:
        return
