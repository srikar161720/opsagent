#!/bin/bash
# scripts/start_infrastructure.sh
# Bring up the full OpsAgent infrastructure stack.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== OpsAgent Infrastructure Startup ==="

echo "[1/4] Starting monitoring stack (Prometheus, Grafana, Loki, Promtail, Kafka, Docker Stats Exporter)..."
docker compose up -d --build prometheus grafana loki promtail zookeeper kafka docker-stats-exporter

echo "[2/4] Waiting for monitoring stack to be healthy (30s)..."
sleep 30

echo "[3/4] Starting OpenTelemetry Demo services..."
docker compose -f demo_app/docker-compose.demo.yml up -d

echo "[4/4] Waiting for demo services to be ready (60s)..."
sleep 60

echo ""
echo "=== Stack is ready ==="
echo "  Demo Frontend : http://localhost:8080"
echo "  Grafana       : http://localhost:3000  (admin / admin)"
echo "  Prometheus    : http://localhost:9090"
echo "  Loki          : http://localhost:3100"
echo "  Kafka         : localhost:9092"
echo ""
echo "NOTE: OpsAgent API and Dashboard are NOT started by this script."
echo "Start them separately in two additional terminals:"
echo "  Terminal 1 — API:       poetry run uvicorn src.serving.api:app --reload --host 0.0.0.0 --port 8000"
echo "  Terminal 2 — Dashboard: poetry run streamlit run src/serving/dashboard.py --server.port 8501"
