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
| **Metrics** | Prometheus + Grafana | Metrics collection and visualization |
| **Logs** | Loki + Kafka | Log aggregation and stream ingestion |
| **Log Parsing** | Drain3 | Template extraction from raw logs |
| **Feature Engineering** | Pandas + NumPy | Windowed aggregations and feature vectors |
| **Topology** | NetworkX | Service dependency graph |
| **Vector DB** | ChromaDB + sentence-transformers | Runbook similarity search |
| **Anomaly Detection** | PyTorch (LSTM-Autoencoder) | Primary: log sequence anomaly scoring |
| **Anomaly Baseline** | scikit-learn (Isolation Forest) | Comparison baseline |
| **Causal Discovery** | causal-learn | PC Algorithm for root cause graph |
| **Agent Orchestration** | LangGraph | Stateful multi-step investigation graph |
| **LLM** | Gemini 1.5 Flash | Agent reasoning and report generation |
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
| **RCAEval** RE1/RE2/RE3 (Zenodo DOI: 10.5281/zenodo.14590730) | 735 labeled failure cases | Cross-system RCA validation; comparison against 5 published baselines |

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
| RCAEval Recall@1 (RE2) | Competitive with CIRCA / RCD | 270-case cross-system validation |

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
make infra-up                         # Start Docker stack (Prometheus, Grafana, Loki, Kafka)
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
| `GEMINI_API_KEY` | **Yes** | Gemini 1.5 Flash — used by `src/agent/graph.py` via `python-dotenv` |

> All infrastructure services (Prometheus, Loki, Kafka, ChromaDB) run locally — no additional API keys needed. `GEMINI_API_KEY` is the only external dependency.

---

## Common Gotchas

| Gotcha | Rule |
|---|---|
| **Shared `LogParser` instance** | Pass the same `LogParser` object to both `LogHubHDFSPreprocessor` and the OTel pipeline. Never create two separate instances — template IDs will be inconsistent across HDFS pretraining and OTel fine-tuning. |
| **Create model after corpus parse** | Always call `preprocessor.parse()` first, then read `preprocessor.num_templates` → `input_dim`. Template count grows during parsing and is only finalized after the full corpus. |
| **`_load_compatible_weights` is not a bug** | `RuntimeError` from `load_state_dict` during fine-tuning is expected — HDFS and OTel feature dims differ by design. The function loads only LSTM body weights; embedding and output layers are reinitialized. |
| **`model.eval()` at inference** | Always call `model.eval()` and wrap in `torch.no_grad()` during reconstruction error scoring. Forgetting leaves dropout active → noisy errors → unstable threshold. |
| **Docker memory budget ~7GB** | OTel Demo (6 services) + monitoring stack saturates ~7GB RAM. Process RCAEval and LogHub data offline as standalone scripts — never inside Docker. |
| **`poetry run` required** | Never run `python` directly; always prefix with `poetry run python` to use the correct virtual environment. |
| **Intel Mac (x86_64) constraints** | PyTorch >=2.3 and onnxruntime >=1.20 dropped macOS x86_64 wheels. `pyproject.toml` pins `torch>=2.0,<2.3` and `onnxruntime>=1.17,<1.20`. Relax these if migrating to Apple Silicon or Linux. |
| **RCAEval without `[default]` extras** | RCAEval's `[default]` extras pin `torch==1.12.1`. We install the base package only and manage torch ourselves. `import RCAEval` works; evaluation utilities are available. |
| **numpy <2.0 required** | numpy is pinned to `<2.0` for compatibility with torch 2.2.x and the broader dependency tree. |
| **Poetry PATH setup** | Poetry is at `~/.local/bin/poetry`, pipx at `~/Library/Python/3.13/bin/pipx`. Prefix commands with `export PATH="$HOME/.local/bin:$HOME/Library/Python/3.13/bin:$PATH"` if not in shell profile. |

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
│   ├── RCAEval/re1/ · re2/ · re3/      # 375 + 270 + 90 labeled RCA cases (~4GB total)
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
