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