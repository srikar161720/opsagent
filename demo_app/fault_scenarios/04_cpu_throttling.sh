#!/usr/bin/env bash
# Fault Injection: CPU Throttling
# Target: productcatalogservice
# Ground Truth: productcatalogservice
# Difficulty: Medium
# Method: Limit CPU to 0.1 cores via docker update (severe throttling).
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
