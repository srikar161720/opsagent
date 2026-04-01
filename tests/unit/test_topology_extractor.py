"""Tests for TopologyGraph service dependency graph."""

from __future__ import annotations

import json

from src.data_collection.topology_extractor import TopologyGraph

EXPECTED_NODES = {
    "redis",
    "cartservice",
    "checkoutservice",
    "paymentservice",
    "productcatalogservice",
    "currencyservice",
    "frontend",
}


class TestTopologyGraph:
    """Tests for the static OTel Demo service topology."""

    def test_graph_has_7_nodes(self) -> None:
        topo = TopologyGraph()
        assert len(topo.graph.nodes()) == 7

    def test_graph_has_9_edges(self) -> None:
        topo = TopologyGraph()
        assert len(topo.graph.edges()) == 9

    def test_all_expected_nodes_present(self) -> None:
        topo = TopologyGraph()
        assert set(topo.graph.nodes()) == EXPECTED_NODES

    def test_edge_attributes_protocol(self) -> None:
        topo = TopologyGraph()
        for u, v, data in topo.graph.edges(data=True):
            assert data["protocol"] == "gRPC", f"Edge ({u}, {v}) missing protocol"

    def test_edge_attributes_latency(self) -> None:
        topo = TopologyGraph()
        for u, v, data in topo.graph.edges(data=True):
            assert data["avg_latency_ms"] == 0.0, f"Edge ({u}, {v}) wrong latency"

    def test_subgraph_frontend(self) -> None:
        topo = TopologyGraph()
        sub = topo.get_subgraph("frontend")
        # frontend has 4 upstream deps, 0 downstream
        assert set(sub["upstream"]) == {
            "cartservice",
            "productcatalogservice",
            "checkoutservice",
            "currencyservice",
        }
        assert sub["downstream"] == []

    def test_subgraph_redis(self) -> None:
        topo = TopologyGraph()
        sub = topo.get_subgraph("redis")
        # redis has 0 upstream deps, 1 downstream (cartservice)
        assert sub["upstream"] == []
        assert sub["downstream"] == ["cartservice"]

    def test_subgraph_checkoutservice(self) -> None:
        topo = TopologyGraph()
        sub = topo.get_subgraph("checkoutservice")
        assert set(sub["upstream"]) == {
            "cartservice",
            "productcatalogservice",
            "currencyservice",
            "paymentservice",
        }
        assert sub["downstream"] == ["frontend"]

    def test_to_json_valid_structure(self) -> None:
        topo = TopologyGraph()
        data = json.loads(topo.to_json())
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 7
        assert len(data["edges"]) == 9

    def test_subgraph_unknown_service(self) -> None:
        topo = TopologyGraph()
        sub = topo.get_subgraph("nonexistent_service")
        assert sub["nodes"] == []
        assert sub["edges"] == []
        assert sub["upstream"] == []
        assert sub["downstream"] == []
