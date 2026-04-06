"""Service topology retrieval tool for the OpsAgent investigation agent.

Wraps the static TopologyGraph to expose service dependency information
as a LangChain tool callable by the LLM.
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from src.data_collection.topology_extractor import TopologyGraph

logger = logging.getLogger(__name__)

_topology = TopologyGraph()


@tool
def get_topology(
    service_name: str | None = None,
) -> dict:
    """Retrieve the service dependency graph for the microservice system.

    Call this FIRST at the start of every investigation to understand which
    services depend on which others.  Upstream services (dependencies of the
    affected service) are prime root cause suspects.  Downstream services are
    likely showing symptoms.

    Args:
        service_name: If provided, returns the subgraph centered on this
            service, including its direct upstream (dependencies) and
            downstream (dependents).  If None, returns the full system
            topology.

    Returns:
        dict with keys: nodes, edges, upstream, downstream.
    """
    try:
        if service_name is not None:
            return _topology.get_subgraph(service_name)

        full: dict = json.loads(_topology.to_json())
        full["upstream"] = []
        full["downstream"] = []
        return full
    except Exception:
        logger.exception("get_topology failed")
        return {
            "error": "Failed to retrieve topology",
            "nodes": [],
            "edges": [],
            "upstream": [],
            "downstream": [],
        }
