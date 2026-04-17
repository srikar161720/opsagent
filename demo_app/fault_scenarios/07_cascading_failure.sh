#!/usr/bin/env bash
# Fault Injection: Cascading Failure
# Target: cartservice (root cause)
# Ground Truth: cartservice
# Difficulty: Hard
# Method: Stop cartservice, then wait 30s for downstream propagation.
#         checkoutservice (depends on cartservice) and frontend (depends on cartservice)
#         will begin degrading — but the root cause is cartservice, not the downstream
#         services showing symptoms. This tests whether the agent can trace past symptoms
#         to the true root cause using causal discovery.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/../docker-compose.demo.yml"
ACTION="${1:-}"

case "$ACTION" in
    inject)
        echo "[INJECT] Stopping cartservice to trigger cascading failure..."
        docker compose -f "$COMPOSE_FILE" stop cartservice
        echo "[INJECT] Waiting 30s for downstream services to degrade..."
        sleep 30
        echo "[INJECT] Cascading failure propagated. checkoutservice and frontend should show degradation."
        ;;
    restore)
        echo "[RESTORE] Starting cartservice..."
        docker compose -f "$COMPOSE_FILE" start cartservice
        echo "[RESTORE] Waiting 30s for full downstream recovery..."
        sleep 30
        echo "[RESTORE] cartservice and downstream services restored."
        ;;
    *)
        echo "Usage: $0 {inject|restore}"
        exit 1
        ;;
esac
