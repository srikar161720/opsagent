"""Directed service dependency graph for the OTel Demo microservice system.

Used by the agent's get_topology tool to determine upstream/downstream
relationships when forming root cause hypotheses.
An edge A → B means "A is called by B" (A is a dependency of B).
Therefore: upstream services of B = {A} (potential root causes when B is affected).
"""

from __future__ import annotations

import json
from typing import Any

import networkx as nx


class TopologyGraph:
    """Static directed service dependency graph for the OTel Demo stack.

    7 nodes: 6 core OTel Demo services + Redis.
    9 edges representing synchronous call dependencies.
    """

    # OTel Astronomy Shop service dependencies (7 nodes: 6 core services + redis)
    # Excluded from reduced stack: emailservice, shippingservice, recommendationservice, adservice
    # Format: (dependency, dependent) — dependency is upstream of dependent
    KNOWN_EDGES: list[tuple[str, str]] = [
        ("redis", "cartservice"),
        ("cartservice", "checkoutservice"),
        ("productcatalogservice", "checkoutservice"),
        ("currencyservice", "checkoutservice"),
        ("paymentservice", "checkoutservice"),
        ("cartservice", "frontend"),
        ("productcatalogservice", "frontend"),
        ("checkoutservice", "frontend"),
        ("currencyservice", "frontend"),
    ]

    def __init__(self) -> None:
        self.graph: nx.DiGraph = nx.DiGraph()
        self._init_topology()

    def _init_topology(self) -> None:
        """Populate the graph with known OTel Demo service dependencies."""
        for dep, svc in self.KNOWN_EDGES:
            self.graph.add_edge(dep, svc, protocol="gRPC", avg_latency_ms=0.0)

    def get_subgraph(self, service_name: str) -> dict[str, Any]:
        """Return the subgraph centered on a given service.

        Returns:
            dict with keys:
              nodes: list of node dicts ({name, ...attributes})
              edges: list of edge dicts ({source, target, protocol, avg_latency_ms})
              upstream: services that service_name depends on (potential root causes)
              downstream: services that depend on service_name (show symptoms)
        """
        if service_name not in self.graph:
            return {
                "nodes": [],
                "edges": [],
                "upstream": [],
                "downstream": [],
            }

        return {
            "nodes": [
                {"name": n, **self.graph.nodes[n]}
                for n in self.graph.nodes()
                if n == service_name
                or service_name in self.graph.predecessors(n)
                or service_name in self.graph.successors(n)
            ],
            "edges": [
                {"source": u, "target": v, **self.graph.edges[u, v]}
                for u, v in self.graph.edges()
                if service_name in (u, v)
            ],
            "upstream": list(self.graph.predecessors(service_name)),
            "downstream": list(self.graph.successors(service_name)),
        }

    def to_json(self) -> str:
        """Serialize the full topology to JSON (used by get_topology agent tool)."""
        return json.dumps(
            {
                "nodes": [
                    {"name": n, **self.graph.nodes[n]} for n in self.graph.nodes()
                ],
                "edges": [
                    {"source": u, "target": v, **self.graph.edges[u, v]}
                    for u, v in self.graph.edges()
                ],
            },
            indent=2,
        )
