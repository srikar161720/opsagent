#!/usr/bin/env bash
# Fault Injection: Configuration Error
# Target: productcatalogservice
# Ground Truth: productcatalogservice
# Difficulty: Hard
# Method: Stop the compose-managed productcatalogservice, then start a replacement
#         container with an invalid port (PRODUCT_CATALOG_SERVICE_PORT=999999).
#         The Go service logs "fatal: invalid port" and exits immediately; with
#         --restart on-failure it crash-loops, producing probe_up oscillation
#         between 1 (during the brief startup window) and 0 (during failure).
#
# Note: The previous target (currencyservice) was swapped because v1.10.0
#       currencyservice SIGSEGVs in baseline, making this fault indistinguishable
#       from normal operation. productcatalogservice has a clean baseline.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/../docker-compose.demo.yml"
FAULT_CONTAINER="demo_app-productcatalogservice-fault"
NETWORK="opsagent_opsagent-net"
IMAGE="ghcr.io/open-telemetry/demo:1.10.0-productcatalogservice"
ACTION="${1:-}"

case "$ACTION" in
    inject)
        echo "[INJECT] Stopping compose-managed productcatalogservice..."
        docker compose -f "$COMPOSE_FILE" stop productcatalogservice

        echo "[INJECT] Starting productcatalogservice with invalid port config..."
        docker run -d \
            --name "$FAULT_CONTAINER" \
            --network "$NETWORK" \
            --network-alias productcatalogservice \
            --restart on-failure \
            -e PRODUCT_CATALOG_SERVICE_PORT=999999 \
            "$IMAGE"
        echo "[INJECT] Faulty productcatalogservice running. Will crash-loop due to invalid port."
        ;;
    restore)
        echo "[RESTORE] Stopping faulty productcatalogservice container..."
        docker stop "$FAULT_CONTAINER" 2>/dev/null || true
        docker rm "$FAULT_CONTAINER" 2>/dev/null || true

        echo "[RESTORE] Starting compose-managed productcatalogservice..."
        docker compose -f "$COMPOSE_FILE" start productcatalogservice
        echo "[RESTORE] Waiting 15s for productcatalogservice to stabilize..."
        sleep 15
        echo "[RESTORE] productcatalogservice restored to normal configuration."
        ;;
    *)
        echo "Usage: $0 {inject|restore}"
        exit 1
        ;;
esac
