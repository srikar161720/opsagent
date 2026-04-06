"""Agent tool registry — all tools available to the OpsAgent investigation graph."""

from src.agent.tools.discover_causation import discover_causation
from src.agent.tools.get_topology import get_topology
from src.agent.tools.query_metrics import query_metrics
from src.agent.tools.search_logs import search_logs
from src.agent.tools.search_runbooks import search_runbooks

TOOLS = [query_metrics, search_logs, get_topology, search_runbooks, discover_causation]

__all__ = [
    "TOOLS",
    "discover_causation",
    "get_topology",
    "query_metrics",
    "search_logs",
    "search_runbooks",
]
