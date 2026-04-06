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
| **Logs** | Loki + Kafka | Log aggregation and stream ingestion |
| **Log Parsing** | Drain3 | Template extraction from raw logs |
| **Feature Engineering** | Pandas + NumPy | Windowed aggregations and feature vectors |
| **Topology** | NetworkX | Service dependency graph |
| **Vector DB** | ChromaDB + sentence-transformers | Runbook similarity search |
| **Anomaly Detection** | PyTorch (LSTM-Autoencoder) | Primary: log sequence anomaly scoring |
| **Anomaly Baseline** | scikit-learn (Isolation Forest) | Comparison baseline |
| **Causal Discovery** | causal-learn | PC Algorithm for root cause graph |
| **Agent Orchestration** | LangGraph | Stateful multi-step investigation graph |
| **LLM** | Gemini 2.5 Flash Lite | Agent reasoning and report generation |
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
| **OpenTelemetry Demo** (self-generated via fault injection) | ~24h baseline + 40 fault tests | Primary training data and controlled evaluation with known ground truth |
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
| `GEMINI_API_KEY` | **Yes** | Gemini 2.5 Flash Lite — used by `src/agent/graph.py` via `python-dotenv` |

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
| **Only 6 container-level metrics available** | Docker Stats Exporter exposes only container-level metrics: `cpu_usage`, `memory_usage`, `network_rx_bytes_rate`, `network_tx_bytes_rate`, `network_rx_errors_rate`, `network_tx_errors_rate`. Application-level metrics (latency, error_rate, request_count, connection_count) are NOT available — OTel Demo services don't expose Prometheus `/metrics` endpoints. The system prompt and tool docstrings must list only these 6 metrics. |
| **PC algorithm depth must be capped for >15 columns** | With 32 columns (4 metrics × 2 services × 4 lag levels), unrestricted PC takes 30+ minutes at depth 5–6 (C(30,5) = 142,506 conditioning sets per edge pair). Always pass `max_conditioning_set=4` to `discover_causal_graph()`. Depth 4 is sufficient for OTel Demo causal chains (longest = 3 hops) and completes in <30 seconds. |
| **LLM model: `gemini-2.5-flash-lite`** | The project uses Gemini 2.5 Flash Lite (stable). The model string is `gemini-2.5-flash-lite` in `src/agent/graph.py` and `configs/agent_config.yaml`. Do not use preview models (e.g., `gemini-3.1-flash-lite-preview`) in production code. |
| **LangGraph `AgentState` must be TypedDict** | Use `class AgentState(TypedDict)` from `typing_extensions`, not `class AgentState(dict)` with annotations. LangGraph's `StateGraph` requires a proper TypedDict for state validation and reducer recognition. |

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
├── infrastructure/                     # prometheus/ · grafana/ · loki/ · kafka/  (configs)
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
