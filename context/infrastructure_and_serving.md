# Infrastructure & Serving Specifications

**Implementation files:**
- `docker-compose.yml` — Main monitoring + message queue stack + Docker Stats Exporter + Promtail + OTel Collector + Service Probe Exporter
- `demo_app/docker-compose.demo.yml` — OTel Demo microservices (reduced, v1.10.0 images, with OTEL_EXPORTER_OTLP_ENDPOINT env vars and dummy SHIPPING/EMAIL env vars for checkoutservice)
- `infrastructure/prometheus/prometheus.yml` — Prometheus scrape config (scrapes docker-stats-exporter, otel-collector, service-probe-exporter)
- `infrastructure/otel-collector/otel-collector-config.yaml` — OTel Collector config with `spanmetrics` connector (traces → metrics) and Prometheus exporter on port 9464
- `infrastructure/service_probe_exporter/probe_exporter.py` — Custom Python exporter that probes each service via application-level data exchange (Redis PING, HTTP GET, gRPC payload), exposes `service_probe_up` and `service_probe_duration_seconds` on port 9102
- `infrastructure/promtail/promtail-config.yml` — Promtail Docker SD config (ships container logs to Loki via Docker socket)
- `infrastructure/loki/loki-config.yml` — Loki storage config
- `infrastructure/grafana/provisioning/datasources/datasources.yml` — Grafana datasources
- `infrastructure/grafana/dashboards/service_overview.json` — Main dashboard
- `demo_app/fault_scenarios/0{1-8}_*.sh` — Fault injection scripts (7 active + 1 retained out-of-scope: `04_cpu_throttling.sh` was removed from `FAULT_SCRIPTS` in Session 12 — undetectable on idle demo)
- `src/serving/api.py` — FastAPI application
- `src/serving/dashboard.py` — Streamlit dashboard
- `Dockerfile` — OpsAgent container image
- `scripts/start_infrastructure.sh` — One-command startup script
- `.env.example` — Environment variable template

**Port map summary:**
| Service | Port | URL |
|---|---|---|
| OTel Demo Frontend | 8080 | http://localhost:8080 |
| Grafana | 3000 | http://localhost:3000 (admin/admin) |
| Prometheus | 9090 | http://localhost:9090 |
| Loki | 3100 | http://localhost:3100 |
| Kafka | 9092 | localhost:9092 |
| Docker Stats Exporter | 9101 | http://localhost:9101 |
| OTel Collector (OTLP gRPC) | 4317 | grpc://localhost:4317 |
| OTel Collector (OTLP HTTP) | 4318 | http://localhost:4318 |
| OTel Collector (Prometheus exporter) | 9464 | http://localhost:9464 |
| Service Probe Exporter | 9102 | http://localhost:9102 |
| OpsAgent API | 8000 | http://localhost:8000 |
| Streamlit Dashboard | 8501 | http://localhost:8501 |

---

## 1. Main Docker Compose Stack

Create `docker-compose.yml` in the project root. This file manages the monitoring stack and message queue. The OTel Demo services live in a separate compose file to keep them cleanly separated.

```yaml
# docker-compose.yml

services:

  # ── Metrics ─────────────────────────────────────────────────────────
  prometheus:
    image: prom/prometheus:v2.47.0
    ports:
      - "9090:9090"
    volumes:
      - ./infrastructure/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./infrastructure/prometheus/alert_rules.yml:/etc/prometheus/alert_rules.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--storage.tsdb.retention.time=7d'
      - '--web.enable-lifecycle'          # Allows config reload via API
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '0.5'

  # ── Visualization ────────────────────────────────────────────────────
  grafana:
    image: grafana/grafana:10.2.0
    ports:
      - "3000:3000"
    volumes:
      - ./infrastructure/grafana/provisioning:/etc/grafana/provisioning
      - ./infrastructure/grafana/dashboards:/var/lib/grafana/dashboards
      - grafana_data:/var/lib/grafana
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_USERS_ALLOW_SIGN_UP=false
      - GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/service_overview.json
    depends_on:
      - prometheus
      - loki
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 256M
          cpus: '0.25'

  # ── Log Aggregation ──────────────────────────────────────────────────
  loki:
    image: grafana/loki:2.9.0
    ports:
      - "3100:3100"
    volumes:
      - ./infrastructure/loki/loki-config.yml:/etc/loki/local-config.yaml
      - loki_data:/loki
    command: -config.file=/etc/loki/local-config.yaml
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '0.5'

  # ── Message Queue ────────────────────────────────────────────────────
  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '0.5'

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_LOG_RETENTION_HOURS: 24
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"
      KAFKA_NUM_PARTITIONS: 3
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 1536M      # Kafka needs significant heap
          cpus: '1.0'

  # ── Container Metrics ────────────────────────────────────────────────
  docker-stats-exporter:
    build: ./infrastructure/docker_stats_exporter
    ports:
      - "9101:9101"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - opsagent-net
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 128M
          cpus: '0.25'

networks:
  opsagent-net:
    driver: bridge

volumes:
  prometheus_data:
  grafana_data:
  loki_data:
```

> **Note:** All services use a shared `opsagent-net` bridge network. The OTel Demo compose file references this as an external network (`opsagent_opsagent-net`).

> **macOS note:** cAdvisor was originally used for container metrics but cannot discover individual containers on macOS Docker Desktop (cgroupv2 + VM-based Docker). It was replaced with a custom Docker Stats Exporter that queries the Docker API directly via the Python `docker` SDK. The exporter uses a background collection thread to avoid Prometheus scrape timeouts.

> **Exposed gauges (port 9101):** `container_cpu_usage_seconds_total` (counter), `container_memory_usage_bytes` (gauge), `container_memory_working_set_bytes` (gauge), **`container_spec_memory_limit_bytes` (gauge — cgroup memory limit, added Session 13)**, `container_network_receive_bytes_total` / `_transmit_bytes_total` / `_receive_errors_total` / `_transmit_errors_total` (counters), `container_fs_usage_bytes` (counter). All gauges share the `{service, name}` label set so Prometheus can join derived ratios like `memory_working_set / memory_limit` natively without `on()` / `ignoring()`. The `limit` gauge is read from `stats["memory_stats"]["limit"]` and tracks `docker update --memory` in real time; on macOS Docker Desktop an uncapped container reports `limit == host RAM` (~16 GB), which is correct behaviour — `memory_utilization` ratio stays <1% for those containers.

---

## 2. OTel Demo Compose (Reduced Services)

Six core services from the OpenTelemetry Astronomy Shop are included; four are excluded to stay within the 16 GB RAM budget.

**Included (✅):** `frontend`, `cartservice`, `checkoutservice`, `paymentservice`, `productcatalogservice`, `currencyservice`, `redis`, `loadgenerator`

**Excluded (❌):** `adservice`, `recommendationservice`, `emailservice`, `shippingservice` — not essential for RCA demonstration.

```yaml
# demo_app/docker-compose.demo.yml

services:

  frontend:
    image: ghcr.io/open-telemetry/demo:latest-frontend
    ports:
      - "8080:8080"
    environment:
      - FRONTEND_PORT=8080
      - PRODUCT_CATALOG_SERVICE_ADDR=productcatalogservice:3550
      - CURRENCY_SERVICE_ADDR=currencyservice:7001
      - CART_SERVICE_ADDR=cartservice:7070
      - CHECKOUT_SERVICE_ADDR=checkoutservice:5050
    depends_on:
      - productcatalogservice
      - cartservice
      - checkoutservice
      - currencyservice
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M

  cartservice:
    image: ghcr.io/open-telemetry/demo:latest-cartservice
    environment:
      - CART_SERVICE_PORT=7070
      - REDIS_ADDR=redis:6379
    depends_on:
      - redis
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 256M

  checkoutservice:
    image: ghcr.io/open-telemetry/demo:latest-checkoutservice
    environment:
      - CHECKOUT_SERVICE_PORT=5050
      - CART_SERVICE_ADDR=cartservice:7070
      - CURRENCY_SERVICE_ADDR=currencyservice:7001
      - PAYMENT_SERVICE_ADDR=paymentservice:50051
      - PRODUCT_CATALOG_SERVICE_ADDR=productcatalogservice:3550
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 256M

  paymentservice:
    image: ghcr.io/open-telemetry/demo:latest-paymentservice
    environment:
      - PAYMENT_SERVICE_PORT=50051
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 256M

  productcatalogservice:
    image: ghcr.io/open-telemetry/demo:latest-productcatalogservice
    environment:
      - PRODUCT_CATALOG_SERVICE_PORT=3550
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 256M

  currencyservice:
    image: ghcr.io/open-telemetry/demo:latest-currencyservice
    environment:
      - CURRENCY_SERVICE_PORT=7001
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 256M

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 128M

  loadgenerator:
    image: ghcr.io/open-telemetry/demo:latest-loadgenerator
    environment:
      - FRONTEND_ADDR=frontend:8080
      - USERS=5              # Low load; 5 concurrent simulated users
    depends_on:
      - frontend
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 256M
```

---

## 3. Resource Allocation Summary

| Service | Memory Limit | CPU Limit | Notes |
|---|---|---|---|
| Kafka | 1.5 GB | 1.0 | Needs heap space for log retention |
| Zookeeper | 512 MB | 0.5 | Kafka coordination |
| Prometheus | 512 MB | 0.5 | 7-day metric retention |
| Loki | 512 MB | 0.5 | Log storage + query |
| Grafana | 256 MB | 0.25 | Dashboard rendering |
| Docker Stats Exporter | 128 MB | 0.25 | Container metrics exporter (queries Docker API) |
| OTel Demo (6 services) | ~1.5 GB | ~1.5 | ~256 MB each |
| Redis | 128 MB | — | Small in-memory store |
| Load Generator | 256 MB | — | Synthetic traffic |
| OpsAgent (API + Dashboard) | 1 GB | 1.0 | Agent + ML model in memory |
| **Total** | **~7.25 GB** | **~5.25 cores** | Leaves ~8.75 GB for OS/IDE on 16 GB Mac |

> **Note:** RCAEval and LogHub HDFS preprocessing are run **offline** (not as Docker services). HDFS pretraining is run on Google Colab Pro. Neither adds to the Docker memory budget above.

---

## 4. Infrastructure Configuration Files

### 4.1 Prometheus — `infrastructure/prometheus/prometheus.yml`

> **Architecture decision:** OTel Demo services use gRPC and don't expose native Prometheus `/metrics` endpoints. Instead of adding an OTel Collector, we use a custom Docker Stats Exporter that queries the Docker API directly and exposes container-level metrics (CPU, memory, network) in Prometheus format. This replaces cAdvisor, which cannot discover containers on macOS Docker Desktop.

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  scrape_timeout: 10s

rule_files:
  - "alert_rules.yml"

scrape_configs:
  # Container-level metrics via Docker Stats Exporter
  # (replaces cAdvisor which cannot discover containers on macOS Docker Desktop)
  - job_name: 'docker-stats-exporter'
    static_configs:
      - targets: ['docker-stats-exporter:9101']
    scrape_interval: 15s

  # Prometheus self-monitoring
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
```

**`infrastructure/prometheus/alert_rules.yml`** (optional — for the Watchdog trigger)
```yaml
groups:
  - name: opsagent_alerts
    rules:
      - alert: HighLatency
        expr: http_request_duration_seconds{quantile="0.99"} > 0.5
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "High P99 latency on {{ $labels.service }}"

      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[1m]) / rate(http_requests_total[1m]) > 0.05
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "High error rate on {{ $labels.service }}"
```

### 4.2 Loki — `infrastructure/loki/loki-config.yml`

```yaml
auth_enabled: false

server:
  http_listen_port: 3100
  grpc_listen_port: 9096

ingester:
  lifecycler:
    ring:
      kvstore:
        store: inmemory
      replication_factor: 1
    final_sleep: 0s
  chunk_idle_period: 1h
  max_chunk_age: 1h

schema_config:
  configs:
    - from: 2020-10-24
      store: boltdb-shipper
      object_store: filesystem
      schema: v11
      index:
        prefix: index_
        period: 24h

storage_config:
  boltdb_shipper:
    active_index_directory: /loki/index
    cache_location: /loki/cache
    shared_store: filesystem
  filesystem:
    directory: /loki/chunks

compactor:
  working_directory: /loki/compactor
  shared_store: filesystem

limits_config:
  enforce_metric_name: false
  reject_old_samples: true
  reject_old_samples_max_age: 168h    # 7 days
  ingestion_rate_mb: 16
  ingestion_burst_size_mb: 32

chunk_store_config:
  max_look_back_period: 0s

table_manager:
  retention_deletes_enabled: false
  retention_period: 0s
```

### 4.3 Grafana — `infrastructure/grafana/provisioning/datasources/datasources.yml`

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
    jsonData:
      timeInterval: "15s"

  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100
    editable: false
    jsonData:
      maxLines: 1000
```

**`infrastructure/grafana/provisioning/dashboards/dashboards.yml`:**
```yaml
apiVersion: 1

providers:
  - name: 'OpsAgent Dashboards'
    orgId: 1
    folder: 'OpsAgent'
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /var/lib/grafana/dashboards
```

---

## 5. Startup Script

`scripts/start_infrastructure.sh` — bring up the full stack in one command.

```bash
#!/bin/bash
# scripts/start_infrastructure.sh
# Bring up the full OpsAgent infrastructure stack.

set -e

echo "=== OpsAgent Infrastructure Startup ==="

echo "[1/4] Starting monitoring stack (Prometheus, Grafana, Loki, Kafka, Docker Stats Exporter)..."
docker compose up -d --build prometheus grafana loki zookeeper kafka docker-stats-exporter

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
```

`scripts/stop_infrastructure.sh` — tear down the full stack in one command.

```bash
#!/bin/bash
# scripts/stop_infrastructure.sh
# Tear down the full OpsAgent infrastructure stack.
#
# NOTE: OpsAgent API (uvicorn) and Dashboard (streamlit) are NOT managed by this script.
# Stop them manually with Ctrl+C in the terminals where they are running before calling this.

set -e

echo "=== OpsAgent Infrastructure Shutdown ==="

echo "[1/2] Stopping OpenTelemetry Demo services..."
docker compose -f demo_app/docker-compose.demo.yml down

echo "[2/2] Stopping monitoring stack (Prometheus, Grafana, Loki, Kafka)..."
docker compose down

echo ""
echo "=== Infrastructure stopped ==="
echo "Reminder: If OpsAgent API or Dashboard are still running, stop them with Ctrl+C."
```

---

## 6. Fault Injection Suite

Eight fault types covering easy → hard difficulty. Each script follows the same structure: log start time, inject fault, wait, restore, log end time. Ground truth for evaluation is hardcoded per fault type.

**Ground truth mapping:**
```python
GROUND_TRUTH = {
    "service_crash":         "cartservice",
    "high_latency":          "paymentservice",
    "memory_pressure":       "checkoutservice",
    "cpu_throttling":        "productcatalogservice",
    "connection_exhaustion": "redis",
    "network_partition":     "paymentservice",
    "cascading_failure":     "cartservice",
    "config_error":          "currencyservice",
}
```

### Fault 1: Service Crash (Easy) — `01_service_crash.sh`

| Attribute | Value |
|---|---|
| Target | `cartservice` |
| Method | `docker stop` |
| Symptoms | Connection refused, downstream 503s |
| Detection time | < 30 seconds |
| Difficulty | Easy — binary state change |

```bash
#!/bin/bash
SERVICE="cartservice"
DURATION_SECONDS=120

echo "$(date -Iseconds) - FAULT_START: Service Crash on $SERVICE" | tee -a /tmp/fault_log.txt
docker stop $SERVICE
sleep $DURATION_SECONDS
docker start $SERVICE
echo "$(date -Iseconds) - FAULT_END: $SERVICE restarted" | tee -a /tmp/fault_log.txt
```

### Fault 2: High Latency Injection (Easy) — `02_high_latency.sh`

| Attribute | Value |
|---|---|
| Target | `paymentservice` |
| Method | Linux `tc netem` traffic control (2 s delay) |
| Symptoms | P99 latency spike, upstream timeouts |
| Detection time | < 60 seconds |
| Difficulty | Easy — clear metric signature |

```bash
#!/bin/bash
SERVICE="paymentservice"
LATENCY_MS=2000
DURATION_SECONDS=120

echo "$(date -Iseconds) - FAULT_START: High Latency ${LATENCY_MS}ms on $SERVICE" | tee -a /tmp/fault_log.txt

# Inject network delay inside the container
docker exec $SERVICE sh -c "
  apt-get install -y iproute2 -qq 2>/dev/null || true
  tc qdisc add dev eth0 root netem delay ${LATENCY_MS}ms
" 2>/dev/null || true

sleep $DURATION_SECONDS

# Remove delay
docker exec $SERVICE sh -c "tc qdisc del dev eth0 root 2>/dev/null || true"

echo "$(date -Iseconds) - FAULT_END: Latency removed from $SERVICE" | tee -a /tmp/fault_log.txt
```

> **Alternative if `tc` is unavailable:** Use an environment variable `LATENCY_MS` if the OTel service image supports it (check service docs), or use a proxy sidecar (Toxiproxy).

### Fault 3: Memory Pressure (Medium) — `03_memory_pressure.sh`

| Attribute | Value |
|---|---|
| Target | `checkoutservice` |
| Method | `docker update --memory` with a **dynamic cap** computed as `max(working_mb × 1.2, working_mb + 2)` (Session 13 patch) |
| Symptoms | Sustained working-set ≥80% of cgroup limit (captured by `memory_utilization` CRITICAL detector). Go runtime GC-cycles aggressively without emitting OOMKilled stdout logs; `probe_up` stays at 1, `probe_latency` stays near baseline. |
| Detection time | < 120 seconds (Session 13: typical first-CRITICAL fire within 30 s of fault start via the sweep) |
| Difficulty | Medium — no crash, no log trail; the signal is a metric-ratio saturation that requires the `container_spec_memory_limit_bytes` gauge to be scraped. |

```bash
#!/usr/bin/env bash
# Session 13 version: dynamic cap that scales with current working set.
# A fixed 25 MiB cap previously failed to saturate an idle-state checkoutservice
# (~15 MiB working set → only 60% utilization, below the 80% CRITICAL threshold).
# Dynamic cap guarantees ~83-94% utilization regardless of Go runtime heap state.
set -euo pipefail

CONTAINER="demo_app-checkoutservice-1"
FALLBACK_CAP_MB=25   # used if docker stats parse fails

get_working_mb() {
    local raw num_unit num unit
    raw=$(docker stats --no-stream --format '{{.MemUsage}}' "$1" 2>/dev/null) || return 1
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

case "${1:-}" in
    inject)
        WORKING_MB=$(get_working_mb "$CONTAINER" || true)
        if [[ -n "$WORKING_MB" && "$WORKING_MB" =~ ^[0-9]+$ && "$WORKING_MB" -gt 0 ]]; then
            # cap = max(W * 1.2, W + 2). The +2 MiB floor takes over for cold
            # heaps (< 10 MiB) where 1.2x would leave <2 MiB GC headroom.
            CAP_MUL=$(( WORKING_MB * 12 / 10 ))
            CAP_ADD=$(( WORKING_MB + 2 ))
            if (( CAP_MUL > CAP_ADD )); then CAP_MB=$CAP_MUL; else CAP_MB=$CAP_ADD; fi
        else
            CAP_MB=$FALLBACK_CAP_MB
        fi
        docker update --memory "${CAP_MB}m" --memory-swap "${CAP_MB}m" "$CONTAINER"
        ;;
    restore)
        docker update --memory 256m --memory-swap 256m "$CONTAINER"
        ;;
esac
```

> **Important (Session 13 gotcha):** This fault class only becomes detectable once the Docker Stats Exporter emits `container_spec_memory_limit_bytes` AND `query_metrics.py` fires CRITICAL on the ratio `working_set / limit`. Without the exporter gauge, Prometheus has no denominator to divide by; without the detector, the agent would see elevated memory but no CRITICAL override on the correct service. See `CLAUDE.md` gotchas: "Docker Stats Exporter emits `container_spec_memory_limit_bytes` (Session 13)" and "`memory_utilization` CRITICAL detector (Session 13)".

### Fault 4: CPU Throttling (Medium) — `04_cpu_throttling.sh`

| Attribute | Value |
|---|---|
| Target | `productcatalogservice` |
| Method | `docker update --cpus 0.1` (10% of one CPU) |
| Symptoms | High CPU wait, latency degradation, request queuing |
| Detection time | < 120 seconds |
| Difficulty | Medium — similar symptoms to other resource issues |

```bash
#!/bin/bash
SERVICE="productcatalogservice"
CPU_LIMIT="0.1"
DURATION_SECONDS=180

echo "$(date -Iseconds) - FAULT_START: CPU Throttling ($CPU_LIMIT CPU) on $SERVICE" | tee -a /tmp/fault_log.txt
docker update --cpus=$CPU_LIMIT $SERVICE
sleep $DURATION_SECONDS
docker update --cpus=0 $SERVICE    # 0 = remove limit
echo "$(date -Iseconds) - FAULT_END: CPU limit removed from $SERVICE" | tee -a /tmp/fault_log.txt
```

### Fault 5: Connection Exhaustion (Medium) — `05_connection_exhaustion.sh`

| Attribute | Value |
|---|---|
| Target | `redis` (limits cartservice connections) |
| Method | `redis-cli CONFIG SET maxclients 5` |
| Symptoms | "too many connections" errors, cartservice degradation |
| Detection time | < 90 seconds |
| Difficulty | Medium — requires understanding connection pooling |

```bash
#!/bin/bash
SERVICE="redis"
MAX_CLIENTS=5
DURATION_SECONDS=180

echo "$(date -Iseconds) - FAULT_START: Connection Exhaustion (max $MAX_CLIENTS) on $SERVICE" | tee -a /tmp/fault_log.txt
docker exec $SERVICE redis-cli CONFIG SET maxclients $MAX_CLIENTS
sleep $DURATION_SECONDS
docker exec $SERVICE redis-cli CONFIG SET maxclients 10000   # restore default
echo "$(date -Iseconds) - FAULT_END: maxclients restored on $SERVICE" | tee -a /tmp/fault_log.txt
```

### Fault 6: Network Partition (Medium) — `06_network_partition.sh`

| Attribute | Value |
|---|---|
| Target | `paymentservice` (isolated from `checkoutservice`) |
| Method | `docker network disconnect` |
| Symptoms | Payment timeouts, checkout 500 errors |
| Detection time | < 45 seconds |
| Difficulty | Medium — affects only a subset of call paths |

```bash
#!/bin/bash
SERVICE="paymentservice"
NETWORK="opsagent_default"   # docker compose default network name
DURATION_SECONDS=120

echo "$(date -Iseconds) - FAULT_START: Network Partition on $SERVICE" | tee -a /tmp/fault_log.txt
docker network disconnect $NETWORK $SERVICE
sleep $DURATION_SECONDS
docker network connect $NETWORK $SERVICE
echo "$(date -Iseconds) - FAULT_END: $SERVICE reconnected to $NETWORK" | tee -a /tmp/fault_log.txt
```

### Fault 7: Cascading Failure (Hard) — `07_cascading_failure.sh`

| Attribute | Value |
|---|---|
| Target | `cartservice` (upstream; causes frontend + checkoutservice to degrade) |
| Method | `docker stop cartservice` for extended period |
| Symptoms | Multiple services show errors in sequence (staggered timestamps) |
| **Key RCA challenge** | Root cause is `cartservice`, NOT the downstream symptom services |
| Detection time | < 60 seconds |
| Difficulty | Hard — requires causal discovery to distinguish root from symptoms |

```bash
#!/bin/bash
SERVICE="cartservice"
DURATION_SECONDS=180

echo "$(date -Iseconds) - FAULT_START: Cascading Failure (killing $SERVICE)" | tee -a /tmp/fault_log.txt
docker stop $SERVICE
sleep $DURATION_SECONDS
docker start $SERVICE
echo "$(date -Iseconds) - FAULT_END: $SERVICE restarted" | tee -a /tmp/fault_log.txt
```

### Fault 8: Config Error (Hard) — `08_config_error.sh`

| Attribute | Value |
|---|---|
| Target | `currencyservice` |
| Method | Restart with invalid environment variable |
| Symptoms | Currency conversion failures, checkout errors, confusing mixed signals |
| Detection time | < 30 seconds (fast but subtle) |
| Difficulty | Hard — service stays "running" but returns wrong results |

```bash
#!/bin/bash
SERVICE="currencyservice"
DURATION_SECONDS=120

echo "$(date -Iseconds) - FAULT_START: Config Error on $SERVICE" | tee -a /tmp/fault_log.txt

# Restart with broken config — service runs but currency conversion fails
docker stop $SERVICE
docker run -d --name ${SERVICE}_broken \
  --network opsagent_default \
  -e CURRENCY_SERVICE_PORT=7001 \
  -e EXCHANGE_RATE_API_URL="http://invalid-endpoint:9999" \
  ghcr.io/open-telemetry/demo:latest-currencyservice

sleep $DURATION_SECONDS

docker stop ${SERVICE}_broken
docker rm ${SERVICE}_broken
docker start $SERVICE   # Restart original with correct config

echo "$(date -Iseconds) - FAULT_END: $SERVICE config restored" | tee -a /tmp/fault_log.txt
```

### Evaluation Test Schedule

| Day | Fault Types | Runs Each | Total Tests |
|---|---|---|---|
| Day 1 | service_crash, high_latency | 5 | 10 |
| Day 2 | memory_pressure, cpu_throttling | 5 | 10 |
| Day 3 | connection_exhaustion, network_partition | 5 | 10 |
| Day 4 | cascading_failure, config_error | 5 | 10 |
| Day 5 | False-positive check (24h normal operation) | — | — |
| **Total** | **7 active fault types** | **5 each** | **35 tests** (Session 12: `cpu_throttling` removed — undetectable on idle demo) |

---

## 7. FastAPI Application

### 7.1 Full `api.py` Implementation

The modern `lifespan` context manager is used instead of deprecated `@app.on_event("startup")` decorators. Heavy resources (AgentExecutor, TopologyGraph) are initialized once at startup and stored on `app.state`.

```python
# src/serving/api.py
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from src.agent.executor import AgentExecutor
from src.data_collection.topology_extractor import TopologyGraph


# ── Request / Response Models ─────────────────────────────────────────────────

class AlertPayload(BaseModel):
    service: str
    metric: str
    value: float
    threshold: float
    timestamp: str              # ISO-8601


class InvestigationRequest(BaseModel):
    alert: AlertPayload
    time_range_minutes: int = 30


class RootCauseResult(BaseModel):
    service: str
    component: Optional[str] = None
    confidence: float


class InvestigationResponse(BaseModel):
    investigation_id: str
    status: str                 # "completed" | "failed"
    root_cause: Optional[RootCauseResult]
    report: Optional[str]
    evidence: List[Dict[str, Any]] = []
    recommendations: List[str] = []
    duration_seconds: float


# ── In-memory result store (resets on restart; sufficient for demo) ───────────
_investigation_results: Dict[str, InvestigationResponse] = {}


# ── Lifespan: initialize shared resources once at startup ─────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize heavy resources before the app starts accepting requests.
    Use app.state to share them across all request handlers.

    Modern FastAPI pattern: replaces deprecated @app.on_event("startup").
    DO NOT initialize these at module import time — they must be inside lifespan
    so they are not opened before fork when running multiple Uvicorn workers.
    """
    # Load AgentExecutor (compiles LangGraph, initializes LLM client, loads LSTM-AE)
    app.state.agent = AgentExecutor.from_config("configs/agent_config.yaml")
    # Load topology graph singleton
    app.state.topology = TopologyGraph()
    print("OpsAgent API startup complete.")

    yield   # Application is running; handle requests

    # Cleanup on shutdown (release LLM client, etc.)
    print("OpsAgent API shutting down.")


app = FastAPI(
    title="OpsAgent API",
    description="Autonomous Root Cause Analysis for Microservices",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    """
    Health check endpoint. Verifies connectivity to all external services.

    Returns:
        {status, components: {prometheus, loki, kafka, chromadb, llm}}
    """
    components = {}

    # Prometheus
    try:
        r = requests.get("http://localhost:9090/-/healthy", timeout=2)
        components["prometheus"] = "connected" if r.status_code == 200 else "error"
    except Exception:
        components["prometheus"] = "unreachable"

    # Loki
    try:
        r = requests.get("http://localhost:3100/ready", timeout=2)
        components["loki"] = "connected" if r.status_code == 200 else "error"
    except Exception:
        components["loki"] = "unreachable"

    # Kafka (attempt metadata fetch via confluent-kafka)
    try:
        from confluent_kafka.admin import AdminClient
        admin = AdminClient({"bootstrap.servers": "localhost:9092"})
        # list_topics() with a short timeout to verify connectivity
        admin.list_topics(timeout=2)
        components["kafka"] = "connected"
    except Exception:
        components["kafka"] = "unreachable"

    # ChromaDB
    try:
        import chromadb
        client = chromadb.PersistentClient(path="data/chromadb")
        client.heartbeat()
        components["chromadb"] = "connected"
    except Exception:
        components["chromadb"] = "unreachable"

    components["llm"] = "available"   # Gemini API is stateless; assume available

    overall = "healthy" if all(v == "connected" or v == "available" for v in components.values()) else "degraded"
    return {"status": overall, "components": components}


@app.get("/topology")
def get_topology(service: Optional[str] = None):
    """
    Get the current service dependency graph.

    Args:
        service: Optional service name to get a focused subgraph.
                 If omitted, returns the full topology.

    Returns:
        {nodes: [{name, type, status}], edges: [{source, target, protocol}]}
    """
    topology: TopologyGraph = app.state.topology
    if service:
        return topology.get_subgraph(service)
    return topology.to_json()


@app.post("/investigate", response_model=InvestigationResponse)
async def trigger_investigation(
    request: InvestigationRequest,
    background_tasks: BackgroundTasks,
):
    """
    Trigger a new RCA investigation.

    The investigation runs synchronously here (typical duration: 30–90 seconds).
    For long-running async investigations, use POST /investigate/async instead.

    Returns the completed RCA report with root cause, evidence chain,
    and recommended remediation actions.
    """
    investigation_id = f"inv_{uuid.uuid4().hex[:8]}"
    start_time = datetime.now(timezone.utc)

    agent: AgentExecutor = app.state.agent

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: agent.investigate(
                metrics={},         # In real pipeline: fetched from Prometheus
                logs=None,
                anomaly_timestamp=request.alert.timestamp,
                alert=request.alert.model_dump(),
            ),
        )

        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        response = InvestigationResponse(
            investigation_id=investigation_id,
            status="completed",
            root_cause=RootCauseResult(
                service=result.get("root_cause", "unknown"),
                confidence=result.get("root_cause_confidence", 0.0),
            ),
            report=result.get("rca_report"),
            evidence=[],
            recommendations=result.get("recommended_actions", []),
            duration_seconds=duration,
        )

    except Exception as e:
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        response = InvestigationResponse(
            investigation_id=investigation_id,
            status="failed",
            root_cause=None,
            report=f"Investigation failed: {e}",
            duration_seconds=duration,
        )

    _investigation_results[investigation_id] = response
    return response


@app.get("/investigations/{investigation_id}", response_model=InvestigationResponse)
def get_investigation(investigation_id: str):
    """Retrieve a previously completed investigation by ID."""
    if investigation_id not in _investigation_results:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return _investigation_results[investigation_id]


@app.get("/investigations", response_model=List[InvestigationResponse])
def list_investigations(limit: int = 20):
    """List recent investigations, newest first."""
    results = list(_investigation_results.values())
    return results[-limit:][::-1]
```

**Run the API:**
```bash
# Development (auto-reload)
poetry run uvicorn src.serving.api:app --reload --host 0.0.0.0 --port 8000

# Auto-generated docs available at:
#   http://localhost:8000/docs   (Swagger UI)
#   http://localhost:8000/redoc  (ReDoc)
```

---

## 8. Streamlit Dashboard

### 8.1 Dashboard Pages

| Page | Purpose | Key Components |
|---|---|---|
| **Overview** | Live system status | Service health grid, recent alert list, topology graph (NetworkX + pyvis) |
| **Investigate** | Manual investigation trigger | Alert form, investigation spinner, RCA report display |
| **History** | Past investigations | Investigation table, expandable report viewer, confidence chart |
| **Metrics** | Real-time monitoring | Grafana iframe embed or Plotly charts via Prometheus queries |
| **Settings** | Configuration | Threshold slider, LLM temp setting, tool budget adjustment |

### 8.2 `dashboard.py` Implementation

```python
# src/serving/dashboard.py
import time
from datetime import datetime, timezone

import requests
import streamlit as st

API_BASE = "http://localhost:8000"

st.set_page_config(
    page_title="OpsAgent — Autonomous RCA",
    page_icon="🔍",
    layout="wide",
)

# ── Sidebar navigation ───────────────────────────────────────────────────────
page = st.sidebar.radio(
    "Navigation",
    ["Overview", "Investigate", "History", "Metrics", "Settings"],
    index=0,
)

st.sidebar.markdown("---")
st.sidebar.caption("OpsAgent v0.1.0")


# ── Page: Overview ───────────────────────────────────────────────────────────
if page == "Overview":
    st.title("🔍 OpsAgent — System Overview")

    # Health check
    try:
        health = requests.get(f"{API_BASE}/health", timeout=3).json()
        overall = health.get("status", "unknown")
        color = "green" if overall == "healthy" else "orange"
        st.markdown(f"**API Status:** :{color}[{overall.upper()}]")

        components = health.get("components", {})
        cols = st.columns(len(components))
        for col, (name, status) in zip(cols, components.items()):
            icon = "✅" if status in ("connected", "available") else "❌"
            col.metric(name, f"{icon} {status}")
    except Exception:
        st.error("Cannot reach OpsAgent API at http://localhost:8000")

    st.divider()

    # Service topology (static diagram using NetworkX)
    st.subheader("Service Dependency Graph")
    try:
        topology = requests.get(f"{API_BASE}/topology", timeout=3).json()
        if isinstance(topology, str):
            import json
            topology = json.loads(topology)
        st.json(topology)    # Fallback: show JSON; replace with pyvis HTML in full impl
    except Exception:
        st.warning("Could not load topology from API.")

    # Recent investigations
    st.subheader("Recent Investigations")
    try:
        investigations = requests.get(f"{API_BASE}/investigations?limit=5", timeout=3).json()
        if investigations:
            for inv in investigations:
                rc = inv.get("root_cause") or {}
                confidence = rc.get("confidence", 0.0)
                service = rc.get("service", "unknown")
                status = inv.get("status", "unknown")
                icon = "✅" if inv.get("status") == "completed" else "❌"
                st.text(f"{icon} [{inv['investigation_id']}] Root cause: {service} ({confidence:.0%} confidence) — {status}")
        else:
            st.info("No investigations yet.")
    except Exception:
        st.warning("Could not load investigations from API.")


# ── Page: Investigate ────────────────────────────────────────────────────────
elif page == "Investigate":
    st.title("🚨 Trigger Investigation")
    st.caption("Manually trigger an RCA investigation for a specific alert.")

    with st.form("investigation_form"):
        col1, col2 = st.columns(2)
        service = col1.selectbox(
            "Affected Service",
            ["cartservice", "checkoutservice", "paymentservice",
             "productcatalogservice", "currencyservice", "frontend"],
        )
        metric = col2.selectbox(
            "Alert Metric",
            ["latency_p99", "error_rate", "cpu_usage", "memory_usage", "connection_count"],
        )
        col3, col4 = st.columns(2)
        value = col3.number_input("Observed Value", value=500.0)
        threshold = col4.number_input("Threshold", value=200.0)
        time_range = st.slider("Look-back window (minutes)", 10, 60, 30)
        submitted = st.form_submit_button("🔍 Start Investigation", type="primary")

    if submitted:
        payload = {
            "alert": {
                "service": service,
                "metric": metric,
                "value": value,
                "threshold": threshold,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "time_range_minutes": time_range,
        }

        with st.spinner("Investigating... (may take 30–90 seconds)"):
            try:
                resp = requests.post(
                    f"{API_BASE}/investigate",
                    json=payload,
                    timeout=120,
                ).json()

                st.success(f"Investigation complete — ID: {resp['investigation_id']}")

                rc = resp.get("root_cause") or {}
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("Root Cause Service", rc.get("service", "unknown"))
                col_b.metric("Confidence", f"{rc.get('confidence', 0):.0%}")
                col_c.metric("Duration", f"{resp.get('duration_seconds', 0):.1f}s")

                st.subheader("RCA Report")
                st.code(resp.get("report", "No report generated."), language=None)

                if resp.get("recommendations"):
                    st.subheader("Recommended Actions")
                    for i, action in enumerate(resp["recommendations"], 1):
                        st.markdown(f"{i}. {action}")

            except requests.exceptions.Timeout:
                st.error("Investigation timed out (>120s). Check API logs.")
            except Exception as e:
                st.error(f"Investigation failed: {e}")


# ── Page: History ────────────────────────────────────────────────────────────
elif page == "History":
    st.title("📋 Investigation History")

    try:
        investigations = requests.get(f"{API_BASE}/investigations?limit=50", timeout=3).json()
        if not investigations:
            st.info("No past investigations found.")
        else:
            import pandas as pd

            rows = []
            for inv in investigations:
                rc = inv.get("root_cause") or {}
                rows.append({
                    "ID": inv.get("investigation_id"),
                    "Status": inv.get("status"),
                    "Root Cause": rc.get("service", "—"),
                    "Confidence": f"{rc.get('confidence', 0):.0%}",
                    "Duration (s)": f"{inv.get('duration_seconds', 0):.1f}",
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True)

            selected_id = st.selectbox("View full report:", [r["ID"] for r in rows])
            if selected_id:
                selected = next(i for i in investigations if i["investigation_id"] == selected_id)
                st.subheader(f"Report: {selected_id}")
                st.code(selected.get("report", "No report."), language=None)

    except Exception as e:
        st.error(f"Could not load history: {e}")


# ── Page: Metrics ────────────────────────────────────────────────────────────
elif page == "Metrics":
    st.title("📊 Real-Time Metrics")
    st.caption("Live Grafana dashboard — refresh to update.")

    # Embed Grafana dashboard via iframe
    grafana_url = "http://localhost:3000/d/service_overview?orgId=1&kiosk=tv&refresh=15s"
    st.components.v1.iframe(grafana_url, height=800, scrolling=True)


# ── Page: Settings ───────────────────────────────────────────────────────────
elif page == "Settings":
    st.title("⚙️ Settings")
    st.caption("Configuration overrides. Changes take effect on next investigation.")

    st.subheader("Anomaly Detection")
    threshold_pct = st.slider("Anomaly threshold (percentile)", 90, 99, 95)
    st.caption(f"Current: P{threshold_pct} reconstruction error triggers investigation.")

    st.subheader("Agent Configuration")
    max_tool_calls = st.slider("Max tool calls per investigation", 5, 20, 10)
    confidence_threshold = st.slider("Confidence stop threshold", 0.5, 0.95, 0.7)

    st.subheader("LLM Configuration")
    temperature = st.slider("LLM temperature", 0.0, 1.0, 0.1, step=0.05)
    st.caption("Low temperature (0.1) → deterministic reasoning. Increase for more creative exploration.")

    if st.button("Save Settings", type="primary"):
        st.success("Settings saved. These will be applied to the next investigation.")
        st.info("Note: Full dynamic config reload is a stretch goal. For now, update configs/agent_config.yaml and restart the API.")
```

**Run the dashboard:**
```bash
poetry run streamlit run src/serving/dashboard.py --server.port 8501
```

---

## 9. Dockerfile

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.7.1

# Copy dependency files first (layer caching)
COPY pyproject.toml poetry.lock ./

# Install Python dependencies (no dev deps in production image)
RUN poetry config virtualenvs.create false \
    && poetry install --no-dev --no-interaction --no-ansi

# Copy application source
COPY src/ ./src/
COPY configs/ ./configs/
COPY runbooks/ ./runbooks/

# Models and ChromaDB are mounted as volumes at runtime
# (too large to bake into the image)

# Expose API and Dashboard ports
EXPOSE 8000   
EXPOSE 8501   

# Default: start FastAPI (override CMD to run Streamlit instead)
CMD ["uvicorn", "src.serving.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Add OpsAgent service to `docker-compose.yml`:**
```yaml
  opsagent:
    build: .
    ports:
      - "8000:8000"
      - "8501:8501"
    volumes:
      - ./models:/app/models           # Pre-trained model checkpoints
      - ./data/chromadb:/app/data/chromadb  # Runbook vector store
    environment:
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - PROMETHEUS_URL=http://prometheus:9090
      - LOKI_URL=http://loki:3100
      - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
    depends_on:
      - prometheus
      - loki
      - kafka
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '1.0'
```

---

## 10. Environment Variables

Create `.env.example` in the project root — copy to `.env` and fill in real values.

```bash
# .env.example
# Copy to .env and fill in values before running.

# ── Required ─────────────────────────────────────────────────────────────────
GEMINI_API_KEY=your_google_ai_studio_api_key_here

# ── Infrastructure URLs (defaults work for local Docker Compose) ──────────────
PROMETHEUS_URL=http://localhost:9090
LOKI_URL=http://localhost:3100
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
CHROMADB_PATH=data/chromadb

# ── Agent Configuration ───────────────────────────────────────────────────────
AGENT_MAX_TOOL_CALLS=10
AGENT_CONFIDENCE_THRESHOLD=0.7
LLM_MODEL=gemini-3-flash-preview
LLM_TEMPERATURE=0.1

# ── Anomaly Detection ─────────────────────────────────────────────────────────
ANOMALY_THRESHOLD_PERCENTILE=95

# ── Paths ─────────────────────────────────────────────────────────────────────
LSTM_AE_CHECKPOINT=models/lstm_autoencoder/finetuned_otel.pt
DRAIN3_PERSISTENCE_PATH=models/drain3/drain3_state.bin
```

Load in Python using `pydantic-settings`:
```python
# src/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    google_api_key: str
    prometheus_url: str = "http://localhost:9090"
    loki_url: str = "http://localhost:3100"
    kafka_bootstrap_servers: str = "localhost:9092"
    agent_max_tool_calls: int = 10
    agent_confidence_threshold: float = 0.7
    llm_model: str = "gemini-3-flash-preview"
    llm_temperature: float = 0.1

    class Config:
        env_file = ".env"

settings = Settings()
```

---

## 11. Useful Operational Commands

```bash
# ── Full stack management ────────────────────────────────────────────────────
bash scripts/start_infrastructure.sh          # Start everything
docker compose down                           # Stop monitoring stack
docker compose -f demo_app/docker-compose.demo.yml down   # Stop demo

# ── Check service health ─────────────────────────────────────────────────────
docker compose ps                             # Status of all containers
curl http://localhost:9090/-/healthy          # Prometheus health
curl http://localhost:3100/ready              # Loki health
curl http://localhost:8000/health             # OpsAgent API health

# ── Kafka topic inspection ────────────────────────────────────────────────────
docker exec kafka kafka-topics --list --bootstrap-server localhost:9092
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic opsagent-logs --from-beginning --max-messages 5

# ── Prometheus manual query ───────────────────────────────────────────────────
curl 'http://localhost:9090/api/v1/query?query=up'
curl 'http://localhost:9090/api/v1/query?query=rate(http_requests_total[1m])'

# ── Loki log query ─────────────────────────────────────────────────────────────
curl -G 'http://localhost:3100/loki/api/v1/query' \
  --data-urlencode 'query={job="otel-demo"} |= "error"' \
  --data-urlencode 'limit=10'

# ── Run fault injection ────────────────────────────────────────────────────────
bash demo_app/fault_scenarios/07_cascading_failure.sh

# ── Manual investigation trigger ──────────────────────────────────────────────
curl -X POST http://localhost:8000/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "alert": {
      "service": "checkoutservice",
      "metric": "latency_p99",
      "value": 528,
      "threshold": 200,
      "timestamp": "2025-03-15T14:32:05Z"
    },
    "time_range_minutes": 30
  }'

# ── View investigation result ─────────────────────────────────────────────────
curl http://localhost:8000/investigations/inv_abc123

# ── Force Prometheus config reload (after editing prometheus.yml) ─────────────
curl -X POST http://localhost:9090/-/reload

# ── Collect 24h baseline data ─────────────────────────────────────────────────
python scripts/generate_training_data.py --duration 24h --output data/baseline/
```
