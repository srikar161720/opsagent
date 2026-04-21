"""RCAEval cross-system evaluation harness for OpsAgent.

Runs the OpsAgent LangGraph investigation against every case in an
RCAEval variant (RE1, RE2, or RE3), using the offline-mode tool
dispatchers added in Session 15 so no live Prometheus/Loki is required.

Per-case results land in ``data/evaluation/rcaeval_<variant>_<filter>/``
as individual JSON files; a ``summary.json`` with overall Recall@1,
Recall@3, and per-fault-type breakdowns is written after all cases
complete. ``--resume`` re-runs only cases whose output JSON isn't on
disk yet — useful when Gemini API transients interrupt a multi-hour
run.

Week 10 Part 1 scope is OB-only (~246 cases across RE1-OB, RE2-OB,
RE3-OB) because the agent's :class:`TopologyGraph` matches the OTel
Astronomy Shop / Online Boutique service names. SS and TT variants are
deferred pending per-system topology graphs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.preprocessing.rcaeval_adapter import RCAEvalDataAdapter
from tests.evaluation.metrics_calculator import calculate_metrics

logger = logging.getLogger(__name__)


# Recognised variant names and their on-disk directory layout.
_VARIANTS: dict[str, str] = {
    "re1": "re1",
    "re2": "re2",
    "re3": "re3",
}

# System filters. ``"ob"`` keeps only Online Boutique (OTel Astronomy Shop
# overlap). ``"all"`` disables filtering. SS and TT are accepted for
# completeness but produce empty runs this session (see docstring above).
_SYSTEM_FILTERS: dict[str, str | None] = {
    "ob": "-OB/",
    "ss": "-SS/",
    "tt": "-TT/",
    "all": None,
}


# Per-system service whitelists. RCAEval CSVs ship a dataset-wide metric
# table that contains traffic labels (``PassthroughCluster``,
# ``InboundPassthroughClusterIpv4``), external-facing proxy artefacts
# (``frontend-external``, ``frontend-check``), pod container labels
# (``main``), cross-system leakage (RE3-OB CSVs contain the full Sock
# Shop stack: ``carts``, ``carts-db``, ``orders``, ``rabbitmq``,
# ``queue-master``, ``user-db``, etc.), and load-generator labels
# (``loadgenerator``). None of these are real services under
# investigation. Without a whitelist, they leak into ``affected_services``
# and get surfaced as root-cause hypotheses — the agent has no way to
# tell them apart from real services based on column names alone.
#
# The whitelists are the ground-truth service sets per dataset system.
# For RCAEval-OB we use the full OTel Astronomy Shop / Online Boutique
# service inventory (11 services). Cases whose ``metrics`` dict contains
# keys outside this list have those keys dropped before the agent sees
# them.
_OB_SERVICES: frozenset[str] = frozenset(
    {
        "adservice",
        "cartservice",
        "checkoutservice",
        "currencyservice",
        "emailservice",
        "frontend",
        "paymentservice",
        "productcatalogservice",
        "recommendationservice",
        "redis",
        "shippingservice",
    }
)

# Map from system_filter CLI key → whitelist frozenset. ``None`` disables
# filtering entirely (used by the ``"all"`` mode or when the caller
# wants to test without a whitelist).
_SYSTEM_WHITELISTS: dict[str, frozenset[str] | None] = {
    "ob": _OB_SERVICES,
    # SS / TT whitelists are intentionally not populated. The OB-only
    # scope was the user-chosen subset for Session 15. If SS / TT
    # evaluation is added later, populate these with the corresponding
    # Sock Shop / Train Ticket service inventories.
    "ss": None,
    "tt": None,
    "all": None,
}


def _filter_case_to_whitelist(
    case: dict,
    whitelist: frozenset[str] | None,
) -> dict:
    """Filter ``case["metrics"]`` to contain only whitelisted services.

    Returns a NEW case dict — the original is not mutated. When
    ``whitelist is None`` the case is returned unchanged.

    This is the scope-enforcement layer for RCAEval evaluation: the
    adapter faithfully exposes every column the CSV contains, including
    noise / cross-system leakage. The evaluator trims the metric view
    down to the OB services the agent is meant to consider. The
    ground-truth root-cause service (always a real OB service per the
    fault directory name) is guaranteed to remain in the filtered dict
    because fault directories only name real OB services.
    """
    if whitelist is None:
        return case
    filtered_metrics = {svc: df for svc, df in case.get("metrics", {}).items() if svc in whitelist}
    new_case = dict(case)
    new_case["metrics"] = filtered_metrics
    return new_case


def _case_id_to_filename(case_id: str) -> str:
    """Convert a hierarchical case ID to a safe JSON filename.

    ``"RE2-OB/checkoutservice_cpu/1"`` →
    ``"RE2-OB__checkoutservice_cpu__1.json"``.

    Using a double-underscore separator avoids collisions with the
    single underscores inside fault-directory names (``{service}_{fault}``).
    """
    return case_id.replace("/", "__") + ".json"


def _build_investigator() -> Any:
    """Construct the default :class:`src.agent.executor.AgentExecutor`.

    Mirrors the factory pattern established by
    ``tests/evaluation/fault_injection_suite.py``. Keeps the evaluator
    decoupled from the specific config path so future variants (e.g.
    baseline investigators) can plug in without harness changes.
    """
    from src.agent.executor import AgentExecutor

    return AgentExecutor.from_config("configs/agent_config.yaml")


def _filter_cases(
    case_ids: list[str],
    system_filter: str | None,
) -> list[str]:
    """Keep only cases matching the system-filter substring."""
    if not system_filter:
        return case_ids
    return [cid for cid in case_ids if system_filter in cid]


def _already_done(output_dir: Path, case_id: str) -> bool:
    """True when a per-case JSON already exists on disk."""
    return (output_dir / _case_id_to_filename(case_id)).exists()


def _build_alert(case: dict) -> dict:
    """Construct the synthetic alert dict passed to ``agent.investigate``.

    RCAEval cases are pre-labeled failures, so we synthesise a minimal
    alert with ``anomaly_score=1.0`` and the timestamp from
    ``inject_time.txt``. ``affected_services`` is derived from the
    preloaded metric DataFrame keys so the agent's topology analysis
    has a concrete service list.
    """
    services = sorted(case.get("metrics", {}).keys())
    return {
        "title": "Anomaly Detected — Automated Investigation Triggered",
        "severity": "evaluation",
        "timestamp": case["anomaly_timestamp"],
        "anomaly_score": 1.0,
        "affected_services": services,
    }


# Substrings indicating the exception chain bottomed out at a
# Gemini API rate-limit / quota-exhaustion error. Google's
# ``google-genai`` library raises ``ClientError: 429 RESOURCE_EXHAUSTED``,
# which langchain wraps as
# ``ChatGoogleGenerativeAIError: Error calling model ... (RESOURCE_EXHAUSTED)``.
# We match against the textual representation because both the raw and
# wrapped exception types are runtime-imported from optional deps —
# substring matching is more portable than ``isinstance`` checks.
_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "RESOURCE_EXHAUSTED",
    "429 Too Many Requests",
    "429 Client Error",
    "quota",  # matches "Resource has been exhausted (e.g. check quota)"
    "rate limit",
    "rate_limit",
)


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Return True when ``exc`` looks like a Gemini API rate-limit / 429.

    Detection is intentionally loose — the exception chain from
    google-genai → tenacity → langchain → langgraph nests several
    wrapper types, and we care about the root-cause string content
    rather than any specific class hierarchy.
    """
    msg = str(exc).lower()
    # Check both direct string content and the exception's ``__cause__``
    # / ``__context__`` chain so wrapped 429s are still caught.
    seen: list[str] = [msg]
    cursor: BaseException | None = exc.__cause__ or exc.__context__
    depth = 0
    while cursor is not None and depth < 5:
        seen.append(str(cursor).lower())
        cursor = cursor.__cause__ or cursor.__context__
        depth += 1
    combined = " ".join(seen)
    return any(marker.lower() in combined for marker in _RATE_LIMIT_MARKERS)


def _unknown_prediction(exc: BaseException) -> dict:
    """Build a stub prediction when a case can't be investigated.

    Used when an exception reaches the evaluator after retries have been
    exhausted (or the exception wasn't rate-limit-related and is
    treated as a single-case failure). The stub mirrors what
    ``agent.investigate`` returns so downstream ``_build_record`` keeps
    working.
    """
    return {
        "root_cause": "unknown",
        "root_cause_confidence": 0.0,
        "top_3_predictions": [],
        "confidence": 0.0,
        "rca_report": f"Investigation failed: {exc}",
        "recommended_actions": [],
    }


def _invoke_agent_raw(agent: Any, case: dict) -> dict:
    """Call ``agent.investigate`` with preloaded case data — may raise.

    Unlike the older ``_invoke_agent``, this does NOT swallow exceptions.
    The caller (``evaluate_on_rcaeval``) is responsible for catch-and-
    retry on rate-limit errors and for falling back to the unknown stub
    on permanent failures. Keeping this raw makes the retry logic
    above testable in isolation.
    """
    alert = _build_alert(case)
    return agent.investigate(
        alert=alert,
        metrics=case["metrics"],
        logs=case.get("logs"),
        anomaly_timestamp=case["anomaly_timestamp"],
        start_time=case["anomaly_timestamp"],
    )


def _invoke_agent_with_retry(
    agent: Any,
    case: dict,
    rate_limit_retries: int,
    rate_limit_backoff_seconds: float,
) -> tuple[dict, int]:
    """Invoke the agent with rate-limit-aware retry.

    Returns ``(prediction_dict, attempts_made)``. On a rate-limit error,
    sleeps ``rate_limit_backoff_seconds * 2**(attempt-1)`` and retries up
    to ``rate_limit_retries`` additional times (so worst-case
    total attempts = ``rate_limit_retries + 1``). Backoff is exponential
    so short bursts clear quickly while sustained exhaustion escalates
    to multi-minute waits.

    Non-rate-limit exceptions and exhausted rate-limit retries fall
    through to :func:`_unknown_prediction`. Returning the stub
    preserves the pre-Session-15 behaviour of "a single broken case
    doesn't abort the whole run."
    """
    case_id = case.get("case_id", "<unknown>")
    last_exc: BaseException | None = None
    max_total_attempts = max(1, rate_limit_retries + 1)
    for attempt in range(1, max_total_attempts + 1):
        try:
            return _invoke_agent_raw(agent, case), attempt
        except Exception as exc:
            last_exc = exc
            if not _is_rate_limit_error(exc):
                logger.exception("Case %s failed (non-rate-limit)", case_id)
                return _unknown_prediction(exc), attempt
            if attempt >= max_total_attempts:
                logger.warning(
                    "Case %s: rate-limit retries exhausted after %d attempts. "
                    "Recording as 'unknown' prediction. Error: %s",
                    case_id,
                    attempt,
                    exc,
                )
                return _unknown_prediction(exc), attempt
            wait = rate_limit_backoff_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Case %s: rate-limit 429 on attempt %d/%d. Sleeping %.1fs before retry.",
                case_id,
                attempt,
                max_total_attempts,
                wait,
            )
            time.sleep(wait)
    # Fallback — should never reach here since the loop body always
    # returns, but keeps the function total.
    return _unknown_prediction(last_exc or RuntimeError("unreachable")), max_total_attempts


# Default timings for rate-limit handling — exposed as defaults so CLI
# callers and tests can both reference them.
_DEFAULT_INTER_CASE_DELAY_SECONDS: float = 5.0
_DEFAULT_RATE_LIMIT_RETRIES: int = 3
_DEFAULT_RATE_LIMIT_BACKOFF_SECONDS: float = 60.0


def _build_record(
    case: dict,
    prediction: dict,
    dataset: str,
    investigation_duration: float,
) -> dict:
    """Build the per-case result dict that matches the evaluator spec.

    Fields (per ``context/data_pipeline_specs.md`` §7.2):
    ``case_id``, ``dataset``, ``fault_type``, ``ground_truth``,
    ``predicted_root_cause``, ``top_3_predictions``, ``confidence``,
    ``is_correct``, ``notes``. Two additional fields mirror the
    fault-injection suite's per-test JSONs so
    :func:`tests.evaluation.metrics_calculator.calculate_metrics`
    produces meaningful MTTR/latency stats:
    ``investigation_duration_seconds`` and ``detection_latency_seconds``.
    """
    gt = case.get("ground_truth", {})
    root_cause = prediction.get("root_cause") or "unknown"
    ground_truth = gt.get("root_cause_service", "unknown")
    return {
        "case_id": case["case_id"],
        "dataset": dataset,
        "fault_type": gt.get("fault_type", "unknown"),
        "ground_truth": ground_truth,
        "predicted_root_cause": root_cause,
        "top_3_predictions": prediction.get("top_3_predictions", []),
        "confidence": float(prediction.get("confidence", 0.0) or 0.0),
        "is_correct": root_cause == ground_truth,
        "investigation_duration_seconds": investigation_duration,
        # RCAEval anomaly timestamps are pre-recorded; detection latency
        # is irrelevant (no real-time detection pipeline). Report 0 so
        # downstream metric calculators don't misinterpret a missing key.
        "detection_latency_seconds": 0.0,
        "notes": "",
    }


def _persist_record(output_dir: Path, record: dict) -> None:
    """Write a per-case record to disk."""
    filename = _case_id_to_filename(record["case_id"])
    path = output_dir / filename
    with path.open("w") as f:
        json.dump(record, f, indent=2)


def _iter_target_cases(
    adapter: RCAEvalDataAdapter,
    system_filter: str | None,
    subsample: int | None,
    resume: bool,
    output_dir: Path,
) -> Iterable[str]:
    """Yield case IDs to evaluate, applying filter / subsample / resume."""
    cases = _filter_cases(adapter.list_cases(), system_filter)
    if resume:
        cases = [cid for cid in cases if not _already_done(output_dir, cid)]
    if subsample is not None and subsample > 0:
        cases = cases[:subsample]
    return cases


def _load_existing_records(output_dir: Path) -> list[dict]:
    """Load already-written per-case JSONs (supports --resume summary math)."""
    records: list[dict] = []
    for path in sorted(output_dir.glob("*.json")):
        if path.name == "summary.json":
            continue
        try:
            with path.open() as f:
                records.append(json.load(f))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping unreadable result %s: %s", path, exc)
    return records


def evaluate_on_rcaeval(
    agent: Any,
    dataset_path: str,
    results_output_dir: str,
    system_filter: str | None = None,
    subsample: int | None = None,
    resume: bool = False,
    whitelist: frozenset[str] | None = None,
    inter_case_delay_seconds: float = _DEFAULT_INTER_CASE_DELAY_SECONDS,
    rate_limit_retries: int = _DEFAULT_RATE_LIMIT_RETRIES,
    rate_limit_backoff_seconds: float = _DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
) -> dict:
    """Evaluate OpsAgent on every case in a single RCAEval variant.

    Args:
        agent: Investigator with an ``.investigate(alert, metrics, logs,
            anomaly_timestamp, start_time) → dict`` method — typically an
            :class:`src.agent.executor.AgentExecutor` instance.
        dataset_path: Filesystem path to one RCAEval variant
            (e.g. ``"data/RCAEval/re2"``). Contains system subdirectories
            RE{N}-OB / RE{N}-SS / RE{N}-TT.
        results_output_dir: Destination directory for per-case JSONs and
            the final ``summary.json``.
        system_filter: Optional substring matched against case IDs (e.g.
            ``"-OB/"`` keeps Online Boutique cases only).
        subsample: Optional cap on the number of cases to evaluate.
        resume: When True, skip cases whose per-case JSON already exists
            on disk. Useful for multi-hour runs interrupted by API
            transients.
        whitelist: Optional frozenset of real service names. When set,
            each case's ``metrics`` dict is filtered to only these
            services before being passed to the agent. This removes
            RCAEval dataset-level noise (traffic labels, cross-system
            services, pod container labels) that would otherwise appear
            as root-cause hypotheses. Pass ``None`` to disable filtering.
        inter_case_delay_seconds: Seconds to sleep between cases. Default
            5.0s spreads sustained Gemini API load so the preview
            model's per-minute quota bucket doesn't saturate. Set to 0
            to disable (e.g. in unit tests).
        rate_limit_retries: Maximum rate-limit retries per case (in
            addition to the initial attempt). Default 3 → worst case 4
            total attempts before falling back to "unknown". Each retry
            sleeps ``rate_limit_backoff_seconds * 2**(attempt-1)`` —
            60s, 120s, 240s by default.
        rate_limit_backoff_seconds: Base backoff for rate-limit retries.
            Doubles on each retry so the third retry waits 4× this
            value.

    Returns:
        Summary dict with keys ``dataset``, ``total_cases``,
        ``recall_at_1``, ``recall_at_3``, ``recall_by_fault``,
        ``avg_investigation_duration_seconds``, ``elapsed_seconds``.
    """
    output_dir = Path(results_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    adapter = RCAEvalDataAdapter(dataset_path)
    target_cases = list(_iter_target_cases(adapter, system_filter, subsample, resume, output_dir))
    dataset_name = Path(dataset_path).name

    print(
        f"[rcaeval:{dataset_name}] Evaluating {len(target_cases)} case(s) "
        f"(system_filter={system_filter!r}, subsample={subsample}, "
        f"resume={resume}, whitelist={len(whitelist) if whitelist else 'disabled'}, "
        f"inter_case_delay={inter_case_delay_seconds:.1f}s, "
        f"rate_limit_retries={rate_limit_retries})"
    )

    suite_start = time.perf_counter()
    for idx, case_id in enumerate(target_cases, start=1):
        try:
            case = adapter.load_case(case_id)
        except Exception as exc:
            logger.warning("Skipping case %s (load failed): %s", case_id, exc)
            continue

        # Apply OB service whitelist: drop RCAEval dataset-level noise
        # (PassthroughCluster, loadgenerator, Sock Shop leakage, etc.)
        # from the case's metric services before the agent sees them.
        case = _filter_case_to_whitelist(case, whitelist)

        case_started = datetime.now(UTC)
        prediction, attempts = _invoke_agent_with_retry(
            agent,
            case,
            rate_limit_retries=rate_limit_retries,
            rate_limit_backoff_seconds=rate_limit_backoff_seconds,
        )
        investigation_duration = (datetime.now(UTC) - case_started).total_seconds()

        record = _build_record(case, prediction, dataset_name, investigation_duration)
        if attempts > 1:
            record["notes"] = f"retried {attempts - 1} time(s) for rate-limit 429"
        _persist_record(output_dir, record)

        marker = "✓" if record["is_correct"] else "✗"
        attempt_note = f" [retries={attempts - 1}]" if attempts > 1 else ""
        print(
            f"  [{idx}/{len(target_cases)}] {marker} {case_id}: "
            f"predicted={record['predicted_root_cause']}, "
            f"gt={record['ground_truth']}, conf={record['confidence']:.2f}, "
            f"dur={investigation_duration:.1f}s{attempt_note}"
        )

        # Inter-case delay: spread sustained LLM load so preview-model
        # burst caps on gemini-3-flash-preview don't saturate. Skip
        # after the final case — no point waiting when nothing's next.
        if inter_case_delay_seconds > 0 and idx < len(target_cases):
            time.sleep(inter_case_delay_seconds)

    elapsed = time.perf_counter() - suite_start
    # Load ALL on-disk records (including previous partial runs when
    # resume=True) so the summary reflects the full state.
    all_records = _load_existing_records(output_dir)
    metrics = calculate_metrics(all_records)

    summary = {
        "dataset": dataset_name,
        "total_cases": len(all_records),
        "recall_at_1": metrics.recall_at_1,
        "recall_at_3": metrics.recall_at_3,
        "recall_by_fault": metrics.recall_by_fault,
        "avg_investigation_duration_seconds": metrics.avg_mttr_proxy,
        "ci_recall_at_1": list(metrics.ci_recall_at_1) if metrics.ci_recall_at_1 else None,
        "elapsed_seconds": elapsed,
    }

    with (output_dir / "summary.json").open("w") as f:
        json.dump(summary, f, indent=2)

    print(
        f"\n[rcaeval:{dataset_name}] Summary:\n"
        f"  Recall@1:       {summary['recall_at_1']:.1%}\n"
        f"  Recall@3:       {summary['recall_at_3']:.1%}\n"
        f"  Total cases:    {summary['total_cases']}\n"
        f"  Elapsed:        {elapsed:.1f}s\n"
    )
    return summary


def run_all_rcaeval_variants(
    agent: Any,
    base_path: str = "data/RCAEval",
    system_filter: str | None = None,
    subsample: int | None = None,
    resume: bool = False,
    inter_case_delay_seconds: float = _DEFAULT_INTER_CASE_DELAY_SECONDS,
    rate_limit_retries: int = _DEFAULT_RATE_LIMIT_RETRIES,
    rate_limit_backoff_seconds: float = _DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
) -> dict[str, dict]:
    """Run :func:`evaluate_on_rcaeval` sequentially on RE1, RE2, RE3.

    Each variant's output goes to
    ``data/evaluation/rcaeval_<variant>[_<system_filter>]/``. Returns a
    dict keyed by variant name (``"re1"``, ``"re2"``, ``"re3"``) mapping
    to that variant's summary dict.

    Accepts the USER-level ``system_filter`` KEY (e.g. ``"ob"``) — the
    same string the CLI passes to ``--system-filter``. Resolves both the
    substring (for case-id matching) and the whitelist internally via
    :data:`_SYSTEM_FILTERS` and :data:`_SYSTEM_WHITELISTS`.

    Rate-limit parameters (``inter_case_delay_seconds``,
    ``rate_limit_retries``, ``rate_limit_backoff_seconds``) are
    forwarded to each variant's :func:`evaluate_on_rcaeval` call.
    """
    all_results: dict[str, dict] = {}
    filter_key = system_filter or "all"
    substring = _SYSTEM_FILTERS.get(filter_key)
    whitelist = _SYSTEM_WHITELISTS.get(filter_key)
    for variant in ("re1", "re2", "re3"):
        dataset_path = f"{base_path}/{variant}"
        suffix = f"_{system_filter}" if system_filter else ""
        output_dir = f"data/evaluation/rcaeval_{variant}{suffix}"
        print(f"\n{'=' * 70}\nEvaluating RCAEval {variant.upper()}\n{'=' * 70}")
        all_results[variant] = evaluate_on_rcaeval(
            agent=agent,
            dataset_path=dataset_path,
            results_output_dir=output_dir,
            system_filter=substring,
            subsample=subsample,
            resume=resume,
            whitelist=whitelist,
            inter_case_delay_seconds=inter_case_delay_seconds,
            rate_limit_retries=rate_limit_retries,
            rate_limit_backoff_seconds=rate_limit_backoff_seconds,
        )
    return all_results


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate OpsAgent against RCAEval RE1/RE2/RE3 failure cases "
            "using offline-mode tool dispatchers (no live Docker stack needed)."
        ),
    )
    parser.add_argument(
        "--variant",
        choices=sorted(_VARIANTS),
        required=True,
        help="RCAEval variant to evaluate.",
    )
    parser.add_argument(
        "--system-filter",
        choices=sorted(_SYSTEM_FILTERS),
        default="ob",
        help=(
            "System variant within the RCAEval dataset to evaluate. "
            "Default 'ob' restricts to Online Boutique (OTel Astronomy Shop "
            "topology match)."
        ),
    )
    parser.add_argument(
        "--subsample",
        type=int,
        default=None,
        help="Evaluate only the first N cases (useful for smoke tests).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip cases whose per-case JSON already exists on disk. "
            "Use when a long run was interrupted by an API transient."
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Output directory override. Defaults to "
            "data/evaluation/rcaeval_<variant>_<system-filter>/."
        ),
    )
    parser.add_argument(
        "--base-path",
        type=str,
        default="data/RCAEval",
        help="Root directory containing re1/, re2/, re3/ subdirs.",
    )
    parser.add_argument(
        "--inter-case-delay",
        type=float,
        default=_DEFAULT_INTER_CASE_DELAY_SECONDS,
        help=(
            "Seconds to sleep between cases (default %(default)s). "
            "Spreads sustained Gemini API load so preview-model burst "
            "caps don't saturate. Set to 0 to disable."
        ),
    )
    parser.add_argument(
        "--rate-limit-retries",
        type=int,
        default=_DEFAULT_RATE_LIMIT_RETRIES,
        help=(
            "How many times to retry a case when Gemini returns 429 "
            "RESOURCE_EXHAUSTED (default %(default)s). Each retry uses "
            "exponential backoff based on --rate-limit-backoff-seconds. "
            "Retries exhausted → case recorded as 'unknown'."
        ),
    )
    parser.add_argument(
        "--rate-limit-backoff-seconds",
        type=float,
        default=_DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
        help=(
            "Base seconds for rate-limit exponential backoff "
            "(default %(default)s). Doubles on each retry: 60s, 120s, "
            "240s by default. Long enough to clear a per-minute quota "
            "bucket on gemini-3-flash-preview."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = _parse_args(argv)

    system_filter_substring = _SYSTEM_FILTERS[args.system_filter]
    whitelist = _SYSTEM_WHITELISTS.get(args.system_filter)
    dataset_path = f"{args.base_path}/{_VARIANTS[args.variant]}"

    default_output = f"data/evaluation/rcaeval_{args.variant}_{args.system_filter}"
    output_dir = args.output or default_output

    agent = _build_investigator()

    evaluate_on_rcaeval(
        agent=agent,
        dataset_path=dataset_path,
        results_output_dir=output_dir,
        system_filter=system_filter_substring,
        subsample=args.subsample,
        resume=args.resume,
        whitelist=whitelist,
        inter_case_delay_seconds=args.inter_case_delay,
        rate_limit_retries=args.rate_limit_retries,
        rate_limit_backoff_seconds=args.rate_limit_backoff_seconds,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    sys.exit(main())
