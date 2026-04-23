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
    mcnemar_test,
    mttr_proxy,
    per_group_recall,
    precision,
    recall_at_1,
    recall_at_3,
    wilson_ci,
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


# ---------------------------------------------------------------------------
# TestLoadResultsSkipsSummary
# ---------------------------------------------------------------------------
class TestLoadResultsSkipsSummary:
    def test_skips_summary_json(self, tmp_path: Path) -> None:
        (tmp_path / "summary.json").write_text(json.dumps({"total_cases": 2}))
        (tmp_path / "case_1.json").write_text(json.dumps({"test_id": "case_1"}))
        (tmp_path / "case_2.json").write_text(json.dumps({"test_id": "case_2"}))
        results = load_results(str(tmp_path))
        ids = sorted(r["test_id"] for r in results)
        assert ids == ["case_1", "case_2"]

    def test_includes_summary_when_renamed(self, tmp_path: Path) -> None:
        # Only the exact filename "summary.json" is skipped.
        (tmp_path / "my_summary.json").write_text(json.dumps({"test_id": "my_summary"}))
        results = load_results(str(tmp_path))
        assert len(results) == 1


# ---------------------------------------------------------------------------
# TestWilsonCI
# ---------------------------------------------------------------------------
class TestWilsonCI:
    def test_all_success_lower_bound_above_90pct(self) -> None:
        # At n=35 with 35/35 correct, the Wilson lower bound should be ~0.90.
        lo, hi = wilson_ci(35, 35)
        assert 0.88 < lo < 0.92
        # Upper bound clamps to 1.0.
        assert 0.999 < hi <= 1.0

    def test_zero_success_upper_bound_below_10pct(self) -> None:
        lo, hi = wilson_ci(0, 35)
        assert lo >= 0.0
        assert 0.08 < hi < 0.12

    def test_interval_bounds_proportion(self) -> None:
        # The Wilson CI is centred on a shifted estimate so it doesn't
        # strictly contain the point estimate when close to boundaries —
        # but for reasonable-n, non-degenerate proportions it should.
        lo, hi = wilson_ci(4, 35)
        p_hat = 4 / 35
        assert lo <= p_hat <= hi
        assert 0.03 < lo < 0.08
        assert 0.22 < hi < 0.30

    def test_rejects_non_positive_n(self) -> None:
        with pytest.raises(ValueError):
            wilson_ci(0, 0)
        with pytest.raises(ValueError):
            wilson_ci(0, -1)

    def test_rejects_out_of_range_successes(self) -> None:
        with pytest.raises(ValueError):
            wilson_ci(-1, 10)
        with pytest.raises(ValueError):
            wilson_ci(11, 10)


# ---------------------------------------------------------------------------
# TestMcnemarTest
# ---------------------------------------------------------------------------
class TestMcnemarTest:
    def test_identical_results_not_significant(self) -> None:
        ops = [True, False, True, False, True]
        baseline = [True, False, True, False, True]
        result = mcnemar_test(ops, baseline)
        # Zero discordant pairs -> exact binomial p=1.0.
        assert result["p_value"] == 1.0
        assert result["significant"] is False
        assert result["n01"] == 0
        assert result["n10"] == 0
        assert result["n"] == 5

    def test_strictly_better_ops_is_significant(self) -> None:
        # 10 tests; OpsAgent gets all right, baseline gets all wrong.
        # n10 = 10, n01 = 0 -> highly significant via exact binomial.
        ops = [True] * 10
        baseline = [False] * 10
        result = mcnemar_test(ops, baseline)
        assert result["n10"] == 10
        assert result["n01"] == 0
        assert result["p_value"] < 0.05
        assert result["significant"] is True

    def test_mixed_discordance(self) -> None:
        # Some cases each way; tests counts are correct.
        ops = [True, True, False, True, False]
        baseline = [True, False, True, False, False]
        result = mcnemar_test(ops, baseline)
        assert result["n10"] == 2  # ops right / baseline wrong at indexes 1, 3
        assert result["n01"] == 1  # ops wrong / baseline right at index 2
        assert result["n"] == 5

    def test_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError):
            mcnemar_test([True], [True, False])

    def test_rejects_empty_inputs(self) -> None:
        with pytest.raises(ValueError):
            mcnemar_test([], [])


# ---------------------------------------------------------------------------
# TestPerGroupRecall
# ---------------------------------------------------------------------------
class TestPerGroupRecall:
    def _record(
        self,
        *,
        fault_type: str,
        ground_truth: str,
        predicted: str,
        top3: list[str] | None = None,
        is_correct: bool | None = None,
    ) -> dict:
        return {
            "fault_type": fault_type,
            "ground_truth": ground_truth,
            "predicted_root_cause": predicted,
            "top_3_predictions": top3 or [predicted, "other1", "other2"],
            "is_correct": predicted == ground_truth if is_correct is None else is_correct,
        }

    def test_group_by_fault_type(self) -> None:
        records = [
            self._record(fault_type="crash", ground_truth="cart", predicted="cart"),
            self._record(fault_type="crash", ground_truth="cart", predicted="frontend"),
            self._record(fault_type="latency", ground_truth="frontend", predicted="frontend"),
        ]
        grouped = per_group_recall(records, "fault_type")
        assert grouped["crash"]["n"] == 2
        assert grouped["crash"]["correct"] == 1
        assert grouped["crash"]["recall_at_1"] == 0.5
        assert grouped["latency"]["n"] == 1
        assert grouped["latency"]["recall_at_1"] == 1.0

    def test_group_by_ground_truth_for_per_service(self) -> None:
        # Per-service recall: group by ground_truth.
        records = [
            self._record(fault_type="cpu", ground_truth="adservice", predicted="frontend"),
            self._record(fault_type="cpu", ground_truth="adservice", predicted="frontend"),
            self._record(fault_type="mem", ground_truth="cart", predicted="cart"),
        ]
        grouped = per_group_recall(records, "ground_truth")
        assert grouped["adservice"]["recall_at_1"] == 0.0
        assert grouped["cart"]["recall_at_1"] == 1.0

    def test_empty_input_returns_empty_dict(self) -> None:
        assert per_group_recall([], "fault_type") == {}

    def test_counts_top3_hits(self) -> None:
        records = [
            self._record(
                fault_type="cpu",
                ground_truth="adservice",
                predicted="frontend",
                top3=["frontend", "adservice", "checkoutservice"],
            ),
        ]
        grouped = per_group_recall(records, "fault_type")
        assert grouped["cpu"]["recall_at_3"] == 1.0
        assert grouped["cpu"]["correct_top3"] == 1
