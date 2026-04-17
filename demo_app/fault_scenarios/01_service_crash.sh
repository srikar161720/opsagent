#!/usr/bin/env bash
# Fault Injection: Service Crash
# Target: cartservice
# Ground Truth: cartservice
# Difficulty: Easy
# Method: Stop the cartservice container; downstream services (checkout, frontend) degrade.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/../docker-compose.demo.yml"
ACTION="${1:-}"

case "$ACTION" in
    inject)
        echo "[INJECT] Stopping cartservice..."
        docker compose -f "$COMPOSE_FILE" stop cartservice
        echo "[INJECT] cartservice stopped. Downstream services will begin to degrade."
        ;;
    restore)
        echo "[RESTORE] Starting cartservice..."
        docker compose -f "$COMPOSE_FILE" start cartservice
        echo "[RESTORE] Waiting 15s for cartservice to stabilize..."
        sleep 15
        echo "[RESTORE] cartservice restored."
        ;;
    *)
        echo "Usage: $0 {inject|restore}"
        exit 1
        ;;
esac
