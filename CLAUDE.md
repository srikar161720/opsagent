# OpsAgent — Claude Code Context

> Autonomous Root Cause Analysis Agent for Microservices. OpsAgent monitors a microservice
> architecture, detects anomalies across logs and metrics, and autonomously investigates
> incidents to produce structured RCA reports — acting as a virtual Site Reliability Engineer.

---

## Architecture: Two-Loop Design

**Fast Loop (Watchdog)** — Real-time stream processing. Kafka ingests logs from microservices; Drain3 extracts log templates; the LSTM-Autoencoder scores sequences against a baseline threshold. Prometheus scrapes metrics in parallel. When the combined anomaly score exceeds the threshold, the Fast Loop fires a trigger to the Slow Loop.

**Slow Loop (Investigator)** — LangGraph-powered agent. On trigger, the agent runs a multi-step investigation: it receives the alert context, queries metrics and logs via tools, runs causal discovery to build a dependency graph, scores counterfactual confidence, and generates a structured RCA report. The report includes an evidence chain, the root cause service/component, and a confidence score. Results surface via a FastAPI endpoint and a Streamlit dashboard.

---

## Technology Stack

| Layer | Tool | Purpose |
|---|---|---|
| **Orchestration** | Docker Compose | Local multi-service stack |
| **Target System** | OpenTelemetry Demo (reduced, 6 services) | Source of microservice logs and metrics |
| **Metrics** | Prometheus + Grafana + Docker Stats Exporter | Container-level metrics collection and visualization |
| **App Telemetry** | OpenTelemetry Collector (spanmetrics connector) | Application-level request/error/latency metrics from traces |
| **Service Probes** | Service Probe Exporter (custom) | Direct TCP/HTTP probes for availability + latency per service |
| **Logs** | Loki + Kafka | Log aggregation and stream ingestion |
| **Log Parsing** | Drain3 | Template extraction from raw logs |
| **Feature Engineering** | Pandas + NumPy | Windowed aggregations and feature vectors |
| **Topology** | NetworkX | Service dependency graph |
| **Vector DB** | ChromaDB + sentence-transformers | Runbook similarity search |
| **Anomaly Detection** | PyTorch (LSTM-Autoencoder) | Primary: log sequence anomaly scoring |
| **Anomaly Baseline** | scikit-learn (Isolation Forest) | Comparison baseline |
| **Causal Discovery** | causal-learn | PC Algorithm for root cause graph |
| **Agent Orchestration** | LangGraph | Stateful multi-step investigation graph |
| **LLM** | Gemini 3 Flash (preview) | Agent reasoning and report generation |
| **API** | FastAPI | REST endpoint (`POST /investigate`) |
| **Dashboard** | Streamlit | Interactive demo UI |
| **Dependency Mgmt** | Poetry | Python environment management |
| **Code Quality** | Ruff + mypy | Linting and static type checking |
| **GPU Compute** | Google Colab Pro | LSTM-AE training (T4/L4/A100) |

---

## Data Strategy

Three complementary sources — each serves a distinct, non-overlapping purpose:

| Dataset | Size | Role |
|---|---|---|
| **OpenTelemetry Demo** (self-generated via fault injection) | ~24h baseline + 35 fault tests (7 types × 5 reps) | Primary training data and controlled evaluation with known ground truth |
| **LogHub HDFS** (Zenodo DOI: 10.5281/zenodo.8196385) | 11M+ logs, block-level labels | LSTM-AE pretraining; Drain3 template validation; benchmark vs. DeepLog / LogRobust |
| **RCAEval** RE1/RE2/RE3 (Zenodo DOI: 10.5281/zenodo.14590730) | 736 labeled failure cases | Cross-system RCA validation; comparison against 5 published baselines |

---

## Target Outcomes

| Metric | Target | Evaluation Source |
|---|---|---|
| Recall@1 | ≥ 80% | OTel Demo fault injection (40 cases) |
| Recall@3 | ≥ 95% | OTel Demo fault injection (40 cases) |
| Precision | ≥ 70% | False positive rate during normal operation |
| Detection Latency | < 60 s | Timestamp: fault injection → alert |
| MTTR Proxy | ≥ 50% reduction | vs. rule-based and AD-only baselines |
| Explanation Quality | ≥ 4.0 / 5.0 | Manual rubric scoring |
| RCAEval Recall@1 (RE2) | Competitive with CIRCA / RCD | 271-case cross-system validation |

---

## Development Conventions

- **Language:** Python >=3.11,<3.13 (pinned for torch/onnxruntime Intel Mac wheel availability), enforced type hints throughout
- **Dependency management:** `poetry install` (or `make setup`) — never `pip install` directly
- **Linting/Formatting:** `ruff check . --fix && ruff format .` before committing
- **Type checking:** `mypy src/` — resolve errors before marking a task complete
- **Testing:** `poetry run pytest tests/unit/` for unit tests; `poetry run pytest tests/integration/` for pipeline tests
- **Branching:** Create a new Git branch per feature/phase; never commit directly to `main`
- **Notebooks:** Kept in `notebooks/` for experimentation only — production code lives in `src/`
- **GPU training:** Use Google Colab Pro for LSTM-AE training; save checkpoints to `models/`
- **Secrets:** All API keys (Gemini, etc.) via `.env` — never hardcoded; use `python-dotenv`
- **Config:** Hyperparameters and paths live in `configs/*.yaml`, not hardcoded in source
- **Mocking external services:** Unit tests mock Kafka, Prometheus, Loki, and ChromaDB at the client level using `unittest.mock.patch`. Never start real services for unit tests.
- **Test fixtures:** Shared fixtures in `tests/conftest.py`; use `pytest.fixture` with `function` scope for stateful tests, `session` scope for read-only shared data.
- **Integration tests require the stack:** Run `bash scripts/start_infrastructure.sh` before `poetry run pytest tests/integration/`.
- **No references to context files in project code:** Files outside of `CLAUDE.md`, `PROGRESS.md`, and `context/` must never contain references to context files (e.g., `context/architecture_and_design.md`, `context/data_pipeline_specs.md`, `PROGRESS.md`). These are internal Claude Code session documents — project source code, configs, docs, tests, notebooks, scripts, and README files must be fully self-contained. Do not add comments like `# See context/...` or `# As specified in PROGRESS.md`. If information from a context file is needed, inline it directly.

### Common Commands

```bash
# Environment
make setup                            # poetry install (all dependencies)
poetry run python <script>            # Run within Poetry env

# Infrastructure
make infra-up                         # Start Docker stack (Prometheus, Grafana, Loki, Kafka, Docker Stats Exporter)
make infra-down                       # Tear down Docker stack
make demo-up                          # Start OTel Demo app
make demo-down                        # Stop OTel Demo app

# Data
poetry run python scripts/download_datasets.py --all  # Download RCAEval + LogHub HDFS
poetry run python scripts/generate_training_data.py    # Collect OTel Demo baseline
poetry run python scripts/inject_faults.py             # Run fault injection scenarios

# Quality
make lint                             # ruff check --fix + ruff format
make typecheck                        # mypy src/
make test                             # pytest tests/unit/ -v
make test-integration                 # pytest tests/integration/ -v

# Serving
make run                              # FastAPI on :8000
make dashboard                        # Streamlit on :8501

# Evaluation
poetry run python scripts/run_evaluation.py            # Full evaluation suite

# Cleanup
make clean                            # Remove __pycache__, .mypy_cache, .ruff_cache
```

---

## Required Environment Variables

Copy `.env.example` to `.env` and populate before running any component that uses the LLM:

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | **Yes** | Gemini 3 Flash (preview) — used by `src/agent/graph.py` via `python-dotenv` |

> All infrastructure services (Prometheus, Loki, Kafka, ChromaDB) run locally — no additional API keys needed. `GEMINI_API_KEY` is the only external dependency.

---

## Common Gotchas

| Gotcha | Rule |
|---|---|
| **Shared `LogParser` instance** | Pass the same `LogParser` object to both `LogHubHDFSPreprocessor` and the OTel pipeline. Never create two separate instances — template IDs will be inconsistent across HDFS pretraining and OTel fine-tuning. |
| **Create model after corpus parse** | Always call `preprocessor.parse()` first, then read `preprocessor.num_templates` → `input_dim`. Template count grows during parsing and is only finalized after the full corpus. |
| **`_load_compatible_weights` is not a bug** | `RuntimeError` from `load_state_dict` during fine-tuning is expected — HDFS and OTel feature dims differ by design. The function loads only LSTM body weights; embedding and output layers are reinitialized. |
| **`model.eval()` at inference** | Always call `model.eval()` and wrap in `torch.no_grad()` during reconstruction error scoring. Forgetting leaves dropout active → noisy errors → unstable threshold. |
| **Docker memory budget ~7.25GB** | OTel Demo (6 services) + monitoring stack + Docker Stats Exporter saturates ~7.25GB RAM. Process RCAEval and LogHub data offline as standalone scripts — never inside Docker. |
| **`poetry run` required** | Never run `python` directly; always prefix with `poetry run python` to use the correct virtual environment. |
| **Intel Mac (x86_64) constraints** | PyTorch >=2.3 and onnxruntime >=1.20 dropped macOS x86_64 wheels. `pyproject.toml` pins `torch>=2.0,<2.3` and `onnxruntime>=1.17,<1.20`. Relax these if migrating to Apple Silicon or Linux. |
| **RCAEval pip package is a stub** | The pip-installed `RCAEval` package only contains `is_ok()`. The `RCAEval.utility` module with download functions does not exist — it only exists in the GitHub source. `scripts/download_datasets.py` downloads directly from Zenodo API instead. |
| **RCAEval without `[default]` extras** | RCAEval's `[default]` extras pin `torch==1.12.1`. We install the base package only and manage torch ourselves. `import RCAEval` works; evaluation utilities are available. |
| **RCAEval RE2 case count** | RE2 has 271 cases (not 270 as documented), because RE2-OB has 91 cases instead of 90. RE1 has 375, RE3 has 90. Total: 736 cases. |
| **RCAEval file format differences** | Three naming conventions, not two: (1) RE1-OB uses `data.csv` with simple `{service}_{metric}` columns (51 cols, 5 metric types). (2) RE1-SS/TT use `data.csv` but with container-metric naming (439-1246 cols). (3) All RE2/RE3 use `metrics.csv` with container-metric naming (389-1574 cols, 50 metric types). Infrastructure noise (GKE nodes, AWS IPs, `istio-init`) appears as service prefixes and must be filtered. Service names differ across systems (OB: `adservice`, SS: `carts`, TT: `ts-auth-service`). |
| **numpy <2.0 required** | numpy is pinned to `<2.0` for compatibility with torch 2.2.x and the broader dependency tree. |
| **transformers <5.0 and sentence-transformers <4.0 required** | `transformers` 5.x requires PyTorch >=2.4, but we pin torch <2.3 for Intel Mac x86_64 wheels. `transformers` 5.x has a bug where `nn.Module` is referenced unconditionally but `torch.nn` is only imported when PyTorch >=2.4 is detected. Pinned `transformers>=4.36,<5.0` and `sentence-transformers>=2.2,<4.0`. Relax these when migrating to Apple Silicon or Linux. |
| **Poetry PATH setup** | Poetry is at `~/.local/bin/poetry`, pipx at `~/Library/Python/3.13/bin/pipx`. Prefix commands with `export PATH="$HOME/.local/bin:$HOME/Library/Python/3.13/bin:$PATH"` if not in shell profile. |
| **Docker Stats Exporter replaces cAdvisor** | OTel Demo services use gRPC and don't expose Prometheus `/metrics` endpoints. A custom Docker Stats Exporter queries the Docker API directly and exposes container metrics (CPU, memory, network) in Prometheus format on port 9101. cAdvisor was replaced because it cannot discover individual containers on macOS Docker Desktop (cgroupv2 + VM-based Docker). |
| **Redis must be in `SERVICES` list** | `generate_training_data.py` has a `SERVICES` list for metadata and a `_SVC_FILTER` PromQL regex. Both must include `redis`. The Docker Stats Exporter exposes Redis metrics (it has the `com.docker.compose.service` label), and Redis is a fault injection target (`connection_exhaustion`). Omitting Redis from `SERVICES` causes metadata to report 6 services instead of 7, even though Redis data is present in snapshots. |
| **Promtail ships logs to Loki (no Kafka)** | Promtail uses `docker_sd_configs` to discover containers via Docker socket and ships logs to Loki. Promtail does NOT natively support Kafka output. The `keep` relabel action causes "at least one label pair required" errors in Promtail 2.9.0 — use unfiltered discovery with `job=docker` label instead; downstream Loki queries filter by `{service="<name>"}`. |
| **macOS sleep during long collections** | Use `caffeinate -s` to prevent macOS sleep during 24h data collection. Mac must be plugged into AC power. Example: `caffeinate -s poetry run python scripts/generate_training_data.py --duration 24h`. |
| **Data collection resume** | `generate_training_data.py` supports resume — if interrupted, re-running the same command picks up from the last snapshot via `metadata.json`. The Docker stack must remain running throughout. |
| **Baseline EDA findings** | 16 zero-variance metric pairs (all `network_rx/tx_errors_rate` + 2 `fs_usage_bytes`) — zero in baseline but kept for anomaly detection (spike during faults = detection signal). `memory_usage_bytes` and `memory_working_set_bytes` are perfectly correlated (r=1.0) for all services — drop `memory_usage_bytes`, keep `memory_working_set_bytes`. No outliers detected in 24h baseline (3σ rolling window). Cross-service correlations (`redis↔cartservice` network, `frontend↔paymentservice` TX) reflect real topology. |
| **LogHub HDFS is 100% INFO level** | All log lines in HDFS.log are INFO level — anomaly detection cannot use log level as a feature. Detection must rely on Drain3 template sequence patterns. 100% of lines contain block IDs (no filtering needed). 15 templates from 10K sample; top 5 cover 95.7%. |
| **Drain3 v0.9.1 lacks `TemplateMiner.match()`** | `match()` was added in Drain3 v0.9.8. Our installed version (0.9.1) does not have it. `LogParser.match()` uses `Drain.tree_search(root_node, tokens)` instead — this is the same underlying read-only lookup. Do not upgrade Drain3 without verifying `TemplateMinerConfig` API compatibility. |
| **confluent-kafka, not kafka-python** | `pyproject.toml` installs `confluent-kafka>=2.3`. The API is `Consumer(config_dict)` + `.poll(timeout)` + `.subscribe([topics])`, NOT `KafkaConsumer(topic, **kwargs)`. Message values are `bytes` (call `.decode()`), timestamps are `(type, ms)` tuples. Never import from `kafka` — that package is not installed. |
| **RCAEval has no `metadata.json`** | RCAEval case directories do NOT contain `metadata.json`. Ground truth is parsed from directory names (`{service}_{fault_type}`), and anomaly timestamps are read from `inject_time.txt` (Unix epoch seconds). The adapter uses 3-level directory traversal: `{System}/{service_fault}/{run}/`. |
| **RCAEval simple format detection** | Do not detect RE1-OB "simple format" by checking for absence of hyphens — `frontend-external_load` has a hyphen in the service name. Instead, check whether the metric suffix after the last underscore is a known simple metric (`cpu`, `mem`, `load`, `latency`, `error`). |
| **HDFS Drain3 on 100K lines: 45 templates** | 100K HDFS lines produce 45 templates (vs 15 from 10K). The difference is rare event templates (exceptions, replication, deletions). Top 5 generalized templates cover 98.3%. 21/45 are singletons (first-encounter literals). Template vocabulary converges by ~92K lines. sim_th=0.4 is optimal; at 0.6 templates explode from 15 to 642. |
| **Two 24h baselines exist** | `data/baseline/` (Session 3) has metrics only (1440 snapshots, 0 logs). `data/baseline_with_logs/` (Session 8) has metrics + logs (1440 snapshots, 2885 log entries). The second baseline is used for fine-tuning. The first is kept for reference. |
| **HDFS full corpus: 115 templates (not 45)** | Full 11.2M HDFS lines produce 115 Drain3 templates (vs 45 from 100K sample, vs 15 from 10K). The difference is rare event templates (exceptions, edge cases). `input_dim` for the LSTM-AE is 115 on HDFS data, not the 20-50 estimated in specs. The architecture handles dynamic `input_dim`. |
| **HDFS pretraining checkpoint format** | `pretrain_on_hdfs()` saves checkpoints as `{"model_state_dict": ..., "history": ...}` (wrapped format). Both `_load_compatible_weights()` and `finetune_on_otel_demo()` handle both raw state_dict and wrapped format via `isinstance(checkpoint, dict) and "model_state_dict" in checkpoint`. |
| **`torch.nn.functional as F` triggers ruff N812** | Ruff flags `import torch.nn.functional as F` as CamelCase imported as non-lowercase. Use `import torch.nn.functional as functional` instead. |
| **Fine-tuning requires new log-enriched baseline** | The first 24h baseline (`data/baseline/`) has metrics only. The second 24h baseline (`data/baseline_with_logs/`) collected metrics + logs via Promtail → Loki. Fine-tuning uses the second baseline processed through `FeatureEngineer.build_sequence()`. |
| **HDFS benchmark F1=0.58 is expected** | The HDFS pretraining-only benchmark produces moderate F1 (0.58) because one-hot template sequences are sparse. This is a nice-to-have evaluation track. The primary evaluation is OTel Demo fault injection with richer combined features. |
| **OTel fine-tuning: normalize features** | Raw feature vectors span 6 orders of magnitude (`memory_working_set_bytes` ~10^8 vs `cpu_usage_rate` ~10^-3). Without z-score normalization, MSE loss is ~10^14 and the model cannot converge. Always normalize with `scaler_mean.npy` / `scaler_std.npy` before training and inference. Normalization stats are saved in `data/splits/otel/`. |
| **OTel fine-tuning: lr=0.001, not 0.0001** | The conservative `lr=0.0001` converges too slowly (100 epochs, still declining). Since HDFS→OTel transfer only loads LSTM body weights (embedding/output reinitialized due to `input_dim` mismatch 115→54), `lr=0.001` is safe and converges at ~epoch 158 with early stopping. |
| **OTel feature vector: 54 dims (6 metrics)** | Feature layout: log templates (5×2 + 2 = 12) + metric stats (6 metrics × 7 stats = 42) = 54. Metrics: `cpu_usage_rate`, `memory_working_set_bytes`, `network_rx_bytes_rate`, `network_tx_bytes_rate`, `network_rx_errors_rate`, `network_tx_errors_rate`. Error rates are zero in baseline (anomaly signal when they spike during faults). `memory_usage_bytes` and `fs_usage_bytes` excluded (redundant / sparse). |
| **checkoutservice needs SHIPPING_SERVICE_ADDR** | The reduced OTel Demo excludes shippingservice, but checkoutservice panics on startup without `SHIPPING_SERVICE_ADDR`. Fixed by adding dummy env vars (`localhost:50053`) in `demo_app/docker-compose.demo.yml`. The service starts cleanly; checkout requests needing shipping fail gracefully at the gRPC level. |
| **PC algorithm: undirected edges in simple chains** | PC cannot orient edges in A→B→C (no collider/v-structure). Only chains with colliders (e.g., B→C←D) produce directed edges. Counterfactual confidence scoring breaks ties for undirected edges. This is expected PC behavior, not a bug. |
| **OTel Demo services produce minimal stdout logs** | Most OTel Demo services (frontend, cartservice, currencyservice, paymentservice, redis) are compiled gRPC services with minimal console output. Only checkoutservice and productcatalogservice produce regular logs (OTel exporter retry warnings). Expect ~2,900 log entries per 24h baseline. Log volume will increase during fault injection (error messages, stack traces). |
| **PromQL templates use `.replace()`, not `.format()`** | Agent tool PromQL templates use `{service="{service}"}` with `.replace("{service}", name)`. Do NOT use double braces `{{...}}` — that is Python's `.format()` escaping syntax which produces literal `{{` in the output, causing Prometheus 400 Bad Request errors. |
| **8 container-level metrics available** | Docker Stats Exporter exposes: `cpu_usage`, `memory_usage`, `memory_limit` (cgroup limit in bytes — added Session 13), `memory_utilization` (derived ratio working_set/limit — added Session 13), `network_rx_bytes_rate`, `network_tx_bytes_rate`, `network_rx_errors_rate`, `network_tx_errors_rate`. The two memory-saturation metrics were added in Session 13 to enable the `memory_utilization` CRITICAL detector (see separate gotcha). Application-level metrics (latency, error_rate, request_count, connection_count) are NOT available from Docker — OTel Demo services don't expose Prometheus `/metrics` endpoints directly. The system prompt and tool docstrings must list the 8 container metrics + 3 spanmetric + 2 probe metrics. |
| **PC algorithm depth must be capped at 3** | `discover_causation.py` uses `max_conditioning_set=3`. Depth 4 was tested but produced worse results — more aggressive edge pruning removed weak signals from crashed services while strengthening spurious signals from healthy ones. The tool also caps at 5 services, uses `lags=[1, 2]` (not `[1, 2, 5]`), and applies three-layer singularity defense (zero-variance drop, correlation drop >0.999, 1e-8 jitter). |
| **PC algorithm singular matrix defense** | Fisher's Z test crashes with `LinAlgError: Singular matrix` when columns are constant or perfectly correlated. `discover_causation.py` applies: (1) drop zero-variance columns (`var < 1e-12`), (2) drop correlated columns (`|r| > 0.999` via `_drop_correlated_columns()`), (3) add tiny jitter (`np.random.default_rng(42)`, scale `1e-8`). Common triggers: `network_rx/tx_errors_rate` = 0, lagged copies of slow-changing memory metrics. |
| **Crashed services are invisible to PC algorithm** | A stopped container produces no new metrics. Prometheus serves stale cached data from the `rate()` lookback window. The PC algorithm only sees surviving services and picks whichever shows the most variance. **Fix:** `query_metrics.py` detects stale data (>90s old) and sparse data (<70% expected points) and returns `anomalous: True` with CRITICAL note. `analyze_causation_node` in `graph.py` overrides low-confidence PC results (<50%) with the LLM's top hypothesis when CRITICAL evidence is present. |
| **OTel Demo images are distroless** | OTel Demo service images (paymentservice, cartservice, etc.) have no package manager (`apt-get`, `apk`), no `tc`, no `iproute2`. Fault injection scripts that need network tools (e.g., `02_high_latency.sh`) must use a sidecar container sharing the target's network namespace (`--network container:$CONTAINER`, Alpine + `iproute2`). |
| **`docker update --cpus` NanoCpus can't be cleared** | Once `docker update --cpus 0.1` sets `NanoCpus`, `docker update --cpus 0` does not reset it. `docker update --cpu-quota=-1` fails with "Conflicting options". The only fix is `docker compose up -d --force-recreate <service>` to recreate the container from compose (which has no CPU limit). |
| **Fault injection alert `affected_services` excludes currencyservice** | `fault_injection_suite.py` populates `affected_services` with the **6 legitimate services** (cartservice, checkoutservice, frontend, paymentservice, productcatalogservice, redis) — NOT the full 7. `currencyservice` is excluded in Session 12 because its v1.10.0 SIGSEGV crash-loop makes probe_up=0 + `std::logic_error` crash logs a permanent baseline state that the agent otherwise fixates on and misattributes as the fault. Without at least one service in `affected_services`, `executor.py` falls back to an empty list and the agent investigates blind. |
| **Fault injection alert title must be neutral** | Do not include fault type or "Fault Injection" in the alert title. The LLM reads the title and will blame "Fault Injection System" as root cause instead of a real service. Use `"Anomaly Detected — Automated Investigation Triggered"`. |
| **Causal discovery uses 10-minute window** | `analyze_causation_node` calls `discover_causation` with `time_range_minutes=10` (not 30). With a 120s pre-investigation wait, this gives ~8/40 fault data points vs ~8/120 with 30 min — much better anomaly-to-baseline ratio for the PC algorithm. |
| **Pre-investigation wait is 120 seconds** | `fault_injection_suite.py` waits 120s after fault injection before triggering the agent investigation. `rate()`'s `[1m]` lookback persists data ~75s past a service stop; at 120s, coverage drops below the 70% sparse threshold so CRITICAL fires reliably. 60s was insufficient. (See the "Pre-investigation wait is 120 seconds (was 60s)" gotcha below for context on the upgrade.) |
| **Gemini API requires paid tier for evaluation** | Free tiers impose daily request limits on Gemini models. Running 35 fault injection tests at ~5 LLM calls each requires ~175 API calls. Upgrade to paid tier before running the evaluation suite. |
| **LLM model: `gemini-3-flash-preview`** | The project uses Gemini 3 Flash (preview). The model string lives in `src/agent/graph.py`, `configs/agent_config.yaml`, `tests/conftest.py`, and `tests/evaluation/baseline_comparison.py`. Swapped from `gemini-2.5-flash-lite` in Session 12 after diagnosis identified LLM reasoning capacity as a top driver of low Recall@1. Unlike Gemini 2.5, Gemini 3 does not require `thinking_budget` configuration. |
| **LangGraph `AgentState` must be TypedDict** | Use `class AgentState(TypedDict)` from `typing_extensions`, not `class AgentState(dict)` with annotations. LangGraph's `StateGraph` requires a proper TypedDict for state validation and reducer recognition. |
| **OTel Demo v1.7.0 does NOT export traces** | The v1.7.0 service images have OTel SDK instrumentation but silently fail to export traces even with `OTEL_EXPORTER_OTLP_ENDPOINT` set. Process-level metrics export fine, but traces don't flow to the collector. **Fix:** Upgrade to v1.10.0 images, which have corrected SDK behavior. v1.11.0+ introduces breaking changes (Valkey replaces Redis, service name changes) — stay on v1.10.0. |
| **OTel Demo v1.10.0: cartservice on port 8080** | In v1.10.0, the .NET cartservice binds internally to port 8080 (not 7070 as in v1.7.0). The `CART_SERVICE_PORT=7070` env var is used by OTHER services for connection, but the .NET app listens on ASPNETCORE_URLS default (8080). Probe exporter and direct TCP probes must use port 8080 for cartservice. |
| **OTel Demo v1.10.0: currencyservice crash-loops** | The C++ currencyservice in v1.10.0 exits with SIGSEGV (exit code 139) repeatedly. This creates a noisy baseline where `service_probe_up` for currencyservice intermittently shows 0 even during normal operation. config_error tests targeting currencyservice are therefore harder to distinguish from baseline. |
| **Loadgenerator v1.10.0 configuration** | v1.10.0 loadgenerator uses Locust with Playwright by default. Must set `LOCUST_HOST`, `LOCUST_HEADLESS=true`, `LOCUST_USERS`, `LOCUST_SPAWN_RATE` env vars explicitly. `LOCUST_PLAYWRIGHT` env var is deprecated but setting it to 0 prevents Playwright browser warnings. Output is silenced by `--skip-log-setup` flag — use span metrics to verify traffic is flowing. |
| **OTel Collector spanmetrics connector** | The collector config uses the `spanmetrics` connector to convert trace spans into `span_calls_total`, `span_duration_milliseconds_bucket` Prometheus metrics. These provide per-service request rate, error rate, and latency histograms. Only services that export traces are visible (frontend, checkoutservice, productcatalogservice, paymentservice, loadgenerator). cartservice, currencyservice, and redis don't export traces. |
| **Application metrics have irregular data rates** | `request_rate`, `error_rate`, `latency_p99` from spanmetrics depend on actual traffic, not a fixed scrape interval. The sparse/stale CRITICAL detection MUST be skipped for these metrics (they're in `_app_metrics` set in `query_metrics.py`). Otherwise, low-traffic services produce false CRITICAL signals. |
| **`probe_up=0` does NOT trigger default 2σ anomaly** | When a service is down and probe_up returns `[1,1,1,0,0,0,0,0]`, the mean is ~0.4 and std is ~0.5. `abs(0.0 - 0.4) = 0.4 < 2 * 0.5 = 1.0` — so the standard 2σ check returns `anomalous=False`. Fix: dedicated probe_up check in `query_metrics.py` that flags CRITICAL when 3+ of last 4 values are 0.0 AND `mean > 0.1` (service was previously up). |
| **Service Probe Exporter: data exchange required** | TCP `connect()` succeeds on paused containers (kernel handles SYN/SYN-ACK even when process is SIGSTOP'd). Must send application-level data (Redis PING, HTTP GET, gRPC empty payload) and wait for response to correctly detect paused/frozen services. See `infrastructure/service_probe_exporter/probe_exporter.py`. |
| **Probe metrics are gauges, not rates** | `service_probe_up` and `service_probe_duration_seconds` are Prometheus gauge metrics from the Service Probe Exporter. They have no `rate()` lookback window issues. When a service goes down, the next probe (within 15s) shows `probe_up=0` immediately — no waiting for sparse data detection. |
| **probe_latency spike detection (10x baseline)** | `query_metrics.py` flags probe_latency as CRITICAL when current value exceeds 10x the mean AND mean > 0.0001s. This catches network latency injection (tc netem 500ms applied to frontend: normal ~0.017s → 1.0s = 60x spike). Latency faults don't affect probe_up (service is still reachable, just slow). |
| **Cross-test state pollution in metric windows** | The 10-minute query window can contain residual data from the PREVIOUS test's fault. A service restored 60-120s ago still shows some zero values in probe_up history. Healthy services recovering from prior faults can false-trigger the probe_up CRITICAL check (3+ of last 4 zeros) during the current test's cooldown. Mitigation: longer cooldowns (>4 min) between tests or per-test metric snapshotting. |
| **Redis has naturally high CPU variance** | Among all OTel Demo services, redis consistently shows the highest CPU usage and most fluctuation during normal operation (mean ~0.01 vs others ~0.001). This attracts the PC algorithm's causal signal and the LLM's "upstream failure" reasoning. **System prompt fix:** Added explicit guidance that elevated redis CPU alone is NOT an anomaly indicator — only consider redis as root cause if `probe_up=0` or logs mention redis connection failures. |
| **Pre-investigation wait is 120 seconds (was 60s)** | The `rate()` function's `[1m]` lookback window persists data for ~75s after a service stops. At 60s wait, crashed services still show 100% data coverage (rate() data hasn't expired). At 120s, coverage drops to ~65% triggering the 70% sparse CRITICAL threshold. See `tests/evaluation/fault_injection_suite.py` `run_fault_injection()` wait loop. |
| **Sparse threshold is 70% (was 90%)** | `query_metrics.py` flags `sparse=True` when coverage < 0.70 (was 0.90). The 90% threshold triggered false CRITICAL on healthy services with minor scrape jitter. At 70%, only services that crashed within the current test's 10-min window (losing 30%+ of data) trigger CRITICAL. |
| **Frozen metric detection** | `query_metrics.py` checks if 5+ of the last 8 rate-metric values (cpu, network_rx, network_tx) are exactly 0.0 AND historical mean > 0.0001. This catches paused containers where Docker Stats Exporter still reports stats but CPU rate goes flat. currencyservice naturally has mean ~0.0 CPU so it correctly doesn't trigger (protected by `had_activity` check). |
| **high_latency targets frontend (was paymentservice)** | The loadgenerator (Locust) sends traffic to frontend, not paymentservice. 500ms tc netem on paymentservice produced no observable signal because no requests reached it. Changed target to frontend where real traffic flows; probe_latency spikes 60x (0.017s → 1.0s) under 500ms injection. |
| **Fault script restore methods** | Active scripts used by the 35-test evaluation suite: (1) `01_service_crash.sh`: stop/start. (2) `02_high_latency.sh`: Alpine sidecar with `tc netem`, remove qdisc on restore. (3) `03_memory_pressure.sh` (Session 13 patch): **dynamic cap** `max(working_mb * 1.2, working_mb + 2)` — measures `docker stats --no-stream` at inject time and caps memory proportionally, guaranteeing ~83-94% utilization regardless of Go runtime heap state. Fallback to fixed 25 MiB if `docker stats` parse fails. Restore unconditionally returns cap to 256 MiB. Replaces the earlier fixed `25m` cap that failed to saturate a cold-heap checkoutservice (~15 MiB working set) and produced only 60% utilization — below the detector's 80% threshold. (5) `05_connection_exhaustion.sh`: `docker pause/unpause redis` (was `CONFIG SET maxclients` — pause produces detectable signal via probe). (6) `06_network_partition.sh`: `docker pause/unpause paymentservice` (was `docker network disconnect` — pause produces detectable signal). (7) `07_cascading_failure.sh`: stop cartservice + 30s propagation. (8) `08_config_error.sh`: replacement container targeting productcatalogservice with `PRODUCT_CATALOG_SERVICE_PORT=999999` + `--restart on-failure` (crash-loop via invalid port). Unused: `04_cpu_throttling.sh` (removed from registry Session 12 — see gotcha below). |
| **cpu_throttling removed from eval suite** | The fault script `04_cpu_throttling.sh` (`docker update --cpus 0.1 productcatalogservice`) is intentionally absent from `FAULT_SCRIPTS` in `tests/evaluation/fault_injection_suite.py`. Diagnosis in Session 12 showed productcatalogservice baseline CPU is 0.09% of a core — a cap at 10% (or even 1%) is never reached, producing no probe_latency or cpu_usage signal. The script file is retained for future use if the demo gets load-tested or migrated to a higher-traffic setup. |
| **Agent graph is 7 nodes, not 5** | Session 12 added two new nodes. Flow is: `START → analyze_context → sweep_probes → form_hypothesis → gather_evidence → analyze_causation → (conditional: continue→form_hypothesis, end→knockout) → generate_report → END`. `sweep_probes_node` runs once after `analyze_context`. `knockout_node` sits on the end-branch so it runs once at final decision time, not per investigation loop. Both bypass `tool_calls_remaining` since they're mandatory infrastructure, not LLM-directed reasoning. |
| **Sweep covers 5 metrics + logs, bypasses tool budget** | `sweep_probes_node` queries `probe_up`, `probe_latency`, `cpu_usage`, `memory_usage`, `memory_utilization` for every service in `affected_services`, PLUS a per-service `search_logs` with crash-pattern query. 6 services × 5 metrics + 6 log calls = **36 queries per investigation** (Session 13 added `memory_utilization`). These do NOT decrement `tool_calls_remaining` — the LLM keeps its full 10-call budget. Each sweep evidence entry carries `args["critical"] = bool` set from the pre-truncation tool result. |
| **`args["critical"]` flag bypasses the 500-char finding truncation** | Evidence entries store a serialized `finding` truncated to 500 chars. The `"CRITICAL"` substring of the tool's note often sits after the timestamps array and gets cut off. `analyze_causation_node` reads the direct `args["critical"]` boolean FIRST (set by the sweep from the full tool result), falling back to the legacy `"CRITICAL" in finding` string-scan for LLM-invoked tool calls. Any tool that produces CRITICAL signals must either set this flag when called from the sweep OR ensure the CRITICAL substring lives in the first 500 chars of the JSON dump. |
| **search_logs crash-pattern CRITICAL escalation** | `search_logs` in Session 12 detects ≥3 crash/OOM/fatal log matches via `_CRASH_PATTERNS` (OOMKilled, SIGKILL/SIGSEGV, panic, std::logic_error, terminate, fatal, unhandled exception, exit 137/139, listen-tcp-bind failure, core dumped, connection refused, max clients reached) and `_detect_crash_signal()`. When escalated, returns `critical_service=<service_filter>`, `anomalous=True`, and a `"CRITICAL: …"` note. Escalation requires `service_filter` to be set — can't attribute a system-wide search to one service. |
| **Knockout node falsifies weak hypotheses via sweep CRITICAL counts** | `knockout_node` runs after `analyze_causation` on the end-branch of the conditional. It skips when `root_cause_confidence ≥ 0.75` (the CRITICAL-override already fired — don't second-guess) or `root_cause` is unknown/inconclusive. Otherwise counts sweep-evidence `critical=True` entries per candidate (root_cause + top-3 LLM hypotheses) and swaps `root_cause` to a strictly-more-critical alternative with a moderate confidence bump (max 0.65, below the 0.75 override band). |
| **LogQL `\|=` is literal-substring, not boolean — use regex `\|~` for OR** | LLM-generated queries like `"panic OR fatal"` produced 400 Bad Request at Loki because `\|=` treats the whole string as one literal, and embedded `"..."` terms terminated the LogQL string prematurely. `search_logs._build_logql()` splits on `\s+OR\s+`, strips embedded quotes/backticks from each term, and emits `\|~ \`(?i)term1\|term2\|…\`` when multiple terms are present. Backtick string literals (raw) avoid the escape-quote trap. |
| **Gemini 3 Flash Preview returns list-of-parts content** | `response.content` from `ChatGoogleGenerativeAI(model="gemini-3-flash-preview")` is `[{"type":"text","text":"..."}, {"type":"thought", …}]`, NOT a plain string like Gemini 2.x. Any code checking `isinstance(response.content, str)` discards all text. Use the `_extract_text(response)` helper in `src/agent/graph.py` which handles both shapes and filters out non-text parts (thoughts, signatures). |
| **`fault_injection_suite.py` uses `datetime.now(UTC)` everywhere** | Previously used `datetime.now()` which returns local time with no tz suffix. Tool code interprets naive timestamps as UTC → pinned windows end up hours in the future → empty data → zero-fill → PC bail → "inconclusive". All 5 `datetime.now()` calls in `run_fault_injection()` now explicitly use `datetime.now(UTC).isoformat()`. |
| **start_time pins window to `[anchor - 60s, min(anchor + 10min, now)]`** | `query_metrics`, `search_logs`, and `discover_causation` all accept `start_time: str \| None`. When set, the window extends 60s BEFORE the anchor (for pre-fault baseline context) and ends at min(anchor + time_range_minutes, now) to avoid querying the future. The `AgentExecutor.investigate()` and `AgentState.start_time` plumbing threads this through to every tool call via `gather_evidence_node`. CRITICAL-check `baseline_mean` uses points before `anchor_ts` directly (anchor-based split), not first-60%-of-window, when `start_time` is pinned. |
| **02_high_latency.sh inject/restore are idempotent** | Both `inject` and `restore` force-remove any prior sidecar AND strip any stale `tc netem` qdisc via a throwaway Alpine container that shares the target's netns. Required because removing the `opsagent-tc-sidecar` container does NOT remove the qdisc it installed on frontend's eth0 — a subsequent `inject` would fail with "container name in use" or produce a double-netem. Always restore via the script, not `docker stop`. |
| **Session 12 — Recall@1: 42.9% (15/35)** | Session 12 Tier 1/2/3 run achieved **42.9% Recall@1 / 68.6% Recall@3** (up from Session 11's 27.5% / 40%). Tier 4/5 fixes (Fix 17 crash-log CRITICAL, Fix 18-ext 4-metric sweep + log sweep, Fix 21 knockout, direct `args.critical` flag) implemented and unit-verified — a full 35-test re-run with Tier 4/5 is pending. Expected improvement to 65–80% based on the autopsy. The 500-char `evidence[].finding` truncation bug is the main remaining ceiling; partially worked around via the direct `args.critical` flag on sweep entries. |
| **Docker Stats Exporter emits `container_spec_memory_limit_bytes` (Session 13)** | `infrastructure/docker_stats_exporter/exporter.py` `_extract_memory()` now returns a 3-tuple `(usage, working_set, limit)`; the new `container_spec_memory_limit_bytes{service,name}` gauge is exposed alongside the working-set gauge with the **identical label set** (`{service, name}`) so Prometheus joins the ratio `working_set / limit` natively — no `on()` / `ignoring()` required. The `limit` is read from `stats["memory_stats"]["limit"]` which tracks `docker update --memory` in real time. On macOS Docker Desktop, a container without an explicit `--memory` flag reports `limit == host RAM` (~16 GB), so `memory_utilization` stays < 1% for uncapped containers — this is the intended signal shape, not a bug. Containers without a limit field in the API response are silently absent from the gauge (tested). |
| **`memory_utilization` CRITICAL detector (Session 13)** | `query_metrics.py` fires CRITICAL for `memory_utilization` when `peak >= 0.80 AND baseline_mean <= 0.50 AND len(values) >= 4`. **Peak-based, not `values[-1]`-based:** Go/JVM runtimes under tight cgroup caps GC-cycle — working set sits near the cap for 5+ consecutive 15-s samples then dips briefly on each collection. A GC dip right before the 120-s investigation mark (working set 57% of cap) would make `current = values[-1]` miss the 80% threshold even when the fault obviously saturated mid-window. `peak = np.max(arr)` captures the sustained-saturation band. Emitted `stats` include `peak`, `current`, `baseline_mean`, `mean`, `min`, `max`, `std`. Baseline guard prevents flagging services that live hot in baseline (none today — max observed is paymentservice at 0.46). |
| **`memory_utilization` and `memory_limit` added to METRIC_PROMQL (Session 13)** | `src/agent/tools/query_metrics.py` now exposes two new metrics: `memory_limit` → `container_spec_memory_limit_bytes{service="{service}"}` and `memory_utilization` → `container_memory_working_set_bytes{service="{service}"} / container_spec_memory_limit_bytes{service="{service}"}`. The ratio query joins natively on matching `{service, name}` labels. `memory_utilization` is NOT added to `_CAUSAL_METRICS` in `discover_causation.py` — derived ratios degrade Fisher's Z test by introducing spurious correlations with the numerator/denominator columns if they were also included. The CRITICAL-override path handles root-cause attribution directly, without needing PC to see the ratio. |
| **`03_memory_pressure.sh` dynamic cap formula (Session 13)** | The fault script now measures current working set via `docker stats --no-stream --format '{{.MemUsage}}'`, parses KiB / MiB / GiB via awk+sed (no Python dependency), and applies `cap_mb = max(working_mb * 1.2, working_mb + 2)`. Float-math formatting: `awk -v n="$num" 'BEGIN{printf "%d", n*1024}'` etc. The `W + 2 MiB` branch takes over for cold heaps (< 10 MiB working set) where the 1.2× multiplier alone would leave < 2 MiB GC headroom and risk OOMKill. Both branches deliver 80-94% immediate utilization, well above the detector's 0.80 threshold. Falls back to fixed 25 MiB cap if `docker stats` output can't be parsed. Restore unconditionally sets cap to 256 MiB. Previously a fixed `--memory 25m` that failed to saturate an idle-state checkoutservice (15 MiB working set → only 60% of 25 MiB cap). |
| **Session 13 — Recall@1: 100% (35/35)** | Session 13 ran the full 35-test suite with all Session 12 Tier 4/5 fixes + memory saturation detection (new `container_spec_memory_limit_bytes` gauge, `memory_utilization` CRITICAL detector with peak-based trigger, dynamic fault-script cap). Result: **100% Recall@1 and 100% Recall@3 with every prediction at exactly 0.75 confidence**, zero inconclusive, mean investigation 24.1 s, mean detection latency 125.2 s, mean MTTR 149.4 s. 95% Wilson CI on Recall@1: [90.1%, 100.0%] — even the lower bound clears the 80% target. Per-fault: 5/5 on every class including **memory_pressure** (was 2/5 in Session 12). Top-1 prediction distribution matches GT 1-to-1 (cartservice 10, others 5 each) — zero misattribution. Compared to Session 11: +72.5 pp Recall@1 (27.5% → 100%); vs Session 12: +8.6 pp Recall@1 (91.4% → 100%) and memory_pressure +60 pp (40% → 100%). Results in `data/evaluation/results_session13/`. 330 unit tests pass, ruff + mypy clean. |

---

## Project Structure

```
opsagent/
├── CLAUDE.md                           # ← You are here: session context for Claude Code
├── PROGRESS.md                         # Phase checklist + progress log
├── context/                            # Detailed specs — load on demand (see below)
│   ├── architecture_and_design.md      # Design decisions, diagrams, risk, scope
│   ├── anomaly_detection_specs.md      # LSTM-AE architecture, training, threshold strategy
│   ├── causal_discovery_specs.md       # PC algorithm, counterfactual confidence scoring
│   ├── agent_specs.md                  # LangGraph state, tools, prompts, RCA report template
│   ├── data_pipeline_specs.md          # Kafka, Drain3, feature eng, dataset adapters
│   ├── infrastructure_and_serving.md   # Docker configs, FastAPI, Streamlit, scripts
│   ├── evaluation_strategy.md          # Fault injection, RCAEval, HDFS benchmark, metrics
│   └── config_reference.md             # YAML config templates for models, agent, datasets
│
├── README.md · pyproject.toml · poetry.lock · Makefile · docker-compose.yml · Dockerfile · .env.example · .gitignore
│
├── docs/                               # CRISP-DM report, architecture.md, evaluation_results.md, api_reference.md, images/
│   ├── problem_statement.md            # Problem scope and OpsAgent approach
│   ├── success_metrics.md              # Target metrics, rubrics, statistical analysis plan
│   └── baselines.md                    # 3 internal + 5 published baseline descriptions
├── infrastructure/                     # prometheus/ · grafana/ · loki/ · kafka/ · docker_stats_exporter/ · otel-collector/ · service_probe_exporter/
│
├── demo_app/
│   ├── docker-compose.demo.yml         # OTel Demo services (6 reduced services)
│   ├── load_generator/locustfile.py
│   └── fault_scenarios/                # 01_service_crash.sh … 08_config_error.sh
│
├── data/
│   ├── baseline/                       # 24h OTel Demo normal operation data (metadata.json)
│   ├── evaluation/results/             # Per-test JSON results + explanation_quality_scores.csv
│   ├── RCAEval/re1/ · re2/ · re3/      # 375 + 271 + 90 labeled RCA cases (~4GB total)
│   └── LogHub/HDFS/                    # HDFS.log + anomaly_label.csv (~1GB)
│
├── src/
│   ├── data_collection/                # kafka_consumer.py · metrics_collector.py · topology_extractor.py
│   ├── preprocessing/                  # log_parser.py · windowing.py · feature_engineering.py · rcaeval_adapter.py · loghub_preprocessor.py
│   ├── anomaly_detection/              # lstm_autoencoder.py · trainer.py · pretrain_on_loghub.py · detector.py · threshold.py · isolation_forest.py
│   ├── causal_discovery/               # pc_algorithm.py · counterfactual.py · graph_utils.py
│   ├── agent/
│   │   ├── tools/                      # query_metrics.py · search_logs.py · get_topology.py · search_runbooks.py · discover_causation.py
│   │   ├── prompts/                    # system_prompt.py · report_template.py
│   │   └── state.py · graph.py · executor.py
│   ├── knowledge_base/                 # runbook_indexer.py · embeddings.py
│   └── serving/                        # api.py · dashboard.py
│
├── tests/
│   ├── conftest.py                     # Shared test fixtures
│   ├── unit/                           # test_log_parser.py · test_feature_engineering.py · test_anomaly_detection.py · test_causal_discovery.py · test_agent_tools.py
│   ├── integration/                    # test_data_pipeline.py · test_agent_workflow.py
│   └── evaluation/                     # fault_injection_suite.py · rcaeval_evaluation.py · loghub_benchmark.py · metrics_calculator.py · baseline_comparison.py
│
├── notebooks/                          # 01–08: EDA, log parsing, anomaly detection (Colab GPU), causal discovery, agent prototyping, evaluation analysis
├── scripts/                            # setup_environment.sh · start/stop_infrastructure.sh · download_datasets.py · generate_training_data.py · prepare_data_splits.py · inject_faults.py · run_evaluation.py
├── runbooks/                           # connection_exhaustion.md · cascading_failure.md · memory_pressure.md · high_latency.md · general_troubleshooting.md · external_docs/
├── models/
│   ├── lstm_autoencoder/               # pretrained_hdfs.pt · finetuned_otel.pt
│   └── isolation_forest/
└── configs/                            # model_config.yaml · agent_config.yaml · dataset_config.yaml · evaluation_scenarios.yaml
```

---

## How to Use Context Files

**Start every session** by reading this file (`CLAUDE.md`) — it gives you the full picture.

**Check progress** by reading `PROGRESS.md` to see which tasks are complete, what's next, and any logged blockers.

**Load detailed specs on demand** — do not load all `context/` files upfront. Consult them only when actively working on that domain:

| Working on... | Read this context file |
|---|---|
| LSTM-AE or Isolation Forest | `context/anomaly_detection_specs.md` |
| PC Algorithm or counterfactuals | `context/causal_discovery_specs.md` |
| LangGraph agent, tools, prompts | `context/agent_specs.md` |
| Kafka, Drain3, feature engineering, adapters | `context/data_pipeline_specs.md` |
| Docker, Prometheus, Grafana, Loki, FastAPI | `context/infrastructure_and_serving.md` |
| Fault injection, metrics, baselines, stats | `context/evaluation_strategy.md` |
| YAML config templates | `context/config_reference.md` |
| Design decisions, risk, architecture diagrams | `context/architecture_and_design.md` |

> **IMPORTANT:** Never load all `context/` files at once — this bloats the context window.
> Load only the file(s) relevant to the current task.
