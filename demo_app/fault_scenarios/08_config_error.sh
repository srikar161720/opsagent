#!/usr/bin/env bash
# Fault Injection: Configuration Error
# Target: currencyservice
# Ground Truth: currencyservice
# Difficulty: Hard
# Method: Stop the compose-managed currencyservice, then start a replacement
#         container with an invalid port (CURRENCY_SERVICE_PORT=1). Binding to
#         port 1 requires root privileges, so the gRPC server fails to start
#         and the container crash-loops, producing stale/no metrics.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/../docker-compose.demo.yml"
FAULT_CONTAINER="demo_app-currencyservice-fault"
NETWORK="opsagent_opsagent-net"
IMAGE="ghcr.io/open-telemetry/demo:1.10.0-currencyservice"
ACTION="${1:-}"

case "$ACTION" in
    inject)
        echo "[INJECT] Stopping compose-managed currencyservice..."
        docker compose -f "$COMPOSE_FILE" stop currencyservice

        echo "[INJECT] Starting currencyservice with invalid port config..."
        docker run -d \
            --name "$FAULT_CONTAINER" \
            --network "$NETWORK" \
            --network-alias currencyservice \
            -e CURRENCY_SERVICE_PORT=1 \
            "$IMAGE"
        echo "[INJECT] Faulty currencyservice running. Will crash-loop due to port bind failure."
        ;;
    restore)
        echo "[RESTORE] Stopping faulty currencyservice container..."
        docker stop "$FAULT_CONTAINER" 2>/dev/null || true
        docker rm "$FAULT_CONTAINER" 2>/dev/null || true

        echo "[RESTORE] Starting compose-managed currencyservice..."
        docker compose -f "$COMPOSE_FILE" start currencyservice
        echo "[RESTORE] Waiting 15s for currencyservice to stabilize..."
        sleep 15
        echo "[RESTORE] currencyservice restored to normal configuration."
        ;;
    *)
        echo "Usage: $0 {inject|restore}"
        exit 1
        ;;
esac
