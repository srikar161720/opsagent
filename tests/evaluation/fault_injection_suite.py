"""Automated fault injection test runner for OTel Demo evaluation.

Orchestrates 35 fault injection tests (7 fault types × 5 runs):
inject fault -> wait -> call agent investigation -> save result JSON -> restore.

cpu_throttling was removed from the active scope in Session 12 because the
fault is undetectable on the idle demo — see the FAULT_SCRIPTS definition
and CLAUDE.md for the full rationale.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from src.agent.executor import AgentExecutor

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Active fault scenarios (7 types × 5 repetitions = 35 tests).
# cpu_throttling was removed in Session 12: `docker update --cpus 0.1` against
# productcatalogservice (baseline CPU 0.09%) never forces the service above
# the cap, so the fault produces no detectable signal. The shell script remains
# at demo_app/fault_scenarios/04_cpu_throttling.sh for reference, but it is
# intentionally absent from this registry.
FAULT_SCRIPTS: dict[str, str] = {
    "service_crash": "demo_app/fault_scenarios/01_service_crash.sh",
    "high_latency": "demo_app/fault_scenarios/02_high_latency.sh",
    "memory_pressure": "demo_app/fault_scenarios/03_memory_pressure.sh",
    "connection_exhaustion": "demo_app/fault_scenarios/05_connection_exhaustion.sh",
    "network_partition": "demo_app/fault_scenarios/06_network_partition.sh",
    "cascading_failure": "demo_app/fault_scenarios/07_cascading_failure.sh",
    "config_error": "demo_app/fault_scenarios/08_config_error.sh",
}

GROUND_TRUTH: dict[str, str] = {
    "service_crash": "cartservice",
    "high_latency": "frontend",
    "memory_pressure": "checkoutservice",
    "connection_exhaustion": "redis",
    "network_partition": "paymentservice",
    "cascading_failure": "cartservice",
    "config_error": "productcatalogservice",
}


def _resolve_script(fault_type: str) -> str:
    """Resolve fault script path relative to project root."""
    return str(PROJECT_ROOT / FAULT_SCRIPTS[fault_type])


# Default paths for the fine-tuned LSTM-AE model + z-score normalization stats
# used by ADOnlyBaseline. These are the artifacts produced by the Session 8
# fine-tune run (see PROGRESS.md Week 6.5). If either is missing at run time,
# ADOnlyBaseline logs a warning and falls back to its built-in raw-variance
# heuristic — the run still completes but its Recall@1 is under-representative
# of the proper LSTM-AE baseline.
_LSTM_AE_CHECKPOINT = "models/lstm_autoencoder/finetuned_otel.pt"
_LSTM_AE_SCALER_DIR = "data/splits/otel"

_VALID_BASELINES = ("rule-based", "ad-only", "llm-no-tools")


def _build_investigator(baseline_kind: str | None) -> Any:
    """Return the object whose ``.investigate(alert, start_time=...)`` will
    drive each test in the fault-injection suite.

    - ``baseline_kind is None`` (default): returns a real ``AgentExecutor``
      loaded from ``configs/agent_config.yaml`` — bit-for-bit identical to
      the Session 13 invocation pattern.
    - ``"rule-based"`` / ``"ad-only"`` / ``"llm-no-tools"``: returns a
      ``BaselineInvestigatorAdapter`` wrapping the corresponding baseline
      class. The adapter upshapes the baseline's 3-field ``predict()``
      return into the 6-field shape the harness expects.

    Unknown ``baseline_kind`` values raise ``ValueError`` listing the valid
    choices — surfaces typos as CLI errors rather than silent fallbacks.
    """
    if baseline_kind is None:
        from src.agent.executor import AgentExecutor

        return AgentExecutor.from_config("configs/agent_config.yaml")

    from tests.evaluation.baseline_comparison import (
        ADOnlyBaseline,
        BaselineInvestigatorAdapter,
        LLMWithoutToolsBaseline,
        RuleBasedBaseline,
    )

    if baseline_kind == "rule-based":
        return BaselineInvestigatorAdapter(RuleBasedBaseline(), kind="rule-based")
    if baseline_kind == "ad-only":
        return BaselineInvestigatorAdapter(
            ADOnlyBaseline(
                model_path=_LSTM_AE_CHECKPOINT,
                scaler_dir=_LSTM_AE_SCALER_DIR,
            ),
            kind="ad-only",
        )
    if baseline_kind == "llm-no-tools":
        return BaselineInvestigatorAdapter(LLMWithoutToolsBaseline(), kind="llm-no-tools")

    raise ValueError(
        f"Unknown baseline '{baseline_kind}'. Valid choices: {', '.join(_VALID_BASELINES)}."
    )


def run_fault_injection(
    fault_type: str,
    run_id: int,
    output_dir: Path,
    agent: AgentExecutor,
    max_wait_seconds: int = 120,
) -> dict[str, Any]:
    """Execute one fault injection test and return the result dict.

    Steps: inject fault -> wait -> call agent investigation -> save result -> restore.
    """
    record: dict[str, Any] = {
        "test_id": f"{fault_type}_run_{run_id}",
        "fault_type": fault_type,
        "run_id": run_id,
        "ground_truth": GROUND_TRUTH[fault_type],
        "fault_start_time": datetime.now(UTC).isoformat(),
        "status": "running",
    }

    script_path = _resolve_script(fault_type)

    # --- Inject the fault ---
    try:
        subprocess.run(
            ["bash", script_path, "inject"],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        record["status"] = "failed"
        record["error"] = str(e)
        record["fault_end_time"] = datetime.now(UTC).isoformat()
        _save_result(record, output_dir)
        return record

    record["fault_end_time"] = datetime.now(UTC).isoformat()

    # --- Wait for anomaly to manifest, then trigger investigation ---
    print(f"  Waiting up to {max_wait_seconds}s for anomaly detection...")
    alert_time: str | None = None
    deadline = time.time() + max_wait_seconds

    while time.time() < deadline:
        # Wait 120s for the fault to fully manifest in metrics before
        # triggering investigation. The rate() function's [1m] lookback
        # window means data persists for ~75s after a service stops.
        # At 120s, a crashed service's coverage drops to ~65% (well below
        # the 70% sparse threshold), producing a reliable CRITICAL signal.
        time.sleep(120)
        alert_time = datetime.now(UTC).isoformat()
        break

    if alert_time is None:
        record["status"] = "no_detection"
        record["is_correct"] = False
    else:
        record["alert_time"] = alert_time
        # currencyservice is deliberately EXCLUDED from affected_services.
        # The v1.10.0 image has a SIGSEGV crash-loop bug that throws
        # "std::logic_error: basic_string: construction from null is not valid"
        # and exits continuously in baseline. Including it in the sweep
        # causes the agent to fixate on currencyservice's probe_up=0 and
        # crash logs — both real but unrelated to the injected fault.
        # currencyservice is never a ground-truth target in the active
        # 7-fault suite (config_error was retargeted to productcatalogservice
        # in Session 12 for this exact reason).
        alert = {
            "title": "Anomaly Detected — Automated Investigation Triggered",
            "severity": "high",
            "timestamp": alert_time,
            "anomaly_score": 1.0,
            "affected_services": [
                "cartservice",
                "checkoutservice",
                "frontend",
                "paymentservice",
                "productcatalogservice",
                "redis",
            ],
        }

        # Live mode: agent queries Prometheus/Loki via tools.
        # Pin the metric window to [fault_start_time, fault_start_time+10min]
        # so this test's signal is isolated from previous tests' residue.
        report = agent.investigate(alert=alert, start_time=record["fault_start_time"])

        record["investigation_complete_time"] = datetime.now(UTC).isoformat()
        record["detection_latency_seconds"] = (
            datetime.fromisoformat(record["alert_time"])
            - datetime.fromisoformat(record["fault_start_time"])
        ).total_seconds()
        record["investigation_duration_seconds"] = (
            datetime.fromisoformat(record["investigation_complete_time"])
            - datetime.fromisoformat(record["alert_time"])
        ).total_seconds()
        record["predicted_root_cause"] = report.get("root_cause")
        record["top_3_predictions"] = report.get("top_3_predictions", [])
        record["confidence"] = report.get("confidence", 0.0)
        record["is_correct"] = record["predicted_root_cause"] == record["ground_truth"]
        record["status"] = "completed"

        # Save RCA report text
        rca_text = report.get("rca_report", "")
        if rca_text:
            reports_dir = output_dir / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            report_file = reports_dir / f"{fault_type}_run_{run_id}.md"
            report_file.write_text(rca_text)
            record["rca_report_file"] = str(report_file)

    _save_result(record, output_dir)

    # --- Restore the system ---
    try:
        subprocess.run(
            ["bash", script_path, "restore"],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print(f"  WARNING: Failed to restore after {fault_type} run {run_id}")

    return record


def _save_result(record: dict[str, Any], output_dir: Path) -> None:
    """Save a result dict as a JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / f"{record['test_id']}.json"
    with open(out_file, "w") as f:
        json.dump(record, f, indent=2)


def _shuffled_faults(faults: list[str], seed: int) -> list[str]:
    """Deterministically shuffle fault_type names using a given seed.

    Shuffling spreads destructive faults (service_crash, cascading_failure)
    across the run so that their residue (probe_up=0 for ~5min post-restart)
    doesn't always pollute the same downstream test. Per-fault runs remain
    sequential so per-fault cooldowns still apply correctly.
    """
    shuffled = list(faults)
    random.Random(seed).shuffle(shuffled)
    return shuffled


def _load_per_fault_cooldowns(
    config_path: str = "configs/evaluation_scenarios.yaml",
) -> dict[str, int]:
    """Load per-fault cooldown_seconds from evaluation_scenarios.yaml."""
    try:
        path = PROJECT_ROOT / config_path
        with open(path) as f:
            cfg = yaml.safe_load(f)
        fault_types = cfg.get("fault_types", [])
        return {s["name"]: s.get("cooldown_seconds", 300) for s in fault_types}
    except (FileNotFoundError, KeyError):
        return {}


def main() -> None:
    """Run the fault injection evaluation suite."""
    parser = argparse.ArgumentParser(description="Run OTel Demo fault injection suite")
    parser.add_argument("--fault", help="Single fault type to run (default: all)")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", default="data/evaluation/results/")
    parser.add_argument(
        "--cooldown",
        type=int,
        default=None,
        help="Global cooldown override (seconds). "
        "If not set, per-fault cooldown from evaluation_scenarios.yaml is used.",
    )
    parser.add_argument(
        "--max-wait",
        type=int,
        default=120,
        help="Max seconds to wait for anomaly detection per test (default: 120)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help=(
            "Random seed used to shuffle fault_type order when running all "
            "fault types (default: 42). Ignored when --fault is set."
        ),
    )
    parser.add_argument(
        "--baseline",
        choices=list(_VALID_BASELINES),
        default=None,
        help=(
            "Swap the OpsAgent investigator for an internal comparison "
            "baseline. When set, each test uses the named baseline in place "
            "of AgentExecutor. Default (unset) runs OpsAgent as in Session 13."
        ),
    )
    args = parser.parse_args()

    agent = _build_investigator(args.baseline)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.fault:
        if args.fault not in FAULT_SCRIPTS:
            known = ", ".join(sorted(FAULT_SCRIPTS.keys()))
            print(
                f"Error: '{args.fault}' is not a registered fault type. Known fault types: {known}",
                file=sys.stderr,
            )
            sys.exit(1)
        faults = [args.fault]
    else:
        faults = _shuffled_faults(list(FAULT_SCRIPTS.keys()), args.seed)
        print(f"Shuffled fault order (seed={args.seed}): {faults}")
    per_fault_cooldown = _load_per_fault_cooldowns()

    for fault_type in faults:
        for run_id in range(1, args.repetitions + 1):
            print(f"\n{'=' * 60}")
            print(f"Running: {fault_type} (Run {run_id}/{args.repetitions})")
            print(f"{'=' * 60}")

            result = run_fault_injection(
                fault_type,
                run_id,
                output_dir,
                agent=agent,
                max_wait_seconds=args.max_wait,
            )

            print(f"Status: {result['status']}")
            if result.get("is_correct") is not None:
                print(
                    f"Correct: {result['is_correct']}  "
                    f"(predicted={result.get('predicted_root_cause')}, "
                    f"truth={result.get('ground_truth')})"
                )

            # Cooldown before next test (skip after last test)
            is_last = run_id == args.repetitions and fault_type == faults[-1]
            if not is_last:
                cooldown = (
                    args.cooldown
                    if args.cooldown is not None
                    else per_fault_cooldown.get(fault_type, 300)
                )
                print(f"Cooldown: {cooldown}s...")
                time.sleep(cooldown)

    print("\nFault injection suite complete.")


if __name__ == "__main__":
    main()
