import os
from dotenv import load_dotenv

load_dotenv()

OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://localhost:5000")
TRAFFIC_INFO_URL = os.getenv("TRAFFIC_INFO_URL", "")
PREDICTIVE_INFO_URL = os.getenv("PREDICTIVE_INFO_URL", "")
CPP_ROUTER_URL = os.getenv("CPP_ROUTER_URL", "")
TRACKING_SERVICE_URL = os.getenv("TRACKING_SERVICE_URL", "")
GRAPH_COMPRESSION_EDGE_THRESHOLD = int(
	os.getenv("GRAPH_COMPRESSION_EDGE_THRESHOLD", "2000")
)
OSMNX_GRAPH_MARGIN_M = float(os.getenv("OSMNX_GRAPH_MARGIN_M", "1500"))