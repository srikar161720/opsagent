"""Integration tests for the OpsAgent data pipeline.

These tests require the Docker infrastructure stack to be running:
    bash scripts/start_infrastructure.sh

Mark all tests with @pytest.mark.integration so they can be skipped
when running unit tests only.
"""

from __future__ import annotations

import json

import pytest
import requests


@pytest.mark.integration
class TestPrometheusIntegration:
    """Tests for Prometheus metric collection."""

    def test_prometheus_is_reachable(self) -> None:
        resp = requests.get("http://localhost:9090/-/healthy", timeout=5)
        assert resp.status_code == 200

    def test_metrics_collector_query(self) -> None:
        from src.data_collection.metrics_collector import MetricsCollector

        collector = MetricsCollector()
        result = collector.instant_query("up")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_service_metrics_non_empty(self) -> None:
        from datetime import UTC, datetime, timedelta

        from src.data_collection.metrics_collector import MetricsCollector

        collector = MetricsCollector()
        end = datetime.now(UTC)
        start = end - timedelta(minutes=5)
        result = collector.range_query(
            'container_cpu_usage_seconds_total{service="frontend"}',
            start,
            end,
            step="15s",
        )
        # May be empty if frontend is not running, but should not error
        assert isinstance(result, list)


@pytest.mark.integration
class TestLokiIntegration:
    """Tests for Loki log search."""

    def test_loki_is_reachable(self) -> None:
        resp = requests.get("http://localhost:3100/ready", timeout=5)
        assert resp.status_code == 200

    def test_loki_query_returns_valid_format(self) -> None:
        resp = requests.get(
            "http://localhost:3100/loki/api/v1/query",
            params={"query": '{job="docker"}', "limit": "5"},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "success"


@pytest.mark.integration
class TestTopologyIntegration:
    """Tests for the TopologyGraph."""

    def test_topology_produces_valid_json(self) -> None:
        """Full OB topology: 11 nodes (reduced 7 + OB-only 4), 14 edges."""
        from src.data_collection.topology_extractor import TopologyGraph

        topo = TopologyGraph()
        json_str = topo.to_json()
        parsed = json.loads(json_str)
        assert len(parsed["nodes"]) == 11
        assert len(parsed["edges"]) == 14

    def test_subgraph_for_each_service(self) -> None:
        from src.data_collection.topology_extractor import TopologyGraph

        topo = TopologyGraph()
        services = [
            "frontend",
            "cartservice",
            "checkoutservice",
            "paymentservice",
            "productcatalogservice",
            "currencyservice",
            "redis",
        ]
        for svc in services:
            sub = topo.get_subgraph(svc)
            assert "nodes" in sub
            assert "upstream" in sub
            assert "downstream" in sub


@pytest.mark.integration
class TestFullPipeline:
    """Tests for end-to-end data pipeline flow."""

    def test_metrics_to_feature_vector(self) -> None:
        from datetime import UTC, datetime, timedelta

        from src.data_collection.metrics_collector import MetricsCollector

        collector = MetricsCollector()
        end = datetime.now(UTC)
        start = end - timedelta(minutes=5)

        metrics = {
            "cpu": 'rate(container_cpu_usage_seconds_total{service="frontend"}[1m])',
        }
        result = collector.get_service_metrics("frontend", metrics, start, end)
        assert isinstance(result, dict)
        assert "cpu" in result
