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
        # Empty container-level metric response is NEUTRAL (not CRITICAL)
        # because we can't distinguish "never reported" (baseline-flaky
        # service like currencyservice) from "recently crashed" without
        # a historical baseline. Sparse/stale CRITICAL handles the latter.
        assert result["values"] == []
        assert result["timestamps"] == []
        assert result["anomalous"] is False
        assert "CRITICAL" not in result.get("note", "")

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_probe_latency_spike_uses_baseline_mean(self, mock_cls: MagicMock) -> None:
        """probe_latency CRITICAL uses first-60% baseline, not full-window mean.

        Without this fix, once half the window is spiked, full-window mean
        equals ~(baseline + spike)/2, and the 10x ratio check fails. With the
        baseline-only mean, the ratio stays >10x.
        """
        from src.agent.tools.query_metrics import query_metrics

        # 40 points: first 24 at baseline 0.001s, last 16 at spike 0.500s.
        # baseline_mean = 0.001, current = 0.500 → ratio = 500x > 10x ✓
        # Contaminated full-window mean = 0.2 → ratio = 2.5x (would fail old check)
        values = [0.001] * 24 + [0.500] * 16
        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = self._mock_range_response(values)
        # Use 10-min window so 40 points = 100% coverage (not 33% sparse).
        result = query_metrics.invoke(
            {
                "service_name": "frontend",
                "metric_name": "probe_latency",
                "time_range_minutes": 10,
            }
        )
        assert result["anomalous"] is True
        assert "CRITICAL" in result["note"]
        assert "baseline_mean" in result["stats"]

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_probe_latency_no_spike_stable_baseline(self, mock_cls: MagicMock) -> None:
        """Stable latency must NOT fire CRITICAL."""
        from src.agent.tools.query_metrics import query_metrics

        values = [0.001] * 40
        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = self._mock_range_response(values)
        # Use 10-min window so 40 points = 100% coverage, no sparse false-trip.
        result = query_metrics.invoke(
            {
                "service_name": "frontend",
                "metric_name": "probe_latency",
                "time_range_minutes": 10,
            }
        )
        assert "CRITICAL" not in result.get("note", "")

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_probe_up_fresh_drop_fires_critical(self, mock_cls: MagicMock) -> None:
        """A service healthy for the entire window until the last 2 readings
        must trigger CRITICAL via the fresh-drop check (baseline_mean >= 0.9
        AND last 2 readings both 0), even though fewer than 3 of the last 4
        are zero."""
        from src.agent.tools.query_metrics import query_metrics

        # 40 points: 38 healthy (1.0) then 2 zeros. Last 4 = [1, 1, 0, 0] →
        # old "3+ of 4 zeros" check would NOT fire. Baseline (first 24) = 1.0
        # and last 2 are 0 → fresh-drop DOES fire.
        values = [1.0] * 38 + [0.0, 0.0]
        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = self._mock_range_response(values)
        result = query_metrics.invoke(
            {
                "service_name": "paymentservice",
                "metric_name": "probe_up",
                "time_range_minutes": 10,
            }
        )
        assert result["anomalous"] is True
        assert "CRITICAL" in result["note"]
        assert "baseline_mean" in result["stats"]

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_probe_exporter_unavailable_is_not_critical(self, mock_cls: MagicMock) -> None:
        """If the probe exporter is down (no series returned), the response
        is neutral, not CRITICAL — we can't tell whether the service is down
        or the exporter is down."""
        from src.agent.tools.query_metrics import query_metrics

        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = []
        result = query_metrics.invoke(
            {"service_name": "frontend", "metric_name": "probe_up"}
        )
        assert result["anomalous"] is False
        assert "probe exporter unavailable" in result.get("note", "")

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_start_time_sets_window(self, mock_cls: MagicMock) -> None:
        """When start_time is given, the collector is called with a window
        that extends 60s BEFORE the anchor (for pre-fault baseline context)
        and ends at min(anchor + time_range, now)."""
        from datetime import UTC, datetime, timedelta

        from src.agent.tools.query_metrics import query_metrics

        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = self._mock_range_response([0.5] * 40)

        # Use a start time far in the past so end clamps naturally.
        anchor = datetime.now(UTC) - timedelta(hours=2)
        iso = anchor.isoformat()
        query_metrics.invoke(
            {
                "service_name": "frontend",
                "metric_name": "cpu_usage",
                "time_range_minutes": 10,
                "start_time": iso,
            }
        )
        call = mock_instance.range_query.call_args
        actual_start: datetime = call.args[1]
        actual_end: datetime = call.args[2]
        # start must be 60s BEFORE the pinned anchor (pre-fault baseline)
        expected_start = anchor - timedelta(seconds=60)
        assert abs((actual_start - expected_start).total_seconds()) < 1
        # end must be anchor + 10min (anchor + 10min is still in the past)
        expected_end = anchor + timedelta(minutes=10)
        assert abs((actual_end - expected_end).total_seconds()) < 1

    @patch("src.agent.tools.query_metrics.MetricsCollector")
    def test_start_time_clamps_future_end_to_now(self, mock_cls: MagicMock) -> None:
        """If start_time + time_range_minutes is in the future, end is
        clamped to now so we don't query data that doesn't exist yet."""
        from datetime import UTC, datetime, timedelta

        from src.agent.tools.query_metrics import query_metrics

        mock_instance = mock_cls.return_value
        mock_instance.range_query.return_value = self._mock_range_response([0.5] * 8)

        # start = 2 minutes ago → theoretical end is 8 min in future → clamp.
        near = datetime.now(UTC) - timedelta(minutes=2)
        query_metrics.invoke(
            {
                "service_name": "frontend",
                "metric_name": "cpu_usage",
                "time_range_minutes": 10,
                "start_time": near.isoformat(),
            }
        )
        call = mock_instance.range_query.call_args
        actual_end: datetime = call.args[2]
        now = datetime.now(UTC)
        # end should be within a few seconds of now, NOT 8 minutes in future
        assert abs((actual_end - now).total_seconds()) < 5


# ═══════════════════════════════════════════════════════════════════════════
# TestSearchLogs
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildLogQL:
    """Tests for the _build_logql helper (LogQL construction).

    Ensures OR-style queries produced by the LLM or the forced log search
    are converted into a regex line filter instead of a broken literal
    filter with embedded quotes.
    """

    def test_single_term_uses_literal_filter(self) -> None:
        from src.agent.tools.search_logs import _build_logql

        logql = _build_logql("OOM", "cartservice")
        assert '|= `OOM`' in logql
        assert 'service="cartservice"' in logql

    def test_or_alternation_uses_regex_filter(self) -> None:
        from src.agent.tools.search_logs import _build_logql

        logql = _build_logql("panic OR fatal OR error", "productcatalogservice")
        assert "|~" in logql
        # Case-insensitive alternation
        assert "(?i)" in logql
        for term in ("panic", "fatal", "error"):
            assert term in logql

    def test_nested_quotes_stripped(self) -> None:
        """Embedded double quotes inside OR terms must be stripped so they
        can't terminate the LogQL string literal (the bug that produced
        400 Bad Request during the April 17 eval run)."""
        from src.agent.tools.search_logs import _build_logql

        logql = _build_logql('panic OR fatal OR "bind"', "productcatalogservice")
        assert '"bind"' not in logql
        # bind itself must still be present as a regex term
        assert "bind" in logql

    def test_backtick_string_literal(self) -> None:
        """Use backtick strings for LogQL to avoid any quote-escaping issues."""
        from src.agent.tools.search_logs import _build_logql

        logql = _build_logql("connection refused", "cartservice")
        # backticks, not double-quotes, around the term
        assert '`connection refused`' in logql

    def test_no_service_filter_uses_job_label(self) -> None:
        from src.agent.tools.search_logs import _build_logql

        logql = _build_logql("error", None)
        assert 'job="docker"' in logql


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

    def _mock_crash_loki_response(self, service: str, crash_count: int) -> dict:
        """Build a Loki response with ``crash_count`` crash-pattern matches."""
        crash_msgs = [
            "FATAL ERROR: OOMKilled by kernel oom_reaper",
            "panic: runtime error: nil pointer",
            "SIGSEGV: segmentation fault at 0x0",
            "terminate called after throwing an instance of std::logic_error",
            "container exited with code 137",
        ]
        entries = [
            (str(1704067200_000000000 + i), crash_msgs[i % len(crash_msgs)])
            for i in range(crash_count)
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
    def test_crash_signal_escalates_to_critical(self, mock_get: MagicMock) -> None:
        """When a log search returns ≥3 crash-pattern matches for a specific
        service, the result dict includes a ``critical_service`` field and a
        ``CRITICAL: …`` note. This is what Fix 17 uses to propagate crash
        signals to ``analyze_causation_node``."""
        from src.agent.tools.search_logs import search_logs

        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_crash_loki_response(
            service="checkoutservice", crash_count=5
        )
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = search_logs.invoke(
            {"query": "OOM OR panic OR fatal", "service_filter": "checkoutservice"}
        )
        assert result.get("critical_service") == "checkoutservice"
        assert result.get("anomalous") is True
        assert "CRITICAL" in result.get("note", "")
        assert result.get("crash_match_count") == 5

    @patch("src.agent.tools.search_logs.requests.get")
    def test_single_crash_match_no_escalation(self, mock_get: MagicMock) -> None:
        """One stray fatal message isn't enough to escalate — the ≥3
        threshold filters out one-off transient warnings."""
        from src.agent.tools.search_logs import search_logs

        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_crash_loki_response(
            service="checkoutservice", crash_count=1
        )
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = search_logs.invoke(
            {"query": "fatal", "service_filter": "checkoutservice"}
        )
        assert "critical_service" not in result
        assert "CRITICAL" not in result.get("note", "")
        assert result.get("crash_match_count") == 1

    @patch("src.agent.tools.search_logs.requests.get")
    def test_no_service_filter_no_escalation(self, mock_get: MagicMock) -> None:
        """Without service_filter, a crash signature can't be attributed to
        one service, so we refuse to escalate even with many matches."""
        from src.agent.tools.search_logs import search_logs

        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_crash_loki_response(
            service="any", crash_count=10
        )
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = search_logs.invoke({"query": "panic OR OOM OR fatal"})
        assert "critical_service" not in result


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

    @patch("src.agent.tools.discover_causation.MetricsCollector")
    def test_critical_service_becomes_root(self, mock_cls: MagicMock) -> None:
        """With critical_services=['svc_a'], svc_a is excluded from PC input
        but becomes the root cause and synthesized edges are produced."""
        from src.agent.tools.discover_causation import discover_causation

        rng = np.random.default_rng(42)
        n = 40
        # Only svc_b and svc_c get metrics (svc_a is critical → excluded from PC)
        b_vals = rng.normal(0, 1, n).tolist()
        c_vals = (0.8 * np.array(b_vals) + rng.normal(0, 0.3, n)).tolist()

        mock_instance = mock_cls.return_value
        # side_effect only consumed for non-critical services (svc_b, svc_c)
        mock_instance.get_service_metrics.side_effect = [
            {
                "cpu": b_vals,
                "memory": b_vals,
                "net_rx": b_vals,
                "net_tx": b_vals,
                "probe_up": [1.0] * n,
                "probe_latency": [0.01] * n,
            },
            {
                "cpu": c_vals,
                "memory": c_vals,
                "net_rx": c_vals,
                "net_tx": c_vals,
                "probe_up": [1.0] * n,
                "probe_latency": [0.01] * n,
            },
        ]
        result = discover_causation.invoke(
            {
                "services": ["svc_a", "svc_b", "svc_c"],
                "time_range_minutes": 10,
                "critical_services": ["svc_a"],
            }
        )
        # Root cause must be the critical service with confidence >= 0.75
        assert result["root_cause"] == "svc_a"
        assert result["root_cause_confidence"] >= 0.75
        # Only svc_b and svc_c were fetched — svc_a was skipped
        assert mock_instance.get_service_metrics.call_count == 2

    def test_metric_set_excludes_spanmetrics(self) -> None:
        """_CAUSAL_METRICS must NOT include spanmetrics (request_rate /
        error_rate / latency). Spanmetrics are unavailable for non-trace
        services (cartservice / redis / currencyservice) and their empty
        series crashed PC on every investigation."""
        from src.agent.tools.discover_causation import _CAUSAL_METRICS

        assert "request_rate" not in _CAUSAL_METRICS
        assert "error_rate" not in _CAUSAL_METRICS
        assert "latency" not in _CAUSAL_METRICS
        # Container + probe metrics must still be present.
        for required in ("cpu", "memory", "net_rx", "net_tx", "probe_up", "probe_latency"):
            assert required in _CAUSAL_METRICS

    @patch("src.agent.tools.discover_causation.MetricsCollector")
    def test_partial_data_still_runs_pc(self, mock_cls: MagicMock) -> None:
        """With the softened gate (min_len>=6, <=30% short columns), partial
        data from one service should not abort the whole analysis."""
        from src.agent.tools.discover_causation import discover_causation

        rng = np.random.default_rng(42)
        n = 40
        a_vals = rng.normal(0, 1, n).tolist()
        b_vals = (0.8 * np.array(a_vals) + rng.normal(0, 0.3, n)).tolist()

        # Six metrics each: all 40 points for cpu / memory / net_rx / net_tx,
        # with probe_up / probe_latency at 40 points too — all well above 6.
        # (Short-column case exercised separately via a synthetic scenario.)
        mock_instance = mock_cls.return_value
        mock_instance.get_service_metrics.side_effect = [
            {
                "cpu": a_vals,
                "memory": a_vals,
                "net_rx": a_vals,
                "net_tx": a_vals,
                "probe_up": [1.0] * n,
                "probe_latency": [0.01] * n,
            },
            {
                "cpu": b_vals,
                "memory": b_vals,
                "net_rx": b_vals,
                "net_tx": b_vals,
                "probe_up": [1.0] * n,
                "probe_latency": [0.01] * n,
            },
        ]

        result = discover_causation.invoke(
            {"services": ["svc_a", "svc_b"], "time_range_minutes": 10}
        )
        # With 40 points + strong correlation, PC should produce something
        # other than "inconclusive due to insufficient data".
        assert "Insufficient data points" not in result.get("counterfactual", "")


# ═══════════════════════════════════════════════════════════════════════════
# TestMetricsCollector (Fix 8: zero-fill + NaN filter)
# ═══════════════════════════════════════════════════════════════════════════


class TestMetricsCollector:
    """Tests for get_service_metrics zero-fill and NaN handling."""

    @patch("src.data_collection.metrics_collector.requests.get")
    def test_empty_range_returns_zero_filled(self, mock_get: MagicMock) -> None:
        """Empty Prometheus range response must zero-fill to the expected step
        count so downstream consumers (causal discovery) don't bail."""
        from datetime import UTC, datetime, timedelta

        from src.data_collection.metrics_collector import MetricsCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "success", "data": {"result": []}}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        collector = MetricsCollector()
        end = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        start = end - timedelta(minutes=10)  # 600s window
        result = collector.get_service_metrics(
            service="frontend",
            metric_queries={"error_rate": "foo"},
            start=start,
            end=end,
            step="15s",
        )
        # Expected steps = 600 / 15 = 40
        assert len(result["error_rate"]) == 40
        assert all(v == 0.0 for v in result["error_rate"])

    @patch("src.data_collection.metrics_collector.requests.get")
    def test_nan_values_filtered(self, mock_get: MagicMock) -> None:
        """NaN values (e.g. histogram_quantile on empty buckets) must be
        coerced to 0.0 so they don't propagate through pandas."""
        from datetime import UTC, datetime, timedelta

        from src.data_collection.metrics_collector import MetricsCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"service": "frontend"},
                        "values": [[1000, "1.0"], [1015, "NaN"], [1030, "2.0"]],
                    }
                ]
            },
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        collector = MetricsCollector()
        end = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        start = end - timedelta(seconds=45)
        result = collector.get_service_metrics(
            service="frontend",
            metric_queries={"latency": "foo"},
            start=start,
            end=end,
            step="15s",
        )
        vals = result["latency"]
        assert 1.0 in vals
        assert 2.0 in vals
        # NaN coerced to 0.0 — no NaN should remain.
        for v in vals:
            import math

            assert not math.isnan(v)

    @patch("src.data_collection.metrics_collector.requests.get")
    def test_instant_query_empty_returns_zero(self, mock_get: MagicMock) -> None:
        """Empty instant query returns [0.0], not []."""
        from src.data_collection.metrics_collector import MetricsCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "success", "data": {"result": []}}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        collector = MetricsCollector()
        result = collector.get_service_metrics(
            service="frontend",
            metric_queries={"cpu": "foo"},
        )
        assert result["cpu"] == [0.0]

    def test_parse_step_seconds(self) -> None:
        """_parse_step_seconds must handle 15s, 1m, 30, etc."""
        from src.data_collection.metrics_collector import _parse_step_seconds

        assert _parse_step_seconds("15s") == 15.0
        assert _parse_step_seconds("1m") == 60.0
        assert _parse_step_seconds("30") == 30.0
        # Unparseable defaults to 15s.
        assert _parse_step_seconds("garbage") == 15.0
