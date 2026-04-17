"""Unit tests for the 5 OpsAgent agent tools.

All external services (Prometheus, Loki, ChromaDB) are mocked at the
client level using ``unittest.mock.patch``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# TestGetTopology
# ═══════════════════════════════════════════════════════════════════════════


class TestGetTopology:
    """Tests for the get_topology agent tool."""

    def test_full_topology_returns_all_nodes(self) -> None:
        from src.agent.tools.get_topology import get_topology

        result = get_topology.invoke({"service_name": None})
        node_names = [n["name"] for n in result["nodes"]]
        assert len(node_names) == 7
        assert "redis" in node_names
        assert "frontend" in node_names

    def test_full_topology_returns_all_edges(self) -> None:
        from src.agent.tools.get_topology import get_topology

        result = get_topology.invoke({"service_name": None})
        assert len(result["edges"]) == 9

    def test_subgraph_cartservice(self) -> None:
        from src.agent.tools.get_topology import get_topology

        result = get_topology.invoke({"service_name": "cartservice"})
        assert "redis" in result["upstream"]
        assert "checkoutservice" in result["downstream"] or "frontend" in result["downstream"]

    def test_subgraph_unknown_service(self) -> None:
        from src.agent.tools.get_topology import get_topology

        result = get_topology.invoke({"service_name": "nonexistent"})
        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["upstream"] == []
        assert result["downstream"] == []

    def test_none_returns_full_with_empty_upstream_downstream(self) -> None:
        from src.agent.tools.get_topology import get_topology

        result = get_topology.invoke({"service_name": None})
        assert result["upstream"] == []
        assert result["downstream"] == []

    def test_return_structure(self) -> None:
        from src.agent.tools.get_topology import get_topology

        result = get_topology.invoke({"service_name": "frontend"})
        assert "nodes" in result
        assert "edges" in result
        assert "upstream" in result
        assert "downstream" in result


# ═══════════════════════════════════════════════════════════════════════════
# TestQueryMetrics
# ═══════════════════════════════════════════════════════════════════════════


class TestQueryMetrics:
    """Tests for the query_metrics agent tool."""

    def _mock_range_response(self, values: list[float]) -> list[dict]:
        """Build a mock Prometheus range_query return value.

        Uses recent timestamps (relative to now) so that stale/sparse
        data detection in query_metrics doesn't false-trigger on test data.
        """
        import time

        now = int(time.time())
        # Place values at 15s intervals ending at "now"
        start = now - (len(values) - 1) * 15
        return [
            {
                "metric": {"service": "frontend"},
                "values": [[start + i * 15, str(v)] for i, v in enumerate(values)],
            }
        ]

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_valid_cpu_metric(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.query_metrics import query_metrics

        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = self._mock_range_response(
            [0.05, 0.06, 0.07, 0.08, 0.09]
        )
        result = query_metrics.invoke({"service_name": "frontend", "metric_name": "cpu_usage"})
        assert len(result["values"]) == 5
        assert len(result["timestamps"]) == 5
        assert "stats" in result
        assert "min" in result["stats"]

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_valid_memory_metric(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.query_metrics import query_metrics

        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = self._mock_range_response([200e6, 201e6, 202e6])
        result = query_metrics.invoke(
            {"service_name": "cartservice", "metric_name": "memory_usage"}
        )
        assert len(result["values"]) == 3
        assert result["stats"]["mean"] == pytest.approx(201e6, rel=0.01)

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_stats_computation(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.query_metrics import query_metrics

        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = self._mock_range_response(values)
        result = query_metrics.invoke({"service_name": "frontend", "metric_name": "cpu_usage"})
        assert result["stats"]["min"] == pytest.approx(10.0)
        assert result["stats"]["max"] == pytest.approx(50.0)
        assert result["stats"]["mean"] == pytest.approx(30.0)
        assert result["stats"]["current"] == pytest.approx(50.0)

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_anomalous_flag_true(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.query_metrics import query_metrics

        # Normal baseline + a huge spike at the end (needs to be > 2σ from mean)
        values = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1000.0]
        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = self._mock_range_response(values)
        result = query_metrics.invoke({"service_name": "frontend", "metric_name": "cpu_usage"})
        assert result["anomalous"] is True

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_anomalous_flag_false(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.query_metrics import query_metrics

        # Use enough data points to exceed 90% coverage for a 30-min window
        # (expected ~120 points at 15s interval). 115 points = 96% coverage.
        values = [1.0 + 0.01 * (i % 10) for i in range(115)]
        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = self._mock_range_response(values)
        result = query_metrics.invoke({"service_name": "frontend", "metric_name": "cpu_usage"})
        assert result["anomalous"] is False

    def test_invalid_metric_name(self) -> None:
        from src.agent.tools.query_metrics import query_metrics

        result = query_metrics.invoke({"service_name": "frontend", "metric_name": "invalid_metric"})
        assert "error" in result
        assert result["timestamps"] == []

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_prometheus_connection_error(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.query_metrics import query_metrics

        mock_instance = mock_cls.return_value
        mock_instance.range_query.side_effect = ConnectionError("Cannot connect")
        result = query_metrics.invoke({"service_name": "frontend", "metric_name": "cpu_usage"})
        assert "error" in result
        assert result["values"] == []

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_empty_prometheus_response(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.query_metrics import query_metrics

        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = []
        result = query_metrics.invoke({"service_name": "frontend", "metric_name": "cpu_usage"})
        assert result["values"] == []
        assert result["timestamps"] == []


# ═══════════════════════════════════════════════════════════════════════════
# TestSearchLogs
# ═══════════════════════════════════════════════════════════════════════════


class TestSearchLogs:
    """Tests for the search_logs agent tool."""

    def _mock_loki_response(
        self,
        entries: list[tuple[str, str]] | None = None,
        service: str = "cartservice",
    ) -> dict[str, Any]:
        if entries is None:
            entries = [
                ("1704067200000000000", "ERROR: Connection refused to redis:6379"),
                ("1704067201000000000", "INFO: Request processed successfully"),
                ("1704067202000000000", "WARN: Retry attempt 3 for upstream"),
            ]
        return {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {"service": service, "job": "docker"},
                        "values": entries,
                    }
                ],
            },
        }

    @patch("src.agent.tools.search_logs.requests.get")
    def test_service_filter_logql(self, mock_get: MagicMock) -> None:
        from src.agent.tools.search_logs import search_logs

        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_loki_response()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        search_logs.invoke({"query": "error", "service_filter": "cartservice"})

        call_args = mock_get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params", {})
        assert 'service="cartservice"' in params["query"]

    @patch("src.agent.tools.search_logs.requests.get")
    def test_no_filter_searches_all(self, mock_get: MagicMock) -> None:
        from src.agent.tools.search_logs import search_logs

        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_loki_response()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        search_logs.invoke({"query": "error"})

        call_args = mock_get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params", {})
        assert 'job="docker"' in params["query"]

    @patch("src.agent.tools.search_logs.requests.get")
    def test_correct_result_structure(self, mock_get: MagicMock) -> None:
        from src.agent.tools.search_logs import search_logs

        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_loki_response()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = search_logs.invoke({"query": "error"})
        assert "entries" in result
        assert "total_count" in result
        assert "error_count" in result
        assert "top_patterns" in result

    @patch("src.agent.tools.search_logs.requests.get")
    def test_error_count(self, mock_get: MagicMock) -> None:
        from src.agent.tools.search_logs import search_logs

        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_loki_response()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = search_logs.invoke({"query": "error"})
        assert result["error_count"] == 1  # Only one ERROR entry
        assert result["total_count"] == 3

    @patch("src.agent.tools.search_logs.requests.get")
    def test_limit_parameter(self, mock_get: MagicMock) -> None:
        from src.agent.tools.search_logs import search_logs

        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_loki_response()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        search_logs.invoke({"query": "error", "limit": 50})

        call_args = mock_get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params", {})
        assert params["limit"] == "50"

    @patch("src.agent.tools.search_logs.requests.get")
    def test_loki_connection_error(self, mock_get: MagicMock) -> None:
        from src.agent.tools.search_logs import search_logs

        mock_get.side_effect = ConnectionError("Cannot connect")
        result = search_logs.invoke({"query": "error"})
        assert "error" in result
        assert result["total_count"] == 0
        assert result["entries"] == []

    @patch("src.agent.tools.search_logs.requests.get")
    def test_empty_loki_response(self, mock_get: MagicMock) -> None:
        from src.agent.tools.search_logs import search_logs

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "success",
            "data": {"resultType": "streams", "result": []},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = search_logs.invoke({"query": "nonexistent"})
        assert result["entries"] == []
        assert result["total_count"] == 0
        assert result["error_count"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# TestSearchRunbooks
# ═══════════════════════════════════════════════════════════════════════════


class TestSearchRunbooks:
    """Tests for the search_runbooks agent tool."""

    @patch("src.agent.tools.search_runbooks.RunbookIndexer")
    def test_returns_results_structure(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.search_runbooks import search_runbooks

        mock_instance = mock_cls.return_value
        mock_instance.search.return_value = [
            {"content": "Restart the service", "source": "general.md", "relevance_score": 0.85},
        ]
        result = search_runbooks.invoke({"query": "service restart"})
        assert "results" in result
        assert len(result["results"]) == 1
        r = result["results"][0]
        assert "title" in r
        assert "content" in r
        assert "relevance_score" in r
        assert "source" in r

    @patch("src.agent.tools.search_runbooks.RunbookIndexer")
    def test_empty_query_results(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.search_runbooks import search_runbooks

        mock_instance = mock_cls.return_value
        mock_instance.search.return_value = []
        result = search_runbooks.invoke({"query": "zzz obscure query zzz"})
        assert result["results"] == []

    @patch("src.agent.tools.search_runbooks.RunbookIndexer")
    def test_top_k_parameter(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.search_runbooks import search_runbooks

        mock_instance = mock_cls.return_value
        mock_instance.search.return_value = [
            {"content": "Result 1", "source": "a.md", "relevance_score": 0.9},
        ]
        search_runbooks.invoke({"query": "test", "top_k": 1})
        mock_instance.search.assert_called_with("test", top_k=1)

    @patch("src.agent.tools.search_runbooks.RunbookIndexer")
    def test_chromadb_error(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.search_runbooks import search_runbooks

        mock_cls.side_effect = RuntimeError("ChromaDB unavailable")
        result = search_runbooks.invoke({"query": "test"})
        assert "error" in result
        assert result["results"] == []

    @patch("src.agent.tools.search_runbooks.RunbookIndexer")
    def test_relevance_scores_in_range(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.search_runbooks import search_runbooks

        mock_instance = mock_cls.return_value
        mock_instance.search.return_value = [
            {"content": "r1", "source": "a.md", "relevance_score": 0.92},
            {"content": "r2", "source": "b.md", "relevance_score": 0.71},
        ]
        result = search_runbooks.invoke({"query": "test"})
        for r in result["results"]:
            assert 0.0 <= r["relevance_score"] <= 1.0


# ═══════════════════════════════════════════════════════════════════════════
# TestDiscoverCausation
# ═══════════════════════════════════════════════════════════════════════════


class TestDiscoverCausation:
    """Tests for the discover_causation agent tool."""

    @patch("src.agent.tools.discover_causation.MetricsCollector")
    def test_returns_correct_structure(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.discover_causation import discover_causation

        rng = np.random.default_rng(42)
        n = 60
        a_vals = rng.normal(0, 1, n).tolist()
        b_vals = (0.8 * np.array(a_vals) + rng.normal(0, 0.3, n)).tolist()

        mock_instance = mock_cls.return_value
        mock_instance.get_service_metrics.side_effect = [
            {"cpu": a_vals, "memory": a_vals},
            {"cpu": b_vals, "memory": b_vals},
        ]

        result = discover_causation.invoke(
            {"services": ["svc_a", "svc_b"], "time_range_minutes": 30}
        )
        assert "causal_edges" in result
        assert "root_cause" in result
        assert "root_cause_confidence" in result
        assert "counterfactual" in result
        assert "graph_ascii" in result

    @patch("src.agent.tools.discover_causation.MetricsCollector")
    def test_confidence_in_range(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.discover_causation import discover_causation

        rng = np.random.default_rng(42)
        n = 60
        a_vals = rng.normal(0, 1, n).tolist()
        b_vals = (0.8 * np.array(a_vals) + rng.normal(0, 0.3, n)).tolist()

        mock_instance = mock_cls.return_value
        mock_instance.get_service_metrics.side_effect = [
            {"cpu": a_vals, "memory": a_vals},
            {"cpu": b_vals, "memory": b_vals},
        ]

        result = discover_causation.invoke(
            {"services": ["svc_a", "svc_b"], "time_range_minutes": 30}
        )
        assert 0.0 <= result["root_cause_confidence"] <= 1.0

    def test_single_service_error(self) -> None:
        from src.agent.tools.discover_causation import discover_causation

        result = discover_causation.invoke({"services": ["only_one"], "time_range_minutes": 30})
        assert "error" in result
        assert result["root_cause"] == "only_one"

    @patch("src.agent.tools.discover_causation.MetricsCollector")
    def test_insufficient_data(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.discover_causation import discover_causation

        mock_instance = mock_cls.return_value
        mock_instance.get_service_metrics.return_value = {"cpu": [1.0, 2.0]}

        result = discover_causation.invoke(
            {"services": ["svc_a", "svc_b"], "time_range_minutes": 30}
        )
        assert result["root_cause"] == "inconclusive"

    @patch("src.agent.tools.discover_causation.MetricsCollector")
    def test_prometheus_failure(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.discover_causation import discover_causation

        mock_instance = mock_cls.return_value
        mock_instance.get_service_metrics.side_effect = ConnectionError("down")

        result = discover_causation.invoke(
            {"services": ["svc_a", "svc_b"], "time_range_minutes": 30}
        )
        assert result["root_cause"] == "inconclusive"

    @patch("src.agent.tools.discover_causation.MetricsCollector")
    def test_empty_metrics_response(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.discover_causation import discover_causation

        mock_instance = mock_cls.return_value
        mock_instance.get_service_metrics.return_value = {}

        result = discover_causation.invoke(
            {"services": ["svc_a", "svc_b"], "time_range_minutes": 30}
        )
        assert result["root_cause"] == "inconclusive"

    @patch("src.agent.tools.discover_causation.MetricsCollector")
    def test_counterfactual_non_empty(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.discover_causation import discover_causation

        rng = np.random.default_rng(42)
        n = 60
        a_vals = rng.normal(0, 1, n).tolist()
        b_vals = (0.8 * np.array(a_vals) + rng.normal(0, 0.3, n)).tolist()

        mock_instance = mock_cls.return_value
        mock_instance.get_service_metrics.side_effect = [
            {"cpu": a_vals, "memory": a_vals},
            {"cpu": b_vals, "memory": b_vals},
        ]

        result = discover_causation.invoke(
            {"services": ["svc_a", "svc_b"], "time_range_minutes": 30}
        )
        assert isinstance(result["counterfactual"], str)
        assert len(result["counterfactual"]) > 0

    @patch("src.agent.tools.discover_causation.MetricsCollector")
    def test_no_data_from_prometheus(self, mock_cls: MagicMock) -> None:
        from src.agent.tools.discover_causation import discover_causation

        mock_instance = mock_cls.return_value
        # Return empty lists for all metrics
        mock_instance.get_service_metrics.return_value = {"cpu": [], "memory": []}

        result = discover_causation.invoke(
            {"services": ["svc_a", "svc_b"], "time_range_minutes": 30}
        )
        assert result["root_cause"] == "inconclusive"
