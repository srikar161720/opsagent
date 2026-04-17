#!/usr/bin/env bash
# Fault Injection: Connection Exhaustion
# Target: redis
# Ground Truth: redis
# Difficulty: Medium
# Method: Pause the redis container (SIGSTOP). This freezes the process,
#         causing all container metrics to go stale and all connections from
#         cartservice to fail. More detectable than maxclients limit because
#         Docker Stats Exporter sees stale stats (CPU stops incrementing).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/../docker-compose.demo.yml"
ACTION="${1:-}"

case "$ACTION" in
    inject)
        echo "[INJECT] Pausing redis (freezing process)..."
        docker compose -f "$COMPOSE_FILE" pause redis
        echo "[INJECT] redis paused. All connections will fail, metrics will go stale."
        ;;
    restore)
        echo "[RESTORE] Unpausing redis..."
        docker compose -f "$COMPOSE_FILE" unpause redis
        echo "[RESTORE] redis resumed. Connections should recover."
        ;;
    *)
        echo "Usage: $0 {inject|restore}"
        exit 1
        ;;
esac
