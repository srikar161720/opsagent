"""Evaluation metrics for OpsAgent fault injection and cross-system tests.

Computes Recall@1, Recall@3, Precision, Detection Latency, MTTR Proxy,
confidence intervals, and per-fault-type breakdowns from per-test JSON results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import scipy.stats as stats


@dataclass
class EvaluationResults:
    """Container for all computed evaluation metrics."""

    recall_at_1: float
    recall_at_3: float
    precision: float  # Set separately from false-positive test
    avg_detection_latency: float
    avg_mttr_proxy: float
    avg_explanation_quality: float
    recall_by_fault: dict[str, float] = field(default_factory=dict)
    latency_by_fault: dict[str, list[float]] = field(default_factory=dict)
    ci_recall_at_1: tuple[float, float] | None = None


def load_results(results_dir: str) -> list[dict]:
    """Load all per-test JSON files from a directory."""
    results = []
    for f in sorted(Path(results_dir).glob("*.json")):
        with open(f) as fp:
            results.append(json.load(fp))
    return results


def recall_at_1(predictions: list[str], ground_truths: list[str]) -> float:
    """Proportion of cases where top prediction == ground truth."""
    if not predictions:
        return 0.0
    correct = sum(p == t for p, t in zip(predictions, ground_truths, strict=False))
    return correct / len(predictions)


def recall_at_3(
    top3_predictions: list[list[str]],
    ground_truths: list[str],
) -> float:
    """Proportion of cases where ground truth is in the top 3 predictions."""
    if not top3_predictions:
        return 0.0
    correct = sum(t in preds[:3] for preds, t in zip(top3_predictions, ground_truths, strict=False))
    return correct / len(top3_predictions)


def precision(true_positives: int, false_positives: int) -> float:
    """Precision from the 24-hour false positive test.

    Args:
        true_positives:  alerts fired during actual fault injections
        false_positives: alerts fired during the 24h normal operation window
    """
    total = true_positives + false_positives
    return 1.0 if total == 0 else true_positives / total


def detection_latency(fault_start: str, alert_time: str) -> float:
    """Seconds from fault injection start to first alert (ISO-8601 strings)."""
    start = datetime.fromisoformat(fault_start)
    alert = datetime.fromisoformat(alert_time)
    return (alert - start).total_seconds()


def mttr_proxy(alert_time: str, rca_complete: str, is_correct: bool) -> float | None:
    """Seconds from alert to RCA completion. None if root cause was incorrect."""
    if not is_correct:
        return None
    alert = datetime.fromisoformat(alert_time)
    complete = datetime.fromisoformat(rca_complete)
    return (complete - alert).total_seconds()


def confidence_interval(data: list[float], confidence: float = 0.95) -> tuple[float, float]:
    """95% CI for the mean using a t-distribution (appropriate for n < 40)."""
    arr = np.array(data)
    n = len(arr)
    if n < 2:
        return (float(arr.mean()), float(arr.mean()))
    se = stats.sem(arr)
    h = se * stats.t.ppf((1 + confidence) / 2, df=n - 1)
    return (float(arr.mean() - h), float(arr.mean() + h))


def calculate_metrics(results: list[dict]) -> EvaluationResults:
    """Compute all metrics from a list of result dicts."""
    preds = [r.get("predicted_root_cause") for r in results]
    top3 = [r.get("top_3_predictions", []) for r in results]
    truths = [r.get("ground_truth") for r in results]

    r1 = recall_at_1(preds, truths)
    r3 = recall_at_3(top3, truths)

    # Detection latency
    latencies = [
        r["detection_latency_seconds"] for r in results if "detection_latency_seconds" in r
    ]
    avg_latency = float(np.mean(latencies)) if latencies else 0.0

    # MTTR proxy — only for correct predictions
    mttr_vals = [
        r["investigation_duration_seconds"]
        for r in results
        if r.get("is_correct") and "investigation_duration_seconds" in r
    ]
    avg_mttr = float(np.mean(mttr_vals)) if mttr_vals else 0.0

    # Explanation quality (manually scored subset only)
    quality = [
        r["explanation_quality"] for r in results if r.get("explanation_quality") is not None
    ]
    avg_quality = float(np.mean(quality)) if quality else 0.0

    # Per-fault-type breakdown
    fault_types = sorted(set(r.get("fault_type", "unknown") for r in results))
    recall_by_fault: dict[str, float] = {}
    latency_by_fault: dict[str, list[float]] = {}

    for fault in fault_types:
        subset = [r for r in results if r.get("fault_type") == fault]
        correct = sum(1 for r in subset if r.get("is_correct"))
        recall_by_fault[fault] = correct / len(subset) if subset else 0.0
        lat = [r["detection_latency_seconds"] for r in subset if "detection_latency_seconds" in r]
        latency_by_fault[fault] = lat

    # Confidence interval for Recall@1
    binary = [1.0 if r.get("is_correct") else 0.0 for r in results]
    ci = confidence_interval(binary)

    return EvaluationResults(
        recall_at_1=r1,
        recall_at_3=r3,
        precision=0.0,  # Computed separately from 24h false positive test
        avg_detection_latency=avg_latency,
        avg_mttr_proxy=avg_mttr,
        avg_explanation_quality=avg_quality,
        recall_by_fault=recall_by_fault,
        latency_by_fault=latency_by_fault,
        ci_recall_at_1=ci,
    )
