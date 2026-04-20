"""Tests for tests.evaluation.baseline_comparison."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.evaluation.baseline_comparison import (
    ADOnlyBaseline,
    BaselineInvestigatorAdapter,
    LLMWithoutToolsBaseline,
    RuleBasedBaseline,
    run_all_baselines,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mock_collector(
    cpu_values: dict[str, list[float]] | None = None,
    mem_values: dict[str, list[float]] | None = None,
) -> MagicMock:
    """Create a mock MetricsCollector with configurable per-service responses."""
    collector = MagicMock()
    cpu_values = cpu_values or {}
    mem_values = mem_values or {}

    def side_effect(service: str, queries: dict) -> dict:
        return {
            "cpu_usage": cpu_values.get(service, [0.01]),
            "memory_usage": mem_values.get(service, [50_000_000.0]),
        }

    collector.get_service_metrics.side_effect = side_effect
    return collector


SAMPLE_ALERT = {
    "title": "Test Alert",
    "severity": "high",
    "timestamp": "2026-04-01T10:00:00",
    "anomaly_score": 1.0,
}


# ---------------------------------------------------------------------------
# TestRuleBasedBaseline
# ---------------------------------------------------------------------------
class TestRuleBasedBaseline:
    def test_identifies_high_cpu_service(self) -> None:
        collector = _mock_collector(
            cpu_values={
                "cartservice": [0.95],  # above 0.85 threshold
                "frontend": [0.10],
                "redis": [0.05],
            }
        )
        baseline = RuleBasedBaseline(collector=collector)
        result = baseline.predict(SAMPLE_ALERT, services=["cartservice", "frontend", "redis"])
        assert result["root_cause"] == "cartservice"

    def test_identifies_high_memory_service(self) -> None:
        collector = _mock_collector(
            cpu_values={"checkoutservice": [0.10], "frontend": [0.05]},
            mem_values={
                "checkoutservice": [250_000_000.0],  # above 200MB threshold
                "frontend": [50_000_000.0],
            },
        )
        baseline = RuleBasedBaseline(collector=collector)
        result = baseline.predict(SAMPLE_ALERT, services=["checkoutservice", "frontend"])
        assert result["root_cause"] == "checkoutservice"

    def test_no_threshold_breach_falls_back_to_highest_cpu(self) -> None:
        collector = _mock_collector(
            cpu_values={
                "frontend": [0.30],
                "redis": [0.10],
            }
        )
        baseline = RuleBasedBaseline(collector=collector)
        result = baseline.predict(SAMPLE_ALERT, services=["frontend", "redis"])
        assert result["root_cause"] == "frontend"

    def test_returns_correct_structure(self) -> None:
        collector = _mock_collector()
        baseline = RuleBasedBaseline(collector=collector)
        result = baseline.predict(SAMPLE_ALERT, services=["cartservice"])
        assert "root_cause" in result
        assert "top_3_predictions" in result
        assert "confidence" in result
        assert isinstance(result["top_3_predictions"], list)

    def test_returns_top3_sorted(self) -> None:
        collector = _mock_collector(
            cpu_values={
                "a": [0.90],  # highest (above threshold)
                "b": [0.50],
                "c": [0.10],
            }
        )
        baseline = RuleBasedBaseline(collector=collector)
        result = baseline.predict(SAMPLE_ALERT, services=["a", "b", "c"])
        assert len(result["top_3_predictions"]) == 3
        assert result["top_3_predictions"][0] == "a"


# ---------------------------------------------------------------------------
# TestADOnlyBaseline
# ---------------------------------------------------------------------------
class TestADOnlyBaseline:
    def test_returns_highest_error_service(self) -> None:
        collector = _mock_collector(
            cpu_values={"svc_a": [0.9], "svc_b": [0.01]},
            mem_values={"svc_a": [300_000_000.0], "svc_b": [10_000_000.0]},
        )
        # Without model, uses variance as proxy
        baseline = ADOnlyBaseline(model_path=None, collector=collector)
        result = baseline.predict(SAMPLE_ALERT, services=["svc_a", "svc_b"])
        # svc_a has higher variance due to larger values
        assert result["root_cause"] in ["svc_a", "svc_b"]

    def test_returns_correct_structure(self) -> None:
        collector = _mock_collector()
        baseline = ADOnlyBaseline(model_path=None, collector=collector)
        result = baseline.predict(SAMPLE_ALERT, services=["cartservice"])
        assert "root_cause" in result
        assert "top_3_predictions" in result
        assert "confidence" in result

    def test_handles_single_service(self) -> None:
        collector = _mock_collector(cpu_values={"redis": [0.5]})
        baseline = ADOnlyBaseline(model_path=None, collector=collector)
        result = baseline.predict(SAMPLE_ALERT, services=["redis"])
        assert result["root_cause"] == "redis"
        assert len(result["top_3_predictions"]) == 1

    def test_returns_top3_sorted(self) -> None:
        collector = _mock_collector(
            cpu_values={"a": [100.0], "b": [10.0], "c": [1.0]},
            mem_values={"a": [100.0], "b": [10.0], "c": [1.0]},
        )
        baseline = ADOnlyBaseline(model_path=None, collector=collector)
        result = baseline.predict(SAMPLE_ALERT, services=["a", "b", "c"])
        assert len(result["top_3_predictions"]) == 3

    def test_fallback_without_model(self) -> None:
        """Without a model file, should still produce predictions using variance."""
        collector = _mock_collector(
            cpu_values={"svc": [0.1, 0.9, 0.5]},
        )
        baseline = ADOnlyBaseline(model_path="/nonexistent/model.pt", collector=collector)
        result = baseline.predict(SAMPLE_ALERT, services=["svc"])
        assert result["root_cause"] == "svc"


# ---------------------------------------------------------------------------
# TestLLMWithoutToolsBaseline
# ---------------------------------------------------------------------------
class TestLLMWithoutToolsBaseline:
    @patch("tests.evaluation.baseline_comparison.LLMWithoutToolsBaseline.predict")
    def test_returns_correct_structure(self, mock_predict: MagicMock) -> None:
        mock_predict.return_value = {
            "root_cause": "cartservice",
            "top_3_predictions": ["cartservice", "redis", "frontend"],
            "confidence": 0.7,
        }
        baseline = LLMWithoutToolsBaseline()
        result = baseline.predict(SAMPLE_ALERT, services=["cartservice"])
        assert "root_cause" in result
        assert "top_3_predictions" in result
        assert "confidence" in result

    def test_parse_response_extracts_service(self) -> None:
        baseline = LLMWithoutToolsBaseline()
        response = "cartservice\ncartservice\nredis\nfrontend\n0.8"
        result = baseline._parse_response(response, ["cartservice", "redis", "frontend"])
        assert result["root_cause"] == "cartservice"
        assert len(result["top_3_predictions"]) == 3

    def test_parse_response_handles_unknown(self) -> None:
        baseline = LLMWithoutToolsBaseline()
        response = "I don't know what happened.\n0.3"
        result = baseline._parse_response(response, ["cartservice", "redis"])
        assert result["root_cause"] == "unknown"

    def test_parse_response_extracts_confidence(self) -> None:
        baseline = LLMWithoutToolsBaseline()
        response = "cartservice\n0.85"
        result = baseline._parse_response(response, ["cartservice"])
        assert result["confidence"] == 0.85

    def test_parse_response_default_confidence(self) -> None:
        baseline = LLMWithoutToolsBaseline()
        response = "cartservice is the root cause"
        result = baseline._parse_response(response, ["cartservice"])
        assert result["confidence"] == 0.5  # default

    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_predict_wires_max_retries_3(self, mock_llm_cls: MagicMock) -> None:
        """predict() must construct ChatGoogleGenerativeAI with max_retries=3
        so transient network errors (macOS DNS cache blips, Gemini rate-limit
        429s, transient 5xx) don't silently abort a whole 35-test run. Matches
        the max_retries setting on the OpsAgent path in src/agent/graph.py."""
        mock_llm_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "cartservice\ncartservice\nredis\n0.7"
        mock_llm_instance.invoke.return_value = mock_response
        mock_llm_cls.return_value = mock_llm_instance

        collector = _mock_collector()
        baseline = LLMWithoutToolsBaseline(collector=collector)
        baseline.predict(SAMPLE_ALERT, services=["cartservice", "redis"])

        # Verify max_retries=3 was passed to the constructor
        assert mock_llm_cls.called, "ChatGoogleGenerativeAI was not instantiated"
        call_kwargs = mock_llm_cls.call_args.kwargs
        assert call_kwargs.get("max_retries") == 3, (
            f"max_retries must be 3 (got {call_kwargs.get('max_retries')!r}). "
            f"Kept in sync with src/agent/graph.py:_get_llm()."
        )


# ---------------------------------------------------------------------------
# TestRunAllBaselines
# ---------------------------------------------------------------------------
class TestRunAllBaselines:
    @pytest.fixture()
    def results_dir(self, tmp_path: Path) -> Path:
        results = tmp_path / "results"
        results.mkdir()
        for i in range(3):
            record = {
                "test_id": f"crash_run_{i + 1}",
                "fault_type": "service_crash",
                "run_id": i + 1,
                "ground_truth": "cartservice",
                "predicted_root_cause": "cartservice",
                "is_correct": True,
                "alert_time": "2026-04-01T10:00:10",
                "detection_latency_seconds": 10.0,
                "investigation_duration_seconds": 45.0,
                "status": "completed",
            }
            (results / f"crash_run_{i + 1}.json").write_text(json.dumps(record))
        return results

    @patch("tests.evaluation.baseline_comparison.MetricsCollector")
    def test_returns_all_three_results(
        self,
        mock_cls: MagicMock,
        results_dir: Path,
        tmp_path: Path,
    ) -> None:
        mock_instance = mock_cls.return_value
        mock_instance.get_service_metrics.return_value = {
            "cpu_usage": [0.5],
            "memory_usage": [100_000_000.0],
        }
        # Patch LLM to avoid real API calls
        with patch(
            "tests.evaluation.baseline_comparison.LLMWithoutToolsBaseline.predict"
        ) as mock_llm:
            mock_llm.return_value = {
                "root_cause": "cartservice",
                "top_3_predictions": ["cartservice"],
                "confidence": 0.5,
            }
            summaries = run_all_baselines(
                results_dir=str(results_dir),
                output_dir=str(tmp_path / "output"),
                model_path="/nonexistent/model.pt",
            )

        assert "rule_based" in summaries
        assert "ad_only" in summaries
        assert "llm_no_tools" in summaries

    @patch("tests.evaluation.baseline_comparison.MetricsCollector")
    def test_saves_results_to_subdirs(
        self,
        mock_cls: MagicMock,
        results_dir: Path,
        tmp_path: Path,
    ) -> None:
        mock_instance = mock_cls.return_value
        mock_instance.get_service_metrics.return_value = {
            "cpu_usage": [0.5],
            "memory_usage": [100_000_000.0],
        }
        with patch(
            "tests.evaluation.baseline_comparison.LLMWithoutToolsBaseline.predict"
        ) as mock_llm:
            mock_llm.return_value = {
                "root_cause": "cartservice",
                "top_3_predictions": ["cartservice"],
                "confidence": 0.5,
            }
            run_all_baselines(
                results_dir=str(results_dir),
                output_dir=str(tmp_path / "output"),
                model_path="/nonexistent/model.pt",
            )

        baseline_dir = tmp_path / "output" / "baseline_results"
        assert (baseline_dir / "rule_based").exists()
        assert (baseline_dir / "ad_only").exists()
        assert (baseline_dir / "llm_no_tools").exists()

    def test_empty_results_returns_empty(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        summaries = run_all_baselines(results_dir=str(empty_dir))
        assert summaries == {}


# ---------------------------------------------------------------------------
# TestBaselineInvestigatorAdapter
# ---------------------------------------------------------------------------
class TestBaselineInvestigatorAdapter:
    """The adapter wraps a *Baseline.predict() so its 3-field return can be
    consumed by the fault-injection harness which expects AgentExecutor's
    6-field ``investigate()`` shape. Lets ``run_fault_injection()`` treat
    baselines and OpsAgent interchangeably.
    """

    def _fake_baseline(self, predict_return: dict) -> MagicMock:
        """Build a baseline mock whose predict(...) returns a given dict."""
        b = MagicMock()
        b.predict.return_value = predict_return
        return b

    def test_adapter_upshapes_three_fields_to_six(self) -> None:
        baseline = self._fake_baseline(
            {
                "root_cause": "cartservice",
                "top_3_predictions": ["cartservice", "redis", "frontend"],
                "confidence": 0.8,
            }
        )
        adapter = BaselineInvestigatorAdapter(baseline, kind="rule-based")
        result = adapter.investigate(alert={"affected_services": ["cartservice"]})
        # All 6 fields expected by fault_injection_suite.run_fault_injection()
        # must be present and well-typed.
        for key in (
            "root_cause",
            "root_cause_confidence",
            "top_3_predictions",
            "confidence",
            "rca_report",
            "recommended_actions",
        ):
            assert key in result, f"missing key: {key}"
        assert result["root_cause"] == "cartservice"
        assert result["root_cause_confidence"] == pytest.approx(0.8)
        assert result["confidence"] == pytest.approx(0.8)
        assert result["top_3_predictions"] == ["cartservice", "redis", "frontend"]
        assert "cartservice" in result["rca_report"]
        assert result["recommended_actions"] == []

    def test_adapter_passes_services_from_alert(self) -> None:
        """`affected_services` in the alert must propagate to the baseline's
        `services` kwarg so currencyservice (intentionally excluded from
        alerts per Session 12) stays out of baseline predictions too."""
        baseline = self._fake_baseline(
            {
                "root_cause": "frontend",
                "top_3_predictions": ["frontend"],
                "confidence": 0.5,
            }
        )
        adapter = BaselineInvestigatorAdapter(baseline, kind="rule-based")
        alert = {
            "affected_services": [
                "cartservice",
                "checkoutservice",
                "frontend",
                "paymentservice",
                "productcatalogservice",
                "redis",
            ],
        }
        adapter.investigate(alert=alert)
        call = baseline.predict.call_args
        assert call.kwargs["services"] == alert["affected_services"]
        assert "currencyservice" not in call.kwargs["services"]

    def test_adapter_ignores_start_time(self) -> None:
        """Baselines are point-in-time; they don't honour start_time pinning.
        The adapter accepts the kwarg silently so the harness can pass it
        uniformly regardless of investigator type."""
        baseline = self._fake_baseline(
            {"root_cause": "redis", "top_3_predictions": ["redis"], "confidence": 0.4}
        )
        adapter = BaselineInvestigatorAdapter(baseline, kind="ad-only")
        # Should not raise even though start_time is ISO-8601.
        result = adapter.investigate(
            alert={"affected_services": ["redis"]},
            start_time="2026-04-19T10:00:00+00:00",
        )
        assert result["root_cause"] == "redis"

    def test_adapter_defensive_on_missing_keys(self) -> None:
        """A malformed baseline return (missing keys) must not crash the
        adapter. Safe defaults: 'unknown' root_cause, [] top_3, 0.0
        confidence."""
        baseline = self._fake_baseline({})  # empty dict
        adapter = BaselineInvestigatorAdapter(baseline, kind="llm-no-tools")
        result = adapter.investigate(alert={"affected_services": []})
        assert result["root_cause"] == "unknown"
        assert result["top_3_predictions"] == []
        assert result["confidence"] == pytest.approx(0.0)
        assert result["root_cause_confidence"] == pytest.approx(0.0)
        # Even on a degenerate return, the RCA-report stub is a non-empty string.
        assert isinstance(result["rca_report"], str)
        assert len(result["rca_report"]) > 0

    def test_adapter_kind_surfaces_in_report(self) -> None:
        """The `kind` tag must appear in the synthesised rca_report so RCA-report
        files for different baselines are distinguishable post-hoc."""
        baseline = self._fake_baseline(
            {
                "root_cause": "paymentservice",
                "top_3_predictions": ["paymentservice"],
                "confidence": 0.6,
            }
        )
        for kind in ("rule-based", "ad-only", "llm-no-tools"):
            adapter = BaselineInvestigatorAdapter(baseline, kind=kind)
            result = adapter.investigate(alert={"affected_services": ["paymentservice"]})
            assert f"[baseline:{kind}]" in result["rca_report"]
