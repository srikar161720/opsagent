"""Aggregate all evaluation results into a single summary JSON.

Loads per-case results from every results directory (OpsAgent OTel Demo,
three internal baselines, two RCAEval-OB variants), computes the standard
metrics plus Wilson CIs and pairwise McNemar tests, and writes
``data/evaluation/evaluation_summary.json`` for the notebook and results
doc to consume.

Where the source directory already contains a pre-computed ``summary.json``
(RCAEval runs), the aggregator asserts the newly computed numbers match
within float epsilon; a drift fails the script loudly.

Usage::

    poetry run python scripts/run_evaluation.py
    poetry run python scripts/run_evaluation.py --output data/evaluation/my_summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.evaluation.metrics_calculator import (  # noqa: E402
    calculate_metrics,
    load_results,
    mcnemar_test,
    per_group_recall,
    wilson_ci,
)

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "evaluation" / "evaluation_summary.json"

# Each entry = (label, relative_dir, kind, notes) where kind is one of
# "otel_opsagent", "otel_baseline", "rcaeval_offline".
EVAL_DIRS: list[tuple[str, str, str, str]] = [
    (
        "opsagent_otel_primary",
        "data/evaluation/results_fault_injection_tests",
        "otel_opsagent",
        "OpsAgent live on the OTel Demo (35 tests, 7 fault types x 5 runs).",
    ),
    (
        "rule_based_otel",
        "data/evaluation/baseline_rule_based",
        "otel_baseline",
        (
            "Rule-Based baseline on the OTel Demo. One-shot classification; "
            "investigation_duration ~0.1s is not comparable to OpsAgent."
        ),
    ),
    (
        "ad_only_otel",
        "data/evaluation/baseline_ad_only",
        "otel_baseline",
        (
            "AD-Only baseline on the OTel Demo. Confidence collapses to 1.000 "
            "due to zero-padding of an 8-dim feature vector into the LSTM-AE's 54-dim input. "
            "This is a pre-existing design limitation of the baseline."
        ),
    ),
    (
        "llm_no_tools_otel",
        "data/evaluation/baseline_llm_no_tools",
        "otel_baseline",
        (
            "LLM-Without-Tools baseline on the OTel Demo. Cart-bias (24/35 "
            "predictions = cartservice) driven by baseline ECONNREFUSED log noise."
        ),
    ),
    (
        "opsagent_rcaeval_re1_ob",
        "data/evaluation/rcaeval_re1_ob",
        "rcaeval_offline",
        (
            "OpsAgent offline-mode on RCAEval RE1-OB. "
            "detection_latency_seconds is 0.0 across the board (not measured offline); "
            "MTTR figures equal investigation_duration since there is no pre-investigation wait."
        ),
    ),
    (
        "opsagent_rcaeval_re2_ob",
        "data/evaluation/rcaeval_re2_ob",
        "rcaeval_offline",
        (
            "OpsAgent offline-mode on RCAEval RE2-OB. Same detection_latency "
            "caveat as RE1-OB."
        ),
    ),
]

# Which McNemar pairings to compute. Each tuple: (label, ops_dir_label, baseline_dir_label).
MCNEMAR_PAIRS: list[tuple[str, str, str]] = [
    ("opsagent_vs_rule_based", "opsagent_otel_primary", "rule_based_otel"),
    ("opsagent_vs_ad_only", "opsagent_otel_primary", "ad_only_otel"),
    ("opsagent_vs_llm_no_tools", "opsagent_otel_primary", "llm_no_tools_otel"),
]

FLOAT_EPS = 1e-6


def _metrics_to_dict(results: list[dict], kind: str, notes: str) -> dict:
    """Compute metrics + Wilson CIs + per-group breakdowns for one directory."""
    metrics = calculate_metrics(results)
    payload = asdict(metrics)

    n = len(results)
    correct_top1 = sum(1 for r in results if r.get("is_correct"))
    correct_top3 = sum(
        1 for r in results if r.get("ground_truth") in (r.get("top_3_predictions") or [])[:3]
    )

    payload["n"] = n
    payload["correct_top1"] = correct_top1
    payload["correct_top3"] = correct_top3
    payload["wilson_ci_recall_at_1"] = list(wilson_ci(correct_top1, n)) if n else [0.0, 0.0]
    payload["wilson_ci_recall_at_3"] = list(wilson_ci(correct_top3, n)) if n else [0.0, 0.0]

    # Mean reported confidence (informative for baselines that collapse to 0.5/1.0).
    confidences = [
        r.get("confidence") for r in results if isinstance(r.get("confidence"), int | float)
    ]
    payload["mean_confidence"] = float(sum(confidences) / len(confidences)) if confidences else 0.0

    payload["per_fault"] = per_group_recall(results, "fault_type")
    payload["per_ground_truth"] = per_group_recall(results, "ground_truth")

    # Prediction distribution (what the investigator tends to pick).
    pred_counter = Counter(r.get("predicted_root_cause", "unknown") for r in results)
    payload["predicted_root_cause_counts"] = dict(pred_counter.most_common())

    payload["kind"] = kind
    payload["notes"] = notes

    # Precision is explicitly NOT computed here; see docs/evaluation_results.md Limitations.
    payload.pop("precision", None)
    return payload


def _assert_matches_existing_summary(label: str, computed: dict, summary_path: Path) -> None:
    """If a pre-computed summary.json exists, compare key fields within FLOAT_EPS."""
    if not summary_path.is_file():
        return
    with open(summary_path) as handle:
        existing = json.load(handle)

    checks: list[tuple[str, object, object]] = []
    for key in ("recall_at_1", "recall_at_3"):
        if key in existing:
            checks.append((key, existing[key], computed.get(key)))
    if "total_cases" in existing:
        checks.append(("total_cases", existing["total_cases"], computed.get("n")))

    for key, old_val, new_val in checks:
        if (
            isinstance(old_val, int | float)
            and isinstance(new_val, int | float)
            and abs(float(old_val) - float(new_val)) > FLOAT_EPS
        ):
            raise RuntimeError(
                f"[{label}] drift from {summary_path}: {key}={old_val} "
                f"(existing) vs {new_val} (computed)"
            )


def aggregate(output_path: Path) -> dict:
    """Run the full aggregation. Returns the dict that was written."""
    summary: dict = {
        "generated_at": None,  # filled in below
        "directories": {},
        "mcnemar_tests": {},
    }

    # Populate per-directory metrics.
    per_label_results: dict[str, list[dict]] = {}
    for label, rel_dir, kind, notes in EVAL_DIRS:
        full_dir = PROJECT_ROOT / rel_dir
        if not full_dir.is_dir():
            print(f"WARNING: {rel_dir} does not exist, skipping.", file=sys.stderr)
            continue

        results = load_results(str(full_dir))
        if not results:
            print(f"WARNING: {rel_dir} has no result files, skipping.", file=sys.stderr)
            continue

        metrics_dict = _metrics_to_dict(results, kind, notes)
        metrics_dict["source_dir"] = rel_dir

        _assert_matches_existing_summary(label, metrics_dict, full_dir / "summary.json")

        summary["directories"][label] = metrics_dict
        per_label_results[label] = results
        print(
            f"{label}: n={metrics_dict['n']} "
            f"R@1={metrics_dict['recall_at_1']:.3f} "
            f"R@3={metrics_dict['recall_at_3']:.3f} "
            f"Wilson-R@1={metrics_dict['wilson_ci_recall_at_1']}"
        )

    # RCAEval combined OB aggregate (RE1-OB + RE2-OB).
    ob_combined_results: list[dict] = []
    for label in ("opsagent_rcaeval_re1_ob", "opsagent_rcaeval_re2_ob"):
        if label in per_label_results:
            ob_combined_results.extend(per_label_results[label])
    if ob_combined_results:
        combined = _metrics_to_dict(
            ob_combined_results,
            kind="rcaeval_offline",
            notes=(
                "Combined RE1-OB + RE2-OB (n=216). RE3-OB aborted at 4 cases and is "
                "excluded. detection_latency_seconds not meaningful offline."
            ),
        )
        combined["source_dir"] = "re1_ob + re2_ob"
        summary["directories"]["opsagent_rcaeval_ob_combined"] = combined
        print(
            f"opsagent_rcaeval_ob_combined: n={combined['n']} "
            f"R@1={combined['recall_at_1']:.3f} R@3={combined['recall_at_3']:.3f}"
        )

    # McNemar pairings. Align by test_id; drop cases that don't appear in both.
    for pair_label, ops_label, baseline_label in MCNEMAR_PAIRS:
        if ops_label not in per_label_results or baseline_label not in per_label_results:
            continue

        ops_by_id = {r.get("test_id"): r for r in per_label_results[ops_label]}
        baseline_by_id = {r.get("test_id"): r for r in per_label_results[baseline_label]}
        common_ids = sorted(set(ops_by_id.keys()) & set(baseline_by_id.keys()))
        ops_correct = [bool(ops_by_id[tid].get("is_correct")) for tid in common_ids]
        baseline_correct = [bool(baseline_by_id[tid].get("is_correct")) for tid in common_ids]

        if not ops_correct:
            continue

        result = mcnemar_test(ops_correct, baseline_correct)
        result["caveat"] = (
            "Matched by test_id only (same fault_type + run_id). The OpsAgent run and the "
            "baseline runs took place on different calendar dates with fresh Docker stacks; "
            "not experimentally paired."
        )
        summary["mcnemar_tests"][pair_label] = result
        print(
            f"{pair_label}: p={result['p_value']:.6g} "
            f"n10={result['n10']} n01={result['n01']} "
            f"significant={result['significant']}"
        )

    # Timestamp.
    from datetime import UTC, datetime

    summary["generated_at"] = datetime.now(UTC).isoformat()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as handle:
        json.dump(summary, handle, indent=2, default=str)

    print(f"\nWrote {output_path.relative_to(PROJECT_ROOT)}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path (default: {DEFAULT_OUTPUT.relative_to(PROJECT_ROOT)}).",
    )
    args = parser.parse_args(argv)
    try:
        aggregate(args.output)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
