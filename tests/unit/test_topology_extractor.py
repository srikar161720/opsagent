"""Tests for TopologyGraph service dependency graph."""

from __future__ import annotations

import json

from src.data_collection.topology_extractor import TopologyGraph

# Full OTel Astronomy Shop / Online Boutique topology. The reduced local
# OTel Demo stack only runs the first 7 services; the last 4 are added to
# the graph so RCAEval-OB cases can attribute faults to them.
EXPECTED_NODES = {
    "redis",
    "cartservice",
    "checkoutservice",
    "paymentservice",
    "productcatalogservice",
    "currencyservice",
    "frontend",
    # OB extension (not running locally — safe; query_metrics returns
    # neutral responses for missing services).
    "adservice",
    "emailservice",
    "recommendationservice",
    "shippingservice",
}

# Services actually running in the reduced local OTel Demo. Used by
# live-path behavioural guards below.
OTEL_DEMO_REDUCED = {
    "redis",
    "cartservice",
    "checkoutservice",
    "paymentservice",
    "productcatalogservice",
    "currencyservice",
    "frontend",
}


class TestTopologyGraph:
    """Tests for the static OTel Astronomy Shop / Online Boutique topology."""

    def test_graph_has_11_nodes(self) -> None:
        """Full OB topology has 11 nodes (reduced 7 + OB-only 4)."""
        topo = TopologyGraph()
        assert len(topo.graph.nodes()) == 11

    def test_graph_has_14_edges(self) -> None:
        """9 reduced OTel Demo edges + 5 OB extension edges."""
        topo = TopologyGraph()
        assert len(topo.graph.edges()) == 14

    def test_all_expected_nodes_present(self) -> None:
        topo = TopologyGraph()
        assert set(topo.graph.nodes()) == EXPECTED_NODES

    def test_reduced_otel_demo_services_present(self) -> None:
        """The services actually running locally must remain in the graph."""
        topo = TopologyGraph()
        assert OTEL_DEMO_REDUCED.issubset(set(topo.graph.nodes()))

    def test_ob_extension_services_present(self) -> None:
        """RCAEval ground-truth services that aren't in the reduced stack."""
        topo = TopologyGraph()
        assert {
            "adservice",
            "emailservice",
            "recommendationservice",
            "shippingservice",
        }.issubset(set(topo.graph.nodes()))

    def test_edge_attributes_protocol(self) -> None:
        topo = TopologyGraph()
        for u, v, data in topo.graph.edges(data=True):
            assert data["protocol"] == "gRPC", f"Edge ({u}, {v}) missing protocol"

    def test_edge_attributes_latency(self) -> None:
        topo = TopologyGraph()
        for u, v, data in topo.graph.edges(data=True):
            assert data["avg_latency_ms"] == 0.0, f"Edge ({u}, {v}) wrong latency"

    def test_subgraph_frontend(self) -> None:
        """frontend gains adservice + recommendationservice as upstreams."""
        topo = TopologyGraph()
        sub = topo.get_subgraph("frontend")
        assert set(sub["upstream"]) == {
            "cartservice",
            "productcatalogservice",
            "checkoutservice",
            "currencyservice",
            "adservice",
            "recommendationservice",
        }
        assert sub["downstream"] == []

    def test_subgraph_redis(self) -> None:
        topo = TopologyGraph()
        sub = topo.get_subgraph("redis")
        assert sub["upstream"] == []
        assert sub["downstream"] == ["cartservice"]

    def test_subgraph_checkoutservice(self) -> None:
        """checkoutservice gains emailservice + shippingservice as upstreams."""
        topo = TopologyGraph()
        sub = topo.get_subgraph("checkoutservice")
        assert set(sub["upstream"]) == {
            "cartservice",
            "productcatalogservice",
            "currencyservice",
            "paymentservice",
            "emailservice",
            "shippingservice",
        }
        assert sub["downstream"] == ["frontend"]

    def test_subgraph_adservice(self) -> None:
        """adservice is a leaf: no upstreams, called by frontend only."""
        topo = TopologyGraph()
        sub = topo.get_subgraph("adservice")
        assert sub["upstream"] == []
        assert sub["downstream"] == ["frontend"]

    def test_subgraph_emailservice(self) -> None:
        """emailservice called only by checkoutservice."""
        topo = TopologyGraph()
        sub = topo.get_subgraph("emailservice")
        assert sub["upstream"] == []
        assert sub["downstream"] == ["checkoutservice"]

    def test_subgraph_recommendationservice(self) -> None:
        """recommendationservice calls productcatalogservice, called by frontend."""
        topo = TopologyGraph()
        sub = topo.get_subgraph("recommendationservice")
        assert sub["upstream"] == ["productcatalogservice"]
        assert sub["downstream"] == ["frontend"]

    def test_subgraph_shippingservice(self) -> None:
        """shippingservice is a leaf: no upstreams, called by checkoutservice."""
        topo = TopologyGraph()
        sub = topo.get_subgraph("shippingservice")
        assert sub["upstream"] == []
        assert sub["downstream"] == ["checkoutservice"]

    def test_to_json_valid_structure(self) -> None:
        topo = TopologyGraph()
        data = json.loads(topo.to_json())
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 11
        assert len(data["edges"]) == 14

    def test_subgraph_unknown_service(self) -> None:
        topo = TopologyGraph()
        sub = topo.get_subgraph("nonexistent_service")
        assert sub["nodes"] == []
        assert sub["edges"] == []
        assert sub["upstream"] == []
        assert sub["downstream"] == []
