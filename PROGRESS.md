# OpsAgent — Progress Tracker

> **How to use:** Check off tasks as they are completed. Update the Progress Log at the end
> of every Claude Code session. Consult `CLAUDE.md` for project context and `context/` files
> for implementation specs before starting each phase.

---

## Phase 1 — Business Understanding (Week 1)

**Goal:** Define problem scope, success criteria, and get proposal approved.

### Setup & Documentation
- [x] Create `docs/problem_statement.md`
- [x] Create `docs/success_metrics.md` with Recall@1, Precision, Detection Latency, RCAEval targets
- [x] Create `docs/baselines.md` documenting all 4 baseline approaches
- [x] Submit project proposal to advisor; receive approval

### Repository Initialization
- [x] Run `git init` and create full directory structure (see `CLAUDE.md` for tree)
- [x] Initialize Poetry environment (`pyproject.toml`); install all dependencies including `RCAEval` package
- [x] Create `.env.example`, `.gitignore` (include `data/`, `models/*.pt`, `.env`)
- [x] Create `README.md` with project overview and hybrid data strategy description
- [x] Create stub `Makefile` with `setup`, `test`, `run` targets
- [x] Create blank config stubs: `configs/model_config.yaml`, `agent_config.yaml`, `dataset_config.yaml`, `evaluation_scenarios.yaml`

### ✅ Phase 1 Complete When:
- Proposal approved by advisor
- `poetry install` succeeds and `import RCAEval` works
- All directory structure created including `data/RCAEval/` and `data/LogHub/HDFS/`

---

## Phase 2 — Data Understanding (Weeks 2–3)

**Goal:** Stand up infrastructure, deploy OTel Demo, collect baseline data, download and explore real-world datasets.

### Infrastructure (Week 2)
- [x] Create `docker-compose.yml` with Prometheus, Grafana, Loki, Zookeeper, Kafka, Docker Stats Exporter
- [x] Create `infrastructure/prometheus/prometheus.yml` (scrape Docker Stats Exporter for container-level metrics)
- [x] Create `infrastructure/loki/loki-config.yml`
- [x] Create `infrastructure/grafana/provisioning/datasources/datasources.yml` (Prometheus + Loki)
- [x] Create `demo_app/docker-compose.demo.yml` (6 OTel services + Redis + loadgenerator)
- [x] Create `scripts/start_infrastructure.sh` and `scripts/stop_infrastructure.sh`
- [x] Verify full stack: Grafana shows metrics at `localhost:3000`; Prometheus targets healthy; Docker Stats Exporter scraping all containers
- [x] Create `scripts/generate_training_data.py` and start 24h OTel Demo baseline collection

### Dataset Acquisition & EDA (Week 3)
- [x] Create `scripts/download_datasets.py` (with `--rcaeval`, `--loghub`, `--all` flags)
- [ ] Download RCAEval RE1 (375 cases), RE2 (270 cases), RE3 (90 cases) to `data/RCAEval/`
- [ ] Download LogHub HDFS to `data/LogHub/HDFS/` (verify `HDFS.log` + `anomaly_label.csv` present)
- [ ] Create `notebooks/01_data_exploration.ipynb` — OTel Demo log volume, metric availability, error rates
- [ ] Create `notebooks/02_rcaeval_exploration.ipynb` — fault distributions, root cause service breakdown, metric naming across RE1/RE2/RE3
- [ ] Create `notebooks/03_loghub_exploration.ipynb` — block structure, anomaly rate (~2.9%), Drain3 sample quality
- [ ] Visualize OTel Demo service topology (`docs/images/service_topology.html`)
- [ ] Confirm 24h OTel Demo baseline collection complete (`data/baseline/metadata.json` status = `completed`)
- [ ] Advisor check-in completed (Week 3) — demo running stack + downloaded datasets

### ✅ Phase 2 Complete When:
- All Docker services healthy; Kafka receiving logs
- RCAEval RE1/RE2/RE3 downloaded and case structure verified
- LogHub HDFS downloaded; anomaly labels loaded; block ID regex confirmed
- 24+ hours of OTel Demo baseline data collected

---

## Phase 3 — Data Preparation (Weeks 4–5)

**Goal:** Build OTel Demo data pipeline, implement dataset adapters, feature engineering, runbook indexing.

### OTel Demo Pipeline (Week 4)
- [ ] `src/data_collection/kafka_consumer.py` — `LogConsumer` class
- [ ] `src/preprocessing/log_parser.py` — `LogParser` wrapping Drain3 `TemplateMiner`
- [ ] `src/preprocessing/windowing.py` — `WindowAggregator` (60s windows)
- [ ] `src/preprocessing/feature_engineering.py` — `FeatureEngineer` (log template counts + metric stats)
- [ ] `src/preprocessing/rcaeval_adapter.py` — `RCAEvalDataAdapter` with column normalization; smoke-tested against RE1/RE2/RE3
- [ ] `src/data_collection/metrics_collector.py` — Prometheus query client
- [ ] `notebooks/04_log_parsing_analysis.ipynb` — Drain3 template quality across OTel Demo + HDFS samples

### LogHub Preprocessor & Supporting Components (Week 5)
- [ ] `src/preprocessing/loghub_preprocessor.py` — `LogHubHDFSPreprocessor`: block grouping, label loading, sequence building; validated (~2.9% anomaly rate)
- [ ] `src/data_collection/topology_extractor.py` — `TopologyGraph` (NetworkX DiGraph, 6 services + Redis = 7 total nodes, all edges)
- [ ] `src/knowledge_base/runbook_indexer.py` — `RunbookIndexer` with ChromaDB + `all-MiniLM-L6-v2`
- [ ] `src/knowledge_base/embeddings.py` — embedding utility functions (sentence-transformers `all-MiniLM-L6-v2` wrapper)
- [ ] Create 5 runbooks: `connection_exhaustion.md`, `cascading_failure.md`, `memory_pressure.md`, `high_latency.md`, `general_troubleshooting.md`
- [ ] Index all runbooks into ChromaDB; verify search returns relevant results
- [ ] `scripts/prepare_data_splits.py` — CLI script that calls `create_otel_splits()` and `create_hdfs_splits()` (defined in `src/preprocessing/`; see `context/data_pipeline_specs.md` §5)
- [ ] Advisor check-in completed (Week 5) — logs flowing, templates extracted, adapters working

### ✅ Phase 3 Complete When:
- OTel Demo pipeline: Kafka → Drain3 → windowing → feature vectors working end-to-end
- RCAEval adapter verified against all three variants (RE1/RE2/RE3)
- LogHub HDFS preprocessor producing correct sequence shapes and ~2.9% anomaly rate
- Data splits ready for training
- Runbooks indexed into ChromaDB; similarity search working

---

## Phase 4 — Modeling (Weeks 6–8)

**Goal:** Pretrain LSTM-AE on HDFS, fine-tune on OTel Demo, implement causal discovery, build and test the LangGraph agent.

### LSTM-Autoencoder (Week 6)
- [ ] `src/anomaly_detection/lstm_autoencoder.py` — `LSTMAutoencoder` class with `get_reconstruction_error()`
- [ ] `src/anomaly_detection/trainer.py` — `AnomalyTrainer` with training loop and early stopping
- [ ] `src/anomaly_detection/pretrain_on_loghub.py` — `pretrain_on_hdfs()` and `finetune_on_otel_demo()` with `_load_compatible_weights()`
- [ ] `src/anomaly_detection/threshold.py` — 95th percentile threshold from baseline reconstruction errors
- [ ] `src/anomaly_detection/isolation_forest.py` — Isolation Forest baseline (n_estimators=100, contamination=0.01)
- [ ] `src/anomaly_detection/detector.py` — `AnomalyDetector` real-time detection service (Fast Loop → Slow Loop bridge: scores each window, fires alert to `AgentExecutor` when threshold exceeded)
- [ ] Run pretraining on HDFS in `notebooks/05_anomaly_detection_dev.ipynb` (Colab Pro, GPU) — checkpoint saved to `models/lstm_autoencoder/pretrained_hdfs.pt`
- [ ] Run fine-tuning on OTel Demo — checkpoint saved to `models/lstm_autoencoder/finetuned_otel.pt`
- [ ] Plot and save training curves to `docs/images/`

### Causal Discovery (Week 7)
- [ ] `src/causal_discovery/pc_algorithm.py` — `discover_causal_graph()` wrapping causal-learn PC (α=0.05, Fisher's Z)
- [ ] `src/causal_discovery/counterfactual.py` — `calculate_counterfactual_confidence()` scoring
- [ ] `src/causal_discovery/graph_utils.py` — `CausalEdge` and `CausalGraph` dataclasses
- [ ] `notebooks/06_causal_discovery_dev.ipynb` — validate PC algorithm on synthetic data with known ground truth

### LangGraph Agent (Week 8)
- [ ] `src/agent/state.py` — `AgentState` TypedDict
- [ ] `src/agent/graph.py` — LangGraph workflow: analyze_context → form_hypothesis → gather_evidence → analyze_causation → should_continue → generate_report
- [ ] `src/agent/tools/query_metrics.py` — Prometheus query tool
- [ ] `src/agent/tools/search_logs.py` — Loki search tool
- [ ] `src/agent/tools/get_topology.py` — topology retrieval tool
- [ ] `src/agent/tools/search_runbooks.py` — ChromaDB search tool
- [ ] `src/agent/tools/discover_causation.py` — causal analysis tool
- [ ] `src/agent/prompts/system_prompt.py` — agent persona + instructions
- [ ] `src/agent/prompts/report_template.py` — RCA report template
- [ ] `src/agent/executor.py` — `AgentExecutor` (10 tool call limit, error handling)
- [ ] `notebooks/07_agent_prototyping.ipynb` — agent workflow testing
- [ ] End-to-end agent test with manually injected fault — produces valid RCA report
- [ ] Advisor check-in completed (Week 8) — show HDFS pretraining curves + working agent demo

### Unit Tests
- [ ] `tests/unit/test_log_parser.py`
- [ ] `tests/unit/test_feature_engineering.py`
- [ ] `tests/unit/test_anomaly_detection.py`
- [ ] `tests/unit/test_causal_discovery.py`
- [ ] `tests/unit/test_agent_tools.py`
- [ ] `tests/integration/test_data_pipeline.py`
- [ ] `tests/integration/test_agent_workflow.py`

### ✅ Phase 4 Complete When:
- `pretrained_hdfs.pt` and `finetuned_otel.pt` saved; training curves show convergence
- Anomaly threshold correctly flags injected test anomalies
- PC algorithm produces valid causal graphs on synthetic data
- Counterfactual confidence scores in [0, 1]
- Agent completes full investigation successfully with manual fault injection

---

## Phase 5 — Evaluation (Weeks 9–10)

**Goal:** Run fault injection tests, RCAEval cross-system evaluation, calculate all metrics and visualizations. See `context/evaluation_strategy.md` for full evaluation details.

### Fault Injection Evaluation (Week 9)
- [ ] Create all 8 fault injection bash scripts in `demo_app/fault_scenarios/`
- [ ] Create `scripts/inject_faults.py` — automated fault injection coordinator
- [ ] Create `tests/evaluation/fault_injection_suite.py`
- [ ] Create `tests/evaluation/metrics_calculator.py` — Recall@1, Recall@3, Precision, Detection Latency, MTTR Proxy, F1
- [ ] Run 40 OTel Demo fault injection tests (8 fault types × 5 runs); save results to `data/evaluation/results/`
- [ ] Create `tests/evaluation/baseline_comparison.py`
- [ ] Evaluate 3 internal baselines: Rule-Based, AD-Only, LLM-Without-Tools

### RCAEval Cross-System Evaluation (Week 10, Days 1–2)
- [ ] Create `tests/evaluation/rcaeval_evaluation.py` — `evaluate_on_rcaeval()` + `run_all_rcaeval_variants()`
- [ ] Run OpsAgent against RE1 (375 cases); save results
- [ ] Run OpsAgent against RE2 (270 cases); save results
- [ ] Run OpsAgent against RE3 (90 cases); save results
- [ ] Compare results against published baselines (BARO, CIRCA, RCD, CausalRCA, MicroHECL)

### Analysis & Visualizations (Week 10, Days 3–5)
- [ ] Calculate all metrics across both evaluation tracks
- [ ] Manually score 25–30 RCA reports using 5-point explanation quality rubric; log to `data/evaluation/explanation_quality_scores.csv`
- [ ] Create `notebooks/08_evaluation_analysis.ipynb` with required visualizations (1–9):
  - [ ] Recall@1 by fault type (OTel Demo)
  - [ ] Baseline comparison grouped bar chart (OTel Demo)
  - [ ] RCAEval Recall@1 by variant vs. published baselines
  - [ ] Detection latency distribution
  - [ ] Confusion matrix (OTel Demo)
  - [ ] Causal graph examples
  - [ ] Agent tool usage distribution
  - [ ] Explanation quality score distribution
  - [ ] RCAEval Recall@1 by fault type (RE2 breakdown)
- [ ] Draft `docs/evaluation_results.md`
- [ ] Run statistical analysis (confidence intervals, paired t-tests)
- [ ] Advisor check-in completed (Week 10) — present results with charts

### 🔵 Nice-to-Have: LogHub HDFS AD Benchmark (Week 10 — only if ahead of schedule)

> Complete only after Tracks 1 and 2 are fully done. Full benchmark details in `context/anomaly_detection_specs.md` §11.

- [ ] Run `tests/evaluation/loghub_benchmark.py` with `pretrained_hdfs.pt` (Stage 2) — record F1, Precision, Recall
- [ ] Run `tests/evaluation/loghub_benchmark.py` with `finetuned_otel.pt` (Stage 3) — record F1, Precision, Recall
- [ ] Add Visualizations 10–11 to `notebooks/08_evaluation_analysis.ipynb`: HDFS F1 bar chart + training loss curves
- [ ] Document Track 3 results in `docs/evaluation_results.md` Section 3

### ✅ Phase 5 Complete When:
- 40 OTel Demo fault injection tests complete; Recall@1 ≥ 80%
- All 735 RCAEval cases evaluated; results vs. published baselines computed
- All required metrics calculated; Visualizations 1–9 created
- `docs/evaluation_results.md` drafted
- *(Nice-to-have: HDFS AD Benchmark tasks completed if time allows)*

---

## Phase 6 — Deployment & Documentation (Weeks 11–12)

**Goal:** Deploy serving layer, write CRISP-DM report, clean up repository, prepare presentation.

### Serving Layer (Week 11)
- [ ] `src/serving/api.py` — FastAPI with `POST /investigate`, `GET /health`, `GET /topology`
- [ ] `src/serving/dashboard.py` — Streamlit: system overview, investigation trigger, history viewer, real-time metrics
- [ ] `Dockerfile` — OpsAgent container (Python 3.11-slim, Poetry, FastAPI on :8000, Streamlit on :8501)
- [ ] Update `docker-compose.yml` to include OpsAgent service
- [ ] Verify end-to-end demo: fault injected → agent triggered → RCA report displayed in Streamlit

### Documentation & Final Cleanup (Week 12)
- [ ] Write `docs/CRISP_DM_Report.md` — all 6 phases with EDA findings, adapter design decisions, two-phase training strategy, dual evaluation tracks, statistical analysis
- [ ] Write `docs/architecture.md`
- [ ] Write `docs/api_reference.md`
- [ ] Add docstrings to all `src/` modules; run `mypy src/` with zero errors
- [ ] Update `README.md` — setup instructions, `python scripts/download_datasets.py --all`, demo walkthrough
- [ ] Add `LICENSE`
- [ ] Ensure `data/`, `models/*.pt` are in `.gitignore`; repo is clean for GitHub
- [ ] Create presentation slides: problem, architecture, data strategy, ML techniques, evaluation results (both tracks), demo
- [ ] Record demo video (optional backup)
- [ ] Advisor check-in completed (Week 11) — pre-defense review with draft report

### ✅ Phase 6 Complete When:
- End-to-end demo working (fault → alert → RCA report in Streamlit)
- `docs/CRISP_DM_Report.md` complete (all three data sources documented)
- Repository is clean; `README.md` has `python scripts/download_datasets.py --all`
- Presentation slides complete; ready for defense

---

## Progress Log

> **Template — fill this out at the end of every Claude Code session:**

```
### [YYYY-MM-DD] — Session N

**Phase:** Phase X — <Phase Name>
**Duration:** ~X hours

**Completed:**
- <task or file created/modified>
- <task or file created/modified>

**In Progress:**
- <task partially done — describe state>

**Blockers / Issues:**
- <any problems encountered and how they were handled or left>

**Next Session:**
- <first task to pick up in next session>
- <second task if applicable>

**Notes:**
- <any design decisions made, unexpected findings, or deviations from the plan>
```

---

### 2026-03-24 — Session 1

**Phase:** Phase 1 — Business Understanding
**Duration:** ~2 hours

**Completed:**
- Created `docs/problem_statement.md` — problem scope, two-loop architecture, three-dataset strategy, target users
- Created `docs/success_metrics.md` — all target metrics with rationale, explanation quality rubric, statistical analysis plan
- Created `docs/baselines.md` — 3 internal ablation baselines + 5 published RCAEval baselines with descriptions
- Created full directory structure (30+ directories) with `.gitkeep` files for Git tracking
- Created `__init__.py` in all 14 Python packages under `src/` and `tests/`
- Created `tests/conftest.py` shared fixture file
- Created `pyproject.toml` with all dependencies; ran `poetry install` successfully (Python 3.12, torch 2.2.2)
- Created `.env.example` (GEMINI_API_KEY template) and `.gitignore` (data/, models/*.pt, .env, caches)
- Created `README.md` with project overview, architecture summary, data strategy, quick start, tech stack table, project structure
- Created `Makefile` with targets: setup, test, test-integration, lint, format, typecheck, run, dashboard, infra-up/down, demo-up/down, clean
- Created all 4 config YAML files with full content from `context/config_reference.md`:
  - `configs/model_config.yaml` — LSTM-AE hyperparameters, two-phase training
  - `configs/agent_config.yaml` — LLM settings, tool parameters, causal discovery
  - `configs/dataset_config.yaml` — paths and parameters for all 3 datasets
  - `configs/evaluation_scenarios.yaml` — 8 fault types, evaluation runner settings

**In Progress:**
- None — Phase 1 is complete

**Blockers / Issues:**
- **PyTorch macOS x86_64 compatibility:** PyTorch >=2.3 dropped macOS Intel (x86_64) wheels. Pinned `torch>=2.0,<2.3` (resolves to 2.2.2). On Apple Silicon or Linux, this constraint can be relaxed.
- **onnxruntime macOS x86_64 compatibility:** onnxruntime >=1.20 lacks macOS x86_64 wheels. Pinned `onnxruntime>=1.17,<1.20` (resolves to 1.19.2).
- **RCAEval dependency conflict:** RCAEval's `[default]` extras pin `torch==1.12.1`, conflicting with our `torch>=2.0`. Resolved by installing RCAEval base package only (without `[default]` extras); `import RCAEval` works.
- **Python version:** System had Python 3.9 (conda) and 3.14 (system). Installed Python 3.12 via Homebrew and Poetry via pipx for compatibility with all dependencies.

**Next Session:**
- Begin Phase 2 — Data Understanding: create `docker-compose.yml` and infrastructure configs
- Set up OTel Demo services and monitoring stack

**Notes:**
- Poetry and pipx were installed fresh this session (`~/.local/bin/poetry`, `~/Library/Python/3.13/bin/pipx`). Ensure `PATH` includes these directories in future sessions.
- Python constraint set to `>=3.11,<3.13` in `pyproject.toml` due to torch/onnxruntime wheel availability on Intel Mac. Can be widened to `<3.14` once running on Apple Silicon or Linux.
- Config YAML files contain full production values (not stubs) — copied from `context/config_reference.md` to ensure cross-config constraints are satisfied from the start.
- `numpy` pinned to `<2.0` to maintain compatibility with torch 2.2.x and the broader dependency tree.

---

### 2026-03-25 — Session 2

**Phase:** Phase 2 — Data Understanding (Week 2 Infrastructure)
**Duration:** ~3 hours

**Completed:**
- Created `docker-compose.yml` — Prometheus, Grafana, Loki, Zookeeper, Kafka, cAdvisor (container metrics)
- Created `demo_app/docker-compose.demo.yml` — 6 OTel Demo services (frontend, cartservice, checkoutservice, paymentservice, productcatalogservice, currencyservice) + Redis + loadgenerator; pinned to `ghcr.io/open-telemetry/demo:1.7.0-*` tags; uses external `opsagent_opsagent-net` network
- Created `infrastructure/prometheus/prometheus.yml` — scrapes cAdvisor for container-level metrics (CPU, memory, network, disk I/O) with service name relabeling; Prometheus self-monitoring
- Created `infrastructure/prometheus/alert_rules.yml` — placeholder alert rules for HighLatency and HighErrorRate
- Created `infrastructure/loki/loki-config.yml` — boltdb-shipper storage, 7-day retention, in-memory ring
- Created `infrastructure/grafana/provisioning/datasources/datasources.yml` — auto-provisions Prometheus + Loki datasources
- Created `infrastructure/grafana/provisioning/dashboards/dashboards.yml` — auto-provisions dashboard directory
- Created `infrastructure/grafana/dashboards/service_overview.json` — Grafana dashboard with 6 panels (CPU, memory, network I/O, filesystem, network errors, container count)
- Created `scripts/start_infrastructure.sh` — sequential startup (monitoring stack → OTel Demo) with health wait periods
- Created `scripts/stop_infrastructure.sh` — reverse teardown (OTel Demo → monitoring stack)
- Created `scripts/generate_training_data.py` — baseline data collector with Prometheus metric snapshots + Loki log queries, 60s intervals, resume support, graceful interrupt handling, `metadata.json` tracking
- Created `scripts/download_datasets.py` — RCAEval automated download via `RCAEval.utility` + LogHub HDFS manual download instructions with verification
- Started 24h OTel Demo baseline metric collection (in progress, running via `caffeinate -s`)

**In Progress:**
- 24h OTel Demo baseline data collection is actively running (~230+ snapshots at session end)
- Logs are not being collected (0 count) — Loki has no log shipper configured yet (Promtail needed)
- Full stack verification incomplete — Prometheus + cAdvisor confirmed working; Grafana accessible; Kafka and log pipeline not yet verified

**Blockers / Issues:**
- **OTel Demo services don't expose Prometheus `/metrics` endpoints:** The original `prometheus.yml` from the context spec targeted individual service HTTP ports (frontend:8080, cartservice:7070, etc.), but these are gRPC services without native Prometheus exporters. **Resolution:** Replaced with cAdvisor for container-level metrics (CPU, memory, network, filesystem). This provides the metrics needed for anomaly detection without requiring an OTel Collector.
- **cAdvisor volume mounts on macOS:** The standard Linux cAdvisor config mounts `/sys`, `/var/lib/docker`, `/dev/disk`, etc. These don't exist or work the same way on macOS with Docker Desktop. **Resolution:** Stripped down to only `/var/run/docker.sock:ro` with `privileged: true` and `platform: linux/amd64`. cAdvisor starts successfully on Docker Desktop for Mac.
- **Docker daemon not running:** User encountered "failed to connect to docker API" error on first attempt. **Resolution:** Open Docker Desktop app and wait for it to fully start before running infrastructure scripts.
- **No log shipper to Loki:** Loki is running but empty — no Promtail or Docker logging driver configured to push container logs. Data collection script shows `logs: 0` on every iteration. **Resolution deferred:** Will add Promtail to the infrastructure stack in a future session before evaluation needs logs. Metrics collection is sufficient for the current 24h baseline run.

**Next Session:**
- Add Promtail to `docker-compose.yml` to ship container logs to Loki (requires stack restart — must wait until 24h collection completes)
- Download RCAEval datasets (RE1/RE2/RE3) and LogHub HDFS
- Create EDA notebooks (01, 02, 03) for OTel Demo baseline, RCAEval, and LogHub HDFS data exploration
- Visualize OTel Demo service topology (`docs/images/service_topology.html`)
- Confirm 24h baseline collection completed successfully

**Notes:**
- OTel Demo images pinned to `1.7.0` tag (not `latest`) for reproducibility.
- cAdvisor exposes metrics on port 8081 externally (mapped from container port 8080) to avoid conflict with OTel Demo frontend on port 8080.
- The `generate_training_data.py` script uses cAdvisor-provided container metrics via Prometheus — queries filter by `container_label_com_docker_compose_service` label to isolate OTel Demo services.
- Prometheus config diverges from `context/infrastructure_and_serving.md` spec: uses cAdvisor scraping instead of direct service scraping. The context file should be updated to reflect this architectural decision.
- Use `caffeinate -s` on macOS to prevent sleep during 24h data collection. Mac must be plugged into AC power.
- Data collection supports resume — if interrupted (`Ctrl+C` or kill), re-running the same command picks up from the last snapshot count via `metadata.json`.

---

### 2026-03-26 — Session 3

**Phase:** Phase 2 — Data Understanding (Week 2 Infrastructure Fix + Week 3 Start)
**Duration:** ~4 hours

**Completed:**
- Diagnosed empty metric data from first 24h collection — all 1440 snapshots contained empty arrays despite `status: completed`
- Root cause identified: cAdvisor v0.47.2 cannot discover individual Docker containers on macOS Docker Desktop (cgroupv2 + VM-based Docker). cAdvisor only saw the root cgroup (`id: /`); its Docker container discovery API returned 0 containers; `docker version` from inside cAdvisor returned "NOT FOUND"
- Replaced cAdvisor with custom Docker Stats Exporter (`infrastructure/docker_stats_exporter/`):
  - `exporter.py` — queries Docker API via Python Docker SDK, exposes Prometheus-format metrics on port 9101
  - `Dockerfile` — Python 3.11-slim with docker SDK
  - `requirements.txt` — docker>=7.0.0
  - Uses background thread for stats collection (avoids Prometheus scrape timeouts; `container.stats()` blocks ~1-2s per container)
- Updated `docker-compose.yml` — removed cAdvisor service, added `docker-stats-exporter` service
- Updated `infrastructure/prometheus/prometheus.yml` — replaced `cadvisor` scrape job with `docker-stats-exporter` job (port 9101)
- Updated `scripts/generate_training_data.py` — changed `_SVC_FILTER` from `container_label_com_docker_compose_service` to `service` label; simplified service name extraction
- Updated `infrastructure/grafana/dashboards/service_overview.json` — replaced all `container_label_com_docker_compose_service` references with `service` in query expressions and legend formats
- Fixed Grafana datasource provisioning (`infrastructure/grafana/provisioning/datasources/datasources.yml`) — added explicit `uid: prometheus` and `uid: loki` fields to match dashboard JSON references
- Updated `scripts/start_infrastructure.sh` — references `docker-stats-exporter` instead of `cadvisor`, includes `--build` flag
- Updated `CLAUDE.md` — replaced cAdvisor references with Docker Stats Exporter in technology stack, common commands, and gotchas sections
- Verified fix end-to-end: Prometheus targets all UP, 7 services reporting metrics with real values, rate() queries working, 3-minute test collection produced non-empty snapshots
- Confirmed Grafana dashboard populated with live data (no more "Datasource prometheus not found" error)
- Cleared old empty baseline data and started new 24h OTel Demo baseline collection (in progress)

**In Progress:**
- Second 24h OTel Demo baseline data collection actively running with verified non-empty metric snapshots
- Logs still not collected (0 count) — Promtail deferred since log training pipeline uses Kafka → Drain3 (Phase 3), not Loki baseline logs

**Blockers / Issues:**
- **cAdvisor cannot discover containers on macOS Docker Desktop:** cAdvisor v0.47.2 running inside Docker on macOS Docker Desktop (Docker 29.3.0, cgroupv2) cannot see other containers. The Docker socket is mounted but cAdvisor's internal Docker client fails to connect. Without Docker API access or cgroup filesystem mounts, cAdvisor only reports root-level aggregate metrics. **Resolution:** Replaced cAdvisor with a custom Docker Stats Exporter that queries the Docker API directly via the Python `docker` SDK. This works reliably on macOS Docker Desktop.
- **Docker Stats Exporter initial timeout:** First version of the exporter called `container.stats(stream=False)` synchronously during HTTP request handling. With ~14 containers at ~1-2s each, total collection time (~20s) exceeded Prometheus's 10s scrape timeout, causing BrokenPipeError. **Resolution:** Moved stats collection to a background thread that runs every 10s and caches results. The `/metrics` endpoint returns cached data instantly.
- **Grafana "Datasource prometheus not found":** The Grafana dashboard JSON referenced `"uid": "prometheus"` but the provisioned datasource YAML didn't specify a `uid` field, so Grafana auto-generated a random UID. **Resolution:** Added explicit `uid: prometheus` and `uid: loki` to the datasource provisioning YAML.

**Next Session:**
- Confirm second 24h baseline collection completed successfully with non-empty metrics
- Download RCAEval datasets (RE1/RE2/RE3) and LogHub HDFS
- Create EDA notebooks (01, 02, 03)
- Visualize OTel Demo service topology
- Complete remaining Phase 2 tasks

**Notes:**
- The Docker Stats Exporter uses a 10s background collection interval with thread-safe caching. Prometheus scrapes every 15s, so data is always fresh within one collection cycle.
- The exporter labels metrics with `service` (from `com.docker.compose.service` Docker label) — simpler than cAdvisor's `container_label_com_docker_compose_service`.
- `fs_usage_bytes` reports 6 entries instead of 7 — some containers don't report blkio stats. This is expected and doesn't affect downstream processing.
- Log collection (Promtail/Loki) intentionally deferred — the log training pipeline (LSTM-AE) uses Kafka → Drain3 → template sequences, not Loki baseline logs. Promtail can be added later if needed for the agent's `search_logs` tool.
- Python stdout buffering in Docker was fixed by adding `PYTHONUNBUFFERED=1` to the exporter's Dockerfile.
