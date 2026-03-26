#!/bin/bash
# scripts/stop_infrastructure.sh
# Tear down the full OpsAgent infrastructure stack.
#
# NOTE: OpsAgent API (uvicorn) and Dashboard (streamlit) are NOT managed by this script.
# Stop them manually with Ctrl+C in the terminals where they are running before calling this.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== OpsAgent Infrastructure Shutdown ==="

echo "[1/2] Stopping OpenTelemetry Demo services..."
docker compose -f demo_app/docker-compose.demo.yml down

echo "[2/2] Stopping monitoring stack (Prometheus, Grafana, Loki, Kafka)..."
docker compose down

echo ""
echo "=== Infrastructure stopped ==="
echo "Reminder: If OpsAgent API or Dashboard are still running, stop them with Ctrl+C."
