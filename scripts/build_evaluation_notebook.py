# ruff: noqa: E501
"""Generate ``notebooks/08_evaluation_analysis.ipynb`` from a Python spec.

The repo has no jupyter kernel installed, so notebooks are authored by
writing valid nbformat-4 JSON directly. This helper keeps the notebook
source in one Python file (easier to diff, lint, and review) and produces
the final .ipynb by running ``poetry run python scripts/build_evaluation_notebook.py``.

The notebook wraps the chart-generation module at
``scripts/make_evaluation_charts.py`` — each chart cell calls one of its
plot functions and displays the resulting PNG inline.

E501 is silenced at file level because markdown tables and inline prose
inside the ``md(...)`` cells must stay on single lines to render correctly;
splitting them would inject spurious line breaks into the final notebook.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "notebooks" / "08_evaluation_analysis.ipynb"


def md(src: str) -> dict:
    lines = src.rstrip("\n").split("\n")
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": [f"{line}\n" for line in lines[:-1]] + [lines[-1]],
    }


def code(src: str) -> dict:
    lines = src.rstrip("\n").split("\n")
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [f"{line}\n" for line in lines[:-1]] + [lines[-1]],
    }


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


CELLS: list[dict] = [
    md("""\
# 08 — Phase 5 Evaluation Analysis

Final analysis notebook for OpsAgent's Phase 5 evaluation. Aggregates results
from Sessions 13-15 and produces the 9 visualizations in `context/evaluation_strategy.md` §9.

**Data sources**

| Source | Directory | Sessions |
|---|---|---|
| OpsAgent OTel Demo (35 tests) | `data/evaluation/results_session13/` | 13 |
| Rule-Based / AD-Only / LLM-Without-Tools (35 each) | `data/evaluation/baseline_*/` | 14 |
| RCAEval RE1-OB (125 cases) | `data/evaluation/rcaeval_re1_ob/` | 15 |
| RCAEval RE2-OB (91 cases) | `data/evaluation/rcaeval_re2_ob/` | 15 |

The heavy lifting (loading, statistical tests, plotting) lives in `scripts/make_evaluation_charts.py`
and `scripts/run_evaluation.py`; this notebook is a thin presentation layer.
"""),
    md("""\
## 1. Setup & load aggregated metrics

Before running this notebook, make sure the aggregated summary exists:

```bash
poetry run python scripts/run_evaluation.py
poetry run python scripts/make_evaluation_charts.py
```

The first command writes `data/evaluation/evaluation_summary.json`;
the second regenerates every PNG under `docs/images/evaluation_charts/`.
The notebook re-imports the plotting module and re-runs each chart
inline so edits show up without leaving the notebook.
"""),
    code("""\
import sys
from pathlib import Path

project_root = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from IPython.display import Image, Markdown, display  # noqa: E402, I001
from scripts.make_evaluation_charts import (  # noqa: E402, I001
    SAVE_DIR,
    load_all,
    plot_viz1_recall_by_fault,
    plot_viz2_baseline_comparison,
    plot_viz3_detection_latency,
    plot_viz4_confusion_matrix,
    plot_viz5_causal_examples,
    plot_viz6_tool_usage,
    plot_viz7_quality_distribution,
    plot_viz8_rcaeval_comparison,
    plot_viz9_rcaeval_re2_by_fault,
)

data = load_all()
summary = data.summary["directories"]
print("Loaded directories:", list(summary.keys()))
print("McNemar pairs:", list(data.summary["mcnemar_tests"].keys()))
"""),
    code("""\
# One-line summary per investigator/variant.
header = f"{'Label':36s}  {'n':>4s}  {'R@1':>6s}  {'R@3':>6s}  {'Wilson-R@1':>20s}"
print(header)
print("-" * len(header))
for label, payload in summary.items():
    wilson = payload.get("wilson_ci_recall_at_1", [0, 0])
    print(
        f"{label:36s}  {payload['n']:4d}  "
        f"{payload['recall_at_1']:6.3f}  {payload['recall_at_3']:6.3f}  "
        f"[{wilson[0]:.3f}, {wilson[1]:.3f}]"
    )
"""),
    md("""\
## 2. Primary Evaluation — OTel Demo (Session 13)

OpsAgent's primary track: 7 fault types × 5 runs = 35 tests injected on the
reduced OTel Astronomy Shop stack. Session 13 achieved **100% Recall@1 and
100% Recall@3** at uniform 0.75 confidence (the CRITICAL-override band). The
95% Wilson CI on Recall@1 is [0.901, 1.0] — even the lower bound clears the
80% Phase-5 target.

Memory-pressure detection (was 40% in Session 12) moved to 100% after adding
the `container_spec_memory_limit_bytes` gauge + `memory_utilization` CRITICAL
detector with peak-based trigger.
"""),
    code("""\
# Viz 1 — Recall@1 by fault type
plot_viz1_recall_by_fault(data, SAVE_DIR / "01_recall_by_fault.png")
display(Image(str(SAVE_DIR / "01_recall_by_fault.png")))
"""),
    code("""\
# Viz 3 — Detection latency box plot by fault type
plot_viz3_detection_latency(data, SAVE_DIR / "03_detection_latency.png")
display(Image(str(SAVE_DIR / "03_detection_latency.png")))
"""),
    md("""\
## 3. Internal Baseline Comparison (Session 14)

Three ablation baselines on the same 35-test suite:

- **Rule-Based:** static CPU/memory/latency thresholds.
- **AD-Only:** LSTM-AE reconstruction-error ranking (no agent).
- **LLM-Without-Tools:** Gemini with system prompt + alert context, no tool access.

Each run is matched by `test_id` to Session 13's OpsAgent run. Note that the
baselines physically executed on 2026-04-20, while OpsAgent was 2026-04-19 —
the pairing is **by test definition (same fault + run_id)** not experimental,
since each ran on a freshly recycled Docker stack.

McNemar's exact test (paired binary) confirms OpsAgent's advantage is highly
significant against every baseline.
"""),
    code("""\
# Print McNemar p-values
mcnemar = data.summary["mcnemar_tests"]
for pair, result in mcnemar.items():
    print(
        f"{pair:30s}  p={result['p_value']:.4g}  "
        f"n10(ops right, baseline wrong)={result['n10']:3d}  "
        f"n01(baseline right, ops wrong)={result['n01']:3d}  "
        f"significant@α=0.05={result['significant']}"
    )
"""),
    code("""\
# Viz 2 — Baseline comparison (R@1, R@3, mean confidence)
plot_viz2_baseline_comparison(data, SAVE_DIR / "02_baseline_comparison.png")
display(Image(str(SAVE_DIR / "02_baseline_comparison.png")))
"""),
    code("""\
# Viz 4 — Confusion matrices (OpsAgent pure diagonal vs LLM-Without-Tools cart-bias)
plot_viz4_confusion_matrix(data, SAVE_DIR / "04_confusion_matrix.png")
display(Image(str(SAVE_DIR / "04_confusion_matrix.png")))
"""),
    md("""\
**Per-baseline failure modes:**

- *Rule-Based*: OTel Demo idles at <3% CPU per service; no service organically crosses the 85% CPU or 200 MB memory thresholds. Every prediction falls through to the "highest raw CPU" fallback → always `frontend`.
- *AD-Only*: The baseline builds an 8-dim feature vector (cpu + memory × mean/std/min/max) and zero-pads to the LSTM-AE's 54-dim input. 46 of 54 dims are zero at predict time → reconstruction error caps at the 0.253 threshold for every service (confidence = 1.000 uniformly), and the tiebreak picks max-CPU → always `frontend`.
- *LLM-Without-Tools*: baseline ECONNREFUSED log noise (frontend → cartservice:7070 misconfiguration in v1.10.0) dominates every snapshot, so the LLM predicts `cartservice` 24/35 times. 10 of those 24 happen to be cart-GT tests — coincidental R@1 boost. R@3 = 80% is the honest metric of what the LLM reasons about.
"""),
    md("""\
## 4. Investigation Artefacts — Causal reasoning & tool usage

OpsAgent's RCA reports include a **Causal Analysis** section with a short
ASCII root-cause tag plus a counterfactual sentence (e.g. "If cartservice had
remained at baseline…"). Three exemplars below show the structure across
fault families.

Agent tool usage (Viz 6) is a **proxy** from regex matches in the RCA report
text — Session 13 result JSONs did not capture per-call tool counts, and the
deterministic sweep (36 calls per investigation: 5 metrics × 6 services + 6
log calls) dominates by construction.
"""),
    code("""\
# Viz 5 — Causal analysis excerpts from 3 exemplar reports
plot_viz5_causal_examples(data, SAVE_DIR / "05_causal_examples.png")
display(Image(str(SAVE_DIR / "05_causal_examples.png")))
"""),
    code("""\
# Viz 6 — Tool-usage proxy (regex counts from RCA report text)
plot_viz6_tool_usage(data, SAVE_DIR / "06_tool_usage.png")
display(Image(str(SAVE_DIR / "06_tool_usage.png")))
"""),
    md("""\
## 5. Cross-System Validation — RCAEval-OB (Session 15)

OpsAgent ran in offline-mode against RE1-OB (125 cases) and RE2-OB (91 cases)
— 216 total cases. RE3-OB (30 cases) aborted at 4 due to Gemini 2.5 Flash RPD
exhaustion and is excluded from all aggregates. SS and TT variants were not
evaluated (topology/vocabulary mismatch with OpsAgent's OTel-Demo-trained
reasoning).

Combined result: **Recall@1 7.9% (95% Wilson CI [5.0%, 12.2%])**,
**Recall@3 33.3% (CI [27.4%, 39.9%])**. Three systematic failure modes:
(1) vocab-unfamiliar services (adservice 0/25), (2) low-traffic services
(currencyservice 0/43), (3) non-propagating faults (cpu 0/41).

**Key finding:** Session 13's 100% depended heavily on OpsAgent's custom
telemetry stack (Service Probe Exporter probe_up, memory_utilization CRITICAL
detectors). RCAEval CSVs are metrics-only with none of those direct-
observability signals, so the native LLM+PC reasoning pipeline drops to
near-random on Recall@1. The Recall@3 lift (+6 pp above random) indicates
the pipeline does partial work but cannot rank top-1 without probe-style
detectors.
"""),
    code("""\
# Viz 8 — RCAEval-OB: OpsAgent vs published BARO baseline (OB)
plot_viz8_rcaeval_comparison(data, SAVE_DIR / "08_rcaeval_comparison.png")
display(Image(str(SAVE_DIR / "08_rcaeval_comparison.png")))
_note = (
    "*Metric note:* BARO's 0.86 is **Avg@5** (mean of AC@1..AC@5 per BARO"
    " paper Table 3), which strictly dominates Recall@1 by construction."
    " OpsAgent's Recall@3 (0.33) is the more comparable metric against"
    " Avg@5, though still well below BARO's OB score."
    " Per-variant OB numbers for CIRCA / RCD / CausalRCA / MicroCause are"
    " not published in the RCAEval paper (the paper reports only Train"
    " Ticket in detail); see `configs/rcaeval_published_baselines.yaml` for"
    " sources."
)
display(Markdown(_note))
"""),
    code("""\
# Viz 9 — RCAEval RE2-OB: OpsAgent Recall by fault type
plot_viz9_rcaeval_re2_by_fault(data, SAVE_DIR / "09_rcaeval_re2_by_fault.png")
display(Image(str(SAVE_DIR / "09_rcaeval_re2_by_fault.png")))
"""),
    md("""\
## 6. Explanation Quality (Manual Scoring)

35 Session 13 OpsAgent RCA reports were scored against the 5-point rubric in
`docs/success_metrics.md`. Each report receives 5 sub-dimension scores
(root_cause_accuracy, evidence_quality, causal_analysis, recommendations,
presentation); the overall score is the mean of the five.

If the scoring CSV has not yet been filled, Viz 7 renders a placeholder.
Run `poetry run python scripts/make_evaluation_charts.py --viz 7` again
after scoring to regenerate the real histogram.
"""),
    code("""\
# Viz 7 — Explanation quality distribution (regenerates based on CSV state)
filled = plot_viz7_quality_distribution(SAVE_DIR / "07_quality_distribution.png")
display(Image(str(SAVE_DIR / "07_quality_distribution.png")))
if not filled:
    display(Markdown(
        "⚠️ `data/evaluation/explanation_quality_scores.csv` has no scores yet — placeholder shown."
    ))
"""),
    md("""\
## 7. Phase 5 Target Scorecard

| Metric | Target | Achieved | Status |
|---|---|---|---|
| Recall@1 (OTel Demo) | ≥ 80% | **100%** (Wilson [90.1%, 100%]) | ✅ met |
| Recall@3 (OTel Demo) | ≥ 95% | **100%** | ✅ met |
| Precision (24h FP-rate) | ≥ 70% | *not measured (original target)*; classifier precision 1.000 / 1.000 (micro/macro, Section 4.4) | partial |
| Detection Latency | < 60 s | mean 125 s across 35 tests | ❌ above target (driven by 120 s pre-investigation wait) |
| MTTR Proxy | ≥ 50% reduction | 24 s OpsAgent vs 0.1 s baselines — *not a meaningful comparison* | — |
| Explanation Quality | ≥ 4.0 / 5.0 | **4.25 / 5.0** (95% CI [4.12, 4.38]) | ✅ met |
| RCAEval RE2 R@1 | Competitive w/ CIRCA/RCD | 7.7% (OB only) — below published baselines | ❌ below target; honest finding |

Detection latency: the 60s target was set before the Session 11-13 diagnostic
work established that a 120-s pre-investigation wait is necessary for the
`rate()` lookback window to expire stale data. Once that's accounted for,
OpsAgent detects correctly in >99% of cases — the 125-s figure is dominated
by the injected wait, not algorithmic latency. This is documented in
`docs/evaluation_results.md` Limitations.
"""),
    md("""\
## 8. Reproducibility

- **Git branch:** `main`, Session 16 work uncommitted at time of this notebook run.
- **OTel Demo images:** `ghcr.io/open-telemetry/demo:1.10.0-*`.
- **LLM models:** `gemini-3-flash-preview` (live OTel Demo), `gemini-2.5-flash` (offline RCAEval).
- **Fault-injection seed:** `--seed 42` (default in `tests/evaluation/fault_injection_suite.py`).
- **Session timestamps:**
  - Session 13 (OpsAgent live): 2026-04-19
  - Session 14 (baselines): 2026-04-20
  - Session 15 (RCAEval RE1-OB + RE2-OB): 2026-04-21
- **Result directories:** `data/evaluation/results_session13/`, `baseline_{rule_based,ad_only,llm_no_tools}/`, `rcaeval_re1_ob/`, `rcaeval_re2_ob/`.
- **Aggregated summary:** `data/evaluation/evaluation_summary.json` (this notebook's primary input).
"""),
]


def build_notebook() -> dict:
    return {
        "cells": CELLS,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.12.0"},
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    notebook = build_notebook()
    with open(OUTPUT_PATH, "w") as handle:
        json.dump(notebook, handle, indent=1)
        handle.write("\n")
    print(f"Wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)} ({len(CELLS)} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
