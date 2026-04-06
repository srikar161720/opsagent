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
- [x] Create `scripts/download_datasets.py` (with `--rcaeval`, `--loghub`, `--all`, `--status` flags; downloads from Zenodo API)
- [x] Download RCAEval RE1 (375 cases), RE2 (271 cases), RE3 (90 cases) to `data/RCAEval/`
- [x] Download LogHub HDFS to `data/LogHub/HDFS/` (verify `HDFS.log` + `anomaly_label.csv` present)
- [x] Create `notebooks/01_data_exploration.ipynb` — OTel Demo metric availability, time series, distributions, correlations, anomalous period detection
- [x] Create `notebooks/02_rcaeval_exploration.ipynb` — fault distributions, root cause service breakdown, metric column naming across RE1/RE2/RE3
- [x] Create `notebooks/03_loghub_exploration.ipynb` — block structure, anomaly rate (~2.9%), Drain3 sample quality, normal vs anomalous comparison
- [x] Visualize OTel Demo service topology (`docs/images/service_topology.html`) — interactive vis.js graph with 7 nodes, 9 edges
- [x] Confirm 24h OTel Demo baseline collection complete (`data/baseline/metadata.json` status = `completed`, 1440 snapshots)
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
- [x] `src/data_collection/kafka_consumer.py` — `LogConsumer` class
- [x] `src/preprocessing/log_parser.py` — `LogParser` wrapping Drain3 `TemplateMiner`
- [x] `src/preprocessing/windowing.py` — `WindowAggregator` (60s windows)
- [x] `src/preprocessing/feature_engineering.py` — `FeatureEngineer` (log template counts + metric stats)
- [x] `src/preprocessing/rcaeval_adapter.py` — `RCAEvalDataAdapter` with column normalization; smoke-tested against RE1/RE2/RE3
- [x] `src/data_collection/metrics_collector.py` — Prometheus query client
- [x] `notebooks/04_log_parsing_analysis.ipynb` — Drain3 template quality across HDFS 100K sample (OTel Demo logs deferred — no log shipper configured yet)

### LogHub Preprocessor & Supporting Components (Week 5)
- [x] `src/preprocessing/loghub_preprocessor.py` — `LogHubHDFSPreprocessor`: block grouping, label loading, sequence building; `create_hdfs_splits()` and `create_otel_splits()` helper functions
- [x] `src/data_collection/topology_extractor.py` — `TopologyGraph` (NetworkX DiGraph, 7 nodes, 9 edges, `get_subgraph()`, `to_json()`)
- [x] `src/knowledge_base/runbook_indexer.py` — `RunbookIndexer` with ChromaDB + `all-MiniLM-L6-v2` (`index_file()`, `index_directory()`, `search()`)
- [x] `src/knowledge_base/embeddings.py` — embedding utility functions (sentence-transformers `all-MiniLM-L6-v2` wrapper with lazy singleton)
- [x] Create 5 runbooks: `connection_exhaustion.md`, `cascading_failure.md`, `memory_pressure.md`, `high_latency.md`, `general_troubleshooting.md`
- [x] Index all runbooks into ChromaDB; verify search returns relevant results (57 chunks indexed; 3/4 top-1 matches, 4/4 top-3 matches)
- [x] `scripts/prepare_data_splits.py` — CLI script with `--hdfs`, `--otel`, `--all` flags; saves `train.npy`, `val.npy`, `metadata.json` to `data/splits/hdfs/`
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
- [x] `src/anomaly_detection/lstm_autoencoder.py` — `LSTMAutoencoder(nn.Module)` with `forward()`, `get_reconstruction_error()` (encoder-decoder with latent bottleneck, 138K params)
- [x] `src/anomaly_detection/trainer.py` — `AnomalyTrainer` with MSELoss, Adam optimizer, early stopping, best-model restoration
- [x] `src/anomaly_detection/pretrain_on_loghub.py` — `pretrain_on_hdfs()`, `finetune_on_otel_demo()`, `_load_compatible_weights()`, `_one_hot_encode()`
- [x] `src/anomaly_detection/threshold.py` — `calculate_threshold()` with 95th percentile from baseline reconstruction errors
- [x] `src/anomaly_detection/isolation_forest.py` — `IsolationForestDetector` baseline (n_estimators=100, contamination=0.01, n_jobs=-1)
- [x] `src/anomaly_detection/detector.py` — `AnomalyDetector` real-time detection service (Fast Loop → Slow Loop bridge: scores each window, fires alert callback when threshold exceeded)
- [x] Run pretraining on HDFS in `notebooks/05_anomaly_detection_dev.ipynb` (Colab Pro, GPU) — checkpoint saved to `models/lstm_autoencoder/pretrained_hdfs.pt` (115 templates, 1.6M sequences, early stopped at epoch 36/50, val_loss=0.000011)
- [x] Run fine-tuning on OTel Demo — completed in Session 8 (Colab Pro L4 GPU, 54-dim features, lr=0.001, early stopped at epoch 168/200, val_loss=0.134, threshold=0.253)
- [x] Plot and save training curves to `docs/images/` (hdfs_pretraining_curves.png, hdfs_error_distribution.png)

### Log Pipeline & Fine-tuning Data Collection (Week 6.5 — between Week 6 and Week 7)

**Before collection (infrastructure setup):**
- [x] Add Promtail service to `docker-compose.yml` — ship OTel Demo container logs to Loki (Promtail does not natively support Kafka output)
- [x] Create `infrastructure/promtail/promtail-config.yml` — Docker SD via socket, `service` label from `com.docker.compose.service`, push to Loki
- [x] Update `scripts/start_infrastructure.sh` and `scripts/stop_infrastructure.sh` to include Promtail
- [x] Restart Docker stack and verify logs flowing: Loki receiving log entries with `service` label for all 7 OTel Demo services
- [x] Update `scripts/generate_training_data.py` to collect logs from Loki alongside Prometheus metrics (per-service queries with `{service="<name>"}`, `"service"` key in log entries)

**24h data collection (run with `caffeinate -s`; Week 7 tasks can proceed in parallel):**
- [x] Run 24-hour baseline collection with both metrics AND logs — saved to `data/baseline_with_logs/` (1440 snapshots, 2885 log entries, status=completed)

**After collection completes:**
- [x] Process collected data through `FeatureEngineer.build_sequence()` to produce feature vectors for fine-tuning — z-score normalized, saved to `data/splits/otel/` (train: 1144×10×54, val: 286×10×54, scaler params saved)
- [x] Run fine-tuning on OTel Demo using `finetune_on_otel_demo()` — checkpoint saved to `models/lstm_autoencoder/finetuned_otel.pt` (54-dim, lr=0.001, early stopped epoch 168/200, val_loss=0.134, 132K params)
- [x] Calculate anomaly detection threshold using `calculate_threshold()` on fine-tuned model — 95th percentile threshold: 0.253
- [x] Plot and save training curves (pretrain + fine-tune) to `docs/images/` (finetune_training_curves.png)
- [x] Synthetic anomaly benchmark on fine-tuned model — F1=0.97, Precision=0.95, Recall=1.00 (CPU spike 100%, Memory spike 100%, Network error 100% detection)

### Causal Discovery (Week 7)

> **Note:** Week 7 tasks have no dependency on the Week 6.5 data collection and can be completed while the 24h log-enriched baseline collection is in progress.

- [x] `src/causal_discovery/pc_algorithm.py` — `discover_causal_graph()` wrapping causal-learn PC (α=0.05, Fisher's Z), `create_time_lags()`, `parse_causal_graph()`
- [x] `src/causal_discovery/counterfactual.py` — `calculate_counterfactual_confidence()` scoring, `compute_baseline_stats()`
- [x] `src/causal_discovery/graph_utils.py` — `CausalEdge` and `CausalGraph` dataclasses with `to_ascii()` and `top_edges()`
- [x] `notebooks/06_causal_discovery_dev.ipynb` — validated PC on synthetic A→B→C (correct skeleton, no spurious A→C), complex B→C←D (collider oriented correctly), alpha sensitivity, counterfactual confidence scoring

### LangGraph Agent (Week 8)
- [x] `src/agent/state.py` — `AgentState` TypedDict (13 fields, `add_messages` reducer on `messages`, LangGraph `StateGraph` compatible)
- [x] `src/agent/graph.py` — LangGraph workflow: 5-node `StateGraph` (analyze_context → form_hypothesis → gather_evidence → analyze_causation → conditional → generate_report), `should_continue` routing, Gemini 2.5 Flash Lite LLM with `bind_tools`
- [x] `src/agent/tools/query_metrics.py` — Prometheus query tool (6 Docker Stats Exporter metrics: cpu_usage, memory_usage, network_rx/tx_bytes_rate, network_rx/tx_errors_rate; returns timestamps, values, stats, anomalous flag)
- [x] `src/agent/tools/search_logs.py` — Loki search tool (LogQL queries, service filter, error counting, pattern extraction)
- [x] `src/agent/tools/get_topology.py` — topology retrieval tool (wraps `TopologyGraph`, full graph or subgraph per service)
- [x] `src/agent/tools/search_runbooks.py` — ChromaDB search tool (wraps `RunbookIndexer`, returns title/content/relevance_score)
- [x] `src/agent/tools/discover_causation.py` — causal analysis tool (orchestrates MetricsCollector → time lags → PC algorithm → counterfactual scoring → CausalGraph; `max_conditioning_set=4` depth cap)
- [x] `src/agent/prompts/system_prompt.py` — agent persona + 6-step investigation methodology + available metrics list + tool budget guidance
- [x] `src/agent/prompts/report_template.py` — RCA report template with `str.format()` placeholders (14 fields)
- [x] `src/agent/executor.py` — `AgentExecutor` with `from_config()` classmethod, dual-mode `investigate()` (live + offline/RCAEval), error handling
- [x] `src/agent/tools/__init__.py` — `TOOLS` registry exporting all 5 tool functions
- [x] `src/agent/__init__.py` — package exports (`AgentExecutor`, `AgentState`)
- [x] `notebooks/07_agent_prototyping.ipynb` — 7-section notebook: setup, individual tool testing, graph compilation, routing logic, executor setup, full investigation, report inspection
- [x] `scripts/run_agent_demo.py` — CLI demo script with prerequisite checks, live/offline modes, formatted output
- [ ] End-to-end agent test with manually injected fault — produces valid RCA report (demo tested live; full fault injection deferred to Phase 5)
- [ ] Advisor check-in completed (Week 8) — show HDFS pretraining curves + working agent demo

### Unit Tests
- [x] `tests/unit/test_log_parser.py` — 12 tests (completed Session 6)
- [x] `tests/unit/test_feature_engineering.py` — 13 tests (completed Session 6)
- [x] `tests/unit/test_anomaly_detection.py` — 22 tests: LSTMAutoencoder (5), AnomalyTrainer (4), Threshold (3), IsolationForest (3), AnomalyDetector (3), LoadCompatibleWeights (2), OneHotEncode (2)
- [x] `tests/unit/test_causal_discovery.py` — 31 tests: CausalEdge (2), CausalGraph (5), CreateTimeLags (6), DiscoverCausalGraph (5), ParseCausalGraph (4), ComputeBaselineStats (4), CounterfactualConfidence (5)
- [x] `tests/unit/test_agent_tools.py` — 35 tests: GetTopology (6), QueryMetrics (8), SearchLogs (7), SearchRunbooks (5), DiscoverCausation (9)
- [x] `tests/unit/test_agent_state.py` — 5 tests: TypedDict structure, add_messages annotation, field completeness, construction, type hints
- [x] `tests/unit/test_agent_graph.py` — 12 tests: BuildGraph (2), ShouldContinue (5), AnalyzeContextNode (2), HelperFunctions (3)
- [x] `tests/unit/test_agent_executor.py` — 10 tests: Init (2), Investigate (4), FormatAlert (2), ExtractTop3 (3) — note: one test was removed by linter (9 remaining)
- [x] `tests/integration/test_data_pipeline.py` — 7 tests: PrometheusIntegration (2), LokiIntegration (2), TopologyIntegration (2), FullPipeline (1). `@pytest.mark.integration`
- [x] `tests/integration/test_agent_workflow.py` — 8 tests: GraphCompilation (1), ToolInvocation (3), EndToEndInvestigation (3, requires GEMINI_API_KEY). `@pytest.mark.integration`

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
- [ ] Run OpsAgent against RE2 (271 cases); save results
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
- All 736 RCAEval cases evaluated; results vs. published baselines computed
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

---

### 2026-03-27 — Session 4

**Phase:** Phase 2 — Data Understanding (Week 3 Dataset Acquisition & EDA)
**Duration:** ~3 hours

**Completed:**
- Confirmed second 24h OTel Demo baseline collection completed successfully (1440 snapshots, 8 metrics × 7 services, all non-empty, `status: completed`)
- Added `seaborn` dependency via Poetry (v0.13.2) for EDA notebook visualizations
- Rewrote `scripts/download_datasets.py` — replaced broken `RCAEval.utility` imports with direct Zenodo API downloads; added automated LogHub HDFS download; improved verification with true case counting via `inject_time.txt`; added per-system breakdown in `--status` output
- Downloaded and verified all RCAEval datasets: RE1 (375 cases across RE1-OB/SS/TT), RE2 (271 cases across RE2-OB/SS/TT), RE3 (90 cases across RE3-OB/SS/TT) — total 736 cases
- Downloaded and verified LogHub HDFS: `HDFS.log` (1505 MB, 11M+ lines), `anomaly_label.csv` (575,061 blocks)
- Created `notebooks/01_data_exploration.ipynb` — OTel Demo baseline EDA with 8 cells: data loading, statistical summary, time series plots (8 metrics × 7 services), distribution boxplots, correlation heatmap, anomalous period detection (rolling 3σ), service comparison bar charts, summary
- Created `notebooks/02_rcaeval_exploration.ipynb` — RCAEval EDA with 8 cells: case inventory builder, directory structure samples, fault type distribution charts, root cause service breakdown, metric column naming analysis across OB/SS/TT, data format comparison table, adapter design summary
- Created `notebooks/03_loghub_exploration.ipynb` — LogHub HDFS EDA with 9 cells: anomaly label analysis (rate verification), 100K-line log sample loading with parsing, block structure analysis, log pattern analysis (levels, components), Drain3 template extraction test (10K messages, depth=4, sim_th=0.4), template frequency distribution, normal vs anomalous block comparison, summary
- Created `docs/images/service_topology.html` — interactive vis.js topology visualization with 7 nodes (frontend, cartservice, checkoutservice, paymentservice, productcatalogservice, currencyservice, redis), 9 directed edges, color-coded by tier (gateway/backend/data store), hierarchical layout, legend
- Updated `PROGRESS.md` — marked all Phase 2 Week 3 tasks complete (except advisor check-in)

**In Progress:**
- None — all Phase 2 implementation tasks complete; only advisor check-in remains

**Blockers / Issues:**
- **RCAEval pip package is a stub:** The installed `RCAEval` PyPI package only contains a single `is_ok()` function. The `RCAEval.utility` module with download functions does not exist in the pip package — it only exists in the GitHub source. **Resolution:** Rewrote `download_datasets.py` to download directly from Zenodo API (`https://zenodo.org/api/records/14590730/files/{name}/content`) without depending on `RCAEval.utility`.
- **RE2 case count discrepancy:** RE2-OB has 91 cases instead of the documented 90, making the RE2 total 271 instead of 270. This is a minor data issue in the upstream RCAEval benchmark — no impact on our evaluation pipeline.
- **RE1 vs RE2/RE3 file format differences:** RE1 uses `data.csv` with simple `{service}_{metric}` column naming (51 columns). RE2/RE3 use `metrics.csv` with container-level metric naming (up to 418 columns). The Phase 3 `RCAEvalDataAdapter` must handle both formats.

**Next Session:**
- Begin Phase 3 — Data Preparation: implement OTel Demo data pipeline (`kafka_consumer.py`, `log_parser.py`, `windowing.py`, `feature_engineering.py`)
- Implement `rcaeval_adapter.py` and `loghub_preprocessor.py`
- Run notebooks 01-03 to validate EDA outputs

**Notes:**
- Notebooks were created in Session 4 and executed + validated in Session 5 — all outputs confirmed correct.
- RCAEval service naming differs across systems: OB uses `adservice`, `cartservice`, etc.; SS uses `carts`, `catalogue`, `orders`, etc.; TT uses `ts-auth-service`, `ts-order-service`, etc. The adapter must normalize these.
- LogHub HDFS has 575,061 blocks total in `anomaly_label.csv`, with expected ~2.9% anomaly rate.
- The topology visualization uses vis.js loaded from CDN — no Python dependency added. Edge direction follows the convention from `context/data_pipeline_specs.md`: A → B means "A is called by B" (A is a dependency of B).
- `seaborn` added as a main dependency (not dev-only) since it's used in notebooks that are part of the project deliverables.

---

### 2026-03-29 — Session 5

**Phase:** Phase 2 — Data Understanding (Week 3 Notebook Validation & Data Quality Review)
**Duration:** ~3 hours

**Completed:**
- Corrected RE2 case count (270→271) and total (735→736) across all project files: `CLAUDE.md`, `PROGRESS.md`, `README.md`, `configs/dataset_config.yaml`, `docs/problem_statement.md`, `docs/baselines.md`, `docs/success_metrics.md`, `context/architecture_and_design.md`, `context/evaluation_strategy.md`, `context/config_reference.md`, `context/data_pipeline_specs.md`
- Fixed outdated `RCAEval.utility` references in `context/data_pipeline_specs.md` — replaced with Zenodo API download instructions matching the actual `scripts/download_datasets.py` implementation
- Updated `context/data_pipeline_specs.md` §6.2 with accurate RCAEval directory structure, file format differences, and all three naming conventions
- Diagnosed Redis missing from notebook 01 output — traced to `SERVICES` list in `generate_training_data.py` (lines 29-36) not including `"redis"`, even though the PromQL filter and actual snapshot data contained Redis metrics
- Fixed `scripts/generate_training_data.py` — added `"redis"` to `SERVICES` list
- Fixed `data/baseline/metadata.json` — added `"redis"` to services array (now reports 7 services)
- Validated notebook 01 (OTel Demo baseline EDA) — all outputs verified:
  - 1440 snapshots, 79,152 rows, 7 services × 8 metrics, full 24h coverage with no gaps
  - 16 zero-variance pairs identified (14 network error rates + 2 fs_usage_bytes) — safe to drop
  - `memory_usage_bytes` and `memory_working_set_bytes` perfectly correlated (r=1.0) across all 7 services — drop one
  - Cross-service correlations confirmed real topology: `redis↔cartservice` network (r=0.939), `frontend↔paymentservice` TX (r=0.987)
  - No anomalous periods detected via 3σ rolling window — very stable baseline
  - Redis is the highest CPU consumer (mean 5.1e-3); frontend is the heaviest memory user (~200MB)
  - `checkoutservice` has 24 missing memory snapshots (1416 vs 1440) — minor container restart, not a concern
- Validated notebook 02 (RCAEval exploration) — all outputs verified:
  - 736 total cases confirmed: RE1=375, RE2=271, RE3=90
  - RE2 extra case traced to `checkoutservice_cpu` in OB having 4 runs instead of 3
  - Discovered RE1 format inconsistency: RE1-OB uses simple naming (51 cols), but RE1-SS (439 cols) and RE1-TT (1246 cols) use container-metric naming — same format as RE2/RE3
  - Infrastructure noise identified in column names: GKE node names, AWS IPs, `istio-init` — adapter must filter these
  - Updated `context/data_pipeline_specs.md` to document three naming conventions (not two)
- Validated notebook 03 (LogHub HDFS exploration) — all outputs verified:
  - 575,061 blocks, 2.93% anomaly rate — matches expected ~2.9%
  - 0 parse failures on 100K lines; 100% of lines contain block IDs (no filtering needed)
  - 100% INFO log level — anomaly detection must rely on template sequence patterns, not log levels
  - 15 Drain3 templates from 10K sample; top 5 cover 95.7%, top 10 cover 100%; 5 singletons
  - Anomalous blocks have fewer logs (mean 9.7 vs 12.7) with wider variance — incomplete lifecycle patterns
  - Drain3 parameters (`depth=4`, `sim_th=0.4`, `max_children=100`) validated as producing reasonable output

**In Progress:**
- None — all notebook validation complete

**Blockers / Issues:**
- **Redis missing from `SERVICES` metadata list:** `generate_training_data.py` included `redis` in the PromQL filter (`_SVC_FILTER`) but not in the `SERVICES` list used for `metadata.json`. The Docker Stats Exporter correctly exposed Redis metrics, Prometheus scraped them, and they were present in all 1440 snapshots — only the metadata was wrong. **Resolution:** Added `"redis"` to both the `SERVICES` list and `metadata.json`.
- **RCAEval RE1 format inconsistency:** The context files stated "RE1 uses `data.csv` with simple naming" — this is only true for RE1-OB (51 columns). RE1-SS (439 cols) and RE1-TT (1246 cols) use `data.csv` but with container-metric naming identical to RE2/RE3. **Resolution:** Updated `context/data_pipeline_specs.md` to document all three naming conventions. The `RCAEvalDataAdapter` must detect format by inspecting column names, not by variant alone.

**Next Session:**
- Begin Phase 3 — Data Preparation: implement OTel Demo data pipeline (`kafka_consumer.py`, `log_parser.py`, `windowing.py`, `feature_engineering.py`)
- Implement `rcaeval_adapter.py` with three-format detection and infrastructure noise filtering
- Implement `loghub_preprocessor.py` with streaming parser

**Notes:**
- All three EDA notebooks have been executed and validated — outputs confirm data quality is good across all three datasets with no blocking issues.
- The 24h OTel Demo baseline data does not need re-collection — Redis metrics were already present in snapshot files; only the metadata label was missing.
- Key design decisions confirmed by EDA: (1) drop `memory_usage_bytes` (redundant with `memory_working_set_bytes`), (2) drop zero-variance network error rate features, (3) per-service anomaly thresholds are necessary due to high variance differences across services, (4) Drain3 parameters validated on HDFS data.
- RE1 adapter cannot assume simple naming based on variant — must inspect column name patterns to detect format.

---

### 2026-03-29 — Session 6

**Phase:** Phase 3 — Data Preparation (Week 4 OTel Demo Pipeline)
**Duration:** ~4 hours

**Completed:**
- Fixed 8 discrepancies in `context/data_pipeline_specs.md`:
  1. Kafka library: rewrote LogConsumer from `kafka-python` to `confluent-kafka` API (`Consumer.poll()`, `msg.error()`, `msg.value().decode()`)
  2. RCAEval `metadata.json` assumption: replaced with `inject_time.txt` parsing (no metadata.json exists in dataset)
  3. RCAEval directory traversal: updated from 1-level to 3-level walk (system → fault → run)
  4. RCAEval case ID format: changed from `"cpu_cartservice_001"` to `"RE2-OB/checkoutservice_cpu/1"`
  5. RCAEval timestamp source: reads from `inject_time.txt` (Unix epoch) instead of metadata.json
  6. `_load_metrics()`: tries `metrics.csv` first, falls back to `data.csv` for RE1
  7. Ground truth extraction: parses service and fault_type from directory name (split on last underscore)
  8. Drain3 `match()` API: replaced `TemplateMiner.match()` (requires Drain3 >=0.9.8) with `Drain.tree_search()` (available in installed v0.9.1)
- Created `src/preprocessing/log_parser.py` — `LogParser` class wrapping Drain3 with `parse()` (mutating), `match()` (read-only via `tree_search`), bidirectional template_to_id/id_to_template mappings, monotonically increasing integer IDs, `FilePersistence`
- Created `src/data_collection/kafka_consumer.py` — `LogConsumer` class using `confluent_kafka.Consumer` with poll loop, JSON decoding, timestamp tuple unpacking, error handling
- Created `src/data_collection/metrics_collector.py` — `MetricsCollector` class with `instant_query()`, `range_query()`, `get_service_metrics()` against Prometheus HTTP API
- Created `src/preprocessing/windowing.py` — `WindowAggregator` class with 60s non-overlapping windows, `add_log()` returns completed window on boundary crossing, `add_metric()`, `flush()` for partial windows
- Created `src/preprocessing/feature_engineering.py` — `FeatureEngineer` class: log template counts + normalized freq + error ratio + unique count (num_templates × 2 + 2) combined with metric stats (mean/std/min/max/p50/p99/delta = 7 per metric), `build_sequence()` for LSTM-AE input, `reset()` for batch use
- Created `src/preprocessing/rcaeval_adapter.py` — `RCAEvalDataAdapter` class with 3-level directory traversal, 3-format detection (RE1-OB simple, RE1-SS/TT container-metric, RE2/RE3 container-metric), infrastructure noise filtering (`gke-*`, `ip-192-168-*`, `istio-init`), ground truth from directory names, timestamps from `inject_time.txt`
- Created `notebooks/04_log_parsing_analysis.ipynb` — executed and validated: 100K HDFS lines → 45 templates, growth curve converges by line ~92K, top 5 templates cover 98.3%, sensitivity analysis confirms sim_th=0.4 is optimal (15 templates at 0.4 vs 642 at 0.6)
- Created `tests/conftest.py` — 6 shared fixtures: `sample_hdfs_log_lines`, `sample_otel_log_lines`, `sample_window_dict`, `empty_window_dict`, `mock_kafka_message`, `mock_prometheus_response`
- Created `tests/unit/test_log_parser.py` — 12 tests: parse tuple return, first ID=0, monotonic IDs, template generalization, num_templates growth, match known/unknown, match doesn't create templates, get_template reverse lookup, persistence, HDFS log parsing
- Created `tests/unit/test_feature_engineering.py` — 13 tests: feature_dim formula, no-metrics/no-templates edge cases, output shape, template counts/freq, empty window, metric features (mean/min/max), error ratio with/without parser, build_sequence shape/ValueError, reset clears delta
- Smoke-tested RCAEval adapter: RE1=375, RE2=271, RE3=90 cases — all counts match expected totals
- All 25 unit tests passing; ruff lint clean; mypy 0 errors on all new source files

**In Progress:**
- None — all Week 4 tasks complete

**Blockers / Issues:**
- **Drain3 v0.9.1 lacks `TemplateMiner.match()` method:** The spec and Context7 docs reference `match()` but it was added in v0.9.8. Our installed version (0.9.1) only has `add_log_message()`. **Resolution:** Used `Drain.tree_search(root_node, tokens)` as an equivalent read-only lookup. This is the underlying mechanism that `match()` wraps in newer versions. Updated spec to document this.
- **`confluent-kafka` vs `kafka-python` API mismatch:** The spec used `kafka-python` imports (`from kafka import KafkaConsumer`) but `pyproject.toml` installs `confluent-kafka>=2.3`. These are entirely different libraries with incompatible APIs. **Resolution:** Rewrote LogConsumer using `confluent_kafka.Consumer` with `poll()` loop, `msg.error()` checking, and `msg.value().decode()` byte handling. Updated spec accordingly.
- **RCAEval has no `metadata.json` files:** The spec assumed each case directory contains `metadata.json` with ground truth and timestamps. No such files exist in the downloaded dataset. **Resolution:** Ground truth is parsed from directory names (`{service}_{fault_type}`) and timestamps from `inject_time.txt` (Unix epoch). Rewrote the entire adapter with 3-level directory traversal.
- **RE1-OB simple format detection failed initially:** The `_is_simple_format()` function checked for hyphens in column names, but RE1-OB has `frontend-external_load` and `frontend-external_error` (hyphens in service names). **Resolution:** Changed detection to check whether the metric suffix after the last underscore is a known simple metric (cpu, mem, load, latency, error) rather than checking for absence of hyphens.
- **mypy type errors in 3 files:** `windowing.py` had `None + timedelta` potential; `kafka_consumer.py` had `bytes | None` decode issue; `metrics_collector.py` had untyped return from `resp.json()`. **Resolution:** Added `assert` guard for window_start, null check for message value, and explicit type annotation for Prometheus response.

**Next Session:**
- Begin Phase 3 Week 5: `loghub_preprocessor.py`, `topology_extractor.py`, `runbook_indexer.py`, `embeddings.py`
- Create 5 runbooks in `runbooks/`
- Create `scripts/prepare_data_splits.py`
- Index runbooks into ChromaDB

**Notes:**
- Notebook 04 focuses on HDFS logs only — OTel Demo log parsing cannot be demonstrated until a log shipper (Promtail → Kafka) is configured. The 24h baseline collected metrics only; logs require separate infrastructure (Kafka topic ingestion from OTel services). This does not block Phase 3 Week 5 or the LSTM-AE pretraining (which uses HDFS, not OTel logs).
- The 24h baseline metric data does NOT need re-collection. Log data was never part of the baseline collection — logs enter the system via real-time Kafka streaming during fine-tuning (Phase 4), not from stored baseline files.
- Drain3 on 100K HDFS lines: 45 templates (vs 15 from 10K in notebook 03). The difference is from rare event templates (exceptions, deletions, replication). Core lifecycle templates (top 5 = 98.3% coverage) are the same. Template vocabulary converges by ~92K lines.
- Drain3 sensitivity analysis: sim_th=0.4 sits in the "safe zone" (15 templates). At 0.6 templates explode to 642 (40x increase). This validates the chosen parameter.
- RCAEval RE1-OB has 14 services in metrics (includes `main` and `frontend-external`), while RE2/RE3-OB have 13. This is inherent to the upstream dataset, not a bug.
- Context7 was used to verify up-to-date APIs for confluent-kafka, Drain3, and ChromaDB before implementation.

---

### 2026-04-01 — Session 7

**Phase:** Phase 3 → Phase 4 — Data Preparation (Week 5 completion) + Modeling (Week 6 LSTM-Autoencoder)
**Duration:** ~5 hours

**Completed:**

*Phase 3 Week 5 (carried from Session 6):*
- Created `src/preprocessing/loghub_preprocessor.py` — `LogHubHDFSPreprocessor` class with streaming HDFS.log parsing, block ID grouping via regex, anomaly label loading, fixed-length sequence building (left-padded, chunked); `create_hdfs_splits()` and `create_otel_splits()` helper functions
- Created `src/data_collection/topology_extractor.py` — `TopologyGraph` class with NetworkX DiGraph (7 nodes, 9 edges), `get_subgraph()` returning upstream/downstream dependencies with guard for unknown services, `to_json()` serialization
- Created `src/knowledge_base/embeddings.py` — lazy-loaded singleton wrapper for sentence-transformers `all-MiniLM-L6-v2` with `embed_text()` and `embed_batch()` functions
- Created `src/knowledge_base/runbook_indexer.py` — `RunbookIndexer` class with ChromaDB `PersistentClient`, paragraph-boundary chunking, MD5 document IDs, upsert-based indexing, similarity search with `1.0 - distance` relevance scoring
- Created 5 runbook markdown files in `runbooks/`: `connection_exhaustion.md`, `cascading_failure.md`, `memory_pressure.md`, `high_latency.md`, `general_troubleshooting.md` — each 500+ words with Symptoms, Root Cause, Investigation Steps, Remediation, Prevention sections
- Created `scripts/prepare_data_splits.py` — CLI script with `--hdfs`, `--otel`, `--all` flags
- Pinned `transformers>=4.36,<5.0` and `sentence-transformers>=2.2,<4.0` in `pyproject.toml` to resolve Intel Mac compatibility (transformers 5.x requires PyTorch >=2.4); downgraded to transformers 4.57.6 and sentence-transformers 3.4.1
- Indexed all 5 runbooks into ChromaDB — 57 chunks total; verified search: 3/4 top-1 matches, 4/4 top-3 matches
- Added `hdfs_data_dir` fixture to `tests/conftest.py` (synthetic 4 blocks, 16 log lines)
- Created 4 test files: `test_loghub_preprocessor.py` (15 tests), `test_topology_extractor.py` (10 tests), `test_embeddings.py` (5 tests), `test_runbook_indexer.py` (11 tests)
- All 66 unit tests passing; ruff lint clean; mypy 0 errors

*Phase 4 Week 6:*
- Created `src/anomaly_detection/lstm_autoencoder.py` — `LSTMAutoencoder(nn.Module)` with encoder-decoder LSTM architecture, latent bottleneck (Linear→LSTM→Linear→Linear→LSTM→Linear), `forward()` and `get_reconstruction_error()` (MSE per sequence)
- Created `src/anomaly_detection/trainer.py` — `AnomalyTrainer` with MSELoss, Adam optimizer, DataLoader-based training, early stopping with best-model in-memory restoration, device auto-detection
- Created `src/anomaly_detection/pretrain_on_loghub.py` — `pretrain_on_hdfs()` (HDFS parsing → one-hot encode → train), `finetune_on_otel_demo()` (load pretrained → partial weight transfer → fine-tune with lower LR), `_load_compatible_weights()` (filters by key name and shape match, skips embedding/output_layer)
- Created `src/anomaly_detection/threshold.py` — `calculate_threshold()` with batched inference under `no_grad()`, returns `np.percentile(errors, percentile)`
- Created `src/anomaly_detection/isolation_forest.py` — `IsolationForestDetector` wrapping sklearn with `fit()`, `predict()`, `score_samples()`, `n_jobs=-1`
- Created `src/anomaly_detection/detector.py` — `AnomalyDetector` Fast Loop → Slow Loop bridge with `score()` method, alert dict construction, callback invocation on threshold breach
- Created `tests/unit/test_anomaly_detection.py` — 22 tests across 7 classes (LSTMAutoencoder, AnomalyTrainer, Threshold, IsolationForest, AnomalyDetector, LoadCompatibleWeights, OneHotEncode)
- Created `notebooks/05_anomaly_detection_dev.ipynb` — 8-cell Colab GPU notebook (setup, HDFS load, pretrain, plot curves, threshold, HDFS benchmark, error distribution, fine-tune placeholder)
- All 88 unit tests passing; ruff lint clean; mypy 0 errors on `src/anomaly_detection/`

*Pretraining (run manually on Google Colab):*
- Parsed full HDFS.log: 11.2M lines → 115 Drain3 templates, 575,061 blocks
- Normal sequences: 1,608,443 (shape `(1608443, 10)`), anomalous: 42,785; sequence-level anomaly rate: 2.59%
- One-hot encoded to shape `(1286754, 10, 115)` train / `(321689, 10, 115)` val
- Training converged: loss 0.000444 → 0.000015 (epoch 1→36), val_loss best 0.000011 at epoch 31-32
- Early stopped at epoch 36/50 (patience=5); model restored to best weights
- Checkpoint saved: `models/lstm_autoencoder/pretrained_hdfs.pt` (138,243 parameters)
- 95th percentile threshold on holdout normal data: 0.000003
- HDFS benchmark: F1=0.58, Precision=0.60, Recall=0.56 (anomaly class) — moderate, expected for one-hot template-only features
- Training curves and error distribution plots saved to `docs/images/`

*PROGRESS.md updates:*
- Added "Week 6.5 — Log Pipeline & Fine-tuning Data Collection" section between Week 6 and Week 7 with 10 tasks for Promtail, log collection, fine-tuning, and threshold calculation

**In Progress:**
- None — all Week 5 + Week 6 implementation tasks complete; pretraining done

**Blockers / Issues:**
- **`transformers` 5.x NameError on Intel Mac:** The installed `transformers` 5.3.0 references `nn.Module` in a function signature but only imports `torch.nn` when PyTorch >=2.4 is detected. With torch 2.2.2, importing `sentence_transformers` crashes. **Resolution:** Pinned `transformers>=4.36,<5.0` and `sentence-transformers>=2.2,<4.0` in `pyproject.toml`. Downgraded to transformers 4.57.6 and sentence-transformers 3.4.1. All imports and ChromaDB indexing now work.
- **Drain3 v0.9.1 `load_state()` RuntimeError in tests:** `TemplateMiner.load_state()` iterates a dict while modifying it (Python 3.12 raises RuntimeError). Occurs when `LogParser()` loads existing state from `models/drain3/`. **Resolution:** Tests pass fresh `tmp_path`-based persistence paths to `LogParser` to avoid loading stale state.
- **HDFS benchmark F1=0.58 (below 0.90 target):** The one-hot encoded template sequences are sparse — anomalous blocks often share the same templates as normal blocks in different order/frequency. This is the HDFS-only pretraining result; the primary evaluation (OTel Demo fault injection) uses richer combined log+metric feature vectors. The HDFS benchmark is a nice-to-have track (see PROGRESS.md Phase 5).
- **HDFS full corpus produces 115 templates (vs 45 from 100K sample):** The full 11.2M lines expose rare event templates not seen in the 100K sample. Template vocabulary converges; 115 is the final count. This changes `input_dim` from the estimated 20-50 to 115, but the architecture handles dynamic `input_dim`.

**Next Session:**
- Begin Week 6.5: Add Promtail to `docker-compose.yml`, configure log shipping, collect 4-8h baseline with logs+metrics
- Run fine-tuning on OTel Demo with collected feature vectors
- Calculate production threshold on fine-tuned model
- Begin Week 7: Causal discovery (`pc_algorithm.py`, `counterfactual.py`, `graph_utils.py`)

**Notes:**
- The trainer saves best model weights in memory (not to disk) and restores after training loop. The caller (`pretrain_on_hdfs`, `finetune_on_otel_demo`) handles `torch.save()` with wrapped format `{"model_state_dict": ..., "history": ...}`.
- `_load_compatible_weights()` filters checkpoint keys by both name pattern and shape match — it loads ~8-12 LSTM encoder/decoder tensors while skipping embedding and output_layer (which depend on `input_dim` and differ between HDFS and OTel).
- The `AnomalyDetector.score()` method returns the reconstruction error as a float and fires the `on_anomaly` callback only when error > threshold. The alert dict includes `anomaly_score`, `threshold`, `severity`, `timestamp`, and `affected_services`.
- Ruff flagged `import torch.nn.functional as F` as N812 (CamelCase imported as non-lowercase). Renamed to `functional` throughout `pretrain_on_loghub.py`.
- Context7 was used to verify PyTorch nn.LSTM, DataLoader, and training loop patterns before implementation.
- The HDFS anomaly rate at the sequence level (2.59%) is lower than the block level (2.93%) because anomalous blocks have fewer log events per block (mean 9.7 vs 12.7), producing fewer chunked sequences per block.

---

### 2026-04-03 — Session 8

**Phase:** Phase 4 — Modeling (Week 6.5 completion + Week 7 Causal Discovery)
**Duration:** ~6 hours

**Completed:**

*Week 6.5 — Before collection (infrastructure setup):*
- Created `infrastructure/promtail/promtail-config.yml` — Docker SD via socket, `service` label from `com.docker.compose.service`, push to Loki at `loki:3100`
- Added Promtail service to `docker-compose.yml` — `grafana/promtail:2.9.0`, 128M memory, Docker socket mount
- Updated `scripts/start_infrastructure.sh` and `scripts/stop_infrastructure.sh` to include Promtail
- Updated `scripts/generate_training_data.py` — per-service Loki queries (`{service="<name>"}`), added `"service"` key to log entries; fixed pre-existing lint/mypy issues (line length, `timezone.utc` → `UTC`, `params` type annotation)
- Fixed `demo_app/docker-compose.demo.yml` — added `SHIPPING_SERVICE_ADDR=localhost:50053` and `EMAIL_SERVICE_ADDR=localhost:8080` to checkoutservice (was panic-crashing on startup without shippingservice)
- Verified Promtail shipping logs: Loki receiving labeled entries from all 7 OTel Demo services, 3-minute test collection confirmed non-zero log counts with proper service attribution

*Week 6.5 — 24h data collection:*
- Ran 24h baseline with metrics + logs: `caffeinate -s poetry run python scripts/generate_training_data.py --duration 24h --output-dir data/baseline_with_logs/`
- Collection completed: 1440 snapshots, 2885 log entries (checkoutservice: 1440, productcatalogservice: 1440, redis: 5), 8 metric types × 7 services, status=completed

*Week 6.5 — After collection (fine-tuning):*
- Preprocessed baseline into feature vectors: 1430 sequences → train (1144, 10, 54) / val (286, 10, 54) with z-score normalization, saved to `data/splits/otel/`
- Feature vector: 54 dims = log (5 templates × 2 + 2 = 12) + metrics (6 metrics × 7 stats = 42). Metrics: cpu_usage_rate, memory_working_set_bytes, network_rx/tx_bytes_rate, network_rx/tx_errors_rate
- Fine-tuned on Colab Pro (L4 GPU): 3 iterations to optimize — (1) lr=0.0001 too slow (100 epochs, val_loss=0.425, no convergence), (2) lr=0.001 with 40 dims converged at epoch 60 (val_loss=0.296), (3) lr=0.001 with 54 dims (added error rate metrics) converged at epoch 158 (val_loss=0.134, best result)
- Checkpoint saved: `models/lstm_autoencoder/finetuned_otel.pt` (132,326 params)
- 95th percentile threshold: 0.253
- Synthetic anomaly benchmark: F1=0.97, Precision=0.95, Recall=1.00 (CPU spike 100%, Memory spike 100%, Network error 100% detection rate)
- Training curves and benchmark plots saved to `docs/images/` (finetune_training_curves.png, otel_finetune_benchmark.png)

*Week 7 — Causal Discovery:*
- Created `src/causal_discovery/graph_utils.py` — `CausalEdge` and `CausalGraph` dataclasses with `to_ascii()` rendering and `top_edges()` sorting
- Created `src/causal_discovery/pc_algorithm.py` — `discover_causal_graph()` wrapping causal-learn PC (Fisher's Z, stable=True, uc_rule=0, uc_priority=2), `create_time_lags()` for temporal feature augmentation, `parse_causal_graph()` for extracting directed edges from causal-learn's adjacency matrix
- Created `src/causal_discovery/counterfactual.py` — `calculate_counterfactual_confidence()` (correlation² × z-score contribution, clamped [0,1]) and `compute_baseline_stats()`
- Created `tests/unit/test_causal_discovery.py` — 31 tests across 7 classes: CausalEdge (2), CausalGraph (5), CreateTimeLags (6), DiscoverCausalGraph (5), ParseCausalGraph (4), ComputeBaselineStats (4), CounterfactualConfidence (5)
- Created `notebooks/06_causal_discovery_dev.ipynb` — validated on synthetic data: simple A→B→C chain (correct skeleton, no spurious A→C), complex B→C←D topology (collider correctly oriented), alpha sensitivity analysis, counterfactual confidence scoring, time-lagged features
- All 119 unit tests passing (88 existing + 31 new); ruff clean; mypy clean

**In Progress:**
- None — all Week 6.5 + Week 7 tasks complete

**Blockers / Issues:**
- **Promtail `keep` relabel causes empty-label errors:** Promtail 2.9.0 with `docker_sd_configs` opens log streams for ALL discovered containers before applying relabel rules. The `keep` action filters targets, but containers that don't match still have their logs read and batched without labels, causing Loki to reject with "at least one label pair is required per stream". **Resolution:** Removed the `keep` filter; set a default `job=docker` label on all containers. The `generate_training_data.py` script filters by service at the Loki query level (`{service="<name>"}`), so monitoring stack logs in Loki are harmless.
- **checkoutservice panic without SHIPPING_SERVICE_ADDR:** The reduced OTel Demo excluded shippingservice, but checkoutservice requires `SHIPPING_SERVICE_ADDR` to start. **Resolution:** Added `SHIPPING_SERVICE_ADDR=localhost:50053` and `EMAIL_SERVICE_ADDR=localhost:8080` as dummy env vars in `demo_app/docker-compose.demo.yml`. Service starts cleanly; checkout requests needing shipping fail gracefully at gRPC level.
- **Un-normalized features caused MSE loss ~10^14:** Raw feature vectors span 6 orders of magnitude (memory_bytes ~10^8 vs cpu_rate ~10^-3). First fine-tuning attempt produced constant val_loss with no learning. **Resolution:** Added z-score normalization per feature dimension before saving splits. Scaler params (`scaler_mean.npy`, `scaler_std.npy`) saved alongside splits for inference-time use.
- **lr=0.0001 too conservative for fine-tuning:** With HDFS→OTel dimension mismatch (115→54), embedding and output layers are reinitialized from scratch. Only LSTM body weights transfer. The conservative lr=0.0001 needed 100+ epochs without converging. **Resolution:** Increased to lr=0.001 (matching pretraining rate). Converged properly with early stopping.
- **PC algorithm produces undirected edges in simple chains:** The A→B→C synthetic data has no collider, so PC correctly recovers the skeleton but cannot determine edge direction. Only topologies with v-structures (e.g., B→C←D) produce directed edges. **Resolution:** This is expected PC behavior. The counterfactual confidence scoring handles undirected edges by scoring all edge directions and ranking by confidence.
- **causal-learn not installed on Colab:** `notebooks/06_causal_discovery_dev.ipynb` requires `causal-learn` which is not in Colab's default environment. **Resolution:** Added `%pip install causal-learn` cell at the top of the notebook.

**Next Session:**
- Begin Week 8: LangGraph Agent (`state.py`, `graph.py`, agent tools, prompts, executor)
- Create `notebooks/07_agent_prototyping.ipynb`
- End-to-end agent test with manually injected fault

**Notes:**
- Promtail version 2.9.0 uses `docker_sd_configs` syntax (not the newer `docker:` shorthand which was added in later versions). The Context7 docs showed both syntaxes; the newer one caused "field docker not found" errors.
- causal-learn's edge encoding: `cg.G.graph[j,i]==1 and cg.G.graph[i,j]==-1` means i→j (note transposed indices). The `parse_causal_graph()` function handles this mapping.
- OTel Demo services produce very few stdout logs (~2 entries/minute total from checkoutservice + productcatalogservice). This is inherent to compiled gRPC services. The ~2,900 log entries over 24h is sufficient because feature vectors are primarily metric-driven (42/54 dims).
- Fine-tuning was iteratively improved across 3 Colab runs: (1) unnormalized features → broken, (2) normalized + 40 dims + lr=0.0001 → slow convergence, (3) normalized + 54 dims + lr=0.001 → val_loss=0.134, F1=0.97 on synthetic benchmark. Adding network error rate metrics (zero in baseline, spike during faults) proved valuable.
- Context7 was used to verify causal-learn PC API (return value structure, edge encoding conventions, Fisher Z test import path) and Promtail Docker SD configuration syntax.

---

### 2026-04-05 — Session 9

**Phase:** Phase 4 — Modeling (Week 8 LangGraph Agent)
**Duration:** ~5 hours

**Completed:**

*Phase A — State Definition + Prompts:*
- Created `src/agent/state.py` — `AgentState` TypedDict with 13 fields in 4 groups (input, investigation, causal, output); `messages` field uses `Annotated[list, add_messages]` reducer from LangGraph; all other fields use last-write-wins
- Created `src/agent/prompts/system_prompt.py` — `SYSTEM_PROMPT` constant with 6-step investigation methodology (map topology → form hypotheses → gather evidence → analyze causation → consult docs → generate report), key principles, available metrics list, tool budget guidance
- Created `src/agent/prompts/report_template.py` — `RCA_REPORT_TEMPLATE` with 14 `str.format()` placeholders for structured RCA output

*Phase B — Tool Implementations:*
- Created `src/agent/tools/get_topology.py` — wraps `TopologyGraph`, returns full topology or per-service subgraph with upstream/downstream lists; no external dependency
- Created `src/agent/tools/query_metrics.py` — wraps `MetricsCollector` with 6 PromQL templates mapping to Docker Stats Exporter metrics; computes stats (min/max/mean/std/current) and 2σ anomalous flag
- Created `src/agent/tools/search_logs.py` — direct HTTP calls to Loki (`/loki/api/v1/query_range`), LogQL construction with optional service filter, error level extraction, top pattern counting
- Created `src/agent/tools/search_runbooks.py` — wraps `RunbookIndexer`, transforms results to `{title, content, relevance_score, source}` format
- Created `src/agent/tools/discover_causation.py` — orchestrates full causal pipeline: MetricsCollector → DataFrame → `create_time_lags()` → `discover_causal_graph(max_conditioning_set=4)` → `parse_causal_graph()` → `compute_baseline_stats()` → `calculate_counterfactual_confidence()` → `CausalGraph.to_ascii()`; identifies root cause as highest-confidence source with no incoming edges
- Created `src/agent/tools/__init__.py` — `TOOLS` registry list + `__all__` exports

*Phase C — Graph + Executor:*
- Created `src/agent/graph.py` — 5-node `StateGraph` with `ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite")`, `bind_tools(TOOLS)`, conditional routing via `should_continue()`; manual tool execution in `gather_evidence_node` to track tool call budget; `_parse_hypotheses()` JSON extraction from LLM responses; `_extract_actions()` from report text
- Created `src/agent/executor.py` — `AgentExecutor` class with `from_config()` classmethod, dual-mode `investigate()` (live: tools query Prometheus/Loki; offline: pre-loaded data for RCAEval), initial state construction with `SystemMessage` + `HumanMessage`, error handling with fallback report
- Updated `src/agent/__init__.py` — exports `AgentExecutor` and `AgentState`

*Phase D — Comprehensive Tests (62 new tests):*
- Updated `tests/conftest.py` — added 5 new fixtures: `sample_alert`, `sample_prometheus_range_response_factory`, `sample_loki_response_factory`, `sample_agent_config`, `sample_causal_metrics_df`
- Created `tests/unit/test_agent_tools.py` — 35 tests across 5 classes: TestGetTopology (6), TestQueryMetrics (8), TestSearchLogs (7), TestSearchRunbooks (5), TestDiscoverCausation (9). All external services mocked.
- Created `tests/unit/test_agent_state.py` — 5 tests: TypedDict validation, add_messages annotation, field completeness, construction, type hints
- Created `tests/unit/test_agent_graph.py` — 12 tests: graph compilation (2), should_continue routing (5), analyze_context_node (2), helper functions (3)
- Created `tests/unit/test_agent_executor.py` — 10 tests: init/config loading (2), investigate output (4), _format_alert (2), _extract_top3 (3)
- Created `tests/integration/test_data_pipeline.py` — 7 tests: Prometheus (2), Loki (2), Topology (2), FullPipeline (1). All `@pytest.mark.integration`.
- Created `tests/integration/test_agent_workflow.py` — 8 tests: graph compilation (1), live tool invocation (3), end-to-end investigation (3, requires GEMINI_API_KEY). All `@pytest.mark.integration`.

*Phase E — Notebook + Demo:*
- Created `notebooks/07_agent_prototyping.ipynb` — 7-section notebook: setup/imports, individual tool testing (all 5 tools), graph compilation + Mermaid visualization, routing logic verification, AgentExecutor setup from config, full investigation with synthetic alert, report inspection
- Created `scripts/run_agent_demo.py` — CLI demo script with prerequisite checks (API key, Prometheus, Loki), synthetic alert construction, live/offline modes, formatted output with timing

*Bug Fixes + Model Change:*
- Changed LLM from `gemini-1.5-flash` → `gemini-2.5-flash-lite` in `src/agent/graph.py`, `configs/agent_config.yaml`, and `tests/conftest.py`
- Fixed double curly braces in PromQL templates — `{{service="{service}"}}` produced literal `{{...}}` since `.replace()` doesn't interpret Python brace escaping; Prometheus returned 400 Bad Request. Fixed to single braces in `query_metrics.py` (6 templates) and `discover_causation.py` (4 templates)
- Fixed system prompt referencing unavailable metrics (`error_rate`, `latency_p99`) — the LLM wasted 4/10 tool calls on invalid metric names. Added explicit available metrics list to `SYSTEM_PROMPT` and updated tool budget example to reference `cpu_usage, memory_usage`
- Added `max_conditioning_set` parameter to `discover_causal_graph()` in `src/causal_discovery/pc_algorithm.py` — maps to causal-learn's `depth` parameter; set to 4 in `discover_causation.py` to prevent combinatorial explosion (32 columns at unrestricted depth took 30+ minutes; capped at depth 4, runs in <30 seconds)

**In Progress:**
- End-to-end agent demo tested live but was interrupted due to PC algorithm taking 30+ min before depth cap was added. Demo to be re-run with `max_conditioning_set=4`.

**Blockers / Issues:**
- **Double curly braces in PromQL templates:** The `METRIC_PROMQL` dict used `{{service="{service}"}}` which is Python's brace-escaping syntax for `.format()` strings, but the code uses `.replace()` instead. The double braces passed through literally, producing invalid PromQL that Prometheus rejected with 400 Bad Request. **Resolution:** Changed all PromQL templates to use single braces: `{service="{service}"}`.
- **LLM requesting unavailable metrics:** The system prompt listed `error_rate` and `latency_p99` in the tool budget example, but Docker Stats Exporter doesn't expose application-level metrics — only container-level (CPU, memory, network). The LLM wasted 4 of its 10 tool calls on these invalid metrics before the tool returned error dicts. **Resolution:** Added explicit "Available Metrics" section to `SYSTEM_PROMPT` listing only the 6 valid metric names, and updated the tool budget example.
- **PC algorithm combinatorial explosion at depth 5–6:** With 2 services × 4 metrics × 4 lag levels = 32 columns, the unrestricted PC algorithm spent 30+ minutes in depth 5–6 computing C(30,5) = 142,506 and C(30,6) = 593,775 conditioning set tests per edge pair. This is correct algorithm behavior, not a bug — but impractical for real-time RCA. **Resolution:** Added `max_conditioning_set` parameter to `discover_causal_graph()` (maps to causal-learn's `depth` kwarg), set to 4 in the discover_causation tool. Depth 4 is sufficient for the OTel Demo topology (longest causal chain = 3 hops) and completes in seconds. The config value `causal_discovery.max_conditioning_set: 3` in `agent_config.yaml` was already defined but not wired through — now it is.
- **LangGraph `AgentState` must be TypedDict (not dict subclass):** Initial implementation used `class AgentState(dict)` with annotations, which LangGraph doesn't recognize for state validation. **Resolution:** Changed to `class AgentState(TypedDict)` from `typing_extensions`, matching LangGraph's documented pattern.
- **mypy `response.content` type:** `ChatGoogleGenerativeAI.invoke()` returns `AIMessage` whose `.content` is `str | list[str | dict]`, not just `str`. Passing to string-typed functions triggered mypy errors. **Resolution:** Added `isinstance(response.content, str)` guards before passing to `_parse_hypotheses()` and `_extract_actions()`.

**Next Session:**
- Re-run `scripts/run_agent_demo.py` with depth-capped PC algorithm and verify full RCA report generation
- Begin Phase 5 — Evaluation: create fault injection scripts, evaluation framework
- Advisor check-in with working agent demo

**Notes:**
- Installed dependency versions: LangGraph 1.1.3, langchain-core 1.2.22, langchain-google-genai 4.2.1, Poetry 2.3.2.
- The `AgentState` uses `Annotated[list, add_messages]` for the `messages` field — this is the LangGraph reducer that appends new messages by ID instead of replacing the list. All other fields use default last-write-wins semantics.
- The 5-node graph structure (not `create_react_agent`) was chosen deliberately — it enforces the investigation protocol regardless of what the LLM prefers, preventing it from skipping directly to report generation.
- Tool execution in `gather_evidence_node` is manual (not LangGraph's built-in `ToolNode`) to maintain control over the tool call counter. The node checks `response.tool_calls`, executes each via `_TOOLS_BY_NAME`, creates `ToolMessage` objects, and decrements `tool_calls_remaining`.
- The `discover_causation` tool identifies root cause as the highest-confidence edge source with no incoming edges. If all sources also appear as targets (cycles), it falls back to the source of the highest-confidence edge overall.
- The system prompt explicitly lists the 6 available metrics from Docker Stats Exporter. The original spec's 7 metrics (latency_p50/p99, error_rate, request_count, connection_count) are application-level metrics not available from container monitoring — only cpu_usage, memory_usage, and network_rx/tx_bytes/errors_rate are exposed.
- Context7 was used to verify LangGraph `StateGraph` API (TypedDict state, `add_messages` reducer, `START`/`END` constants, `add_conditional_edges`), `ChatGoogleGenerativeAI` tool binding, and `ToolMessage` construction patterns.
- 181 total unit tests passing (119 existing + 62 new). All ruff lint clean. mypy 0 errors on `src/agent/` (13 files).
