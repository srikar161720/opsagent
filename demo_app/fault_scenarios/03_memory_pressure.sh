#!/usr/bin/env bash
# Fault Injection: Memory Pressure
# Target: checkoutservice
# Ground Truth: checkoutservice
# Difficulty: Medium
#
# Method: Cap memory at max(working_mb * 1.2, working_mb + 2). Dynamic scaling
# makes the cap deterministic across runs — a fixed 25 MiB cap previously
# missed saturation when the Go runtime had GC'd aggressively after idle
# (working_set 15 MiB << 25 MiB cap yielded only 60% utilization, below the
# memory_utilization CRITICAL detector's 80% threshold). The 1.2x multiplier
# gives ~83% immediate utilization for medium/large heaps (W >= 10 MiB). For
# cold-start heaps (W < 10 MiB) the 1.2x cap would leave only ~1 MiB
# headroom and risk GC-allocation OOM, so the floor "W + 2 MiB" takes over —
# util starts at W / (W + 2) >= 80% with a guaranteed 2 MiB of GC headroom.
# Either branch leaves enough headroom to avoid instant OOMKill (which would
# convert this fault into an indistinguishable service_crash).
#
# Restore always returns memory to 256 MiB — the docker-compose default.
set -euo pipefail

CONTAINER="demo_app-checkoutservice-1"
ACTION="${1:-}"

# Fallback fixed cap if working-set measurement fails (exporter unreachable,
# docker stats malformed, etc.). Matches the historical cap used before the
# dynamic formula was introduced.
FALLBACK_CAP_MB=25

# Parse "15.23MiB / 256MiB" (or KiB/GiB variants) from the Docker API and
# echo the MiB value as an integer. Echoes nothing on failure.
get_working_mb() {
    local container="$1"
    local raw num_unit num unit
    raw=$(docker stats --no-stream --format '{{.MemUsage}}' "$container" 2>/dev/null) || return 1
    num_unit=$(echo "$raw" | awk -F'/' '{gsub(/^[ \t]+|[ \t]+$/, "", $1); print $1}')
    num=$(echo "$num_unit" | sed 's/[A-Za-z]*$//')
    unit=$(echo "$num_unit" | sed 's/[0-9.]*//')
    case "$unit" in
        KiB) awk -v n="$num" 'BEGIN{printf "%d", n/1024}' ;;
        MiB) awk -v n="$num" 'BEGIN{printf "%d", n}' ;;
        GiB) awk -v n="$num" 'BEGIN{printf "%d", n*1024}' ;;
        *)   return 1 ;;
    esac
}

case "$ACTION" in
    inject)
        WORKING_MB=$(get_working_mb "$CONTAINER" || true)
        if [[ -n "$WORKING_MB" && "$WORKING_MB" =~ ^[0-9]+$ && "$WORKING_MB" -gt 0 ]]; then
            # cap = max(working_mb * 1.2, working_mb + 2). Integer arithmetic:
            # 1.2x via *12/10. The +2 MiB headroom floor kicks in for cold
            # heaps (< 10 MiB) where the multiplier alone would leave <2 MiB
            # for GC-allocation.
            CAP_MUL=$(( WORKING_MB * 12 / 10 ))
            CAP_ADD=$(( WORKING_MB + 2 ))
            if (( CAP_MUL > CAP_ADD )); then
                CAP_MB=$CAP_MUL
            else
                CAP_MB=$CAP_ADD
            fi
            echo "[INJECT] checkoutservice working_set=${WORKING_MB}MiB -> cap=${CAP_MB}MiB (max of 1.2x, +2)"
        else
            CAP_MB=$FALLBACK_CAP_MB
            echo "[INJECT] Could not parse docker stats MemUsage; using fallback cap=${CAP_MB}MiB"
        fi
        docker update --memory "${CAP_MB}m" --memory-swap "${CAP_MB}m" "$CONTAINER"
        echo "[INJECT] Memory limit set to ${CAP_MB}MiB. checkoutservice will experience memory pressure."
        ;;
    restore)
        echo "[RESTORE] Restoring checkoutservice memory limit to 256MiB..."
        docker update --memory 256m --memory-swap 256m "$CONTAINER"
        echo "[RESTORE] Memory limit restored."
        ;;
    *)
        echo "Usage: $0 {inject|restore}"
        exit 1
        ;;
esac
