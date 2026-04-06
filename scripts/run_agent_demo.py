"""Run an interactive OpsAgent investigation demo.

Usage:
    poetry run python scripts/run_agent_demo.py
    poetry run python scripts/run_agent_demo.py --service cartservice
    poetry run python scripts/run_agent_demo.py --offline

Modes:
    Live (default):  Requires Docker stack running + GEMINI_API_KEY set.
                     Agent queries Prometheus and Loki in real time.
    Offline:         Requires only GEMINI_API_KEY.
                     Uses synthetic data — no Docker needed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv()


def check_prerequisites(offline: bool) -> bool:
    """Verify required services and environment."""
    ok = True

    # Check API key
    if not os.environ.get("GEMINI_API_KEY"):
        print("[FAIL] GEMINI_API_KEY not set in environment or .env file")
        print("       Get a key at: https://aistudio.google.com/apikey")
        print("       Then add to .env: GEMINI_API_KEY=your_key_here")
        ok = False
    else:
        print("[  OK] GEMINI_API_KEY is set")

    if not offline:
        # Check Prometheus
        try:
            import requests

            resp = requests.get("http://localhost:9090/-/healthy", timeout=3)
            if resp.status_code == 200:
                print("[  OK] Prometheus is running (http://localhost:9090)")
            else:
                print("[FAIL] Prometheus returned non-200 status")
                ok = False
        except Exception:
            print("[FAIL] Prometheus not reachable at http://localhost:9090")
            print("       Run: make infra-up")
            ok = False

        # Check Loki
        try:
            resp = requests.get("http://localhost:3100/ready", timeout=3)
            if resp.status_code == 200:
                print("[  OK] Loki is running (http://localhost:3100)")
            else:
                print("[WARN] Loki returned non-200 (logs may not be available)")
        except Exception:
            print("[WARN] Loki not reachable (log search will return empty results)")

    return ok


def run_demo(service: str, offline: bool) -> None:
    """Run the agent investigation demo."""
    from src.agent.executor import AgentExecutor

    # Build alert
    affected = [service]
    if service == "cartservice":
        affected.append("checkoutservice")
    elif service == "checkoutservice":
        affected.append("frontend")

    alert = {
        "title": f"LSTM-AE Anomaly Detected — Elevated metrics in {service}",
        "severity": "high",
        "timestamp": datetime.now(UTC).isoformat(),
        "affected_services": affected,
        "anomaly_score": 0.45,
        "threshold": 0.253,
    }

    print("\n" + "=" * 65)
    print("  OpsAgent — Root Cause Analysis Investigation")
    print("=" * 65)
    print(f"\n  Alert:     {alert['title']}")
    print(f"  Severity:  {alert['severity']}")
    print(f"  Services:  {', '.join(alert['affected_services'])}")
    print(f"  Score:     {alert['anomaly_score']} (threshold: {alert['threshold']})")
    print(f"  Mode:      {'Offline (synthetic)' if offline else 'Live (Prometheus + Loki)'}")
    print(f"\n{'─' * 65}")
    print("  Starting investigation... (this may take 30-90 seconds)\n")

    config = {
        "agent": {
            "investigation": {
                "max_tool_calls": 8,
                "confidence_threshold": 0.7,
                "timeout_seconds": 120,
            },
        }
    }

    executor = AgentExecutor(config)
    start_time = time.time()

    result = executor.investigate(alert=alert)

    elapsed = time.time() - start_time

    # Display results
    print(f"{'─' * 65}")
    print(f"  Investigation completed in {elapsed:.1f}s")
    print(f"{'─' * 65}\n")

    print(f"  Root Cause:   {result['root_cause']}")
    print(f"  Confidence:   {result['root_cause_confidence']:.0%}")
    print(f"  Top 3:        {result['top_3_predictions']}")

    if result.get("recommended_actions"):
        print("\n  Recommended Actions:")
        for i, action in enumerate(result["recommended_actions"], 1):
            print(f"    {i}. {action}")

    print(f"\n{'=' * 65}")
    print("  FULL RCA REPORT")
    print(f"{'=' * 65}\n")

    if result.get("rca_report"):
        print(result["rca_report"])
    else:
        print("  (No report generated)")

    print(f"\n{'=' * 65}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an OpsAgent investigation demo.")
    parser.add_argument(
        "--service",
        default="cartservice",
        help="Primary affected service (default: cartservice)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run without Docker stack (synthetic data only)",
    )
    args = parser.parse_args()

    print("\nOpsAgent Demo — Prerequisite Check")
    print("─" * 40)

    if not check_prerequisites(args.offline):
        print("\nFix the issues above and try again.")
        sys.exit(1)

    print("\nAll prerequisites met!\n")
    run_demo(args.service, args.offline)


if __name__ == "__main__":
    main()
