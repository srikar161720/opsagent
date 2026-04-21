"""Unit tests for ``src.agent.offline_data`` — the offline-mode helpers.

These tests verify that the helpers produce the EXACT same dict shape as
their live counterparts (query_metrics, search_logs, discover_causation)
so downstream graph nodes can process results identically regardless of
the mode. Contract-test coverage: the fields the CRITICAL-override path
reads (``note``, ``anomalous``, ``critical_service``,
``stats["baseline_mean"]``, ``stats["current"]``) must be populated on
the offline path when applicable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.agent import offline_data

# ── Column-name normalization ────────────────────────────────────────────────


class TestCanonicalMetricSeries:
    """Tests for the ``canonical_metric_series`` normalizer."""

    def test_simple_format_resolves_cpu_usage_alias(self) -> None:
        df = pd.DataFrame(
            {
                "cpu_usage": [0.1, 0.2, 0.3],
                "memory_usage": [1e8, 1.1e8, 1.2e8],
            }
        )
        series = offline_data.canonical_metric_series(df, "cpu_usage")
        assert not series.empty
        assert list(series.values) == [0.1, 0.2, 0.3]

    def test_simple_format_resolves_via_short_alias(self) -> None:
        # Some RE1-OB DataFrames retain short metric suffixes like "cpu"
        # before the adapter's rename step; the canonical resolver should
        # still find them.
        df = pd.DataFrame({"cpu": [0.05, 0.1, 0.2]})
        series = offline_data.canonical_metric_series(df, "cpu_usage")
        assert list(series.values) == [0.05, 0.1, 0.2]

    def test_container_format_cpu_matches(self) -> None:
        df = pd.DataFrame(
            {
                "container-cpu-usage-seconds-total": [1.0, 2.0, 3.0],
                "other-unrelated-col": [0, 0, 0],
            }
        )
        series = offline_data.canonical_metric_series(df, "cpu_usage")
        assert list(series.values) == [1.0, 2.0, 3.0]

    def test_container_format_memory_matches(self) -> None:
        df = pd.DataFrame(
            {
                "container-memory-working-set-bytes": [100, 200, 300],
            }
        )
        series = offline_data.canonical_metric_series(df, "memory_usage")
        assert list(series.values) == [100, 200, 300]

    def test_container_format_network_rx_matches(self) -> None:
        df = pd.DataFrame(
            {
                "container-network-receive-bytes-total": [1024, 2048, 4096],
            }
        )
        series = offline_data.canonical_metric_series(df, "network_rx_bytes_rate")
        assert list(series.values) == [1024, 2048, 4096]

    def test_missing_metric_returns_empty_series(self) -> None:
        df = pd.DataFrame({"unrelated": [1, 2, 3]})
        series = offline_data.canonical_metric_series(df, "cpu_usage")
        assert series.empty

    def test_non_numeric_values_coerced_to_nan_and_dropped(self) -> None:
        df = pd.DataFrame({"cpu_usage": ["0.1", "abc", "0.3"]})
        series = offline_data.canonical_metric_series(df, "cpu_usage")
        # "abc" is coerced to NaN and dropped
        assert len(series) == 2
        assert 0.1 in series.values
        assert 0.3 in series.values


# ── query_preloaded_metrics ──────────────────────────────────────────────────


@pytest.fixture
def synthetic_metrics() -> dict[str, pd.DataFrame]:
    """Two services with 20 data points each of cpu_usage and memory_usage.

    cartservice has a single end-of-window spike far above the stable 0.1
    baseline so the 2σ anomaly check triggers cleanly; frontend stays
    flat at 0.05.
    """
    timestamps = [1700000000 + 15 * i for i in range(20)]
    return {
        "cartservice": pd.DataFrame(
            {
                "timestamp": timestamps,
                "cpu_usage": [0.1] * 19 + [2.0],  # single sharp spike
                "memory_usage": [1e8] * 20,
            }
        ),
        "frontend": pd.DataFrame(
            {
                "timestamp": timestamps,
                "cpu_usage": [0.05] * 20,
                "memory_usage": [5e7] * 20,
            }
        ),
    }


class TestQueryPreloadedMetrics:
    """Tests for ``query_preloaded_metrics``."""

    def test_missing_service_returns_neutral_note(
        self,
        synthetic_metrics: dict[str, pd.DataFrame],
    ) -> None:
        result = offline_data.query_preloaded_metrics(
            service_name="nonexistent",
            metric_name="cpu_usage",
            preloaded_metrics=synthetic_metrics,
        )
        assert result["anomalous"] is False
        assert "No preloaded data" in result["note"]
        assert "nonexistent" in result["note"]

    def test_unavailable_metric_returns_neutral_note(
        self,
        synthetic_metrics: dict[str, pd.DataFrame],
    ) -> None:
        # probe_up doesn't exist in RCAEval data
        result = offline_data.query_preloaded_metrics(
            service_name="cartservice",
            metric_name="probe_up",
            preloaded_metrics=synthetic_metrics,
        )
        assert result["anomalous"] is False
        assert "not available in offline RCAEval data" in result["note"]

    def test_cpu_spike_flags_as_anomalous(
        self,
        synthetic_metrics: dict[str, pd.DataFrame],
    ) -> None:
        result = offline_data.query_preloaded_metrics(
            service_name="cartservice",
            metric_name="cpu_usage",
            preloaded_metrics=synthetic_metrics,
        )
        assert result["anomalous"] is True
        assert result["stats"]["current"] == pytest.approx(2.0)
        # Baseline mean (first 60% of window) is the 0.1 flat baseline.
        assert result["stats"]["baseline_mean"] < 0.3
        assert result["service"] == "cartservice"
        assert result["metric"] == "cpu_usage"

    def test_stable_metric_is_not_anomalous(
        self,
        synthetic_metrics: dict[str, pd.DataFrame],
    ) -> None:
        result = offline_data.query_preloaded_metrics(
            service_name="frontend",
            metric_name="cpu_usage",
            preloaded_metrics=synthetic_metrics,
        )
        assert result["anomalous"] is False
        assert result["stats"]["mean"] == pytest.approx(0.05)

    def test_preloaded_metrics_none_returns_neutral(self) -> None:
        result = offline_data.query_preloaded_metrics(
            service_name="cartservice",
            metric_name="cpu_usage",
            preloaded_metrics=None,
        )
        assert result["anomalous"] is False
        assert "offline mode with no preloaded metrics" in result["note"]

    def test_frozen_metric_detection_fires_critical(self) -> None:
        # Service had activity (mean > 0), then went to zero for last 5 samples
        preloaded = {
            "checkoutservice": pd.DataFrame(
                {
                    "timestamp": list(range(10)),
                    "cpu_usage": [0.05, 0.1, 0.08, 0.06, 0.04, 0, 0, 0, 0, 0],
                }
            )
        }
        result = offline_data.query_preloaded_metrics(
            service_name="checkoutservice",
            metric_name="cpu_usage",
            preloaded_metrics=preloaded,
        )
        assert result["anomalous"] is True
        assert result.get("frozen") is True
        assert "CRITICAL" in result["note"]
        assert "FROZEN" in result["note"]


# ── search_preloaded_logs ────────────────────────────────────────────────────


class TestSearchPreloadedLogs:
    """Tests for ``search_preloaded_logs``."""

    def test_none_logs_returns_empty_response(self) -> None:
        result = offline_data.search_preloaded_logs(
            query="error",
            preloaded_logs=None,
        )
        assert result["entries"] == []
        assert result["total_count"] == 0
        assert result["crash_match_count"] == 0

    def test_empty_df_returns_empty_response(self) -> None:
        result = offline_data.search_preloaded_logs(
            query="error",
            preloaded_logs=pd.DataFrame(),
        )
        assert result["total_count"] == 0

    def test_substring_match_finds_errors(self) -> None:
        logs = pd.DataFrame(
            {
                "timestamp": ["2024-01-01T00:00:00Z"] * 3,
                "service": ["cartservice", "cartservice", "frontend"],
                "message": [
                    "INFO: request processed",
                    "ERROR: connection refused",
                    "WARN: slow response",
                ],
            }
        )
        result = offline_data.search_preloaded_logs(
            query="ERROR",
            preloaded_logs=logs,
        )
        assert result["total_count"] == 1
        assert result["error_count"] == 1
        assert result["entries"][0]["service"] == "cartservice"

    def test_service_filter_restricts_results(self) -> None:
        logs = pd.DataFrame(
            {
                "service": ["cartservice", "frontend", "cartservice"],
                "message": ["ERROR A", "ERROR B", "ERROR C"],
            }
        )
        result = offline_data.search_preloaded_logs(
            query="ERROR",
            service_filter="cartservice",
            preloaded_logs=logs,
        )
        assert result["total_count"] == 2
        assert all(e["service"] == "cartservice" for e in result["entries"])

    def test_or_alternation_query_matches_any_term(self) -> None:
        logs = pd.DataFrame(
            {
                "message": [
                    "ERROR: timeout occurred",
                    "ERROR: connection refused",
                    "INFO: healthy check",
                    "WARN: panic averted",
                ],
            }
        )
        result = offline_data.search_preloaded_logs(
            query="timeout OR panic",
            preloaded_logs=logs,
        )
        assert result["total_count"] == 2

    def test_crash_patterns_escalate_to_critical(self) -> None:
        # 3+ crash-pattern matches with service_filter → CRITICAL
        logs = pd.DataFrame(
            {
                "service": ["checkoutservice"] * 4,
                "message": [
                    "panic: runtime error",
                    "SIGSEGV received",
                    "terminate called after throwing",
                    "fatal error: unhandled exception",
                ],
            }
        )
        result = offline_data.search_preloaded_logs(
            query="panic OR SIGSEGV OR terminate OR fatal",
            service_filter="checkoutservice",
            preloaded_logs=logs,
        )
        assert result.get("critical_service") == "checkoutservice"
        assert result.get("anomalous") is True
        assert "CRITICAL" in result.get("note", "")
        assert result["crash_match_count"] >= 3

    def test_single_crash_match_does_not_escalate(self) -> None:
        logs = pd.DataFrame(
            {
                "service": ["cartservice"],
                "message": ["panic: once"],
            }
        )
        result = offline_data.search_preloaded_logs(
            query="panic",
            service_filter="cartservice",
            preloaded_logs=logs,
        )
        # Only 1 match — below the 3-match threshold → no escalation
        assert result.get("critical_service") is None

    def test_alt_column_names_are_detected(self) -> None:
        # Some RCAEval variants use "log" / "container" instead of "message"
        # / "service"
        logs = pd.DataFrame(
            {
                "time": ["2024-01-01"],
                "container": ["cartservice"],
                "log": ["ERROR: database down"],
            }
        )
        result = offline_data.search_preloaded_logs(
            query="ERROR",
            service_filter="cartservice",
            preloaded_logs=logs,
        )
        assert result["total_count"] == 1


# ── discover_causation_from_df ───────────────────────────────────────────────


class TestDiscoverCausationFromDf:
    """Tests for ``discover_causation_from_df``."""

    def test_fewer_than_two_services_returns_trivial(self) -> None:
        result = offline_data.discover_causation_from_df(
            services=["cartservice"],
            preloaded_metrics={"cartservice": pd.DataFrame({"cpu_usage": [0.1, 0.2]})},
        )
        assert "error" in result
        assert result["root_cause_confidence"] == 0.0

    def test_none_preloaded_metrics_returns_inconclusive(self) -> None:
        result = offline_data.discover_causation_from_df(
            services=["a", "b", "c"],
            preloaded_metrics=None,
        )
        assert result["root_cause"] == "inconclusive"
        explanation = result["counterfactual"].lower()
        assert "no preloaded metrics" in explanation

    def test_critical_services_override_root_cause(self) -> None:
        # Minimal two-service causal input; critical_services should pick
        # the override path without needing real causal edges.
        rng = np.random.default_rng(0)
        preloaded = {
            "cartservice": pd.DataFrame(
                {
                    "timestamp": list(range(20)),
                    "cpu_usage": rng.normal(0.1, 0.02, 20),
                    "memory_usage": rng.normal(1e8, 1e6, 20),
                    "network_rx_bytes_rate": rng.normal(1000, 50, 20),
                    "network_tx_bytes_rate": rng.normal(500, 25, 20),
                }
            ),
            "frontend": pd.DataFrame(
                {
                    "timestamp": list(range(20)),
                    "cpu_usage": rng.normal(0.05, 0.01, 20),
                    "memory_usage": rng.normal(5e7, 5e5, 20),
                    "network_rx_bytes_rate": rng.normal(800, 40, 20),
                    "network_tx_bytes_rate": rng.normal(400, 20, 20),
                }
            ),
        }
        result = offline_data.discover_causation_from_df(
            services=["cartservice", "frontend"],
            preloaded_metrics=preloaded,
            critical_services=["cartservice"],
        )
        # Override path: root_cause is the critical service
        assert result["root_cause"] == "cartservice"
        assert result["root_cause_confidence"] >= 0.75

    def test_probe_metrics_excluded_from_causal_input(self) -> None:
        # This is a contract test: the OFFLINE causal path must NOT try to
        # fetch probe_up / probe_latency (they don't exist in RCAEval).
        # Verifying via _OFFLINE_CAUSAL_METRICS module constant.
        metrics = set(offline_data._OFFLINE_CAUSAL_METRICS)
        assert "probe_up" not in metrics
        assert "probe_latency" not in metrics
        assert "cpu_usage" in metrics
        assert "memory_usage" in metrics
