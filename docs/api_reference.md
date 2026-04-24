# OpsAgent HTTP API Reference

Complete reference for the OpsAgent REST API. Every endpoint is implemented in [src/serving/api.py](../src/serving/api.py); all request and response schemas are defined in [src/serving/schemas.py](../src/serving/schemas.py).

The API is a FastAPI application. An interactive Swagger UI is also available at `http://localhost:8000/docs` once the API is running.

## Table of contents

1. [Base URL and authentication](#1-base-url-and-authentication)
2. [Quick-start curl snippets](#2-quick-start-curl-snippets)
3. [Endpoint catalog](#3-endpoint-catalog)
4. [Endpoint reference](#4-endpoint-reference)
   - [4.1 `GET /health`](#41-get-health)
   - [4.2 `GET /topology`](#42-get-topology)
   - [4.3 `POST /investigate`](#43-post-investigate)
   - [4.4 `GET /investigations/{investigation_id}`](#44-get-investigationsinvestigation_id)
   - [4.5 `GET /investigations`](#45-get-investigations)
   - [4.6 `POST /demo/investigate`](#46-post-demoinvestigate)
   - [4.7 `GET /demo/investigations/{investigation_id}/status`](#47-get-demoinvestigationsinvestigation_idstatus)
5. [Schemas](#5-schemas)
6. [Demo phase lifecycle](#6-demo-phase-lifecycle)
7. [Service to fault-type mapping](#7-service-to-fault-type-mapping)
8. [Concurrency and limits](#8-concurrency-and-limits)
9. [Error model](#9-error-model)

---

## 1. Base URL and authentication

| Environment | Base URL |
|---|---|
| Local host process (`make run`) | `http://localhost:8000` |
| Docker Compose (`make docker-up`) | `http://localhost:8000` |
| Inside `opsagent-net` (service-to-service) | `http://opsagent-api:8000` |

OpsAgent does not require client authentication. The only required environment variable is `GEMINI_API_KEY`, which is read by the agent at startup (not by clients). CORS is configured to accept requests from the Streamlit dashboard at `http://localhost:8501`.

## 2. Quick-start curl snippets

Health check:

```bash
curl -s http://localhost:8000/health | python -m json.tool
```

Fire a guided demo (service-picker flow):

```bash
curl -s -X POST http://localhost:8000/demo/investigate \
  -H "Content-Type: application/json" \
  -d '{"service": "cartservice"}'
```

Poll demo status (repeat every 2 seconds):

```bash
curl -s http://localhost:8000/demo/investigations/demo_abc12345/status | python -m json.tool
```

List recent investigations:

```bash
curl -s http://localhost:8000/investigations?limit=5 | python -m json.tool
```

## 3. Endpoint catalog

| Method | Path | Tag | Purpose |
|---|---|---|---|
| `GET` | `/health` | ops | Report OpsAgent and dependency status |
| `GET` | `/topology` | ops | Return the full service dependency graph or a subgraph |
| `POST` | `/investigate` | agent | Run a synchronous RCA investigation for a caller-supplied alert |
| `GET` | `/investigations/{investigation_id}` | agent | Fetch a single investigation by id |
| `GET` | `/investigations` | agent | List recent investigations, newest first |
| `POST` | `/demo/investigate` | demo | Start a guided fault-injection demo for one of six services |
| `GET` | `/demo/investigations/{investigation_id}/status` | demo | Return the phase snapshot for a guided demo |

## 4. Endpoint reference

### 4.1 `GET /health`

Report OpsAgent's overall health plus the reachability of each external dependency.

**Response:** `HealthStatus` (200)

The overall `status` is `"healthy"` when every component's status is either `"connected"` or `"available"`; otherwise `"degraded"`.

**Component probes:**

| Component | Healthy value | Probe |
|---|---|---|
| `prometheus` | `connected` | HTTP GET `/-/ready` with a short timeout |
| `loki` | `connected` | HTTP GET `/ready` |
| `kafka` | `connected` | `AdminClient.list_topics(timeout=2)` |
| `chromadb` | `connected` | `PersistentClient.heartbeat()` |
| `llm` | `available` | Presence of the `GEMINI_API_KEY` environment variable (the live endpoint is not called because it would burn tokens) |

**Example response:**

```json
{
  "status": "healthy",
  "components": {
    "prometheus": "connected",
    "loki": "connected",
    "kafka": "connected",
    "chromadb": "connected",
    "llm": "available"
  }
}
```

**curl:**

```bash
curl -s http://localhost:8000/health
```

### 4.2 `GET /topology`

Return the service dependency graph. With no query parameters, returns the full 11-node graph. With `?service=<name>`, returns the subgraph consisting of that service plus its immediate upstream and downstream neighbours.

**Query parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `service` | string or null | null | If set, return a subgraph centred on this service |

**Response:** `TopologyResponse` (200)

```json
{
  "nodes": [
    {"name": "frontend", "attributes": {}},
    {"name": "cartservice", "attributes": {}},
    {"name": "checkoutservice", "attributes": {}},
    {"name": "productcatalogservice", "attributes": {}},
    {"name": "paymentservice", "attributes": {}},
    {"name": "currencyservice", "attributes": {}},
    {"name": "redis", "attributes": {}},
    {"name": "adservice", "attributes": {}},
    {"name": "emailservice", "attributes": {}},
    {"name": "recommendationservice", "attributes": {}},
    {"name": "shippingservice", "attributes": {}}
  ],
  "edges": [
    {"source": "frontend", "target": "cartservice", "attributes": {}},
    {"source": "cartservice", "target": "redis", "attributes": {}},
    {"source": "frontend", "target": "checkoutservice", "attributes": {}}
  ],
  "subgraph_of": null
}
```

When `service` is supplied, `subgraph_of` echoes the parameter value.

**curl examples:**

```bash
curl -s http://localhost:8000/topology
curl -s "http://localhost:8000/topology?service=cartservice"
```

### 4.3 `POST /investigate`

Run a root-cause investigation synchronously for a caller-supplied alert. Typical wall-clock duration is 30 to 90 seconds. Errors are caught and returned as `status="failed"` rather than HTTP 5xx so the dashboard can surface them directly.

**Request body:** `InvestigationRequest`

```json
{
  "alert": {
    "service": "cartservice",
    "metric": "probe_up",
    "value": 0.0,
    "threshold": 1.0,
    "timestamp": "2026-04-20T16:42:10Z"
  },
  "time_range_minutes": 30
}
```

`time_range_minutes` is the look-back window used by the agent's tool queries. Valid range: 1 to 180, default 30.

**Response:** `InvestigationResponse` (200)

```json
{
  "investigation_id": "inv_ab12cd34",
  "status": "completed",
  "root_cause": {
    "service": "cartservice",
    "component": null,
    "confidence": 0.75
  },
  "top_3_predictions": ["cartservice", "redis", "checkoutservice"],
  "report": "... full RCA report text ...",
  "evidence": [],
  "recommendations": [
    "Restart the cartservice container",
    "Check the Redis connection pool configuration"
  ],
  "duration_seconds": 24.6,
  "started_at": "2026-04-23T19:00:00+00:00"
}
```

When the investigation fails, `status` is `"failed"`, `root_cause` is null, and `report` contains the failure message.

**curl:**

```bash
curl -s -X POST http://localhost:8000/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "alert": {
      "service": "cartservice",
      "metric": "probe_up",
      "value": 0.0,
      "threshold": 1.0,
      "timestamp": "2026-04-20T16:42:10Z"
    },
    "time_range_minutes": 30
  }'
```

**Note on `affected_services`:** This endpoint takes a single-service alert and forwards it to the agent with `affected_services = [alert.service]`. If you need the multi-service sweep that the evaluation harness uses, trigger [`POST /demo/investigate`](#46-post-demoinvestigate) instead, which builds the same 6-service alert shape that the primary evaluation used to reach 100 percent Recall@1.

### 4.4 `GET /investigations/{investigation_id}`

Fetch a previously completed investigation by id. The API keeps the last 100 investigations in memory (FIFO, newest wins).

**Path parameters:**

| Name | Type | Description |
|---|---|---|
| `investigation_id` | string | Id returned by a prior `POST /investigate` or `POST /demo/investigate` call |

**Response:** `InvestigationResponse` (200) with the same shape as [section 4.3](#43-post-investigate).

**Errors:**

| Status | Meaning |
|---|---|
| 404 | Investigation not found (never existed or evicted from the 100-entry cache) |

**curl:**

```bash
curl -s http://localhost:8000/investigations/inv_ab12cd34
```

### 4.5 `GET /investigations`

List recent investigations, newest first. Backed by the same in-memory FIFO cache as the single-id endpoint.

**Query parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `limit` | integer | 20 | Max entries to return. Clamped to the range 1 to 100. |

**Response:** `list[InvestigationResponse]` (200). Empty list when the cache has no entries.

**curl:**

```bash
curl -s "http://localhost:8000/investigations?limit=5"
```

### 4.6 `POST /demo/investigate`

Start a guided end-to-end demo for one of six supported services. The API returns an `investigation_id` immediately and runs the full `inject -> wait 120 s -> investigate -> restore` lifecycle in a background task. The dashboard polls `GET /demo/investigations/{id}/status` every two seconds to drive its phase stepper.

**Request body:** `DemoInvestigationRequest`

```json
{"service": "cartservice"}
```

`service` must be one of: `cartservice`, `checkoutservice`, `frontend`, `paymentservice`, `productcatalogservice`, `redis`. Any other value is rejected with HTTP 422 by FastAPI's request validation.

**Response:** plain dict (200)

```json
{
  "investigation_id": "demo_abc12345",
  "service": "cartservice",
  "fault_type": "service_crash",
  "ground_truth": "cartservice"
}
```

Use the returned `investigation_id` to poll [`GET /demo/investigations/{id}/status`](#47-get-demoinvestigationsinvestigation_idstatus).

**Errors:**

| Status | Meaning |
|---|---|
| 409 | A demo is already running. Only one demo is allowed at a time. |
| 422 | `service` is not one of the six supported values. |

**Typical wall-clock:** roughly three minutes (about 120 s wait + 25 s investigation + 15 s restore + overhead).

**curl:**

```bash
curl -s -X POST http://localhost:8000/demo/investigate \
  -H "Content-Type: application/json" \
  -d '{"service": "checkoutservice"}'
```

### 4.7 `GET /demo/investigations/{investigation_id}/status`

Return the current phase of a demo investigation. Intended to be polled at roughly 2 second intervals while a demo is active.

**Path parameters:**

| Name | Type | Description |
|---|---|---|
| `investigation_id` | string | The `demo_*` id returned by `POST /demo/investigate` |

**Response:** `DemoInvestigationStatus` (200)

```json
{
  "investigation_id": "demo_abc12345",
  "service": "cartservice",
  "fault_type": "service_crash",
  "phase": "investigating",
  "phase_label": "Investigating",
  "progress_pct": 70,
  "started_at": "2026-04-23T19:00:00+00:00",
  "completed_at": null,
  "error": null,
  "result": null
}
```

When `phase` reaches `"completed"` or `"failed"`, the `result` field is populated with the full `InvestigationResponse`.

**Errors:**

| Status | Meaning |
|---|---|
| 404 | Unknown `investigation_id` (never existed or evicted from the 20-entry demo status cache) |

**curl:**

```bash
curl -s http://localhost:8000/demo/investigations/demo_abc12345/status
```

## 5. Schemas

All schemas are Pydantic v2 models defined in [src/serving/schemas.py](../src/serving/schemas.py). Types shown here match the Python source exactly.

### `AlertPayload`

| Field | Type | Required | Description |
|---|---|---|---|
| `service` | string | yes | Service the alert refers to |
| `metric` | string | yes | Metric that crossed its threshold |
| `value` | float | yes | Observed value that triggered the alert |
| `threshold` | float | yes | Configured alert threshold |
| `timestamp` | string | yes | ISO-8601 timestamp of the anomaly |

### `InvestigationRequest`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `alert` | `AlertPayload` | yes | | See above |
| `time_range_minutes` | integer | no | 30 | Look-back window in minutes (1 to 180) |

### `RootCauseResult`

| Field | Type | Required | Description |
|---|---|---|---|
| `service` | string | yes | Predicted root-cause service |
| `component` | string or null | no | Optional sub-component identifier |
| `confidence` | float | yes | 0.0 to 1.0. See [confidence banding](#confidence-banding) below |

### `InvestigationResponse`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `investigation_id` | string | yes | | `inv_*` for synchronous investigations, `demo_*` for demo investigations |
| `status` | string | yes | | `"completed"` or `"failed"` |
| `root_cause` | `RootCauseResult` or null | no | null | Null when the investigation failed or the agent returned "inconclusive" |
| `top_3_predictions` | list of strings | no | `[]` | Top three candidate services in rank order |
| `report` | string or null | no | null | Full RCA report text (plain text with ASCII section headers) |
| `evidence` | list of dicts | no | `[]` | Structured evidence entries. Currently a placeholder. |
| `recommendations` | list of strings | no | `[]` | Remediation steps extracted from the RCA report |
| `duration_seconds` | float | no | 0.0 | Wall-clock investigation time |
| `started_at` | string or null | no | null | ISO-8601 start timestamp |

### `HealthStatus`

| Field | Type | Description |
|---|---|---|
| `status` | string | `"healthy"` or `"degraded"` |
| `components` | map of string to string | Per-dependency probe result |

### `TopologyResponse`

| Field | Type | Description |
|---|---|---|
| `nodes` | list of `{name, attributes}` | All services in the graph |
| `edges` | list of `{source, target, attributes}` | Directed edges |
| `subgraph_of` | string or null | Non-null when the response is a subgraph |

### `DemoInvestigationRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `service` | `DemoService` | yes | One of the six supported services (see below) |

`DemoService` is a string literal type restricted to: `cartservice`, `checkoutservice`, `frontend`, `paymentservice`, `productcatalogservice`, `redis`.

### `DemoInvestigationStatus`

| Field | Type | Required | Description |
|---|---|---|---|
| `investigation_id` | string | yes | `demo_*` id |
| `service` | `DemoService` | yes | Target of the injected fault |
| `fault_type` | string | yes | Canonical fault-type name (see [section 7](#7-service-to-fault-type-mapping)) |
| `phase` | `DemoPhase` | yes | Current phase (see [section 6](#6-demo-phase-lifecycle)) |
| `phase_label` | string | yes | Human-readable label for `phase` |
| `progress_pct` | integer | yes | 0 to 100 |
| `started_at` | string or null | no | ISO-8601 demo start time |
| `completed_at` | string or null | no | ISO-8601 demo completion time |
| `error` | string or null | no | Error detail when `phase` is `"failed"` |
| `result` | `InvestigationResponse` or null | no | Populated when `phase` is `"completed"` or `"failed"` |

### Confidence banding

The `confidence` field on `RootCauseResult` uses a two-band scheme driven by whether the agent's direct-observability detectors fired:

- **0.75 (CRITICAL override).** One or more of the following "hard" signals was observed in the window: `probe_up=0` for three or more of the last four probes, `memory_utilization` peak above 0.80 with baseline below 0.50, sparse `rate()` data below 70 percent coverage, stale metrics older than 90 seconds, a rate metric that flat-lines to 0 for five or more consecutive samples despite non-zero baseline activity, a `probe_latency` spike at 10x baseline, or three or more crash-pattern log matches. In this case the agent publishes a fixed 0.75 and records the specific trigger in the RCA report's evidence chain. All 35 OTel Demo fault-injection tests in the primary evaluation completed at exactly 0.75.
- **0.40 to 0.65 (LLM + PC blend).** No direct-observability signal fired, so the root cause comes from the LangGraph agent's hypothesis ranking plus the PC algorithm's counterfactual confidence score. This band is typical on cross-system datasets that lack probe and memory-limit signals (the RCAEval evaluation averaged 0.54 in this band).

## 6. Demo phase lifecycle

A guided demo moves through these phases in order. The `progress_pct` column is exactly what the API returns; the dashboard uses it as the phase stepper's numeric progress.

| `phase` | `phase_label` | `progress_pct` | What is happening |
|---|---|---|---|
| `queued` | Queued | 0 | Status row created, background task not yet scheduled |
| `injecting` | Injecting fault | 10 | `bash <fault_script> inject` running in a worker thread |
| `waiting` | Waiting for anomaly | 30 | Sleeping 120 seconds so the `rate()` lookback window ages out of clean baseline data |
| `investigating` | Investigating | 70 | `AgentExecutor.investigate()` running in a worker thread |
| `restoring` | Restoring | 90 | `bash <fault_script> restore` running. Always runs, including on prior failure. |
| `completed` | Completed | 100 | Success terminal state. `result` populated. |
| `failed` | Failed | 100 | Failure terminal state. `error` populated; `result` populated with a failure stub. |

Phases `queued`, `injecting`, `waiting`, and `investigating` are "in-flight". If the FastAPI process receives SIGTERM or SIGINT while a demo is in-flight, the lifespan shutdown hook synchronously runs the relevant fault script's `restore` action so the Docker stack is not left in a broken state.

## 7. Service to fault-type mapping

The guided demo hard-codes the pairing below. Each fault script lives in `demo_app/fault_scenarios/`.

| Service | Fault type | Script | What it does |
|---|---|---|---|
| `cartservice` | `service_crash` | `01_service_crash.sh` | `docker stop` then `docker start` |
| `frontend` | `high_latency` | `02_high_latency.sh` | Alpine sidecar with `tc netem` adds 500 ms latency on eth0 |
| `checkoutservice` | `memory_pressure` | `03_memory_pressure.sh` | Dynamic `docker update --memory` cap sized to current working set |
| `redis` | `connection_exhaustion` | `05_connection_exhaustion.sh` | `docker pause` and `docker unpause` |
| `paymentservice` | `network_partition` | `06_network_partition.sh` | `docker pause` and `docker unpause` |
| `productcatalogservice` | `config_error` | `08_config_error.sh` | Replacement container with an invalid `PRODUCT_CATALOG_SERVICE_PORT` and `--restart on-failure` |

Scripts 04 (`cpu_throttling`) and 07 (`cascading_failure`) are not wired into the guided demo.

## 8. Concurrency and limits

| Limit | Value | Where enforced |
|---|---|---|
| Max demos running concurrently | 1 | `asyncio.Lock` on `app.state.demo_lock`; second POST returns HTTP 409 |
| Max investigations retained | 100 | `OrderedDict` FIFO in `app.state.investigations` (`MAX_HISTORY`) |
| Max demo status entries retained | 20 | `OrderedDict` FIFO in `app.state.demo_status` (`_DEMO_STATUS_CAP`) |
| Fault-script subprocess timeout | 120 seconds | `_DEMO_SUBPROCESS_TIMEOUT` |
| Pre-investigation wait | 120 seconds | `_DEMO_WAIT_SECONDS` |
| Metric look-back window (demo) | 10 minutes | `_DEMO_TIME_RANGE_MINUTES` |
| `POST /investigate` look-back | 1 to 180 minutes | Pydantic validation on `InvestigationRequest.time_range_minutes` |
| `GET /investigations` page size | 1 to 100 | Clamped by the handler |

## 9. Error model

FastAPI returns JSON error bodies with a single `detail` field for all non-200 responses:

```json
{"detail": "Investigation not found"}
```

Successful-but-failed investigations do NOT return an HTTP error. They return 200 with `status="failed"` and a human-readable report. This keeps the dashboard's render path simple: a single schema shape regardless of outcome.
