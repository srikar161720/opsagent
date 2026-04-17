"""Tests for tests.evaluation.metrics_calculator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evaluation.metrics_calculator import (
    EvaluationResults,
    calculate_metrics,
    confidence_interval,
    detection_latency,
    load_results,
    mttr_proxy,
    precision,
    recall_at_1,
    recall_at_3,
)


# ---------------------------------------------------------------------------
# TestRecallAt1
# ---------------------------------------------------------------------------
class TestRecallAt1:
    def test_perfect_predictions(self) -> None:
        assert recall_at_1(["a", "b", "c"], ["a", "b", "c"]) == 1.0

    def test_no_correct(self) -> None:
        assert recall_at_1(["x", "y", "z"], ["a", "b", "c"]) == 0.0

    def test_partial_correct(self) -> None:
        result = recall_at_1(["a", "x", "c"], ["a", "b", "c"])
        assert abs(result - 2 / 3) < 1e-9

    def test_empty_input(self) -> None:
        assert recall_at_1([], []) == 0.0


# ---------------------------------------------------------------------------
# TestRecallAt3
# ---------------------------------------------------------------------------
class TestRecallAt3:
    def test_ground_truth_in_top3(self) -> None:
        top3 = [["a", "b", "c"], ["d", "e", "f"]]
        truths = ["b", "f"]
        assert recall_at_3(top3, truths) == 1.0

    def test_ground_truth_not_in_top3(self) -> None:
        top3 = [["a", "b", "c"]]
        truths = ["z"]
        assert recall_at_3(top3, truths) == 0.0

    def test_mixed(self) -> None:
        top3 = [["a", "b", "c"], ["x", "y", "z"]]
        truths = ["a", "w"]
        assert recall_at_3(top3, truths) == 0.5

    def test_empty_input(self) -> None:
        assert recall_at_3([], []) == 0.0


# ---------------------------------------------------------------------------
# TestPrecision
# ---------------------------------------------------------------------------
class TestPrecision:
    def test_no_false_positives(self) -> None:
        assert precision(10, 0) == 1.0

    def test_all_false_positives(self) -> None:
        assert precision(0, 10) == 0.0

    def test_mixed(self) -> None:
        assert abs(precision(8, 2) - 0.8) < 1e-9

    def test_zero_total(self) -> None:
        assert precision(0, 0) == 1.0


# ---------------------------------------------------------------------------
# TestDetectionLatency
# ---------------------------------------------------------------------------
class TestDetectionLatency:
    def test_valid_timestamps(self) -> None:
        result = detection_latency("2026-04-01T10:00:00", "2026-04-01T10:00:42")
        assert result == 42.0

    def test_isoformat_with_microseconds(self) -> None:
        result = detection_latency("2026-04-01T10:00:00.000000", "2026-04-01T10:01:00.500000")
        assert result == 60.5


# ---------------------------------------------------------------------------
# TestMttrProxy
# ---------------------------------------------------------------------------
class TestMttrProxy:
    def test_correct_prediction_returns_duration(self) -> None:
        result = mttr_proxy("2026-04-01T10:00:00", "2026-04-01T10:01:30", is_correct=True)
        assert result == 90.0

    def test_incorrect_prediction_returns_none(self) -> None:
        result = mttr_proxy("2026-04-01T10:00:00", "2026-04-01T10:01:30", is_correct=False)
        assert result is None

    def test_valid_timestamps_with_microseconds(self) -> None:
        result = mttr_proxy(
            "2026-04-01T10:00:00.000000",
            "2026-04-01T10:02:00.000000",
            is_correct=True,
        )
        assert result == 120.0


# ---------------------------------------------------------------------------
# TestConfidenceInterval
# ---------------------------------------------------------------------------
class TestConfidenceInterval:
    def test_returns_tuple(self) -> None:
        lo, hi = confidence_interval([1.0, 0.0, 1.0, 0.0, 1.0])
        assert isinstance(lo, float)
        assert isinstance(hi, float)
        assert lo < hi

    def test_single_value(self) -> None:
        lo, hi = confidence_interval([0.75])
        assert lo == hi == 0.75

    def test_wider_with_more_variance(self) -> None:
        narrow = confidence_interval([0.5, 0.5, 0.5, 0.5])
        wide = confidence_interval([0.0, 1.0, 0.0, 1.0])
        narrow_range = narrow[1] - narrow[0]
        wide_range = wide[1] - wide[0]
        assert wide_range > narrow_range


# ---------------------------------------------------------------------------
# TestLoadResults
# ---------------------------------------------------------------------------
class TestLoadResults:
    def test_loads_json_files(self, tmp_path: Path) -> None:
        for i in range(3):
            (tmp_path / f"test_{i}.json").write_text(
                json.dumps({"test_id": f"test_{i}", "is_correct": True})
            )
        results = load_results(str(tmp_path))
        assert len(results) == 3

    def test_empty_dir(self, tmp_path: Path) -> None:
        results = load_results(str(tmp_path))
        assert results == []

    def test_ignores_non_json_files(self, tmp_path: Path) -> None:
        (tmp_path / "readme.txt").write_text("not json")
        (tmp_path / "result.json").write_text(json.dumps({"test_id": "r1"}))
        results = load_results(str(tmp_path))
        assert len(results) == 1


# ---------------------------------------------------------------------------
# TestCalculateMetrics
# ---------------------------------------------------------------------------
class TestCalculateMetrics:
    @pytest.fixture()
    def sample_results(self) -> list[dict]:
        return [
            {
                "test_id": "crash_1",
                "fault_type": "service_crash",
                "ground_truth": "cartservice",
                "predicted_root_cause": "cartservice",
                "top_3_predictions": ["cartservice", "redis", "frontend"],
                "is_correct": True,
                "detection_latency_seconds": 12.0,
                "investigation_duration_seconds": 45.0,
                "explanation_quality": 4.5,
                "status": "completed",
            },
            {
                "test_id": "crash_2",
                "fault_type": "service_crash",
                "ground_truth": "cartservice",
                "predicted_root_cause": "frontend",
                "top_3_predictions": ["frontend", "cartservice", "redis"],
                "is_correct": False,
                "detection_latency_seconds": 15.0,
                "investigation_duration_seconds": 50.0,
                "status": "completed",
            },
            {
                "test_id": "latency_1",
                "fault_type": "high_latency",
                "ground_truth": "paymentservice",
                "predicted_root_cause": "paymentservice",
                "top_3_predictions": ["paymentservice", "frontend", "redis"],
                "is_correct": True,
                "detection_latency_seconds": 30.0,
                "investigation_duration_seconds": 60.0,
                "explanation_quality": 4.0,
                "status": "completed",
            },
        ]

    def test_returns_evaluation_results(self, sample_results: list[dict]) -> None:
        metrics = calculate_metrics(sample_results)
        assert isinstance(metrics, EvaluationResults)

    def test_recall_at_1_value(self, sample_results: list[dict]) -> None:
        metrics = calculate_metrics(sample_results)
        assert abs(metrics.recall_at_1 - 2 / 3) < 1e-9

    def test_per_fault_breakdown(self, sample_results: list[dict]) -> None:
        metrics = calculate_metrics(sample_results)
        assert "service_crash" in metrics.recall_by_fault
        assert "high_latency" in metrics.recall_by_fault
        assert metrics.recall_by_fault["service_crash"] == 0.5
        assert metrics.recall_by_fault["high_latency"] == 1.0

    def test_latency_by_fault(self, sample_results: list[dict]) -> None:
        metrics = calculate_metrics(sample_results)
        assert len(metrics.latency_by_fault["service_crash"]) == 2
        assert len(metrics.latency_by_fault["high_latency"]) == 1

    def test_ci_recall_at_1(self, sample_results: list[dict]) -> None:
        metrics = calculate_metrics(sample_results)
        assert metrics.ci_recall_at_1 is not None
        lo, hi = metrics.ci_recall_at_1
        assert lo <= metrics.recall_at_1 <= hi

    def test_avg_explanation_quality(self, sample_results: list[dict]) -> None:
        metrics = calculate_metrics(sample_results)
        assert abs(metrics.avg_explanation_quality - 4.25) < 1e-9

    def test_mttr_only_for_correct(self, sample_results: list[dict]) -> None:
        metrics = calculate_metrics(sample_results)
        # Only correct predictions: 45.0 and 60.0
        assert abs(metrics.avg_mttr_proxy - 52.5) < 1e-9
