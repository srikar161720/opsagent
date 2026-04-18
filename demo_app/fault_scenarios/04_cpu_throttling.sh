#!/usr/bin/env bash
# Fault Injection: CPU Throttling
# Target: productcatalogservice
# Ground Truth: productcatalogservice
# Difficulty: Medium
# Method: Limit CPU to 0.1 cores via docker update (severe throttling).
#
# NOT IN ACTIVE REGISTRY (Session 12). This script is retained for reference
# but is intentionally absent from FAULT_SCRIPTS in
# tests/evaluation/fault_injection_suite.py. Diagnosis showed
# productcatalogservice baseline CPU is ~0.09% of a core — a cap at 10%
# (or even 1%) is never reached, so the fault produces no detectable signal.
# To re-enable: (a) load-test productcatalogservice so its CPU demand
# actually exceeds the cap, or (b) migrate to a demo with higher baseline
# CPU usage, then re-add this entry to FAULT_SCRIPTS.
set -euo pipefail

CONTAINER="demo_app-productcatalogservice-1"
ACTION="${1:-}"

case "$ACTION" in
    inject)
        echo "[INJECT] Throttling productcatalogservice to 0.1 CPUs..."
        docker update --cpus 0.1 "$CONTAINER"
        echo "[INJECT] CPU throttled. productcatalogservice will respond slowly."
        ;;
    restore)
        echo "[RESTORE] Removing CPU throttle from productcatalogservice..."
        # docker update --cpus sets NanoCpus which can't be cleared via update.
        # Recreate the container from compose (which has no CPU limit) instead.
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        COMPOSE_FILE="$SCRIPT_DIR/../docker-compose.demo.yml"
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate productcatalogservice
        echo "[RESTORE] Waiting 15s for productcatalogservice to stabilize..."
        sleep 15
        echo "[RESTORE] productcatalogservice recreated without CPU limit."
        ;;
    *)
        echo "Usage: $0 {inject|restore}"
        exit 1
        ;;
esac
