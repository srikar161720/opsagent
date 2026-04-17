"""Fault injection coordinator — user-facing entry point.

Runs preflight checks, delegates to fault_injection_suite, and prints a summary.

Usage:
    poetry run python scripts/inject_faults.py                          # All faults, 5 runs
    poetry run python scripts/inject_faults.py --fault service_crash    # Single fault type
    poetry run python scripts/inject_faults.py --skip-preflight         # Skip checks
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))

from tests.evaluation.fault_injection_suite import FAULT_SCRIPTS  # noqa: E402
from tests.evaluation.fault_injection_suite import main as suite_main  # noqa: E402
from tests.evaluation.metrics_calculator import (  # noqa: E402
    calculate_metrics,
    load_results,
)


def preflight_checks() -> list[str]:
    """Verify prerequisites before running fault injection tests.

    Returns a list of error messages. Empty list means all checks passed.
    """
    load_dotenv(PROJECT_ROOT / ".env")
    errors: list[str] = []

    # 1. Docker daemon running
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        errors.append("Docker daemon is not running. Start Docker Desktop first.")

    # 2. GEMINI_API_KEY set
    if not os.environ.get("GEMINI_API_KEY"):
        errors.append("GEMINI_API_KEY not set. Copy .env.example to .env and add your key.")

    # 3. Prometheus reachable
    try:
        resp = requests.get("http://localhost:9090/-/ready", timeout=5)
        if resp.status_code != 200:
            errors.append("Prometheus not ready (non-200 response).")
    except requests.ConnectionError:
        errors.append("Prometheus not reachable at localhost:9090. Run 'make infra-up'.")

    # 4. Loki reachable
    try:
        resp = requests.get("http://localhost:3100/ready", timeout=5)
        if resp.status_code != 200:
            errors.append("Loki not ready (non-200 response).")
    except requests.ConnectionError:
        errors.append("Loki not reachable at localhost:3100. Run 'make infra-up'.")

    # 5. Demo services running
    demo_compose = PROJECT_ROOT / "demo_app" / "docker-compose.demo.yml"
    if demo_compose.exists():
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(demo_compose), "ps", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0 or not result.stdout.strip():
                errors.append("OTel Demo services not running. Run 'make demo-up'.")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            errors.append("Could not check OTel Demo status.")

    # 6. Fault scripts exist
    for _name, path in FAULT_SCRIPTS.items():
        full_path = PROJECT_ROOT / path
        if not full_path.exists():
            errors.append(f"Missing fault script: {path}")

    return errors


def print_summary(results_dir: str) -> None:
    """Load results and print a quick metrics summary."""
    results = load_results(results_dir)
    if not results:
        print("\nNo results found.")
        return

    metrics = calculate_metrics(results)
    completed = [r for r in results if r.get("status") == "completed"]
    failed = [r for r in results if r.get("status") == "failed"]

    print(f"\n{'=' * 60}")
    print("EVALUATION SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total tests:        {len(results)}")
    print(f"Completed:          {len(completed)}")
    print(f"Failed:             {len(failed)}")
    print(f"Recall@1:           {metrics.recall_at_1:.1%}")
    print(f"Recall@3:           {metrics.recall_at_3:.1%}")
    print(f"Avg Latency:        {metrics.avg_detection_latency:.1f}s")
    print(f"Avg MTTR Proxy:     {metrics.avg_mttr_proxy:.1f}s")

    if metrics.ci_recall_at_1:
        lo, hi = metrics.ci_recall_at_1
        print(f"Recall@1 95% CI:    [{lo:.1%}, {hi:.1%}]")

    if metrics.recall_by_fault:
        print(f"\n{'Fault Type':<25} {'Recall@1':>10}")
        print("-" * 37)
        for fault, r1 in sorted(metrics.recall_by_fault.items()):
            marker = " *" if r1 < 0.8 else ""
            print(f"{fault:<25} {r1:>9.1%}{marker}")


def main() -> None:
    """Entry point for fault injection evaluation."""
    parser = argparse.ArgumentParser(description="OpsAgent Fault Injection Evaluation")
    parser.add_argument("--fault", help="Single fault type to run (default: all)")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", default="data/evaluation/results/")
    parser.add_argument("--cooldown", type=int, default=None)
    parser.add_argument("--max-wait", type=int, default=120)
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip preflight checks",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print summary of existing results without running tests",
    )
    args = parser.parse_args()

    if args.summary_only:
        print_summary(args.output)
        return

    # Preflight checks
    if not args.skip_preflight:
        print("Running preflight checks...")
        errors = preflight_checks()
        if errors:
            print("\nPreflight checks FAILED:")
            for err in errors:
                print(f"  - {err}")
            sys.exit(1)
        print("All preflight checks passed.\n")

    # Build sys.argv for the suite's argparse
    suite_args = ["fault_injection_suite"]
    if args.fault:
        suite_args.extend(["--fault", args.fault])
    suite_args.extend(["--repetitions", str(args.repetitions)])
    suite_args.extend(["--output", args.output])
    if args.cooldown is not None:
        suite_args.extend(["--cooldown", str(args.cooldown)])
    suite_args.extend(["--max-wait", str(args.max_wait)])

    original_argv = sys.argv
    sys.argv = suite_args
    try:
        suite_main()
    finally:
        sys.argv = original_argv

    # Print summary
    print_summary(args.output)


if __name__ == "__main__":
    main()
