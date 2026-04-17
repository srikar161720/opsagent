#!/usr/bin/env bash
# Fault Injection: Network Partition
# Target: paymentservice
# Ground Truth: paymentservice
# Difficulty: Medium
# Method: Pause the paymentservice container (SIGSTOP). This makes the service
#         completely unresponsive — functionally equivalent to a network partition
#         from the application's perspective. Unlike docker network disconnect,
#         pausing produces detectable stale metrics via the Docker Stats Exporter.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/../docker-compose.demo.yml"
ACTION="${1:-}"

case "$ACTION" in
    inject)
        echo "[INJECT] Pausing paymentservice (simulating network partition)..."
        docker compose -f "$COMPOSE_FILE" pause paymentservice
        echo "[INJECT] paymentservice paused. All inbound requests will fail."
        ;;
    restore)
        echo "[RESTORE] Unpausing paymentservice..."
        docker compose -f "$COMPOSE_FILE" unpause paymentservice
        echo "[RESTORE] Waiting 10s for paymentservice to re-establish connections..."
        sleep 10
        echo "[RESTORE] paymentservice resumed."
        ;;
    *)
        echo "Usage: $0 {inject|restore}"
        exit 1
        ;;
esac
