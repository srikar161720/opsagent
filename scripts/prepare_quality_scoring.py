"""Prepare the explanation-quality scoring artefacts.

Generates two files from the primary-evaluation OpsAgent OTel Demo results so the user
can manually score every RCA report against the 5-point rubric in
``docs/success_metrics.md``:

* ``data/evaluation/explanation_quality_scores.csv``: skeleton with
  ``test_id`` and ``fault_type`` pre-filled; all score columns blank.
* ``data/evaluation/quality_scoring_guide.md``: rubric plus per-fault-type
  index of report filenames with direct markdown links.

Usage::

    poetry run python scripts/prepare_quality_scoring.py
    poetry run python scripts/prepare_quality_scoring.py --force   # overwrite
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "data" / "evaluation" / "results_fault_injection_tests"
DEFAULT_REPORTS_DIR = DEFAULT_RESULTS_DIR / "reports"
DEFAULT_CSV = PROJECT_ROOT / "data" / "evaluation" / "explanation_quality_scores.csv"
DEFAULT_GUIDE = PROJECT_ROOT / "data" / "evaluation" / "quality_scoring_guide.md"

CSV_COLUMNS = [
    "test_id",
    "fault_type",
    "root_cause_accuracy",
    "evidence_quality",
    "causal_analysis",
    "recommendations",
    "presentation",
    "overall_score",
    "notes",
]

_RUBRIC_LINES = [
    "| Score | Description |",
    "|-------|-------------|",
    "| 5 | Root cause correct; evidence chain complete and logically sound; "
    "remediation actionable |",
    "| 4 | Root cause correct; evidence mostly complete; minor gaps in reasoning or remediation |",
    "| 3 | Root cause partially correct (right service, wrong component); evidence "
    "present but incomplete |",
    "| 2 | Root cause incorrect but investigation direction reasonable; some useful "
    "evidence collected |",
    "| 1 | Root cause incorrect; evidence irrelevant or missing; report provides no "
    "diagnostic value |",
]
RUBRIC_TABLE = "\n".join(_RUBRIC_LINES) + "\n"

_SUB_DIMENSION_LINES = [
    "Score each report on five 1-5 sub-dimensions (the overall score is the mean):",
    "",
    "1. **root_cause_accuracy** — Does the report name the correct root-cause service/component?",
    "2. **evidence_quality** — Is the evidence chain detailed, chronological, and "
    "cites concrete metrics/logs?",
    "3. **causal_analysis** — Does the causal reasoning (PC graph + counterfactual) make sense?",
    "4. **recommendations** — Are the immediate and long-term actions specific and useful?",
    "5. **presentation** — Is the report well-structured, readable, and clear for "
    "an on-call engineer?",
]
SUB_DIMENSIONS = "\n".join(_SUB_DIMENSION_LINES) + "\n"


def _collect_results(results_dir: Path) -> list[dict]:
    """Load every per-test JSON from the primary-evaluation results directory (skip ``summary.json``)."""
    rows: list[dict] = []
    for path in sorted(results_dir.glob("*.json")):
        if path.name == "summary.json":
            continue
        with open(path) as handle:
            rows.append(json.load(handle))
    return rows


def write_scoring_csv(results: list[dict], output_path: Path) -> None:
    """Write the CSV skeleton with pre-filled test_id + fault_type."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for record in sorted(results, key=lambda r: (r.get("fault_type", ""), r.get("run_id", 0))):
            writer.writerow(
                [
                    record.get("test_id", ""),
                    record.get("fault_type", ""),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )


def _build_guide_markdown(results: list[dict], reports_dir: Path) -> str:
    by_fault: dict[str, list[dict]] = defaultdict(list)
    for record in results:
        by_fault[record.get("fault_type", "unknown")].append(record)

    lines: list[str] = [
        "# Explanation Quality Scoring — User Instructions",
        "",
        "## Overview",
        "",
        f"OpsAgent's primary evaluation track produced **{len(results)}** RCA reports on the ",
        "OTel Demo fault-injection suite (7 fault types x 5 runs each). This guide ",
        "walks through the scoring workflow; scores land in ",
        "`data/evaluation/explanation_quality_scores.csv` which already has `test_id` and ",
        "`fault_type` pre-populated, so all you need to do is fill the 5 score columns and ",
        "an optional `notes` column for each row.",
        "",
        "## 5-Point Rubric",
        "",
        RUBRIC_TABLE,
        "",
        "## Sub-dimensions",
        "",
        SUB_DIMENSIONS,
        "",
        "## Scoring Workflow",
        "",
        "For each of the 35 rows in the CSV:",
        "",
        "1. Open the matching RCA report from the per-fault index below.",
        "2. Read the report sections in order: **Executive Summary**, **Root Cause**, ",
        "   **Evidence Chain**, **Causal Analysis**, **Recommended Actions**.",
        "3. Score each of the five sub-dimensions on a 1-5 integer scale.",
        "4. Leave `overall_score` blank — the analysis script computes it as the mean of ",
        "   the five sub-dimensions (or you can fill it yourself if you prefer a weighted view).",
        "5. Add an optional one-line `notes` comment (wrap values containing commas in ",
        "   double quotes).",
        "",
        "## Per-Fault-Type Report Index",
        "",
        f"All 35 reports live under `{reports_dir.relative_to(PROJECT_ROOT)}/`. Grouped by ",
        "fault type (5 runs each):",
        "",
    ]

    for fault_type in sorted(by_fault.keys()):
        lines.append(f"### {fault_type}")
        lines.append("")
        for record in sorted(by_fault[fault_type], key=lambda r: r.get("run_id", 0)):
            test_id = record.get("test_id", "")
            gt = record.get("ground_truth", "?")
            pred = record.get("predicted_root_cause", "?")
            is_correct = record.get("is_correct", False)
            correctness = "correct" if is_correct else "incorrect"
            relative_report = (reports_dir / f"{test_id}.md").relative_to(PROJECT_ROOT)
            lines.append(
                f"- `{test_id}` — GT: `{gt}`, predicted: `{pred}` ({correctness})\n"
                f"  - [open report]({relative_report})"
            )
        lines.append("")

    lines.extend(
        [
            "## When Finished",
            "",
            "Save the filled CSV in place at ",
            "`data/evaluation/explanation_quality_scores.csv` and ping me. I will:",
            "",
            "1. Validate the schema (columns, score ranges [1, 5]).",
            "2. Compute `overall_score` per row as the mean of the five sub-dimensions ",
            "   (unless you've already filled it).",
            "3. Regenerate Viz 7 (explanation quality histogram) in ",
            "   `notebooks/08_evaluation_analysis.ipynb`.",
            "4. Populate Section 6 of `docs/evaluation_results.md` with the mean, 95% CI, ",
            "   and per-fault averages.",
            "",
            "**Target:** mean overall_score >= 4.0 / 5.0 (per `docs/success_metrics.md`).",
            "",
        ]
    )

    return "\n".join(lines)


def write_scoring_guide(results: list[dict], output_path: Path, reports_dir: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_build_guide_markdown(results, reports_dir))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="Directory with per-test JSON results (default: the primary-evaluation results directory).",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=DEFAULT_REPORTS_DIR,
        help="Directory with per-test Markdown RCA reports.",
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="CSV output path.")
    parser.add_argument("--guide", type=Path, default=DEFAULT_GUIDE, help="Guide output path.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the CSV even if it already has non-empty score rows.",
    )
    args = parser.parse_args(argv)

    results_dir = args.results_dir
    if not results_dir.is_dir():
        print(f"ERROR: results directory {results_dir} does not exist.", file=sys.stderr)
        return 1

    results = _collect_results(results_dir)
    if not results:
        print(f"ERROR: no JSON results found under {results_dir}.", file=sys.stderr)
        return 1

    # Guard against clobbering in-progress scoring work.
    if args.csv.exists() and not args.force:
        existing_rows = list(csv.reader(args.csv.open()))
        if len(existing_rows) > 1 and any(
            any(cell.strip() for cell in row[2:7]) for row in existing_rows[1:]
        ):
            print(
                f"{args.csv} already has scores filled in. Re-run with --force to overwrite.",
                file=sys.stderr,
            )
            return 2

    write_scoring_csv(results, args.csv)
    write_scoring_guide(results, args.guide, args.reports_dir)

    print(f"Wrote CSV skeleton ({len(results)} rows) -> {args.csv.relative_to(PROJECT_ROOT)}")
    print(f"Wrote scoring guide               -> {args.guide.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
