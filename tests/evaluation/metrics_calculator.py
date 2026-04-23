"""Evaluation metrics for OpsAgent fault injection and cross-system tests.

Computes Recall@1, Recall@3, Precision, Detection Latency, MTTR Proxy,
confidence intervals (t-distribution and Wilson score), McNemar's paired
significance test, and per-group breakdowns (per-fault, per-service,
per-variant) from per-test JSON results.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import scipy.stats as stats
from statsmodels.stats.contingency_tables import mcnemar as _mcnemar


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
    """Load all per-test JSON files from a directory.

    Skips ``summary.json`` files (produced by the RCAEval evaluator as a
    pre-computed aggregate alongside the per-case files) so callers can
    iterate a directory without filtering them out manually.
    """
    results = []
    for f in sorted(Path(results_dir).glob("*.json")):
        if f.name == "summary.json":
            continue
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


def wilson_ci(
    successes: int,
    n: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the t-distribution when the outcome is binary (is_correct)
    and the point estimate may hit the boundary (p = 0 or p = 1). At n=35
    with p=1.0 the t-distribution collapses to a zero-width interval; the
    Wilson lower bound is ~0.901, which is the correct honest answer.

    Reference: Wilson, E.B. (1927). "Probable Inference, the Law of Succession,
    and Statistical Inference". JASA 22 (158): 209-212.
    """
    if n <= 0:
        raise ValueError("n must be a positive integer")
    if successes < 0 or successes > n:
        raise ValueError("successes must be between 0 and n inclusive")

    p_hat = successes / n
    z = stats.norm.ppf((1 + confidence) / 2)
    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    half = (z / denom) * np.sqrt(p_hat * (1 - p_hat) / n + z**2 / (4 * n * n))
    lo = max(0.0, float(center - half))
    hi = min(1.0, float(center + half))
    return (lo, hi)


def mcnemar_test(
    ops_correct: list[bool],
    baseline_correct: list[bool],
    *,
    exact: bool = True,
) -> dict:
    """McNemar's paired test on matched binary outcomes.

    Tests the null hypothesis that OpsAgent and a baseline have the same
    accuracy, using the 2x2 contingency of discordant pairs:

    * ``n01`` = baseline right, OpsAgent wrong
    * ``n10`` = OpsAgent right, baseline wrong

    ``exact=True`` uses the binomial distribution (correct for small
    discordance counts; recommended for n < 25). Returns the p-value and
    whether the result is significant at alpha=0.05 alongside the counts
    so the caller can report them in the results doc.

    Note: this test assumes the same test cases were evaluated by both
    investigators. In Session 13/14 the test IDs match by definition
    (same fault_type + run_id) but the runs physically executed on
    different calendar dates, so this is "matched by design" rather than
    "experimentally paired". Document that caveat when reporting.
    """
    if len(ops_correct) != len(baseline_correct):
        raise ValueError("ops_correct and baseline_correct must have the same length")
    if not ops_correct:
        raise ValueError("cannot run McNemar test on empty inputs")

    n01 = sum(1 for a, b in zip(ops_correct, baseline_correct, strict=True) if not a and b)
    n10 = sum(1 for a, b in zip(ops_correct, baseline_correct, strict=True) if a and not b)
    table = [[0, n01], [n10, 0]]
    result = _mcnemar(table, exact=exact)
    p_value = float(result.pvalue)
    return {
        "p_value": p_value,
        "significant": p_value < 0.05,
        "n01": n01,
        "n10": n10,
        "n": len(ops_correct),
    }


def per_group_recall(
    results: list[dict],
    group_key: str,
) -> dict[str, dict[str, float | int]]:
    """Group results by a key and compute Recall@1 / Recall@3 / counts per group.

    Generic replacement for the per-fault-type loop inside ``calculate_metrics``.
    Works for any categorical key present on the result dicts — e.g.
    ``"fault_type"`` for OTel Demo, ``"ground_truth"`` for per-service
    breakdowns, ``"dataset"`` for RCAEval RE1/RE2 splits.

    Each group entry contains: ``n``, ``recall_at_1``, ``recall_at_3``,
    ``correct``, ``correct_top3`` for downstream plotting and CIs.
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for record in results:
        key = str(record.get(group_key, "unknown"))
        buckets[key].append(record)

    out: dict[str, dict[str, float | int]] = {}
    for key, subset in buckets.items():
        n = len(subset)
        correct = sum(1 for r in subset if r.get("is_correct"))
        correct_top3 = sum(
            1 for r in subset if r.get("ground_truth") in (r.get("top_3_predictions") or [])[:3]
        )
        out[key] = {
            "n": n,
            "correct": correct,
            "correct_top3": correct_top3,
            "recall_at_1": correct / n if n else 0.0,
            "recall_at_3": correct_top3 / n if n else 0.0,
        }
    return dict(sorted(out.items()))
