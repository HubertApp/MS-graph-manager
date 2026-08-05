import os
from dotenv import load_dotenv

load_dotenv()

OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://localhost:5000")
TRAFFIC_INFO_URL = os.getenv("TRAFFIC_INFO_URL", "")
PREDICTIVE_INFO_URL = os.getenv("PREDICTIVE_INFO_URL", "")
CPP_ROUTER_GRPC_TARGET = os.getenv("CPP_ROUTER_GRPC_TARGET", "localhost:50051")
TRACKING_SERVICE_URL = os.getenv("TRACKING_SERVICE_URL", "")
OSMNX_GRAPH_MARGIN_M = float(os.getenv("OSMNX_GRAPH_MARGIN_M", "1500"))