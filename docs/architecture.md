# OpsAgent Architecture

OpsAgent is an autonomous Root Cause Analysis (RCA) agent for microservice systems. It monitors a running microservice stack, detects anomalies across logs and metrics, and autonomously investigates incidents to produce structured RCA reports with quantified confidence scores. This document describes how the pieces fit together.

## Table of contents

1. [System overview and design philosophy](#1-system-overview-and-design-philosophy)
2. [Two-loop design](#2-two-loop-design)
3. [Service topology](#3-service-topology)
4. [Data flow](#4-data-flow)
5. [Component catalog](#5-component-catalog)
6. [LangGraph investigation flow](#6-langgraph-investigation-flow)
7. [Confidence banding](#7-confidence-banding)
8. [Serving layer](#8-serving-layer)
9. [Key design decisions](#9-key-design-decisions)
10. [Limitations](#10-limitations)

---

## 1. System overview and design philosophy

OpsAgent acts as a virtual Site Reliability Engineer (SRE). The design is shaped by three observations about how human SREs actually triage incidents:

1. **Real-time detection and deep investigation belong on different clocks.** An anomaly detector has to run against every new log line and every metric scrape. A root-cause investigation, by contrast, is a multi-step reasoning process that involves querying multiple observability systems, reading historical topology, and cross-checking hypotheses. Running them on the same clock wastes compute on the detector path and starves the investigator path.
2. **Evidence trumps vibes.** SREs diagnose by reading concrete signals: a probe is returning zero, a pod's memory is at its cgroup cap, a span's latency spiked by 60x. A useful RCA agent has to produce evidence of the same grain, not plausible-sounding prose.
3. **Structured output beats free-form.** A root cause that can be consumed programmatically (a service name, a confidence score, a ranked top-3) is worth more than a paragraph of explanation.

These observations lead to the two-loop design below, to the agent's use of direct-observability signals as a confidence override, and to the structured RCA report format.

## 2. Two-loop design

OpsAgent separates detection from investigation into two cooperating loops.

```
              +-------------------------------------------+
              |            Microservice stack             |
              |  (OTel Demo: frontend, cartservice, ...)  |
              +----------+------------------+-------------+
                         |                  |
                 logs    |                  |    metrics
                         v                  v
          +-----------+----+         +--------+--------+
          |   Kafka topic  |         |   Prometheus    |
          | opsagent.logs  |         | (15s scrape)    |
          +--------+-------+         +--------+--------+
                   |                          |
                   v                          |
          +--------+-------+                  |
          |   Drain3       |                  |
          | template miner |                  |
          +--------+-------+                  |
                   |                          |
                   v                          v
          +--------+---------+     +----------+---------+
          | LSTM-Autoencoder |     | Threshold detector |
          | (log sequence    |     | (per-metric 2-sigma|
          |  reconstruction  |     |  + CRITICAL-over-  |
          |  error)          |     |  ride signals)     |
          +--------+---------+     +----------+---------+
                   \                          /
                    \     Fast Loop          /
                     \  (Watchdog, always on)
                      \                      /
                       v                    v
                    +--+------------------ -+-+
                    |    Combined anomaly     |
                    |  score > threshold?     |
                    +-----------+-------------+
                                |
                                |  trigger alert
                                v
                 Slow Loop (Investigator, on-demand)
                                |
                                v
            +--------+-------------------+-------+
            |          LangGraph agent           |
            |  (7 nodes, Gemini 3 Flash driver)  |
            |                                    |
            |  analyze_context -> sweep_probes   |
            |    -> form_hypothesis              |
            |    -> gather_evidence              |
            |    -> analyze_causation            |
            |    -> (loop or knockout)           |
            |    -> generate_report              |
            +-------------------+----------------+
                                |
                                v
                    +-----------+-----------+
                    | Structured RCA report |
                    |  + confidence score   |
                    +-----------+-----------+
                                |
                                v
                 +-----+---------------------+-----+
                 |  FastAPI endpoint / Streamlit   |
                 |  dashboard                      |
                 +---------------------------------+
```

### Fast Loop (Watchdog)

Stream-oriented and always on. Three parallel inputs:

- **Logs.** A Promtail sidecar tails every Docker container and ships lines to Loki; a Kafka topic (`opsagent.logs`) receives the same stream so the agent can consume historical windows on demand. Drain3 extracts log templates; new templates expand the vocabulary (115 templates on the full LogHub HDFS corpus, about 54 distinct templates during OTel Demo baseline).
- **Metrics.** Prometheus scrapes every service every 15 seconds. Eight container-level metrics come from a custom Docker Stats Exporter (cpu_usage, memory_usage, memory_limit, memory_utilization, network rx/tx bytes rate, network rx/tx errors rate). Three span-metrics (request_rate, error_rate, latency_p99) come from the OTel Collector's spanmetrics connector. Two probe metrics (probe_up, probe_latency) come from a custom Service Probe Exporter that performs real TCP and HTTP probes (a Redis PING, an HTTP GET on cartservice, a gRPC empty payload on the gRPC services).
- **Anomaly scoring.** Two complementary signals. The LSTM-Autoencoder scores log sequences against a learned baseline (reconstruction error). A set of direct-observability detectors run against each metric window (2-sigma anomaly, sparse `rate()` coverage below 70 percent, stale data older than 90 s, rate-metric flat-line, probe_latency 10x baseline, memory_utilization peak above 80 percent, crash-pattern log match). When any detector fires, the score is labelled CRITICAL.

When the combined anomaly score crosses the configured threshold, the Fast Loop emits an alert and wakes the Slow Loop.

### Slow Loop (Investigator)

Episode-oriented and on-demand. A LangGraph state machine walks through a fixed sequence of reasoning nodes, querying observability tools, running causal discovery, and drafting an RCA report. Each run takes 20 to 30 seconds of wall-clock time on a current-generation laptop with Gemini 3 Flash (preview) as the LLM driver. The agent state is a typed dict that carries the alert, the running evidence chain, the current list of hypotheses, and the causal graph.

## 3. Service topology

OpsAgent carries a static directed graph of the microservice system. Source of truth: [src/data_collection/topology_extractor.py](../src/data_collection/topology_extractor.py) (`TopologyGraph.KNOWN_EDGES`). The graph has 11 nodes and 14 directed edges.

Edge convention: `(dependency, dependent)` means the first service is called by the second. So `("redis", "cartservice")` reads "cartservice depends on redis".

**Nodes (11):** `frontend`, `cartservice`, `checkoutservice`, `productcatalogservice`, `paymentservice`, `currencyservice`, `redis`, `adservice`, `emailservice`, `recommendationservice`, `shippingservice`.

**Core edges (9, active on the reduced local OTel Demo stack):**

| Upstream | Downstream |
|---|---|
| redis | cartservice |
| cartservice | checkoutservice |
| productcatalogservice | checkoutservice |
| currencyservice | checkoutservice |
| paymentservice | checkoutservice |
| cartservice | frontend |
| productcatalogservice | frontend |
| checkoutservice | frontend |
| currencyservice | frontend |

**Extended edges (5, only relevant for the RCAEval cross-system evaluation):**

| Upstream | Downstream |
|---|---|
| adservice | frontend |
| recommendationservice | frontend |
| productcatalogservice | recommendationservice |
| emailservice | checkoutservice |
| shippingservice | checkoutservice |

The extended services are not running in the reduced local stack, so their probes return empty responses, which the metric query tool treats as a neutral note rather than an anomaly. Keeping them in the graph lets the agent reason about full Online Boutique cases during cross-system evaluation without extra code paths.

A visual rendering of the topology is available in [docs/images/service_topology.html](images/service_topology.html). The dashboard's Overview page also renders the graph as a Graphviz circle-layout diagram; see [docs/images/dashboard/01_overview.png](images/dashboard/01_overview.png).

## 4. Data flow

End-to-end data flow through the system:

1. **Container stdout to logs.** Promtail tails every Docker container's stdout, labels each line with its service, and ships to Loki. A parallel Kafka topic receives the same lines for stream processing.
2. **Log templating.** Drain3 (pinned to v0.9.1) extracts structured templates from raw log lines. Template id plus parameter vector becomes one dimension in the feature window.
3. **Metrics collection.** Prometheus scrapes each exporter on a 15 s interval. Feature vectors aggregate metrics into 5-minute windows with 7 statistics each (mean, std, min, max, p50, p90, p99).
4. **Feature window.** The feature engineer produces a 54-dimensional vector per window: 12 log-template signals plus 6 metrics x 7 statistics. The raw spans roughly 6 orders of magnitude; z-score normalization against the baseline's saved mean and std is applied before scoring.
5. **Anomaly scoring.** The LSTM-Autoencoder ingests the normalized window and produces a reconstruction error. Error above the threshold (calibrated against the last 24 hours of baseline) means "anomalous window".
6. **Trigger.** When anomaly scoring combined with the direct-observability detectors fires, an alert is emitted.
7. **Investigation.** The LangGraph agent takes the alert, sweeps direct-observability probes, forms hypotheses, gathers evidence via tool calls against Prometheus and Loki, runs the PC causal-discovery algorithm against the metric time series, and drafts the RCA report.
8. **Delivery.** The report, root-cause service, top-3 candidates, and confidence score surface via `POST /investigate` or the guided demo `POST /demo/investigate` endpoint, and are rendered in the Streamlit dashboard.

## 5. Component catalog

One subsection per `src/` subpackage. Full source in [src/](../src/).

### `data_collection`

Ingestion and static system metadata. Owns the Kafka consumer that pulls logs into the anomaly pipeline, the Prometheus metrics collector used by data-generation scripts to snapshot the baseline, and the topology graph. Key files: [src/data_collection/kafka_consumer.py](../src/data_collection/kafka_consumer.py), [src/data_collection/metrics_collector.py](../src/data_collection/metrics_collector.py), [src/data_collection/topology_extractor.py](../src/data_collection/topology_extractor.py). The Kafka client is `confluent-kafka`, not `kafka-python`, and decodes message values as bytes.

### `preprocessing`

Turns raw logs and metric snapshots into feature vectors the anomaly detector can score. Owns Drain3 integration, the 5-minute windowing logic, the feature engineer that assembles the 54-dimensional vector, and two dataset adapters: one for LogHub HDFS (11 million lines, 115 templates on the full corpus) and one for RCAEval (three file-format branches across RE1, RE2, RE3). Key files: [src/preprocessing/log_parser.py](../src/preprocessing/log_parser.py), [src/preprocessing/windowing.py](../src/preprocessing/windowing.py), [src/preprocessing/feature_engineering.py](../src/preprocessing/feature_engineering.py), [src/preprocessing/loghub_preprocessor.py](../src/preprocessing/loghub_preprocessor.py), [src/preprocessing/rcaeval_adapter.py](../src/preprocessing/rcaeval_adapter.py).

### `anomaly_detection`

Two models with complementary roles. The LSTM-Autoencoder is the primary: a sequence model trained in two phases (pretrained on 11 million HDFS log lines for general log-sequence structure, then fine-tuned on the OTel Demo baseline at lr=0.001 with the embedding and output layers reinitialized). Isolation Forest is a sanity baseline. Both share the threshold calibrator, which picks the anomaly cutoff from the 24-hour baseline's reconstruction-error distribution. Key files: [src/anomaly_detection/lstm_autoencoder.py](../src/anomaly_detection/lstm_autoencoder.py), [src/anomaly_detection/trainer.py](../src/anomaly_detection/trainer.py), [src/anomaly_detection/pretrain_on_loghub.py](../src/anomaly_detection/pretrain_on_loghub.py), [src/anomaly_detection/detector.py](../src/anomaly_detection/detector.py), [src/anomaly_detection/threshold.py](../src/anomaly_detection/threshold.py), [src/anomaly_detection/isolation_forest.py](../src/anomaly_detection/isolation_forest.py).

### `causal_discovery`

The PC Algorithm from the `causal-learn` library, plus a counterfactual-confidence scoring layer that breaks ties on edges the PC algorithm left undirected. Defensive code wraps Fisher's Z test against singular-matrix failures: the input is filtered to drop zero-variance columns, drop near-perfectly-correlated columns (absolute r above 0.999), and add tiny jitter to the rest. The depth is capped at 3 (depth 4 was tested and degraded results). Key files: [src/causal_discovery/pc_algorithm.py](../src/causal_discovery/pc_algorithm.py), [src/causal_discovery/counterfactual.py](../src/causal_discovery/counterfactual.py), [src/causal_discovery/graph_utils.py](../src/causal_discovery/graph_utils.py).

### `agent`

The LangGraph investigation agent. A typed state ([src/agent/state.py](../src/agent/state.py)), a 7-node graph ([src/agent/graph.py](../src/agent/graph.py)), five agent tools, two system-prompt variants (live versus RCAEval-offline), an RCA report template, and the top-level `AgentExecutor` entry point. See [section 6](#6-langgraph-investigation-flow) for the node-by-node breakdown. Tool implementations are in [src/agent/tools/](../src/agent/tools/): `query_metrics.py`, `search_logs.py`, `get_topology.py`, `search_runbooks.py`, `discover_causation.py`.

### `knowledge_base`

Retrieval-augmented runbook lookup. A sentence-transformers embedder encodes every runbook in [runbooks/](../runbooks/), ChromaDB stores the vectors, and the `search_runbooks` agent tool returns the top-k matches for a given incident signature. Key files: [src/knowledge_base/runbook_indexer.py](../src/knowledge_base/runbook_indexer.py), [src/knowledge_base/embeddings.py](../src/knowledge_base/embeddings.py).

### `serving`

The FastAPI HTTP API and Streamlit dashboard. Seven HTTP endpoints are documented in [docs/api_reference.md](api_reference.md). The dashboard is five pages: Overview, Investigate, History, Metrics, Settings. Key files: [src/serving/api.py](../src/serving/api.py), [src/serving/dashboard.py](../src/serving/dashboard.py), [src/serving/schemas.py](../src/serving/schemas.py), [src/serving/theme.py](../src/serving/theme.py), [src/serving/dashboard_helpers.py](../src/serving/dashboard_helpers.py).

## 6. LangGraph investigation flow

The agent's reasoning is a 7-node LangGraph state machine. The entire graph is assembled in [src/agent/graph.py](../src/agent/graph.py) `build_graph()`.

```
START
  |
  v
analyze_context       (read alert, set investigation goals)
  |
  v
sweep_probes          (36 direct-observability queries, bypasses tool budget)
  |
  v
form_hypothesis       (LLM ranks top-3 candidate root causes)
  |
  v
gather_evidence       (LLM-directed tool calls, decrements budget)
  |
  v
analyze_causation     (PC algorithm + counterfactual scoring)
  |
  +-- (conditional) --+
  |                   |
  v                   v
form_hypothesis   knockout          (falsify weak hypotheses against sweep signals)
  |                   |
  |                   v
  |            generate_report
  |                   |
  |                   v
  |                  END
  |
  (loop until budget or confidence stop condition)
```

**Node details:**

- **`analyze_context_node`.** Reads the alert. Determines which services and metrics the agent should pay attention to. Sets the evidence chain's starting state.
- **`sweep_probes_node`.** Runs a fixed set of direct-observability queries for every service in `affected_services`: `probe_up`, `probe_latency`, `cpu_usage`, `memory_usage`, `memory_utilization`, plus one crash-pattern log search per service. For the 6-service demo alert, that is 5 metric queries x 6 services + 6 log searches = 36 queries. These queries do NOT decrement the agent's tool budget, because they are mandatory infrastructure rather than LLM-directed reasoning. Every sweep evidence entry carries a `critical` boolean flag set from the pre-truncation tool result.
- **`form_hypothesis_node`.** The LLM ranks candidate root causes given the evidence so far. The prompt is strict about scope: the LLM is instructed to rank only services in `affected_services`. A post-LLM filter drops any hypothesis whose service is out of scope, falling back to the raw hypotheses if the filter would empty the list.
- **`gather_evidence_node`.** The LLM chooses tool calls (from `query_metrics`, `search_logs`, `get_topology`, `search_runbooks`, `discover_causation`) to deepen the evidence chain. Each call decrements the tool budget. Default budget is 10.
- **`analyze_causation_node`.** Runs the PC algorithm against a 10-minute window of the relevant services' metrics, scores remaining undirected edges by counterfactual-confidence, and produces a causal-graph summary. When any sweep evidence entry has `critical=True`, a CRITICAL-override path overrides low-confidence PC results with the LLM's top hypothesis.
- **`knockout_node`.** Placed on the end-branch of the conditional. Counts sweep-evidence `critical` flags per candidate (current root cause plus top-3 LLM hypotheses). If a strictly-more-critical alternative exists and the current `root_cause_confidence` is below 0.75, swap and bump confidence to at most 0.65 (deliberately below the 0.75 CRITICAL-override band).
- **`generate_report_node`.** Drafts the final RCA report using the template in [src/agent/prompts/report_template.py](../src/agent/prompts/report_template.py). The report includes an Executive Summary, Issue, Root Cause, Evidence, Causal Graph, Recommendations, and Confidence.

**Conditional edge:** After `analyze_causation`, the `should_continue` predicate decides whether to loop back to `form_hypothesis` (when the agent has useful budget and no clear answer) or advance to `knockout` and then `generate_report`.

## 7. Confidence banding

The confidence score on every root-cause prediction uses a two-band scheme. This is the single most important design decision in the agent: it is what separates "the agent told a plausible story" from "the agent has a hard signal".

**0.75 (CRITICAL override).** When any of the agent's direct-observability detectors fired in the investigation window, the agent publishes a fixed confidence of 0.75 and records the specific trigger in the RCA report's evidence chain. Qualifying triggers:

| Trigger | Detector location | Description |
|---|---|---|
| `probe_up=0` | `query_metrics` | Three or more of the last four probes returned 0 AND the historical mean was above 0.1 |
| `memory_utilization` peak | `query_metrics` | Peak above 0.80 AND baseline below 0.50 AND at least 4 samples |
| Sparse `rate()` data | `query_metrics` | Coverage below 70 percent of expected samples |
| Stale metrics | `query_metrics` | Most recent sample older than 90 s |
| Frozen rate-metric | `query_metrics` | 5 or more of last 8 rate values exactly 0 AND baseline above 1e-4 |
| `probe_latency` spike | `query_metrics` | Current value above 10x mean AND mean above 1e-4 s |
| Crash-pattern logs | `search_logs` | Three or more matches of crash, OOM, fatal, SIGKILL, SIGSEGV, or similar patterns |

The primary-track OTel Demo evaluation completed 35 of 35 fault-injection tests at exactly 0.75 confidence. The dashboard's root-cause card displays this as a solid circular progress ring.

**0.40 to 0.65 (LLM plus PC blend).** When no direct-observability signal fired, the root cause comes from the LangGraph agent's hypothesis ranking plus the PC algorithm's counterfactual-confidence score. The cross-system RCAEval evaluation averaged 0.54 in this band. Practitioners reading a report in this band should treat the root-cause service as a ranked suggestion, not a hard answer, and lean on the top-3 list.

## 8. Serving layer

The serving layer is two long-running processes: a FastAPI API on port 8000 and a Streamlit dashboard on port 8501.

### FastAPI

Seven endpoints (catalog in [docs/api_reference.md](api_reference.md)): `GET /health`, `GET /topology`, `POST /investigate`, `GET /investigations`, `GET /investigations/{id}`, `POST /demo/investigate`, `GET /demo/investigations/{id}/status`.

The `POST /demo/investigate` endpoint is the headline serving feature. A user picks one of six services. The API returns an `investigation_id` immediately and kicks off an async background task that:

1. Runs the matching `demo_app/fault_scenarios/*.sh inject` script in a worker thread.
2. Sleeps 120 seconds so the anomaly fully propagates into the metric lookback window.
3. Runs `AgentExecutor.investigate()` in a worker thread.
4. Runs the matching `restore` script in a worker thread. This always runs, including on prior exception, so the Docker stack is never left in a broken state.

A single-user `asyncio.Lock` guards the background task: a second POST returns HTTP 409 while the first demo is still running. A FastAPI lifespan shutdown hook sweeps any in-flight demo and runs its restore script synchronously on SIGTERM or SIGINT.

### Streamlit dashboard

Five pages:

- **Overview.** Service health grid, topology graph, recent-investigations summary. See [docs/images/dashboard/01_overview.png](images/dashboard/01_overview.png).
- **Investigate.** Six-service picker, topology preview, phase stepper (Injecting, Waiting, Investigating, Restoring, Completed) driven by polling `GET /demo/investigations/{id}/status` every two seconds. On completion, the page renders the root-cause card, the top-3 list, and the full RCA report. Screenshots: [02_investigate_picker.png](images/dashboard/02_investigate_picker.png), [03_investigate_in_progress.png](images/dashboard/03_investigate_in_progress.png), [04_investigate_completed.png](images/dashboard/04_investigate_completed.png), [07_rca_report_scrolled.png](images/dashboard/07_rca_report_scrolled.png).
- **History.** Paginated list of past investigations with root cause, confidence (Streamlit `ProgressColumn` using `format="percent"`), and duration. See [05_history.png](images/dashboard/05_history.png).
- **Metrics.** Embedded Grafana dashboard. Requires three Grafana env vars (`GF_SECURITY_ALLOW_EMBEDDING`, `GF_AUTH_ANONYMOUS_ENABLED`, `GF_AUTH_ANONYMOUS_ORG_ROLE`). See [06_metrics_grafana.png](images/dashboard/06_metrics_grafana.png).
- **Settings.** Health summary, endpoint URLs, version.

## 9. Key design decisions

| Decision | Rationale |
|---|---|
| Two-loop design, not single-loop | Detection needs to run against every new event; investigation is expensive and episodic. Mixing them starves the expensive path. |
| LSTM-AE primary, Isolation Forest baseline | LSTM-AE captures sequence structure in logs (Drain3 template order matters). Isolation Forest is a simple baseline for comparison. |
| Two-phase training (HDFS pretrain then OTel fine-tune) | The OTel Demo baseline is only 24 hours, too small to train a sequence model from scratch. HDFS pretraining (11 million lines) gives general log-sequence priors; fine-tuning adapts to the OTel-specific template vocabulary. The feature dims differ (115 vs 54), so only the LSTM body weights transfer; embedding and output layers are reinitialized. |
| Direct-observability detectors plus LLM reasoning | Direct detectors catch the hard-signal cases where the target service is literally unreachable or saturated. The LLM handles the reasoning cases where the signal is subtler. The two bands (0.75 versus 0.40 to 0.65) make the separation visible in the score. |
| PC algorithm depth capped at 3 | Depth 4 was tested and degraded results: more aggressive edge pruning removed weak signals from crashed services while strengthening spurious signals from healthy ones. |
| Three-layer singularity defense in PC | Fisher's Z test crashes with a singular-matrix error when columns are constant or perfectly correlated. Drop zero-variance columns, drop near-perfectly-correlated columns (absolute r above 0.999), then add tiny jitter. |
| Peak-based memory detection | Go and JVM runtimes under tight cgroup caps GC-cycle: working set sits near the cap for 5-plus consecutive 15-second samples, then dips briefly on each collection. Using `values[-1]` would miss a saturation that happened to coincide with a GC dip. `peak = np.max(arr)` captures the sustained-saturation band. |
| 120-second pre-investigation wait | Prometheus `rate()` uses a 1-minute lookback; metrics from a crashed service persist roughly 75 seconds after the container stops. At 60 seconds the sparse detector does not fire; at 120 seconds coverage drops reliably below 70 percent. |
| `currencyservice` excluded from live-alert `affected_services` | The v1.10.0 C++ currencyservice SIGSEGV-crashes repeatedly under load. Its probe_up intermittently shows 0 even in baseline, so leaving it in the affected-services list causes the agent to misattribute unrelated faults to it. The offline (RCAEval) prompt variant removes this clause because currencyservice is a legitimate fault target on the RCAEval OB dataset. |
| Gemini 3 Flash (preview) live, Gemini 2.5 Flash offline | Gemini 3's extra reasoning capacity matters for the live CRITICAL-override logic; Gemini 2.5 has much higher request-per-minute limits, which matters for sustained RCAEval evaluation runs. |

## 10. Limitations

- **Cross-system telemetry gap.** OpsAgent's 100 percent Recall@1 on the primary OTel Demo track relies on custom telemetry (the Service Probe Exporter, the Docker Stats Exporter's `container_spec_memory_limit_bytes` gauge, the sparse and stale detectors). Datasets that lack those signals, such as the RCAEval metrics-only CSVs, do not benefit from the 0.75 CRITICAL-override path. The cross-system RCAEval results (7.9 percent Recall@1) are honest to this limitation.
- **Low-traffic amplification.** The OTel Demo sits near 3 percent CPU per service when idle. Threshold-based and reconstruction-error baselines that rely on metrics exceeding a constant threshold look artificially weak in this environment. This is a characteristic of the benchmark, not the baselines, and is documented in the evaluation results.
- **Trace coverage.** The OTel Collector's spanmetrics connector only produces request-rate, error-rate, and latency-p99 for services that export traces. `cartservice`, `currencyservice`, and `redis` do not export traces in v1.10.0, so their span-metrics are absent. The agent compensates by leaning on the container-level and probe-level metrics, which are available for every service.
- **Single-node demo.** The default Docker Compose stack runs on a single laptop (tested on Apple Silicon and Intel macOS Docker Desktop). Scale testing on a multi-node cluster is out of scope for this project.
- **One demo at a time.** The guided-demo endpoint rejects concurrent requests with HTTP 409. This is a feature for a demo on a shared Docker stack, not a limit for a multi-tenant deployment.
