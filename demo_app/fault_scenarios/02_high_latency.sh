#!/usr/bin/env bash
# Fault Injection: High Latency
# Target: frontend
# Ground Truth: frontend
# Difficulty: Easy
# Method: Use a sidecar container sharing frontend's network namespace
#         to run tc netem (adds 500ms delay). Frontend is the primary traffic
#         target from the load generator, so the latency is directly visible
#         in span metrics and response times.
set -euo pipefail

CONTAINER="demo_app-frontend-1"
SIDECAR="opsagent-tc-sidecar"
ACTION="${1:-}"

case "$ACTION" in
    inject)
        echo "[INJECT] Adding 500ms latency to frontend..."
        # Run a lightweight Alpine sidecar that shares frontend's network namespace
        docker run -d --rm \
            --name "$SIDECAR" \
            --network "container:$CONTAINER" \
            --cap-add NET_ADMIN \
            alpine:3.19 \
            sh -c "apk add --no-cache iproute2 >/dev/null 2>&1 && tc qdisc add dev eth0 root netem delay 500ms && sleep infinity"
        echo "[INJECT] 500ms latency applied to frontend via sidecar."
        ;;
    restore)
        echo "[RESTORE] Removing latency from frontend..."
        docker exec "$SIDECAR" tc qdisc del dev eth0 root 2>/dev/null || true
        docker stop "$SIDECAR" 2>/dev/null || true
        echo "[RESTORE] Latency removed from frontend."
        ;;
    *)
        echo "Usage: $0 {inject|restore}"
        exit 1
        ;;
esac
