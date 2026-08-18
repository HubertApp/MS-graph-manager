import os

from dotenv import load_dotenv

load_dotenv()

OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://localhost:5000")
TRAFFIC_INFO_URL = os.getenv("TRAFFIC_INFO_URL", "")
PREDICTIVE_INFO_URL = os.getenv("PREDICTIVE_INFO_URL", "")
TRACKING_SERVICE_URL = os.getenv("TRACKING_SERVICE_URL", "")
OSMNX_GRAPH_MARGIN_M = float(os.getenv("OSMNX_GRAPH_MARGIN_M", "1500"))

CPP_ROUTER_GRPC_TARGET = os.getenv("CPP_ROUTER_GRPC_TARGET", "envoy:50051")
CPP_ROUTER_TIMEOUT_S = float(os.getenv("CPP_ROUTER_TIMEOUT_S", "10.0"))

CPP_ROUTER_MAX_MESSAGE_MB = int(os.getenv("CPP_ROUTER_MAX_MESSAGE_MB", "64"))

AOM_GRAPHQL_URL = os.getenv("AOM_GRAPHQL_URL", "")
TRANSIT_NETWORK_IDS = [
    value.strip()
    for value in os.getenv("TRANSIT_NETWORK_IDS", "").split(",")
    if value.strip()
]
TRANSIT_ACCESS_RADIUS_M = float(os.getenv("TRANSIT_ACCESS_RADIUS_M", "400"))
TRANSIT_FETCH_TIMEOUT_S = float(os.getenv("TRANSIT_FETCH_TIMEOUT_S", "30.0"))
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "")