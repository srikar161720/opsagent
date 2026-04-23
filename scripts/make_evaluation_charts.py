"""Generate all evaluation-analysis charts for the Phase 5 deliverables.

Produces Visualizations 1-9 as PNG files under
``docs/images/evaluation_charts/``. Called both by the standalone CLI and by
the ``notebooks/08_evaluation_analysis.ipynb`` notebook.

Usage::

    poetry run python scripts/make_evaluation_charts.py
    poetry run python scripts/make_evaluation_charts.py --viz 6  # just Viz 6
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.evaluation.metrics_calculator import load_results  # noqa: E402

SAVE_DIR = PROJECT_ROOT / "docs" / "images" / "evaluation_charts"
SUMMARY_PATH = PROJECT_ROOT / "data" / "evaluation" / "evaluation_summary.json"
QUALITY_CSV = PROJECT_ROOT / "data" / "evaluation" / "explanation_quality_scores.csv"
SESSION13_DIR = PROJECT_ROOT / "data" / "evaluation" / "results_session13"
SESSION13_REPORTS = SESSION13_DIR / "reports"
BASELINE_DIRS = {
    "Rule-Based": PROJECT_ROOT / "data" / "evaluation" / "baseline_rule_based",
    "AD-Only": PROJECT_ROOT / "data" / "evaluation" / "baseline_ad_only",
    "LLM-No-Tools": PROJECT_ROOT / "data" / "evaluation" / "baseline_llm_no_tools",
}
RCAEVAL_DIRS = {
    "RE1-OB": PROJECT_ROOT / "data" / "evaluation" / "rcaeval_re1_ob",
    "RE2-OB": PROJECT_ROOT / "data" / "evaluation" / "rcaeval_re2_ob",
}
PUBLISHED_BASELINES_YAML = PROJECT_ROOT / "configs" / "rcaeval_published_baselines.yaml"

plt.rcParams.update(
    {
        "figure.dpi": 150,
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.labelsize": 11,
    }
)


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedData:
    summary: dict
    session13: list[dict]
    baselines: dict[str, list[dict]]
    rcaeval: dict[str, list[dict]]


def _load_summary() -> dict:
    if not SUMMARY_PATH.is_file():
        raise FileNotFoundError(f"{SUMMARY_PATH} not found. Run scripts/run_evaluation.py first.")
    with open(SUMMARY_PATH) as handle:
        return json.load(handle)


def load_all() -> LoadedData:
    summary = _load_summary()
    session13 = load_results(str(SESSION13_DIR))
    baselines = {label: load_results(str(path)) for label, path in BASELINE_DIRS.items()}
    rcaeval = {label: load_results(str(path)) for label, path in RCAEVAL_DIRS.items()}
    return LoadedData(summary=summary, session13=session13, baselines=baselines, rcaeval=rcaeval)


# ---------------------------------------------------------------------------
# Viz 1 — Recall@1 by fault type (OTel Demo, OpsAgent)
# ---------------------------------------------------------------------------


def plot_viz1_recall_by_fault(data: LoadedData, out_path: Path) -> None:
    per_fault = data.summary["directories"]["opsagent_otel_session13"]["per_fault"]
    faults = sorted(per_fault.keys())
    recalls = [per_fault[f]["recall_at_1"] for f in faults]
    colors = ["#2ecc71" if r >= 0.8 else "#e67e22" if r >= 0.6 else "#e74c3c" for r in recalls]

    fig, ax = plt.subplots(figsize=(11, 5))
    x = np.arange(len(faults))
    bars = ax.bar(x, recalls, color=colors, edgecolor="white", linewidth=0.5)
    ax.axhline(y=0.8, color="#27ae60", linestyle="--", linewidth=1.5, label="Target 80%")
    ax.set_xlabel("Fault Type")
    ax.set_ylabel("Recall@1")
    ax.set_title("Recall@1 by Fault Type — OpsAgent on OTel Demo (n=35)")
    ax.set_ylim(0, 1.08)
    for bar, r in zip(bars, recalls, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.02,
            f"{r:.0%}",
            ha="center",
            fontsize=10,
        )
    ax.set_xticks(x)
    ax.set_xticklabels(faults, rotation=35, ha="right")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Viz 2 — Baseline comparison grouped bar (R@1, R@3, mean confidence)
# ---------------------------------------------------------------------------


def plot_viz2_baseline_comparison(data: LoadedData, out_path: Path) -> None:
    investigators = ["OpsAgent", "Rule-Based", "AD-Only", "LLM-No-Tools"]
    labels = [
        "opsagent_otel_session13",
        "rule_based_otel",
        "ad_only_otel",
        "llm_no_tools_otel",
    ]
    metric_keys = ["recall_at_1", "recall_at_3", "mean_confidence"]
    metric_names = ["Recall@1", "Recall@3", "Mean Confidence"]

    dirs = data.summary["directories"]
    fig, ax = plt.subplots(figsize=(11, 6))
    x = np.arange(len(metric_names))
    width = 0.2

    colors = ["#2ecc71", "#3498db", "#9b59b6", "#e67e22"]
    for i, (label, investigator, color) in enumerate(
        zip(labels, investigators, colors, strict=True)
    ):
        vals = [dirs[label].get(k, 0.0) for k in metric_keys]
        bars = ax.bar(x + i * width, vals, width, label=investigator, color=color)
        for bar, v in zip(bars, vals, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.015,
                f"{v:.2f}",
                ha="center",
                fontsize=8,
            )

    ax.set_xticks(x + width * (len(investigators) - 1) / 2)
    ax.set_xticklabels(metric_names)
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1.15)
    ax.set_title("OTel Demo — OpsAgent vs. Internal Baselines (n=35 each)")
    ax.legend(loc="upper right")
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Viz 3 — Detection latency box plot (OpsAgent only)
# ---------------------------------------------------------------------------


def plot_viz3_detection_latency(data: LoadedData, out_path: Path) -> None:
    per_fault_latency: dict[str, list[float]] = {}
    for record in data.session13:
        fault = record.get("fault_type", "unknown")
        latency = record.get("detection_latency_seconds")
        if latency is None:
            continue
        per_fault_latency.setdefault(fault, []).append(float(latency))

    faults = sorted(per_fault_latency.keys())
    latencies = [per_fault_latency[f] for f in faults]

    fig, ax = plt.subplots(figsize=(11, 5))
    bp = ax.boxplot(latencies, tick_labels=faults, patch_artist=True)
    for patch in bp["boxes"]:
        patch.set_facecolor("#3498db")
        patch.set_alpha(0.7)
    ax.axhline(y=60, color="red", linestyle="--", linewidth=1.5, label="Target < 60s")
    ax.set_ylabel("Detection Latency (seconds)")
    ax.set_xlabel("Fault Type")
    ax.set_title("Detection Latency by Fault Type — OpsAgent on OTel Demo")
    plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
    ax.legend(loc="upper right")
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Viz 4 — Confusion matrices (OpsAgent + LLM-No-Tools side by side)
# ---------------------------------------------------------------------------


def _confusion_matrix(results: list[dict]) -> tuple[list[str], np.ndarray]:
    services = sorted(
        {r.get("ground_truth") for r in results if r.get("ground_truth")}
        | {r.get("predicted_root_cause") for r in results if r.get("predicted_root_cause")}
    )
    idx = {s: i for i, s in enumerate(services)}
    matrix = np.zeros((len(services), len(services)), dtype=int)
    for r in results:
        gt = r.get("ground_truth")
        pred = r.get("predicted_root_cause")
        if gt in idx and pred in idx:
            matrix[idx[gt], idx[pred]] += 1
    return services, matrix


def plot_viz4_confusion_matrix(data: LoadedData, out_path: Path) -> None:
    ops_services, ops_matrix = _confusion_matrix(data.session13)
    llm_services, llm_matrix = _confusion_matrix(data.baselines["LLM-No-Tools"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    sns.heatmap(
        ops_matrix,
        annot=True,
        fmt="d",
        cmap="Greens",
        xticklabels=ops_services,
        yticklabels=ops_services,
        ax=ax1,
        cbar_kws={"label": "Count"},
    )
    ax1.set_title("OpsAgent — Confusion Matrix (n=35, diagonal = correct)")
    ax1.set_xlabel("Predicted Root Cause")
    ax1.set_ylabel("Actual Root Cause")
    ax1.set_xticklabels(ops_services, rotation=35, ha="right")

    sns.heatmap(
        llm_matrix,
        annot=True,
        fmt="d",
        cmap="Oranges",
        xticklabels=llm_services,
        yticklabels=llm_services,
        ax=ax2,
        cbar_kws={"label": "Count"},
    )
    ax2.set_title("LLM-Without-Tools — Confusion Matrix (cart-bias visible)")
    ax2.set_xlabel("Predicted Root Cause")
    ax2.set_ylabel("Actual Root Cause")
    ax2.set_xticklabels(llm_services, rotation=35, ha="right")

    fig.suptitle("Root-Cause Prediction Confusion Matrices — OTel Demo", fontsize=14)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Viz 5 — Causal graph examples (embed 3 reports verbatim)
# ---------------------------------------------------------------------------


def plot_viz5_causal_examples(data: LoadedData, out_path: Path) -> None:
    """Render the causal-analysis section from 3 exemplar reports as text.

    The RCA reports contain a single-line ASCII root-cause block plus a
    counterfactual sentence. We render three exemplars side-by-side inside
    a matplotlib figure (text blocks), which is more honest than inventing
    a synthetic NetworkX graph from a single-line ASCII string.
    """
    exemplars = [
        ("service_crash_run_1", "Service Crash (cartservice)"),
        ("memory_pressure_run_1", "Memory Pressure (checkoutservice)"),
        ("high_latency_run_2", "High Latency (frontend)"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    for ax, (test_id, title) in zip(axes, exemplars, strict=True):
        report_path = SESSION13_REPORTS / f"{test_id}.md"
        if not report_path.is_file():
            ax.text(0.5, 0.5, f"Report not found:\n{test_id}", ha="center", va="center")
            ax.set_axis_off()
            ax.set_title(title, fontsize=12)
            continue

        text = report_path.read_text()
        extract = _extract_causal_section(text)
        ax.text(
            0.02,
            0.98,
            extract,
            family="monospace",
            fontsize=8,
            verticalalignment="top",
            wrap=True,
        )
        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.set_axis_off()

    fig.suptitle(
        "Causal-Analysis Excerpts — 3 Exemplar OpsAgent RCA Reports (Session 13)",
        fontsize=14,
    )
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def _extract_causal_section(report_text: str) -> str:
    """Pull the CAUSAL ANALYSIS section from an RCA report text.

    Returns a wrapped, readable text block for embedding in a matplotlib
    figure. Falls back to the first 600 chars if parsing fails.
    """
    match = re.search(
        r"CAUSAL ANALYSIS\s*[─\-]+\s*(.+?)(?=\n[─═]{10,}|\nRECOMMENDED ACTIONS)",
        report_text,
        re.DOTALL,
    )
    if not match:
        return report_text[:600]

    content = match.group(1).strip()
    wrapped: list[str] = []
    for line in content.splitlines():
        if len(line) <= 60:
            wrapped.append(line)
            continue
        words = line.split()
        current = ""
        for word in words:
            if len(current) + len(word) + 1 > 60:
                wrapped.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            wrapped.append(current)
    return "\n".join(wrapped[:40])  # cap the height


# ---------------------------------------------------------------------------
# Viz 6 — Agent tool-usage distribution (proxy from report text)
# ---------------------------------------------------------------------------

_TOOL_PATTERNS = {
    "query_metrics": re.compile(
        r"(probe_up|probe_latency|cpu_usage|memory_usage|memory_utilization|"
        r"memory_limit|network_(rx|tx)(_bytes|_errors)?_rate|container_)",
        re.IGNORECASE,
    ),
    "search_logs": re.compile(
        r"(\blogs?\b|\blog entr(y|ies)\b|stack trace|panic|"
        r"oomkilled|sigsegv|sigkill|crash-log|error message)",
        re.IGNORECASE,
    ),
    "discover_causation": re.compile(
        r"(pc algorithm|causal (graph|analysis|discovery)|counterfactual|"
        r"z-score|conditional independence)",
        re.IGNORECASE,
    ),
    "search_runbooks": re.compile(
        r"(runbook|remediation runbook|troubleshooting guide|sre handbook)",
        re.IGNORECASE,
    ),
    "get_topology": re.compile(
        r"(topology|dependency graph|upstream|downstream)",
        re.IGNORECASE,
    ),
}


def _count_tool_mentions_in_report(report_text: str) -> dict[str, int]:
    """Return a per-tool count of regex matches across the report text."""
    counts: dict[str, int] = {}
    for tool_name, pattern in _TOOL_PATTERNS.items():
        counts[tool_name] = len(pattern.findall(report_text))
    return counts


def plot_viz6_tool_usage(data: LoadedData, out_path: Path) -> None:
    """Pie chart of tool mentions across all 35 Session 13 RCA reports.

    Caveat: this is a PROXY, not a direct tool-call count. Session 13
    results JSONs did not capture per-call tool usage, so this counts how
    often each tool's *signals* appear in the final RCA reports. The agent's
    deterministic sweep (36 calls per investigation: 5 metrics x 6 services
    + 6 log calls = 30 query_metrics + 6 search_logs) makes query_metrics
    and search_logs dominant by construction; this visualization is
    illustrative rather than precise.
    """
    totals: Counter[str] = Counter()
    report_count = 0
    for record in data.session13:
        test_id = record.get("test_id")
        if not test_id:
            continue
        report_path = SESSION13_REPORTS / f"{test_id}.md"
        if not report_path.is_file():
            continue
        counts = _count_tool_mentions_in_report(report_path.read_text())
        for tool, c in counts.items():
            totals[tool] += c
        report_count += 1

    tools = list(totals.keys())
    values = [totals[t] for t in tools]
    colors_map = {
        "query_metrics": "#3498db",
        "search_logs": "#e67e22",
        "discover_causation": "#9b59b6",
        "search_runbooks": "#2ecc71",
        "get_topology": "#95a5a6",
    }
    colors = [colors_map.get(t, "#bdc3c7") for t in tools]

    fig, ax = plt.subplots(figsize=(9, 7))
    wedges, _, autotexts = ax.pie(
        values,
        labels=tools,
        colors=colors,
        autopct="%1.1f%%",
        startangle=90,
        wedgeprops={"edgecolor": "white", "linewidth": 1},
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontweight("bold")
    ax.set_title(
        f"Agent Tool-Usage Proxy (regex mentions across {report_count} RCA reports)\n"
        "NOTE: inferred from report text; sweep structure dominates by construction.",
        fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Viz 7 — Explanation quality histogram
# ---------------------------------------------------------------------------


def _compute_overall_score(row: dict[str, str]) -> float | None:
    """Return the overall_score as the mean of the 5 sub-dimensions.

    Falls back to the user-supplied ``overall_score`` cell if populated.
    Returns None if the row has no scores filled in.
    """
    if row.get("overall_score", "").strip():
        try:
            return float(row["overall_score"])
        except ValueError:
            pass

    sub_keys = [
        "root_cause_accuracy",
        "evidence_quality",
        "causal_analysis",
        "recommendations",
        "presentation",
    ]
    sub_vals: list[float] = []
    for key in sub_keys:
        cell = row.get(key, "").strip()
        if not cell:
            return None
        try:
            sub_vals.append(float(cell))
        except ValueError:
            return None
    return sum(sub_vals) / len(sub_vals)


def _read_quality_scores() -> list[dict[str, str]]:
    if not QUALITY_CSV.is_file():
        return []
    with open(QUALITY_CSV, newline="") as handle:
        return list(csv.DictReader(handle))


def plot_viz7_quality_distribution(out_path: Path) -> bool:
    """Plot the distribution; returns True if any scores are filled, else False."""
    rows = _read_quality_scores()
    scores = [s for s in (_compute_overall_score(r) for r in rows) if s is not None]
    if not scores:
        # Placeholder chart so the notebook/doc still has a file to reference.
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.text(
            0.5,
            0.5,
            "Explanation Quality Scores PENDING\n\n"
            "The user has not filled in the CSV yet.\n"
            "Run `poetry run python scripts/make_evaluation_charts.py --viz 7`\n"
            "again after scoring is complete.",
            ha="center",
            va="center",
            fontsize=12,
        )
        ax.set_title("Viz 7 — Explanation Quality Score Distribution (pending)")
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(out_path)
        plt.close(fig)
        return False

    arr = np.array(scores)
    mean = float(arr.mean())
    ci_lo, ci_hi = (
        (
            mean - 1.96 * arr.std(ddof=1) / np.sqrt(len(arr)),
            mean + 1.96 * arr.std(ddof=1) / np.sqrt(len(arr)),
        )
        if len(arr) > 1
        else (mean, mean)
    )

    fig, ax = plt.subplots(figsize=(10, 5))
    bins = np.arange(0.75, 5.5, 0.25)
    ax.hist(arr, bins=bins, color="#3498db", edgecolor="white")
    ax.axvline(x=4.0, color="red", linestyle="--", linewidth=1.5, label="Target >= 4.0")
    ax.axvline(
        x=mean,
        color="#27ae60",
        linestyle="-",
        linewidth=2,
        label=f"Mean = {mean:.2f} (95% CI [{ci_lo:.2f}, {ci_hi:.2f}])",
    )
    ax.set_xlabel("Overall Score (1-5)")
    ax.set_ylabel("Count")
    ax.set_title(f"Explanation Quality Distribution (n={len(arr)} OpsAgent reports, Session 13)")
    ax.set_xlim(0.75, 5.25)
    ax.legend()
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return True


# ---------------------------------------------------------------------------
# Viz 8 — RCAEval OpsAgent vs published BARO (OB-variant)
# ---------------------------------------------------------------------------


def _load_published_baselines() -> dict:
    import yaml

    with open(PUBLISHED_BASELINES_YAML) as handle:
        return yaml.safe_load(handle)


def plot_viz8_rcaeval_comparison(data: LoadedData, out_path: Path) -> None:
    published = _load_published_baselines()
    dirs = data.summary["directories"]

    opsagent_ob = {
        "RE1-OB\n(n=125)": dirs["opsagent_rcaeval_re1_ob"]["recall_at_1"],
        "RE2-OB\n(n=91)": dirs["opsagent_rcaeval_re2_ob"]["recall_at_1"],
        "OB Combined\n(n=216)": dirs["opsagent_rcaeval_ob_combined"]["recall_at_1"],
    }
    opsagent_ob_r3 = {
        "RE1-OB\n(n=125)": dirs["opsagent_rcaeval_re1_ob"]["recall_at_3"],
        "RE2-OB\n(n=91)": dirs["opsagent_rcaeval_re2_ob"]["recall_at_3"],
        "OB Combined\n(n=216)": dirs["opsagent_rcaeval_ob_combined"]["recall_at_3"],
    }
    baro_avg5 = float(published["baro"]["overall_ob"])

    categories = list(opsagent_ob.keys()) + ["BARO (OB, Avg@5)"]
    r1_values = list(opsagent_ob.values()) + [baro_avg5]
    r3_values = list(opsagent_ob_r3.values()) + [baro_avg5]
    colors = ["#3498db", "#3498db", "#3498db", "#95a5a6"]

    fig, ax = plt.subplots(figsize=(11, 6))
    x = np.arange(len(categories))
    width = 0.35

    bars1 = ax.bar(
        x - width / 2,
        r1_values,
        width,
        label="OpsAgent Recall@1 / BARO Avg@5",
        color=colors,
    )
    bars2 = ax.bar(
        x + width / 2,
        r3_values,
        width,
        label="OpsAgent Recall@3",
        color=["#85c1e9" if c == "#3498db" else "#d7dbdd" for c in colors],
        hatch="//",
    )

    for bars, vals in [(bars1, r1_values), (bars2, r3_values)]:
        for bar, v in zip(bars, vals, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.015,
                f"{v:.2f}",
                ha="center",
                fontsize=9,
            )

    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1.05)
    ax.set_title(
        "RCAEval-OB — OpsAgent vs. Published BARO Baseline\n"
        "NOTE: BARO metric = Avg@5 (BARO paper Table 3); OpsAgent = Recall@1 / Recall@3. "
        "Avg@5 strictly dominates Recall@1."
    )
    ax.legend(loc="upper left")
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Viz 9 — RCAEval RE2-OB Recall@1 by fault type
# ---------------------------------------------------------------------------


def plot_viz9_rcaeval_re2_by_fault(data: LoadedData, out_path: Path) -> None:
    per_fault = data.summary["directories"]["opsagent_rcaeval_re2_ob"]["per_fault"]
    faults = sorted(per_fault.keys())
    recalls = [per_fault[f]["recall_at_1"] for f in faults]
    recalls3 = [per_fault[f]["recall_at_3"] for f in faults]
    ns = [per_fault[f]["n"] for f in faults]

    x = np.arange(len(faults))
    width = 0.35
    fig, ax = plt.subplots(figsize=(11, 5))
    bars1 = ax.bar(x - width / 2, recalls, width, label="Recall@1", color="#3498db")
    bars2 = ax.bar(x + width / 2, recalls3, width, label="Recall@3", color="#85c1e9", hatch="//")
    for bars, vals in [(bars1, recalls), (bars2, recalls3)]:
        for bar, v in zip(bars, vals, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.02,
                f"{v:.2f}",
                ha="center",
                fontsize=9,
            )
    labels = [f"{f}\n(n={n})" for f, n in zip(faults, ns, strict=True)]
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Recall")
    ax.set_ylim(0, 1.0)
    ax.set_title("RCAEval RE2-OB — OpsAgent Recall by Fault Type (n=91)")
    ax.legend()
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


VIZ_REGISTRY: dict[int, tuple[str, str]] = {
    1: ("01_recall_by_fault.png", "plot_viz1_recall_by_fault"),
    2: ("02_baseline_comparison.png", "plot_viz2_baseline_comparison"),
    3: ("03_detection_latency.png", "plot_viz3_detection_latency"),
    4: ("04_confusion_matrix.png", "plot_viz4_confusion_matrix"),
    5: ("05_causal_examples.png", "plot_viz5_causal_examples"),
    6: ("06_tool_usage.png", "plot_viz6_tool_usage"),
    7: ("07_quality_distribution.png", "plot_viz7_quality_distribution"),
    8: ("08_rcaeval_comparison.png", "plot_viz8_rcaeval_comparison"),
    9: ("09_rcaeval_re2_by_fault.png", "plot_viz9_rcaeval_re2_by_fault"),
}


def _resolve_plot(name: str):
    return globals()[name]


def run_all(skip_pending_quality: bool = True) -> dict[int, Path]:
    """Generate every chart. Returns {viz_id: saved_path}."""
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    data = load_all()
    saved: dict[int, Path] = {}
    quality_filled = False

    for viz_id, (filename, func_name) in sorted(VIZ_REGISTRY.items()):
        out_path = SAVE_DIR / filename
        func = _resolve_plot(func_name)
        if viz_id == 7:
            # Special handling: quality chart may be pending.
            quality_filled = func(out_path)
            saved[viz_id] = out_path
            status = "" if quality_filled else " (pending user scoring)"
            print(f"Viz {viz_id}: {out_path.relative_to(PROJECT_ROOT)}{status}")
        else:
            func(data, out_path)
            saved[viz_id] = out_path
            print(f"Viz {viz_id}: {out_path.relative_to(PROJECT_ROOT)}")

    if skip_pending_quality and not quality_filled:
        print("\nNOTE: Viz 7 is a placeholder until explanation_quality_scores.csv is filled.")
    return saved


def run_one(viz_id: int) -> Path:
    if viz_id not in VIZ_REGISTRY:
        raise ValueError(f"Unknown viz id {viz_id}. Valid ids: {sorted(VIZ_REGISTRY)}")
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    filename, func_name = VIZ_REGISTRY[viz_id]
    out_path = SAVE_DIR / filename
    if viz_id == 7:
        _resolve_plot(func_name)(out_path)
    else:
        data = load_all()
        _resolve_plot(func_name)(data, out_path)
    print(f"Viz {viz_id}: {out_path.relative_to(PROJECT_ROOT)}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--viz",
        type=int,
        default=None,
        help="Generate only the specified visualization (1-9). Default: all.",
    )
    args = parser.parse_args(argv)
    if args.viz is not None:
        run_one(args.viz)
    else:
        run_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
