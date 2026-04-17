#!/usr/bin/env bash
# Fault Injection: Memory Pressure
# Target: checkoutservice
# Ground Truth: checkoutservice
# Difficulty: Medium
# Method: Reduce memory limit from 256M to 25M via docker update.
#         checkoutservice working set is ~23MB, so a 25MB limit causes
#         real memory pressure and potential OOM kills.
set -euo pipefail

CONTAINER="demo_app-checkoutservice-1"
ACTION="${1:-}"

case "$ACTION" in
    inject)
        echo "[INJECT] Reducing checkoutservice memory limit to 25MB..."
        docker update --memory 25m --memory-swap 25m "$CONTAINER"
        echo "[INJECT] Memory limit reduced. checkoutservice will experience OOM pressure."
        ;;
    restore)
        echo "[RESTORE] Restoring checkoutservice memory limit to 256MB..."
        docker update --memory 256m --memory-swap 256m "$CONTAINER"
        echo "[RESTORE] Memory limit restored."
        ;;
    *)
        echo "Usage: $0 {inject|restore}"
        exit 1
        ;;
esac
